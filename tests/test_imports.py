from __future__ import annotations

from sqlalchemy import select

from realestate.models import Favorite, LifeAnchor, Listing, ListingSnapshot, Property
from realestate.parsing.address_parser import normalize_address
from realestate.sources.manual_csv import (
    detect_price_change,
    import_favorites_csv,
    import_life_anchors_file,
)


def test_address_normalization() -> None:
    assert normalize_address("4521 Example Avenue S #2") == "4521 EXAMPLE AVE S"


def test_favorite_csv_import_creates_listing_snapshot_and_property(session, tmp_path) -> None:
    csv_path = tmp_path / "favorites.csv"
    csv_path.write_text(
        "source,url,address,city,state,zip,price,beds,baths,finished_sqft,lot_size,year_built,property_type,status,description,user_rating,user_notes\n"
        'manual,https://example.invalid/1,4521 Example Avenue S,Minneapolis,MN,55419,699000,4,3,2650,7405,1938,single_family,active,"Updated kitchen, sewer line noted.",strong_like,Verify sewer\n',
        encoding="utf-8",
    )

    favorites = import_favorites_csv(csv_path, session)
    session.flush()

    assert len(favorites) == 1
    assert session.execute(select(Property)).scalars().one().normalized_address == "4521 EXAMPLE AVE S"
    listing = session.execute(select(Listing)).scalars().one()
    assert listing.list_price == 699000
    assert session.execute(select(Favorite)).scalars().one().user_rating == "strong_like"
    assert session.execute(select(ListingSnapshot)).scalars().one().price == 699000


def test_duplicate_import_updates_existing_listing_and_detects_price_reduction(session, tmp_path) -> None:
    csv_path = tmp_path / "favorites.csv"
    header = (
        "source,url,address,city,state,zip,price,beds,baths,finished_sqft,lot_size,year_built,"
        "property_type,status,description,user_rating,user_notes\n"
    )
    row1 = (
        'manual,https://example.invalid/1,4521 Example Avenue S,Minneapolis,MN,55419,699000,4,3,2650,7405,1938,'
        'single_family,active,"Updated kitchen.",strong_like,Verify sewer\n'
    )
    row2 = row1.replace("699000", "679000", 1)
    csv_path.write_text(header + row1, encoding="utf-8")
    import_favorites_csv(csv_path, session)
    session.flush()
    csv_path.write_text(header + row2, encoding="utf-8")
    import_favorites_csv(csv_path, session)
    session.flush()

    listings = session.execute(select(Listing)).scalars().all()
    assert len(listings) == 1
    assert len(listings[0].snapshots) == 2
    change = detect_price_change(listings[0])
    assert change["changed"] is True
    assert change["direction"] == "reduction"
    assert change["amount"] == 20000


def test_blank_reimport_preserves_existing_listing_facts(session, tmp_path) -> None:
    csv_path = tmp_path / "favorites.csv"
    header = (
        "source,url,address,city,state,zip,price,beds,baths,finished_sqft,lot_size,year_built,"
        "property_type,status,description,user_rating,user_notes\n"
    )
    row_with_facts = (
        'manual_address_list,,1831 Koehnen Cir,Excelsior,MN,55331,800000,4,4,5540,51401,1980,'
        'single_family,coming_soon,"Original listing facts.",like,Notes\n'
    )
    blank_report_request = (
        "manual_address_list,,1831 Koehnen Cir,Excelsior,MN,55331,,,,,,,,,,,\n"
    )
    csv_path.write_text(header + row_with_facts, encoding="utf-8")
    import_favorites_csv(csv_path, session)
    session.flush()
    csv_path.write_text(header + blank_report_request, encoding="utf-8")
    import_favorites_csv(csv_path, session)
    session.flush()

    listing = session.execute(select(Listing)).scalars().one()
    assert listing.list_price == 800000
    assert listing.beds == 4
    assert listing.baths == 4
    assert listing.finished_sqft == 5540
    assert listing.status == "coming_soon"
    assert listing.description == "Original listing facts."


def test_life_anchor_import_from_yaml(session, tmp_path) -> None:
    path = tmp_path / "anchors.yaml"
    path.write_text(
        """
anchors:
  - name: Work
    category: work
    address: 100 Washington Ave S
    city: Minneapolis
    state: MN
    zip: "55401"
    priority: 1
    notes: Office
""",
        encoding="utf-8",
    )

    anchors = import_life_anchors_file(path, session)
    session.flush()

    assert len(anchors) == 1
    anchor = session.execute(select(LifeAnchor)).scalars().one()
    assert anchor.name == "Work"
    assert anchor.category == "work"
    assert "Minneapolis" in anchor.address


def test_life_anchor_replace_removes_old_profile_anchors(session, tmp_path) -> None:
    first = tmp_path / "first.yaml"
    first.write_text(
        """
anchors:
  - name: Old Work
    category: work
    address: Old
""",
        encoding="utf-8",
    )
    second = tmp_path / "second.yaml"
    second.write_text(
        """
anchors:
  - name: Example Work Anchor
    category: work
    address: Example Work Anchor
    city: Plymouth
    state: MN
""",
        encoding="utf-8",
    )

    import_life_anchors_file(first, session)
    import_life_anchors_file(second, session, replace=True)
    session.flush()

    anchors = session.execute(select(LifeAnchor)).scalars().all()
    assert len(anchors) == 1
    assert anchors[0].name == "Example Work Anchor"
