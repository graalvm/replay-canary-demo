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

"""CLI and logging tests."""

from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest

from replay_canary.cli import _log_comparison_result, create_parser
from replay_canary.logging import configure_logging
from replay_canary.services.compare import CompareResult


def test_all_commands_have_help() -> None:
    parser = create_parser()
    for command in ("build", "record", "replay", "compare"):
        with pytest.raises(SystemExit) as exit_info:
            parser.parse_args((command, "--help"))
        assert exit_info.value.code == 0


def test_compare_does_not_accept_a_corpus() -> None:
    parser = create_parser()
    with pytest.raises(SystemExit) as exit_info:
        parser.parse_args(
            (
                "compare",
                "--baseline",
                "before",
                "--candidate",
                "after",
                "--corpus",
                "wrong",
            )
        )
    assert exit_info.value.code == 2


def test_record_accepts_repeated_workloads() -> None:
    namespace = create_parser().parse_args(
        (
            "record",
            "--workload",
            "renaissance:scrabble",
            "--workload",
            "dacapo:fop",
        )
    )
    assert namespace.workload == ["renaissance:scrabble", "dacapo:fop"]


def test_record_rejects_empty_additional_argument() -> None:
    with pytest.raises(SystemExit) as exit_info:
        create_parser().parse_args(("record", "--jvm-arg="))

    assert exit_info.value.code == 2


def test_global_graal_repository_override_is_parsed() -> None:
    namespace = create_parser().parse_args(
        (
            "--graal-repository",
            "../graal-variant-a",
            "replay",
            "--corpus",
            "full-corpus",
        )
    )

    assert str(namespace.graal_repository) == "../graal-variant-a"


def test_replay_accepts_repeated_jvm_arguments() -> None:
    namespace = create_parser().parse_args(
        (
            "replay",
            "--corpus",
            "full-corpus",
            "--replay-arg=-Djdk.graal.FullUnroll=false",
            "--replay-arg=-Xlog:gc",
            "--replay-arg=-XX:CompileOnly=example/Method.run",
        )
    )

    assert namespace.replay_arg == [
        "-Djdk.graal.FullUnroll=false",
        "-Xlog:gc",
        "-XX:CompileOnly=example/Method.run",
    ]


def test_replay_rejects_non_jvm_argument() -> None:
    parser = create_parser()

    with pytest.raises(SystemExit) as exit_info:
        parser.parse_args(
            (
                "replay",
                "--corpus",
                "full-corpus",
                "--replay-arg=--compare-graphs=true",
            )
        )

    assert exit_info.value.code == 2


def test_console_is_brief_on_stdout_while_debug_log_is_detailed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    debug_log = tmp_path / "debug.log"
    logger = configure_logging(False, debug_log)

    logger.info("record run 1/1: suite:workload")
    logger.debug("exec: mx benchmark --internal-detail")
    for handler in logger.handlers:
        handler.flush()

    captured = capsys.readouterr()
    assert captured.out == "INFO: record run 1/1: suite:workload\n"
    assert captured.err == ""
    debug_output = debug_log.read_text(encoding="utf-8")
    assert "record run 1/1: suite:workload" in debug_output
    assert "exec: mx benchmark --internal-detail" in debug_output


def test_compare_logs_artifact_locations_and_markdown_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    summary = "# Replay canary comparison: changes found\n\nDetails.\n"
    (tmp_path / "summary.md").write_text(summary, encoding="utf-8")
    result = Mock()
    result.manifest.identity.id = "comparison-id"
    result.manifest.report = "report.html"
    result.manifest.summary = "summary.md"
    result.path = tmp_path
    logger = configure_logging(False)

    _log_comparison_result(cast(CompareResult, result), logger)

    assert capsys.readouterr().out == (
        "INFO: comparison ID: comparison-id\n"
        f"INFO: comparison directory: {tmp_path}\n"
        f"INFO: HTML report: {tmp_path / 'report.html'}\n"
        f"INFO: comparison summary:\n{summary}"
    )
