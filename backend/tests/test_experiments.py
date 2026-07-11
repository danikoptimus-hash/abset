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


def test_get_experiment_last_modified_is_null_when_never_edited(app_client):
    """_make_experiment() creates the row directly via the repo (not through
    a job), so there's no audit_log entry and no block edit — the header's
    "Last modified by" has nothing to show yet."""
    _login(app_client)
    _make_experiment("exp_never_edited")
    body = app_client.get("/api/v1/experiments/exp_never_edited").json()
    assert body["last_modified_at"] is None
    assert body["last_modified_by_email"] is None


def test_get_experiment_last_modified_reflects_latest_status_change(app_client):
    owner_id = UserRepo().create(
        email="owner_lastmod@co.com", first_name="Last", last_name="Mod", password_hash=hash_password("pw12345"), role="editor"
    )
    _make_experiment("exp_lastmod_status", owner_id=owner_id)

    app_client.post("/api/v1/auth/login", json={"email": "owner_lastmod@co.com", "password": "pw12345"})
    resp = app_client.post("/api/v1/experiments/exp_lastmod_status/status", json={"to": "running"})
    assert resp.status_code == 200

    body = app_client.get("/api/v1/experiments/exp_lastmod_status").json()
    assert body["last_modified_at"] is not None
    assert body["last_modified_by_email"] == "owner_lastmod@co.com"
    assert body["last_modified_by_first_name"] == "Last"


def test_get_experiment_last_modified_reflects_block_edit_when_more_recent(app_client):
    """A block edit alone (no status/publication/rename/properties change)
    still updates "Last modified by" — blocks aren't in audit_log, only
    experiment_blocks.updated_at/updated_by track it."""
    owner_id = UserRepo().create(
        email="owner_blockmod@co.com", first_name="Block", last_name="Editor", password_hash=hash_password("pw12345"), role="editor"
    )
    _make_experiment("exp_lastmod_blocks", owner_id=owner_id)

    app_client.post("/api/v1/auth/login", json={"email": "owner_blockmod@co.com", "password": "pw12345"})
    existing = app_client.get("/api/v1/experiments/exp_lastmod_blocks/blocks").json()
    hypothesis = next(b for b in existing if b["kind"] == "hypothesis")
    resp = app_client.put(
        "/api/v1/experiments/exp_lastmod_blocks/blocks",
        json=[{"id": hypothesis["id"], "kind": "hypothesis", "title": "H", "content_md": "edited", "position": 0}],
    )
    assert resp.status_code == 200

    body = app_client.get("/api/v1/experiments/exp_lastmod_blocks").json()
    assert body["last_modified_at"] is not None
    assert body["last_modified_by_email"] == "owner_blockmod@co.com"
    assert body["last_modified_by_first_name"] == "Block"


def test_experiment_reports_from_artifact_dir(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _make_experiment("exp_files")

    exp_dir = tmp_path / "exp_files"
    exp_dir.mkdir(parents=True, exist_ok=True)
    (exp_dir / "design_report.html").write_text("<html>design</html>", encoding="utf-8")

    detail = app_client.get("/api/v1/experiments/exp_files").json()
    assert detail["available_reports"] == ["design_report.html"]
    assert any(f["path"] == "design_report.html" for f in detail["files"])

    report_resp = app_client.get("/api/v1/experiments/exp_files/reports/design_report.html")
    assert report_resp.status_code == 200
    assert "design" in report_resp.text
    assert "attachment" not in report_resp.headers.get("content-disposition", "")

    # 6-part package pt.9: ?download=1 swaps to a file attachment with a
    # <experiment>_<report_name> filename, same content either way. The
    # header also carries filename*=UTF-8''... (RFC 5987, needed for
    # non-ASCII experiment names — see content_disposition()); this
    # experiment's name is plain ASCII, so the two agree.
    download_resp = app_client.get("/api/v1/experiments/exp_files/reports/design_report.html?download=1")
    assert download_resp.status_code == 200
    assert download_resp.text == report_resp.text
    assert download_resp.headers["content-disposition"] == (
        'attachment; filename="exp_files_design_report.html"; '
        "filename*=UTF-8''exp_files_design_report.html"
    )

    bad_report = app_client.get("/api/v1/experiments/exp_files/reports/report.html")
    assert bad_report.status_code == 404


def test_experiment_samples_zip_404_when_no_assignments(app_client, tmp_path, monkeypatch):
    """6-part package pt.7: not_found is correct here — there really are no
    assignments. The bug this replaces was 404-ing even when assignments
    DID exist, because the old implementation looked for a samples/*.csv
    directory on disk that ABKIT_MODE=db never writes (see
    backend/routers/experiments.py::_load_group_assignments)."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _make_experiment("exp_no_samples")
    resp = app_client.get("/api/v1/experiments/exp_no_samples/samples.zip")
    assert resp.status_code == 404


def test_experiment_samples_generated_from_assignments_table(app_client, tmp_path, monkeypatch):
    """6-part package pt.7 (bug fix): Download Samples must work off the
    real assignments table, not a file-mode-era samples/ directory that
    ABKIT_MODE=db never populates — regression coverage for the reported
    not_found bug on an experiment that genuinely has a split."""
    import pandas as pd

    from abkit.db.repositories import AssignmentRepo

    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    exp = _make_experiment("exp_real_split")
    assignments = pd.DataFrame(
        {
            "unit_id": ["u1", "u2", "u3", "u4"],
            "group": ["control", "control", "treatment", "treatment"],
            "stratum": ["ios", "android", "ios", "android"],
            "assigned_at": pd.Timestamp.now(tz="UTC"),
        }
    )
    AssignmentRepo().bulk_insert(exp.id, assignments)

    samples_resp = app_client.get("/api/v1/experiments/exp_real_split/samples")
    assert samples_resp.status_code == 200
    samples = {s["filename"]: s for s in samples_resp.json()}
    assert set(samples) == {"control.csv", "treatment.csv"}
    assert samples["control.csv"]["n_rows"] == 2
    assert samples["treatment.csv"]["n_rows"] == 2

    csv_resp = app_client.get("/api/v1/experiments/exp_real_split/samples/control.csv")
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    lines = csv_resp.text.strip().splitlines()
    assert lines[0] == "unit_id,group,stratum"
    assert len(lines) == 3  # header + 2 control rows
    assert "u1" in csv_resp.text and "u2" in csv_resp.text
    assert "u3" not in csv_resp.text  # treatment unit must not leak into control.csv

    missing_csv = app_client.get("/api/v1/experiments/exp_real_split/samples/missing.csv")
    assert missing_csv.status_code == 404

    zip_resp = app_client.get("/api/v1/experiments/exp_real_split/samples.zip")
    assert zip_resp.status_code == 200
    assert zip_resp.headers["content-type"] == "application/zip"
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
        assert set(zf.namelist()) == {"control.csv", "treatment.csv"}
        assert zf.read("treatment.csv").decode("utf-8").count("\n") == 3  # header + 2 rows


def test_samples_download_works_for_cyrillic_colon_experiment_name(app_client, tmp_path, monkeypatch):
    """Samples-download follow-up: a real experiment name like
    "PA: Тестовый эксперимент без истории" (Cyrillic + colon + spaces)
    crashed both download endpoints with an opaque internal_error —
    Starlette encodes response headers as latin-1, and a raw non-ASCII
    Content-Disposition filename= blows that up deep inside
    Response.__init__ (UnicodeEncodeError). content_disposition() (backend/
    routers/experiments.py) now sends an ASCII-sanitized filename= fallback
    plus the real name via RFC 5987's filename*=UTF-8''... The colon makes
    this name invalid as a Windows directory component, so this only
    exercises the DB-backed samples endpoints (no filesystem artifact dir
    involved) — report downloads get the same content_disposition() fix,
    covered separately (ASCII name) in test_experiment_reports_from_artifact_dir."""
    import io
    import re
    import zipfile
    from urllib.parse import quote, unquote

    import pandas as pd

    from abkit.db.repositories import AssignmentRepo

    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    name = "PA: Тестовый эксперимент без истории"
    exp = _make_experiment(name)
    encoded_name = quote(name, safe="")

    assignments = pd.DataFrame(
        {
            "unit_id": ["u1", "u2"],
            "group": ["control", "treatment"],
            "stratum": [None, None],
            "assigned_at": pd.Timestamp.now(tz="UTC"),
        }
    )
    AssignmentRepo().bulk_insert(exp.id, assignments)

    def _decoded_filename_star(header: str) -> str:
        match = re.search(r"filename\*=UTF-8''([^;]+)", header)
        assert match, f"no filename*=UTF-8'' in {header!r}"
        return unquote(match.group(1))

    zip_resp = app_client.get(f"/api/v1/experiments/{encoded_name}/samples.zip")
    assert zip_resp.status_code == 200, zip_resp.text
    disposition = zip_resp.headers["content-disposition"]
    assert _decoded_filename_star(disposition) == f"{name}_samples.zip"
    # Plain filename= fallback must be pure ASCII, or this response itself
    # would fail to encode the same way the original bug did.
    fallback = re.search(r'filename="([^"]+)"', disposition).group(1)
    fallback.encode("ascii")  # raises if not ASCII-safe
    assert ":" not in fallback
    with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
        assert set(zf.namelist()) == {"control.csv", "treatment.csv"}

    csv_resp = app_client.get(f"/api/v1/experiments/{encoded_name}/samples/control.csv")
    assert csv_resp.status_code == 200, csv_resp.text
    assert _decoded_filename_star(csv_resp.headers["content-disposition"]) == "control.csv"

    missing_csv = app_client.get(f"/api/v1/experiments/{encoded_name}/samples/missing.csv")
    assert missing_csv.status_code == 404


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
    exp = _make_experiment("exp_audit")
    # object_id, not just object_name — matches how abkit/jobs.py::_audit
    # actually writes entries in production (see bug fix п.15: History is
    # filtered by object_id now, not object_name).
    AuditRepo().log(
        action="design", object_type="experiment", object_id=str(exp.id),
        object_name="exp_audit", user_email="someone@co.com",
    )

    resp = app_client.get("/api/v1/experiments/exp_audit/audit")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["action"] == "design"


def test_experiment_audit_history_scoped_to_object_id_not_name(app_client):
    """Bug fix п.15: delete an experiment, create a new one under the SAME
    name — the new row gets a fresh uuid (server_default gen_random_uuid()),
    so its History must show only ITS OWN events, not the deleted
    experiment's (including its own delete audit entry) just because the
    name matches."""
    from abkit.db.repositories import AuditRepo, ExperimentRepo

    _login(app_client, email="owner_audit_reuse@co.com", role="admin")
    owner_id = UserRepo().get_by_email("owner_audit_reuse@co.com").id
    old_exp = _make_experiment("reused_name_exp", owner_id=owner_id)
    old_id = old_exp.id
    AuditRepo().log(
        action="experiment.create", object_type="experiment", object_id=str(old_id),
        object_name="reused_name_exp", user_email="owner_audit_reuse@co.com",
    )
    ExperimentRepo().delete("reused_name_exp")
    AuditRepo().log(
        action="experiment.delete", object_type="experiment", object_id=str(old_id),
        object_name="reused_name_exp", user_email="owner_audit_reuse@co.com",
    )

    new_exp = _make_experiment("reused_name_exp", owner_id=owner_id)
    assert new_exp.id != old_id  # fresh uuid, not the deleted row reused
    AuditRepo().log(
        action="experiment.create", object_type="experiment", object_id=str(new_exp.id),
        object_name="reused_name_exp", user_email="owner_audit_reuse@co.com",
    )

    resp = app_client.get("/api/v1/experiments/reused_name_exp/audit")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["action"] == "experiment.create"
    assert body["items"][0]["object_id"] == str(new_exp.id)
