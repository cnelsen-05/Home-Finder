from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    create_engine,
    delete,
    func,
    insert,
    select,
    text,
)
from sqlalchemy.engine import Engine

from realestate.db import (
    database_mode,
    database_url,
    is_sqlite_url,
    make_engine,
    sqlite_url_for_path,
)
from realestate.geospatial import json_dumps
from realestate.models import Base


def backup_database_to_json(path: Path, *, url: str | None = None) -> Path:
    engine = make_engine(url=url or database_url())
    payload = _database_payload(engine)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return path


def database_table_counts(*, url: str | None = None) -> dict[str, int]:
    engine = make_engine(url=url or database_url())
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        return {
            table.name: connection.execute(select(func.count()).select_from(table)).scalar_one()
            for table in Base.metadata.sorted_tables
        }


def database_status(*, url: str | None = None) -> dict[str, Any]:
    return {
        "database": database_mode(url=url),
        "counts": database_table_counts(url=url),
    }


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
            rows = [
                _coerce_row_for_table(table, dict(row._mapping))
                for row in source.execute(select(table)).all()
            ]
            counts[table.name] = len(rows)
            if rows:
                dest.execute(insert(table), rows)
        _reset_postgres_sequences(dest_engine, dest)
    return counts


def restore_database_from_json(
    path: Path,
    *,
    destination_url: str | None = None,
    replace: bool = False,
) -> dict[str, int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("format") != "homeanalyze_db_backup_v1":
        raise ValueError(f"Unsupported backup format: {payload.get('format')}")
    dest_engine = make_engine(url=destination_url or database_url())
    Base.metadata.create_all(dest_engine)
    tables_payload = payload.get("tables") or {}
    counts: dict[str, int] = {}
    with dest_engine.begin() as dest:
        if replace:
            for table in reversed(Base.metadata.sorted_tables):
                dest.execute(delete(table))
        for table in Base.metadata.sorted_tables:
            rows = [
                _coerce_row_for_table(table, row)
                for row in tables_payload.get(table.name, [])
            ]
            counts[table.name] = len(rows)
            if rows:
                dest.execute(insert(table), rows)
        _reset_postgres_sequences(dest_engine, dest)
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


def _coerce_row_for_table(table, row: dict[str, Any]) -> dict[str, Any]:
    coerced: dict[str, Any] = {}
    for column in table.columns:
        value = row.get(column.name)
        if value is None:
            coerced[column.name] = None
        elif isinstance(column.type, DateTime):
            coerced[column.name] = _parse_datetime(value)
        elif isinstance(column.type, Boolean):
            coerced[column.name] = _parse_bool(value)
        elif isinstance(column.type, Integer):
            coerced[column.name] = int(value)
        elif isinstance(column.type, Float):
            coerced[column.name] = float(value)
        else:
            coerced[column.name] = value
    return coerced


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f")
    raise TypeError(f"Cannot parse datetime value {value!r}")


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _reset_postgres_sequences(engine: Engine, connection) -> None:
    if is_sqlite_url(str(engine.url)):
        return
    for table in Base.metadata.sorted_tables:
        primary_keys = list(table.primary_key.columns)
        if len(primary_keys) != 1 or not isinstance(primary_keys[0].type, Integer):
            continue
        column = primary_keys[0]
        sequence_name = connection.execute(
            text("select pg_get_serial_sequence(:table_name, :column_name)"),
            {"table_name": table.name, "column_name": column.name},
        ).scalar_one_or_none()
        if not sequence_name:
            continue
        max_id = connection.execute(select(func.max(column))).scalar_one() or 0
        if max_id:
            connection.execute(
                text("select setval(:sequence_name, :value, true)"),
                {"sequence_name": sequence_name, "value": int(max_id)},
            )
        else:
            connection.execute(
                text("select setval(:sequence_name, 1, false)"),
                {"sequence_name": sequence_name},
            )
