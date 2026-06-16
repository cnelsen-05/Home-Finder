from __future__ import annotations

from sqlalchemy import select

from realestate.config import load_preferences
from realestate.models import Listing, PublicRecord, Report
from realestate.reports.render import render_comparison_report, render_favorite_review
from realestate.scoring.guardrails import guardrail_violations
from realestate.sources.manual_csv import import_favorites_csv
from realestate.sources.mls_grid import MLSGridAdapter, MLSGridCredentialsMissing
from realestate.sources.public_records.hennepin_property import HennepinPropertyAdapter


def _import_two(session, tmp_path):
    path = tmp_path / "favorites.csv"
    path.write_text(
        "source,url,address,city,state,zip,price,beds,baths,finished_sqft,lot_size,year_built,property_type,status,description,user_rating,user_notes\n"
        'manual,https://example.invalid/1,4521 Example Ave S,Minneapolis,MN,55419,699000,4,3,2650,7405,1938,single_family,active,"Updated kitchen, finished basement, two-car garage.",strong_like,Good\n'
        'manual,https://example.invalid/2,612 Example Blvd,St. Louis Park,MN,55416,759000,4,2.5,2450,6098,1951,single_family,active,"No basement photos, shared driveway, busy road.",maybe,Watch\n',
        encoding="utf-8",
    )
    import_favorites_csv(path, session)
    session.flush()
    return session.execute(select(Listing).order_by(Listing.id)).scalars().all()


def test_report_rendering_writes_markdown_and_report_row(session, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    prefs = load_preferences()
    listings = _import_two(session, tmp_path)

    path = render_favorite_review(session, listings[0].id, prefs)
    session.flush()

    text = path.read_text(encoding="utf-8")
    assert "Favorite Home Review" in text
    assert "Questions for Buyer Agent" in text
    assert session.execute(select(Report)).scalars().one().report_type == "favorite_home_review"


def test_comparison_report_ranking(session, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    prefs = load_preferences()
    _import_two(session, tmp_path)

    path = render_comparison_report(session, prefs)

    text = path.read_text(encoding="utf-8")
    assert "Favorite Homes Comparison" in text
    assert "4521 Example" in text
    assert "612 Example" in text


def test_hennepin_adapter_failure_handling_is_persistable(session, tmp_path) -> None:
    listings = _import_two(session, tmp_path)
    adapter = HennepinPropertyAdapter()

    result = adapter.lookup_property(listings[0].property)
    record = PublicRecord(
        property=listings[0].property,
        source_name=result.source_name,
        source_url=result.source_url,
        record_type=result.record_type,
        parsed_json='{"status": "skipped"}',
        confidence=result.confidence,
        notes=result.notes,
    )
    session.add(record)
    session.flush()

    stored = session.execute(select(PublicRecord)).scalars().one()
    assert stored.confidence == "low"
    assert "disabled" in stored.notes.lower()


def test_mls_credentials_missing(monkeypatch) -> None:
    monkeypatch.delenv("MLS_GRID_BASE_URL", raising=False)
    monkeypatch.delenv("MLS_GRID_TOKEN", raising=False)
    adapter = MLSGridAdapter()

    try:
        adapter.fetch_listing("abc")
    except MLSGridCredentialsMissing as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError("Expected MLSGridCredentialsMissing")


def test_fair_housing_guardrail_checks() -> None:
    assert guardrail_violations("Do not discuss racial composition.")
    assert not guardrail_violations("Shorter commute to daycare option A and closer to parks.")
