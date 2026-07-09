"""DB1 (CLAUDE.md, Database Connections feature): unit tests for the crypto
and engine-URL-building modules that don't need a database at all."""

import pytest

from abkit.db_connections.crypto import DecryptionError, decrypt_password, encrypt_password
from abkit.db_connections.engines import ConnectionSpec, build_url, default_port


@pytest.fixture(autouse=True)
def _secret_key(monkeypatch):
    monkeypatch.setenv("ABKIT_SECRET_KEY", "a-real-generated-secret-for-crypto-tests")


def test_encrypt_decrypt_roundtrip():
    ciphertext = encrypt_password("s3cr3t")
    assert ciphertext != "s3cr3t"
    assert decrypt_password(ciphertext) == "s3cr3t"


def test_decrypt_fails_with_different_secret_key(monkeypatch):
    ciphertext = encrypt_password("s3cr3t")
    monkeypatch.setenv("ABKIT_SECRET_KEY", "a-completely-different-secret-value-here")
    with pytest.raises(DecryptionError):
        decrypt_password(ciphertext)


def test_default_ports():
    assert default_port("postgresql", ssl=False) == 5432
    assert default_port("postgresql", ssl=True) == 5432
    assert default_port("clickhouse", ssl=False) == 8123
    assert default_port("clickhouse", ssl=True) == 8443
    assert default_port("mssql", ssl=False) == 1433


def test_default_port_rejects_unknown_engine():
    with pytest.raises(ValueError):
        default_port("mysql", ssl=False)


def test_build_url_postgresql():
    spec = ConnectionSpec(
        engine="postgresql", host="db.internal", port=5432, database="mydb",
        username="alice", password="p@ss/word", ssl=False,
    )
    url = build_url(spec)
    assert url.startswith("postgresql+psycopg://alice:")
    assert "@db.internal:5432/mydb" in url
    # special characters in the password must be percent-encoded, not break the URL
    assert "p@ss/word" not in url


def test_build_url_postgresql_ssl():
    spec = ConnectionSpec(
        engine="postgresql", host="db.internal", port=5432, database="mydb",
        username="alice", password="pw", ssl=True,
    )
    assert build_url(spec).endswith("?sslmode=require")


def test_build_url_clickhouse():
    spec = ConnectionSpec(
        engine="clickhouse", host="ch.internal", port=8123, database="analytics",
        username="bob", password="pw", ssl=False,
    )
    url = build_url(spec)
    assert url.startswith("clickhousedb://bob:pw@ch.internal:8123/analytics")


def test_build_url_clickhouse_ssl_sets_secure_param():
    spec = ConnectionSpec(
        engine="clickhouse", host="ch.internal", port=8443, database="analytics",
        username="bob", password="pw", ssl=True,
    )
    assert build_url(spec).endswith("?secure=true")


def test_build_url_mssql():
    spec = ConnectionSpec(
        engine="mssql", host="sql.internal", port=1433, database="master",
        username="sa", password="pw", ssl=False,
    )
    url = build_url(spec)
    assert url.startswith("mssql+pymssql://sa:pw@sql.internal:1433/master")


def test_build_url_rejects_unknown_engine():
    spec = ConnectionSpec(
        engine="mysql", host="h", port=1, database="d", username="u", password="p",
    )
    with pytest.raises(ValueError):
        build_url(spec)
