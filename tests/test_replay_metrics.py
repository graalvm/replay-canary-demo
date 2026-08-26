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

"""Replay metrics tests."""

import pytest

from replay_canary.errors import ValidationError
from replay_canary.model.replay import (
    CompilationMetrics,
    IterationMetrics,
    ReplayMetrics,
)


def launcher_results() -> list[object]:
    return [
        {
            "type": "compilation",
            "iteration": 0,
            "compile_id": 17,
            "method_name": "pkg.Type.method(int)",
            "entry_bci": -1,
            "wall_time_ns": 11,
            "thread_time_ns": 12,
            "allocated_memory": 13,
            "compiled_bytecodes": 14,
            "target_code_size": 15,
            "target_code_hash": "1a2b3c4d",
            "events": {"PAPI_TOT_INS": 16},
        },
        {
            "type": "iteration_total",
            "iteration": 0,
            "wall_time_ns": 21,
            "thread_time_ns": 22,
            "allocated_memory": 23,
            "compiled_bytecodes": 24,
            "target_code_size": 25,
            "target_code_hash": "01020304",
            "events": {"PAPI_TOT_INS": 26},
        },
        {
            "type": "future_record_type",
            "ignored": True,
        },
    ]


def test_launcher_results_parse_and_normalized_format_round_trips() -> None:
    metrics = ReplayMetrics.from_launcher_json(
        launcher_results(), retired_instruction_event="PAPI_TOT_INS"
    )

    assert metrics.iterations == (
        IterationMetrics(
            iteration=0,
            wall_time_ns=21,
            thread_time_ns=22,
            allocated_memory=23,
            compiled_bytecodes=24,
            target_code_size=25,
            target_code_hash="01020304",
            retired_instructions=26,
        ),
    )
    assert metrics.compilations == (
        CompilationMetrics(
            iteration=0,
            compile_id=17,
            method_name="pkg.Type.method(int)",
            entry_bci=-1,
            wall_time_ns=11,
            thread_time_ns=12,
            allocated_memory=13,
            compiled_bytecodes=14,
            target_code_size=15,
            target_code_hash="1a2b3c4d",
            retired_instructions=16,
        ),
    )
    assert ReplayMetrics.from_json(metrics.as_json()) == metrics


def test_missing_event_is_recorded_as_zero() -> None:
    value = launcher_results()
    compilation = value[0]
    assert isinstance(compilation, dict)
    compilation.pop("events")

    metrics = ReplayMetrics.from_launcher_json(
        value, retired_instruction_event="PAPI_TOT_INS"
    )

    assert metrics.compilations[0].retired_instructions == 0


def test_duplicate_iteration_and_compilation_keys_are_rejected() -> None:
    value = launcher_results()
    iteration = value[1]
    assert isinstance(iteration, dict)
    value.append(dict(iteration))
    with pytest.raises(ValidationError, match="duplicate iterations"):
        ReplayMetrics.from_launcher_json(
            value, retired_instruction_event="PAPI_TOT_INS"
        )

    value = launcher_results()
    compilation = value[0]
    assert isinstance(compilation, dict)
    value.append(dict(compilation))
    with pytest.raises(ValidationError, match="duplicate compilations"):
        ReplayMetrics.from_launcher_json(
            value, retired_instruction_event="PAPI_TOT_INS"
        )


def test_malformed_metric_is_rejected() -> None:
    value = launcher_results()
    compilation = value[0]
    assert isinstance(compilation, dict)
    compilation["compile_id"] = "invalid"

    with pytest.raises(ValidationError, match="compile_id"):
        ReplayMetrics.from_launcher_json(
            value, retired_instruction_event="PAPI_TOT_INS"
        )

    value = launcher_results()
    compilation = value[0]
    assert isinstance(compilation, dict)
    compilation["target_code_hash"] = None
    with pytest.raises(ValidationError, match="target_code_hash"):
        ReplayMetrics.from_launcher_json(
            value, retired_instruction_event="PAPI_TOT_INS"
        )


def test_configured_event_is_used_for_retired_instructions() -> None:
    value = launcher_results()
    for record in value[:2]:
        assert isinstance(record, dict)
        record["events"] = {
            "PAPI_TOT_INS": 1,
            "RETIRED_INSTRUCTIONS": 42,
        }

    metrics = ReplayMetrics.from_launcher_json(
        value, retired_instruction_event="RETIRED_INSTRUCTIONS"
    )

    assert metrics.iterations[0].retired_instructions == 42
    assert metrics.compilations[0].retired_instructions == 42
