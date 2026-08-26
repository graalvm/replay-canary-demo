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

"""Persistent model tests."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from replay_canary.errors import DataFormatError, ValidationError
from replay_canary.model.common import Identity, IdentityFactory
from replay_canary.model.corpus import RunKey
from replay_canary.model.profile import HotMethodWindow


def test_identity_factory_uses_injected_id_and_clock() -> None:
    timestamp = datetime(2026, 7, 30, 12, 34, 56, tzinfo=timezone.utc)
    factory = IdentityFactory(
        id_provider=lambda: UUID("00000000-0000-4000-8000-000000000001"),
        clock=lambda: timestamp,
    )

    identity = factory.create("friendly-name")

    assert identity.as_json() == {
        "id": "00000000-0000-4000-8000-000000000001",
        "label": "friendly-name",
        "created_at": "2026-07-30T12:34:56Z",
    }
    assert Identity.from_json(identity.as_json()) == identity


def test_identity_round_trip_preserves_microseconds() -> None:
    timestamp = datetime(2026, 7, 30, 12, 34, 56, 123456, tzinfo=timezone.utc)
    identity = Identity("00000000-0000-4000-8000-000000000001", None, timestamp)

    assert identity.as_json()["created_at"] == "2026-07-30T12:34:56.123456Z"
    assert Identity.from_json(identity.as_json()) == identity


@pytest.mark.parametrize(
    "label",
    [
        "",
        "  ",
        " leading",
        "trailing ",
        "../path",
        "path/name",
        "path\\name",
        "bad\nlabel",
    ],
)
def test_identity_rejects_invalid_labels(label: str) -> None:
    with pytest.raises(ValidationError, match="label"):
        Identity(
            "00000000-0000-4000-8000-000000000001",
            label,
            datetime.now(timezone.utc),
        )


def test_run_key_is_stable_and_validated() -> None:
    key = RunKey("renaissance", "scrabble", 2)
    assert key.value == "renaissance--scrabble--2"
    assert RunKey.from_json(key.as_json()) == key

    with pytest.raises(ValidationError, match="run_index"):
        RunKey("renaissance", "scrabble", -1)


def test_hot_method_window_rolls_and_resets_incompatible_history() -> None:
    window = HotMethodWindow(
        "renaissance",
        "scrabble",
        2,
        Decimal("0.01"),
        (("B.m()",),),
    )

    advanced = window.append(["C.m()", "A.m()", "A.m()"]).append(["D.m()"])
    assert advanced.profiles == (("A.m()", "C.m()"), ("D.m()",))
    assert advanced.hot_methods() == ("A.m()", "C.m()", "D.m()")
    assert HotMethodWindow.from_json(advanced.as_json()) == advanced

    resized = advanced.copy_with(window_size=1, hot_method_threshold=Decimal("0.01"))
    assert resized.profiles == (("D.m()",),)

    changed_threshold = advanced.copy_with(
        window_size=2, hot_method_threshold=Decimal("0.02")
    )
    assert changed_threshold.profiles == ()


def test_hot_method_window_rejects_unknown_fields() -> None:
    with pytest.raises(DataFormatError, match="unknown field"):
        HotMethodWindow.from_json(
            {
                "suite_name": "suite",
                "workload_name": "workload",
                "window_size": 2,
                "hot_method_threshold": "0.01",
                "profiles": [],
                "schema_version": 1,
            }
        )
