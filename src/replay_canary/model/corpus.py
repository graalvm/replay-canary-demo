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

"""Replay corpus model."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from replay_canary.errors import DataFormatError, ValidationError
from replay_canary.model.common import (
    CommitMetadata,
    Identity,
    JsonObject,
    JsonValue,
    ProcessOutcome,
    reject_unknown_keys,
    require_int,
    require_list,
    require_object,
    require_string,
    require_string_tuple,
    validate_relative_path,
)

#: Replay file extensions stored in corpus artifacts, in preference order.
REPLAY_FILE_EXTENSIONS = (".replay", ".json")


@dataclass(frozen=True, order=True)
class RunKey:
    """Stable identity of one benchmark run."""

    #: Benchmark suite name.
    suite_name: str
    #: Workload name within the suite.
    workload_name: str
    #: Zero-based run index.
    run_index: int

    def __post_init__(self) -> None:
        """Validate the run key."""

        if not self.suite_name or not self.workload_name:
            raise ValidationError("run suite and workload names must not be empty")
        if any(
            character in self.suite_name + self.workload_name for character in "/\\\0"
        ):
            raise ValidationError("run suite and workload names must not be path-like")
        if self.run_index < 0:
            raise ValidationError("run_index must not be negative")

    @property
    def value(self) -> str:
        """Return a filesystem-safe stable key.

        :return: Stable run key text.
        """

        return f"{self.suite_name}--{self.workload_name}--{self.run_index}"

    def as_json(self) -> JsonObject:
        """Serialize the key.

        :return: JSON-compatible run key.
        """

        return {
            "suite_name": self.suite_name,
            "workload_name": self.workload_name,
            "run_index": self.run_index,
        }

    @classmethod
    def from_json(cls, value: object) -> "RunKey":
        """Parse a run key.

        :param value: Raw run key object.
        :return: Validated run key.
        """

        obj = require_object(value, "run key")
        reject_unknown_keys(
            obj, {"suite_name", "workload_name", "run_index"}, "run key"
        )
        return cls(
            require_string(obj, "suite_name"),
            require_string(obj, "workload_name"),
            require_int(obj, "run_index"),
        )


@dataclass(frozen=True)
class RecordingParameters:
    """Parameters that affect the contents of a corpus."""

    #: Number of recording runs per workload.
    runs: int
    #: Number of recent profiles used for hot-method selection.
    hot_window_size: int
    #: Minimum sample share used to select hot methods.
    hot_method_threshold: Decimal
    #: Profile sampling frequency in hertz.
    sampling_frequency: int
    #: Timeout for one benchmark process.
    timeout_seconds: int
    #: Maximum Java heap size.
    heap_size: str
    #: Additional JVM arguments used while recording, in command-line order.
    jvm_args: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate recording parameters."""

        if (
            min(
                self.runs,
                self.hot_window_size,
                self.sampling_frequency,
                self.timeout_seconds,
            )
            < 1
        ):
            raise ValidationError("recording integer parameters must be positive")
        if not self.hot_method_threshold.is_finite() or not (
            Decimal(0) <= self.hot_method_threshold <= Decimal(1)
        ):
            raise ValidationError(
                "hot_method_threshold must be at least 0 and at most 1"
            )
        if not self.heap_size:
            raise ValidationError("heap_size must not be empty")
        if any(not argument for argument in self.jvm_args):
            raise ValidationError("recording arguments must not be empty")

    def as_json(self) -> JsonObject:
        """Serialize recording parameters.

        :return: JSON-compatible recording parameters.
        """

        return {
            "runs": self.runs,
            "hot_window_size": self.hot_window_size,
            "hot_method_threshold": str(self.hot_method_threshold),
            "sampling_frequency": self.sampling_frequency,
            "timeout_seconds": self.timeout_seconds,
            "heap_size": self.heap_size,
            "jvm_args": list(self.jvm_args),
        }

    @classmethod
    def from_json(cls, value: object) -> "RecordingParameters":
        """Parse recording parameters.

        :param value: Raw recording parameters object.
        :return: Validated recording parameters.
        """

        obj = require_object(value, "recording_parameters")
        reject_unknown_keys(
            obj,
            {
                "runs",
                "hot_window_size",
                "hot_method_threshold",
                "sampling_frequency",
                "timeout_seconds",
                "heap_size",
                "jvm_args",
            },
            "recording_parameters",
        )
        try:
            threshold = Decimal(require_string(obj, "hot_method_threshold"))
        except InvalidOperation as error:
            raise DataFormatError(
                "recording_parameters.hot_method_threshold must be a decimal"
            ) from error
        return cls(
            runs=require_int(obj, "runs", minimum=1),
            hot_window_size=require_int(obj, "hot_window_size", minimum=1),
            hot_method_threshold=threshold,
            sampling_frequency=require_int(obj, "sampling_frequency", minimum=1),
            timeout_seconds=require_int(obj, "timeout_seconds", minimum=1),
            heap_size=require_string(obj, "heap_size"),
            jvm_args=require_string_tuple(obj, "jvm_args"),
        )


@dataclass(frozen=True)
class CorpusRun:
    """One benchmark recording outcome."""

    #: Stable key for the benchmark run.
    key: RunKey
    #: Relative directory containing replay files, if recording succeeded.
    replay_files: str | None
    #: Number of replay files selected from the recording.
    replayable_compilations: int
    #: Relative path to the recording log.
    log: str
    #: Benchmark process outcome.
    outcome: ProcessOutcome

    def __post_init__(self) -> None:
        """Validate paths and counts."""

        if self.replay_files is not None:
            validate_relative_path(self.replay_files, "replay_files")
        validate_relative_path(self.log, "log")
        if self.replayable_compilations < 0:
            raise ValidationError("replayable_compilations must not be negative")

    def as_json(self) -> JsonObject:
        """Serialize a corpus run.

        :return: JSON-compatible corpus run.
        """

        return {
            "key": self.key.as_json(),
            "replay_files": self.replay_files,
            "replayable_compilations": self.replayable_compilations,
            "log": self.log,
            "outcome": self.outcome.as_json(),
        }

    @classmethod
    def from_json(cls, value: object) -> "CorpusRun":
        """Parse a corpus run.

        :param value: Raw corpus run object.
        :return: Validated corpus run.
        """

        obj = require_object(value, "corpus run")
        reject_unknown_keys(
            obj,
            {
                "key",
                "replay_files",
                "replayable_compilations",
                "log",
                "outcome",
            },
            "corpus run",
        )
        replay_files = obj.get("replay_files")
        if replay_files is not None and not isinstance(replay_files, str):
            raise DataFormatError("replay_files must be a string or null")
        return cls(
            key=RunKey.from_json(obj.get("key")),
            replay_files=replay_files,
            replayable_compilations=require_int(obj, "replayable_compilations"),
            log=require_string(obj, "log"),
            outcome=ProcessOutcome.from_json(obj.get("outcome")),
        )


@dataclass(frozen=True)
class CorpusManifest:
    """Complete corpus manifest."""

    #: Immutable corpus identity.
    identity: Identity
    #: Resolved compiler commit metadata.
    commit: CommitMetadata
    #: Recorded suite names and versions.
    benchmark_suites: tuple[tuple[str, str], ...]
    #: Parameters used to record the corpus.
    recording_parameters: RecordingParameters
    #: GraalVM home used for recording.
    graalvm_home: str
    #: mx version used for recording.
    mx_version: str
    #: Recording outcomes in deterministic order.
    runs: tuple[CorpusRun, ...]

    def __post_init__(self) -> None:
        """Validate compiler metadata and run keys."""

        if not self.graalvm_home or not self.mx_version:
            raise ValidationError("corpus compiler metadata must not be empty")
        if len({run.key for run in self.runs}) != len(self.runs):
            raise ValidationError("corpus contains duplicate run keys")

    def as_json(self) -> JsonObject:
        """Serialize the corpus manifest.

        :return: JSON-compatible corpus manifest.
        """

        suites: list[JsonValue] = [
            {"name": name, "version": version}
            for name, version in self.benchmark_suites
        ]
        return {
            **self.identity.as_json(),
            "commit": self.commit.as_json(),
            "benchmark_suites": suites,
            "recording_parameters": self.recording_parameters.as_json(),
            "graalvm_home": self.graalvm_home,
            "mx_version": self.mx_version,
            "runs": [run.as_json() for run in self.runs],
        }

    @classmethod
    def from_json(cls, value: object) -> "CorpusManifest":
        """Parse a corpus manifest.

        :param value: Raw corpus manifest object.
        :return: Validated corpus manifest.
        """

        obj = require_object(value, "corpus manifest")
        reject_unknown_keys(
            obj,
            {
                "id",
                "label",
                "created_at",
                "commit",
                "benchmark_suites",
                "recording_parameters",
                "graalvm_home",
                "mx_version",
                "runs",
            },
            "corpus manifest",
        )
        suites: list[tuple[str, str]] = []
        for value_item in require_list(obj.get("benchmark_suites"), "benchmark_suites"):
            suite: dict[str, Any] = dict(require_object(value_item, "benchmark suite"))
            reject_unknown_keys(suite, {"name", "version"}, "benchmark suite")
            suites.append(
                (
                    require_string(suite, "name"),
                    require_string(suite, "version", allow_empty=True),
                )
            )
        return cls(
            identity=Identity.from_json(obj),
            commit=CommitMetadata.from_json(obj.get("commit")),
            benchmark_suites=tuple(suites),
            recording_parameters=RecordingParameters.from_json(
                obj.get("recording_parameters")
            ),
            graalvm_home=require_string(obj, "graalvm_home"),
            mx_version=require_string(obj, "mx_version"),
            runs=tuple(
                CorpusRun.from_json(run)
                for run in require_list(obj.get("runs"), "runs")
            ),
        )
