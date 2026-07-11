from typer.testing import CliRunner

from cli import app

runner = CliRunner()


def test_list_command_runs_on_empty_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path))
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "ABSet experiments" in result.stdout


def test_design_command_is_stub():
    result = runner.invoke(app, ["design"])
    assert result.exit_code == 1


def test_status_command_updates_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path))
    from abkit import storage

    path = storage.experiment_path(tmp_path, "exp1")
    storage.register_experiment(tmp_path, "exp1", path)

    result = runner.invoke(app, ["status", "exp1", "running"])
    assert result.exit_code == 0
    registry = storage.read_registry(tmp_path)
    assert registry["exp1"]["status"] == "running"


def test_status_command_invalid_transition_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path))
    from abkit import storage

    path = storage.experiment_path(tmp_path, "exp1")
    storage.register_experiment(tmp_path, "exp1", path)

    result = runner.invoke(app, ["status", "exp1", "completed"])
    assert result.exit_code == 1
