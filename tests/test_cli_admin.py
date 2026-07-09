"""cli_admin.py (abkit-admin entrypoint) — DOCKER.md §4.3/§9. Smoke-тесты через
typer.testing.CliRunner — реальная БД (db_url), команды сами по себе доверенные
(не требуют залогиненного пользователя, см. abkit/auth/service.py)."""

import numpy as np
import pandas as pd
from typer.testing import CliRunner

from cli_admin import app

runner = CliRunner()


def _env(db_url, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path / "data"))


def test_create_admin_then_list_users(db_url, tmp_path, monkeypatch):
    _env(db_url, tmp_path, monkeypatch)

    result = runner.invoke(app, ["create-admin", "--email", "admin@co.com", "--password", "pw12345"])
    assert result.exit_code == 0
    assert "admin@co.com" in result.stdout

    result = runner.invoke(app, ["list-users"])
    assert result.exit_code == 0
    assert "admin@co.com" in result.stdout


def test_create_admin_without_password_prints_generated_one(db_url, tmp_path, monkeypatch):
    _env(db_url, tmp_path, monkeypatch)

    result = runner.invoke(app, ["create-admin", "--email", "gen@co.com"])
    assert result.exit_code == 0
    assert "Temporary password" in result.stdout


def test_create_user_rejects_unknown_role(db_url, tmp_path, monkeypatch):
    _env(db_url, tmp_path, monkeypatch)

    result = runner.invoke(app, ["create-user", "--email", "u@co.com", "--role", "superuser"])
    assert result.exit_code == 1
    assert "unknown role" in result.stdout


def test_create_user_then_reset_password(db_url, tmp_path, monkeypatch):
    _env(db_url, tmp_path, monkeypatch)

    result = runner.invoke(
        app, ["create-user", "--email", "editor@co.com", "--role", "editor", "--password", "initialpw"]
    )
    assert result.exit_code == 0

    result = runner.invoke(app, ["reset-password", "--email", "editor@co.com"])
    assert result.exit_code == 0
    assert "Password reset" in result.stdout
    assert "Temporary password" in result.stdout


def test_reset_password_unknown_user_fails(db_url, tmp_path, monkeypatch):
    _env(db_url, tmp_path, monkeypatch)

    result = runner.invoke(app, ["reset-password", "--email", "nobody@co.com"])
    assert result.exit_code == 1


def test_import_legacy_via_cli(db_url, tmp_path, monkeypatch):
    _env(db_url, tmp_path, monkeypatch)

    from abkit.config import DesignConfig, MetricConfig
    from abkit.experiment import Experiment

    legacy_dir = tmp_path / "legacy"
    rng = np.random.default_rng(0)
    n = 100
    data = pd.DataFrame(
        {"user_id": [f"u{i}" for i in range(n)], "revenue": rng.normal(100, 20, size=n)}
    )
    config = DesignConfig(
        name="cli_import_exp",
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="revenue", type="continuous")],
        sample_size=n,
        split_method="simple",
        seed=1,
    )
    Experiment.design(config, data, experiments_dir=legacy_dir)

    runner.invoke(app, ["create-admin", "--email", "importer@co.com", "--password", "pw12345"])

    result = runner.invoke(
        app, ["import-legacy", "--dir", str(legacy_dir), "--owner", "importer@co.com"]
    )
    assert result.exit_code == 0
    assert "cli_import_exp" in result.stdout
    assert "Imported" in result.stdout

    # повторный запуск — идемпотентно, без ошибок
    result2 = runner.invoke(
        app, ["import-legacy", "--dir", str(legacy_dir), "--owner", "importer@co.com"]
    )
    assert result2.exit_code == 0
    assert "skipped" in result2.stdout


def test_import_legacy_unknown_owner_fails(db_url, tmp_path, monkeypatch):
    _env(db_url, tmp_path, monkeypatch)

    empty_dir = tmp_path / "empty_legacy"
    empty_dir.mkdir()

    result = runner.invoke(
        app, ["import-legacy", "--dir", str(empty_dir), "--owner", "nobody@co.com"]
    )
    assert result.exit_code == 1
    assert "not found" in result.stdout
