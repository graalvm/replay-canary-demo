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

"""Comparison manifest model."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Literal, TypeAlias

from replay_canary.errors import DataFormatError, ValidationError
from replay_canary.model.common import (
    Identity,
    JsonObject,
    reject_unknown_keys,
    require_int,
    require_object,
    require_string,
    validate_id,
    validate_relative_path,
)
from replay_canary.model.corpus import RunKey

#: Metrics with default relative-change thresholds.
TRACKED_THRESHOLDS: tuple[tuple[str, Decimal], ...] = (
    ("retired_instructions", Decimal("0.03")),
    ("allocated_memory", Decimal("0.02")),
    ("target_code_size", Decimal("0.02")),
)
#: Metrics included in comparisons and reports, in display order.
DIAGNOSTIC_METRICS: tuple[str, ...] = (
    "retired_instructions",
    "allocated_memory",
    "target_code_size",
    "compiled_bytecodes",
    "wall_time_ns",
    "thread_time_ns",
)
#: Relationship between baseline and candidate compilation presence.
CompilationPresence: TypeAlias = Literal["matched", "baseline_only", "candidate_only"]
#: Outcome category for a corpus-run comparison.
RunComparisonStatus: TypeAlias = Literal["compared", "skipped", "failed"]


@dataclass(frozen=True)
class MetricComparison:
    """Baseline and candidate metric values and their relative ratio."""

    #: Baseline value, if available.
    baseline: float | None
    #: Candidate value, if available.
    candidate: float | None
    #: Candidate value divided by the baseline value, if defined.
    ratio: float | None
    #: Relative change threshold, if the metric is tracked.
    threshold: Decimal | None
    #: Change category for reporting.
    classification: str

    def __post_init__(self) -> None:
        """Validate numeric values and the classification."""

        for value in (self.baseline, self.candidate, self.ratio):
            if value is not None and not isfinite(value):
                raise ValidationError("comparison metric values must be finite")
        if self.classification not in {
            "increase",
            "decrease",
            "unchanged",
            "unavailable",
        }:
            raise ValidationError(
                f"invalid metric classification: {self.classification}"
            )


@dataclass(frozen=True)
class CompilationComparison:
    """Presence, hash, and metric differences for one compilation key."""

    #: Zero-based replay iteration.
    iteration: int
    #: Compiler ID from the replay file.
    compile_id: int
    #: Compiled method name.
    method_name: str
    #: Bytecode index where compilation started.
    entry_bci: int
    #: Relationship between baseline and candidate compilation presence.
    presence: CompilationPresence
    #: Baseline target-code hash, if present.
    baseline_hash: str | None
    #: Candidate target-code hash, if present.
    candidate_hash: str | None
    #: Whether target code changed, if both sides are present.
    code_changed: bool | None
    #: Metric comparisons in display order.
    metrics: tuple[tuple[str, MetricComparison], ...]


@dataclass(frozen=True)
class RunComparison:
    """Comparison status and details for one corpus run."""

    #: Stable corpus run key.
    key: RunKey
    #: Comparison outcome category.
    status: RunComparisonStatus
    #: Optional reason the run was not compared.
    message: str | None
    #: Common baseline target-code hash, if available.
    baseline_hash: str | None
    #: Common candidate target-code hash, if available.
    candidate_hash: str | None
    #: Whether target code changed, if both hashes are available.
    code_changed: bool | None
    #: Run-level metric comparisons.
    metrics: tuple[tuple[str, MetricComparison], ...]
    #: Per-compilation comparisons.
    compilations: tuple[CompilationComparison, ...]


@dataclass(frozen=True)
class WorkloadComparison:
    """Arithmetic mean of paired run ratios for one workload."""

    #: Benchmark suite name.
    suite_name: str
    #: Workload name within the suite.
    workload_name: str
    #: Equal-work comparisons normalized to a baseline of one when available.
    metrics: tuple[tuple[str, MetricComparison], ...]


@dataclass(frozen=True)
class ComparisonResult:
    """In-memory replay comparison used to render reports."""

    #: Counts of compared, skipped, and failed runs.
    counts: "ComparisonCounts"
    #: Whether target code changed in any compared run.
    code_changed: bool
    #: Metrics aggregated across comparable runs.
    aggregate_metrics: tuple[tuple[str, MetricComparison], ...]
    #: Per-workload comparisons.
    workloads: tuple[WorkloadComparison, ...]
    #: Per-run comparisons.
    runs: tuple[RunComparison, ...]


@dataclass(frozen=True)
class ComparisonCounts:
    """Summary counts for a comparison."""

    #: Runs compared successfully.
    compared: int
    #: Runs skipped because source data was unavailable.
    skipped: int
    #: Runs not compared because replay failed.
    failed: int

    def __post_init__(self) -> None:
        """Validate all counts."""

        if min(self.compared, self.skipped, self.failed) < 0:
            raise ValidationError("comparison counts must not be negative")

    def as_json(self) -> JsonObject:
        """Serialize summary counts.

        :return: JSON-compatible summary counts.
        """

        return {
            "compared": self.compared,
            "skipped": self.skipped,
            "failed": self.failed,
        }

    @classmethod
    def from_json(cls, value: object) -> "ComparisonCounts":
        """Parse summary counts.

        :param value: Raw summary counts object.
        :return: Validated summary counts.
        """

        obj = require_object(value, "counts")
        reject_unknown_keys(obj, {"compared", "skipped", "failed"}, "counts")
        return cls(
            require_int(obj, "compared"),
            require_int(obj, "skipped"),
            require_int(obj, "failed"),
        )


@dataclass(frozen=True)
class ComparisonManifest:
    """Metadata and output locations for one comparison."""

    #: Immutable comparison identity.
    identity: Identity
    #: ID of the shared corpus.
    corpus_id: str
    #: ID of the replay used as baseline.
    baseline_replay_id: str
    #: ID of the replay used as candidate.
    candidate_replay_id: str
    #: Relative thresholds by metric name.
    thresholds: tuple[tuple[str, Decimal], ...]
    #: Counts of compared, skipped, and failed runs.
    counts: ComparisonCounts
    #: Relative path to the concise Markdown summary.
    summary: str
    #: Relative path to the HTML report.
    report: str

    def __post_init__(self) -> None:
        """Validate references, thresholds, and output paths."""

        validate_id(self.corpus_id)
        validate_id(self.baseline_replay_id)
        validate_id(self.candidate_replay_id)
        if self.baseline_replay_id == self.candidate_replay_id:
            raise ValidationError("baseline and candidate replay IDs must differ")
        if len({name for name, _ in self.thresholds}) != len(self.thresholds):
            raise ValidationError("comparison thresholds must have unique names")
        if any(
            not name or not value.is_finite() or value < 0 or value > 1
            for name, value in self.thresholds
        ):
            raise ValidationError(
                "comparison thresholds must have names and values between 0 and 1"
            )
        object.__setattr__(self, "thresholds", tuple(sorted(self.thresholds)))
        validate_relative_path(self.summary, "summary")
        validate_relative_path(self.report, "report")

    def as_json(self) -> JsonObject:
        """Serialize the comparison manifest.

        :return: JSON-compatible comparison manifest.
        """

        return {
            **self.identity.as_json(),
            "corpus_id": self.corpus_id,
            "baseline_replay_id": self.baseline_replay_id,
            "candidate_replay_id": self.candidate_replay_id,
            "thresholds": {name: str(value) for name, value in self.thresholds},
            "counts": self.counts.as_json(),
            "summary": self.summary,
            "report": self.report,
        }

    @classmethod
    def from_json(cls, value: object) -> "ComparisonManifest":
        """Parse a comparison manifest.

        :param value: Raw comparison manifest object.
        :return: Validated comparison manifest.
        """

        obj = require_object(value, "comparison manifest")
        reject_unknown_keys(
            obj,
            {
                "id",
                "label",
                "created_at",
                "corpus_id",
                "baseline_replay_id",
                "candidate_replay_id",
                "thresholds",
                "counts",
                "summary",
                "report",
            },
            "comparison manifest",
        )
        raw_thresholds = require_object(obj.get("thresholds"), "thresholds")
        thresholds: list[tuple[str, Decimal]] = []
        for name, raw_value in raw_thresholds.items():
            if not isinstance(raw_value, str):
                raise DataFormatError(f"threshold {name!r} must be a decimal string")
            try:
                thresholds.append((name, Decimal(raw_value)))
            except InvalidOperation as error:
                raise DataFormatError(
                    f"threshold {name!r} must be a decimal string"
                ) from error
        return cls(
            identity=Identity.from_json(obj),
            corpus_id=require_string(obj, "corpus_id"),
            baseline_replay_id=require_string(obj, "baseline_replay_id"),
            candidate_replay_id=require_string(obj, "candidate_replay_id"),
            thresholds=tuple(thresholds),
            counts=ComparisonCounts.from_json(obj.get("counts")),
            summary=require_string(obj, "summary"),
            report=require_string(obj, "report"),
        )
