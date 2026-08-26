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

"""Compiler preparation tests."""

from datetime import datetime, timezone
from pathlib import Path

from replay_canary.adapters.git import WorktreeStatus
from replay_canary.model.common import CommitMetadata
from replay_canary.services.compiler import CompilerEnvironmentLoader


class FakeGit:
    def __init__(self) -> None:
        self.commit_value = CommitMetadata(
            hash="b" * 40,
            parent_hashes=("a" * 40,),
            committed_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
            subject="Selected compiler",
            author_name="Contributor",
        )

    def validate(self) -> None:
        pass

    def status(self) -> WorktreeStatus:
        return WorktreeStatus(())

    def commit(self) -> CommitMetadata:
        return self.commit_value


class FakeMx:
    def __init__(self, tmp_path: Path) -> None:
        self.java_home = tmp_path / "labsjdk"
        self.graalvm_home_value = tmp_path / "graalvm"
        self.calls: list[str | tuple[str, Path]] = []

    def version(self) -> str:
        self.calls.append("version")
        return "mx version test"

    def labsjdk_home(self) -> Path:
        self.calls.append("labsjdk_home")
        return self.java_home

    def graalvm_home(self, java_home: Path) -> Path:
        self.calls.append(("graalvm_home", java_home))
        return self.graalvm_home_value


def test_loads_current_environment_without_build_operations(tmp_path: Path) -> None:
    git = FakeGit()
    mx = FakeMx(tmp_path)

    environment = CompilerEnvironmentLoader(git, mx).load()

    assert environment.commit.hash == "b" * 40
    assert environment.java_home == mx.java_home
    assert environment.graalvm_home == mx.graalvm_home_value
    assert mx.calls == [
        "version",
        "labsjdk_home",
        ("graalvm_home", mx.java_home),
    ]
