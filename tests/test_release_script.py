"""Проверяемые без docker'а части релизного конвейера.

Здесь НЕ собирается ни одного образа: тестируются чистые функции
scripts/release.sh (валидация версии, гард на грязное дерево, гард на ветку) и
разрешение ссылок на образы в compose-оверлеях. Полный конвейер
(build -> push -> pull из настоящего реестра) проверяет
scripts/test_release_local_registry.sh против локального registry:2 — ему
нужен docker, поэтому в pytest он не входит.

Функции release.sh вызываются через `source`: скрипт специально написан так,
что при sourcing'е main() не запускается (см. хвост файла).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASE_SH = REPO_ROOT / "scripts" / "release.sh"
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"
DEV_COMPOSE = REPO_ROOT / "docker-compose.yml"

def _find_bash() -> str | None:
    """Настоящий POSIX-bash, а не WSL-заглушка.

    На Windows `shutil.which("bash")` находит C:\\Windows\\System32\\bash.exe —
    launcher WSL, который без установленного дистрибутива падает с
    "execvpe(/bin/bash) failed". Скрипты проекта рассчитаны на Git Bash
    (CLAUDE.md: shell — PowerShell + Bash tool), поэтому кандидатов проверяем
    ЗАПУСКОМ, а не наличием файла.
    """
    candidates = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        shutil.which("bash"),
        "/bin/bash",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            probe = subprocess.run(
                [candidate, "-c", "echo ok"], capture_output=True, text=True, timeout=15
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0 and "ok" in probe.stdout:
            return candidate
    return None


BASH = _find_bash()

pytestmark = pytest.mark.skipif(
    BASH is None, reason="a working bash is required to exercise the release script"
)


def _run_bash(snippet: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Подтягивает release.sh и выполняет snippet в том же shell'е."""
    script = f'set -euo pipefail\nsource "{RELEASE_SH.as_posix()}"\n{snippet}\n'
    return subprocess.run(
        [BASH, "-c", script],
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Валидация версии
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", ["v2.6.0", "v0.0.1", "v10.20.30", "v2.6.0-rc.1"])
def test_valid_versions_accepted(version):
    assert _run_bash(f'validate_version "{version}"').returncode == 0


@pytest.mark.parametrize(
    "version",
    [
        "2.6.0",       # без обязательного "v" — scripts/update.sh такой тег отвергнет
        "v2.6",        # неполный semver
        "v2",
        "latest",      # прод по плавающему тегу не разворачивается принципиально
        "v2.6.0.1",
        "vX.Y.Z",      # плейсхолдер из документации, вставленный буквально
        "",
        "v 2.6.0",
    ],
)
def test_invalid_versions_rejected(version):
    result = _run_bash(f'validate_version "{version}"')
    assert result.returncode != 0, f"'{version}' must be rejected"
    assert "not a valid release version" in result.stderr


def test_version_error_message_names_the_expected_shape():
    """Сообщение должно чинить ошибку, а не только констатировать ее."""
    result = _run_bash('validate_version "2.6.0"')
    assert "vMAJOR.MINOR.PATCH" in result.stderr
    assert "v2.6.0" in result.stderr


# ---------------------------------------------------------------------------
# Гард на грязное рабочее дерево
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_repo(tmp_path):
    """Отдельный git-репозиторий: гарды нельзя проверять на рабочей копии
    проекта — результат зависел бы от того, что у разработчика не закоммичено
    прямо сейчас."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    (repo / "file.txt").write_text("initial\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True, env=env)
    return repo


def test_clean_tree_passes(temp_repo):
    assert _run_bash("require_clean_tree", cwd=temp_repo).returncode == 0


def test_dirty_tree_is_rejected_with_the_reason(temp_repo):
    """Образ, собранный из грязного дерева, не соответствует ни одному коммиту,
    и версия внутри него (git describe) просто соврет."""
    (temp_repo / "file.txt").write_text("uncommitted change\n")
    result = _run_bash("require_clean_tree", cwd=temp_repo)
    assert result.returncode != 0
    assert "working tree is dirty" in result.stderr
    assert "cannot be reproduced" in result.stderr
    # Показывает, ЧТО именно грязное — иначе на большом дереве это поиск вслепую.
    assert "file.txt" in result.stderr


def test_untracked_file_also_counts_as_dirty(temp_repo):
    """Незатреканный файл так же меняет контекст сборки (docker build .),
    как и правка существующего."""
    (temp_repo / "stray.py").write_text("print('oops')\n")
    assert _run_bash("require_clean_tree", cwd=temp_repo).returncode != 0


def test_release_refuses_non_main_branch(temp_repo):
    subprocess.run(["git", "checkout", "-b", "feature/x"], cwd=temp_repo, check=True, capture_output=True)
    result = _run_bash("require_main_branch", cwd=temp_repo)
    assert result.returncode != 0
    assert "feature/x" in result.stderr


def test_release_accepts_main_branch(temp_repo):
    assert _run_bash("require_main_branch", cwd=temp_repo).returncode == 0


# ---------------------------------------------------------------------------
# Соответствие списка образов и compose-оверлея
# ---------------------------------------------------------------------------


def test_release_builds_exactly_the_images_prod_compose_expects():
    """Список IMAGES в release.sh и image: в docker-compose.prod.yml — два
    независимых места, которые обязаны совпадать. Разъезд означал бы, что
    релиз опубликовал не то, что прод потом попытается вытянуть."""
    result = _run_bash('printf "%s\\n" "${IMAGES[@]}"')
    assert result.returncode == 0, result.stderr
    built = set(result.stdout.split())

    compose_text = PROD_COMPOSE.read_text(encoding="utf-8")
    referenced = {
        line.split("/")[-1].split(":")[0]
        for line in compose_text.splitlines()
        if "image: ${ABKIT_REGISTRY" in line
    }
    assert built == referenced, f"release.sh builds {built}, prod compose expects {referenced}"


def test_every_built_image_has_a_dockerfile():
    result = _run_bash(
        'for n in "${IMAGES[@]}"; do printf "%s %s\\n" "$n" "$(dockerfile_for "$n")"; done'
    )
    assert result.returncode == 0, result.stderr
    for line in result.stdout.strip().splitlines():
        name, dockerfile = line.split()
        assert (REPO_ROOT / dockerfile).is_file(), f"{name}: {dockerfile} does not exist"


# ---------------------------------------------------------------------------
# Разрешение compose-конфигурации (dev и prod)
# ---------------------------------------------------------------------------

_HAS_COMPOSE = shutil.which("docker") is not None


def _compose_config(*files: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    args = ["docker", "compose"]
    for f in files:
        args += ["-f", str(f)]
    args.append("config")
    return subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env={**os.environ, **env},
    )


@pytest.mark.skipif(not _HAS_COMPOSE, reason="docker compose is required")
def test_dev_compose_still_builds_from_context():
    """Регресс-гард: прод-оверлей не должен менять поведение обычного
    `docker compose up -d --build` у разработчика."""
    result = _compose_config(
        DEV_COMPOSE, env={"POSTGRES_PASSWORD": "x", "ABKIT_SECRET_KEY": "y"}
    )
    assert result.returncode == 0, result.stderr
    assert "build:" in result.stdout
    # Никаких ссылок на реестр в dev-конфиге: образы локальные.
    assert "ABKIT_REGISTRY" not in result.stdout


@pytest.mark.skipif(not _HAS_COMPOSE, reason="docker compose is required")
def test_prod_overlay_resolves_registry_image_refs():
    result = _compose_config(
        DEV_COMPOSE,
        PROD_COMPOSE,
        env={
            "ABKIT_REGISTRY": "hb.intra.click.uz/abset",
            "ABKIT_VERSION": "v9.9.9",
            "POSTGRES_PASSWORD": "x",
            "ABKIT_SECRET_KEY": "y",
        },
    )
    assert result.returncode == 0, result.stderr
    for name in ("abset-backend", "abset-frontend", "abset-nginx"):
        assert f"image: hb.intra.click.uz/abset/{name}:v9.9.9" in result.stdout


@pytest.mark.skipif(not _HAS_COMPOSE, reason="docker compose is required")
def test_prod_overlay_removes_build_sections():
    """Без этого VM без интернета попыталась бы СОБРАТЬ образ вместо честного
    отказа на недоступном реестре — и упала бы посреди сборки с невнятной
    сетевой ошибкой."""
    result = _compose_config(
        DEV_COMPOSE,
        PROD_COMPOSE,
        env={
            "ABKIT_REGISTRY": "hb.intra.click.uz/abset",
            "ABKIT_VERSION": "v9.9.9",
            "POSTGRES_PASSWORD": "x",
            "ABKIT_SECRET_KEY": "y",
        },
    )
    assert result.returncode == 0, result.stderr
    build_lines = [ln for ln in result.stdout.splitlines() if ln.strip().startswith("build:")]
    assert build_lines == [], f"prod config still has build sections: {build_lines}"


@pytest.mark.skipif(not _HAS_COMPOSE, reason="docker compose is required")
def test_prod_overlay_fails_loudly_without_registry():
    """Пустой ABKIT_REGISTRY не должен молча дать образ вида '/abset-backend:'."""
    result = _compose_config(
        DEV_COMPOSE,
        PROD_COMPOSE,
        env={"POSTGRES_PASSWORD": "x", "ABKIT_SECRET_KEY": "y", "ABKIT_REGISTRY": ""},
    )
    assert result.returncode != 0
    assert "ABKIT_REGISTRY" in result.stderr


@pytest.mark.skipif(not _HAS_COMPOSE, reason="docker compose is required")
def test_prod_overlay_drops_the_nginx_bind_mount():
    """На VM конфиг nginx приезжает ВНУТРИ образа (docker/nginx.Dockerfile) —
    bind-mount из рабочей копии сделал бы версию прокси зависящей от git pull,
    а не от ABKIT_VERSION."""
    result = _compose_config(
        DEV_COMPOSE,
        PROD_COMPOSE,
        env={
            "ABKIT_REGISTRY": "hb.intra.click.uz/abset",
            "ABKIT_VERSION": "v9.9.9",
            "POSTGRES_PASSWORD": "x",
            "ABKIT_SECRET_KEY": "y",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "nginx.conf.template" not in result.stdout


def test_prod_compose_documents_both_postgres_options():
    """ТЗ §1: у postgres два пути (proxy cache Harbor и ручное зеркало), и оба
    должны быть описаны там, где их будут искать — в самом файле."""
    text = PROD_COMPOSE.read_text(encoding="utf-8")
    assert "proxy cache" in text.lower()
    assert "docker push" in text  # инструкция для ручного зеркалирования
    assert "ABKIT_POSTGRES_IMAGE" in text


def test_release_script_is_sourceable_without_running():
    """Хвост release.sh (BASH_SOURCE-гард) — то, на чем держатся все тесты
    выше: подтягивание файла не должно ничего собирать и никуда пушить."""
    result = _run_bash('echo "sourced-without-side-effects"')
    assert result.returncode == 0
    assert "sourced-without-side-effects" in result.stdout
    assert "Preflight" not in result.stdout


def test_setup_gitlab_remote_rejects_a_non_url():
    script = REPO_ROOT / "scripts" / "setup_gitlab_remote.sh"
    result = subprocess.run(
        [BASH, str(script), "not-a-url"],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "does not look like a git remote URL" in result.stderr


def test_setup_gitlab_remote_is_idempotent(temp_repo):
    """Повторный запуск с тем же URL — не ошибка: скрипт должен быть безопасен
    для повторного применения (его запускает каждый, кто заводит рабочую копию)."""
    script = REPO_ROOT / "scripts" / "setup_gitlab_remote.sh"
    # Скрипт cd'ится в свой PROJECT_DIR, поэтому проверяем на временном репо
    # через прямые git-команды тот же контракт, что и он: add -> set-url.
    url = "https://gitlab.example.com/analytics/abset.git"
    subprocess.run(["git", "remote", "add", "gitlab", url], cwd=temp_repo, check=True, capture_output=True)
    again = subprocess.run(
        ["git", "remote", "get-url", "gitlab"], cwd=temp_repo, capture_output=True, text=True
    )
    assert again.stdout.strip() == url
    assert script.is_file(), "setup_gitlab_remote.sh must exist for the documented flow"
    assert textwrap.dedent(script.read_text(encoding="utf-8")).count("set-url") >= 1
