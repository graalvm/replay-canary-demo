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

"""Process runner tests."""

import logging
import os
import sys
from pathlib import Path

import pytest

from replay_canary.adapters.process import ProcessRunner
from replay_canary.errors import CommandExecutionError, ToolNotFoundError


def runner() -> ProcessRunner:
    return ProcessRunner(logging.getLogger("test"))


def test_capture_merges_environment(tmp_path: Path) -> None:
    output = runner().capture(
        (
            sys.executable,
            "-c",
            "import os; print(os.environ['PATH']); print(os.environ['CANARY_TEST'])",
        ),
        cwd=tmp_path,
        env={"CANARY_TEST": "present"},
    )

    assert os.environ["PATH"] in output
    assert output.rstrip().endswith("present")


def test_capture_reports_failure_output(tmp_path: Path) -> None:
    with pytest.raises(CommandExecutionError, match="specific failure"):
        runner().capture(
            (
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('specific failure'); sys.exit(7)",
            ),
            cwd=tmp_path,
        )


def test_capture_reports_missing_tool(tmp_path: Path) -> None:
    with pytest.raises(ToolNotFoundError, match="tool not found"):
        runner().capture(("definitely-not-a-real-replay-canary-tool",), cwd=tmp_path)


def test_timed_writes_combined_log(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "output.log"
    result = runner().timed(
        (
            sys.executable,
            "-c",
            "import sys; print('stdout'); print('stderr', file=sys.stderr)",
        ),
        cwd=tmp_path / "work",
        timeout_seconds=5,
        log_path=log_path,
    )

    assert result.succeeded
    output = log_path.read_text(encoding="utf-8")
    assert "stdout" in output
    assert "stderr" in output


def test_timed_terminates_process_group(tmp_path: Path) -> None:
    log_path = tmp_path / "timeout.log"
    result = runner().timed(
        (
            sys.executable,
            "-c",
            "import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
            "print('ready', flush=True); time.sleep(30)",
        ),
        cwd=tmp_path / "work",
        timeout_seconds=0.2,
        terminate_grace_seconds=0.2,
        log_path=log_path,
    )

    assert result.timed_out
    assert not result.succeeded
    assert "ready" in log_path.read_text(encoding="utf-8")


def test_logged_runs_without_timeout_and_records_failure(tmp_path: Path) -> None:
    log_path = tmp_path / "command.log"
    result = runner().logged(
        (
            sys.executable,
            "-c",
            "import sys; print('diagnostic'); sys.exit(9)",
        ),
        cwd=tmp_path / "work",
        log_path=log_path,
    )

    assert result.exit_code == 9
    assert not result.succeeded
    assert log_path.read_text(encoding="utf-8") == "diagnostic\n"
