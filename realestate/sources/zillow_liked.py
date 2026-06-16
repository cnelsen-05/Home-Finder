"""Compliant Zillow favorites handling.

This module intentionally does not scrape Zillow. Store user-provided URLs or
import user-created CSV/text exports through the manual import path.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from realestate.sources.manual_csv import import_favorites_csv


def import_user_provided_favorites_csv(path: Path, session: Session):
    return import_favorites_csv(path, session)
