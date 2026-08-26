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

"""Replay canary command-line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Sequence

from replay_canary.adapters.git import CURRENT_REVISION, GitRepository
from replay_canary.adapters.local_store import (
    ComparisonRepository,
    CorpusRepository,
    DataLayout,
    HotMethodWindowRepository,
    ReplayRepository,
)
from replay_canary.adapters.mx import Mx
from replay_canary.adapters.process import ProcessRunner
from replay_canary.config import load_config, require_graal_repository
from replay_canary.errors import ConfigurationError, ReplayCanaryError, ValidationError
from replay_canary.logging import configure_logging
from replay_canary.model.benchmark import BenchmarkName
from replay_canary.model.replay import validate_replay_argument
from replay_canary.services.build import BuildService
from replay_canary.services.compare import CompareRequest, CompareResult, CompareService
from replay_canary.services.compiler import CompilerEnvironmentLoader
from replay_canary.services.record import RecordRequest, RecordService
from replay_canary.services.replay import ReplayRequest, ReplayService


def create_parser() -> argparse.ArgumentParser:
    """Create the public command-line parser.

    :return: Parser for all replay canary commands.
    """

    parser = argparse.ArgumentParser(
        prog="replay-canary",
        description="Detect GraalVM compiler performance changes with replay compilation.",
    )
    parser.add_argument("--config", type=Path, help="configuration TOML path")
    parser.add_argument(
        "--graal-repository",
        type=Path,
        help="override the configured Graal repository",
    )
    parser.add_argument(
        "--data-dir", type=Path, help="override the configured local data directory"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="enable verbose diagnostics"
    )
    parser.add_argument("--debug-log", type=Path, help="write debug logging to PATH")

    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="build a GraalVM compiler revision")
    build.add_argument(
        "--revision",
        default=CURRENT_REVISION,
        help=f"Git revision to checkout (default: {CURRENT_REVISION})",
    )

    record = subparsers.add_parser("record", help="record a replay corpus")
    record.add_argument("--label", help="memorable label for the new corpus")
    record.add_argument(
        "--benchmark-suite",
        action="append",
        dest="benchmark_suites",
        metavar="SUITE",
        help="override a configured benchmark suite; may be repeated",
    )
    record.add_argument(
        "--workload",
        action="append",
        metavar="SUITE:WORKLOAD",
        help="record only this workload; may be repeated",
    )
    record.add_argument(
        "--runs",
        type=_positive_integer,
        help="override the number of recording runs per workload",
    )
    record.add_argument(
        "--hot-window-size",
        type=_positive_integer,
        help="override the number of recent profiles in the hot-method window",
    )
    record.add_argument(
        "--hot-method-threshold",
        type=_threshold,
        help="override the minimum sample share for selecting hot methods",
    )
    record.add_argument(
        "--jvm-arg",
        action="append",
        type=_recording_argument,
        metavar="ARG",
        help="additional benchmark JVM argument; may be repeated",
    )

    replay = subparsers.add_parser("replay", help="replay a saved corpus")
    replay.add_argument("--corpus", required=True, help="corpus ID or label")
    replay.add_argument("--label", help="memorable label for the new replay")
    replay.add_argument(
        "--iterations",
        type=_positive_integer,
        help="override the number of replay iterations per compilation",
    )
    replay.add_argument(
        "--replay-arg",
        action="append",
        type=_replay_argument,
        metavar="ARG",
        help="additional -D or -X JVM argument; may be repeated",
    )

    compare = subparsers.add_parser("compare", help="compare two saved replays")
    compare.add_argument(
        "--baseline", required=True, help="baseline replay ID or label"
    )
    compare.add_argument(
        "--candidate", required=True, help="candidate replay ID or label"
    )
    compare.add_argument("--label", help="memorable label for the new comparison")
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the command-line application and return its exit status.

    :param arguments: Arguments to parse, or ``None`` to use ``sys.argv``.
    :return: Command exit status.
    """

    parser = create_parser()
    namespace = parser.parse_args(arguments)
    logger = configure_logging(namespace.verbose, namespace.debug_log)
    try:
        config = load_config(
            namespace.config,
            namespace.data_dir,
            namespace.graal_repository,
        )
        logger.info("configuration: %s", config.source or "built-in defaults")
        logger.info("data directory: %s", config.data.directory)
        if namespace.command == "build":
            repository = require_graal_repository(config)
            logger.info("Graal repository: %s", repository)
            runner = ProcessRunner(logger)
            build_result = BuildService(
                GitRepository(repository, runner),
                Mx(config.tools.mx, repository, runner),
                DataLayout(config.data.directory),
                logger,
            ).build(namespace.revision)
            logger.info(
                "built GraalVM for revision %s at %s",
                build_result.revision,
                build_result.graalvm_home,
            )
            return 0
        if namespace.command == "record":
            repository = require_graal_repository(config)
            logger.info("Graal repository: %s", repository)
            runner = ProcessRunner(logger)
            git = GitRepository(repository, runner)
            mx = Mx(config.tools.mx, repository, runner)
            layout = DataLayout(config.data.directory)
            request = RecordRequest(
                label=namespace.label,
                benchmark_suites=tuple(
                    namespace.benchmark_suites
                    if namespace.benchmark_suites is not None
                    else config.record.benchmark_suites
                ),
                workloads=tuple(
                    BenchmarkName.parse(value) for value in (namespace.workload or ())
                ),
                runs=namespace.runs or config.record.runs,
                hot_window_size=(
                    namespace.hot_window_size or config.record.hot_window_size
                ),
                hot_method_threshold=(
                    namespace.hot_method_threshold
                    if namespace.hot_method_threshold is not None
                    else config.record.hot_method_threshold
                ),
                jvm_args=tuple(namespace.jvm_arg or ()),
            )
            record_result = RecordService(
                CompilerEnvironmentLoader(git, mx),
                mx,
                runner,
                CorpusRepository(layout),
                HotMethodWindowRepository(layout),
                logger,
            ).record(request)
            logger.info("corpus ID: %s", record_result.manifest.identity.id)
            logger.info("corpus directory: %s", record_result.path)
            logger.info(
                "recorded %d profiles, %d failures, %d replayable compilations",
                record_result.successful_profiles,
                record_result.failed_runs,
                record_result.replayable_compilations,
            )
            return 1 if record_result.partial else 0
        if namespace.command == "replay":
            repository = require_graal_repository(config)
            logger.info("Graal repository: %s", repository)
            runner = ProcessRunner(logger)
            git = GitRepository(repository, runner)
            mx = Mx(config.tools.mx, repository, runner)
            layout = DataLayout(config.data.directory)
            replay_result = ReplayService(
                CompilerEnvironmentLoader(git, mx),
                mx,
                runner,
                CorpusRepository(layout),
                ReplayRepository(layout),
                logger,
            ).replay(
                ReplayRequest(
                    corpus_selector=namespace.corpus,
                    label=namespace.label,
                    iterations=namespace.iterations or config.replay.iterations,
                    retired_instruction_event=(config.replay.retired_instruction_event),
                    replay_args=tuple(namespace.replay_arg or ()),
                )
            )
            logger.info("replay ID: %s", replay_result.manifest.identity.id)
            logger.info("replay directory: %s", replay_result.path)
            logger.info(
                "replayed %d runs, %d failures, %d skipped",
                replay_result.successful_runs,
                replay_result.failed_runs,
                replay_result.skipped_runs,
            )
            return 1 if replay_result.partial else 0
        if namespace.command == "compare":
            layout = DataLayout(config.data.directory)
            comparison_result = CompareService(
                CorpusRepository(layout),
                ReplayRepository(layout),
                ComparisonRepository(layout),
            ).compare(
                CompareRequest(
                    baseline_selector=namespace.baseline,
                    candidate_selector=namespace.candidate,
                    label=namespace.label,
                    thresholds=config.compare.thresholds,
                )
            )
            _log_comparison_result(comparison_result, logger)
            return 0
        raise ReplayCanaryError(f"unknown command: {namespace.command}")
    except ConfigurationError as error:
        parser.error(str(error))
    except ReplayCanaryError as error:
        logger.error("%s", error)
        if namespace.verbose:
            logging.getLogger("replay_canary").exception("command failed")
        return 1
    return 0


def _log_comparison_result(result: CompareResult, logger: logging.Logger) -> None:
    """Log comparison artifact locations and Markdown summary.

    :param result: Published comparison result.
    :param logger: Logger receiving the output.
    """

    logger.info("comparison ID: %s", result.manifest.identity.id)
    logger.info("comparison directory: %s", result.path)
    logger.info("HTML report: %s", result.path / result.manifest.report)
    summary = (result.path / result.manifest.summary).read_text(encoding="utf-8")
    logger.info("comparison summary:\n%s", summary.rstrip("\n"))


def _positive_integer(value: str) -> int:
    """Parse a positive command-line integer.

    :param value: Text supplied on the command line.
    :return: Parsed positive integer.
    """

    try:
        result = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if result < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def _threshold(value: str) -> Decimal:
    """Parse a command-line decimal threshold.

    :param value: Text supplied on the command line.
    :return: Decimal between zero and one, inclusive.
    """

    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError("must be a decimal threshold") from error
    if not result.is_finite() or not (Decimal(0) <= result <= Decimal(1)):
        raise argparse.ArgumentTypeError("must be at least 0 and at most 1")
    return result


def _replay_argument(value: str) -> str:
    """Parse an additional replay JVM argument.

    :param value: Argument supplied on the command line.
    :return: Validated argument.
    """

    try:
        validate_replay_argument(value)
    except ValidationError as error:
        raise argparse.ArgumentTypeError(str(error)) from error
    return value


def _recording_argument(value: str) -> str:
    """Parse an additional recording JVM argument.

    :param value: Argument supplied on the command line.
    :return: Non-empty JVM argument.
    """

    if not value:
        raise argparse.ArgumentTypeError("must not be empty")
    return value


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
