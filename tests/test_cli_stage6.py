import json

import numpy as np
import pandas as pd
import yaml
from typer.testing import CliRunner

from abkit import storage
from abkit.experiment import Experiment
from cli import app

runner = CliRunner()


def _write_design_data(path, n=4000, seed=0):
    rng = np.random.default_rng(seed)
    data = pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(n)],
            "revenue": rng.normal(100, 20, size=n),
            "clicks": rng.binomial(1, 0.10, size=n),
        }
    )
    data.to_csv(path, index=False)
    return data


def _write_design_yaml(path, **overrides):
    config_dict = dict(
        name="cli_exp",
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[
            {"name": "revenue", "type": "continuous"},
            {"name": "clicks", "type": "binary"},
        ],
        sample_size=4000,
        split_method="simple",
        seed=1,
    )
    config_dict.update(overrides)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config_dict, f, allow_unicode=True)


def test_design_command_with_config_creates_experiment(tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path))
    data_path = tmp_path / "design_data.csv"
    config_path = tmp_path / "design.yaml"
    _write_design_data(data_path)
    _write_design_yaml(config_path)

    result = runner.invoke(app, ["design", "--config", str(config_path), "--data", str(data_path)])

    assert result.exit_code == 0, result.output
    assert "cli_exp" in result.output
    registry = storage.read_registry(tmp_path)
    assert "cli_exp" in registry
    assert (tmp_path / "cli_exp" / "config.yaml").exists()
    assert (tmp_path / "cli_exp" / "design_report.html").exists()


def test_design_command_requires_data_with_config(tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path))
    config_path = tmp_path / "design.yaml"
    _write_design_yaml(config_path)

    result = runner.invoke(app, ["design", "--config", str(config_path)])
    assert result.exit_code == 1
    assert "--data" in result.output


def test_design_command_missing_data_file_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path))
    config_path = tmp_path / "design.yaml"
    _write_design_yaml(config_path)

    result = runner.invoke(
        app, ["design", "--config", str(config_path), "--data", str(tmp_path / "missing.csv")]
    )
    assert result.exit_code == 1
    # rich переносит длинные строки по ширине терминала (в CI это уже иначе, чем
    # локально, — узкий/безtty вывод) и может перенести "не найден" через перевод
    # строки прямо между словами; схлопываем пробелы/переносы перед сравнением.
    assert "not found" in " ".join(result.output.split())


def test_design_command_invalid_config_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path))
    data_path = tmp_path / "design_data.csv"
    config_path = tmp_path / "design.yaml"
    _write_design_data(data_path)
    _write_design_yaml(config_path, groups={"control": 0.5, "treatment": 0.6})  # сумма != 1

    result = runner.invoke(app, ["design", "--config", str(config_path), "--data", str(data_path)])
    assert result.exit_code == 1
    assert "Error in config" in result.output


def _design_via_cli(tmp_path, monkeypatch, name="cli_exp"):
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path))
    data_path = tmp_path / "design_data.csv"
    config_path = tmp_path / "design.yaml"
    design_data = _write_design_data(data_path)
    _write_design_yaml(config_path, name=name)
    result = runner.invoke(app, ["design", "--config", str(config_path), "--data", str(data_path)])
    assert result.exit_code == 0, result.output
    return design_data


def test_analyze_command_runs_and_writes_report(tmp_path, monkeypatch):
    _design_via_cli(tmp_path, monkeypatch)
    experiment = Experiment.load("cli_exp", experiments_dir=tmp_path)
    assignments = experiment.assignments
    rng = np.random.default_rng(2)
    post_data = pd.DataFrame(
        {
            "user_id": assignments["unit_id"],
            "revenue": rng.normal(100, 20, size=len(assignments)),
            "clicks": rng.binomial(1, 0.1, size=len(assignments)),
        }
    )
    post_path = tmp_path / "post_data.csv"
    post_data.to_csv(post_path, index=False)

    result = runner.invoke(app, ["analyze", "cli_exp", "--data", str(post_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "cli_exp" / "report.html").exists()
    assert (tmp_path / "cli_exp" / "results.json").exists()


def test_analyze_command_missing_experiment_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path))
    data_path = tmp_path / "post.csv"
    pd.DataFrame({"user_id": ["u1"], "revenue": [1.0]}).to_csv(data_path, index=False)

    result = runner.invoke(app, ["analyze", "ghost_exp", "--data", str(data_path)])
    assert result.exit_code == 1
    assert "Error" in result.output


def test_validate_command_runs_aa_simulation(tmp_path, monkeypatch):
    design_data = _design_via_cli(tmp_path, monkeypatch)
    historical_path = tmp_path / "historical.csv"
    design_data.to_csv(historical_path, index=False)

    result = runner.invoke(
        app, ["validate", "cli_exp", "--data", str(historical_path), "--n-sims", "30"]
    )
    assert result.exit_code == 0, result.output
    assert "FPR" in result.output


def test_validate_command_with_effect_runs_ab_simulation(tmp_path, monkeypatch):
    design_data = _design_via_cli(tmp_path, monkeypatch)
    historical_path = tmp_path / "historical.csv"
    design_data.to_csv(historical_path, index=False)

    result = runner.invoke(
        app,
        [
            "validate", "cli_exp",
            "--data", str(historical_path),
            "--n-sims", "30",
            "--effect", "0.2",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "power" in result.output.lower()


def test_e2e_synthetic_design_analyze_detects_injected_effect(tmp_path, monkeypatch):
    """Критерий готовности этапа 6: синтетика -> design -> пост-данные с эффектом ->
    analyze (через CLI) -> эффект задетектирован -> отчет создан."""
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path))

    n = 6000
    rng = np.random.default_rng(42)
    design_data = pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(n)],
            "revenue": rng.normal(100, 20, size=n),
        }
    )
    data_path = tmp_path / "design_data.csv"
    design_data.to_csv(data_path, index=False)

    config_path = tmp_path / "design.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            dict(
                name="e2e_exp",
                unit_col="user_id",
                groups={"control": 0.5, "treatment": 0.5},
                metrics=[{"name": "revenue", "type": "continuous"}],
                sample_size=n,
                split_method="simple",
                seed=7,
            ),
            f,
            allow_unicode=True,
        )

    design_result = runner.invoke(app, ["design", "--config", str(config_path), "--data", str(data_path)])
    assert design_result.exit_code == 0, design_result.output

    experiment = Experiment.load("e2e_exp", experiments_dir=tmp_path)
    assignments = experiment.assignments
    is_treat = (assignments["group"] == "treatment").to_numpy()
    revenue = rng.normal(100, 20, size=len(assignments))
    revenue[is_treat] += 20.0  # заметный подсаженный эффект
    post_data = pd.DataFrame({"user_id": assignments["unit_id"], "revenue": revenue})
    post_path = tmp_path / "post_data.csv"
    post_data.to_csv(post_path, index=False)

    analyze_result = runner.invoke(app, ["analyze", "e2e_exp", "--data", str(post_path)])
    assert analyze_result.exit_code == 0, analyze_result.output

    report_path = tmp_path / "e2e_exp" / "report.html"
    results_json_path = tmp_path / "e2e_exp" / "results.json"
    assert report_path.exists()
    assert results_json_path.exists()

    payload = json.loads(results_json_path.read_text(encoding="utf-8"))
    revenue_results = [r for r in payload["results"] if r["metric"] == "revenue" and r["is_designed_method"]]
    assert len(revenue_results) == 1
    r = revenue_results[0]
    assert r["effect_abs"] > 0
    assert r["p_value"] < 0.001


def test_status_and_list_still_work_after_design(tmp_path, monkeypatch):
    _design_via_cli(tmp_path, monkeypatch)
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "cli_exp" in result.output

    result = runner.invoke(app, ["status", "cli_exp", "running"])
    assert result.exit_code == 0
    registry = storage.read_registry(tmp_path)
    assert registry["cli_exp"]["status"] == "running"
