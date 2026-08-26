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

"""Comparison report models for an HTML template."""

from __future__ import annotations

from dataclasses import dataclass

from replay_canary.model.comparison import (
    CompilationPresence,
    RunComparisonStatus,
)


@dataclass(frozen=True)
class ArtifactView:
    """Identity shown for a corpus, replay, or comparison."""

    #: Immutable artifact ID.
    object_id: str
    #: Optional user-facing label.
    label: str | None

    @property
    def display_name(self) -> str:
        """Return the label when available, otherwise the ID.

        :return: Artifact display name.
        """

        return self.label or self.object_id


@dataclass(frozen=True)
class RevisionView:
    """Compiler revision metadata."""

    #: Compiler role in the comparison.
    role: str
    #: Resolved commit hash.
    revision: str
    #: Formatted commit time.
    committed_at: str
    #: Commit author name.
    author_name: str
    #: Commit subject.
    subject: str
    #: Additional JVM arguments used for replay.
    replay_args: tuple[str, ...]


@dataclass(frozen=True)
class MetricView:
    """Formatted metric pair for one report location."""

    #: Human-readable metric label.
    label: str
    #: Brief explanation of the metric, when needed at this report location.
    description: str | None
    #: Formatted baseline value.
    baseline: str
    #: Formatted candidate value.
    candidate: str
    #: Formatted relative change.
    delta: str
    #: Display category for the change.
    tone: str


@dataclass(frozen=True)
class CompilationView:
    """One per-compilation comparison row."""

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
    #: Baseline target-code hash for display.
    baseline_hash: str
    #: Candidate target-code hash for display.
    candidate_hash: str
    #: Whether target code changed, if comparable.
    code_changed: bool | None
    #: Absolute path to the source replay file, if available.
    replay_path: str | None
    #: Formatted compilation metrics.
    metrics: tuple[MetricView, ...]


@dataclass(frozen=True)
class RunView:
    """One benchmark run and its optional compilation details."""

    #: HTML anchor for the run section.
    anchor: str
    #: Benchmark suite name.
    suite_name: str
    #: Workload name within the suite.
    workload_name: str
    #: Zero-based run index.
    run_index: int
    #: Comparison outcome category.
    status: RunComparisonStatus
    #: Optional reason the run was not compared.
    message: str | None
    #: Baseline target-code hash for display.
    baseline_hash: str
    #: Candidate target-code hash for display.
    candidate_hash: str
    #: Whether target code changed, if comparable.
    code_changed: bool | None
    #: Formatted run metrics.
    metrics: tuple[MetricView, ...]
    #: Per-compilation rows.
    compilations: tuple[CompilationView, ...]

    @property
    def measured_compilations(self) -> tuple[CompilationView, ...]:
        """Return compilations from non-warmup iterations."""

        return tuple(item for item in self.compilations if item.iteration != 0)

    @property
    def compared_compilations(self) -> int:
        """Return the number of measured compilations present in both replays."""

        return sum(item.presence == "matched" for item in self.measured_compilations)

    @property
    def changed_compilations(self) -> int:
        """Return the number of measured compilations with changed code."""

        return sum(item.code_changed is True for item in self.measured_compilations)

    @property
    def unchanged_compilations(self) -> int:
        """Return the number of measured compilations with unchanged code."""

        return sum(item.code_changed is False for item in self.measured_compilations)

    @property
    def baseline_only_compilations(self) -> int:
        """Return the measured compilations found only in the baseline."""

        return sum(
            item.presence == "baseline_only" for item in self.measured_compilations
        )

    @property
    def candidate_only_compilations(self) -> int:
        """Return the measured compilations found only in the candidate."""

        return sum(
            item.presence == "candidate_only" for item in self.measured_compilations
        )


@dataclass(frozen=True)
class ReportData:
    """Complete input to the standalone HTML renderer."""

    #: Formatted report generation time.
    generated_at: str
    #: Comparison artifact details.
    comparison: ArtifactView
    #: Corpus artifact details.
    corpus: ArtifactView
    #: Absolute local corpus path.
    corpus_path: str
    #: Baseline replay details.
    baseline: ArtifactView
    #: Candidate replay details.
    candidate: ArtifactView
    #: Baseline compiler details.
    baseline_revision: RevisionView
    #: Candidate compiler details.
    candidate_revision: RevisionView
    #: Runs compared successfully.
    compared_runs: int
    #: Runs skipped because source data was unavailable.
    skipped_runs: int
    #: Runs not compared because replay failed.
    failed_runs: int
    #: Compared runs whose aggregate target-code hash changed.
    changed_runs: int
    #: Compared runs whose aggregate target-code hash stayed unchanged.
    unchanged_runs: int
    #: Metrics aggregated across comparable runs.
    aggregate_metrics: tuple[MetricView, ...]
    #: Per-run report sections.
    runs: tuple[RunView, ...]
