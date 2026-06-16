from __future__ import annotations

from sqlalchemy import select

from realestate.db import session_scope, sqlite_url_for_path
from realestate.db_transfer import (
    backup_database_to_json,
    database_table_counts,
    restore_database_from_json,
)
from realestate.models import Favorite, Listing, Property


def test_json_backup_restore_recovers_core_rows(tmp_path) -> None:
    source_db = tmp_path / "source.db"
    backup_path = tmp_path / "backup.json"
    restored_db = tmp_path / "restored.db"

    with session_scope(db_path=source_db) as session:
        prop = Property(
            normalized_address="1000 SAMPLE RD",
            address_line1="1000 Sample Rd",
            city="Plymouth",
            state="MN",
            zip="55446",
            latitude=45.0,
            longitude=-93.0,
        )
        listing = Listing(
            property=prop,
            source="manual",
            list_price=650000,
            beds=4,
            baths=3,
            property_type="single_family",
        )
        session.add(listing)
        session.flush()
        session.add(Favorite(listing=listing, user_rating="like", user_notes="Recover me."))

    backup_database_to_json(backup_path, url=sqlite_url_for_path(source_db))
    counts = restore_database_from_json(
        backup_path,
        destination_url=sqlite_url_for_path(restored_db),
        replace=True,
    )

    assert counts["properties"] == 1
    assert counts["listings"] == 1
    assert counts["favorites"] == 1
    assert database_table_counts(url=sqlite_url_for_path(restored_db))["favorites"] == 1

    with session_scope(db_path=restored_db) as session:
        favorite = session.execute(select(Favorite)).scalars().one()
        assert favorite.user_notes == "Recover me."
        assert favorite.listing is not None
        assert favorite.listing.property.address_line1 == "1000 Sample Rd"
