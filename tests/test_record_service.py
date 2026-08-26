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

"""Recording service tests."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Mapping, Sequence
from uuid import UUID

import pytest

from replay_canary.adapters.local_store import (
    CorpusRepository,
    DataLayout,
    HotMethodWindowRepository,
)
from replay_canary.adapters.process import ProcessResult
from replay_canary.errors import RecordingError
from replay_canary.model.benchmark import Benchmark, BenchmarkName, BenchmarkSuite
from replay_canary.model.common import CommitMetadata, IdentityFactory
from replay_canary.services.compiler import CompilerEnvironment
from replay_canary.services.record import RecordRequest, RecordService

CORPUS_ID = UUID("00000000-0000-4000-8000-000000000010")
NOW = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)


class FakeEnvironmentProvider:
    def __init__(self, tmp_path: Path) -> None:
        graalvm = tmp_path / "graalvm"
        self.environment = CompilerEnvironment(
            commit=CommitMetadata(
                hash="a" * 40,
                committed_at=NOW,
                subject="Test",
                author_name="Contributor",
                parent_hashes=(),
            ),
            java_home=tmp_path / "labsjdk",
            graalvm_home=graalvm,
            mx_version="mx test",
        )

    def load(self) -> CompilerEnvironment:
        return self.environment


class FakeMx:
    def __init__(self) -> None:
        self.exports: list[str] = []
        self.profile = {
            "totalPeriod": 100,
            "code": [
                {
                    "compileId": "7",
                    "name": "7: example.Hot.run()",
                    "level": 4,
                    "period": 50,
                }
            ],
        }
        self.suites = {
            "suite": BenchmarkSuite(
                "suite",
                "1.0",
                (
                    Benchmark("one", ("-jar", "bench.jar", "one")),
                    Benchmark("two", ("-jar", "bench.jar", "two")),
                ),
            ),
            "other": BenchmarkSuite(
                "other",
                "2.0",
                (Benchmark("three", ("-jar", "other.jar")),),
            ),
        }

    def export_benchmark_suite(
        self, suite_name: str, output_path: Path
    ) -> BenchmarkSuite:
        self.exports.append(suite_name)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("{}\n", encoding="utf-8")
        return self.suites[suite_name]

    def profrecord_prefix(
        self, experiment_path: Path, sampling_frequency: int
    ) -> tuple[str, ...]:
        return (
            "mx",
            "profrecord",
            "--frequency",
            str(sampling_frequency),
            "-E",
            str(experiment_path),
        )

    def profjson(self, experiment_path: Path, output_path: Path) -> None:
        output_path.write_text(json.dumps(self.profile), encoding="utf-8")


class FakeRunner:
    def __init__(
        self,
        *,
        outcomes: list[tuple[int, bool]] | None = None,
        replay_extensions: tuple[str, ...] = (),
        create_experiment: bool = True,
    ) -> None:
        self.outcomes = outcomes or [(0, False)]
        self.replay_extensions = replay_extensions
        self.create_experiment = create_experiment
        self.commands: list[tuple[str, ...]] = []
        self.run_workspaces_seen: list[set[str]] = []

    def timed(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
        log_path: Path,
        env: Mapping[str, str] | None = None,
        terminate_grace_seconds: float = 5.0,
    ) -> ProcessResult:
        normalized = tuple(command)
        self.commands.append(normalized)
        self.run_workspaces_seen.append(
            {path.name for path in cwd.parent.parent.iterdir() if path.is_dir()}
        )
        index = len(self.commands) - 1
        exit_code, timed_out = self.outcomes[min(index, len(self.outcomes) - 1)]
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("benchmark output\n", encoding="utf-8")
        if exit_code == 0 and not timed_out and self.create_experiment:
            experiment = Path(normalized[normalized.index("-E") + 1])
            experiment.mkdir(parents=True)
            dump_argument = next(
                item for item in normalized if item.startswith("-Djdk.graal.DumpPath=")
            )
            replay_directory = Path(dump_argument.split("=", 1)[1]) / "replaycomp"
            replay_directory.mkdir(parents=True)
            for extension in self.replay_extensions:
                (replay_directory / f"7{extension}").write_text(
                    extension, encoding="utf-8"
                )
        return ProcessResult(
            duration_seconds=1.5,
            exit_code=exit_code,
            timed_out=timed_out,
        )


def request(
    *,
    runs: int = 1,
    suites: tuple[str, ...] = ("suite",),
    workloads: tuple[BenchmarkName, ...] = (BenchmarkName("suite", "one"),),
    jvm_args: tuple[str, ...] = (),
) -> RecordRequest:
    return RecordRequest(
        label="test-corpus",
        benchmark_suites=suites,
        workloads=workloads,
        runs=runs,
        hot_window_size=3,
        hot_method_threshold=Decimal("0.1"),
        jvm_args=jvm_args,
    )


def create_service(
    tmp_path: Path, mx: FakeMx, runner: FakeRunner
) -> tuple[RecordService, CorpusRepository, HotMethodWindowRepository]:
    layout = DataLayout(tmp_path / "data")
    corpora = CorpusRepository(layout)
    windows = HotMethodWindowRepository(layout)
    identities = IdentityFactory(lambda: CORPUS_ID, lambda: NOW)
    service = RecordService(
        FakeEnvironmentProvider(tmp_path),
        mx,
        runner,
        corpora,
        windows,
        logging.getLogger("test.record_service"),
        identities,
    )
    return service, corpora, windows


def test_usable_profile_publishes_corpus_without_replay_files(
    tmp_path: Path,
) -> None:
    mx = FakeMx()
    service, corpora, windows = create_service(tmp_path, mx, FakeRunner())

    result = service.record(request())

    assert not result.partial
    assert result.successful_profiles == 1
    assert result.replayable_compilations == 0
    assert result.manifest.runs[0].replay_files is None
    assert result.manifest.runs[0].outcome.status == "succeeded"
    assert corpora.resolve("test-corpus") == result.manifest
    window = windows.get("suite", "one")
    assert window is not None
    assert window.profiles[-1] == ("example.Hot.run()",)


def test_record_logs_one_progress_line_per_run(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="test.record_service")
    service, _, _ = create_service(tmp_path, FakeMx(), FakeRunner())

    service.record(request(runs=2))

    assert [record.message for record in caplog.records] == [
        "record run 1/2: suite:one (run 1/2)",
        "record run 2/2: suite:one (run 2/2)",
    ]


def test_successful_run_workspace_is_removed_before_next_run(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    service, _, _ = create_service(tmp_path, FakeMx(), runner)

    service.record(request(runs=2))

    assert runner.run_workspaces_seen == [
        {"suite--one--0"},
        {"suite--one--1"},
    ]


def test_empty_hot_profile_still_advances_and_publishes(tmp_path: Path) -> None:
    mx = FakeMx()
    mx.profile = {"totalPeriod": 100, "code": []}
    service, _, windows = create_service(tmp_path, mx, FakeRunner())

    result = service.record(request())

    assert result.successful_profiles == 1
    window = windows.get("suite", "one")
    assert window is not None
    assert window.profiles[-1] == ()


def test_binary_replay_is_preferred_over_json(tmp_path: Path) -> None:
    mx = FakeMx()
    service, _, _ = create_service(
        tmp_path, mx, FakeRunner(replay_extensions=(".replay", ".json"))
    )

    result = service.record(request())

    assert result.replayable_compilations == 1
    run_directory = result.path / "runs" / "suite--one--0" / "replays"
    assert sorted(path.name for path in run_directory.iterdir()) == ["7.replay"]


def test_partial_profiles_publish_corpus_and_retain_failed_run(
    tmp_path: Path,
) -> None:
    mx = FakeMx()
    runner = FakeRunner(outcomes=[(0, False), (9, False)])
    service, _, windows = create_service(tmp_path, mx, runner)

    result = service.record(request(runs=2))

    assert result.partial
    assert result.successful_profiles == 1
    assert result.failed_runs == 1
    assert [run.outcome.status for run in result.manifest.runs] == [
        "succeeded",
        "process_failed",
    ]
    window = windows.get("suite", "one")
    assert window is not None
    assert window.profiles[-1] == ("example.Hot.run()",)
    work_directories = list((tmp_path / "data" / "work").iterdir())
    assert len(work_directories) == 1
    record_runs = work_directories[0] / "record-runs"
    assert not (record_runs / "suite--one--0").exists()
    assert (record_runs / "suite--one--1").is_dir()


def test_no_usable_profiles_publishes_nothing_and_retains_workspace(
    tmp_path: Path,
) -> None:
    mx = FakeMx()
    service, corpora, windows = create_service(
        tmp_path, mx, FakeRunner(outcomes=[(1, False)])
    )

    with pytest.raises(RecordingError, match="no requested run"):
        service.record(request())

    assert corpora.list() == ()
    assert windows.get("suite", "one") is None
    assert len(list((tmp_path / "data" / "work").iterdir())) == 1


def test_workload_selection_overrides_configured_suites(tmp_path: Path) -> None:
    mx = FakeMx()
    runner = FakeRunner()
    service, _, _ = create_service(tmp_path, mx, runner)

    service.record(
        request(
            suites=("other",),
            workloads=(BenchmarkName("suite", "two"),),
        )
    )

    assert mx.exports == ["suite"]
    assert "-jar" in runner.commands[0]
    assert "two" in runner.commands[0]


def test_additional_jvm_arguments_precede_launcher_and_are_stored(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    service, _, _ = create_service(tmp_path, FakeMx(), runner)
    jvm_args = ("--enable-preview", "-Djdk.graal.PrintCompilation=true")

    result = service.record(request(jvm_args=jvm_args))

    command = runner.commands[0]
    assert command.index(jvm_args[0]) < command.index(jvm_args[1])
    assert command.index(jvm_args[1]) < command.index("-jar")
    parameters = result.manifest.recording_parameters
    assert parameters.jvm_args == jvm_args


def test_duplicate_workload_selection_is_rejected(tmp_path: Path) -> None:
    mx = FakeMx()
    service, corpora, _ = create_service(tmp_path, mx, FakeRunner())
    duplicate = BenchmarkName("suite", "one")

    with pytest.raises(RecordingError, match="duplicates"):
        service.record(request(workloads=(duplicate, duplicate)))

    assert corpora.list() == ()
