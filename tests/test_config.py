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

"""Configuration tests."""

from decimal import Decimal
from pathlib import Path

import pytest

from replay_canary.config import load_config, require_graal_repository
from replay_canary.errors import ConfigurationError


def test_built_in_defaults_resolve_from_working_directory(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)

    assert config.source is None
    assert config.tools.mx == "mx"
    assert config.graal.repository is None
    assert config.data.directory == tmp_path / "replay-canary-data"
    assert config.record.benchmark_suites == (
        "renaissance",
        "dacapo",
        "scala-dacapo",
    )
    assert config.record.hot_method_threshold == Decimal("0.001")
    assert config.replay.iterations == 2
    assert config.replay.retired_instruction_event == "PAPI_TOT_INS"
    assert dict(config.compare.thresholds) == {
        "retired_instructions": Decimal("0.03"),
        "allocated_memory": Decimal("0.02"),
        "target_code_size": Decimal("0.02"),
    }


def test_relative_paths_resolve_from_explicit_config(tmp_path: Path) -> None:
    config_directory = tmp_path / "configuration"
    config_directory.mkdir()
    config_path = config_directory / "custom.toml"
    config_path.write_text(
        """
[tools]
mx = "/tools/mx"
[graal]
repository = "../graal"
[data]
directory = "artifacts"
[record]
benchmark_suites = ["suite"]
runs = 3
hot_window_size = 5
hot_method_threshold = "0.02"
[replay]
iterations = 4
retired_instruction_event = "RETIRED_INSTRUCTIONS"
[compare]
retired_instructions_threshold = "0.05"
allocated_memory_threshold = 0
target_code_size_threshold = "0.01"
""",
        encoding="utf-8",
    )

    config = load_config(config_path, cwd=tmp_path)

    assert config.source == config_path
    assert config.graal.repository == tmp_path / "graal"
    assert config.data.directory == config_directory / "artifacts"
    assert config.record.runs == 3
    assert config.record.hot_window_size == 5
    assert config.record.hot_method_threshold == Decimal("0.02")
    assert config.replay.iterations == 4
    assert config.replay.retired_instruction_event == "RETIRED_INSTRUCTIONS"
    assert dict(config.compare.thresholds) == {
        "retired_instructions": Decimal("0.05"),
        "allocated_memory": Decimal("0"),
        "target_code_size": Decimal("0.01"),
    }


def test_data_directory_override_resolves_from_working_directory(
    tmp_path: Path,
) -> None:
    config_directory = tmp_path / "configuration"
    config_directory.mkdir()
    config_path = config_directory / "custom.toml"
    config_path.write_text("[data]\ndirectory = 'ignored'\n", encoding="utf-8")

    config = load_config(config_path, Path("one-off"), cwd=tmp_path)

    assert config.data.directory == tmp_path / "one-off"


def test_graal_repository_override_wins_and_resolves_from_working_directory(
    tmp_path: Path,
) -> None:
    config_directory = tmp_path / "configuration"
    config_directory.mkdir()
    config_path = config_directory / "custom.toml"
    config_path.write_text(
        "[graal]\nrepository = 'configured'\n",
        encoding="utf-8",
    )

    config = load_config(
        config_path,
        graal_repository_override=Path("graal-variant-a"),
        cwd=tmp_path,
    )

    assert config.graal.repository == tmp_path / "graal-variant-a"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("[unknown]\nvalue = 1\n", "unknown top-level section"),
        ("[record]\nrnus = 2\n", "unknown key record.rnus"),
        ("[record]\nruns = 0\n", "record.runs must be a positive integer"),
        (
            "[record]\nhot_method_threshold = '2'\n",
            "record.hot_method_threshold must be at least 0 and at most 1",
        ),
        (
            "[record]\nbenchmark_suites = ['same', 'same']\n",
            "record.benchmark_suites must not contain duplicates",
        ),
        (
            "[replay]\nretired_instruction_event = ''\n",
            "replay.retired_instruction_event must be a non-empty string",
        ),
        (
            "[compare]\nretired_instructions_threshold = '1.1'\n",
            "compare.retired_instructions_threshold must be at least 0 and at most 1",
        ),
    ],
)
def test_invalid_configuration_reports_file_and_key(
    tmp_path: Path, text: str, expected: str
) -> None:
    path = tmp_path / "bad.toml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ConfigurationError, match=expected) as caught:
        load_config(path, cwd=tmp_path)

    assert str(path) in str(caught.value)


def test_explicit_missing_configuration_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="does not exist"):
        load_config(Path("missing.toml"), cwd=tmp_path)


def test_graal_repository_shape_is_validated(tmp_path: Path) -> None:
    repository = tmp_path / "graal"
    repository.mkdir()
    config_path = tmp_path / "replay-canary.toml"
    config_path.write_text("[graal]\nrepository = 'graal'\n", encoding="utf-8")
    config = load_config(config_path, cwd=tmp_path)

    with pytest.raises(ConfigurationError, match="compiler, vm"):
        require_graal_repository(config)

    (repository / "compiler").mkdir()
    (repository / "vm").mkdir()
    assert require_graal_repository(config) == repository
