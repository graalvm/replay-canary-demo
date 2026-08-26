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

"""Build comparison report data."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from replay_canary.model.common import CommitMetadata, Identity
from replay_canary.model.comparison import ComparisonResult, MetricComparison
from replay_canary.model.corpus import (
    REPLAY_FILE_EXTENSIONS,
    CorpusManifest,
    CorpusRun,
)
from replay_canary.model.replay import ReplayManifest
from replay_canary.reporting.model import (
    ArtifactView,
    CompilationView,
    MetricView,
    ReportData,
    RevisionView,
    RunView,
)

#: Human-readable labels for known metrics.
_METRIC_LABELS = {
    "retired_instructions": "Retired instructions",
    "allocated_memory": "Allocated memory (B)",
    "target_code_size": "Target code size (B)",
    "compiled_bytecodes": "Compiled bytecodes",
    "wall_time_ns": "Wall time (ms)",
    "thread_time_ns": "Thread time (ms)",
}
#: Brief descriptions for aggregate metrics.
_AGGREGATE_METRIC_DESCRIPTIONS = {
    "retired_instructions": (
        "Sum of average retired compiler instructions across all compared runs, "
        "excluding warmup."
    ),
    "allocated_memory": (
        "Sum of average compiler-allocated bytes across all compared runs, excluding "
        "warmup."
    ),
    "target_code_size": (
        "Sum of average emitted machine-code bytes across all compared runs, excluding "
        "warmup."
    ),
}
#: Metrics converted from nanoseconds to milliseconds for display.
_TIME_METRICS = {"wall_time_ns", "thread_time_ns"}


def build_report_data(
    *,
    comparison_identity: Identity,
    corpus: CorpusManifest,
    corpus_path: Path,
    baseline: ReplayManifest,
    candidate: ReplayManifest,
    result: ComparisonResult,
) -> ReportData:
    """Build a local report from the comparison result.

    :param comparison_identity: Identity of the new comparison.
    :param corpus: Shared source corpus.
    :param corpus_path: Source corpus directory.
    :param baseline: Baseline replay manifest.
    :param candidate: Candidate replay manifest.
    :param result: In-memory comparison result.
    :return: Complete template input.
    """

    corpus_runs = {run.key: run for run in corpus.runs}
    return ReportData(
        generated_at=_timestamp(comparison_identity.created_at),
        comparison=_artifact(comparison_identity),
        corpus=_artifact(corpus.identity),
        corpus_path=str(corpus_path.resolve()),
        baseline=_artifact(baseline.identity),
        candidate=_artifact(candidate.identity),
        baseline_revision=_revision(
            "Baseline compiler",
            baseline.commit,
            baseline.replay_parameters.replay_args,
        ),
        candidate_revision=_revision(
            "Candidate compiler",
            candidate.commit,
            candidate.replay_parameters.replay_args,
        ),
        compared_runs=result.counts.compared,
        skipped_runs=result.counts.skipped,
        failed_runs=result.counts.failed,
        changed_runs=sum(run.code_changed is True for run in result.runs),
        unchanged_runs=sum(run.code_changed is False for run in result.runs),
        aggregate_metrics=_metrics(
            result.aggregate_metrics[:3],
            descriptions=_AGGREGATE_METRIC_DESCRIPTIONS,
        ),
        runs=tuple(
            RunView(
                anchor=f"run-{run.key.value}",
                suite_name=run.key.suite_name,
                workload_name=run.key.workload_name,
                run_index=run.key.run_index,
                status=run.status,
                message=run.message,
                baseline_hash=run.baseline_hash or "—",
                candidate_hash=run.candidate_hash or "—",
                code_changed=run.code_changed,
                metrics=_metrics(run.metrics),
                compilations=tuple(
                    CompilationView(
                        iteration=compilation.iteration,
                        compile_id=compilation.compile_id,
                        method_name=compilation.method_name,
                        entry_bci=compilation.entry_bci,
                        presence=compilation.presence,
                        baseline_hash=compilation.baseline_hash or "—",
                        candidate_hash=compilation.candidate_hash or "—",
                        code_changed=compilation.code_changed,
                        replay_path=_replay_path(
                            corpus_path,
                            corpus_runs.get(run.key),
                            compilation.compile_id,
                        ),
                        metrics=_metrics(compilation.metrics),
                    )
                    for compilation in run.compilations
                ),
            )
            for run in result.runs
        ),
    )


def _artifact(identity: Identity) -> ArtifactView:
    """Build report data for an artifact.

    :param identity: Artifact identity.
    :return: Template-facing artifact data.
    """

    return ArtifactView(identity.id, identity.label)


def _revision(
    role: str,
    commit: CommitMetadata,
    replay_args: tuple[str, ...],
) -> RevisionView:
    """Build report data for one compiler revision.

    :param role: Compiler role shown in the report.
    :param commit: Resolved commit metadata.
    :param replay_args: Additional JVM arguments used for replay.
    :return: Template-facing revision data.
    """

    return RevisionView(
        role,
        commit.hash,
        _timestamp(commit.committed_at),
        commit.author_name,
        commit.subject,
        replay_args,
    )


def _timestamp(value: datetime) -> str:
    """Format a timestamp for the HTML report.

    :param value: Timestamp to format.
    :return: UTC display text.
    """

    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _metrics(
    values: tuple[tuple[str, MetricComparison], ...],
    *,
    descriptions: dict[str, str] | None = None,
) -> tuple[MetricView, ...]:
    """Format a sequence of metric comparisons.

    :param values: Named metric comparisons.
    :param descriptions: Optional descriptions keyed by metric name.
    :return: Template-facing metrics in input order.
    """

    return tuple(
        _metric(name, value, (descriptions or {}).get(name)) for name, value in values
    )


def _metric(
    name: str,
    value: MetricComparison,
    description: str | None,
) -> MetricView:
    """Format one metric comparison.

    :param name: Metric name.
    :param value: Metric comparison to format.
    :param description: Optional description for this report location.
    :return: Template-facing metric data.
    """

    if value.ratio is None:
        delta = "n/a"
    else:
        delta = f"{(value.ratio - 1) * 100:+.2f}%"
    tone = value.classification
    if value.threshold is None and value.ratio is not None:
        tone = "diagnostic"
    return MetricView(
        label=_METRIC_LABELS.get(name, name.replace("_", " ").title()),
        description=description,
        baseline=_format_metric(name, value.baseline),
        candidate=_format_metric(name, value.candidate),
        delta=delta,
        tone=tone,
    )


def _format_metric(name: str, value: float | None) -> str:
    """Format one metric value for display.

    :param name: Metric name.
    :param value: Numeric value, if available.
    :return: Display text.
    """

    if value is None:
        return "—"
    if name in _TIME_METRICS:
        return f"{value / 1_000_000:,.3f}"
    if value.is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _replay_path(
    corpus_path: Path,
    run: CorpusRun | None,
    compile_id: int,
) -> str | None:
    """Return the local replay file path when it is available.

    :param corpus_path: Source corpus directory.
    :param run: Corpus run containing replay files, if found.
    :param compile_id: Compilation ID.
    :return: Absolute replay path, or ``None`` when unavailable.
    """

    if run is None or run.replay_files is None:
        return None
    replay_directory = corpus_path / run.replay_files
    for extension in REPLAY_FILE_EXTENSIONS:
        replay_file = replay_directory / f"{compile_id}{extension}"
        if replay_file.is_file():
            return str(replay_file.resolve())
    return None
