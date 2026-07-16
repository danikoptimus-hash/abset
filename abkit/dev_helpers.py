"""Helper for MANUAL debugging on a live/shared stack (CLAUDE.md, "Правило:
гигиена dev-артефактов") — NOT for pytest/Playwright, which have their own
isolated/disposable environments (see scripts/e2e.sh, conftest.py). Any
entity created by hand while poking at a live stack (e.g. via `docker compose
exec backend python`) should go through DevSession, not a direct
jobs/repo call: names get the `_dev_` prefix enforced automatically — it
can't be forgotten — and everything created is tracked, so `teardown()`
removes all of it in one call instead of relying on remembering each name.
A direct jobs/repo call in an ad-hoc debugging script is a mistake, not a
shortcut — cleanup-dev (`abkit/jobs.py::run_cleanup_dev`) is the safety net
for exactly the entities this helper would have prevented needing it for.

Usage::

    from abkit.dev_helpers import DevSession

    with DevSession() as dev:
        experiment = dev.design(current_user, config, data)  # name -> _dev_<name>
        dataset_id = dev.dataset(filename="probe.csv", ...)  # -> _dev_probe.csv
        conn_id = dev.connection(current_user, display_name="probe", ...)  # -> _dev_probe
        tag = dev.tag(current_user, "probe")                # -> _dev_probe
        folder = dev.folder(current_user, "probe")          # -> _dev_probe
        ...
    # teardown() already ran on __exit__ — everything above is gone.

Or call `dev = DevSession()` / `dev.teardown()` manually if a `with` block
doesn't fit the debugging session's shape.
"""

from __future__ import annotations

import uuid as uuid_mod
from dataclasses import dataclass, field
from typing import Any

_DEV_PREFIX = "_dev_"


def ensure_dev_prefix(name: str) -> str:
    return name if name.startswith(_DEV_PREFIX) else f"{_DEV_PREFIX}{name}"


@dataclass
class DevSession:
    _experiment_names: list[str] = field(default_factory=list)
    _dataset_ids: list[uuid_mod.UUID] = field(default_factory=list)
    _connection_ids: list[uuid_mod.UUID] = field(default_factory=list)
    # Item A3 (DB bloat package): tags/folders didn't exist yet when this
    # helper was first written — closing that gap, not new scope. Same
    # forced-prefix + tracked-for-teardown pattern as design/dataset/
    # connection above.
    _tag_ids: list[uuid_mod.UUID] = field(default_factory=list)
    _folder_ids: list[uuid_mod.UUID] = field(default_factory=list)

    def design(self, current_user, config, data: Any, **kwargs: Any):
        """Wraps jobs.run_design — config.name gets the _dev_ prefix forced
        on before the experiment is created."""
        from abkit import jobs

        forced_name = ensure_dev_prefix(config.name)
        if forced_name != config.name:
            config = config.model_copy(update={"name": forced_name})
        experiment = jobs.run_design(current_user, config, data, **kwargs)
        self._experiment_names.append(forced_name)
        return experiment

    def dataset(self, *, filename: str, **create_kwargs: Any) -> uuid_mod.UUID:
        """Wraps DatasetRepo.create — filename gets the _dev_ prefix forced on."""
        from abkit.db.repositories import DatasetRepo

        filename = ensure_dev_prefix(filename)
        dataset_id = DatasetRepo().create(filename=filename, **create_kwargs)
        self._dataset_ids.append(dataset_id)
        return dataset_id

    def connection(self, current_user, *, display_name: str, **kwargs: Any) -> uuid_mod.UUID:
        """Wraps db_connections.service.create_connection — display_name gets
        the _dev_ prefix forced on."""
        from abkit.db_connections.service import create_connection

        display_name = ensure_dev_prefix(display_name)
        conn = create_connection(current_user, display_name=display_name, **kwargs)
        self._connection_ids.append(conn.id)
        return conn.id

    def tag(self, current_user, name: str):
        """Wraps jobs.run_create_tag — name gets the _dev_ prefix forced on.
        Note: tag creation is get-or-create (case-insensitive) — if a tag
        with this exact _dev_-prefixed name already exists, teardown()
        removes that SAME shared tag, same trade-off the real /tags endpoint
        already accepts (CLAUDE.md, tags section)."""
        from abkit import jobs

        name = ensure_dev_prefix(name)
        tag = jobs.run_create_tag(current_user, name)
        self._tag_ids.append(tag.id)
        return tag

    def folder(self, current_user, name: str):
        """Wraps jobs.run_create_folder — name gets the _dev_ prefix forced
        on."""
        from abkit import jobs

        name = ensure_dev_prefix(name)
        folder = jobs.run_create_folder(current_user, name)
        self._folder_ids.append(folder.id)
        return folder

    def teardown(self) -> dict[str, int]:
        """Removes everything created through this session, via the same
        proper service/repo functions the rest of the app uses (not raw
        SQL) — safe to call multiple times (already-removed entities are
        skipped, not errored on)."""
        import shutil
        from pathlib import Path

        from abkit.db.repositories import DatabaseConnectionRepo, DatasetRepo, ExperimentRepo, FolderRepo, TagRepo
        from abkit.db.store import DbExperimentStore

        removed = {"experiments": 0, "datasets": 0, "connections": 0, "tags": 0, "folders": 0}

        for name in self._experiment_names:
            if ExperimentRepo().get_by_name(name) is not None:
                ExperimentRepo().delete(name)
                artifact_dir = DbExperimentStore().data_dir / name
                if artifact_dir.exists():
                    shutil.rmtree(artifact_dir)
                removed["experiments"] += 1
        self._experiment_names.clear()

        for dataset_id in self._dataset_ids:
            ds = DatasetRepo().get_by_id(dataset_id)
            if ds is not None:
                DatasetRepo().delete(dataset_id)
                path = Path(ds.storage_path)
                if path.exists():
                    path.unlink()
                removed["datasets"] += 1
        self._dataset_ids.clear()

        for conn_id in self._connection_ids:
            if DatabaseConnectionRepo().get_by_id(conn_id) is not None:
                DatabaseConnectionRepo().delete(conn_id)
                removed["connections"] += 1
        self._connection_ids.clear()

        for tag_id in self._tag_ids:
            if TagRepo().get_by_id(tag_id) is not None:
                TagRepo().delete(tag_id)
                removed["tags"] += 1
        self._tag_ids.clear()

        for folder_id in self._folder_ids:
            if FolderRepo().get_by_id(folder_id) is not None:
                FolderRepo().delete(folder_id)
                removed["folders"] += 1
        self._folder_ids.clear()

        return removed

    def __enter__(self) -> "DevSession":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        self.teardown()
        return False
