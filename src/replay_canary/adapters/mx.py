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

"""Adapter for the mx commands used by replay canary."""

from __future__ import annotations

from pathlib import Path

from replay_canary.adapters.local_store import read_json
from replay_canary.adapters.process import ProcessRunner
from replay_canary.errors import BuildError, ValidationError
from replay_canary.model.benchmark import BenchmarkSuite

#: mx alias for the currently selected LabsJDK used to build Graal.
LABSJDK_ALIAS = "labsjdk-ce-latest"
#: mx dependencies needed by replay recording and execution.
REPLAY_DEPENDENCIES = "GRAAL_TEST,PAPI_BRIDGE"


class Mx:
    """Execute the mx commands used by replay canary."""

    def __init__(
        self, executable: str, graal_repository: Path, runner: ProcessRunner
    ) -> None:
        """Store resolved mx command paths and the process runner.

        :param executable: Command used to run mx.
        :param graal_repository: Graal checkout path.
        :param runner: Process runner for mx commands.
        """

        #: Command used to run mx.
        self._executable = executable
        #: Resolved Graal checkout path.
        self._graal_repository = graal_repository.resolve()
        #: Graal compiler suite path.
        self._compiler_suite = self._graal_repository / "compiler"
        #: Graal VM suite path.
        self._vm_suite = self._graal_repository / "vm"
        #: Process runner used for mx commands.
        self._runner = runner

    def version(self) -> str:
        """Return the configured mx version.

        :return: mx version output.
        """

        return self._runner.capture(
            (self._executable, "--version"), cwd=self._compiler_suite
        ).strip()

    def fetch_labsjdk(self, log_path: Path) -> None:
        """Fetch the builder LabsJDK and store it under the common alias.

        :param log_path: File receiving build output.
        """

        self._require_success(
            (
                self._executable,
                "-p",
                str(self._compiler_suite),
                "-y",
                "fetch-jdk",
                "--skip-digest-check",
                "-A",
                LABSJDK_ALIAS,
            ),
            cwd=self._graal_repository,
            log_path=log_path,
            operation="fetch LabsJDK",
        )

    def labsjdk_home(self) -> Path:
        """Resolve the currently selected LabsJDK by the alias.

        :return: Resolved LabsJDK home.
        """

        output = self._runner.capture(
            (
                self._executable,
                "-p",
                str(self._compiler_suite),
                "get-jdk-path",
                LABSJDK_ALIAS,
            ),
            cwd=self._graal_repository,
        ).strip()
        return Path(output).expanduser().resolve()

    def build_libgraal(self, java_home: Path, log_path: Path) -> None:
        """Build the libgraal GraalVM configuration.

        :param java_home: Builder JDK home.
        :param log_path: File receiving build output.
        """

        self._require_success(
            (
                self._executable,
                "-p",
                str(self._vm_suite),
                "--env",
                "libgraal",
                "build",
            ),
            cwd=self._graal_repository,
            log_path=log_path,
            env={"JAVA_HOME": str(java_home)},
            operation="build libgraal",
        )

    def build_replay_dependencies(self, java_home: Path, log_path: Path) -> None:
        """Build the replay launcher and public PAPI bridge.

        :param java_home: Builder JDK home.
        :param log_path: File receiving build output.
        """

        self._require_success(
            (
                self._executable,
                "-p",
                str(self._compiler_suite),
                "build",
                "--dependencies",
                REPLAY_DEPENDENCIES,
            ),
            cwd=self._graal_repository,
            log_path=log_path,
            env={
                "JAVA_HOME": str(java_home),
                "ENABLE_PAPI_BRIDGE": "true",
            },
            operation="build replay dependencies",
        )

    def graalvm_home(self, java_home: Path) -> Path:
        """Return the libgraal GraalVM home produced by the VM suite.

        :param java_home: Builder JDK home.
        :return: Validated GraalVM home.
        """

        output = self._runner.capture(
            (
                self._executable,
                "-p",
                str(self._vm_suite),
                "--env",
                "libgraal",
                "graalvm-home",
            ),
            cwd=self._graal_repository,
            env={"JAVA_HOME": str(java_home)},
        ).strip()
        home = Path(output).expanduser().resolve()
        if not (home / "bin" / "java").is_file():
            raise ValidationError(f"mx returned an invalid GraalVM home: {home}")
        return home

    def export_benchmark_suite(
        self, suite_name: str, output_path: Path
    ) -> BenchmarkSuite:
        """Export and parse Java commands for one benchmark suite.

        :param suite_name: Benchmark suite to export.
        :param output_path: Destination for the exported JSON.
        :return: Parsed benchmark suite.
        """

        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._runner.capture(
            (
                self._executable,
                "-p",
                str(self._compiler_suite),
                "benchmark",
                suite_name,
                "--export-java-commands",
                str(output_path),
            ),
            cwd=self._graal_repository,
        )
        suite = BenchmarkSuite.from_json(read_json(output_path))
        if suite.name != suite_name:
            raise ValidationError(
                f"mx exported suite {suite.name!r} when {suite_name!r} was requested"
            )
        return suite

    def profrecord_prefix(
        self, experiment_path: Path, sampling_frequency: int
    ) -> tuple[str, ...]:
        """Return the command prefix that records a proftool experiment.

        :param experiment_path: Destination experiment directory.
        :param sampling_frequency: Sampling frequency in hertz.
        :return: mx profrecord command prefix.
        """

        return (
            self._executable,
            "-p",
            str(self._compiler_suite),
            "profrecord",
            "--frequency",
            str(sampling_frequency),
            "-E",
            str(experiment_path),
        )

    def profjson(self, experiment_path: Path, output_path: Path) -> None:
        """Convert a proftool experiment into JSON.

        :param experiment_path: Source experiment directory.
        :param output_path: Destination JSON path.
        """

        self._runner.capture(
            (
                self._executable,
                "-p",
                str(self._compiler_suite),
                "profjson",
                "-E",
                str(experiment_path),
                "-o",
                str(output_path),
            ),
            cwd=self._graal_repository,
        )

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
        """Construct the replay command with a GC-stable libgraal heap.

        :param graalvm_home: GraalVM home used for replay.
        :param replay_files: Directory containing replay files.
        :param results_file: Destination launcher-results file.
        :param iterations: Number of replay iterations.
        :param heap_size_bytes: Fixed libgraal heap size.
        :param event_name: PAPI event used for retired instructions.
        :param replay_args: Additional validated JVM arguments.
        :return: Complete mx replaycomp command.
        """

        return (
            self._executable,
            "-p",
            str(self._compiler_suite),
            "replaycomp",
            f"--jdk-home={graalvm_home}",
            "-XX:+UseSerialGC",
            "-XX:CompileOnly=org/graalvm/None.none",
            f"-Djdk.graal.internal.MinHeapSize={heap_size_bytes}",
            f"-Djdk.graal.internal.MaxHeapSize={heap_size_bytes}",
            f"-Djdk.graal.internal.Xmn{heap_size_bytes}",
            f"-Djdk.graal.internal.ExpectedEdenSize={heap_size_bytes}",
            "-Djdk.graal.internal.UsedEdenProportionThreshold=1.0",
            *replay_args,
            str(replay_files),
            "--benchmark",
            f"--iterations={iterations}",
            f"--event-names={event_name}",
            f"--results-file={results_file}",
        )

    def _require_success(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path,
        log_path: Path,
        operation: str,
        env: dict[str, str] | None = None,
    ) -> None:
        """Run a build command and raise an error on failure.

        :param command: Complete mx command.
        :param cwd: Process working directory.
        :param log_path: File receiving command output.
        :param operation: Plain description used in errors.
        :param env: Environment overrides, if any.
        """

        result = self._runner.logged(command, cwd=cwd, log_path=log_path, env=env)
        if not result.succeeded:
            raise BuildError(
                f"failed to {operation} (exit code {result.exit_code}); "
                f"see {log_path}"
            )
