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

"""Loading and validation of replay canary configuration."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from replay_canary.errors import ConfigurationError
from replay_canary.model.comparison import TRACKED_THRESHOLDS

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib  # type: ignore[import-not-found]

#: File name used for local configuration discovery.
DEFAULT_CONFIG_NAME = "replay-canary.toml"
#: Command used to run mx when none is configured.
DEFAULT_MX = "mx"
#: Directory name used when no data directory is configured.
DEFAULT_DATA_DIRECTORY = "replay-canary-data"
#: Benchmark suites recorded when none are configured.
DEFAULT_BENCHMARK_SUITES = ("renaissance", "dacapo", "scala-dacapo")
#: Number of recording runs per workload by default.
DEFAULT_RECORD_RUNS = 1
#: Number of recent profiles kept for hot-method selection by default.
DEFAULT_HOT_WINDOW_SIZE = 30
#: Minimum share of samples needed for a method to be hot by default.
DEFAULT_HOT_METHOD_THRESHOLD = Decimal("0.001")
#: Number of replay iterations, including warmup, by default.
DEFAULT_REPLAY_ITERATIONS = 2
#: PAPI event interpreted as the retired-instruction count by default.
DEFAULT_RETIRED_INSTRUCTION_EVENT = "PAPI_TOT_INS"


@dataclass(frozen=True)
class ToolsConfig:
    """Paths or names of external tools."""

    #: Command used to run mx.
    mx: str


@dataclass(frozen=True)
class GraalConfig:
    """Location of the Graal source checkout."""

    #: Resolved path to the Graal checkout, if configured.
    repository: Path | None


@dataclass(frozen=True)
class DataConfig:
    """Location of local generated data."""

    #: Resolved directory for generated local data.
    directory: Path


@dataclass(frozen=True)
class RecordConfig:
    """Default settings for corpus recording."""

    #: Benchmark suites included in a recording.
    benchmark_suites: tuple[str, ...]
    #: Number of recording runs per workload.
    runs: int
    #: Number of recent profiles kept for hot-method selection.
    hot_window_size: int
    #: Minimum share of samples needed for a method to be hot.
    hot_method_threshold: Decimal


@dataclass(frozen=True)
class ReplayConfig:
    """Default settings for corpus replay."""

    #: Number of replay iterations, including warmup.
    iterations: int
    #: PAPI event interpreted as the retired-instruction count.
    retired_instruction_event: str


@dataclass(frozen=True)
class CompareConfig:
    """Default settings for replay comparison."""

    #: Relative change thresholds keyed by metric name.
    thresholds: tuple[tuple[str, Decimal], ...]


@dataclass(frozen=True)
class Config:
    """Fully resolved replay canary configuration."""

    #: Configuration file that was loaded, or ``None`` for built-in defaults.
    source: Path | None
    #: External tool settings.
    tools: ToolsConfig
    #: Graal checkout settings.
    graal: GraalConfig
    #: Local data settings.
    data: DataConfig
    #: Recording defaults.
    record: RecordConfig
    #: Replay defaults.
    replay: ReplayConfig
    #: Comparison defaults.
    compare: CompareConfig


#: Allowed keys in each configuration section.
_ALLOWED: dict[str, set[str]] = {
    "tools": {"mx"},
    "graal": {"repository"},
    "data": {"directory"},
    "record": {
        "benchmark_suites",
        "runs",
        "hot_window_size",
        "hot_method_threshold",
    },
    "replay": {"iterations", "retired_instruction_event"},
    "compare": {f"{name}_threshold" for name, _ in TRACKED_THRESHOLDS},
}


def load_config(
    explicit_path: Path | None = None,
    data_directory_override: Path | None = None,
    graal_repository_override: Path | None = None,
    cwd: Path | None = None,
) -> Config:
    """Load configuration with explicit, local, then built-in precedence.

    :param explicit_path: Explicit configuration path, if supplied.
    :param data_directory_override: One-off local data directory override.
    :param graal_repository_override: One-off Graal repository override, if
        supplied.
    :param cwd: Base directory, or ``None`` to use the current directory.
    :return: Fully resolved configuration.
    """

    working_directory = (cwd or Path.cwd()).resolve()
    config_path = _select_config(explicit_path, working_directory)
    raw: Mapping[str, Any] = {}
    base_directory = working_directory
    if config_path is not None:
        try:
            with config_path.open("rb") as config_file:
                loaded = tomllib.load(config_file)
        except OSError as error:
            raise ConfigurationError(
                f"cannot read configuration {config_path}: {error}"
            ) from error
        except tomllib.TOMLDecodeError as error:
            raise ConfigurationError(
                f"invalid TOML in configuration {config_path}: {error}"
            ) from error
        raw = loaded
        base_directory = config_path.parent

    sections = _validate_sections(raw, config_path)
    tools = sections["tools"]
    graal = sections["graal"]
    data = sections["data"]
    record = sections["record"]
    replay = sections["replay"]
    compare = sections["compare"]

    mx = _string(tools.get("mx", DEFAULT_MX), "tools.mx", config_path)
    repository_value = graal.get("repository")
    repository: Path | None
    if graal_repository_override is not None:
        repository = _resolve_path(graal_repository_override, working_directory)
    else:
        repository = (
            _resolve_path(
                _string(repository_value, "graal.repository", config_path),
                base_directory,
            )
            if repository_value is not None
            else None
        )

    if data_directory_override is not None:
        data_directory = _resolve_path(data_directory_override, working_directory)
    else:
        configured_data = _string(
            data.get("directory", DEFAULT_DATA_DIRECTORY),
            "data.directory",
            config_path,
        )
        data_directory = _resolve_path(configured_data, base_directory)

    suites_value = record.get("benchmark_suites", list(DEFAULT_BENCHMARK_SUITES))
    suites = _string_list(suites_value, "record.benchmark_suites", config_path)
    runs = _positive_int(
        record.get("runs", DEFAULT_RECORD_RUNS), "record.runs", config_path
    )
    window_size = _positive_int(
        record.get("hot_window_size", DEFAULT_HOT_WINDOW_SIZE),
        "record.hot_window_size",
        config_path,
    )
    threshold = _decimal_threshold(
        record.get("hot_method_threshold", str(DEFAULT_HOT_METHOD_THRESHOLD)),
        "record.hot_method_threshold",
        config_path,
    )
    iterations = _positive_int(
        replay.get("iterations", DEFAULT_REPLAY_ITERATIONS),
        "replay.iterations",
        config_path,
    )
    retired_instruction_event = _string(
        replay.get("retired_instruction_event", DEFAULT_RETIRED_INSTRUCTION_EVENT),
        "replay.retired_instruction_event",
        config_path,
    )
    comparison_thresholds = tuple(
        (
            name,
            _decimal_threshold(
                compare.get(f"{name}_threshold", str(default)),
                f"compare.{name}_threshold",
                config_path,
            ),
        )
        for name, default in TRACKED_THRESHOLDS
    )

    return Config(
        source=config_path,
        tools=ToolsConfig(mx=mx),
        graal=GraalConfig(repository=repository),
        data=DataConfig(directory=data_directory),
        record=RecordConfig(
            benchmark_suites=suites,
            runs=runs,
            hot_window_size=window_size,
            hot_method_threshold=threshold,
        ),
        replay=ReplayConfig(
            iterations=iterations,
            retired_instruction_event=retired_instruction_event,
        ),
        compare=CompareConfig(thresholds=comparison_thresholds),
    )


def require_graal_repository(config: Config) -> Path:
    """Return and validate the configured Graal repository.

    :param config: Resolved application configuration.
    :return: Validated Graal repository path.
    """

    repository = config.graal.repository
    if repository is None:
        raise ConfigurationError(
            "graal.repository is required; set it in the configuration or pass "
            "--graal-repository"
        )
    if not repository.is_dir():
        raise ConfigurationError(f"graal.repository is not a directory: {repository}")
    missing = [name for name in ("compiler", "vm") if not (repository / name).is_dir()]
    if missing:
        joined = ", ".join(missing)
        raise ConfigurationError(
            f"graal.repository {repository} is missing required suite directories: "
            f"{joined}"
        )
    return repository


def _select_config(explicit_path: Path | None, cwd: Path) -> Path | None:
    """Select an explicit or local configuration file.

    :param explicit_path: Explicit configuration path, if supplied.
    :param cwd: Directory used for relative paths and local discovery.
    :return: Selected configuration path, or ``None`` for built-in defaults.
    """

    if explicit_path is not None:
        path = explicit_path if explicit_path.is_absolute() else cwd / explicit_path
        path = path.resolve()
        if not path.exists():
            raise ConfigurationError(f"configuration file does not exist: {path}")
        if not path.is_file():
            raise ConfigurationError(f"configuration path is not a file: {path}")
        return path
    local = cwd / DEFAULT_CONFIG_NAME
    return local.resolve() if local.is_file() else None


def _validate_sections(
    raw: Mapping[str, Any], path: Path | None
) -> dict[str, Mapping[str, Any]]:
    """Validate and return every configuration section.

    :param raw: Parsed configuration tables.
    :param path: Configuration source path, if any.
    :return: Validated tables keyed by section name.
    """

    unknown_sections = sorted(set(raw) - set(_ALLOWED))
    if unknown_sections:
        raise _config_error(f"unknown top-level section {unknown_sections[0]!r}", path)
    sections: dict[str, Mapping[str, Any]] = {}
    for name, allowed_keys in _ALLOWED.items():
        value = raw.get(name, {})
        if not isinstance(value, dict):
            raise _config_error(f"{name} must be a table", path)
        unknown_keys = sorted(set(value) - allowed_keys)
        if unknown_keys:
            raise _config_error(f"unknown key {name}.{unknown_keys[0]}", path)
        sections[name] = value
    return sections


def _config_error(message: str, path: Path | None) -> ConfigurationError:
    """Add the configuration source to an error message.

    :param message: Validation error detail.
    :param path: Configuration source path, if any.
    :return: Error with source context.
    """

    location = str(path) if path is not None else "<built-in defaults>"
    return ConfigurationError(f"{location}: {message}")


def _string(value: object, key: str, path: Path | None) -> str:
    """Read a non-empty configuration string.

    :param value: Raw value.
    :param key: Qualified configuration key.
    :param path: Configuration source path, if any.
    :return: Validated string.
    """

    if not isinstance(value, str) or not value.strip():
        raise _config_error(f"{key} must be a non-empty string", path)
    return value


def _resolve_path(value: str | Path, base: Path) -> Path:
    """Resolve a path against a base directory.

    :param value: Path to resolve.
    :param base: Base for a relative path.
    :return: Absolute resolved path.
    """

    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _positive_int(value: object, key: str, path: Path | None) -> int:
    """Read a positive configuration integer.

    :param value: Raw value.
    :param key: Qualified configuration key.
    :param path: Configuration source path, if any.
    :return: Validated positive integer.
    """

    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _config_error(f"{key} must be a positive integer", path)
    return value


def _string_list(value: object, key: str, path: Path | None) -> tuple[str, ...]:
    """Read a non-empty list of unique strings.

    :param value: Raw value.
    :param key: Qualified configuration key.
    :param path: Configuration source path, if any.
    :return: Validated strings in configured order.
    """

    if not isinstance(value, list) or not value:
        raise _config_error(f"{key} must be a non-empty array of strings", path)
    result = tuple(_string(item, key, path) for item in value)
    if len(set(result)) != len(result):
        raise _config_error(f"{key} must not contain duplicates", path)
    return result


def _decimal_threshold(value: object, key: str, path: Path | None) -> Decimal:
    """Read a decimal threshold between zero and one, inclusive.

    :param value: Raw value.
    :param key: Qualified configuration key.
    :param path: Configuration source path, if any.
    :return: Validated decimal threshold.
    """

    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise _config_error(f"{key} must be a decimal number", path)
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise _config_error(f"{key} must be a decimal number", path) from error
    if not result.is_finite() or result < 0 or result > 1:
        raise _config_error(f"{key} must be at least 0 and at most 1", path)
    return result
