from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from realestate.config import load_preferences
from realestate.models import IssueFlag, Listing, ListingSnapshot, PublicRecord, SaleHistoryRecord
from realestate.scoring.overall import latest_score, score_listing
from realestate.scoring.quality import score_quality
from realestate.scoring.risk import score_risk
from realestate.scoring.value import score_value
from realestate.sources.manual_csv import detect_price_change, import_favorites_csv


def _import_one(session, tmp_path, description: str = "Updated kitchen and two-car garage."):
    path = tmp_path / "favorites.csv"
    path.write_text(
        "source,url,address,city,state,zip,price,beds,baths,finished_sqft,lot_size,year_built,property_type,status,description,user_rating,user_notes\n"
        f'manual,https://example.invalid/1,4521 Example Ave S,Minneapolis,MN,55419,699000,4,3,2650,7405,1938,single_family,active,"{description}",maybe,Notes\n',
        encoding="utf-8",
    )
    import_favorites_csv(path, session)
    session.flush()
    return session.execute(select(Listing)).scalars().one()


def test_risk_keyword_detection(session, tmp_path) -> None:
    prefs = load_preferences()
    listing = _import_one(session, tmp_path, "As-is investor special with wet basement.")

    quality = score_quality(listing, prefs)
    risk = score_risk(listing, prefs, [])

    assert any("as-is" in item.lower() for item in quality.negative_drivers)
    assert risk.score < 70


def test_missing_data_remains_missing(session, tmp_path) -> None:
    prefs = load_preferences()
    listing = _import_one(session, tmp_path)
    listing.annual_taxes = None
    listing.lot_size_sqft = None

    risk = score_risk(listing, prefs, [])

    assert any("annual_taxes" in item for item in risk.missing_data)
    assert any("lot_size_sqft" in item for item in risk.missing_data)


def test_scoring_is_deterministic_for_same_listing(session, tmp_path) -> None:
    prefs = load_preferences()
    listing = _import_one(session, tmp_path)

    first = score_listing(session, listing, prefs)
    first_payload = json.loads(first.explanation_json)
    second = score_listing(session, listing, prefs)
    second_payload = json.loads(second.explanation_json)

    assert first.overall_score == second.overall_score
    assert first_payload["recommendation_bucket"] == second_payload["recommendation_bucket"]


def test_latest_score_returns_newest_when_history_exists(session, tmp_path) -> None:
    prefs = load_preferences()
    listing = _import_one(session, tmp_path)

    first = score_listing(session, listing, prefs)
    second = score_listing(session, listing, prefs)
    session.flush()

    assert latest_score(session, listing).id == second.id
    assert first.id != second.id


def test_score_creates_issue_flags(session, tmp_path) -> None:
    prefs = load_preferences()
    listing = _import_one(session, tmp_path, "As-is with no basement photos and sewer line concern.")

    score_listing(session, listing, prefs)
    session.flush()

    flags = session.execute(select(IssueFlag)).scalars().all()
    assert flags
    assert any("Verify" in flag.title for flag in flags)


def test_public_record_medium_confidence_improves_risk_context(session, tmp_path) -> None:
    prefs = load_preferences()
    listing = _import_one(session, tmp_path)
    session.add(
        PublicRecord(
            property=listing.property,
            source_name="Manual public record",
            record_type="tax",
            confidence="medium",
            parsed_json="{}",
        )
    )
    session.flush()

    risk = score_risk(listing, prefs, listing.property.public_records)

    assert any("public record" in item.lower() for item in risk.positive_drivers)


def test_stale_public_sale_history_is_not_treated_as_current_comp(session, tmp_path) -> None:
    prefs = load_preferences()
    listing = _import_one(session, tmp_path)
    session.add(
        SaleHistoryRecord(
            property_id=listing.property_id,
            sale_date="2003-03-01",
            sale_price=300000,
            source_name="MetroGIS Regional Parcels",
            confidence="medium",
        )
    )
    session.flush()

    value = score_value(listing, prefs, session)

    assert any("years old" in item for item in value.negative_drivers)
    assert not any("above the latest parcel-reported sale" in item for item in value.negative_drivers)


def test_recent_public_sale_history_can_trigger_price_scrutiny(session, tmp_path) -> None:
    prefs = load_preferences()
    listing = _import_one(session, tmp_path)
    recent_sale = (datetime.now(UTC) - timedelta(days=365)).date().isoformat()
    session.add(
        SaleHistoryRecord(
            property_id=listing.property_id,
            sale_date=recent_sale,
            sale_price=500000,
            source_name="MetroGIS Regional Parcels",
            confidence="medium",
        )
    )
    session.flush()

    value = score_value(listing, prefs, session)

    assert any("above the latest parcel-reported sale" in item for item in value.negative_drivers)


def test_price_change_detection_handles_mixed_timezone_snapshots(session, tmp_path) -> None:
    listing = _import_one(session, tmp_path)
    listing.snapshots[0].observed_at = datetime(2026, 1, 1)
    listing.snapshots[0].price = 699000
    session.add(
        ListingSnapshot(
            listing=listing,
            observed_at=datetime(2026, 1, 2, tzinfo=UTC),
            price=679000,
            status="active",
        )
    )
    session.flush()

    change = detect_price_change(listing)

    assert change["changed"] is True
    assert change["direction"] == "reduction"
    assert change["amount"] == 20000
