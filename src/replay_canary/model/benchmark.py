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

"""Benchmark suites exported by ``mx benchmark``."""

from __future__ import annotations

from dataclasses import dataclass

from replay_canary.errors import DataFormatError, ValidationError
from replay_canary.model.common import (
    reject_unknown_keys,
    require_list,
    require_object,
    require_string,
    require_string_tuple,
)


@dataclass(frozen=True, order=True)
class BenchmarkName:
    """A benchmark workload qualified by suite."""

    #: Benchmark suite name.
    suite_name: str
    #: Workload name within the suite.
    workload_name: str

    def __post_init__(self) -> None:
        """Validate both name components."""

        if not self.suite_name or not self.workload_name:
            raise ValidationError("benchmark suite and workload must not be empty")
        if any(
            character in self.suite_name + self.workload_name for character in "/\\\0"
        ):
            raise ValidationError("benchmark suite and workload must not be path-like")

    @classmethod
    def parse(cls, value: str) -> "BenchmarkName":
        """Parse the ``SUITE:WORKLOAD`` syntax.

        :param value: Qualified workload text.
        :return: Parsed benchmark name.
        """

        parts = value.split(":")
        if len(parts) != 2 or not all(parts):
            raise ValidationError(f"workload must use SUITE:WORKLOAD syntax: {value!r}")
        return cls(parts[0], parts[1])

    def __str__(self) -> str:
        """Return the ``SUITE:WORKLOAD`` form.

        :return: Qualified workload text.
        """

        return f"{self.suite_name}:{self.workload_name}"


@dataclass(frozen=True)
class Benchmark:
    """One exported benchmark Java command."""

    #: Workload name within its suite.
    name: str
    #: Java command arguments for the workload.
    arguments: tuple[str, ...]

    @classmethod
    def from_json(cls, value: object) -> "Benchmark":
        """Parse one exported benchmark.

        :param value: Raw benchmark object.
        :return: Validated benchmark command.
        """

        obj = require_object(value, "benchmark")
        reject_unknown_keys(obj, {"name", "args"}, "benchmark")
        return cls(
            name=require_string(obj, "name"),
            arguments=require_string_tuple(obj, "args"),
        )


@dataclass(frozen=True)
class BenchmarkSuite:
    """A benchmark suite exported by mx."""

    #: Suite name.
    name: str
    #: Version reported by mx.
    version: str
    #: Workloads in exported order.
    benchmarks: tuple[Benchmark, ...]

    def __post_init__(self) -> None:
        """Validate the suite name and workload names."""

        if not self.name:
            raise ValidationError("benchmark suite name must not be empty")
        names = [benchmark.name for benchmark in self.benchmarks]
        if len(set(names)) != len(names):
            raise ValidationError(
                f"benchmark suite {self.name!r} contains duplicate workloads"
            )

    @classmethod
    def from_json(cls, value: object) -> "BenchmarkSuite":
        """Parse an exported suite document.

        :param value: Raw suite object.
        :return: Validated benchmark suite.
        """

        obj = require_object(value, "benchmark suite")
        reject_unknown_keys(obj, {"name", "version", "benchmarks"}, "benchmark suite")
        benchmarks = tuple(
            Benchmark.from_json(item)
            for item in require_list(obj.get("benchmarks"), "benchmarks")
        )
        if not benchmarks:
            raise DataFormatError("benchmark suite contains no workloads")
        return cls(
            name=require_string(obj, "name"),
            version=require_string(obj, "version", allow_empty=True),
            benchmarks=benchmarks,
        )

    def workload_names(self) -> tuple[str, ...]:
        """Return workload names in exported order.

        :return: Workload names in exported order.
        """

        return tuple(benchmark.name for benchmark in self.benchmarks)

    def benchmark(self, workload_name: str) -> Benchmark:
        """Return a workload by name.

        :param workload_name: Workload name within the suite.
        :return: Matching benchmark command.
        """

        for benchmark in self.benchmarks:
            if benchmark.name == workload_name:
                return benchmark
        raise ValidationError(
            f"benchmark suite {self.name!r} has no workload {workload_name!r}"
        )
