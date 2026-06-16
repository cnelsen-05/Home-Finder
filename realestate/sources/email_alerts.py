from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.orm import Session

from realestate.sources.listing_text import import_listing_text

URL_RE = re.compile(r"https?://\S+")


def extract_listing_urls_from_user_email(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return [match.group(0).rstrip(").,]") for match in URL_RE.finditer(text)]


def import_user_forwarded_email_text(path: Path, session: Session):
    """Parse only email text the user received or forwarded."""

    return import_listing_text(path, session, favorite=True)
