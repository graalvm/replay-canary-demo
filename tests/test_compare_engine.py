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

"""Comparison tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from replay_canary.errors import ComparisonError
from replay_canary.model.common import (
    CommitMetadata,
    Identity,
    ProcessOutcome,
    ProcessStatus,
)
from replay_canary.model.comparison import MetricComparison
from replay_canary.model.corpus import (
    CorpusManifest,
    CorpusRun,
    RecordingParameters,
    RunKey,
)
from replay_canary.model.replay import (
    CompilationMetrics,
    IterationMetrics,
    ReplayManifest,
    ReplayMetrics,
    ReplayParameters,
    ReplayRun,
)
from replay_canary.reporting.builder import build_report_data
from replay_canary.reporting.html import render_report
from replay_canary.reporting.summary import render_summary
from replay_canary.services.compare import compare_replays

NOW = datetime(2026, 7, 30, 14, 0, 0, tzinfo=timezone.utc)
CORPUS_ID = "00000000-0000-4000-8000-000000000030"
BASELINE_ID = "00000000-0000-4000-8000-000000000031"
CANDIDATE_ID = "00000000-0000-4000-8000-000000000032"
COMPARISON_ID = "00000000-0000-4000-8000-000000000033"


def identity(object_id: str) -> Identity:
    return Identity(object_id, None, NOW)


def commit() -> CommitMetadata:
    return CommitMetadata("a" * 40, NOW, "Test", "Contributor", ())


def key(workload: str, run_index: int) -> RunKey:
    return RunKey("suite", workload, run_index)


def corpus(keys: tuple[RunKey, ...]) -> CorpusManifest:
    return CorpusManifest(
        identity(CORPUS_ID),
        commit(),
        (("suite", "1"),),
        RecordingParameters(2, 3, Decimal("0.1"), 10000, 600, "12g"),
        "/graalvm",
        "mx test",
        tuple(
            CorpusRun(
                run_key,
                f"runs/{run_key.value}/replays",
                1,
                f"runs/{run_key.value}/output.log",
                ProcessOutcome(0, False, 1, "succeeded"),
            )
            for run_key in keys
        ),
    )


def replay(
    object_id: str,
    keys: tuple[RunKey, ...],
    *,
    corpus_id: str = CORPUS_ID,
    statuses: dict[RunKey, ProcessStatus] | None = None,
    replay_args: tuple[str, ...] = (),
) -> ReplayManifest:
    statuses = statuses or {}
    return ReplayManifest(
        identity(object_id),
        corpus_id,
        commit(),
        "/graalvm",
        "mx test",
        ReplayParameters(3, 600, "12g", "PAPI_TOT_INS", replay_args),
        tuple(
            ReplayRun(
                run_key,
                f"runs/{run_key.value}/metrics.json",
                f"runs/{run_key.value}/output.log",
                3,
                1,
                ProcessOutcome(
                    0,
                    False,
                    1,
                    statuses.get(run_key, "succeeded"),
                ),
            )
            for run_key in keys
        ),
    )


def iteration(index: int, value: int, hash_value: str = "same") -> IterationMetrics:
    return IterationMetrics(
        index,
        value,
        value,
        value,
        value,
        value,
        hash_value,
        value,
    )


def compilation(
    iteration_index: int, compile_id: int, value: int, hash_value: str
) -> CompilationMetrics:
    return CompilationMetrics(
        iteration_index,
        compile_id,
        f"Type.method{compile_id}()",
        -1,
        value,
        value,
        value,
        value,
        value,
        hash_value,
        value,
    )


def metrics(
    values: tuple[int, ...],
    *,
    hashes: tuple[str, ...] | None = None,
    compilations: tuple[CompilationMetrics, ...] = (),
) -> ReplayMetrics:
    hashes = hashes or tuple("same" for _ in values)
    return ReplayMetrics(
        tuple(
            iteration(index, value, hashes[index]) for index, value in enumerate(values)
        ),
        compilations,
    )


def metric(
    result: tuple[tuple[str, MetricComparison], ...], name: str
) -> MetricComparison:
    return dict(result)[name]


def test_arithmetic_mean_iterations_then_corpus_totals_and_run_ratios() -> None:
    keys = (key("work", 0), key("work", 1))
    baseline = replay(BASELINE_ID, keys)
    candidate = replay(CANDIDATE_ID, keys)
    data = {
        (BASELINE_ID, keys[0]): metrics((999, 10, 20)),
        (CANDIDATE_ID, keys[0]): metrics((999, 30, 30)),
        (BASELINE_ID, keys[1]): metrics((999, 30, 30)),
        (CANDIDATE_ID, keys[1]): metrics((999, 30, 60)),
    }

    result = compare_replays(
        corpus(keys),
        baseline,
        candidate,
        lambda replay_value, run: data[(replay_value.identity.id, run.key)],
    )

    workload_pair = metric(result.workloads[0].metrics, "retired_instructions")
    assert workload_pair.baseline == 1.0
    assert workload_pair.candidate == pytest.approx((2.0 + 1.5) / 2)
    assert workload_pair.ratio == pytest.approx((2.0 + 1.5) / 2)
    aggregate_pair = metric(result.aggregate_metrics, "retired_instructions")
    assert aggregate_pair.baseline == 45
    assert aggregate_pair.candidate == 75
    assert aggregate_pair.ratio == pytest.approx(75 / 45)
    assert result.counts.compared == 2


def test_warmup_is_excluded_and_zero_baseline_ratio_is_unavailable() -> None:
    run_key = key("zero", 0)
    baseline = replay(BASELINE_ID, (run_key,))
    candidate = replay(CANDIDATE_ID, (run_key,))
    data = {
        (BASELINE_ID, run_key): metrics((1000, 0, 0)),
        (CANDIDATE_ID, run_key): metrics((1, 10, 10)),
    }

    result = compare_replays(
        corpus((run_key,)),
        baseline,
        candidate,
        lambda replay_value, run: data[(replay_value.identity.id, run.key)],
    )

    pair = metric(result.runs[0].metrics, "retired_instructions")
    assert pair.baseline == 0
    assert pair.candidate == 10
    assert pair.ratio is None
    assert pair.classification == "unavailable"
    workload_pair = metric(result.workloads[0].metrics, "retired_instructions")
    assert workload_pair.ratio is None
    assert workload_pair.classification == "unavailable"


def test_thresholds_apply_only_to_tracked_metrics() -> None:
    run_key = key("threshold", 0)
    baseline = replay(BASELINE_ID, (run_key,))
    candidate = replay(CANDIDATE_ID, (run_key,))
    data = {
        (BASELINE_ID, run_key): metrics((0, 100)),
        (CANDIDATE_ID, run_key): metrics((0, 104)),
    }

    result = compare_replays(
        corpus((run_key,)),
        baseline,
        candidate,
        lambda replay_value, run: data[(replay_value.identity.id, run.key)],
    )

    instructions = metric(result.runs[0].metrics, "retired_instructions")
    wall_time = metric(result.runs[0].metrics, "wall_time_ns")
    assert instructions.classification == "increase"
    assert instructions.threshold == Decimal("0.03")
    assert wall_time.ratio == 1.04
    assert wall_time.threshold is None
    assert wall_time.classification == "unchanged"


def test_configured_threshold_overrides_default_classification() -> None:
    run_key = key("configured-threshold", 0)
    baseline = replay(BASELINE_ID, (run_key,))
    candidate = replay(CANDIDATE_ID, (run_key,))
    data = {
        (BASELINE_ID, run_key): metrics((0, 100)),
        (CANDIDATE_ID, run_key): metrics((0, 104)),
    }

    result = compare_replays(
        corpus((run_key,)),
        baseline,
        candidate,
        lambda replay_value, run: data[(replay_value.identity.id, run.key)],
        thresholds={"retired_instructions": Decimal("0.05")},
    )

    instructions = metric(result.runs[0].metrics, "retired_instructions")
    assert instructions.classification == "unchanged"
    assert instructions.threshold == Decimal("0.05")


def test_inconsistent_baseline_hash_skips_and_candidate_hash_fails() -> None:
    baseline_bad = key("baseline-bad", 0)
    candidate_bad = key("candidate-bad", 0)
    keys = (baseline_bad, candidate_bad)
    baseline = replay(BASELINE_ID, keys)
    candidate = replay(CANDIDATE_ID, keys)
    data = {
        (BASELINE_ID, baseline_bad): metrics((1, 1), hashes=("one", "two")),
        (CANDIDATE_ID, baseline_bad): metrics((1, 1)),
        (BASELINE_ID, candidate_bad): metrics((1, 1)),
        (CANDIDATE_ID, candidate_bad): metrics((1, 1), hashes=("one", "two")),
    }

    result = compare_replays(
        corpus(keys),
        baseline,
        candidate,
        lambda replay_value, run: data[(replay_value.identity.id, run.key)],
    )

    assert [run.status for run in result.runs] == ["skipped", "failed"]
    assert result.runs[0].baseline_hash is None
    assert result.runs[0].candidate_hash == "same"
    assert result.runs[1].baseline_hash == "same"
    assert result.runs[1].candidate_hash is None
    assert result.counts.compared == 0
    assert result.counts.skipped == 1
    assert result.counts.failed == 1
    assert all(pair.ratio is None for _, pair in result.aggregate_metrics)

    summary = render_summary(
        comparison_identity=identity(COMPARISON_ID),
        baseline=baseline,
        candidate=candidate,
        result=result,
    )
    assert "# Replay canary comparison: no comparable runs" in summary
    assert "**Target code hash** is unavailable" in summary


def test_missing_runs_remain_visible_without_truncated_pairing() -> None:
    first = key("first", 0)
    second = key("second", 0)
    baseline = replay(BASELINE_ID, (first,))
    candidate = replay(CANDIDATE_ID, (second,))
    data = {
        (BASELINE_ID, first): metrics((0, 1)),
        (CANDIDATE_ID, second): metrics((0, 1)),
    }

    result = compare_replays(
        corpus((first, second)),
        baseline,
        candidate,
        lambda replay_value, run: data.get((replay_value.identity.id, run.key)),
    )

    assert [run.status for run in result.runs] == ["failed", "skipped"]
    assert result.runs[0].baseline_hash == "same"
    assert result.runs[0].candidate_hash is None
    assert result.runs[1].baseline_hash is None
    assert result.runs[1].candidate_hash == "same"
    assert result.counts.failed == 1
    assert result.counts.skipped == 1


def test_compilations_are_paired_by_iteration_and_compile_id() -> None:
    run_key = key("compilations", 0)
    baseline = replay(BASELINE_ID, (run_key,))
    candidate = replay(CANDIDATE_ID, (run_key,))
    data = {
        (BASELINE_ID, run_key): metrics(
            (1, 1),
            compilations=(
                compilation(0, 1, 100, "old"),
                compilation(1, 2, 200, "only-baseline"),
            ),
        ),
        (CANDIDATE_ID, run_key): metrics(
            (1, 1),
            compilations=(
                compilation(0, 1, 110, "new"),
                compilation(1, 3, 300, "only-candidate"),
            ),
        ),
    }

    result = compare_replays(
        corpus((run_key,)),
        baseline,
        candidate,
        lambda replay_value, run: data[(replay_value.identity.id, run.key)],
    )

    rows = result.runs[0].compilations
    assert [row.presence for row in rows] == [
        "matched",
        "baseline_only",
        "candidate_only",
    ]
    assert rows[0].code_changed is True
    assert rows[1].code_changed is None
    assert rows[2].code_changed is None


def test_cross_corpus_and_identical_replays_are_rejected() -> None:
    run_key = key("bad", 0)
    baseline = replay(BASELINE_ID, (run_key,))
    different_corpus = replay(
        CANDIDATE_ID,
        (run_key,),
        corpus_id="00000000-0000-4000-8000-000000000099",
    )
    with pytest.raises(ComparisonError, match="different corpora"):
        compare_replays(
            corpus((run_key,)),
            baseline,
            different_corpus,
            lambda replay_value, run: None,
        )

    with pytest.raises(ComparisonError, match="must be different"):
        compare_replays(
            corpus((run_key,)),
            baseline,
            baseline,
            lambda replay_value, run: None,
        )


def test_standalone_report_contains_local_details(tmp_path: Path) -> None:
    run_key = key("report", 0)
    corpus_value = corpus((run_key,))
    corpus_path = tmp_path / "corpus"
    replay_directory = corpus_value.runs[0].replay_files
    assert replay_directory is not None
    replay_file = corpus_path / replay_directory / "17.json"
    replay_file.parent.mkdir(parents=True)
    replay_file.write_text("{}", encoding="utf-8")
    baseline = replay(BASELINE_ID, (run_key,))
    candidate = replay(
        CANDIDATE_ID,
        (run_key,),
        replay_args=("-Djdk.graal.FullUnroll=false",),
    )
    method_name = "Type.method()"
    baseline_compilation = CompilationMetrics(
        1,
        17,
        method_name,
        -1,
        1_000_000,
        900_000,
        100,
        10,
        20,
        "old",
        1_000,
    )
    candidate_compilation = CompilationMetrics(
        1,
        17,
        method_name,
        -1,
        1_100_000,
        950_000,
        110,
        10,
        22,
        "new",
        1_100,
    )
    data = {
        (BASELINE_ID, run_key): metrics((0, 100), compilations=(baseline_compilation,)),
        (CANDIDATE_ID, run_key): metrics(
            (0, 110), compilations=(candidate_compilation,)
        ),
    }
    result = compare_replays(
        corpus_value,
        baseline,
        candidate,
        lambda replay_value, run: data[(replay_value.identity.id, run.key)],
    )
    report = build_report_data(
        comparison_identity=identity(COMPARISON_ID),
        corpus=corpus_value,
        corpus_path=corpus_path,
        baseline=baseline,
        candidate=candidate,
        result=result,
    )

    html = render_report(report)

    assert "<h1>Replay canary report</h1>" in html
    assert "Changed runs" in html
    assert "Unchanged runs" in html
    assert "Replay args: none" in html
    assert "-Djdk.graal.FullUnroll=false" in html
    assert "Candidate compiler" in html
    assert "Show iteration 0 compilation rows" in html
    assert '<p class="card-label">Corpus</p>' in html
    assert f"{corpus_path}/" in html
    assert report.runs[0].compilations[0].replay_path == str(replay_file)


def test_summary_reports_workloads_crossing_configured_thresholds() -> None:
    run_key = key("work", 0)
    baseline = replay(BASELINE_ID, (run_key,))
    candidate = replay(CANDIDATE_ID, (run_key,))
    values = {
        BASELINE_ID: metrics((0, 100)),
        CANDIDATE_ID: metrics((0, 110)),
    }
    result = compare_replays(
        corpus((run_key,)),
        baseline,
        candidate,
        lambda replay_value, run: values[replay_value.identity.id],
        thresholds={
            "retired_instructions": Decimal("0.05"),
            "allocated_memory": Decimal("0.20"),
            "target_code_size": Decimal("0.20"),
        },
    )

    summary = render_summary(
        comparison_identity=identity(COMPARISON_ID),
        baseline=baseline,
        candidate=candidate,
        result=result,
    )

    assert "# Replay canary comparison: changes found" in summary
    assert "**Retired instructions** (threshold ±5%)" in summary
    assert "`suite`: `work` (+10.00%)" in summary
    assert "**Allocated memory** (threshold ±20%) is stable." in summary
    assert "**Target code size** (threshold ±20%) is stable." in summary


def test_summary_reports_stable_metrics_at_configured_thresholds() -> None:
    run_key = key("work", 0)
    baseline = replay(BASELINE_ID, (run_key,))
    candidate = replay(CANDIDATE_ID, (run_key,))
    values = {
        BASELINE_ID: metrics((0, 100)),
        CANDIDATE_ID: metrics((0, 110)),
    }
    result = compare_replays(
        corpus((run_key,)),
        baseline,
        candidate,
        lambda replay_value, run: values[replay_value.identity.id],
        thresholds={
            name: Decimal("0.20")
            for name in (
                "retired_instructions",
                "allocated_memory",
                "target_code_size",
            )
        },
    )

    summary = render_summary(
        comparison_identity=identity(COMPARISON_ID),
        baseline=baseline,
        candidate=candidate,
        result=result,
    )

    assert "# Replay canary comparison: no changes found" in summary
    assert summary.count("(threshold ±20%) is stable.") == 3
