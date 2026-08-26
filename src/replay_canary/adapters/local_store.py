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

"""Local persistence for replay canary artifacts."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Generic, Protocol, TypeVar
from uuid import uuid4

from replay_canary.errors import DataFormatError, SelectorError, ValidationError
from replay_canary.model.common import Identity, JsonObject, validate_id
from replay_canary.model.comparison import ComparisonManifest
from replay_canary.model.corpus import CorpusManifest
from replay_canary.model.profile import HotMethodWindow
from replay_canary.model.replay import ReplayManifest, ReplayMetrics


class Manifest(Protocol):
    """Shape shared by immutable manifests."""

    @property
    def identity(self) -> Identity:
        """Return the immutable object identity.

        :return: Manifest identity.
        """
        ...

    def as_json(self) -> JsonObject:
        """Serialize the manifest.

        :return: JSON-compatible manifest.
        """
        ...


#: Manifest type stored by an immutable repository.
ManifestType = TypeVar("ManifestType", bound=Manifest)

#: Hot-method window directory name.
_HOT_METHOD_WINDOWS_DIRECTORY = "hot-method-windows"
#: Corpus artifact directory name.
_CORPORA_DIRECTORY = "corpora"
#: Replay artifact directory name.
_REPLAYS_DIRECTORY = "replays"
#: Comparison artifact directory name.
_COMPARISONS_DIRECTORY = "comparisons"
#: Invocation work directory name.
_WORK_DIRECTORY = "work"
#: Complete set of top-level data directory names.
_DATA_DIRECTORIES = (
    _HOT_METHOD_WINDOWS_DIRECTORY,
    _CORPORA_DIRECTORY,
    _REPLAYS_DIRECTORY,
    _COMPARISONS_DIRECTORY,
    _WORK_DIRECTORY,
)
#: Staged artifact directory name within an invocation workspace.
_STAGING_DIRECTORY = "staging"
#: Artifact manifest file name.
_MANIFEST_FILENAME = "manifest.json"


@dataclass(frozen=True)
class StagingArea:
    """One workspace area, which is retained until published."""

    #: Invocation work directory.
    work_directory: Path
    #: Directory containing the staged artifact.
    object_directory: Path
    #: ID reserved for the staged artifact.
    object_id: str


class DataLayout:
    """Validated paths beneath the configured data directory."""

    def __init__(self, root: Path) -> None:
        """Store the resolved data root.

        :param root: Root directory for generated local data.
        """

        #: Resolved root of all local data.
        self.root = root.resolve()

    def initialize(self) -> None:
        """Create all configured local data directories."""

        self.root.mkdir(parents=True, exist_ok=True)
        for name in _DATA_DIRECTORIES:
            (self.root / name).mkdir(exist_ok=True)

    def child(self, name: str) -> Path:
        """Return a known top-level directory.

        :param name: Configured data-layout directory name.
        :return: Path below the data root.
        """

        if name not in _DATA_DIRECTORIES:
            raise ValueError(f"unknown data layout directory: {name}")
        return self.root / name

    def create_work_directory(self, command: str) -> Path:
        """Create a unique invocation directory under ``work``.

        :param command: Command name used in the directory name.
        :return: New invocation directory.
        """

        _validate_path_component(command, "command")
        self.initialize()
        path = self.child(_WORK_DIRECTORY) / f"{command}-{uuid4()}"
        path.mkdir()
        return path


class ImmutableRepository(Generic[ManifestType]):
    """Store one immutable artifact type with a manifest."""

    def __init__(
        self,
        layout: DataLayout,
        directory_name: str,
        parser: Callable[[object], ManifestType],
        artifact_validator: Callable[[Path, ManifestType], None],
    ) -> None:
        """Configure storage and validation for one artifact type.

        :param layout: Shared local data layout.
        :param directory_name: Top-level artifact directory name.
        :param parser: Parser for persisted manifests.
        :param artifact_validator: Validator for referenced files.
        """

        #: Shared local data layout.
        self._layout = layout
        #: Top-level directory for this artifact type.
        self._directory_name = directory_name
        #: Parser for persisted manifests.
        self._parser = parser
        #: Validator for files referenced by a manifest.
        self._artifact_validator = artifact_validator
        self._layout.initialize()
        #: Root directory for this artifact type.
        self._root = layout.child(directory_name)

    def create_staging(self, identity: Identity) -> StagingArea:
        """Create an empty staging object after checking identity and label.

        :param identity: Reserved identity for the new artifact.
        :return: New staging area.
        """

        validate_id(identity.id)
        if (self._root / identity.id).exists():
            raise ValidationError(
                f"{self._directory_name} object already exists: {identity.id}"
            )
        self._require_unique_label(identity.label)
        invocation = f"{self._directory_name}-{uuid4()}"
        work_directory = self._layout.child(_WORK_DIRECTORY) / invocation
        object_directory = work_directory / _STAGING_DIRECTORY
        object_directory.mkdir(parents=True)
        return StagingArea(work_directory, object_directory, identity.id)

    def publish(self, staging: StagingArea, manifest: ManifestType) -> Path:
        """Validate and atomically publish a complete staged object.

        :param staging: Staging area containing artifact files.
        :param manifest: Complete manifest for the staged artifact.
        :return: Published artifact directory.
        """

        if manifest.identity.id != staging.object_id:
            raise ValidationError("staged object ID does not match its manifest")
        self._validate_staging(staging)
        self._require_unique_label(manifest.identity.label)
        manifest_path = staging.object_directory / _MANIFEST_FILENAME
        write_json_atomic(manifest_path, manifest.as_json())
        parsed = self._parser(read_json(manifest_path))
        if parsed != manifest:
            raise DataFormatError("staged manifest did not round-trip")
        self._artifact_validator(staging.object_directory, parsed)
        final_directory = self._root / manifest.identity.id
        if final_directory.exists():
            raise ValidationError(
                f"{self._directory_name} object already exists: {manifest.identity.id}"
            )
        os.rename(staging.object_directory, final_directory)
        try:
            staging.work_directory.rmdir()
        except OSError:
            pass
        return final_directory

    def get(self, object_id: str) -> ManifestType:
        """Load and validate an object by exact ID.

        :param object_id: Canonical artifact ID.
        :return: Parsed and validated manifest.
        """

        validate_id(object_id)
        object_directory = self._root / object_id
        if not object_directory.is_dir():
            raise SelectorError(f"unknown {self._directory_name} ID: {object_id}")
        manifest = self._parser(read_json(object_directory / _MANIFEST_FILENAME))
        if manifest.identity.id != object_id:
            raise DataFormatError(
                f"manifest ID {manifest.identity.id} does not match directory {object_id}"
            )
        self._artifact_validator(object_directory, manifest)
        return manifest

    def resolve(self, selector: str) -> ManifestType:
        """Resolve an exact ID first, then an exact unique label.

        :param selector: Artifact ID or label.
        :return: Parsed and validated manifest.
        """

        try:
            validate_id(selector)
        except ValidationError:
            pass
        else:
            if (self._root / selector).is_dir():
                return self.get(selector)

        matches = [
            manifest for manifest in self.list() if manifest.identity.label == selector
        ]
        if not matches:
            raise SelectorError(
                f"unknown {self._directory_name} ID or label: {selector!r}"
            )
        if len(matches) > 1:
            raise SelectorError(f"ambiguous {self._directory_name} label: {selector!r}")
        return matches[0]

    def list(self) -> tuple[ManifestType, ...]:
        """Load every object in deterministic ID order.

        :return: Validated manifests in ID order.
        """

        manifests: list[ManifestType] = []
        for entry in sorted(self._root.iterdir(), key=lambda path: path.name):
            if not entry.is_dir():
                raise DataFormatError(f"unexpected file in {self._root}: {entry.name}")
            try:
                manifests.append(self.get(entry.name))
            except ValidationError as error:
                raise DataFormatError(
                    f"invalid object directory name in {self._root}: {entry.name}"
                ) from error
        return tuple(manifests)

    def path_for(self, object_id: str) -> Path:
        """Return a validated existing object's directory.

        :param object_id: Canonical artifact ID.
        :return: Existing artifact directory.
        """

        self.get(object_id)
        return self._root / object_id

    def _require_unique_label(self, label: str | None) -> None:
        """Reject a label already used by this artifact type.

        :param label: New artifact label, if any.
        """

        if label is None:
            return
        matches = [
            manifest.identity.id
            for manifest in self.list()
            if manifest.identity.label == label
        ]
        if matches:
            raise ValidationError(
                f"{self._directory_name} label already exists: {label!r}"
            )

    def _validate_staging(self, staging: StagingArea) -> None:
        """Require a self-contained staging area in the work directory.

        :param staging: Staging area to validate.
        """

        expected_parent = self._layout.child(_WORK_DIRECTORY)
        if staging.work_directory.parent != expected_parent:
            raise ValidationError(
                "staging area is outside the configured work directory"
            )
        if staging.object_directory != staging.work_directory / _STAGING_DIRECTORY:
            raise ValidationError("invalid staging object directory")
        if any(path.is_symlink() for path in staging.object_directory.rglob("*")):
            raise ValidationError("staged object must not contain symlinks")


class CorpusRepository(ImmutableRepository[CorpusManifest]):
    """Local immutable corpus repository."""

    def __init__(self, layout: DataLayout) -> None:
        """Create a repository for corpus manifests and files.

        :param layout: Shared local data layout.
        """

        super().__init__(
            layout,
            _CORPORA_DIRECTORY,
            CorpusManifest.from_json,
            _validate_corpus_files,
        )


class ReplayRepository(ImmutableRepository[ReplayManifest]):
    """Local immutable replay repository."""

    def __init__(self, layout: DataLayout) -> None:
        """Create a repository for replay manifests and files.

        :param layout: Shared local data layout.
        """

        super().__init__(
            layout,
            _REPLAYS_DIRECTORY,
            ReplayManifest.from_json,
            _validate_replay_files,
        )


class ComparisonRepository(ImmutableRepository[ComparisonManifest]):
    """Local immutable comparison repository."""

    def __init__(self, layout: DataLayout) -> None:
        """Create a repository for comparison manifests and files.

        :param layout: Shared local data layout.
        """

        super().__init__(
            layout,
            _COMPARISONS_DIRECTORY,
            ComparisonManifest.from_json,
            _validate_comparison_files,
        )


class HotMethodWindowRepository:
    """Mutable per-suite/workload hot-window storage."""

    def __init__(self, layout: DataLayout) -> None:
        """Create storage for hot-method windows.

        :param layout: Shared local data layout.
        """

        layout.initialize()
        #: Root directory for hot-method window files.
        self._root = layout.child(_HOT_METHOD_WINDOWS_DIRECTORY)

    def get(self, suite_name: str, workload_name: str) -> HotMethodWindow | None:
        """Load a hot window when present.

        :param suite_name: Benchmark suite name.
        :param workload_name: Workload name within the suite.
        :return: Stored window, or ``None`` when absent.
        """

        path = self._path(suite_name, workload_name)
        if not path.exists():
            return None
        window = HotMethodWindow.from_json(read_json(path))
        if window.suite_name != suite_name or window.workload_name != workload_name:
            raise DataFormatError(
                f"hot-method window identity does not match its path: {path}"
            )
        return window

    def put(self, window: HotMethodWindow) -> Path:
        """Atomically replace one hot-method window.

        :param window: Window to store.
        :return: Stored JSON path.
        """

        path = self._path(window.suite_name, window.workload_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(path, window.as_json())
        return path

    def _path(self, suite_name: str, workload_name: str) -> Path:
        """Build the safe path for one workload window.

        :param suite_name: Benchmark suite name.
        :param workload_name: Workload name within the suite.
        :return: JSON path for the window.
        """

        _validate_path_component(suite_name, "suite name")
        _validate_path_component(workload_name, "workload name")
        return self._root / suite_name / f"{workload_name}.json"


def read_json(path: Path) -> object:
    """Read one UTF-8 JSON document.

    :param path: JSON file path.
    :return: Parsed JSON value.
    """

    try:
        with path.open("r", encoding="utf-8") as source:
            return json.load(source)
    except OSError as error:
        raise DataFormatError(f"cannot read JSON document {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise DataFormatError(f"invalid JSON document {path}: {error}") from error


def write_json_atomic(path: Path, value: JsonObject) -> None:
    """Write deterministic JSON through a flushed sibling file and atomic replace.

    :param path: Destination JSON path.
    :param value: JSON object to write.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(value, output, indent=2, sort_keys=True, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _validate_path_component(value: str, field: str) -> None:
    """Reject unsafe filesystem path components.

    :param value: Path component to validate.
    :param field: Field name used in errors.
    """

    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise ValidationError(f"{field} is not a safe path component: {value!r}")


def _validate_corpus_files(directory: Path, manifest: CorpusManifest) -> None:
    """Validate every file referenced by a corpus manifest.

    :param directory: Corpus artifact directory.
    :param manifest: Corpus manifest to validate.
    """

    for run in manifest.runs:
        _require_reference(directory, run.log, directory_expected=False)
        if run.replay_files is not None:
            _require_reference(directory, run.replay_files, directory_expected=True)


def _validate_replay_files(directory: Path, manifest: ReplayManifest) -> None:
    """Validate every file referenced by a replay manifest.

    :param directory: Replay artifact directory.
    :param manifest: Replay manifest to validate.
    """

    for run in manifest.runs:
        _require_reference(directory, run.log, directory_expected=False)
        if run.metrics is not None:
            metrics = _require_reference(
                directory, run.metrics, directory_expected=False
            )
            ReplayMetrics.from_json(read_json(metrics))


def _validate_comparison_files(directory: Path, manifest: ComparisonManifest) -> None:
    """Validate every file referenced by a comparison manifest.

    :param directory: Comparison artifact directory.
    :param manifest: Comparison manifest to validate.
    """

    _require_reference(directory, manifest.summary, directory_expected=False)
    _require_reference(directory, manifest.report, directory_expected=False)


def _require_reference(
    object_directory: Path, relative: str, *, directory_expected: bool
) -> Path:
    """Resolve an artifact reference and require the expected kind.

    :param object_directory: Root artifact directory.
    :param relative: Stored relative path.
    :param directory_expected: Whether the target must be a directory.
    :return: Validated referenced path.
    """

    path = object_directory / relative
    expected = "directory" if directory_expected else "file"
    valid = path.is_dir() if directory_expected else path.is_file()
    if not valid:
        raise DataFormatError(
            f"artifact reference is not an existing {expected}: {relative}"
        )
    return path
