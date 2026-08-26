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

"""Build command orchestration."""

from __future__ import annotations

import logging
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from replay_canary.adapters.git import CURRENT_REVISION, WorktreeStatus
from replay_canary.adapters.local_store import DataLayout
from replay_canary.errors import BuildError


class BuildGit(Protocol):
    """Git operations required by the build service."""

    def validate(self) -> None:
        """Validate the worktree."""

    def resolve(self, revision: str = CURRENT_REVISION) -> str:
        """Resolve a revision.

        :param revision: Revision accepted by Git.
        :return: Full commit hash.
        """

    def head(self) -> str:
        """Return the full commit hash at HEAD."""

    def status(self) -> WorktreeStatus:
        """Return the current worktree status."""

    def checkout(self, revision: str) -> None:
        """Check out a concrete revision.

        :param revision: Concrete revision to check out.
        """


class BuildMx(Protocol):
    """mx operations required by the build service."""

    def version(self) -> str:
        """Return the mx version output."""

    def fetch_labsjdk(self, log_path: Path) -> None:
        """Fetch the builder LabsJDK.

        :param log_path: File receiving command output.
        """

    def labsjdk_home(self) -> Path:
        """Return the selected LabsJDK home."""

    def build_libgraal(self, java_home: Path, log_path: Path) -> None:
        """Build libgraal.

        :param java_home: Builder JDK home.
        :param log_path: File receiving command output.
        """

    def build_replay_dependencies(self, java_home: Path, log_path: Path) -> None:
        """Build the replay launcher dependencies.

        :param java_home: Builder JDK home.
        :param log_path: File receiving command output.
        """

    def graalvm_home(self, java_home: Path) -> Path:
        """Return the validated GraalVM home.

        :param java_home: Builder JDK home.
        :return: GraalVM home containing ``bin/java``.
        """


@dataclass(frozen=True)
class BuildResult:
    """Successful compiler build details."""

    #: Concrete compiler commit hash.
    revision: str
    #: Home of the built GraalVM.
    graalvm_home: Path


class BuildService:
    """Build one concrete GraalVM version."""

    def __init__(
        self,
        git: BuildGit,
        mx: BuildMx,
        layout: DataLayout,
        logger: logging.Logger,
        *,
        system: Callable[[], str] = platform.system,
        machine: Callable[[], str] = platform.machine,
    ) -> None:
        """Store build adapters, logging, and platform providers.

        :param git: Git operations for the Graal checkout.
        :param mx: mx build operations.
        :param layout: Local data layout for build logs.
        :param logger: Logger receiving build progress.
        :param system: Provider for the operating-system name.
        :param machine: Provider for the machine architecture.
        """

        #: Git operations for the Graal checkout.
        self._git = git
        #: mx build operations.
        self._mx = mx
        #: Local data layout for build logs.
        self._layout = layout
        #: Logger receiving build progress.
        self._logger = logger
        #: Provider for the operating-system name.
        self._system = system
        #: Provider for the machine architecture.
        self._machine = machine

    def build(self, revision: str = CURRENT_REVISION) -> BuildResult:
        """Run the build sequence.

        :param revision: Git revision to build.
        :return: Built commit hash and GraalVM home.
        """

        self._validate_platform()
        self._git.validate()
        status = self._git.status()
        if not status.clean:
            raise BuildError(
                "Graal worktree is dirty; commit or stash changes before building"
            )

        concrete_revision = self._git.resolve(revision)
        if concrete_revision != self._git.head():
            self._git.checkout(concrete_revision)

        self._logger.info("building GraalVM at revision %s", concrete_revision)
        work_directory = self._layout.create_work_directory("build")
        try:
            self._mx.version()
            self._logger.info("fetching LabsJDK")
            self._mx.fetch_labsjdk(work_directory / "fetch-jdk.log")
            java_home = self._mx.labsjdk_home()
            self._logger.info("building libgraal")
            self._mx.build_libgraal(java_home, work_directory / "libgraal-build.log")
            self._logger.info("building replay dependencies")
            self._mx.build_replay_dependencies(
                java_home, work_directory / "replay-dependencies-build.log"
            )
            graalvm_home = self._mx.graalvm_home(java_home)
        except Exception as error:
            raise BuildError(
                f"{error}; build workspace retained at {work_directory}"
            ) from error
        shutil.rmtree(work_directory)
        return BuildResult(
            revision=concrete_revision,
            graalvm_home=graalvm_home,
        )

    def _validate_platform(self) -> None:
        """Require x86-64 Linux."""

        system = self._system()
        machine = self._machine().lower()
        if system != "Linux" or machine not in {"amd64", "x86_64"}:
            raise BuildError(
                f"Replay canary requires x86-64 Linux; found {system} {machine}"
            )
