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

"""Hot-method rolling-window model."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from replay_canary.errors import DataFormatError, ValidationError
from replay_canary.model.common import (
    JsonObject,
    reject_unknown_keys,
    require_int,
    require_list,
    require_object,
    require_string,
)


@dataclass(frozen=True)
class HotMethodWindow:
    """Recent hot-method sets for one benchmark workload."""

    #: Benchmark suite name.
    suite_name: str
    #: Workload name within the suite.
    workload_name: str
    #: Maximum number of recent profiles to keep.
    window_size: int
    #: Minimum sample share used to select hot methods.
    hot_method_threshold: Decimal
    #: Hot method names from each recent profile.
    profiles: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        """Validate the window policy and stored profiles."""

        if not self.suite_name or not self.workload_name:
            raise ValidationError("suite and workload names must not be empty")
        if self.window_size < 1:
            raise ValidationError("window_size must be positive")
        if not self.hot_method_threshold.is_finite() or not (
            Decimal(0) <= self.hot_method_threshold <= Decimal(1)
        ):
            raise ValidationError(
                "hot_method_threshold must be at least 0 and at most 1"
            )
        if len(self.profiles) > self.window_size:
            raise ValidationError("profiles exceed the configured window_size")

    def append(self, methods: list[str] | tuple[str, ...]) -> "HotMethodWindow":
        """Return a copy with one normalized profile appended.

        :param methods: Hot methods from the new profile.
        :return: Updated rolling window.
        """

        normalized = tuple(sorted(set(methods)))
        profiles = (*self.profiles, normalized)[-self.window_size :]
        return HotMethodWindow(
            self.suite_name,
            self.workload_name,
            self.window_size,
            self.hot_method_threshold,
            profiles,
        )

    def copy_with(
        self, *, window_size: int, hot_method_threshold: Decimal
    ) -> "HotMethodWindow":
        """Retain compatible recent history after policy changes.

        :param window_size: New maximum number of profiles.
        :param hot_method_threshold: New hot-method threshold.
        :return: Window adjusted to the new policy.
        """

        profiles = (
            self.profiles[-window_size:]
            if hot_method_threshold == self.hot_method_threshold
            else ()
        )
        return HotMethodWindow(
            self.suite_name,
            self.workload_name,
            window_size,
            hot_method_threshold,
            profiles,
        )

    def hot_methods(self) -> tuple[str, ...]:
        """Return the sorted union of methods in the rolling window.

        :return: Unique hot method names in sorted order.
        """

        return tuple(
            sorted({method for profile in self.profiles for method in profile})
        )

    def as_json(self) -> JsonObject:
        """Serialize the hot-method window.

        :return: JSON-compatible hot-method window.
        """

        return {
            "suite_name": self.suite_name,
            "workload_name": self.workload_name,
            "window_size": self.window_size,
            "hot_method_threshold": str(self.hot_method_threshold),
            "profiles": [list(profile) for profile in self.profiles],
        }

    @classmethod
    def from_json(cls, value: object) -> "HotMethodWindow":
        """Parse and validate a hot-method window.

        :param value: Raw hot-method window object.
        :return: Validated hot-method window.
        """

        obj = require_object(value, "hot-method window")
        reject_unknown_keys(
            obj,
            {
                "suite_name",
                "workload_name",
                "window_size",
                "hot_method_threshold",
                "profiles",
            },
            "hot-method window",
        )
        threshold_text = require_string(obj, "hot_method_threshold")
        try:
            threshold = Decimal(threshold_text)
        except InvalidOperation as error:
            raise DataFormatError("hot_method_threshold must be a decimal") from error
        raw_profiles = require_list(obj.get("profiles"), "profiles")
        profiles: list[tuple[str, ...]] = []
        for index, raw_profile in enumerate(raw_profiles):
            items: list[Any] = require_list(raw_profile, f"profiles[{index}]")
            if not all(isinstance(item, str) and item for item in items):
                raise DataFormatError(
                    f"profiles[{index}] must contain non-empty strings"
                )
            profiles.append(tuple(items))
        return cls(
            suite_name=require_string(obj, "suite_name"),
            workload_name=require_string(obj, "workload_name"),
            window_size=require_int(obj, "window_size", minimum=1),
            hot_method_threshold=threshold,
            profiles=tuple(profiles),
        )
