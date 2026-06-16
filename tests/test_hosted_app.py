from __future__ import annotations

from fastapi.testclient import TestClient

from realestate.db import normalize_database_url, reset_engine_cache
from realestate.hosted_app import app


def test_vercel_entrypoint_exports_hosted_app() -> None:
    from api.index import app as vercel_app

    assert vercel_app is app


def test_hosted_app_requires_access_code_and_allows_api_after_login(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.setenv("REAL_ESTATE_DB_PATH", str(tmp_path / "hosted.db"))
    monkeypatch.setenv("HOMEANALYZE_ACCESS_CODE", "family-code")
    monkeypatch.setenv("HOMEANALYZE_AUTH_SECRET", "test-secret")
    reset_engine_cache()
    client = TestClient(app)

    page = client.get("/", follow_redirects=False)
    api = client.get("/api/homes")

    assert page.status_code == 303
    assert page.headers["location"].startswith("/login")
    assert api.status_code == 401

    denied = client.post(
        "/login",
        data={"access_code": "wrong", "next": "/"},
        follow_redirects=False,
    )
    assert denied.status_code == 401

    accepted = client.post(
        "/login",
        data={"access_code": "family-code", "next": "/"},
        follow_redirects=False,
    )
    assert accepted.status_code == 303
    assert "homeanalyze_session" in accepted.headers["set-cookie"]

    homes = client.get("/api/homes")
    assert homes.status_code == 200
    assert homes.json() == {"type": "FeatureCollection", "features": []}

    reset_engine_cache()


def test_postgres_urls_use_psycopg_driver() -> None:
    assert (
        normalize_database_url("postgres://user:pass@example.com/db")
        == "postgresql+psycopg://user:pass@example.com/db"
    )
    assert (
        normalize_database_url("postgresql://user:pass@example.com/db")
        == "postgresql+psycopg://user:pass@example.com/db"
    )
