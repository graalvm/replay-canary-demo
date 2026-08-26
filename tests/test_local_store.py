#
# Copyright (c) 2026, Oracle and/or its affiliates. All rights reserved.
# DO NOT ALTER OR REMOVE COPYRIGHT NOTICES OR THIS FILE HEADER.
#
# The Universal Permissive License (UPL), Version 1.0
#
# Subject to the condition set forth below, permission is hereby granted to any
# person obtaining a copy of this software, associated documentation and/or
# data (collectively the "Software"), free of charge and under any and all
# copyright rights in the Software, and any and all patent rights owned or
# freely licensable by each licensor hereunder covering either (i) the
# unmodified Software as contributed to or provided by such licensor, or (ii)
# the Larger Works (as defined below), to deal in both
#
# (a) the Software, and
#
# (b) any piece of software and/or hardware listed in the lrgrwrks.txt file if
# one is included with the Software (each a "Larger Work" to which the Software
# is contributed by such licensors),
#
# without restriction, including without limitation the rights to copy, create
# derivative works of, display, perform, and distribute the Software and make,
# use, sell, offer for sale, import, export, have made, and have sold the
# Software and the Larger Work(s), and to sublicense the foregoing rights on
# either these or other terms.
#
# This license is subject to the following condition:
#
# The above copyright notice and either this complete permission notice or at a
# minimum a reference to the UPL must be included in all copies or substantial
# portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#

"""Local repository tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from replay_canary.adapters.local_store import (
    ComparisonRepository,
    CorpusRepository,
    DataLayout,
    HotMethodWindowRepository,
    ReplayRepository,
    read_json,
    write_json_atomic,
)
from replay_canary.errors import DataFormatError, SelectorError, ValidationError
from replay_canary.model.common import (
    CommitMetadata,
    Identity,
    IdentityFactory,
    JsonValue,
    ProcessOutcome,
    ProcessStatus,
)
from replay_canary.model.comparison import (
    ComparisonCounts,
    ComparisonManifest,
)
from replay_canary.model.corpus import (
    CorpusManifest,
    CorpusRun,
    RecordingParameters,
    RunKey,
)
from replay_canary.model.profile import HotMethodWindow
from replay_canary.model.replay import ReplayManifest, ReplayParameters, ReplayRun
from replay_canary.services.compare import CompareRequest, CompareService

FIXTURES = Path(__file__).parent / "fixtures"
CORPUS_ID = "00000000-0000-4000-8000-000000000001"
BASELINE_ID = "00000000-0000-4000-8000-000000000002"
CANDIDATE_ID = "00000000-0000-4000-8000-000000000003"
COMPARISON_ID = "00000000-0000-4000-8000-000000000004"
CREATED_AT = datetime(2026, 7, 30, 12, 34, 56, tzinfo=timezone.utc)


def identity(object_id: str, label: str | None) -> Identity:
    return Identity(object_id, label, CREATED_AT)


def commit() -> CommitMetadata:
    return CommitMetadata(
        hash="a" * 40,
        committed_at=CREATED_AT,
        subject="Compiler revision",
        author_name="Contributor",
        parent_hashes=("b" * 40,),
    )


def outcome(status: ProcessStatus = "succeeded") -> ProcessOutcome:
    return ProcessOutcome(0, False, 1.25, status)


def corpus_manifest(label: str = "corpus") -> CorpusManifest:
    return CorpusManifest(
        identity=identity(CORPUS_ID, label),
        commit=commit(),
        benchmark_suites=(("renaissance", "0.16.0"),),
        recording_parameters=RecordingParameters(
            runs=1,
            hot_window_size=30,
            hot_method_threshold=Decimal("0.001"),
            sampling_frequency=1000,
            timeout_seconds=1200,
            heap_size="8g",
        ),
        graalvm_home="/graalvm",
        mx_version="mx 7",
        runs=(
            CorpusRun(
                key=RunKey("renaissance", "scrabble", 0),
                replay_files="runs/renaissance--scrabble--0/replays",
                replayable_compilations=2,
                log="runs/renaissance--scrabble--0/output.log",
                outcome=outcome(),
            ),
        ),
    )


def replay_manifest(
    object_id: str = BASELINE_ID, label: str = "baseline"
) -> ReplayManifest:
    return ReplayManifest(
        identity=identity(object_id, label),
        corpus_id=CORPUS_ID,
        commit=commit(),
        graalvm_home="/graalvm",
        mx_version="mx 7",
        replay_parameters=ReplayParameters(
            iterations=2,
            timeout_seconds=1200,
            heap_size="8g",
            retired_instruction_event="PAPI_TOT_INS",
            replay_args=(),
        ),
        runs=(
            ReplayRun(
                key=RunKey("renaissance", "scrabble", 0),
                metrics="runs/renaissance--scrabble--0/metrics.json",
                log="runs/renaissance--scrabble--0/output.log",
                parsed_iterations=2,
                parsed_compilations=4,
                outcome=outcome(),
            ),
        ),
    )


def comparison_manifest() -> ComparisonManifest:
    return ComparisonManifest(
        identity=identity(COMPARISON_ID, "comparison"),
        corpus_id=CORPUS_ID,
        baseline_replay_id=BASELINE_ID,
        candidate_replay_id=CANDIDATE_ID,
        thresholds=(("instructions", Decimal("0.01")),),
        counts=ComparisonCounts(1, 0, 0),
        summary="summary.md",
        report="report.html",
    )


def write_corpus_files(directory: Path) -> None:
    run = directory / "runs" / "renaissance--scrabble--0"
    (run / "replays").mkdir(parents=True)
    (run / "replays" / "1.replay").write_bytes(b"replay")
    (run / "output.log").write_text("recorded\n", encoding="utf-8")


def write_replay_files(directory: Path) -> None:
    run = directory / "runs" / "renaissance--scrabble--0"
    run.mkdir(parents=True)
    write_json_atomic(run / "metrics.json", {"iterations": [], "compilations": []})
    (run / "output.log").write_text("replayed\n", encoding="utf-8")


def write_comparable_replay_files(directory: Path, value: int) -> None:
    run = directory / "runs" / "renaissance--scrabble--0"
    run.mkdir(parents=True)
    iterations: list[JsonValue] = [
        {
            "iteration": iteration,
            "wall_time_ns": metric,
            "thread_time_ns": metric,
            "allocated_memory": metric,
            "compiled_bytecodes": metric,
            "target_code_size": metric,
            "target_code_hash": "same",
            "retired_instructions": metric,
        }
        for iteration, metric in ((0, 999), (1, value))
    ]
    write_json_atomic(
        run / "metrics.json",
        {"iterations": iterations, "compilations": []},
    )
    (run / "output.log").write_text("replayed\n", encoding="utf-8")


def test_layout_creates_only_approved_directories(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path / "data")
    layout.initialize()

    assert sorted(path.name for path in layout.root.iterdir()) == [
        "comparisons",
        "corpora",
        "hot-method-windows",
        "replays",
        "work",
    ]


def test_golden_manifests_match_the_persistent_models() -> None:
    assert (
        CorpusManifest.from_json(read_json(FIXTURES / "corpus_manifest.json"))
        == corpus_manifest()
    )
    assert (
        ReplayManifest.from_json(read_json(FIXTURES / "replay_manifest.json"))
        == replay_manifest()
    )
    assert (
        ComparisonManifest.from_json(read_json(FIXTURES / "comparison_manifest.json"))
        == comparison_manifest()
    )


def test_corpus_publish_round_trip_and_selector_resolution(tmp_path: Path) -> None:
    repository = CorpusRepository(DataLayout(tmp_path / "data"))
    manifest = corpus_manifest()
    staging = repository.create_staging(manifest.identity)
    write_corpus_files(staging.object_directory)

    published = repository.publish(staging, manifest)

    assert published.name == CORPUS_ID
    assert not staging.work_directory.exists()
    assert repository.get(CORPUS_ID) == manifest
    assert repository.resolve(CORPUS_ID) == manifest
    assert repository.resolve("corpus") == manifest


def test_publish_requires_all_manifest_references(tmp_path: Path) -> None:
    repository = CorpusRepository(DataLayout(tmp_path / "data"))
    manifest = corpus_manifest()
    staging = repository.create_staging(manifest.identity)

    with pytest.raises(DataFormatError, match="output.log"):
        repository.publish(staging, manifest)

    assert staging.work_directory.is_dir()
    assert not (tmp_path / "data" / "corpora" / CORPUS_ID).exists()


def test_labels_are_unique_and_objects_cannot_be_overwritten(tmp_path: Path) -> None:
    repository = CorpusRepository(DataLayout(tmp_path / "data"))
    first = corpus_manifest()
    first_staging = repository.create_staging(first.identity)
    write_corpus_files(first_staging.object_directory)
    repository.publish(first_staging, first)

    with pytest.raises(ValidationError, match="already exists"):
        repository.create_staging(first.identity)

    duplicate_label = CorpusManifest(
        **{
            **first.__dict__,
            "identity": identity("00000000-0000-4000-8000-000000000099", "corpus"),
        }
    )
    with pytest.raises(ValidationError, match="label already exists"):
        repository.create_staging(duplicate_label.identity)


def test_missing_and_ambiguous_selectors_fail(tmp_path: Path) -> None:
    repository = CorpusRepository(DataLayout(tmp_path / "data"))
    with pytest.raises(SelectorError, match="unknown"):
        repository.resolve("missing")

    first = corpus_manifest()
    staging = repository.create_staging(first.identity)
    write_corpus_files(staging.object_directory)
    repository.publish(staging, first)
    copied = tmp_path / "data" / "corpora" / "00000000-0000-4000-8000-000000000098"
    copied.mkdir()
    document = first.as_json()
    document["id"] = "00000000-0000-4000-8000-000000000098"
    write_json_atomic(copied / "manifest.json", document)
    write_corpus_files(copied)

    with pytest.raises(SelectorError, match="ambiguous"):
        repository.resolve("corpus")


def test_manifest_id_must_match_directory(tmp_path: Path) -> None:
    repository = CorpusRepository(DataLayout(tmp_path / "data"))
    object_directory = (
        tmp_path / "data" / "corpora" / "00000000-0000-4000-8000-000000000098"
    )
    object_directory.mkdir()
    write_json_atomic(object_directory / "manifest.json", corpus_manifest().as_json())
    write_corpus_files(object_directory)

    with pytest.raises(DataFormatError, match="does not match directory"):
        repository.list()


def test_path_traversal_in_manifest_is_rejected() -> None:
    document = corpus_manifest().as_json()
    runs = document["runs"]
    assert isinstance(runs, list)
    first_run = runs[0]
    assert isinstance(first_run, dict)
    first_run["log"] = "../outside.log"

    with pytest.raises(ValidationError, match="safe relative path"):
        CorpusManifest.from_json(document)


def test_truncated_manifest_has_a_direct_error(tmp_path: Path) -> None:
    path = tmp_path / "truncated.json"
    path.write_text('{"id":', encoding="utf-8")

    with pytest.raises(DataFormatError, match="invalid JSON"):
        read_json(path)


def test_symlinked_artifact_content_is_rejected(tmp_path: Path) -> None:
    repository = CorpusRepository(DataLayout(tmp_path / "data"))
    manifest = corpus_manifest()
    staging = repository.create_staging(manifest.identity)
    run = staging.object_directory / "runs" / "renaissance--scrabble--0"
    (run / "replays").mkdir(parents=True)
    external = tmp_path / "external.log"
    external.write_text("outside\n", encoding="utf-8")
    (run / "output.log").symlink_to(external)

    with pytest.raises(ValidationError, match="symlink"):
        repository.publish(staging, manifest)


def test_replay_and_comparison_repositories_round_trip(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path / "data")
    replay_repository = ReplayRepository(layout)
    baseline = replay_manifest()
    staging = replay_repository.create_staging(baseline.identity)
    write_replay_files(staging.object_directory)
    replay_repository.publish(staging, baseline)
    assert replay_repository.resolve("baseline") == baseline

    comparison_repository = ComparisonRepository(layout)
    comparison = comparison_manifest()
    comparison_staging = comparison_repository.create_staging(comparison.identity)
    (comparison_staging.object_directory / "report.html").write_text(
        "<!doctype html>", encoding="utf-8"
    )
    (comparison_staging.object_directory / "summary.md").write_text(
        "# Summary\n", encoding="utf-8"
    )
    comparison_repository.publish(comparison_staging, comparison)
    assert comparison_repository.resolve("comparison") == comparison


def test_hot_method_windows_are_atomic_and_path_scoped(tmp_path: Path) -> None:
    repository = HotMethodWindowRepository(DataLayout(tmp_path / "data"))
    window = HotMethodWindow(
        "renaissance",
        "scrabble",
        2,
        Decimal("0.001"),
        (("A.m()",),),
    )

    path = repository.put(window)

    assert path == (
        tmp_path / "data" / "hot-method-windows" / "renaissance" / "scrabble.json"
    )
    assert repository.get("renaissance", "scrabble") == window
    assert repository.get("renaissance", "missing") is None
    assert not list(path.parent.glob("*.tmp"))

    with pytest.raises(ValidationError, match="safe path component"):
        repository.get("../escape", "workload")


def test_compare_service_publishes_all_comparison_artifacts(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path / "data")
    corpora = CorpusRepository(layout)
    replays = ReplayRepository(layout)
    comparisons = ComparisonRepository(layout)

    corpus_value = corpus_manifest()
    corpus_staging = corpora.create_staging(corpus_value.identity)
    write_corpus_files(corpus_staging.object_directory)
    corpora.publish(corpus_staging, corpus_value)

    baseline = replay_manifest(BASELINE_ID, "before")
    baseline_staging = replays.create_staging(baseline.identity)
    write_comparable_replay_files(baseline_staging.object_directory, 100)
    replays.publish(baseline_staging, baseline)

    candidate = replay_manifest(CANDIDATE_ID, "after")
    candidate_staging = replays.create_staging(candidate.identity)
    write_comparable_replay_files(candidate_staging.object_directory, 110)
    replays.publish(candidate_staging, candidate)

    service = CompareService(
        corpora,
        replays,
        comparisons,
        IdentityFactory(lambda: UUID(COMPARISON_ID), lambda: CREATED_AT),
    )
    thresholds = (
        ("retired_instructions", Decimal("0.05")),
        ("allocated_memory", Decimal("0.04")),
        ("target_code_size", Decimal("0.03")),
    )
    published = service.compare(CompareRequest("before", "after", "result", thresholds))

    assert published.path == layout.child("comparisons") / COMPARISON_ID
    assert published.manifest.identity.label == "result"
    assert published.manifest.counts == ComparisonCounts(1, 0, 0)
    assert published.manifest.thresholds == tuple(sorted(thresholds))
    assert dict(published.result.aggregate_metrics)[
        "retired_instructions"
    ].threshold == Decimal("0.05")
    html = (published.path / "report.html").read_text(encoding="utf-8")
    assert "<h1>Replay canary report</h1>" in html
    assert "before" in html
    assert "after" in html
    summary = (published.path / "summary.md").read_text(encoding="utf-8")
    assert "# Replay canary comparison: changes found" in summary
    assert "**Retired instructions** (threshold ±5%)" in summary
    assert "`renaissance`: `scrabble` (+10.00%)" in summary
    assert not list(layout.child("work").iterdir())
