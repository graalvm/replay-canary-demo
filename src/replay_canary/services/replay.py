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

"""Corpus replay orchestration."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from replay_canary.adapters.local_store import (
    CorpusRepository,
    ReplayRepository,
    read_json,
    write_json_atomic,
)
from replay_canary.adapters.process import ProcessResult
from replay_canary.errors import ReplayCanaryError, ReplayError, ValidationError
from replay_canary.model.common import IdentityFactory, ProcessOutcome, ProcessStatus
from replay_canary.model.corpus import CorpusRun
from replay_canary.model.replay import (
    ReplayManifest,
    ReplayMetrics,
    ReplayParameters,
    ReplayRun,
    validate_replay_argument,
)
from replay_canary.services.compiler import (
    CompilerEnvironment,
    CompilerEnvironmentProvider,
)

#: Maximum duration of one replay run.
DEFAULT_REPLAY_TIMEOUT_SECONDS = 600
#: Replay heap size in gibibytes.
DEFAULT_REPLAY_HEAP_SIZE_GIB = 12


class ReplayMx(Protocol):
    """mx command construction used by replay execution."""

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
        """Construct one replay command.

        :param graalvm_home: GraalVM home used for replay.
        :param replay_files: Directory containing replay files.
        :param results_file: Destination launcher-results file.
        :param iterations: Number of replay iterations.
        :param heap_size_bytes: Fixed libgraal heap size.
        :param event_name: PAPI event used for retired instructions.
        :param replay_args: Additional validated JVM arguments.
        :return: Complete replay command.
        """


class ReplayRunner(Protocol):
    """Timed process execution used for replay runs."""

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
        """Run a timed replay command.

        :param command: Executable and arguments.
        :param cwd: Process working directory.
        :param timeout_seconds: Maximum execution time.
        :param log_path: File receiving combined output.
        :param env: Environment overrides, if any.
        :param terminate_grace_seconds: Time allowed after SIGTERM.
        :return: Structured process result.
        """


@dataclass(frozen=True)
class ReplayRequest:
    """Inputs for one replay."""

    #: Corpus ID or label.
    corpus_selector: str
    #: Optional label for the new replay.
    label: str | None
    #: Number of replay iterations, including warmup.
    iterations: int
    #: PAPI event interpreted as the retired-instruction count.
    retired_instruction_event: str
    #: Additional JVM arguments passed to ``mx replaycomp``.
    replay_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplayResult:
    """Published replay and command-level summary."""

    #: Published replay manifest.
    manifest: ReplayManifest
    #: Directory containing the published replay.
    path: Path
    #: Runs replayed successfully.
    successful_runs: int
    #: Runnable runs that failed.
    failed_runs: int
    #: Corpus runs without replay files.
    skipped_runs: int

    @property
    def partial(self) -> bool:
        """Whether any runnable replay failed.

        :return: ``True`` when at least one runnable run failed.
        """

        return self.failed_runs > 0


class ReplayService:
    """Replay every runnable run in one immutable corpus."""

    def __init__(
        self,
        environment_provider: CompilerEnvironmentProvider,
        mx: ReplayMx,
        runner: ReplayRunner,
        corpora: CorpusRepository,
        replays: ReplayRepository,
        logger: logging.Logger,
        identities: IdentityFactory | None = None,
    ) -> None:
        """Store replay adapters and repositories.

        :param environment_provider: Current compiler environment provider.
        :param mx: mx replay command builder.
        :param runner: Timed process runner.
        :param corpora: Corpus repository.
        :param replays: Replay repository.
        :param logger: Logger receiving brief run progress.
        :param identities: Identity factory, or ``None`` to use the default.
        """

        #: Current compiler environment provider.
        self._environment_provider = environment_provider
        #: mx replay command builder.
        self._mx = mx
        #: Timed process runner.
        self._runner = runner
        #: Corpus repository.
        self._corpora = corpora
        #: Replay repository.
        self._replays = replays
        #: Logger receiving brief run progress.
        self._logger = logger
        #: Identity factory for new replays.
        self._identities = identities or IdentityFactory()

    def replay(self, request: ReplayRequest) -> ReplayResult:
        """Execute and publish one replay.

        :param request: Resolved replay inputs.
        :return: Published replay and run summary.
        """

        if request.iterations < 1:
            raise ReplayError("replay iterations must be positive")
        if not request.retired_instruction_event.strip():
            raise ReplayError("retired-instruction event must not be empty")
        try:
            for argument in request.replay_args:
                validate_replay_argument(argument)
        except ValidationError as error:
            raise ReplayError(str(error)) from error
        corpus = self._corpora.resolve(request.corpus_selector)
        corpus_path = self._corpora.path_for(corpus.identity.id)
        identity = self._identities.create(request.label)
        staging = self._replays.create_staging(identity)
        try:
            environment = self._environment_provider.load()
            run_results_list: list[ReplayRun] = []
            total_runs = len(corpus.runs)
            for position, run in enumerate(corpus.runs, start=1):
                self._logger.info(
                    "replay run %d/%d: %s:%s (run %d/%d)",
                    position,
                    total_runs,
                    run.key.suite_name,
                    run.key.workload_name,
                    run.key.run_index + 1,
                    corpus.recording_parameters.runs,
                )
                run_results_list.append(
                    self._replay_run(
                        corpus_path,
                        run,
                        staging.object_directory,
                        staging.work_directory,
                        environment,
                        request.iterations,
                        request.retired_instruction_event,
                        request.replay_args,
                    )
                )
            run_results = tuple(run_results_list)
            runnable_runs = sum(run.outcome.status != "skipped" for run in run_results)
            successful_runs = sum(
                run.outcome.status == "succeeded" for run in run_results
            )
            if runnable_runs == 0:
                raise ReplayError(
                    "corpus contains no replay files; record it again after its "
                    f"hot windows have advanced; workspace retained at {staging.work_directory}"
                )
            if successful_runs == 0:
                raise ReplayError(
                    "no runnable corpus run replayed successfully; "
                    f"workspace retained at {staging.work_directory}"
                )
            manifest = ReplayManifest(
                identity=identity,
                corpus_id=corpus.identity.id,
                commit=environment.commit,
                graalvm_home=str(environment.graalvm_home),
                mx_version=environment.mx_version,
                replay_parameters=ReplayParameters(
                    iterations=request.iterations,
                    timeout_seconds=DEFAULT_REPLAY_TIMEOUT_SECONDS,
                    heap_size=f"{DEFAULT_REPLAY_HEAP_SIZE_GIB}g",
                    retired_instruction_event=request.retired_instruction_event,
                    replay_args=request.replay_args,
                ),
                runs=run_results,
            )
            path = self._replays.publish(staging, manifest)
            shutil.rmtree(staging.work_directory)
        except ReplayError:
            raise
        except ReplayCanaryError as error:
            raise ReplayError(
                f"{error}; replay workspace retained at {staging.work_directory}"
            ) from error
        except Exception as error:
            raise ReplayError(
                f"replay failed: {error}; workspace retained at {staging.work_directory}"
            ) from error

        failed_runs = sum(
            run.outcome.status not in {"succeeded", "skipped"} for run in run_results
        )
        skipped_runs = sum(run.outcome.status == "skipped" for run in run_results)
        return ReplayResult(
            manifest=manifest,
            path=path,
            successful_runs=successful_runs,
            failed_runs=failed_runs,
            skipped_runs=skipped_runs,
        )

    def _replay_run(
        self,
        corpus_path: Path,
        corpus_run: CorpusRun,
        object_directory: Path,
        work_directory: Path,
        environment: CompilerEnvironment,
        iterations: int,
        retired_instruction_event: str,
        replay_args: tuple[str, ...],
    ) -> ReplayRun:
        """Replay one corpus run and store normalized metrics.

        :param corpus_path: Directory containing the source corpus.
        :param corpus_run: Corpus run to replay.
        :param object_directory: Staged replay directory.
        :param work_directory: Invocation work directory.
        :param environment: Prepared compiler environment.
        :param iterations: Number of replay iterations.
        :param retired_instruction_event: PAPI event interpreted as retired
            instructions.
        :param replay_args: Additional validated JVM arguments.
        :return: Replay manifest entry for the run.
        """

        relative_run = Path("runs") / corpus_run.key.value
        artifact_run = object_directory / relative_run
        artifact_run.mkdir(parents=True)
        log_path = artifact_run / "output.log"
        if corpus_run.replay_files is None:
            log_path.write_text(
                "Skipped: corpus run contains no replay files.\n",
                encoding="utf-8",
            )
            return ReplayRun(
                key=corpus_run.key,
                metrics=None,
                log=(relative_run / "output.log").as_posix(),
                parsed_iterations=0,
                parsed_compilations=0,
                outcome=ProcessOutcome(
                    exit_code=None,
                    timed_out=False,
                    duration_seconds=0,
                    status="skipped",
                    message="corpus run contains no replay files",
                ),
            )

        replay_files = corpus_path / corpus_run.replay_files
        run_work = work_directory / "replay-runs" / corpus_run.key.value
        results_file = run_work / "launcher-results.json"
        command = self._mx.replaycomp_command(
            graalvm_home=environment.graalvm_home,
            replay_files=replay_files,
            results_file=results_file,
            iterations=iterations,
            heap_size_bytes=DEFAULT_REPLAY_HEAP_SIZE_GIB * 1024**3,
            event_name=retired_instruction_event,
            replay_args=replay_args,
        )
        process = self._runner.timed(
            command,
            cwd=run_work / "process",
            timeout_seconds=DEFAULT_REPLAY_TIMEOUT_SECONDS,
            log_path=log_path,
            env={
                "JAVA_HOME": str(environment.java_home),
                "ENABLE_PAPI_BRIDGE": "true",
            },
        )
        metrics: ReplayMetrics | None = None
        parse_message: str | None = None
        if results_file.is_file():
            try:
                metrics = ReplayMetrics.from_launcher_json(
                    read_json(results_file),
                    retired_instruction_event=retired_instruction_event,
                )
            except ReplayCanaryError as error:
                parse_message = str(error)
        else:
            parse_message = "replay launcher did not create a results file"

        status: ProcessStatus
        if process.timed_out:
            status = "timed_out"
        elif process.exit_code != 0:
            status = "process_failed"
        elif metrics is None:
            status = "parse_failed"
        elif not metrics.iterations:
            status = "empty_results"
            parse_message = "replay results contain no iteration totals"
        else:
            status = "succeeded"

        metrics_relative: str | None = None
        if metrics is not None:
            metrics_path = artifact_run / "metrics.json"
            write_json_atomic(metrics_path, metrics.as_json())
            metrics_relative = (relative_run / "metrics.json").as_posix()
        return ReplayRun(
            key=corpus_run.key,
            metrics=metrics_relative,
            log=(relative_run / "output.log").as_posix(),
            parsed_iterations=len(metrics.iterations) if metrics else 0,
            parsed_compilations=len(metrics.compilations) if metrics else 0,
            outcome=ProcessOutcome(
                exit_code=process.exit_code,
                timed_out=process.timed_out,
                duration_seconds=process.duration_seconds,
                status=status,
                message=parse_message,
            ),
        )
