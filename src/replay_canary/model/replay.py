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

"""Replay manifest and metric models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from replay_canary.errors import DataFormatError, ValidationError
from replay_canary.model.common import (
    CommitMetadata,
    Identity,
    JsonObject,
    ProcessOutcome,
    reject_unknown_keys,
    require_int,
    require_list,
    require_object,
    require_string,
    require_string_tuple,
    validate_id,
    validate_relative_path,
)
from replay_canary.model.corpus import RunKey


def validate_replay_argument(value: str) -> None:
    """Validate one additional JVM argument passed to ``mx replaycomp``.

    :param value: Raw JVM argument.
    """

    if not value.startswith(("-D", "-X")):
        raise ValidationError("replay argument must start with -D or -X")


@dataclass(frozen=True)
class IterationMetrics:
    """Aggregate metrics for one replay iteration."""

    #: Zero-based replay iteration.
    iteration: int
    #: Compiler wall-clock time in nanoseconds.
    wall_time_ns: int
    #: Compiler thread time in nanoseconds.
    thread_time_ns: int
    #: Bytes allocated by the compiler.
    allocated_memory: int
    #: Number of compiled bytecodes.
    compiled_bytecodes: int
    #: Generated machine-code size in bytes.
    target_code_size: int
    #: Hash of the generated machine code.
    target_code_hash: str
    #: Number of retired instructions.
    retired_instructions: int

    def as_json(self) -> JsonObject:
        """Serialize iteration metrics.

        :return: JSON-compatible iteration metrics.
        """

        return {
            "iteration": self.iteration,
            "wall_time_ns": self.wall_time_ns,
            "thread_time_ns": self.thread_time_ns,
            "allocated_memory": self.allocated_memory,
            "compiled_bytecodes": self.compiled_bytecodes,
            "target_code_size": self.target_code_size,
            "target_code_hash": self.target_code_hash,
            "retired_instructions": self.retired_instructions,
        }

    @classmethod
    def from_json(cls, value: object) -> "IterationMetrics":
        """Parse normalized iteration metrics.

        :param value: Raw iteration metrics object.
        :return: Validated iteration metrics.
        """

        obj = require_object(value, "iteration metrics")
        reject_unknown_keys(
            obj,
            {
                "iteration",
                "wall_time_ns",
                "thread_time_ns",
                "allocated_memory",
                "compiled_bytecodes",
                "target_code_size",
                "target_code_hash",
                "retired_instructions",
            },
            "iteration metrics",
        )
        return cls(
            iteration=require_int(obj, "iteration"),
            wall_time_ns=require_int(obj, "wall_time_ns"),
            thread_time_ns=require_int(obj, "thread_time_ns"),
            allocated_memory=require_int(obj, "allocated_memory"),
            compiled_bytecodes=require_int(obj, "compiled_bytecodes"),
            target_code_size=require_int(obj, "target_code_size"),
            target_code_hash=require_string(obj, "target_code_hash", allow_empty=True),
            retired_instructions=require_int(obj, "retired_instructions"),
        )


@dataclass(frozen=True)
class CompilationMetrics:
    """Metrics for one compilation within one replay iteration."""

    #: Zero-based replay iteration.
    iteration: int
    #: Compiler ID from the replay file.
    compile_id: int
    #: Compiled method name.
    method_name: str
    #: Bytecode index where compilation started.
    entry_bci: int
    #: Compilation wall-clock time in nanoseconds.
    wall_time_ns: int
    #: Compilation thread time in nanoseconds.
    thread_time_ns: int
    #: Bytes allocated by the compilation.
    allocated_memory: int
    #: Number of compiled bytecodes.
    compiled_bytecodes: int
    #: Generated machine-code size in bytes.
    target_code_size: int
    #: Hash of the generated machine code.
    target_code_hash: str
    #: Number of retired instructions.
    retired_instructions: int

    def as_json(self) -> JsonObject:
        """Serialize compilation metrics.

        :return: JSON-compatible compilation metrics.
        """

        return {
            "iteration": self.iteration,
            "compile_id": self.compile_id,
            "method_name": self.method_name,
            "entry_bci": self.entry_bci,
            "wall_time_ns": self.wall_time_ns,
            "thread_time_ns": self.thread_time_ns,
            "allocated_memory": self.allocated_memory,
            "compiled_bytecodes": self.compiled_bytecodes,
            "target_code_size": self.target_code_size,
            "target_code_hash": self.target_code_hash,
            "retired_instructions": self.retired_instructions,
        }

    @classmethod
    def from_json(cls, value: object) -> "CompilationMetrics":
        """Parse normalized compilation metrics.

        :param value: Raw compilation metrics object.
        :return: Validated compilation metrics.
        """

        obj = require_object(value, "compilation metrics")
        reject_unknown_keys(
            obj,
            {
                "iteration",
                "compile_id",
                "method_name",
                "entry_bci",
                "wall_time_ns",
                "thread_time_ns",
                "allocated_memory",
                "compiled_bytecodes",
                "target_code_size",
                "target_code_hash",
                "retired_instructions",
            },
            "compilation metrics",
        )
        return cls(
            iteration=require_int(obj, "iteration"),
            compile_id=require_int(obj, "compile_id"),
            method_name=require_string(obj, "method_name"),
            entry_bci=require_int(obj, "entry_bci", minimum=-1),
            wall_time_ns=require_int(obj, "wall_time_ns"),
            thread_time_ns=require_int(obj, "thread_time_ns"),
            allocated_memory=require_int(obj, "allocated_memory"),
            compiled_bytecodes=require_int(obj, "compiled_bytecodes"),
            target_code_size=require_int(obj, "target_code_size"),
            target_code_hash=require_string(obj, "target_code_hash", allow_empty=True),
            retired_instructions=require_int(obj, "retired_instructions"),
        )


@dataclass(frozen=True)
class ReplayMetrics:
    """Normalized metrics stored for one replayed corpus run."""

    #: Aggregate metrics for each replay iteration.
    iterations: tuple[IterationMetrics, ...]
    #: Metrics for each compilation in each iteration.
    compilations: tuple[CompilationMetrics, ...]

    def __post_init__(self) -> None:
        """Reject duplicate iteration and compilation keys."""

        iteration_keys = [item.iteration for item in self.iterations]
        if len(set(iteration_keys)) != len(iteration_keys):
            raise ValidationError("replay metrics contain duplicate iterations")
        compilation_keys = [
            (item.iteration, item.compile_id) for item in self.compilations
        ]
        if len(set(compilation_keys)) != len(compilation_keys):
            raise ValidationError("replay metrics contain duplicate compilations")

    def as_json(self) -> JsonObject:
        """Serialize normalized replay metrics.

        :return: JSON-compatible replay metrics.
        """

        return {
            "iterations": [item.as_json() for item in self.iterations],
            "compilations": [item.as_json() for item in self.compilations],
        }

    @classmethod
    def from_json(cls, value: object) -> "ReplayMetrics":
        """Parse normalized replay metrics.

        :param value: Raw replay metrics object.
        :return: Validated replay metrics.
        """

        obj = require_object(value, "replay metrics")
        reject_unknown_keys(obj, {"iterations", "compilations"}, "replay metrics")
        return cls(
            iterations=tuple(
                IterationMetrics.from_json(item)
                for item in require_list(obj.get("iterations"), "iterations")
            ),
            compilations=tuple(
                CompilationMetrics.from_json(item)
                for item in require_list(obj.get("compilations"), "compilations")
            ),
        )

    @classmethod
    def from_launcher_json(
        cls, value: object, *, retired_instruction_event: str
    ) -> "ReplayMetrics":
        """Parse the JSON array emitted by ``mx replaycomp --benchmark``.

        :param value: Raw launcher result array.
        :param retired_instruction_event: Event containing retired instructions.
        :return: Normalized replay metrics.
        """

        records = require_list(value, "replay launcher results")
        iterations: list[IterationMetrics] = []
        compilations: list[CompilationMetrics] = []
        for value_item in records:
            record = require_object(value_item, "replay launcher record")
            record_type = record.get("type")
            if record_type == "iteration_total":
                iterations.append(
                    _iteration_from_launcher(record, retired_instruction_event)
                )
            elif record_type == "compilation":
                compilations.append(
                    _compilation_from_launcher(record, retired_instruction_event)
                )
        return cls(tuple(iterations), tuple(compilations))


def _retired_instructions(record: Mapping[str, Any], event_name: str) -> int:
    """Read the retired-instruction counter from a launcher record.

    :param record: Launcher record.
    :param event_name: Event containing retired instructions.
    :return: Retired-instruction count, or zero when absent.
    """

    events = record.get("events", {})
    if not isinstance(events, dict):
        return 0
    return _launcher_int(events, event_name)


def _launcher_int(record: Mapping[str, Any], key: str, *, minimum: int = 0) -> int:
    """Read an integer field from a launcher record.

    :param record: Launcher record.
    :param key: Field name.
    :param minimum: Smallest accepted value.
    :return: Parsed integer, or zero when absent.
    """

    value = record.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValidationError(f"{key} must be an integer")
    try:
        result = int(value)
    except ValueError as error:
        raise ValidationError(f"{key} must be an integer") from error
    if result < minimum:
        raise ValidationError(f"{key} must be at least {minimum}")
    return result


def _launcher_string(record: Mapping[str, Any], key: str) -> str:
    """Read an optional string from a launcher record."""

    value = record.get(key, "")
    if not isinstance(value, str):
        raise ValidationError(f"{key} must be a string")
    return value


def _iteration_from_launcher(
    record: Mapping[str, Any], retired_instruction_event: str
) -> IterationMetrics:
    """Convert one launcher iteration record.

    :param record: Launcher iteration record.
    :param retired_instruction_event: Event containing retired instructions.
    :return: Normalized iteration metrics.
    """

    return IterationMetrics(
        iteration=_launcher_int(record, "iteration"),
        wall_time_ns=_launcher_int(record, "wall_time_ns"),
        thread_time_ns=_launcher_int(record, "thread_time_ns"),
        allocated_memory=_launcher_int(record, "allocated_memory"),
        compiled_bytecodes=_launcher_int(record, "compiled_bytecodes"),
        target_code_size=_launcher_int(record, "target_code_size"),
        target_code_hash=_launcher_string(record, "target_code_hash"),
        retired_instructions=_retired_instructions(record, retired_instruction_event),
    )


def _compilation_from_launcher(
    record: Mapping[str, Any], retired_instruction_event: str
) -> CompilationMetrics:
    """Convert one launcher compilation record.

    :param record: Launcher compilation record.
    :param retired_instruction_event: Event containing retired instructions.
    :return: Normalized compilation metrics.
    """

    method_name = record.get("method_name", "")
    if not isinstance(method_name, str) or not method_name:
        raise ValidationError("method_name must be a non-empty string")
    return CompilationMetrics(
        iteration=_launcher_int(record, "iteration"),
        compile_id=_launcher_int(record, "compile_id"),
        method_name=method_name,
        entry_bci=_launcher_int(record, "entry_bci", minimum=-1),
        wall_time_ns=_launcher_int(record, "wall_time_ns"),
        thread_time_ns=_launcher_int(record, "thread_time_ns"),
        allocated_memory=_launcher_int(record, "allocated_memory"),
        compiled_bytecodes=_launcher_int(record, "compiled_bytecodes"),
        target_code_size=_launcher_int(record, "target_code_size"),
        target_code_hash=_launcher_string(record, "target_code_hash"),
        retired_instructions=_retired_instructions(record, retired_instruction_event),
    )


@dataclass(frozen=True)
class ReplayParameters:
    """Parameters that affect replay measurement."""

    #: Number of replay iterations, including warmup.
    iterations: int
    #: Timeout for one replay process.
    timeout_seconds: int
    #: Maximum Java heap size.
    heap_size: str
    #: PAPI counter interpreted as the retired-instruction count.
    retired_instruction_event: str
    #: Additional JVM arguments passed to ``mx replaycomp`` in command-line order.
    replay_args: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate replay parameters."""

        if self.iterations < 1 or self.timeout_seconds < 1:
            raise ValidationError("replay integer parameters must be positive")
        if not self.heap_size or not self.retired_instruction_event:
            raise ValidationError(
                "replay heap size and retired-instruction event must not be empty"
            )
        for argument in self.replay_args:
            validate_replay_argument(argument)

    def as_json(self) -> JsonObject:
        """Serialize replay parameters.

        :return: JSON-compatible replay parameters.
        """

        return {
            "iterations": self.iterations,
            "timeout_seconds": self.timeout_seconds,
            "heap_size": self.heap_size,
            "retired_instruction_event": self.retired_instruction_event,
            "replay_args": list(self.replay_args),
        }

    @classmethod
    def from_json(cls, value: object) -> "ReplayParameters":
        """Parse replay parameters.

        :param value: Raw replay parameters object.
        :return: Validated replay parameters.
        """

        obj = require_object(value, "replay_parameters")
        reject_unknown_keys(
            obj,
            {
                "iterations",
                "timeout_seconds",
                "heap_size",
                "retired_instruction_event",
                "replay_args",
            },
            "replay_parameters",
        )
        return cls(
            iterations=require_int(obj, "iterations", minimum=1),
            timeout_seconds=require_int(obj, "timeout_seconds", minimum=1),
            heap_size=require_string(obj, "heap_size"),
            retired_instruction_event=require_string(obj, "retired_instruction_event"),
            replay_args=require_string_tuple(obj, "replay_args"),
        )


@dataclass(frozen=True)
class ReplayRun:
    """One corpus-run replay outcome."""

    #: Stable key of the source corpus run.
    key: RunKey
    #: Relative path to normalized metrics, if replay succeeded.
    metrics: str | None
    #: Relative path to the replay log.
    log: str
    #: Number of parsed aggregate iterations.
    parsed_iterations: int
    #: Number of parsed compilation records.
    parsed_compilations: int
    #: Replay process outcome.
    outcome: ProcessOutcome

    def __post_init__(self) -> None:
        """Validate paths and parsed record counts."""

        if self.metrics is not None:
            validate_relative_path(self.metrics, "metrics")
        validate_relative_path(self.log, "log")
        if self.parsed_iterations < 0 or self.parsed_compilations < 0:
            raise ValidationError("parsed result counts must not be negative")

    def as_json(self) -> JsonObject:
        """Serialize a replay run.

        :return: JSON-compatible replay run.
        """

        return {
            "key": self.key.as_json(),
            "metrics": self.metrics,
            "log": self.log,
            "parsed_iterations": self.parsed_iterations,
            "parsed_compilations": self.parsed_compilations,
            "outcome": self.outcome.as_json(),
        }

    @classmethod
    def from_json(cls, value: object) -> "ReplayRun":
        """Parse a replay run.

        :param value: Raw replay run object.
        :return: Validated replay run.
        """

        obj = require_object(value, "replay run")
        reject_unknown_keys(
            obj,
            {
                "key",
                "metrics",
                "log",
                "parsed_iterations",
                "parsed_compilations",
                "outcome",
            },
            "replay run",
        )
        metrics = obj.get("metrics")
        if metrics is not None and not isinstance(metrics, str):
            raise DataFormatError("metrics must be a string or null")
        return cls(
            key=RunKey.from_json(obj.get("key")),
            metrics=metrics,
            log=require_string(obj, "log"),
            parsed_iterations=require_int(obj, "parsed_iterations"),
            parsed_compilations=require_int(obj, "parsed_compilations"),
            outcome=ProcessOutcome.from_json(obj.get("outcome")),
        )


@dataclass(frozen=True)
class ReplayManifest:
    """Complete replay manifest."""

    #: Immutable replay identity.
    identity: Identity
    #: ID of the replayed corpus.
    corpus_id: str
    #: Resolved compiler commit metadata.
    commit: CommitMetadata
    #: GraalVM home used for replay.
    graalvm_home: str
    #: mx version used for replay.
    mx_version: str
    #: Parameters used for replay.
    replay_parameters: ReplayParameters
    #: Replay outcomes in corpus order.
    runs: tuple[ReplayRun, ...]

    def __post_init__(self) -> None:
        """Validate references, compiler metadata, and run keys."""

        validate_id(self.corpus_id)
        if not self.graalvm_home or not self.mx_version:
            raise ValidationError("replay compiler metadata must not be empty")
        if len({run.key for run in self.runs}) != len(self.runs):
            raise ValidationError("replay contains duplicate run keys")

    def as_json(self) -> JsonObject:
        """Serialize the replay manifest.

        :return: JSON-compatible replay manifest.
        """

        return {
            **self.identity.as_json(),
            "corpus_id": self.corpus_id,
            "commit": self.commit.as_json(),
            "graalvm_home": self.graalvm_home,
            "mx_version": self.mx_version,
            "replay_parameters": self.replay_parameters.as_json(),
            "runs": [run.as_json() for run in self.runs],
        }

    @classmethod
    def from_json(cls, value: object) -> "ReplayManifest":
        """Parse a replay manifest.

        :param value: Raw replay manifest object.
        :return: Validated replay manifest.
        """

        obj = require_object(value, "replay manifest")
        reject_unknown_keys(
            obj,
            {
                "id",
                "label",
                "created_at",
                "corpus_id",
                "commit",
                "graalvm_home",
                "mx_version",
                "replay_parameters",
                "runs",
            },
            "replay manifest",
        )
        return cls(
            identity=Identity.from_json(obj),
            corpus_id=require_string(obj, "corpus_id"),
            commit=CommitMetadata.from_json(obj.get("commit")),
            graalvm_home=require_string(obj, "graalvm_home"),
            mx_version=require_string(obj, "mx_version"),
            replay_parameters=ReplayParameters.from_json(obj.get("replay_parameters")),
            runs=tuple(
                ReplayRun.from_json(run)
                for run in require_list(obj.get("runs"), "runs")
            ),
        )
