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

"""Replay service tests."""

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
    ReplayRepository,
)
from replay_canary.adapters.process import ProcessResult
from replay_canary.errors import ReplayError
from replay_canary.model.common import (
    CommitMetadata,
    Identity,
    IdentityFactory,
    ProcessOutcome,
)
from replay_canary.model.corpus import (
    CorpusManifest,
    CorpusRun,
    RecordingParameters,
    RunKey,
)
from replay_canary.services.compiler import CompilerEnvironment
from replay_canary.services.replay import ReplayRequest, ReplayService

CORPUS_ID = "00000000-0000-4000-8000-000000000020"
REPLAY_ID = UUID("00000000-0000-4000-8000-000000000021")
NOW = datetime(2026, 7, 30, 13, 0, 0, tzinfo=timezone.utc)


def commit() -> CommitMetadata:
    return CommitMetadata("a" * 40, NOW, "Test", "Contributor", ())


class FakeEnvironmentProvider:
    def __init__(self, tmp_path: Path) -> None:
        self.environment = CompilerEnvironment(
            commit=commit(),
            java_home=tmp_path / "labsjdk",
            graalvm_home=tmp_path / "graalvm",
            mx_version="mx test",
        )

    def load(self) -> CompilerEnvironment:
        return self.environment


class FakeMx:
    def replaycomp_command(
        self,
        *,
        graalvm_home: Path,
        replay_files: Path,
        results_file: Path,
        iterations: int,
        heap_size_bytes: int,
        event_name: str,
        replay_args: tuple[str, ...],
    ) -> tuple[str, ...]:
        return (
            "mx",
            "replaycomp",
            f"--jdk-home={graalvm_home}",
            str(replay_files),
            f"--iterations={iterations}",
            f"--heap={heap_size_bytes}",
            f"--event-names={event_name}",
            *replay_args,
            f"--results-file={results_file}",
        )


class FakeRunner:
    def __init__(
        self,
        outcomes: list[tuple[int, bool]] | None = None,
        *,
        write_results: bool = True,
    ) -> None:
        self.outcomes = outcomes or [(0, False)]
        self.write_results = write_results
        self.commands: list[tuple[str, ...]] = []
        self.environments: list[Mapping[str, str] | None] = []

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
        self.environments.append(env)
        index = len(self.commands) - 1
        exit_code, timed_out = self.outcomes[min(index, len(self.outcomes) - 1)]
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("replay output\n", encoding="utf-8")
        if self.write_results:
            results_argument = next(
                item for item in normalized if item.startswith("--results-file=")
            )
            results_file = Path(results_argument.split("=", 1)[1])
            event_argument = next(
                item for item in normalized if item.startswith("--event-names=")
            )
            event_name = event_argument.split("=", 1)[1]
            results_file.parent.mkdir(parents=True, exist_ok=True)
            results_file.write_text(
                json.dumps(
                    [
                        {
                            "type": "compilation",
                            "iteration": 0,
                            "compile_id": 7,
                            "method_name": "example.Hot.run()",
                            "entry_bci": -1,
                            "wall_time_ns": 10,
                            "thread_time_ns": 9,
                            "allocated_memory": 100,
                            "compiled_bytecodes": 20,
                            "target_code_size": 30,
                            "target_code_hash": "hash",
                            "events": {event_name: 1000},
                        },
                        {
                            "type": "iteration_total",
                            "iteration": 0,
                            "wall_time_ns": 10,
                            "thread_time_ns": 9,
                            "allocated_memory": 100,
                            "compiled_bytecodes": 20,
                            "target_code_size": 30,
                            "target_code_hash": "hash",
                            "events": {event_name: 1000},
                        },
                    ]
                ),
                encoding="utf-8",
            )
        return ProcessResult(
            duration_seconds=1.0,
            exit_code=exit_code,
            timed_out=timed_out,
        )


def publish_corpus(
    layout: DataLayout, *, runnable: int, skipped: int
) -> CorpusManifest:
    repository = CorpusRepository(layout)
    runs: list[CorpusRun] = []
    identity = Identity(CORPUS_ID, "corpus", NOW)
    staging = repository.create_staging(identity)
    for index in range(runnable + skipped):
        key = RunKey("suite", f"workload-{index}", 0)
        relative = Path("runs") / key.value
        run_directory = staging.object_directory / relative
        run_directory.mkdir(parents=True)
        (run_directory / "output.log").write_text("record\n", encoding="utf-8")
        replay_files: str | None = None
        replay_count = 0
        if index < runnable:
            replay_directory = run_directory / "replays"
            replay_directory.mkdir()
            (replay_directory / "7.replay").write_bytes(b"replay")
            replay_files = (relative / "replays").as_posix()
            replay_count = 1
        runs.append(
            CorpusRun(
                key,
                replay_files,
                replay_count,
                (relative / "output.log").as_posix(),
                ProcessOutcome(0, False, 1, "succeeded"),
            )
        )
    manifest = CorpusManifest(
        identity=identity,
        commit=commit(),
        benchmark_suites=(("suite", "1"),),
        recording_parameters=RecordingParameters(
            1, 3, Decimal("0.1"), 10_000, 600, "12g"
        ),
        graalvm_home="/graalvm",
        mx_version="mx test",
        runs=tuple(runs),
    )
    repository.publish(staging, manifest)
    return manifest


def create_service(
    tmp_path: Path,
    runner: FakeRunner,
    *,
    runnable: int = 1,
    skipped: int = 0,
) -> tuple[ReplayService, ReplayRepository]:
    layout = DataLayout(tmp_path / "data")
    publish_corpus(layout, runnable=runnable, skipped=skipped)
    replays = ReplayRepository(layout)
    return (
        ReplayService(
            FakeEnvironmentProvider(tmp_path),
            FakeMx(),
            runner,
            CorpusRepository(layout),
            replays,
            logging.getLogger("test.replay_service"),
            IdentityFactory(lambda: REPLAY_ID, lambda: NOW),
        ),
        replays,
    )


def request(
    event_name: str = "PAPI_TOT_INS", replay_args: tuple[str, ...] = ()
) -> ReplayRequest:
    return ReplayRequest("corpus", "test-replay", 2, event_name, replay_args)


def test_successful_replay_persists_metrics(tmp_path: Path) -> None:
    runner = FakeRunner()
    service, repository = create_service(tmp_path, runner, skipped=1)

    result = service.replay(request())

    assert not result.partial
    assert result.successful_runs == 1
    assert result.skipped_runs == 1
    assert result.manifest.corpus_id == CORPUS_ID
    assert result.manifest.runs[0].parsed_iterations == 1
    assert result.manifest.runs[0].parsed_compilations == 1
    assert result.manifest.runs[1].outcome.status == "skipped"
    assert repository.resolve("test-replay") == result.manifest
    assert runner.environments == [
        {
            "JAVA_HOME": str(tmp_path / "labsjdk"),
            "ENABLE_PAPI_BRIDGE": "true",
        }
    ]


def test_replay_logs_one_progress_line_per_corpus_run(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="test.replay_service")
    service, _ = create_service(tmp_path, FakeRunner(), runnable=1, skipped=1)

    service.replay(request())

    assert [record.message for record in caplog.records] == [
        "replay run 1/2: suite:workload-0 (run 1/1)",
        "replay run 2/2: suite:workload-1 (run 1/1)",
    ]


def test_replay_passes_and_records_configured_instruction_event(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    service, _ = create_service(tmp_path, runner)

    result = service.replay(request("RETIRED_INSTRUCTIONS"))

    assert "--event-names=RETIRED_INSTRUCTIONS" in runner.commands[0]
    assert (
        result.manifest.replay_parameters.retired_instruction_event
        == "RETIRED_INSTRUCTIONS"
    )
    metrics_relative = result.manifest.runs[0].metrics
    assert metrics_relative is not None
    metrics_path = result.path / metrics_relative
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert metrics["iterations"][0]["retired_instructions"] == 1000


def test_replay_passes_and_records_additional_jvm_arguments(tmp_path: Path) -> None:
    runner = FakeRunner()
    service, _ = create_service(tmp_path, runner)
    replay_args = ("-Djdk.graal.FullUnroll=false", "-Xlog:gc")

    result = service.replay(request(replay_args=replay_args))

    command = runner.commands[0]
    assert command.index(replay_args[0]) < command.index(replay_args[1])
    assert result.manifest.replay_parameters.replay_args == replay_args


def test_replay_rejects_non_jvm_argument_before_creating_data(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    service, repository = create_service(tmp_path, runner)

    with pytest.raises(ReplayError, match="must start with -D or -X"):
        service.replay(request(replay_args=("--compare-graphs=true",)))

    assert repository.list() == ()
    assert runner.commands == []


def test_partial_replay_publishes_explicit_failure(tmp_path: Path) -> None:
    runner = FakeRunner(outcomes=[(0, False), (9, False)])
    service, _ = create_service(tmp_path, runner, runnable=2)

    result = service.replay(request())

    assert result.partial
    assert result.successful_runs == 1
    assert result.failed_runs == 1
    assert [run.outcome.status for run in result.manifest.runs] == [
        "succeeded",
        "process_failed",
    ]


def test_missing_results_is_a_failed_run_and_publishes_nothing(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(write_results=False)
    service, repository = create_service(tmp_path, runner)

    with pytest.raises(ReplayError, match="no runnable corpus run"):
        service.replay(request())

    assert repository.list() == ()
    assert len(list((tmp_path / "data" / "work").iterdir())) == 1


def test_profile_only_corpus_is_not_replayable(tmp_path: Path) -> None:
    service, repository = create_service(tmp_path, FakeRunner(), runnable=0, skipped=1)

    with pytest.raises(ReplayError, match="contains no replay files"):
        service.replay(request())

    assert repository.list() == ()
