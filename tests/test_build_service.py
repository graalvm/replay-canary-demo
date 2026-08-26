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

"""Build service tests."""

import logging
from pathlib import Path

import pytest

from replay_canary.adapters.git import WorktreeStatus
from replay_canary.adapters.local_store import DataLayout
from replay_canary.errors import BuildError
from replay_canary.services.build import BuildService


class FakeGit:
    def __init__(self, *, clean: bool = True, head: str = "a" * 40) -> None:
        self.clean = clean
        self.head_value = head
        self.resolved = "a" * 40
        self.calls: list[str | tuple[object, ...]] = []

    def validate(self) -> None:
        self.calls.append("validate")

    def resolve(self, revision: str = "HEAD") -> str:
        self.calls.append(("resolve", revision))
        return self.resolved

    def head(self) -> str:
        self.calls.append("head")
        return self.head_value

    def status(self) -> WorktreeStatus:
        self.calls.append("status")
        return WorktreeStatus(()) if self.clean else WorktreeStatus((" M file",))

    def checkout(self, revision: str) -> None:
        self.calls.append(("checkout", revision))


class FakeMx:
    def __init__(self, tmp_path: Path) -> None:
        self.java_home = tmp_path / "labsjdk"
        self.graalvm_home_value = tmp_path / "graalvm"
        self.calls: list[str | tuple[object, ...]] = []
        self.failure: str | None = None

    def version(self) -> str:
        self.calls.append("version")
        return "mx version test"

    def fetch_labsjdk(self, log_path: Path) -> None:
        self.calls.append(("fetch_labsjdk", log_path))
        self._fail("fetch")

    def labsjdk_home(self) -> Path:
        self.calls.append("labsjdk_home")
        self._fail("jdk")
        return self.java_home

    def build_libgraal(self, java_home: Path, log_path: Path) -> None:
        self.calls.append(("build_libgraal", java_home, log_path))
        self._fail("libgraal")

    def build_replay_dependencies(self, java_home: Path, log_path: Path) -> None:
        self.calls.append(("build_replay_dependencies", java_home, log_path))
        self._fail("dependencies")

    def graalvm_home(self, java_home: Path) -> Path:
        self.calls.append(("graalvm_home", java_home))
        self._fail("home")
        return self.graalvm_home_value

    def _fail(self, operation: str) -> None:
        if self.failure == operation:
            raise BuildError(f"{operation} failed")


def service(
    tmp_path: Path,
    git: FakeGit,
    mx: FakeMx,
    *,
    system: str = "Linux",
    machine: str = "x86_64",
) -> BuildService:
    return BuildService(
        git,
        mx,
        DataLayout(tmp_path / "data"),
        logging.getLogger("test.build_service"),
        system=lambda: system,
        machine=lambda: machine,
    )


def test_build_at_head_does_not_detach_or_leave_workspace(tmp_path: Path) -> None:
    git = FakeGit()
    mx = FakeMx(tmp_path)

    result = service(tmp_path, git, mx).build("HEAD")

    assert result.revision == "a" * 40
    assert result.graalvm_home == mx.graalvm_home_value
    assert not any(
        isinstance(call, tuple) and call[0] == "checkout" for call in git.calls
    )
    assert list((tmp_path / "data" / "work").iterdir()) == []
    assert [call if isinstance(call, str) else call[0] for call in mx.calls] == [
        "version",
        "fetch_labsjdk",
        "labsjdk_home",
        "build_libgraal",
        "build_replay_dependencies",
        "graalvm_home",
    ]


def test_build_checks_out_different_revision_detached_via_adapter(
    tmp_path: Path,
) -> None:
    git = FakeGit(head="a" * 40)
    git.resolved = "b" * 40
    mx = FakeMx(tmp_path)

    result = service(tmp_path, git, mx).build("feature")

    assert result.revision == "b" * 40
    assert ("checkout", "b" * 40) in git.calls


def test_dirty_worktree_is_rejected_before_mx_or_checkout(tmp_path: Path) -> None:
    git = FakeGit(clean=False)
    git.resolved = "b" * 40
    mx = FakeMx(tmp_path)

    with pytest.raises(BuildError, match="dirty"):
        service(tmp_path, git, mx).build("feature")

    assert mx.calls == []
    assert not any(
        isinstance(call, tuple) and call[0] == "checkout" for call in git.calls
    )


def test_build_failure_retains_workspace_and_is_not_retried(tmp_path: Path) -> None:
    git = FakeGit()
    mx = FakeMx(tmp_path)
    mx.failure = "libgraal"

    with pytest.raises(BuildError, match="workspace retained at"):
        service(tmp_path, git, mx).build()

    workspaces = list((tmp_path / "data" / "work").iterdir())
    assert len(workspaces) == 1
    assert [
        call
        for call in mx.calls
        if isinstance(call, tuple) and call[0] == "build_libgraal"
    ]
    assert not any(
        isinstance(call, tuple) and call[0] == "build_replay_dependencies"
        for call in mx.calls
    )


@pytest.mark.parametrize(
    "system,machine",
    [("Darwin", "arm64"), ("Linux", "aarch64"), ("Windows", "AMD64")],
)
def test_build_rejects_unsupported_platform(
    tmp_path: Path, system: str, machine: str
) -> None:
    git = FakeGit()
    mx = FakeMx(tmp_path)

    with pytest.raises(BuildError, match="requires x86-64 Linux"):
        service(tmp_path, git, mx, system=system, machine=machine).build()

    assert git.calls == []
    assert mx.calls == []
