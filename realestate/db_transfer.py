from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, delete, insert, select
from sqlalchemy.engine import Engine

from realestate.db import database_url, make_engine, sqlite_url_for_path
from realestate.geospatial import json_dumps
from realestate.models import Base


def backup_database_to_json(path: Path, *, url: str | None = None) -> Path:
    engine = make_engine(url=url or database_url())
    payload = _database_payload(engine)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return path


def migrate_sqlite_to_database(
    sqlite_path: Path,
    *,
    destination_url: str | None = None,
    replace: bool = False,
) -> dict[str, int]:
    source_engine = create_engine(sqlite_url_for_path(sqlite_path), future=True)
    dest_engine = make_engine(url=destination_url or database_url())
    Base.metadata.create_all(dest_engine)
    counts: dict[str, int] = {}
    with source_engine.connect() as source, dest_engine.begin() as dest:
        if replace:
            for table in reversed(Base.metadata.sorted_tables):
                dest.execute(delete(table))
        for table in Base.metadata.sorted_tables:
            rows = [dict(row._mapping) for row in source.execute(select(table)).all()]
            counts[table.name] = len(rows)
            if rows:
                dest.execute(insert(table), rows)
    return counts


def _database_payload(engine: Engine) -> dict[str, Any]:
    with engine.connect() as connection:
        return {
            "format": "homeanalyze_db_backup_v1",
            "tables": {
                table.name: [
                    {key: _json_value(value) for key, value in dict(row._mapping).items()}
                    for row in connection.execute(select(table)).all()
                ]
                for table in Base.metadata.sorted_tables
            },
        }


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.loads(json_dumps(value))
    return value
