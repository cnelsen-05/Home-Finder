from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from realestate.config import get_db_path, load_environment
from realestate.models import Base
from realestate.paths import ensure_project_dirs

_ENGINE_CACHE: dict[str, Engine] = {}
_ENGINE_LOCK = Lock()


class HostedDatabaseNotConfigured(RuntimeError):
    """Raised when hosted runtime would otherwise fall back to ephemeral SQLite."""


def configured_database_url() -> str | None:
    load_environment()
    configured = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
    return configured.strip() if configured and configured.strip() else None


def is_hosted_runtime() -> bool:
    load_environment()
    return _truthy(os.getenv("VERCEL")) or _truthy(os.getenv("HOMEANALYZE_HOSTED"))


def allows_ephemeral_sqlite() -> bool:
    load_environment()
    return _truthy(os.getenv("HOMEANALYZE_ALLOW_EPHEMERAL_SQLITE"))


def database_mode(db_path: Path | None = None, url: str | None = None) -> dict[str, str | bool]:
    configured = url or configured_database_url()
    hosted = is_hosted_runtime()
    if configured:
        normalized = normalize_database_url(configured)
        return {
            "mode": "postgres" if normalized.startswith("postgresql+") else "configured",
            "url": _redact_url(normalized),
            "hosted": hosted,
            "persistent": True,
        }
    return {
        "mode": "sqlite",
        "url": str(db_path or get_db_path()),
        "hosted": hosted,
        "persistent": not hosted,
    }


def assert_hosted_database_configured() -> None:
    if is_hosted_runtime() and not configured_database_url() and not allows_ephemeral_sqlite():
        raise HostedDatabaseNotConfigured(
            "Hosted deployment is missing DATABASE_URL or POSTGRES_URL. "
            "Refusing to create an empty ephemeral SQLite database."
        )


def sqlite_url_for_path(db_path: Path | None = None) -> str:
    path = db_path or get_db_path()
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path.as_posix()}"


def database_url(db_path: Path | None = None) -> str:
    if db_path is not None:
        return sqlite_url_for_path(db_path)
    configured = configured_database_url()
    if configured:
        return normalize_database_url(configured)
    assert_hosted_database_configured()
    return sqlite_url_for_path()


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


def is_sqlite_url(url: str) -> bool:
    return url.startswith("sqlite:")


def _truthy(value: str | None) -> bool:
    return bool(value and value.strip().lower() in {"1", "true", "yes", "on"})


def _redact_url(url: str) -> str:
    if "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    return f"{scheme}://***@{rest.split('@', 1)[1]}"


def make_engine(db_path: Path | None = None, url: str | None = None) -> Engine:
    resolved_url = url or database_url(db_path)
    kwargs = {"future": True, "pool_pre_ping": not is_sqlite_url(resolved_url)}
    if is_sqlite_url(resolved_url):
        kwargs["connect_args"] = {"check_same_thread": False}
    elif _database_pool_mode() == "null":
        kwargs["poolclass"] = NullPool
    return create_engine(resolved_url, **kwargs)


def get_engine(db_path: Path | None = None, url: str | None = None) -> Engine:
    resolved_url = url or database_url(db_path)
    with _ENGINE_LOCK:
        engine = _ENGINE_CACHE.get(resolved_url)
        if engine is None:
            engine = make_engine(url=resolved_url)
            _ENGINE_CACHE[resolved_url] = engine
        return engine


def reset_engine_cache() -> None:
    with _ENGINE_LOCK:
        for engine in _ENGINE_CACHE.values():
            engine.dispose()
        _ENGINE_CACHE.clear()


def create_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=engine or make_engine(), expire_on_commit=False, future=True)


def init_database(db_path: Path | None = None, url: str | None = None) -> Engine:
    resolved_url = url or database_url(db_path)
    if is_sqlite_url(resolved_url):
        ensure_project_dirs()
    engine = get_engine(url=resolved_url)
    Base.metadata.create_all(engine)
    return engine


@contextmanager
def session_scope(db_path: Path | None = None, url: str | None = None) -> Iterator[Session]:
    engine = init_database(db_path, url=url)
    factory = create_session_factory(engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _database_pool_mode() -> str:
    load_environment()
    configured = os.getenv("HOMEANALYZE_DB_POOL")
    if configured:
        return configured.strip().lower()
    return "null" if is_hosted_runtime() else "default"
