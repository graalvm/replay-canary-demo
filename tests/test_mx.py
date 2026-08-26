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

"""mx adapter tests."""

from pathlib import Path
from typing import Mapping, Sequence

import pytest

from replay_canary.adapters.mx import Mx
from replay_canary.adapters.process import ProcessResult
from replay_canary.errors import BuildError, ValidationError


class RecordingRunner:
    def __init__(self, java_home: Path, graalvm_home: Path) -> None:
        self.java_home = java_home
        self.graalvm_home = graalvm_home
        self.captures: list[tuple[tuple[str, ...], Path, Mapping[str, str] | None]] = []
        self.logged_commands: list[
            tuple[tuple[str, ...], Path, Path, Mapping[str, str] | None]
        ] = []
        self.next_exit_code = 0

    def capture(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
    ) -> str:
        normalized = tuple(command)
        self.captures.append((normalized, cwd, env))
        if normalized[-1] == "--version":
            return "mx version 7.83.1\n"
        if "get-jdk-path" in normalized:
            return f"{self.java_home}\n"
        if normalized[-1] == "graalvm-home":
            return f"{self.graalvm_home}\n"
        raise AssertionError(f"unexpected capture command: {normalized}")

    def logged(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_path: Path,
        env: Mapping[str, str] | None = None,
    ) -> ProcessResult:
        normalized = tuple(command)
        self.logged_commands.append((normalized, cwd, log_path, env))
        return ProcessResult(
            duration_seconds=1,
            exit_code=self.next_exit_code,
            timed_out=False,
        )


def create_mx(tmp_path: Path) -> tuple[Mx, RecordingRunner, Path, Path]:
    repository = tmp_path / "graal"
    (repository / "compiler").mkdir(parents=True)
    (repository / "vm").mkdir()
    java_home = tmp_path / "labsjdk"
    graalvm_home = tmp_path / "graalvm"
    (graalvm_home / "bin").mkdir(parents=True)
    (graalvm_home / "bin" / "java").touch()
    runner = RecordingRunner(java_home, graalvm_home)
    return Mx("/tools/mx", repository, runner), runner, java_home, graalvm_home  # type: ignore[arg-type]


def test_mx_build_sequence_uses_public_dependencies_and_labsjdk(
    tmp_path: Path,
) -> None:
    mx, runner, java_home, graalvm_home = create_mx(tmp_path)
    logs = tmp_path / "logs"

    assert mx.version() == "mx version 7.83.1"
    mx.fetch_labsjdk(logs / "fetch.log")
    assert mx.labsjdk_home() == java_home
    mx.build_libgraal(java_home, logs / "libgraal.log")
    mx.build_replay_dependencies(java_home, logs / "dependencies.log")
    assert mx.graalvm_home(java_home) == graalvm_home

    repository = tmp_path / "graal"
    compiler = repository / "compiler"
    vm = repository / "vm"
    assert [entry[0] for entry in runner.logged_commands] == [
        (
            "/tools/mx",
            "-p",
            str(compiler),
            "-y",
            "fetch-jdk",
            "--skip-digest-check",
            "-A",
            "labsjdk-ce-latest",
        ),
        ("/tools/mx", "-p", str(vm), "--env", "libgraal", "build"),
        (
            "/tools/mx",
            "-p",
            str(compiler),
            "build",
            "--dependencies",
            "GRAAL_TEST,PAPI_BRIDGE",
        ),
    ]
    assert runner.logged_commands[1][3] == {"JAVA_HOME": str(java_home)}
    assert runner.logged_commands[2][3] == {
        "JAVA_HOME": str(java_home),
        "ENABLE_PAPI_BRIDGE": "true",
    }
    assert all(entry[1] == repository for entry in runner.logged_commands)


def test_mx_reports_failed_build_log(tmp_path: Path) -> None:
    mx, runner, java_home, _ = create_mx(tmp_path)
    runner.next_exit_code = 17
    log = tmp_path / "failed.log"

    with pytest.raises(BuildError, match=str(log)):
        mx.build_replay_dependencies(java_home, log)


def test_mx_rejects_invalid_graalvm_home(tmp_path: Path) -> None:
    mx, runner, _, _ = create_mx(tmp_path)
    runner.graalvm_home = tmp_path / "missing-graalvm"
    with pytest.raises(ValidationError, match="invalid GraalVM home"):
        mx.graalvm_home(runner.java_home)


def test_mx_replay_command_matches_public_launcher_contract(
    tmp_path: Path,
) -> None:
    mx, _, _, graalvm_home = create_mx(tmp_path)
    replay_files = tmp_path / "replays"
    results_file = tmp_path / "results.json"

    command = mx.replaycomp_command(
        graalvm_home=graalvm_home,
        replay_files=replay_files,
        results_file=results_file,
        iterations=3,
        heap_size_bytes=1234,
        event_name="RETIRED_INSTRUCTIONS",
        replay_args=("-Djdk.graal.FullUnroll=false", "-Xlog:gc"),
    )

    assert command == (
        "/tools/mx",
        "-p",
        str(tmp_path / "graal" / "compiler"),
        "replaycomp",
        f"--jdk-home={graalvm_home}",
        "-XX:+UseSerialGC",
        "-XX:CompileOnly=org/graalvm/None.none",
        "-Djdk.graal.internal.MinHeapSize=1234",
        "-Djdk.graal.internal.MaxHeapSize=1234",
        "-Djdk.graal.internal.Xmn1234",
        "-Djdk.graal.internal.ExpectedEdenSize=1234",
        "-Djdk.graal.internal.UsedEdenProportionThreshold=1.0",
        "-Djdk.graal.FullUnroll=false",
        "-Xlog:gc",
        str(replay_files),
        "--benchmark",
        "--iterations=3",
        "--event-names=RETIRED_INSTRUCTIONS",
        f"--results-file={results_file}",
    )
