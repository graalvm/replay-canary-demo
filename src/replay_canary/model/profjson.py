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

"""Parsing of Graal proftool JSON."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from replay_canary.errors import DataFormatError, ValidationError
from replay_canary.model.common import (
    require_int,
    require_list,
    require_object,
    require_string,
)

#: Unstable address suffix emitted in some profile method names.
_UNSTABLE_HEX_SUFFIX = re.compile(r"\.0x[a-fA-F0-9]+")


@dataclass(frozen=True)
class ProfileMethod:
    """One compiled-code entry from a profile."""

    #: Compile ID, if the profile provides one.
    compile_id: int | None
    #: Method name emitted by proftool.
    name: str
    #: Compilation level, if known.
    level: int | None
    #: Number of samples attributed to the method.
    period: int

    @property
    def stable_name(self) -> str:
        """Return a stable Graal MethodFilter-compatible name.

        :return: Normalized method-filter name.
        """

        name = self.name
        colon = name.find(":")
        if colon != -1:
            name = name[colon + 1 :].lstrip()
        return _UNSTABLE_HEX_SUFFIX.sub(".0x*", name).replace(", ", ";")

    @classmethod
    def from_json(cls, value: object) -> "ProfileMethod":
        """Parse one profile method.

        :param value: Raw method object.
        :return: Validated profile method.
        """

        obj = require_object(value, "profile method")
        name = require_string(obj, "name")
        compile_id_value = obj.get("compileId")
        compile_id: int | None
        if compile_id_value is None:
            compile_id = None
        elif isinstance(compile_id_value, bool):
            raise DataFormatError(
                "profile method compileId must be an integer or string"
            )
        elif isinstance(compile_id_value, int):
            compile_id = compile_id_value
        elif isinstance(compile_id_value, str):
            try:
                compile_id = int(compile_id_value.removesuffix("%"))
            except ValueError as error:
                raise DataFormatError(
                    f"invalid profile method compileId: {compile_id_value!r}"
                ) from error
        else:
            raise DataFormatError(
                "profile method compileId must be an integer or string"
            )
        level_value: Any = obj.get("level")
        if level_value is not None and (
            isinstance(level_value, bool) or not isinstance(level_value, int)
        ):
            raise DataFormatError("profile method level must be an integer or null")
        return cls(
            compile_id=compile_id,
            name=name,
            level=level_value,
            period=require_int(obj, "period"),
        )


@dataclass(frozen=True)
class Profile:
    """One profile converted by ``mx profjson``."""

    #: Total number of samples in the profile.
    total_period: int
    #: Compiled methods found in the profile.
    methods: tuple[ProfileMethod, ...]

    @classmethod
    def from_json(cls, value: object) -> "Profile":
        """Parse a profile document.

        :param value: Raw profile object.
        :return: Validated profile.
        """

        obj = require_object(value, "profile")
        total_period = require_int(obj, "totalPeriod")
        methods = tuple(
            ProfileMethod.from_json(item)
            for item in require_list(obj.get("code"), "code")
        )
        return cls(total_period=total_period, methods=methods)

    def hot_graal_methods(self, threshold: Decimal) -> tuple[ProfileMethod, ...]:
        """Return level-four methods meeting the fractional sample threshold.

        :param threshold: Minimum share of total samples.
        :return: Hot Graal methods in profile order.
        """

        if not threshold.is_finite() or not (Decimal(0) <= threshold <= Decimal(1)):
            raise ValidationError("profile threshold must be at least 0 and at most 1")
        if self.total_period == 0:
            return ()
        minimum_period = threshold * self.total_period
        return tuple(
            method
            for method in self.methods
            if method.level == 4 and method.period >= minimum_period
        )
