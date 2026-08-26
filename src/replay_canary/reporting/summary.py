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

"""Render a local Markdown comparison summary."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from replay_canary.model.common import Identity
from replay_canary.model.comparison import ComparisonResult
from replay_canary.model.replay import ReplayManifest

#: Directory containing the Markdown template.
_TEMPLATE_DIRECTORY = Path(__file__).with_name("templates")
#: Human-readable labels for metrics included in the summary.
_METRIC_LABELS = {
    "retired_instructions": "Retired instructions",
    "allocated_memory": "Allocated memory",
    "target_code_size": "Target code size",
}


@dataclass(frozen=True)
class WorkloadFinding:
    """One workload whose metric change crossed its configured threshold."""

    workload_name: str
    delta: str


@dataclass(frozen=True)
class SuiteMetricFindings:
    """Metric findings grouped under one benchmark suite."""

    suite_name: str
    workloads: tuple[WorkloadFinding, ...]


@dataclass(frozen=True)
class MetricSummary:
    """Threshold-aware findings for one tracked metric."""

    label: str
    threshold_percent: str
    suites: tuple[SuiteMetricFindings, ...]
    available: bool


@dataclass(frozen=True)
class SuiteCodeChanges:
    """Workloads with target-code changes in one benchmark suite."""

    suite_name: str
    workloads: tuple[str, ...]


@dataclass(frozen=True)
class SummaryData:
    """Complete template input for one Markdown summary."""

    comparison_name: str
    baseline_name: str
    baseline_revision: str
    candidate_name: str
    candidate_revision: str
    failed_runs: int
    skipped_runs: int
    changed_runs: int
    unchanged_runs: int
    code_changes: tuple[SuiteCodeChanges, ...]
    metrics: tuple[MetricSummary, ...]

    @property
    def has_comparable_runs(self) -> bool:
        """Return whether at least one run produced comparable code hashes."""

        return self.changed_runs + self.unchanged_runs > 0

    @property
    def has_changes(self) -> bool:
        """Return whether code changed or a tracked metric crossed its threshold."""

        return bool(self.code_changes) or any(metric.suites for metric in self.metrics)

    @property
    def outcome(self) -> str:
        """Return the comparison outcome used in the summary heading."""

        if self.has_changes:
            return "changes found"
        if self.has_comparable_runs:
            return "no changes found"
        return "no comparable runs"


def render_summary(
    *,
    comparison_identity: Identity,
    baseline: ReplayManifest,
    candidate: ReplayManifest,
    result: ComparisonResult,
) -> str:
    """Render a local Markdown summary from classified comparison results.

    :param comparison_identity: Identity of the comparison artifact.
    :param baseline: Replay selected as the baseline.
    :param candidate: Replay selected as the candidate.
    :param result: Threshold-classified comparison result.
    :return: Markdown summary ending in one newline.
    """

    data = SummaryData(
        comparison_name=_display_name(comparison_identity),
        baseline_name=_display_name(baseline.identity),
        baseline_revision=baseline.commit.hash,
        candidate_name=_display_name(candidate.identity),
        candidate_revision=candidate.commit.hash,
        failed_runs=result.counts.failed,
        skipped_runs=result.counts.skipped,
        changed_runs=sum(run.code_changed is True for run in result.runs),
        unchanged_runs=sum(run.code_changed is False for run in result.runs),
        code_changes=_code_changes(result),
        metrics=_metric_summaries(result),
    )
    environment = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIRECTORY),
        undefined=StrictUndefined,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    environment.filters["markdown_code"] = _markdown_code
    rendered = environment.get_template("compare_summary.md.j2").render(summary=data)
    return re.sub(r"\n{3,}", "\n\n", rendered).rstrip() + "\n"


def _code_changes(result: ComparisonResult) -> tuple[SuiteCodeChanges, ...]:
    """Group changed-code workloads by benchmark suite."""

    grouped: dict[str, set[str]] = {}
    for run in result.runs:
        if run.code_changed is True:
            grouped.setdefault(run.key.suite_name, set()).add(run.key.workload_name)
    return tuple(
        SuiteCodeChanges(suite_name, tuple(sorted(workloads)))
        for suite_name, workloads in sorted(grouped.items())
    )


def _metric_summaries(result: ComparisonResult) -> tuple[MetricSummary, ...]:
    """Build workload findings from existing threshold classifications."""

    summaries = []
    for name, aggregate in result.aggregate_metrics:
        if aggregate.threshold is None:
            continue
        grouped: dict[str, list[WorkloadFinding]] = {}
        available = False
        for workload in result.workloads:
            comparison = dict(workload.metrics)[name]
            ratio = comparison.ratio
            available = available or ratio is not None
            if ratio is None or comparison.classification not in {
                "increase",
                "decrease",
            }:
                continue
            grouped.setdefault(workload.suite_name, []).append(
                WorkloadFinding(
                    workload.workload_name,
                    _format_change(ratio),
                )
            )
        summaries.append(
            MetricSummary(
                label=_METRIC_LABELS.get(name, name.replace("_", " ").title()),
                threshold_percent=_format_threshold(aggregate.threshold),
                suites=tuple(
                    SuiteMetricFindings(suite_name, tuple(workloads))
                    for suite_name, workloads in sorted(grouped.items())
                ),
                available=available,
            )
        )
    return tuple(summaries)


def _display_name(identity: Identity) -> str:
    """Return the label when available, otherwise the ID."""

    return identity.label or identity.id


def _format_change(ratio: float) -> str:
    """Format a candidate-to-baseline ratio as a relative change."""

    return f"{(ratio - 1) * 100:+.2f}%"


def _format_threshold(threshold: Decimal) -> str:
    """Format a relative threshold as a compact percentage."""

    return f"{threshold * 100:f}".rstrip("0").rstrip(".") or "0"


def _markdown_code(value: str) -> str:
    """Format text as a single-line Markdown code span."""

    normalized = value.replace("\r", "").replace("\n", "").replace("`", "")
    return f"`{normalized}`"
