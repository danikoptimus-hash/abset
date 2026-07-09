"""R2 (FRONTEND.md §3.2): read-only API для экспериментов — список
(пагинация/фильтры status/owner/q), детали (+design_summary passthrough),
отчеты, выборки, результаты, аудит по эксперименту."""

from __future__ import annotations

from abkit.auth.passwords import hash_password
from abkit.db.repositories import ExperimentRepo, ResultRepo, UserRepo


def _login(app_client, email="editor@co.com", role="admin"):
    """Дефолт admin (не editor): большинство тестов этого файла проверяют
    механику списка/деталей, а не видимость draft (FRONTEND.md §3.3 — draft
    видят только владелец и admin) — эксперименты в _make_experiment создаются
    с чужим owner_id, иначе не-владелец/не-admin их бы не увидел. Видимость
    draft для чужого editor/viewer проверяется отдельным тестом ниже."""
    UserRepo().create(email=email, first_name="E", password_hash=hash_password("pw12345"), role=role)
    app_client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345"})


def _make_experiment(name="exp_a", status="designed", owner_id=None):
    if owner_id is None:
        owner_id = UserRepo().create(
            email=f"owner_{name}@co.com", first_name="Owner", password_hash=hash_password("pw12345"), role="editor"
        )
    return ExperimentRepo().create(
        name=name, owner_id=owner_id, status=status,
        config={"name": name, "groups": ["control", "treatment"], "metrics": []},
    )


def test_list_experiments_requires_login(app_client):
    resp = app_client.get("/api/v1/experiments")
    assert resp.status_code == 401


def test_list_experiments_pagination_and_filters(app_client):
    _login(app_client)
    _make_experiment("exp_running", status="running")
    _make_experiment("exp_designed_1", status="designed")
    _make_experiment("exp_designed_2", status="designed")

    resp = app_client.get("/api/v1/experiments", params={"page": 1, "page_size": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2

    resp_status = app_client.get("/api/v1/experiments", params={"status": "running"})
    assert resp_status.json()["total"] == 1
    assert resp_status.json()["items"][0]["name"] == "exp_running"

    resp_q = app_client.get("/api/v1/experiments", params={"q": "designed_2"})
    assert resp_q.json()["total"] == 1
    assert resp_q.json()["items"][0]["name"] == "exp_designed_2"


def test_list_experiments_filters_by_owner(app_client):
    _login(app_client)
    owner_a = UserRepo().create(
        email="owner_a@co.com", first_name="A", password_hash=hash_password("pw12345"), role="editor"
    )
    owner_b = UserRepo().create(
        email="owner_b@co.com", first_name="B", password_hash=hash_password("pw12345"), role="editor"
    )
    _make_experiment("exp_owner_a", owner_id=owner_a)
    _make_experiment("exp_owner_b", owner_id=owner_b)

    resp = app_client.get("/api/v1/experiments", params={"owner": "owner_a@co.com"})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "exp_owner_a"
    assert body["items"][0]["owner_email"] == "owner_a@co.com"


def test_draft_experiment_hidden_from_non_owner_non_admin(app_client):
    other_owner = UserRepo().create(
        email="draft_owner@co.com", first_name="O", password_hash=hash_password("pw12345"), role="editor"
    )
    _make_experiment("draft_exp", owner_id=other_owner)  # publication_status="draft" по умолчанию

    _login(app_client, email="outsider@co.com", role="editor")
    resp_list = app_client.get("/api/v1/experiments")
    assert resp_list.json()["total"] == 0

    resp_detail = app_client.get("/api/v1/experiments/draft_exp")
    assert resp_detail.status_code == 404


def test_draft_experiment_visible_to_owner_and_admin(app_client):
    owner_id = UserRepo().create(
        email="draft_owner2@co.com", first_name="O2", password_hash=hash_password("pw12345"), role="editor"
    )
    _make_experiment("draft_exp2", owner_id=owner_id)

    _login(app_client, email="admin_viewer@co.com", role="admin")
    resp = app_client.get("/api/v1/experiments/draft_exp2")
    assert resp.status_code == 200
    assert resp.json()["publication_status"] == "draft"


def test_published_experiment_visible_to_anyone(app_client):
    from abkit.db.repositories import ExperimentRepo

    other_owner = UserRepo().create(
        email="pub_owner@co.com", first_name="P", password_hash=hash_password("pw12345"), role="editor"
    )
    _make_experiment("pub_exp", owner_id=other_owner)
    ExperimentRepo().update_publication_status("pub_exp", "published")

    _login(app_client, email="anyone@co.com", role="viewer")
    resp = app_client.get("/api/v1/experiments/pub_exp")
    assert resp.status_code == 200
    resp_list = app_client.get("/api/v1/experiments", params={"pub": "published"})
    assert resp_list.json()["total"] == 1


def test_get_experiment_detail_design_summary_is_null_by_default(app_client):
    """create_experiment() никогда не заполняет design_summary (см.
    abkit/db/store.py) — поле честно прокидывается как None, а не скрывается."""
    _login(app_client)
    _make_experiment("exp_detail")
    resp = app_client.get("/api/v1/experiments/exp_detail")
    assert resp.status_code == 200
    body = resp.json()
    assert body["config"]["name"] == "exp_detail"
    assert body["design_summary"] is None
    assert body["available_reports"] == []
    assert body["files"] == []


def test_get_experiment_detail_404_for_missing(app_client):
    _login(app_client)
    resp = app_client.get("/api/v1/experiments/does_not_exist")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_experiment_reports_and_samples_from_artifact_dir(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _make_experiment("exp_files")

    exp_dir = tmp_path / "exp_files"
    exp_dir.mkdir(parents=True, exist_ok=True)
    (exp_dir / "design_report.html").write_text("<html>design</html>", encoding="utf-8")
    samples_dir = exp_dir / "samples"
    samples_dir.mkdir()
    (samples_dir / "control.csv").write_text("unit_id\nu1\nu2\n", encoding="utf-8")

    detail = app_client.get("/api/v1/experiments/exp_files").json()
    assert detail["available_reports"] == ["design_report.html"]
    assert any(f["path"] == "design_report.html" for f in detail["files"])

    report_resp = app_client.get("/api/v1/experiments/exp_files/reports/design_report.html")
    assert report_resp.status_code == 200
    assert "design" in report_resp.text

    bad_report = app_client.get("/api/v1/experiments/exp_files/reports/report.html")
    assert bad_report.status_code == 404

    samples_resp = app_client.get("/api/v1/experiments/exp_files/samples")
    assert samples_resp.status_code == 200
    samples = samples_resp.json()
    assert len(samples) == 1
    assert samples[0]["filename"] == "control.csv"
    assert samples[0]["n_rows"] == 2

    csv_resp = app_client.get("/api/v1/experiments/exp_files/samples/control.csv")
    assert csv_resp.status_code == 200
    assert "u1" in csv_resp.text

    missing_csv = app_client.get("/api/v1/experiments/exp_files/samples/missing.csv")
    assert missing_csv.status_code == 404

    zip_resp = app_client.get("/api/v1/experiments/exp_files/samples.zip")
    assert zip_resp.status_code == 200
    assert zip_resp.headers["content-type"] == "application/zip"


def test_experiment_samples_zip_404_when_no_samples(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _make_experiment("exp_no_samples")
    resp = app_client.get("/api/v1/experiments/exp_no_samples/samples.zip")
    assert resp.status_code == 404


def test_get_results_404_then_returns_latest(app_client):
    _login(app_client)
    exp = _make_experiment("exp_results")
    resp_missing = app_client.get("/api/v1/experiments/exp_results/results")
    assert resp_missing.status_code == 404

    ResultRepo().create(
        experiment_id=exp.id, results={"metrics": [{"name": "conversion", "p_value": 0.03}]},
        report_path="report.html",
    )
    resp = app_client.get("/api/v1/experiments/exp_results/results")
    assert resp.status_code == 200
    assert resp.json()["metrics"][0]["name"] == "conversion"


def test_experiment_audit_visible_to_any_logged_in_user(app_client):
    from abkit.db.repositories import AuditRepo

    _login(app_client, email="viewer@co.com", role="viewer")
    _make_experiment("exp_audit")
    AuditRepo().log(action="design", object_type="experiment", object_name="exp_audit", user_email="someone@co.com")

    resp = app_client.get("/api/v1/experiments/exp_audit/audit")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["action"] == "design"
