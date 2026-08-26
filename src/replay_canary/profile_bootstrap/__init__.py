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

"""Embedded initial hot-method profiles."""

from __future__ import annotations

import json
from importlib.resources import files

from replay_canary.errors import DataFormatError
from replay_canary.model.common import require_list, require_object, require_string


def load_bootstrap_profile(
    suite_name: str, workload_name: str
) -> tuple[str, ...] | None:
    """Load one embedded bootstrap profile when available.

    :param suite_name: Benchmark suite name.
    :param workload_name: Workload name within the suite.
    :return: Bootstrap method names, or ``None`` when no profile is packaged.
    """

    resource = files(__package__).joinpath(f"{suite_name}.json")
    if not resource.is_file():
        return None
    try:
        value: object = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DataFormatError(
            f"cannot load bootstrap profile for {suite_name}: {error}"
        ) from error
    for entry_value in require_list(value, f"bootstrap profile {suite_name}"):
        entry = require_object(entry_value, "bootstrap workload")
        entry_workload = require_string(entry, "workload_name")
        if entry_workload == workload_name:
            methods = require_list(entry.get("hot_methods"), "hot_methods")
            if not all(isinstance(method, str) and method for method in methods):
                raise DataFormatError("bootstrap hot_methods must contain strings")
            return tuple(sorted(set(methods)))
    return None
