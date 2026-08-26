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

"""Adapter for a local Git checkout."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from replay_canary.adapters.process import ProcessRunner
from replay_canary.errors import CommandExecutionError, ValidationError
from replay_canary.model.common import CommitMetadata

#: Symbolic Git revision naming the current checkout.
CURRENT_REVISION = "HEAD"


@dataclass(frozen=True)
class WorktreeStatus:
    """Tracked and untracked changes reported by Git porcelain output."""

    #: Raw porcelain status entries.
    entries: tuple[str, ...]

    @property
    def clean(self) -> bool:
        """Whether the worktree contains no tracked or untracked changes.

        :return: ``True`` when the worktree is clean.
        """

        return not self.entries


class GitRepository:
    """Query and check out revisions in one Git worktree."""

    def __init__(self, path: Path, runner: ProcessRunner) -> None:
        """Store the checkout path and process runner.

        :param path: Git worktree path.
        :param runner: Process runner for Git commands.
        """

        #: Resolved path to the Git worktree.
        self.path = path.resolve()
        #: Process runner used for Git commands.
        self._runner = runner

    def validate(self) -> None:
        """Require the configured path to be a Git worktree."""

        if not self.path.is_dir():
            raise ValidationError(f"not a Git worktree: {self.path}")
        try:
            is_worktree = self._git("rev-parse", "--is-inside-work-tree")
        except CommandExecutionError as error:
            raise ValidationError(f"not a Git worktree: {self.path}") from error
        if is_worktree != "true":
            raise ValidationError(f"not a Git worktree: {self.path}")

    def resolve(self, revision: str = CURRENT_REVISION) -> str:
        """Resolve a revision to one concrete commit hash.

        :param revision: Revision accepted by Git.
        :return: Full commit hash.
        """

        if not revision.strip():
            raise ValidationError("Git revision must not be empty")
        return self._git(
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{revision}^{{commit}}",
        )

    def head(self) -> str:
        """Return the current concrete HEAD commit.

        :return: Full HEAD commit hash.
        """

        return self.resolve(CURRENT_REVISION)

    def commit(self, revision: str = CURRENT_REVISION) -> CommitMetadata:
        """Read commit metadata for a revision.

        :param revision: Revision accepted by Git.
        :return: Commit metadata.
        """

        output = self._git(
            "show",
            "-s",
            "--format=%H%x00%P%x00%cI%x00%aN%x00%s",
            "--end-of-options",
            revision,
            "--",
        )
        fields = output.split("\x00")
        if len(fields) != 5:
            raise ValidationError(f"unexpected Git metadata for revision {revision}")
        hash_value, parents, timestamp, author, subject = fields
        try:
            committed_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValidationError(
                f"invalid Git timestamp for revision {revision}: {timestamp}"
            ) from error
        return CommitMetadata(
            hash=hash_value,
            committed_at=committed_at,
            subject=subject,
            author_name=author,
            parent_hashes=tuple(parents.split()) if parents else (),
        )

    def status(self) -> WorktreeStatus:
        """Return all tracked and untracked worktree changes.

        :return: Parsed porcelain status.
        """

        output = self._git("status", "--porcelain=v1", "--untracked-files=all")
        return WorktreeStatus(tuple(output.splitlines()) if output else ())

    def checkout(self, revision: str) -> None:
        """Check out a concrete revision without cleaning or resetting.

        :param revision: Concrete revision to check out.
        """

        self._git("checkout", "--detach", revision)

    def _git(self, *arguments: str) -> str:
        """Run Git in the configured worktree and strip its output.

        :param arguments: Arguments passed to Git.
        :return: Stripped standard output.
        """

        return self._runner.capture(("git", *arguments), cwd=self.path).rstrip("\n")
