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

"""Corpus recording orchestration."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from replay_canary.adapters.local_store import (
    CorpusRepository,
    HotMethodWindowRepository,
    read_json,
)
from replay_canary.adapters.process import ProcessResult
from replay_canary.errors import RecordingError, ReplayCanaryError
from replay_canary.model.benchmark import BenchmarkName, BenchmarkSuite
from replay_canary.model.common import IdentityFactory, ProcessOutcome, ProcessStatus
from replay_canary.model.corpus import (
    REPLAY_FILE_EXTENSIONS,
    CorpusManifest,
    CorpusRun,
    RecordingParameters,
    RunKey,
)
from replay_canary.model.profile import HotMethodWindow
from replay_canary.model.profjson import Profile
from replay_canary.profile_bootstrap import load_bootstrap_profile
from replay_canary.services.compiler import (
    CompilerEnvironment,
    CompilerEnvironmentProvider,
)

#: Profile sampling frequency in hertz.
DEFAULT_SAMPLING_FREQUENCY = 10_000
#: Maximum duration of one recording run.
DEFAULT_RECORD_TIMEOUT_SECONDS = 600
#: Java heap size used while recording.
DEFAULT_RECORD_HEAP_SIZE = "12g"


class RecordingMx(Protocol):
    """mx operations used with the current compiler environment."""

    def export_benchmark_suite(
        self, suite_name: str, output_path: Path
    ) -> BenchmarkSuite:
        """Export a benchmark suite.

        :param suite_name: Benchmark suite to export.
        :param output_path: Destination JSON path.
        :return: Parsed benchmark suite.
        """

    def profrecord_prefix(
        self, experiment_path: Path, sampling_frequency: int
    ) -> tuple[str, ...]:
        """Return a profrecord command prefix.

        :param experiment_path: Destination experiment directory.
        :param sampling_frequency: Sampling frequency in hertz.
        :return: mx profrecord command prefix.
        """

    def profjson(self, experiment_path: Path, output_path: Path) -> None:
        """Convert a recorded profile to JSON.

        :param experiment_path: Source experiment directory.
        :param output_path: Destination JSON path.
        """


class RecordingRunner(Protocol):
    """Timed process execution used for benchmark runs."""

    def timed(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: float,
        log_path: Path,
        env: dict[str, str] | None = None,
        terminate_grace_seconds: float = 5.0,
    ) -> ProcessResult:
        """Run a timed benchmark command.

        :param command: Executable and arguments.
        :param cwd: Process working directory.
        :param timeout_seconds: Maximum execution time.
        :param log_path: File receiving combined output.
        :param env: Environment overrides, if any.
        :param terminate_grace_seconds: Time allowed after SIGTERM.
        :return: Structured process result.
        """


@dataclass(frozen=True)
class RecordRequest:
    """Inputs for one corpus recording."""

    #: Optional label for the new corpus.
    label: str | None
    #: Benchmark suites selected by default.
    benchmark_suites: tuple[str, ...]
    #: Explicit workloads overriding suite selection.
    workloads: tuple[BenchmarkName, ...]
    #: Number of recording runs per workload.
    runs: int
    #: Number of recent profiles used for hot-method selection.
    hot_window_size: int
    #: Minimum sample share used to select hot methods.
    hot_method_threshold: Decimal
    #: Additional JVM arguments used while recording, in command-line order.
    jvm_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecordResult:
    """Published corpus and command-level summary."""

    #: Published corpus manifest.
    manifest: CorpusManifest
    #: Directory containing the published corpus.
    path: Path
    #: Runs that produced usable profiles.
    successful_profiles: int
    #: Runs that did not produce usable profiles.
    failed_runs: int
    #: Replay files stored across all runs.
    replayable_compilations: int

    @property
    def partial(self) -> bool:
        """Whether some requested runs failed.

        :return: ``True`` when at least one run failed.
        """

        return self.failed_runs > 0


@dataclass(frozen=True)
class _RunResult:
    """Internal result of one recording run."""

    #: Manifest entry for the run.
    manifest: CorpusRun
    #: Updated hot-method window, or ``None`` if the run failed.
    window: HotMethodWindow | None


class RecordService:
    """Profile selected workloads and publish an immutable local corpus."""

    def __init__(
        self,
        environment_provider: CompilerEnvironmentProvider,
        mx: RecordingMx,
        runner: RecordingRunner,
        corpora: CorpusRepository,
        hot_windows: HotMethodWindowRepository,
        logger: logging.Logger,
        identities: IdentityFactory | None = None,
    ) -> None:
        """Store recording adapters and repositories.

        :param environment_provider: Current compiler environment provider.
        :param mx: mx profiling operations.
        :param runner: Timed process runner.
        :param corpora: Corpus repository.
        :param hot_windows: Hot-method window repository.
        :param logger: Logger receiving brief run progress.
        :param identities: Identity factory, or ``None`` to use the default.
        """

        #: Current compiler environment provider.
        self._environment_provider = environment_provider
        #: mx profiling operations.
        self._mx = mx
        #: Timed process runner.
        self._runner = runner
        #: Corpus repository.
        self._corpora = corpora
        #: Hot-method window repository.
        self._hot_windows = hot_windows
        #: Logger receiving brief run progress.
        self._logger = logger
        #: Identity factory for new corpora.
        self._identities = identities or IdentityFactory()

    def record(self, request: RecordRequest) -> RecordResult:
        """Record selected benchmark runs and publish a replay corpus.

        :param request: Resolved recording inputs.
        :return: Published corpus and run summary.
        """

        _validate_request(request)
        identity = self._identities.create(request.label)
        staging = self._corpora.create_staging(identity)
        try:
            environment = self._environment_provider.load()
            suites, selected = self._load_suites_and_selection(
                staging.work_directory, request
            )
            windows = {name: self._load_window(name, request) for name in selected}
            run_results: list[_RunResult] = []
            total_runs = request.runs * len(selected)
            for run_index in range(request.runs):
                for name in selected:
                    self._logger.info(
                        "record run %d/%d: %s:%s (run %d/%d)",
                        len(run_results) + 1,
                        total_runs,
                        name.suite_name,
                        name.workload_name,
                        run_index + 1,
                        request.runs,
                    )
                    run_result = self._record_run(
                        staging.object_directory,
                        staging.work_directory,
                        suites[name.suite_name],
                        name,
                        run_index,
                        windows[name],
                        environment,
                        request,
                    )
                    run_results.append(run_result)
                    if run_result.window is not None:
                        windows[name] = run_result.window

            successful_profiles = sum(
                result.window is not None for result in run_results
            )
            if successful_profiles == 0:
                raise RecordingError(
                    "no requested run produced a usable profile; "
                    f"recording workspace retained at {staging.work_directory}"
                )
            manifest = CorpusManifest(
                identity=identity,
                commit=environment.commit,
                benchmark_suites=tuple(
                    (suite.name, suite.version) for suite in suites.values()
                ),
                recording_parameters=RecordingParameters(
                    runs=request.runs,
                    hot_window_size=request.hot_window_size,
                    hot_method_threshold=request.hot_method_threshold,
                    sampling_frequency=DEFAULT_SAMPLING_FREQUENCY,
                    timeout_seconds=DEFAULT_RECORD_TIMEOUT_SECONDS,
                    heap_size=DEFAULT_RECORD_HEAP_SIZE,
                    jvm_args=request.jvm_args,
                ),
                graalvm_home=str(environment.graalvm_home),
                mx_version=environment.mx_version,
                runs=tuple(result.manifest for result in run_results),
            )
            path = self._corpora.publish(staging, manifest)
            for window in windows.values():
                self._hot_windows.put(window)
            if successful_profiles == len(run_results):
                shutil.rmtree(staging.work_directory)
            else:
                self._logger.warning(
                    "failed run workspaces retained at %s", staging.work_directory
                )
        except RecordingError:
            raise
        except ReplayCanaryError as error:
            raise RecordingError(
                f"{error}; recording workspace retained at {staging.work_directory}"
            ) from error
        except Exception as error:
            raise RecordingError(
                f"recording failed: {error}; "
                f"workspace retained at {staging.work_directory}"
            ) from error

        failed_runs = len(run_results) - successful_profiles
        replayable_compilations = sum(
            run.manifest.replayable_compilations for run in run_results
        )
        return RecordResult(
            manifest=manifest,
            path=path,
            successful_profiles=successful_profiles,
            failed_runs=failed_runs,
            replayable_compilations=replayable_compilations,
        )

    def _load_suites_and_selection(
        self, work_directory: Path, request: RecordRequest
    ) -> tuple[dict[str, BenchmarkSuite], tuple[BenchmarkName, ...]]:
        """Load suite commands and resolve the workload selection.

        :param work_directory: Directory for exported suite files.
        :param request: Resolved recording inputs.
        :return: Suites by name and selected workloads in run order.
        """

        suite_names = (
            tuple(dict.fromkeys(name.suite_name for name in request.workloads))
            if request.workloads
            else request.benchmark_suites
        )
        suites = {
            name: self._mx.export_benchmark_suite(
                name, work_directory / "benchmark-suites" / f"{name}.json"
            )
            for name in suite_names
        }
        if request.workloads:
            selected = request.workloads
            for name in selected:
                suites[name.suite_name].benchmark(name.workload_name)
        else:
            selected = tuple(
                BenchmarkName(suite.name, workload)
                for suite in suites.values()
                for workload in suite.workload_names()
            )
        if len(set(selected)) != len(selected):
            raise RecordingError("workload selection contains duplicates")
        return suites, selected

    def _load_window(
        self, name: BenchmarkName, request: RecordRequest
    ) -> HotMethodWindow:
        """Load or create the hot-method window for one workload.

        :param name: Qualified workload name.
        :param request: Resolved recording inputs.
        :return: Window adjusted to the requested policy.
        """

        existing = self._hot_windows.get(name.suite_name, name.workload_name)
        if existing is not None:
            return existing.copy_with(
                window_size=request.hot_window_size,
                hot_method_threshold=request.hot_method_threshold,
            )
        bootstrap = load_bootstrap_profile(name.suite_name, name.workload_name)
        profiles = (bootstrap,) if bootstrap is not None else ()
        return HotMethodWindow(
            suite_name=name.suite_name,
            workload_name=name.workload_name,
            window_size=request.hot_window_size,
            hot_method_threshold=request.hot_method_threshold,
            profiles=profiles[-request.hot_window_size :],
        )

    def _record_run(
        self,
        object_directory: Path,
        work_directory: Path,
        suite: BenchmarkSuite,
        name: BenchmarkName,
        run_index: int,
        window: HotMethodWindow,
        environment: CompilerEnvironment,
        request: RecordRequest,
    ) -> _RunResult:
        """Record and process one benchmark run.

        :param object_directory: Staged corpus directory.
        :param work_directory: Invocation work directory.
        :param suite: Exported benchmark suite.
        :param name: Workload to run.
        :param run_index: Zero-based run index.
        :param window: Current hot-method window.
        :param environment: Prepared compiler environment.
        :param request: Resolved recording inputs.
        :return: Run manifest entry and optional updated window.
        """

        key = RunKey(name.suite_name, name.workload_name, run_index)
        relative_run = Path("runs") / key.value
        artifact_run = object_directory / relative_run
        artifact_run.mkdir(parents=True)
        log_path = artifact_run / "output.log"
        run_work = work_directory / "record-runs" / key.value
        experiment = run_work / "experiment"
        dump = run_work / "dump"
        dump.mkdir(parents=True)
        command = (
            *self._mx.profrecord_prefix(experiment, DEFAULT_SAMPLING_FREQUENCY),
            str(environment.graalvm_home / "bin" / "java"),
            f"-Xms{DEFAULT_RECORD_HEAP_SIZE}",
            f"-Xmx{DEFAULT_RECORD_HEAP_SIZE}",
            "-XX:+UseG1GC",
            "-Djdk.graal.CompilationFailureAction=ExitVM",
            f"-Djdk.graal.DumpPath={dump}",
            f"-Djdk.graal.RecordForReplay={_record_for_replay_filter(window.hot_methods())}",
            *request.jvm_args,
            *suite.benchmark(name.workload_name).arguments,
        )
        process = self._runner.timed(
            command,
            cwd=run_work / "process",
            timeout_seconds=DEFAULT_RECORD_TIMEOUT_SECONDS,
            log_path=log_path,
        )
        if process.timed_out or process.exit_code != 0:
            status: ProcessStatus = (
                "timed_out" if process.timed_out else "process_failed"
            )
            return _RunResult(
                self._failed_run(key, relative_run, process, status),
                None,
            )
        if not experiment.is_dir():
            return _RunResult(
                self._failed_run(
                    key,
                    relative_run,
                    process,
                    "missing_profile",
                    "profrecord did not create an experiment directory",
                ),
                None,
            )
        profile_path = run_work / "profile.json"
        try:
            self._mx.profjson(experiment, profile_path)
            profile = Profile.from_json(read_json(profile_path))
        except ReplayCanaryError as error:
            return _RunResult(
                self._failed_run(
                    key,
                    relative_run,
                    process,
                    "profile_failed",
                    str(error),
                ),
                None,
            )

        hot_methods = profile.hot_graal_methods(request.hot_method_threshold)
        updated_window = window.append(
            tuple(method.stable_name for method in hot_methods)
        )
        hot_compile_ids = frozenset(
            method.compile_id for method in hot_methods if method.compile_id is not None
        )
        replay_directory = artifact_run / "replays"
        copied = _copy_replay_files(
            dump / "replaycomp",
            replay_directory,
            hot_compile_ids,
        )
        replay_relative = (relative_run / "replays").as_posix() if copied else None
        result = _RunResult(
            CorpusRun(
                key=key,
                replay_files=replay_relative,
                replayable_compilations=copied,
                log=(relative_run / "output.log").as_posix(),
                outcome=ProcessOutcome(
                    exit_code=process.exit_code,
                    timed_out=False,
                    duration_seconds=process.duration_seconds,
                    status="succeeded",
                    message=None,
                ),
            ),
            updated_window,
        )
        shutil.rmtree(run_work)
        return result

    @staticmethod
    def _failed_run(
        key: RunKey,
        relative_run: Path,
        process: ProcessResult,
        status: ProcessStatus,
        message: str | None = None,
    ) -> CorpusRun:
        """Build a manifest entry for a failed recording run.

        :param key: Benchmark run key.
        :param relative_run: Run directory relative to the corpus.
        :param process: Benchmark process result.
        :param status: Machine-readable failure category.
        :param message: Optional failure detail.
        :return: Failed corpus run entry.
        """

        return CorpusRun(
            key=key,
            replay_files=None,
            replayable_compilations=0,
            log=(relative_run / "output.log").as_posix(),
            outcome=ProcessOutcome(
                exit_code=process.exit_code,
                timed_out=process.timed_out,
                duration_seconds=process.duration_seconds,
                status=status,
                message=message,
            ),
        )


def _record_for_replay_filter(methods: tuple[str, ...]) -> str:
    """Encode hot methods for the compiler's ``MethodFilter`` parser.

    Zero-parameter methods must omit the ``()`` suffix to use a broader filter,
    because ``MethodFilter`` may interpret an empty parameter list as one
    wildcard parameter.

    :param methods: Stable hot-method names.
    :return: Comma-separated ``RecordForReplay`` filter.
    """

    return ",".join(
        method.removesuffix("()") if method.endswith("()") else method
        for method in methods
    )


def _copy_replay_files(
    source_directory: Path,
    destination_directory: Path,
    compile_ids: frozenset[int],
) -> int:
    """Copy selected replay files into a staged corpus.

    :param source_directory: Directory containing Graal replay dumps.
    :param destination_directory: Staged destination directory.
    :param compile_ids: Compile IDs selected from the profile.
    :return: Number of replay files copied.
    """

    if not source_directory.is_dir():
        return 0
    copied = 0
    for compile_id in sorted(compile_ids):
        for extension in REPLAY_FILE_EXTENSIONS:
            source = source_directory / f"{compile_id}{extension}"
            if source.is_file():
                destination_directory.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination_directory / source.name)
                copied += 1
                break
    return copied


def _validate_request(request: RecordRequest) -> None:
    """Validate resolved recording inputs.

    :param request: Recording inputs to validate.
    """

    if min(request.runs, request.hot_window_size) < 1:
        raise RecordingError("record runs and hot-window size must be positive")
    if not request.hot_method_threshold.is_finite() or not (
        Decimal(0) <= request.hot_method_threshold <= Decimal(1)
    ):
        raise RecordingError("hot-method threshold must be at least 0 and at most 1")
    if not request.workloads and not request.benchmark_suites:
        raise RecordingError("at least one benchmark suite or workload is required")
    if any(not argument for argument in request.jvm_args):
        raise RecordingError("recording arguments must not be empty")
