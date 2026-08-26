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

"""Shared compiler-environment selection for record and replay."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from replay_canary.adapters.git import WorktreeStatus
from replay_canary.errors import ValidationError
from replay_canary.model.common import CommitMetadata


class CompilerGit(Protocol):
    """Git operations required to inspect the current compiler checkout."""

    def validate(self) -> None:
        """Validate the worktree."""

    def status(self) -> WorktreeStatus:
        """Return the current worktree status."""

    def commit(self) -> CommitMetadata:
        """Return metadata for the current commit."""


class CompilerMx(Protocol):
    """Read-only mx operations required to locate an existing build."""

    def version(self) -> str:
        """Return the mx version output."""

    def labsjdk_home(self) -> Path:
        """Return the selected LabsJDK home."""

    def graalvm_home(self, java_home: Path) -> Path:
        """Return the validated GraalVM home.

        :param java_home: Builder JDK home.
        :return: GraalVM home containing ``bin/java``.
        """


@dataclass(frozen=True)
class CompilerEnvironment:
    """Concrete compiler checkout and executable paths for one command."""

    #: Metadata for the concrete commit.
    commit: CommitMetadata
    #: Home of the builder JDK.
    java_home: Path
    #: Home of the selected GraalVM.
    graalvm_home: Path
    #: mx version used by the command.
    mx_version: str


class CompilerEnvironmentProvider(Protocol):
    """Current compiler environment used by recording and replay."""

    def load(self) -> CompilerEnvironment:
        """Return the validated current compiler environment.

        :return: Resolved compiler environment.
        """


class CompilerEnvironmentLoader:
    """Validate and describe the current compiler checkout and build."""

    def __init__(self, git: CompilerGit, mx: CompilerMx) -> None:
        """Store Git and mx selection adapters.

        :param git: Git operations for the Graal checkout.
        :param mx: Read-only mx operations for existing build paths.
        """

        #: Git operations for the Graal checkout.
        self._git = git
        #: Read-only mx operations for existing build paths.
        self._mx = mx

    def load(self) -> CompilerEnvironment:
        """Return the validated current compiler environment.

        :return: Resolved compiler environment.
        """

        self._git.validate()
        status = self._git.status()
        if not status.clean:
            raise ValidationError(
                "Graal worktree is dirty; commit or stash changes before recording or replaying"
            )
        commit = self._git.commit()
        mx_version = self._mx.version()
        java_home = self._mx.labsjdk_home()
        graalvm_home = self._mx.graalvm_home(java_home)
        return CompilerEnvironment(
            commit=commit,
            java_home=java_home,
            graalvm_home=graalvm_home,
            mx_version=mx_version,
        )
