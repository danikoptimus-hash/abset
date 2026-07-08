"""R2 (FRONTEND.md §3.2, §5.2): список и предпросмотр датасетов
(Dataset.storage_path -> CSV)."""

from __future__ import annotations

from abkit.auth.passwords import hash_password
from abkit.db.repositories import DatasetRepo, ExperimentRepo, UserRepo


def _login(app_client):
    UserRepo().create(email="editor@co.com", name="E", password_hash=hash_password("pw12345"), role="editor")
    app_client.post("/api/v1/auth/login", json={"email": "editor@co.com", "password": "pw12345"})


def _make_dataset(tmp_path, n_rows=5):
    owner_id = UserRepo().create(
        email="owner@co.com", name="Owner", password_hash=hash_password("pw12345"), role="editor"
    )
    exp = ExperimentRepo().create(
        name="exp_ds", owner_id=owner_id, status="designed", config={"name": "exp_ds"}
    )
    csv_path = tmp_path / "upload.csv"
    lines = ["unit_id,value"] + [f"u{i},{i}" for i in range(n_rows)]
    csv_path.write_text("\n".join(lines), encoding="utf-8")
    dataset_id = DatasetRepo().create(
        experiment_id=exp.id, kind="pre_design", filename="upload.csv", n_rows=n_rows,
        columns=["unit_id", "value"], storage_path=str(csv_path), sha256="deadbeef",
    )
    return dataset_id


def test_preview_requires_login(app_client, tmp_path):
    dataset_id = _make_dataset(tmp_path)
    resp = app_client.get(f"/api/v1/datasets/{dataset_id}/preview")
    assert resp.status_code == 401


def test_preview_returns_limited_rows(app_client, tmp_path):
    _login(app_client)
    dataset_id = _make_dataset(tmp_path, n_rows=10)
    resp = app_client.get(f"/api/v1/datasets/{dataset_id}/preview", params={"rows": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"] == "upload.csv"
    assert body["n_rows"] == 10
    assert body["columns"] == ["unit_id", "value"]
    assert len(body["rows"]) == 3
    assert body["rows"][0]["unit_id"] == "u0"


def test_preview_404_for_unknown_dataset(app_client):
    _login(app_client)
    resp = app_client.get("/api/v1/datasets/11111111-1111-1111-1111-111111111111/preview")
    assert resp.status_code == 404


def test_preview_422_for_malformed_id(app_client):
    _login(app_client)
    resp = app_client.get("/api/v1/datasets/not-a-uuid/preview")
    assert resp.status_code == 422


def test_list_datasets_requires_login(app_client, tmp_path):
    _make_dataset(tmp_path)
    resp = app_client.get("/api/v1/datasets")
    assert resp.status_code == 401


def test_list_datasets_includes_experiment_and_uploader(app_client, tmp_path):
    _login(app_client)
    _make_dataset(tmp_path)
    resp = app_client.get("/api/v1/datasets")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["experiment_name"] == "exp_ds"
    assert item["filename"] == "upload.csv"
    assert item["kind"] == "pre_design"


def test_upload_requires_editor_role(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    UserRepo().create(email="viewer@co.com", name="V", password_hash=hash_password("pw12345"), role="viewer")
    app_client.post("/api/v1/auth/login", json={"email": "viewer@co.com", "password": "pw12345"})
    resp = app_client.post(
        "/api/v1/datasets", data={"kind": "pre_design"},
        files={"file": ("data.csv", "a,b\n1,2\n", "text/csv")},
    )
    assert resp.status_code == 403


def test_upload_without_experiment_creates_unlinked_dataset(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    resp = app_client.post(
        "/api/v1/datasets", data={"kind": "pre_design"},
        files={"file": ("upload2.csv", "unit_id,value\nu1,1\nu2,2\n", "text/csv")},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["experiment_id"] is None
    assert body["n_rows"] == 2
    assert body["columns"] == ["unit_id", "value"]
    assert body["dtypes"]["value"] in ("int64",)


def test_upload_with_unknown_experiment_name_404(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    resp = app_client.post(
        "/api/v1/datasets", data={"kind": "post_analysis", "experiment_name": "does_not_exist"},
        files={"file": ("upload3.csv", "unit_id,value\nu1,1\n", "text/csv")},
    )
    assert resp.status_code == 404


def test_upload_invalid_kind_422(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    resp = app_client.post(
        "/api/v1/datasets", data={"kind": "bogus"},
        files={"file": ("upload4.csv", "unit_id,value\nu1,1\n", "text/csv")},
    )
    assert resp.status_code == 422


def test_upload_rejects_file_over_size_limit(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ABKIT_MAX_UPLOAD_MB", "1")
    _login(app_client)
    big_csv = "unit_id,value\n" + "\n".join(f"u{i},{i}" for i in range(200_000))
    assert len(big_csv.encode()) > 1024 * 1024
    resp = app_client.post(
        "/api/v1/datasets", data={"kind": "pre_design"},
        files={"file": ("big.csv", big_csv, "text/csv")},
    )
    assert resp.status_code == 413
