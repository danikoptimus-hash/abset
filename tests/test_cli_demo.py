from typer.testing import CliRunner

from abkit import storage
from cli import app

runner = CliRunner()


def test_demo_runs_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path))
    result = runner.invoke(app, ["demo", "--n", "1000"])

    assert result.exit_code == 0, result.output
    assert "Done!" in result.output
    assert "Verdict for revenue" in result.output

    registry = storage.read_registry(tmp_path)
    assert "demo" in registry
    exp_path = tmp_path / "demo"
    assert (exp_path / "design_report.html").exists()
    assert (exp_path / "report.html").exists()
    assert (exp_path / "results.json").exists()


def test_demo_avoids_name_collision_on_repeat_run(tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path))
    first = runner.invoke(app, ["demo", "--n", "500"])
    assert first.exit_code == 0, first.output

    second = runner.invoke(app, ["demo", "--n", "500"])
    assert second.exit_code == 0, second.output

    registry = storage.read_registry(tmp_path)
    assert "demo" in registry
    assert "demo_2" in registry


def test_demo_detects_injected_effect(tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path))
    result = runner.invoke(app, ["demo", "--n", "4000", "--effect", "0.15"])
    assert result.exit_code == 0, result.output
    assert "significant_positive" in result.output
