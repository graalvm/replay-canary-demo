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

"""Copyright notice tests."""

import re
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIRST_COPYRIGHT_YEAR = 2026
COPYRIGHT_PATTERN = re.compile(
    r"# Copyright \(c\) (?:(?P<start>\d{4}), )?(?P<end>\d{4}), "
    r"Oracle and/or its affiliates\. All rights reserved\."
)
NOTICE_PREFIX = ("#",)
NOTICE_SUFFIX = (
    "# DO NOT ALTER OR REMOVE COPYRIGHT NOTICES OR THIS FILE HEADER.",
    "#",
    "# The Universal Permissive License (UPL), Version 1.0",
    "#",
    "# Subject to the condition set forth below, permission is hereby granted to any",
    "# person obtaining a copy of this software, associated documentation and/or",
    '# data (collectively the "Software"), free of charge and under any and all',
    "# copyright rights in the Software, and any and all patent rights owned or",
    "# freely licensable by each licensor hereunder covering either (i) the",
    "# unmodified Software as contributed to or provided by such licensor, or (ii)",
    "# the Larger Works (as defined below), to deal in both",
    "#",
    "# (a) the Software, and",
    "#",
    "# (b) any piece of software and/or hardware listed in the lrgrwrks.txt file if",
    '# one is included with the Software (each a "Larger Work" to which the Software',
    "# is contributed by such licensors),",
    "#",
    "# without restriction, including without limitation the rights to copy, create",
    "# derivative works of, display, perform, and distribute the Software and make,",
    "# use, sell, offer for sale, import, export, have made, and have sold the",
    "# Software and the Larger Work(s), and to sublicense the foregoing rights on",
    "# either these or other terms.",
    "#",
    "# This license is subject to the following condition:",
    "#",
    "# The above copyright notice and either this complete permission notice or at a",
    "# minimum a reference to the UPL must be included in all copies or substantial",
    "# portions of the Software.",
    "#",
    '# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR',
    "# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,",
    "# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE",
    "# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER",
    "# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,",
    "# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE",
    "# SOFTWARE.",
    "#",
)


def _notice_error(path: Path) -> str | None:
    """Return an error describing an invalid Python copyright notice."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if tuple(lines[: len(NOTICE_PREFIX)]) != NOTICE_PREFIX:
        return "standard notice is not at the beginning of the file"
    if len(lines) < 2:
        return "copyright line is missing"

    match = COPYRIGHT_PATTERN.fullmatch(lines[1])
    if match is None:
        return "Oracle copyright line is missing or malformed"

    start_year = int(match.group("start") or match.group("end"))
    end_year = int(match.group("end"))
    current_year = date.today().year
    if not FIRST_COPYRIGHT_YEAR <= start_year <= end_year <= current_year:
        return (
            f"copyright years must satisfy {FIRST_COPYRIGHT_YEAR} <= start <= "
            f"end <= {current_year}, found {start_year}..{end_year}"
        )

    suffix_end = 2 + len(NOTICE_SUFFIX)
    if tuple(lines[2:suffix_end]) != NOTICE_SUFFIX:
        return "UPL notice is incomplete or malformed"
    return None


def test_python_files_have_valid_copyright_notices() -> None:
    """Require valid notices on Python source and test files."""
    paths = sorted(
        (
            *PROJECT_ROOT.joinpath("src").rglob("*.py"),
            *PROJECT_ROOT.joinpath("tests").rglob("*.py"),
        )
    )
    failures = [
        f"{path.relative_to(PROJECT_ROOT)}: {error}"
        for path in paths
        if (error := _notice_error(path)) is not None
    ]
    assert not failures, "Invalid copyright notices:\n" + "\n".join(failures)
