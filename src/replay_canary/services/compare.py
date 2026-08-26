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

"""Replay comparison and local comparison publication."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterable, Mapping

from replay_canary.adapters.local_store import (
    ComparisonRepository,
    CorpusRepository,
    ReplayRepository,
    read_json,
)
from replay_canary.errors import ComparisonError, ReplayCanaryError
from replay_canary.model.common import IdentityFactory
from replay_canary.model.comparison import (
    DIAGNOSTIC_METRICS,
    TRACKED_THRESHOLDS,
    ComparisonCounts,
    ComparisonManifest,
    ComparisonResult,
    CompilationComparison,
    CompilationPresence,
    MetricComparison,
    RunComparison,
    RunComparisonStatus,
    WorkloadComparison,
)
from replay_canary.model.corpus import CorpusManifest, RunKey
from replay_canary.model.replay import (
    CompilationMetrics,
    IterationMetrics,
    ReplayManifest,
    ReplayMetrics,
    ReplayRun,
)
from replay_canary.reporting.builder import build_report_data
from replay_canary.reporting.html import render_report
from replay_canary.reporting.summary import render_summary

#: Relative change thresholds by metric name.
Thresholds = Mapping[str, Decimal]
#: Callback that loads normalized metrics for a replay run.
MetricsLoader = Callable[[ReplayManifest, ReplayRun], ReplayMetrics | None]


@dataclass(frozen=True)
class CompareRequest:
    """Resolved selectors and label for one comparison."""

    #: Baseline replay ID or label.
    baseline_selector: str
    #: Candidate replay ID or label.
    candidate_selector: str
    #: Optional label for the new comparison.
    label: str | None
    #: Relative change thresholds keyed by metric name.
    thresholds: tuple[tuple[str, Decimal], ...] = TRACKED_THRESHOLDS


@dataclass(frozen=True)
class CompareResult:
    """Published comparison details."""

    #: Published comparison manifest.
    manifest: ComparisonManifest
    #: In-memory comparison result.
    result: ComparisonResult
    #: Directory containing the published comparison.
    path: Path


def compare_replays(
    corpus: CorpusManifest,
    baseline: ReplayManifest,
    candidate: ReplayManifest,
    load_metrics: MetricsLoader,
    *,
    thresholds: Thresholds | None = None,
) -> ComparisonResult:
    """Compare two replays of the exact same corpus.

    :param corpus: Shared source corpus.
    :param baseline: Replay used as the baseline.
    :param candidate: Replay used as the candidate.
    :param load_metrics: Callback that loads metrics for one replay run.
    :param thresholds: Relative thresholds, or ``None`` for defaults.
    :return: Complete comparison result.
    """

    if baseline.identity.id == candidate.identity.id:
        raise ComparisonError("baseline and candidate replay must be different")
    if baseline.corpus_id != candidate.corpus_id:
        raise ComparisonError(
            "baseline and candidate replays reference different corpora"
        )
    if baseline.corpus_id != corpus.identity.id:
        raise ComparisonError("replay corpus ID does not match the loaded corpus")

    threshold_values = dict(TRACKED_THRESHOLDS if thresholds is None else thresholds)
    corpus_keys = tuple(run.key for run in corpus.runs)
    corpus_key_set = set(corpus_keys)
    baseline_runs = {run.key: run for run in baseline.runs}
    candidate_runs = {run.key: run for run in candidate.runs}
    extra_baseline = set(baseline_runs) - corpus_key_set
    extra_candidate = set(candidate_runs) - corpus_key_set
    if extra_baseline or extra_candidate:
        raise ComparisonError("replay contains run keys not present in its corpus")

    run_results: list[RunComparison] = []
    comparable_values: list[tuple[RunKey, dict[str, float], dict[str, float]]] = []
    compared = skipped = failed = 0
    for key in corpus_keys:
        baseline_run = baseline_runs.get(key)
        candidate_run = candidate_runs.get(key)
        baseline_metrics, baseline_hash, baseline_problem = _load_comparable_metrics(
            baseline,
            baseline_run,
            load_metrics,
        )
        candidate_metrics, candidate_hash, candidate_problem = _load_comparable_metrics(
            candidate,
            candidate_run,
            load_metrics,
        )
        if baseline_problem is not None:
            skipped += 1
            run_results.append(
                _uncompared_run(
                    key,
                    "skipped",
                    f"baseline {baseline_problem}",
                    threshold_values,
                    baseline_hash=baseline_hash,
                    candidate_hash=candidate_hash,
                )
            )
            continue
        if candidate_problem is not None:
            failed += 1
            run_results.append(
                _uncompared_run(
                    key,
                    "failed",
                    f"candidate {candidate_problem}",
                    threshold_values,
                    baseline_hash=baseline_hash,
                    candidate_hash=candidate_hash,
                )
            )
            continue
        assert baseline_metrics is not None
        assert candidate_metrics is not None
        baseline_values = _run_metric_averages(baseline_metrics)
        candidate_values = _run_metric_averages(candidate_metrics)
        metric_pairs = _metric_pairs(
            baseline_values, candidate_values, threshold_values
        )
        assert baseline_hash is not None
        assert candidate_hash is not None
        code_changed = baseline_hash != candidate_hash
        run_results.append(
            RunComparison(
                key=key,
                status="compared",
                message=None,
                baseline_hash=baseline_hash,
                candidate_hash=candidate_hash,
                code_changed=code_changed,
                metrics=metric_pairs,
                compilations=_compare_compilations(
                    baseline_metrics.compilations,
                    candidate_metrics.compilations,
                    threshold_values,
                ),
            )
        )
        comparable_values.append((key, baseline_values, candidate_values))
        compared += 1

    workloads = _workload_comparisons(comparable_values, threshold_values)
    aggregate = _aggregate_comparison(comparable_values, threshold_values)
    counts = ComparisonCounts(compared, skipped, failed)
    return ComparisonResult(
        counts=counts,
        code_changed=any(run.code_changed is True for run in run_results),
        aggregate_metrics=aggregate,
        workloads=workloads,
        runs=tuple(run_results),
    )


class CompareService:
    """Resolve local objects, compare them, and publish local output."""

    def __init__(
        self,
        corpora: CorpusRepository,
        replays: ReplayRepository,
        comparisons: ComparisonRepository,
        identities: IdentityFactory | None = None,
    ) -> None:
        """Store comparison repositories and identity creation.

        :param corpora: Corpus repository.
        :param replays: Replay repository.
        :param comparisons: Comparison repository.
        :param identities: Identity factory, or ``None`` to use the default.
        """

        #: Corpus repository.
        self._corpora = corpora
        #: Replay repository.
        self._replays = replays
        #: Comparison repository.
        self._comparisons = comparisons
        #: Identity factory for new comparisons.
        self._identities = identities or IdentityFactory()

    def compare(self, request: CompareRequest) -> CompareResult:
        """Calculate and atomically publish comparison artifacts.

        :param request: Resolved comparison inputs.
        :return: Published manifest, result, and directory.
        """

        baseline = self._replays.resolve(request.baseline_selector)
        candidate = self._replays.resolve(request.candidate_selector)
        if baseline.corpus_id != candidate.corpus_id:
            raise ComparisonError(
                "baseline and candidate replays reference different corpora"
            )
        corpus = self._corpora.get(baseline.corpus_id)
        identity = self._identities.create(request.label)
        replay_paths = {
            baseline.identity.id: self._replays.path_for(baseline.identity.id),
            candidate.identity.id: self._replays.path_for(candidate.identity.id),
        }

        def load_metrics(
            replay: ReplayManifest, run: ReplayRun
        ) -> ReplayMetrics | None:
            """Load metrics referenced by one replay run."""

            if run.metrics is None:
                return None
            replay_path = replay_paths[replay.identity.id]
            return ReplayMetrics.from_json(read_json(replay_path / run.metrics))

        result = compare_replays(
            corpus,
            baseline,
            candidate,
            load_metrics,
            thresholds=dict(request.thresholds),
        )
        staging = self._comparisons.create_staging(identity)
        try:
            summary_name = "summary.md"
            report_name = "report.html"
            report_data = build_report_data(
                comparison_identity=identity,
                corpus=corpus,
                corpus_path=self._corpora.path_for(corpus.identity.id),
                baseline=baseline,
                candidate=candidate,
                result=result,
            )
            (staging.object_directory / report_name).write_text(
                render_report(report_data),
                encoding="utf-8",
            )
            (staging.object_directory / summary_name).write_text(
                render_summary(
                    comparison_identity=identity,
                    baseline=baseline,
                    candidate=candidate,
                    result=result,
                ),
                encoding="utf-8",
            )
            manifest = ComparisonManifest(
                identity=identity,
                corpus_id=corpus.identity.id,
                baseline_replay_id=baseline.identity.id,
                candidate_replay_id=candidate.identity.id,
                thresholds=request.thresholds,
                counts=result.counts,
                summary=summary_name,
                report=report_name,
            )
            path = self._comparisons.publish(staging, manifest)
        except ReplayCanaryError as error:
            raise ComparisonError(
                f"{error}; comparison workspace retained at "
                f"{staging.work_directory}"
            ) from error
        except Exception as error:
            raise ComparisonError(
                f"comparison failed: {error}; workspace retained at "
                f"{staging.work_directory}"
            ) from error
        return CompareResult(manifest, result, path)


def _load_comparable_metrics(
    replay: ReplayManifest,
    run: ReplayRun | None,
    load_metrics: MetricsLoader,
) -> tuple[ReplayMetrics | None, str | None, str | None]:
    """Load metrics when a run's recorded outcome is successful.

    :param replay: Replay containing the run.
    :param run: Replay run, if present.
    :param load_metrics: Callback that loads normalized metrics for the run.
    :return: Loaded metrics, their consistent hash, and no problem; otherwise
        no metrics, no hash, and a problem description.
    """

    if run is None:
        return None, None, "run is missing"
    if run.outcome.status != "succeeded":
        return None, None, f"run status is {run.outcome.status}"
    metrics = load_metrics(replay, run)
    if metrics is None:
        return None, None, "metrics are missing"
    if not any(iteration.iteration != 0 for iteration in metrics.iterations):
        return None, None, "run has no measured iterations after warmup"
    target_code_hash = _consistent_hash(metrics.iterations)
    if target_code_hash is None:
        return None, None, "target-code hash is inconsistent across iterations"
    return metrics, target_code_hash, None


def _consistent_hash(
    iterations: Iterable[IterationMetrics],
) -> str | None:
    """Return the common target-code hash across iterations.

    :param iterations: Iteration metrics to inspect.
    :return: Common hash, or ``None`` if absent or inconsistent.
    """

    values = tuple(iteration.target_code_hash for iteration in iterations)
    if not values:
        return None
    return values[0] if all(value == values[0] for value in values[1:]) else None


def _run_metric_averages(metrics: ReplayMetrics) -> dict[str, float]:
    """Average measured iterations for one run.

    :param metrics: Normalized replay metrics.
    :return: Arithmetic mean by metric name, excluding warmup.
    """

    measured = tuple(
        iteration for iteration in metrics.iterations if iteration.iteration != 0
    )
    return {
        name: _mean(float(getattr(iteration, name)) for iteration in measured)
        for name in DIAGNOSTIC_METRICS
    }


def _metric_pairs(
    baseline: Mapping[str, float],
    candidate: Mapping[str, float],
    thresholds: Thresholds,
) -> tuple[tuple[str, MetricComparison], ...]:
    """Pair baseline and candidate values for every reported metric.

    :param baseline: Baseline values by metric name.
    :param candidate: Candidate values by metric name.
    :param thresholds: Relative thresholds by metric name.
    :return: Metric comparisons in report order.
    """

    return tuple(
        (
            name,
            _metric_pair(baseline.get(name), candidate.get(name), thresholds.get(name)),
        )
        for name in DIAGNOSTIC_METRICS
    )


def _metric_pair(
    baseline: float | int | None,
    candidate: float | int | None,
    threshold: Decimal | None,
) -> MetricComparison:
    """Compare one baseline and candidate metric value.

    :param baseline: Baseline value, if available.
    :param candidate: Candidate value, if available.
    :param threshold: Relative change threshold, if tracked.
    :return: Values, ratio, and change classification.
    """

    baseline_value = float(baseline) if baseline is not None else None
    candidate_value = float(candidate) if candidate is not None else None
    if baseline_value is None or candidate_value is None or baseline_value == 0:
        return MetricComparison(
            baseline_value,
            candidate_value,
            None,
            threshold,
            "unavailable",
        )
    ratio = candidate_value / baseline_value
    classification = "unchanged"
    if threshold is not None and abs(Decimal(str(ratio)) - Decimal(1)) > threshold:
        classification = "increase" if ratio > 1 else "decrease"
    return MetricComparison(
        baseline_value, candidate_value, ratio, threshold, classification
    )


def _workload_comparisons(
    values: Iterable[tuple[RunKey, dict[str, float], dict[str, float]]],
    thresholds: Thresholds,
) -> tuple[WorkloadComparison, ...]:
    """Average paired run ratios into equal-work workload comparisons.

    :param values: Run keys with baseline and candidate metric values.
    :param thresholds: Relative thresholds by metric name.
    :return: Per-workload comparisons in deterministic order.
    """

    groups: dict[
        tuple[str, str],
        list[tuple[dict[str, float], dict[str, float]]],
    ] = defaultdict(list)
    for key, baseline, candidate in values:
        groups[(key.suite_name, key.workload_name)].append((baseline, candidate))
    return tuple(
        WorkloadComparison(
            suite,
            workload,
            _workload_metric_pairs(runs, thresholds),
        )
        for (suite, workload), runs in sorted(groups.items())
    )


def _workload_metric_pairs(
    runs: list[tuple[dict[str, float], dict[str, float]]],
    thresholds: Thresholds,
) -> tuple[tuple[str, MetricComparison], ...]:
    """Build normalized metrics from arithmetic means of paired run ratios.

    :param runs: Paired baseline and candidate run averages for one workload.
    :param thresholds: Relative thresholds by metric name.
    :return: Equal-work metric comparisons in report order.
    """

    pairs = []
    for metric in DIAGNOSTIC_METRICS:
        ratios = []
        for baseline, candidate in runs:
            baseline_value = baseline[metric]
            if baseline_value == 0:
                pairs.append((metric, _metric_pair(None, None, thresholds.get(metric))))
                break
            ratios.append(candidate[metric] / baseline_value)
        else:
            pairs.append(
                (metric, _metric_pair(1.0, _mean(ratios), thresholds.get(metric)))
            )
    return tuple(pairs)


def _aggregate_comparison(
    values: Iterable[tuple[RunKey, dict[str, float], dict[str, float]]],
    thresholds: Thresholds,
) -> tuple[tuple[str, MetricComparison], ...]:
    """Sum metric values across all comparable runs.

    :param values: Run keys with baseline and candidate metric values.
    :param thresholds: Relative thresholds by metric name.
    :return: Aggregate metric comparisons in report order.
    """

    run_values = tuple(values)
    if not run_values:
        return tuple(
            (name, _metric_pair(None, None, thresholds.get(name)))
            for name in DIAGNOSTIC_METRICS
        )
    baseline_totals = {
        metric: sum(value[1][metric] for value in run_values)
        for metric in DIAGNOSTIC_METRICS
    }
    candidate_totals = {
        metric: sum(value[2][metric] for value in run_values)
        for metric in DIAGNOSTIC_METRICS
    }
    return _metric_pairs(baseline_totals, candidate_totals, thresholds)


def _compare_compilations(
    baseline: tuple[CompilationMetrics, ...],
    candidate: tuple[CompilationMetrics, ...],
    thresholds: Thresholds,
) -> tuple[CompilationComparison, ...]:
    """Compare compilation records by iteration and compile ID.

    :param baseline: Baseline compilation metrics.
    :param candidate: Candidate compilation metrics.
    :param thresholds: Relative thresholds by metric name.
    :return: Per-compilation comparisons in key order.
    """

    baseline_by_key = {
        (compilation.iteration, compilation.compile_id): compilation
        for compilation in baseline
    }
    candidate_by_key = {
        (compilation.iteration, compilation.compile_id): compilation
        for compilation in candidate
    }
    rows: list[CompilationComparison] = []
    for key in sorted(set(baseline_by_key) | set(candidate_by_key)):
        baseline_compilation = baseline_by_key.get(key)
        candidate_compilation = candidate_by_key.get(key)
        if baseline_compilation is None:
            presence: CompilationPresence = "candidate_only"
            representative = candidate_by_key[key]
        elif candidate_compilation is None:
            presence = "baseline_only"
            representative = baseline_compilation
        else:
            presence = "matched"
            representative = baseline_compilation
        baseline_hash = (
            baseline_compilation.target_code_hash
            if baseline_compilation is not None
            else None
        )
        candidate_hash = (
            candidate_compilation.target_code_hash
            if candidate_compilation is not None
            else None
        )
        rows.append(
            CompilationComparison(
                iteration=key[0],
                compile_id=key[1],
                method_name=representative.method_name,
                entry_bci=representative.entry_bci,
                presence=presence,
                baseline_hash=baseline_hash,
                candidate_hash=candidate_hash,
                code_changed=(
                    baseline_hash != candidate_hash
                    if baseline_hash is not None and candidate_hash is not None
                    else None
                ),
                metrics=tuple(
                    (
                        metric,
                        _metric_pair(
                            (
                                getattr(baseline_compilation, metric)
                                if baseline_compilation is not None
                                else None
                            ),
                            (
                                getattr(candidate_compilation, metric)
                                if candidate_compilation is not None
                                else None
                            ),
                            thresholds.get(metric),
                        ),
                    )
                    for metric in DIAGNOSTIC_METRICS
                ),
            )
        )
    return tuple(rows)


def _uncompared_run(
    key: RunKey,
    status: RunComparisonStatus,
    message: str,
    thresholds: Thresholds,
    *,
    baseline_hash: str | None,
    candidate_hash: str | None,
) -> RunComparison:
    """Build a run result with unavailable metrics.

    :param key: Stable corpus run key.
    :param status: Comparison outcome category.
    :param message: Reason the run was not compared.
    :param thresholds: Relative thresholds by metric name.
    :param baseline_hash: Consistent baseline target-code hash, if available.
    :param candidate_hash: Consistent candidate target-code hash, if available.
    :return: Run comparison with unavailable metric values.
    """

    return RunComparison(
        key=key,
        status=status,
        message=message,
        baseline_hash=baseline_hash,
        candidate_hash=candidate_hash,
        code_changed=None,
        metrics=tuple(
            (name, _metric_pair(None, None, thresholds.get(name)))
            for name in DIAGNOSTIC_METRICS
        ),
        compilations=(),
    )


def _mean(values: Iterable[float]) -> float:
    """Return the arithmetic mean of a non-empty sequence.

    :param values: Values to average.
    :return: Arithmetic mean.
    """

    collected = tuple(values)
    if not collected:
        raise ComparisonError("cannot average an empty metric sequence")
    return sum(collected) / len(collected)
