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

"""Shared persistent model values and validation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from pathlib import PurePosixPath
from typing import Any, Callable, Literal, Mapping, TypeAlias, cast, get_args
from uuid import UUID, uuid4

from replay_canary.errors import DataFormatError, ValidationError

#: Scalar value accepted by JSON.
JsonScalar: TypeAlias = None | bool | int | float | str
#: Any value accepted by JSON.
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
#: JSON object with string keys.
JsonObject: TypeAlias = dict[str, JsonValue]
#: Outcome category stored for a recording or replay subprocess.
ProcessStatus: TypeAlias = Literal[
    "succeeded",
    "skipped",
    "timed_out",
    "process_failed",
    "missing_profile",
    "profile_failed",
    "parse_failed",
    "empty_results",
]

_PROCESS_STATUSES = frozenset(get_args(ProcessStatus))


@dataclass(frozen=True)
class Identity:
    """Common identity fields for a local object."""

    #: Canonical UUID for the object.
    id: str
    #: Optional user-facing name.
    label: str | None
    #: Time when the object was created.
    created_at: datetime

    def __post_init__(self) -> None:
        """Validate identity fields."""

        validate_id(self.id)
        validate_label(self.label)
        if self.created_at.tzinfo is None:
            raise ValidationError("created_at must include a timezone")

    def as_json(self) -> JsonObject:
        """Return the common identity fields as JSON-compatible values.

        :return: JSON-compatible identity fields.
        """

        return {
            "id": self.id,
            "label": self.label,
            "created_at": format_timestamp(self.created_at),
        }

    @classmethod
    def from_json(cls, value: object) -> "Identity":
        """Parse identity fields from a manifest object.

        :param value: Raw manifest object.
        :return: Validated identity.
        """

        obj = require_object(value, "manifest")
        return cls(
            id=require_string(obj, "id"),
            label=optional_string(obj, "label"),
            created_at=parse_timestamp(require_string(obj, "created_at")),
        )


class IdentityFactory:
    """Create identities with injectable UUID and clock providers."""

    def __init__(
        self,
        id_provider: Callable[[], UUID] = uuid4,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        """Store the providers used to create identities.

        :param id_provider: Provider for new UUIDs.
        :param clock: Provider for creation timestamps.
        """

        #: Provider for new object IDs.
        self._id_provider = id_provider
        #: Provider for creation timestamps.
        self._clock = clock

    def create(self, label: str | None) -> Identity:
        """Create an identity.

        :param label: Optional user-facing label.
        :return: New identity.
        """

        return Identity(str(self._id_provider()), label, self._clock())


@dataclass(frozen=True)
class CommitMetadata:
    """Metadata for the compiler source commit."""

    #: Full commit hash.
    hash: str
    #: Commit timestamp.
    committed_at: datetime
    #: First line of the commit message.
    subject: str
    #: Commit author name.
    author_name: str
    #: Hashes of direct parent commits.
    parent_hashes: tuple[str, ...]

    def __post_init__(self) -> None:
        """Validate required commit metadata."""

        if not self.hash:
            raise ValidationError("commit hash must not be empty")
        if self.committed_at.tzinfo is None:
            raise ValidationError("commit timestamp must include a timezone")

    def as_json(self) -> JsonObject:
        """Serialize commit metadata.

        :return: JSON-compatible commit metadata.
        """

        return {
            "hash": self.hash,
            "committed_at": format_timestamp(self.committed_at),
            "subject": self.subject,
            "author_name": self.author_name,
            "parent_hashes": list(self.parent_hashes),
        }

    @classmethod
    def from_json(cls, value: object) -> "CommitMetadata":
        """Parse commit metadata.

        :param value: Raw commit object.
        :return: Validated commit metadata.
        """

        obj = require_object(value, "commit")
        return cls(
            hash=require_string(obj, "hash"),
            committed_at=parse_timestamp(require_string(obj, "committed_at")),
            subject=require_string(obj, "subject", allow_empty=True),
            author_name=require_string(obj, "author_name", allow_empty=True),
            parent_hashes=require_string_tuple(obj, "parent_hashes"),
        )


@dataclass(frozen=True)
class ProcessOutcome:
    """Persistent outcome of a recording or replay subprocess."""

    #: Process exit code, or ``None`` if none was available.
    exit_code: int | None
    #: Whether the process exceeded its timeout.
    timed_out: bool
    #: Elapsed wall-clock time in seconds.
    duration_seconds: float
    #: Machine-readable outcome category.
    status: ProcessStatus
    #: Optional detail about the outcome.
    message: str | None = None

    def __post_init__(self) -> None:
        """Validate process outcome fields."""

        if self.exit_code is not None and (
            isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int)
        ):
            raise ValidationError("exit_code must be an integer or null")
        if not isfinite(self.duration_seconds) or self.duration_seconds < 0:
            raise ValidationError("duration_seconds must be finite and non-negative")
        if self.status not in _PROCESS_STATUSES:
            raise ValidationError(f"invalid process status: {self.status!r}")

    def as_json(self) -> JsonObject:
        """Serialize the process outcome.

        :return: JSON-compatible process outcome.
        """

        return {
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "duration_seconds": self.duration_seconds,
            "status": self.status,
            "message": self.message,
        }

    @classmethod
    def from_json(cls, value: object) -> "ProcessOutcome":
        """Parse a process outcome.

        :param value: Raw outcome object.
        :return: Validated process outcome.
        """

        obj = require_object(value, "outcome")
        exit_code = obj.get("exit_code")
        if exit_code is not None and (
            isinstance(exit_code, bool) or not isinstance(exit_code, int)
        ):
            raise DataFormatError("outcome.exit_code must be an integer or null")
        return cls(
            exit_code=exit_code,
            timed_out=require_bool(obj, "timed_out"),
            duration_seconds=require_number(obj, "duration_seconds"),
            status=cast(ProcessStatus, require_string(obj, "status")),
            message=optional_string(obj, "message"),
        )


def validate_id(value: str) -> None:
    """Require a canonical UUID string.

    :param value: Object ID to validate.
    """

    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as error:
        raise ValidationError(f"invalid object ID: {value!r}") from error
    if str(parsed) != value:
        raise ValidationError(f"object ID is not a canonical UUID: {value!r}")


def validate_label(value: str | None) -> None:
    """Validate an optional artifact label.

    :param value: Label to validate, if present.
    """

    if value is None:
        return
    if not value.strip():
        raise ValidationError("label must not be empty or whitespace")
    if value != value.strip():
        raise ValidationError("label must not have leading or trailing whitespace")
    if "/" in value or "\\" in value or value in {".", ".."}:
        raise ValidationError("label must not be path-like")
    if not value.isprintable():
        raise ValidationError("label must contain only printable characters")


def validate_relative_path(value: str, field: str) -> str:
    """Validate a stored forward-slash relative path.

    :param value: Relative path text.
    :param field: Field name used in errors.
    :return: Validated path text.
    """

    if "\\" in value:
        raise ValidationError(f"{field} must use forward slashes")
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or path.as_posix() != value
        or ".." in path.parts
    ):
        raise ValidationError(f"{field} must be a safe relative path")
    return value


def format_timestamp(value: datetime) -> str:
    """Format a timezone-aware timestamp as RFC 3339 UTC.

    :param value: Timezone-aware timestamp.
    :return: UTC timestamp text.
    """

    if value.tzinfo is None:
        raise ValidationError("timestamp must include a timezone")
    utc = value.astimezone(timezone.utc)
    timespec = "microseconds" if utc.microsecond else "seconds"
    return utc.isoformat(timespec=timespec).replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    """Parse an RFC 3339 timestamp.

    :param value: Timestamp text.
    :return: Timezone-aware timestamp.
    """

    try:
        normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
        timestamp = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise DataFormatError(f"invalid timestamp: {value!r}") from error
    if timestamp.tzinfo is None:
        raise DataFormatError(f"timestamp has no timezone: {value!r}")
    return timestamp


def require_object(value: object, field: str) -> Mapping[str, Any]:
    """Require a mapping with string keys.

    :param value: Raw value.
    :param field: Field name used in errors.
    :return: Validated mapping.
    """

    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise DataFormatError(f"{field} must be an object")
    return cast(Mapping[str, Any], value)


def require_list(value: object, field: str) -> list[Any]:
    """Require a JSON array.

    :param value: Raw value.
    :param field: Field name used in errors.
    :return: Validated list.
    """

    if not isinstance(value, list):
        raise DataFormatError(f"{field} must be an array")
    return value


def require_string(
    obj: Mapping[str, Any], key: str, *, allow_empty: bool = False
) -> str:
    """Read a required string property.

    :param obj: Source object.
    :param key: Property name.
    :param allow_empty: Whether an empty string is accepted.
    :return: Validated string value.
    """

    value = obj.get(key)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise DataFormatError(
            f"{key} must be a{' non-empty' if not allow_empty else ''} string"
        )
    return value


def optional_string(obj: Mapping[str, Any], key: str) -> str | None:
    """Read an optional nullable string property.

    :param obj: Source object.
    :param key: Property name.
    :return: String value, or ``None`` when absent or null.
    """

    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise DataFormatError(f"{key} must be a string or null")
    return value


def require_bool(obj: Mapping[str, Any], key: str) -> bool:
    """Read a required Boolean property.

    :param obj: Source object.
    :param key: Property name.
    :return: Boolean value.
    """

    value = obj.get(key)
    if not isinstance(value, bool):
        raise DataFormatError(f"{key} must be a boolean")
    return value


def require_int(obj: Mapping[str, Any], key: str, *, minimum: int = 0) -> int:
    """Read a required integer property.

    :param obj: Source object.
    :param key: Property name.
    :param minimum: Smallest accepted value.
    :return: Validated integer.
    """

    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise DataFormatError(
            f"{key} must be an integer greater than or equal to {minimum}"
        )
    return value


def require_number(obj: Mapping[str, Any], key: str) -> float:
    """Read a required finite non-negative number.

    :param obj: Source object.
    :param key: Property name.
    :return: Validated number as a float.
    """

    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DataFormatError(f"{key} must be a number")
    result = float(value)
    if not isfinite(result) or result < 0:
        raise DataFormatError(f"{key} must be finite and non-negative")
    return result


def require_string_tuple(obj: Mapping[str, Any], key: str) -> tuple[str, ...]:
    """Read an array of strings.

    :param obj: Source object.
    :param key: Property name.
    :return: String values as a tuple.
    """

    values = require_list(obj.get(key), key)
    if not all(isinstance(item, str) for item in values):
        raise DataFormatError(f"{key} must contain only strings")
    return tuple(cast(list[str], values))


def reject_unknown_keys(
    obj: Mapping[str, Any], allowed: set[str], context: str
) -> None:
    """Reject unknown persistent fields.

    :param obj: Persistent object to inspect.
    :param allowed: Accepted field names.
    :param context: Object name used in errors.
    """

    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise DataFormatError(f"{context} contains unknown field {unknown[0]!r}")
