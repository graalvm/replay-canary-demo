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

"""Profile and bootstrap tests."""

from decimal import Decimal

import pytest

from replay_canary.errors import DataFormatError
from replay_canary.model.profjson import Profile, ProfileMethod
from replay_canary.profile_bootstrap import load_bootstrap_profile


@pytest.fixture
def profile() -> Profile:
    return Profile.from_json(
        {
            "totalPeriod": 100,
            "code": [
                {
                    "compileId": "42",
                    "name": "42: java.lang.String.charAt(int)",
                    "level": 3,
                    "period": 50,
                },
                {
                    "compileId": "100",
                    "name": "100: java.util.EnumMap$Values.iterator()",
                    "level": 4,
                    "period": 20,
                },
                {
                    "compileId": "101%",
                    "name": "101: pkg.Owner$$Lambda.0x0000aBcD.run(java.lang.Object, int)",
                    "level": 4,
                    "period": 30,
                },
                {
                    "compileId": None,
                    "name": "stub",
                    "level": None,
                    "period": 10,
                },
            ],
        }
    )


def test_hot_graal_methods(profile: Profile) -> None:
    methods = profile.hot_graal_methods(Decimal("0.1"))

    assert tuple(method.stable_name for method in methods) == (
        "java.util.EnumMap$Values.iterator()",
        "pkg.Owner$$Lambda.0x*.run(java.lang.Object;int)",
    )


def test_zero_threshold_selects_all_graal_methods(profile: Profile) -> None:
    methods = profile.hot_graal_methods(Decimal(0))

    assert tuple(method.compile_id for method in methods) == (100, 101)


def test_profile_method_rejects_malformed_compile_id() -> None:
    with pytest.raises(DataFormatError, match="compileId"):
        ProfileMethod.from_json(
            {
                "compileId": "not-an-id",
                "name": "A.m()",
                "level": 4,
                "period": 1,
            }
        )


@pytest.mark.parametrize(
    "suite,workload",
    [
        ("dacapo", "avrora"),
        ("renaissance", "scrabble"),
        ("scala-dacapo", "apparat"),
    ],
)
def test_embedded_bootstrap_profiles_load(suite: str, workload: str) -> None:
    methods = load_bootstrap_profile(suite, workload)

    assert methods is not None
    assert len(methods) > 3
    assert tuple(sorted(set(methods))) == methods


def test_unknown_bootstrap_suite_or_workload_returns_none() -> None:
    assert load_bootstrap_profile("unknown", "workload") is None
    assert load_bootstrap_profile("dacapo", "unknown") is None
