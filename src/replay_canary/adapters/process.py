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

"""Subprocess execution with logging and timeouts."""

from __future__ import annotations

import logging
import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from replay_canary.errors import CommandExecutionError, ToolNotFoundError


@dataclass(frozen=True)
class ProcessResult:
    """Structured result of a logged external process."""

    #: Elapsed wall-clock time in seconds.
    duration_seconds: float
    #: Process exit code.
    exit_code: int
    #: Whether the process exceeded its timeout.
    timed_out: bool

    @property
    def succeeded(self) -> bool:
        """Whether the command finished successfully before its timeout.

        :return: ``True`` for a zero exit code without timeout.
        """

        return not self.timed_out and self.exit_code == 0


class ProcessRunner:
    """Run commands without a shell while preserving the caller's environment."""

    def __init__(self, logger: logging.Logger) -> None:
        """Store the logger used for command diagnostics.

        :param logger: Logger for command diagnostics.
        """

        #: Logger used for command diagnostics.
        self._logger = logger

    def capture(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
    ) -> str:
        """Run a short command and return stdout, raising on failure.

        :param command: Executable and arguments.
        :param cwd: Existing process working directory.
        :param env: Environment overrides, if any.
        :return: Standard output.
        """

        normalized = _command(command)
        cwd = cwd.resolve()
        if not cwd.is_dir():
            raise CommandExecutionError(f"working directory does not exist: {cwd}")
        self._logger.debug("exec (cwd=%s): %s", cwd, shlex.join(normalized))
        try:
            completed = subprocess.run(
                normalized,
                cwd=cwd,
                env=_merged_environment(env),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except FileNotFoundError as error:
            raise ToolNotFoundError(f"tool not found: {normalized[0]}") from error
        if completed.returncode != 0:
            detail = "\n".join(
                value
                for value in (completed.stdout.strip(), completed.stderr.strip())
                if value
            )
            suffix = f": {detail}" if detail else ""
            raise CommandExecutionError(
                f"command failed with exit code {completed.returncode}: "
                f"{shlex.join(normalized)}{suffix}"
            )
        return completed.stdout

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
        """Run a command in a process group, combining output into a log.

        :param command: Executable and arguments.
        :param cwd: Process working directory, created if necessary.
        :param timeout_seconds: Maximum execution time.
        :param log_path: File receiving combined output.
        :param env: Environment overrides, if any.
        :param terminate_grace_seconds: Time allowed after SIGTERM.
        :return: Structured process result.
        """

        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if terminate_grace_seconds < 0:
            raise ValueError("terminate_grace_seconds must not be negative")
        normalized = _command(command)
        cwd = cwd.resolve()
        log_path = log_path.resolve()
        cwd.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        monotonic_start = time.monotonic()
        timed_out = False
        self._logger.debug(
            "exec (timeout=%.1fs, cwd=%s): %s",
            timeout_seconds,
            cwd,
            shlex.join(normalized),
        )
        try:
            with log_path.open("w", encoding="utf-8") as output_file:
                process = subprocess.Popen(
                    normalized,
                    cwd=cwd,
                    env=_merged_environment(env),
                    stdout=output_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                try:
                    exit_code = process.wait(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    self._logger.warning(
                        "command timed out: %s", shlex.join(normalized)
                    )
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        exit_code = process.wait(timeout=terminate_grace_seconds)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        exit_code = process.wait()
        except FileNotFoundError as error:
            raise ToolNotFoundError(f"tool not found: {normalized[0]}") from error
        duration = time.monotonic() - monotonic_start
        return ProcessResult(
            duration_seconds=duration,
            exit_code=exit_code,
            timed_out=timed_out,
        )

    def logged(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_path: Path,
        env: Mapping[str, str] | None = None,
    ) -> ProcessResult:
        """Run a command without a timeout, combining output into a log.

        :param command: Executable and arguments.
        :param cwd: Process working directory, created if necessary.
        :param log_path: File receiving combined output.
        :param env: Environment overrides, if any.
        :return: Structured process result.
        """

        normalized = _command(command)
        cwd = cwd.resolve()
        log_path = log_path.resolve()
        cwd.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        monotonic_start = time.monotonic()
        self._logger.debug("exec (cwd=%s): %s", cwd, shlex.join(normalized))
        try:
            with log_path.open("w", encoding="utf-8") as output_file:
                completed = subprocess.run(
                    normalized,
                    cwd=cwd,
                    env=_merged_environment(env),
                    stdout=output_file,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
        except FileNotFoundError as error:
            raise ToolNotFoundError(f"tool not found: {normalized[0]}") from error
        duration = time.monotonic() - monotonic_start
        return ProcessResult(
            duration_seconds=duration,
            exit_code=completed.returncode,
            timed_out=False,
        )


def _command(command: Sequence[str]) -> tuple[str, ...]:
    """Validate and freeze a command sequence.

    :param command: Executable and arguments.
    :return: Validated immutable command.
    """

    result = tuple(command)
    if not result or any(not isinstance(item, str) or not item for item in result):
        raise ValueError("command must contain non-empty string arguments")
    return result


def _merged_environment(overrides: Mapping[str, str] | None) -> dict[str, str]:
    """Return the current environment with optional overrides.

    :param overrides: Values to add or replace, if any.
    :return: Complete child-process environment.
    """

    environment = os.environ.copy()
    if overrides:
        environment.update(overrides)
    return environment
