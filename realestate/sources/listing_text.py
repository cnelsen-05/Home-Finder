from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from realestate.parsing.listing_text_parser import parse_listing_text
from realestate.sources.manual_csv import upsert_favorite_from_row, upsert_listing_from_row


def import_listing_text(path: Path, session: Session, favorite: bool = True):
    parsed = parse_listing_text(path.read_text(encoding="utf-8"))
    row = {
        "source": "listing_text",
        "address": parsed.address,
        "city": parsed.city,
        "state": parsed.state,
        "zip": parsed.zip,
        "price": parsed.price,
        "beds": parsed.beds,
        "baths": parsed.baths,
        "finished_sqft": parsed.finished_sqft,
        "lot_size": parsed.lot_size_sqft,
        "year_built": parsed.year_built,
        "garage_spaces": parsed.garage_spaces,
        "description": parsed.description,
        "user_rating": "maybe",
        "user_notes": "Imported from user-provided listing text.",
    }
    return upsert_favorite_from_row(row, session) if favorite else upsert_listing_from_row(row, session)
