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

"""Benchmark tests."""

import pytest

from replay_canary.errors import DataFormatError, ValidationError
from replay_canary.model.benchmark import BenchmarkName, BenchmarkSuite


def test_benchmark_name_parses_public_selector() -> None:
    name = BenchmarkName.parse("renaissance:scrabble")
    assert name.suite_name == "renaissance"
    assert name.workload_name == "scrabble"
    assert str(name) == "renaissance:scrabble"


@pytest.mark.parametrize("value", ["", "suite", ":work", "suite:", "a:b:c"])
def test_benchmark_name_rejects_invalid_selector(value: str) -> None:
    with pytest.raises(ValidationError, match="SUITE:WORKLOAD"):
        BenchmarkName.parse(value)


def test_benchmark_suite_parses_and_selects_exact_workload() -> None:
    suite = BenchmarkSuite.from_json(
        {
            "name": "suite",
            "version": "1.0",
            "benchmarks": [
                {"name": "one", "args": ["-jar", "one.jar"]},
                {"name": "two", "args": ["-jar", "two.jar"]},
            ],
        }
    )

    assert suite.workload_names() == ("one", "two")
    assert suite.benchmark("two").arguments == ("-jar", "two.jar")
    with pytest.raises(ValidationError, match="no workload"):
        suite.benchmark("missing")


def test_benchmark_suite_rejects_duplicate_workloads() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        BenchmarkSuite.from_json(
            {
                "name": "suite",
                "version": "1",
                "benchmarks": [
                    {"name": "same", "args": []},
                    {"name": "same", "args": []},
                ],
            }
        )


def test_benchmark_suite_rejects_unknown_export_fields() -> None:
    with pytest.raises(DataFormatError, match="unknown field"):
        BenchmarkSuite.from_json(
            {
                "name": "suite",
                "version": "1",
                "benchmarks": [{"name": "one", "args": []}],
                "unexpected": True,
            }
        )
