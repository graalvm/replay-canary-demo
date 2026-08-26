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

"""Git adapter tests."""

import logging
import os
import subprocess
from pathlib import Path

import pytest

from replay_canary.adapters.git import GitRepository
from replay_canary.adapters.process import ProcessRunner
from replay_canary.errors import CommandExecutionError, ValidationError


def git(repository: Path, *arguments: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=repository,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    )
    return completed.stdout.strip()


def create_repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.name", "Replay canary test")
    git(repository, "config", "user.email", "test@example.com")
    (repository / "tracked.txt").write_text("initial\n", encoding="utf-8")
    git(repository, "add", "tracked.txt")
    environment = os.environ.copy()
    environment["GIT_AUTHOR_DATE"] = "2026-07-30T10:00:00+00:00"
    environment["GIT_COMMITTER_DATE"] = "2026-07-30T10:00:00+00:00"
    git(repository, "commit", "-m", "Initial commit", env=environment)
    return repository, git(repository, "rev-parse", "HEAD")


def adapter(path: Path) -> GitRepository:
    return GitRepository(path, ProcessRunner(logging.getLogger("test")))


def test_resolve_head_branch_hash_and_commit_metadata(tmp_path: Path) -> None:
    repository, commit_hash = create_repository(tmp_path)
    repo = adapter(repository)

    repo.validate()
    assert repo.head() == commit_hash
    assert repo.resolve("main") == commit_hash
    assert repo.resolve(commit_hash) == commit_hash
    commit = repo.commit("main")
    assert commit.hash == commit_hash
    assert commit.parent_hashes == ()
    assert commit.subject == "Initial commit"
    assert commit.author_name == "Replay canary test"
    assert commit.committed_at.isoformat() == "2026-07-30T10:00:00+00:00"


@pytest.mark.parametrize("revision", ("missing", "--no-patch"))
def test_invalid_revision_fails(tmp_path: Path, revision: str) -> None:
    repository, _ = create_repository(tmp_path)

    with pytest.raises(CommandExecutionError, match=revision):
        adapter(repository).resolve(revision)


def test_status_includes_tracked_and_untracked_changes(tmp_path: Path) -> None:
    repository, _ = create_repository(tmp_path)
    repo = adapter(repository)
    assert repo.status().clean

    (repository / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (repository / "untracked.txt").write_text("new\n", encoding="utf-8")

    status = repo.status()
    assert not status.clean
    assert status.entries == (" M tracked.txt", "?? untracked.txt")


def test_checkout_detaches_at_concrete_revision(tmp_path: Path) -> None:
    repository, first_hash = create_repository(tmp_path)
    (repository / "tracked.txt").write_text("second\n", encoding="utf-8")
    git(repository, "add", "tracked.txt")
    git(repository, "commit", "-m", "Second commit")
    repo = adapter(repository)

    repo.checkout(first_hash)

    assert repo.head() == first_hash
    assert git(repository, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"


def test_checkout_failure_does_not_clean_changes(tmp_path: Path) -> None:
    repository, first_hash = create_repository(tmp_path)
    (repository / "tracked.txt").write_text("second\n", encoding="utf-8")
    git(repository, "add", "tracked.txt")
    git(repository, "commit", "-m", "Second commit")
    (repository / "tracked.txt").write_text("local change\n", encoding="utf-8")
    repo = adapter(repository)

    with pytest.raises(CommandExecutionError):
        repo.checkout(first_hash)

    assert (repository / "tracked.txt").read_text(encoding="utf-8") == "local change\n"
    assert not repo.status().clean


def test_validate_rejects_non_repository(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="not a Git worktree"):
        adapter(tmp_path).validate()
