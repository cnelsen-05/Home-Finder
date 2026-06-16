from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from realestate.paths import CONFIG_DIR, DEFAULT_DB_PATH


def load_environment() -> None:
    load_dotenv(override=False)


def get_db_path() -> Path:
    load_environment()
    configured = os.getenv("REAL_ESTATE_DB_PATH")
    if configured:
        return Path(configured)
    return DEFAULT_DB_PATH


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data or {}


def load_preferences(path: Path | None = None) -> dict[str, Any]:
    return load_yaml(path or CONFIG_DIR / "preferences.yaml")


def load_scoring_weights(path: Path | None = None) -> dict[str, Any]:
    return load_yaml(path or CONFIG_DIR / "scoring_weights.yaml")


def write_yaml_if_missing(path: Path, data: dict[str, Any]) -> None:
    if path.exists():
        return
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
