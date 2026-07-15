"""R2 (FRONTEND.md §3.2, §5.2): список и предпросмотр датасетов
(Dataset.storage_path -> CSV)."""

from __future__ import annotations

from abkit.auth.passwords import hash_password
from abkit.db.repositories import DatasetRepo, ExperimentRepo, UserRepo


def _login(app_client):
    UserRepo().create(email="editor@co.com", first_name="E", password_hash=hash_password("pw12345"), role="editor")
    app_client.post("/api/v1/auth/login", json={"email": "editor@co.com", "password": "pw12345"})


def _make_dataset(tmp_path, n_rows=5):
    owner_id = UserRepo().create(
        email="owner@co.com", first_name="Owner", password_hash=hash_password("pw12345"), role="editor"
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


def _make_dataset_with_rows(tmp_path, filename, rows, columns):
    owner_id = UserRepo().create(
        email=f"owner_{filename}@co.com", first_name="Owner", password_hash=hash_password("pw12345"), role="editor"
    )
    csv_path = tmp_path / filename
    header = ",".join(columns)
    csv_path.write_text("\n".join([header] + rows), encoding="utf-8")
    dataset_id = DatasetRepo().create(
        kind="post_analysis", filename=filename, n_rows=len(rows),
        columns=columns, storage_path=str(csv_path), sha256="deadbeef", uploaded_by=owner_id,
    )
    return dataset_id


def test_duplicate_check_reports_no_duplicates_for_unique_unit_col(app_client, tmp_path):
    """Item 2: no dup unit_col -> Date column stays optional."""
    _login(app_client)
    dataset_id = _make_dataset_with_rows(
        tmp_path, "unique.csv",
        rows=[f"u{i},{i}" for i in range(5)],
        columns=["user_id", "revenue"],
    )
    resp = app_client.get(f"/api/v1/datasets/{dataset_id}/duplicate-check", params={"column": "user_id"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"has_duplicates": False, "n_duplicated_units": 0}


def test_duplicate_check_reports_duplicates_for_daily_data(app_client, tmp_path):
    """Item 2: day-by-day data (each user appears on multiple rows) -> Date
    column becomes required, counted by DISTINCT duplicated user, not by
    duplicate row."""
    _login(app_client)
    rows = []
    for day in range(3):
        for i in range(4):
            rows.append(f"u{i},{day},{10 + i}")
    dataset_id = _make_dataset_with_rows(
        tmp_path, "daily.csv", rows=rows, columns=["user_id", "day", "revenue"]
    )
    resp = app_client.get(f"/api/v1/datasets/{dataset_id}/duplicate-check", params={"column": "user_id"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_duplicates"] is True
    assert body["n_duplicated_units"] == 4


def test_duplicate_check_422_for_unknown_column(app_client, tmp_path):
    _login(app_client)
    dataset_id = _make_dataset(tmp_path)
    resp = app_client.get(f"/api/v1/datasets/{dataset_id}/duplicate-check", params={"column": "nope"})
    assert resp.status_code == 422


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
    UserRepo().create(email="viewer@co.com", first_name="V", password_hash=hash_password("pw12345"), role="viewer")
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


def test_demo_design_dataset_requires_editor_role(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    UserRepo().create(email="viewer2@co.com", first_name="V", password_hash=hash_password("pw12345"), role="viewer")
    app_client.post("/api/v1/auth/login", json={"email": "viewer2@co.com", "password": "pw12345"})
    resp = app_client.post("/api/v1/datasets/demo-design")
    assert resp.status_code == 403


def test_demo_design_dataset_creates_pre_design_dataset_with_suggested_config(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    resp = app_client.post("/api/v1/datasets/demo-design")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["dataset_id"]
    config = body["suggested_config"]
    assert config["unit_col"] == "user_id"
    assert set(config["groups"].keys()) == {"control", "treatment"}
    assert len(config["metrics"]) > 0

    preview_resp = app_client.get(f"/api/v1/datasets/{body['dataset_id']}/preview")
    assert preview_resp.status_code == 200
    assert preview_resp.json()["n_rows"] == 5000


def test_demo_design_dataset_uses_incrementing_name_when_demo_taken(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    owner_id = UserRepo().create(
        email="demo_owner@co.com", first_name="D", password_hash=hash_password("pw12345"), role="editor"
    )
    ExperimentRepo().create(name="demo", owner_id=owner_id, status="designed", config={})

    resp = app_client.post("/api/v1/datasets/demo-design")
    assert resp.json()["suggested_config"]["name"] == "demo_2"


def test_metric_baseline_continuous(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    dataset_id = _make_dataset(tmp_path, n_rows=5)  # unit_id,value columns 0..4

    resp = app_client.post(
        f"/api/v1/datasets/{dataset_id}/metric-baseline",
        json={"name": "value", "type": "continuous"},
    )
    assert resp.status_code == 200
    assert resp.json()["baseline_mean"] == 2.0  # mean(0,1,2,3,4)


def test_metric_baseline_returns_null_for_missing_column(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    dataset_id = _make_dataset(tmp_path)

    resp = app_client.post(
        f"/api/v1/datasets/{dataset_id}/metric-baseline",
        json={"name": "does_not_exist", "type": "continuous"},
    )
    assert resp.status_code == 200
    assert resp.json()["baseline_mean"] is None


def test_metric_baseline_404_for_unknown_dataset(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    resp = app_client.post(
        "/api/v1/datasets/11111111-1111-1111-1111-111111111111/metric-baseline",
        json={"name": "value", "type": "continuous"},
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------------
# UX package (Datasets §2): usage-check, delete, edit
# --------------------------------------------------------------------------


def test_dataset_usage_empty_when_unused(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    resp = app_client.post(
        "/api/v1/datasets", data={"kind": "pre_design"},
        files={"file": ("unused.csv", "a,b\n1,2\n", "text/csv")},
    )
    dataset_id = resp.json()["id"]

    usage_resp = app_client.get(f"/api/v1/datasets/{dataset_id}/usage")
    assert usage_resp.status_code == 200
    assert usage_resp.json()["experiments"] == []


def test_dataset_usage_lists_experiment(app_client, tmp_path):
    _login(app_client)
    dataset_id = _make_dataset(tmp_path)  # linked to experiment "exp_ds" via legacy experiment_id
    resp = app_client.get(f"/api/v1/datasets/{dataset_id}/usage")
    assert resp.status_code == 200
    assert resp.json()["experiments"] == ["exp_ds"]


def test_delete_unused_dataset_no_confirm_needed(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    resp = app_client.post(
        "/api/v1/datasets", data={"kind": "pre_design"},
        files={"file": ("to_delete.csv", "a,b\n1,2\n", "text/csv")},
    )
    dataset_id = resp.json()["id"]

    delete_resp = app_client.request("DELETE", f"/api/v1/datasets/{dataset_id}", json={})
    assert delete_resp.status_code == 204

    preview_resp = app_client.get(f"/api/v1/datasets/{dataset_id}/preview")
    assert preview_resp.status_code == 404


def test_delete_used_dataset_requires_typed_delete(app_client, tmp_path):
    # admin: _make_dataset doesn't set uploaded_by, so only admin (not just
    # any editor) can delete it — ownership permission is covered separately
    # by test_delete_dataset_forbidden_for_non_owner_non_admin.
    UserRepo().create(email="admin_del@co.com", first_name="A", password_hash=hash_password("pw12345"), role="admin")
    app_client.post("/api/v1/auth/login", json={"email": "admin_del@co.com", "password": "pw12345"})
    dataset_id = _make_dataset(tmp_path)

    without_confirm = app_client.request("DELETE", f"/api/v1/datasets/{dataset_id}", json={})
    assert without_confirm.status_code == 400
    body = without_confirm.json()
    assert body["error"]["code"] == "confirmation_required"
    assert body["error"]["details"]["experiments"] == ["exp_ds"]

    with_confirm = app_client.request("DELETE", f"/api/v1/datasets/{dataset_id}", json={"confirm": "DELETE"})
    assert with_confirm.status_code == 204


def test_delete_dataset_forbidden_for_non_owner_non_admin(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    UserRepo().create(email="owner2@co.com", first_name="O", password_hash=hash_password("pw12345"), role="editor")
    app_client.post("/api/v1/auth/login", json={"email": "owner2@co.com", "password": "pw12345"})
    resp = app_client.post(
        "/api/v1/datasets", data={"kind": "pre_design"},
        files={"file": ("owned.csv", "a,b\n1,2\n", "text/csv")},
    )
    dataset_id = resp.json()["id"]
    app_client.post("/api/v1/auth/logout")

    UserRepo().create(email="other_editor@co.com", first_name="OE", password_hash=hash_password("pw12345"), role="editor")
    app_client.post("/api/v1/auth/login", json={"email": "other_editor@co.com", "password": "pw12345"})
    delete_resp = app_client.request("DELETE", f"/api/v1/datasets/{dataset_id}", json={})
    assert delete_resp.status_code == 403


def test_patch_dataset_renames(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    resp = app_client.post(
        "/api/v1/datasets", data={"kind": "pre_design"},
        files={"file": ("original.csv", "a,b\n1,2\n", "text/csv")},
    )
    dataset_id = resp.json()["id"]

    patch_resp = app_client.patch(f"/api/v1/datasets/{dataset_id}", json={"name": "renamed.csv"})
    assert patch_resp.status_code == 200, patch_resp.text
    body = patch_resp.json()
    assert body["dataset"]["filename"] == "renamed.csv"
    assert body["job_id"] is None


def test_patch_dataset_rejects_sql_fields_for_upload_source(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    resp = app_client.post(
        "/api/v1/datasets", data={"kind": "pre_design"},
        files={"file": ("upload_only.csv", "a,b\n1,2\n", "text/csv")},
    )
    dataset_id = resp.json()["id"]

    patch_resp = app_client.patch(f"/api/v1/datasets/{dataset_id}", json={"sql_text": "SELECT 1"})
    assert patch_resp.status_code == 404  # StorageError -> not_found, per backend/errors.py


# Item 1 (upload rename step): column_renames re-materializes the CSV to
# parquet with the new names — checked end to end via PATCH + preview
# (dtypes/columns dispatch on file extension, abkit/dataset_files.py, so a
# successful read-back with the new names IS the proof the file changed).
def test_patch_dataset_renames_columns_and_records_original_names(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    resp = app_client.post(
        "/api/v1/datasets", data={"kind": "pre_design"},
        files={"file": ("cust_mnt_v2.csv", "cust_id,amt\n1,10.5\n2,20.0\n", "text/csv")},
    )
    dataset_id = resp.json()["id"]

    patch_resp = app_client.patch(
        f"/api/v1/datasets/{dataset_id}",
        json={"column_renames": {"cust_id": "customer_id", "amt": "amount"}},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    body = patch_resp.json()["dataset"]
    assert body["columns"] == ["customer_id", "amount"]
    assert body["renamed_columns"] == {"customer_id": "cust_id", "amount": "amt"}

    preview = app_client.get(f"/api/v1/datasets/{dataset_id}/preview").json()
    assert preview["columns"] == ["customer_id", "amount"]
    assert preview["rows"][0] == {"customer_id": 1, "amount": 10.5}
    assert preview["renamed_columns"] == {"customer_id": "cust_id", "amount": "amt"}


def test_patch_dataset_second_rename_tracks_true_original_name(app_client, tmp_path, monkeypatch):
    """a -> b, then b -> c: renamed_columns must still point at 'a' (the
    true original), not the intermediate 'b'."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    resp = app_client.post(
        "/api/v1/datasets", data={"kind": "pre_design"},
        files={"file": ("t.csv", "a,other\n1,2\n", "text/csv")},
    )
    dataset_id = resp.json()["id"]

    app_client.patch(f"/api/v1/datasets/{dataset_id}", json={"column_renames": {"a": "b"}})
    patch_resp = app_client.patch(f"/api/v1/datasets/{dataset_id}", json={"column_renames": {"b": "c"}})
    assert patch_resp.status_code == 200, patch_resp.text
    body = patch_resp.json()["dataset"]
    assert body["columns"] == ["c", "other"]
    assert body["renamed_columns"] == {"c": "a"}


def test_patch_dataset_rename_back_to_original_clears_the_entry(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    resp = app_client.post(
        "/api/v1/datasets", data={"kind": "pre_design"},
        files={"file": ("t.csv", "a,other\n1,2\n", "text/csv")},
    )
    dataset_id = resp.json()["id"]

    app_client.patch(f"/api/v1/datasets/{dataset_id}", json={"column_renames": {"a": "b"}})
    patch_resp = app_client.patch(f"/api/v1/datasets/{dataset_id}", json={"column_renames": {"b": "a"}})
    assert patch_resp.status_code == 200, patch_resp.text
    body = patch_resp.json()["dataset"]
    assert body["columns"] == ["a", "other"]
    assert body["renamed_columns"] is None


def test_patch_dataset_column_rename_rejects_empty_name(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    resp = app_client.post(
        "/api/v1/datasets", data={"kind": "pre_design"},
        files={"file": ("t.csv", "a,b\n1,2\n", "text/csv")},
    )
    dataset_id = resp.json()["id"]

    patch_resp = app_client.patch(f"/api/v1/datasets/{dataset_id}", json={"column_renames": {"a": "   "}})
    assert patch_resp.status_code == 404  # StorageError -> not_found, per backend/errors.py


def test_patch_dataset_column_rename_rejects_forbidden_character(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    resp = app_client.post(
        "/api/v1/datasets", data={"kind": "pre_design"},
        files={"file": ("t.csv", "a,b\n1,2\n", "text/csv")},
    )
    dataset_id = resp.json()["id"]

    patch_resp = app_client.patch(f"/api/v1/datasets/{dataset_id}", json={"column_renames": {"a": 'bad,name'}})
    assert patch_resp.status_code == 404


def test_patch_dataset_column_rename_rejects_duplicate_names(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    resp = app_client.post(
        "/api/v1/datasets", data={"kind": "pre_design"},
        files={"file": ("t.csv", "a,b\n1,2\n", "text/csv")},
    )
    dataset_id = resp.json()["id"]

    patch_resp = app_client.patch(f"/api/v1/datasets/{dataset_id}", json={"column_renames": {"a": "b"}})
    assert patch_resp.status_code == 404


def test_patch_dataset_column_rename_rejects_for_sql_source(app_client, tmp_path, monkeypatch):
    """Item 1.4: renaming only applies to source='upload' — SQL column
    names come from the query's own aliases. Admin login: sidesteps the
    ownership check entirely (this test is about the source check, not
    ownership — covered separately elsewhere)."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    UserRepo().create(email="admin_rename@co.com", first_name="A", password_hash=hash_password("pw12345"), role="admin")
    app_client.post("/api/v1/auth/login", json={"email": "admin_rename@co.com", "password": "pw12345"})
    dataset_id = _make_dataset_with_rows(tmp_path, "sql_ds.parquet", ["1,2"], ["a", "b"])
    # No live DB connection needed — force source='sql' directly to
    # simulate a real SQL-sourced row for this permission check.
    from abkit.db.engine import session_scope
    from abkit.db.models import Dataset

    with session_scope() as s:
        ds = s.get(Dataset, dataset_id)
        ds.source = "sql"

    patch_resp = app_client.patch(f"/api/v1/datasets/{dataset_id}", json={"column_renames": {"a": "alpha"}})
    assert patch_resp.status_code == 404


def _upload_owned(app_client, filename: str) -> str:
    resp = app_client.post(
        "/api/v1/datasets", data={"kind": "pre_design"},
        files={"file": (filename, "a,b\n1,2\n", "text/csv")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_bulk_delete_datasets_requires_typed_delete(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    dataset_id = _upload_owned(app_client, "bulk1.csv")

    resp = app_client.post("/api/v1/datasets/bulk-delete", json={"dataset_ids": [dataset_id], "confirm": "nope"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "confirmation_required"


def test_bulk_delete_datasets_happy_path(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    id1 = _upload_owned(app_client, "bulk_a.csv")
    id2 = _upload_owned(app_client, "bulk_b.csv")

    resp = app_client.post("/api/v1/datasets/bulk-delete", json={"dataset_ids": [id1, id2], "confirm": "DELETE"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body["deleted"]) == {id1, id2}
    assert body["skipped"] == []
    assert app_client.get(f"/api/v1/datasets/{id1}/preview").status_code == 404
    assert app_client.get(f"/api/v1/datasets/{id2}/preview").status_code == 404


def test_bulk_delete_datasets_skips_no_permission_and_deletes_the_rest(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    UserRepo().create(email="bulk_owner@co.com", first_name="O", password_hash=hash_password("pw12345"), role="editor")
    app_client.post("/api/v1/auth/login", json={"email": "bulk_owner@co.com", "password": "pw12345"})
    owned_id = _upload_owned(app_client, "owned_bulk.csv")
    app_client.post("/api/v1/auth/logout")

    UserRepo().create(email="bulk_other@co.com", first_name="OT", password_hash=hash_password("pw12345"), role="editor")
    app_client.post("/api/v1/auth/login", json={"email": "bulk_other@co.com", "password": "pw12345"})
    other_id = _upload_owned(app_client, "other_bulk.csv")

    resp = app_client.post(
        "/api/v1/datasets/bulk-delete", json={"dataset_ids": [owned_id, other_id], "confirm": "DELETE"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] == [other_id]
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["dataset_id"] == owned_id
    assert body["skipped"][0]["reason"] == "no permission"
    # the skipped dataset must survive untouched
    assert app_client.get(f"/api/v1/datasets/{owned_id}/preview").status_code == 200


def test_bulk_delete_datasets_used_by_experiment_deletes_anyway_with_confirm(app_client, tmp_path):
    UserRepo().create(email="bulk_admin@co.com", first_name="A", password_hash=hash_password("pw12345"), role="admin")
    app_client.post("/api/v1/auth/login", json={"email": "bulk_admin@co.com", "password": "pw12345"})
    dataset_id = str(_make_dataset(tmp_path))  # tied to experiment "exp_ds"

    resp = app_client.post("/api/v1/datasets/bulk-delete", json={"dataset_ids": [dataset_id], "confirm": "DELETE"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] == [dataset_id]
    assert body["skipped"] == []


def test_bulk_delete_datasets_reports_not_found(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    fake_id = "00000000-0000-0000-0000-000000000000"

    resp = app_client.post("/api/v1/datasets/bulk-delete", json={"dataset_ids": [fake_id], "confirm": "DELETE"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] == []
    assert body["skipped"] == [{"dataset_id": fake_id, "reason": "not found"}]
