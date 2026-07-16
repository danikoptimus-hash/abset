"""abkit — фреймворк для дизайна и анализа A/B тестов."""

import re
from pathlib import Path

from abkit.config import DesignConfig, MetricConfig
from abkit.experiment import DesignError, Experiment

__all__ = ["DesignConfig", "MetricConfig", "Experiment", "DesignError", "PRODUCT_NAME"]

# `git describe --tags --always --long` output, e.g. "v2.5.0-0-g1863360"
# (exact tag) or "v2.5.0-3-gabc1234" (3 commits past the tag). No "-N-g..."
# suffix at all (just a bare sha, or nothing) means no tag was reachable.
_DESCRIBE_RE = re.compile(r"^(?P<tag>.+)-(?P<distance>\d+)-g(?P<sha>[0-9a-f]+)$")


def _format_version(raw: str) -> str:
    """Item 8-Б (audit-details+ package, follow-up): on the tagged commit
    itself, distance is 0 -> plain "vX.Y.Z"; N commits past a tag ->
    "vX.Y.Z+N (sha)" (semver build-metadata-flavored, human-readable at a
    glance which release this is closest to and how far past it); no tag
    reachable at all -> "dev (sha)"."""
    raw = raw.strip()
    if not raw:
        return "dev"
    match = _DESCRIBE_RE.match(raw)
    if match is None:
        # `--always` fallback when no tag exists in history at all: a bare
        # abbreviated sha, no "-N-g" pattern to parse.
        return f"dev ({raw})"
    distance = int(match.group("distance"))
    if distance == 0:
        return match.group("tag")
    return f"{match.group('tag')}+{distance} ({match.group('sha')})"


def _read_version(*, describe_file: Path | None = None) -> str:
    """Single source of truth = `git describe --tags` against the build
    context's own .git (CLAUDE.md "Правило: релизный процесс") — computed
    ONCE, at image-build time, by docker/Dockerfile's `version` stage (never
    at runtime: no git binary or repo history ships in the final image).
    Works identically for a tagged CI release build and a local
    `docker compose up -d --build` with no explicit tag — describe naturally
    reports "how far past the last tag" either way, no separate build-arg
    mechanism needed for the DISPLAYED version (docker/Dockerfile's
    ABKIT_VERSION build-arg still exists, but only for the OCI image label/
    ghcr.io tag name — a different, adjacent concern from what a human sees
    in the app). A bare non-Docker run (pytest, local editable install) has
    no VERSION_DESCRIBE file at all — "dev" plain, since it's not what real
    users see (only the Docker-built About page/report header are).
    describe_file is injectable purely for tests/test_version.py."""
    describe_file = (
        describe_file if describe_file is not None
        else Path(__file__).resolve().parent.parent / "VERSION_DESCRIBE"
    )
    if describe_file.exists():
        raw = describe_file.read_text().strip()
        if raw:
            return _format_version(raw)
    return "dev"


__version__ = _read_version()

# Единый источник имени продукта (UX-пакет, ребрендинг) — README.md, HTML-отчеты
# (abkit/viz/report.py), CLI (cli.py/cli_admin.py --help), backend (Settings >
# About, FastAPI app title), frontend (frontend/src/branding.ts — TS не может
# импортировать Python, синхронизировать вручную при изменении). "abkit" (в
# нижнем регистре) остается техническим идентификатором пакета/репозитория/
# путей — не переименовывается.
PRODUCT_NAME = "ABSet"
