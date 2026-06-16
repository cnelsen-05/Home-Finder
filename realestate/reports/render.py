from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from realestate.models import (
    AmenityDistance,
    CommuteEstimate,
    Favorite,
    IssueFlag,
    LifeAnchor,
    Listing,
    ParcelRecord,
    PublicRecord,
    Report,
    ReviewScore,
    SaleHistoryRecord,
    SavedNeighborhood,
    TaxRecord,
)
from realestate.neighborhoods import neighborhood_report_context, property_neighborhood_context
from realestate.parsing.address_parser import join_address
from realestate.parsing.price_parser import price_per_sqft
from realestate.paths import (
    AGENT_QUESTIONS_DIR,
    COMPARISON_REPORTS_DIR,
    DAILY_REPORTS_DIR,
    FAVORITE_REPORTS_DIR,
    HTML_REPORTS_DIR,
    NEIGHBORHOOD_REPORTS_DIR,
    TOUR_CHECKLISTS_DIR,
    WEEKLY_REPORTS_DIR,
)
from realestate.school_zones import identify_property_elementary_zone
from realestate.scoring.guardrails import assert_guardrail_safe
from realestate.scoring.overall import explanation_from_score, latest_score, score_listing

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def render_favorite_review(session: Session, listing_id: int, preferences: dict) -> Path:
    listing = _get_listing(session, listing_id)
    score_row = latest_score(session, listing) or score_listing(session, listing, preferences)
    explanation = explanation_from_score(score_row)
    public_records = _public_records(session, listing.property_id)
    parcel_records = _parcel_records(session, listing.property_id)
    tax_records = _tax_records(session, listing.property_id)
    issue_flags = _issue_flags(session, listing.id)
    context = _base_context(
        listing,
        score_row,
        explanation,
        public_records,
        issue_flags,
        parcel_records,
        tax_records,
        _amenity_distances(session, listing.property_id),
        _commute_summaries(session, listing.property_id),
        _sale_history_records(session, listing.property_id),
        **_listing_map_context(session, listing),
    )
    path = FAVORITE_REPORTS_DIR / f"{_listing_slug(listing)}_review.md"
    _write_template("favorite_home_review.md.j2", context, path)
    _record_report(session, "favorite_home_review", listing.id, path, explanation["recommendation_summary"])
    return path


def render_all_favorite_reviews(session: Session, preferences: dict) -> list[Path]:
    favorites = session.execute(select(Favorite).where(Favorite.listing_id.is_not(None))).scalars().all()
    paths = []
    for favorite in favorites:
        if favorite.listing_id is not None:
            paths.append(render_favorite_review(session, favorite.listing_id, preferences))
    return paths


def render_comparison_report(session: Session, preferences: dict) -> Path:
    rows = _comparison_rows(session, preferences)
    context = {
        "rows": rows,
        "neighborhood_rows": _neighborhood_rows(session),
        "today": date.today().isoformat(),
        "bucket_label": bucket_label,
    }
    path = COMPARISON_REPORTS_DIR / f"favorite_homes_comparison_{date.today().isoformat()}.md"
    _write_template("comparison_report.md.j2", context, path)
    _record_report(session, "comparison_report", None, path, f"{len(rows)} homes compared.")
    return path


def render_pilot_report(session: Session, listing_ids: list[int], preferences: dict) -> Path:
    rows = _comparison_rows_for_listings(session, listing_ids, preferences)
    context = {"rows": rows, "today": date.today().isoformat(), "bucket_label": bucket_label}
    path = COMPARISON_REPORTS_DIR / f"pilot_analysis_first_{len(rows)}_{date.today().isoformat()}.md"
    _write_template("pilot_report.md.j2", context, path)
    _record_report(session, "pilot_report", None, path, f"{len(rows)} pilot homes analyzed.")
    return path


def render_pilot_report_html(session: Session, listing_ids: list[int], preferences: dict) -> Path:
    rows = _comparison_rows_for_listings(session, listing_ids, preferences)
    context = {"rows": rows, "today": date.today().isoformat(), "bucket_label": bucket_label}
    path = HTML_REPORTS_DIR / f"pilot_analysis_first_{len(rows)}_{date.today().isoformat()}.html"
    _write_template("pilot_report.html.j2", context, path)
    _record_report(session, "pilot_report_html", None, path, f"{len(rows)} pilot homes analyzed.")
    return path


def render_agent_questions(session: Session, listing_id: int, preferences: dict) -> Path:
    listing = _get_listing(session, listing_id)
    score_row = latest_score(session, listing) or score_listing(session, listing, preferences)
    explanation = explanation_from_score(score_row)
    context = _base_context(
        listing,
        score_row,
        explanation,
        _public_records(session, listing.property_id),
        _issue_flags(session, listing.id),
        _parcel_records(session, listing.property_id),
        _tax_records(session, listing.property_id),
        _amenity_distances(session, listing.property_id),
        _commute_summaries(session, listing.property_id),
        _sale_history_records(session, listing.property_id),
        **_listing_map_context(session, listing),
    )
    path = AGENT_QUESTIONS_DIR / f"{_listing_slug(listing)}_agent_questions.md"
    _write_template("agent_questions.md.j2", context, path)
    _record_report(session, "agent_questions", listing.id, path, "Agent questions generated.")
    return path


def render_tour_checklist(session: Session, listing_id: int, preferences: dict) -> Path:
    listing = _get_listing(session, listing_id)
    score_row = latest_score(session, listing) or score_listing(session, listing, preferences)
    explanation = explanation_from_score(score_row)
    context = _base_context(
        listing,
        score_row,
        explanation,
        _public_records(session, listing.property_id),
        _issue_flags(session, listing.id),
        _parcel_records(session, listing.property_id),
        _tax_records(session, listing.property_id),
        _amenity_distances(session, listing.property_id),
        _commute_summaries(session, listing.property_id),
        _sale_history_records(session, listing.property_id),
        **_listing_map_context(session, listing),
    )
    path = TOUR_CHECKLISTS_DIR / f"{_listing_slug(listing)}_tour_checklist.md"
    _write_template("tour_checklist.md.j2", context, path)
    _record_report(session, "tour_checklist", listing.id, path, "Tour checklist generated.")
    return path


def render_daily_report(session: Session, preferences: dict) -> Path:
    rows = _comparison_rows(session, preferences)
    path = DAILY_REPORTS_DIR / f"daily_real_estate_brief_{date.today().isoformat()}.md"
    context = {"rows": rows, "today": date.today().isoformat(), "bucket_label": bucket_label}
    _write_template("daily_report.md.j2", context, path)
    _record_report(session, "daily_report", None, path, f"{len(rows)} tracked homes summarized.")
    return path


def render_weekly_report(session: Session, preferences: dict) -> Path:
    rows = _comparison_rows(session, preferences)
    path = WEEKLY_REPORTS_DIR / f"weekly_real_estate_summary_{date.today().isoformat()}.md"
    context = {"rows": rows, "today": date.today().isoformat(), "bucket_label": bucket_label}
    _write_template("weekly_report.md.j2", context, path)
    _record_report(session, "weekly_report", None, path, f"{len(rows)} tracked homes summarized.")
    return path


def render_neighborhood_report(session: Session, neighborhood_id: int) -> Path:
    neighborhood = session.get(SavedNeighborhood, neighborhood_id)
    if neighborhood is None:
        raise ValueError(f"Saved neighborhood {neighborhood_id} not found.")
    context = neighborhood_report_context(session, neighborhood)
    context["today"] = date.today().isoformat()
    path = NEIGHBORHOOD_REPORTS_DIR / f"{_neighborhood_slug(neighborhood)}_report.md"
    _write_template("neighborhood_area_report.md.j2", context, path)
    _record_report(session, "neighborhood_area_report", None, path, neighborhood.name)
    return path


def render_all_neighborhood_reports(session: Session) -> list[Path]:
    neighborhoods = session.execute(
        select(SavedNeighborhood).order_by(SavedNeighborhood.rating, SavedNeighborhood.name)
    ).scalars().all()
    return [render_neighborhood_report(session, neighborhood.id) for neighborhood in neighborhoods]


def _base_context(
    listing: Listing,
    score_row: ReviewScore,
    explanation: dict[str, Any],
    public_records: list[PublicRecord],
    issue_flags: list[IssueFlag],
    parcel_records: list[ParcelRecord] | None = None,
    tax_records: list[TaxRecord] | None = None,
    amenity_distances: list[AmenityDistance] | None = None,
    commute_estimates: list[dict[str, Any]] | None = None,
    sale_history_records: list[SaleHistoryRecord] | None = None,
    elementary_zone: dict[str, Any] | None = None,
    neighborhood_matches: list[dict[str, Any]] | None = None,
    highlight_matches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    prop = listing.property
    parcel_records = parcel_records or []
    tax_records = tax_records or []
    amenity_distances = amenity_distances or []
    commute_estimates = commute_estimates or []
    sale_history_records = sale_history_records or []
    maintenance_context = _latest_record_data(public_records, "maintenance_roof_context")
    market_context = _latest_record_data(public_records, "market_heat_context")
    community_context = _latest_record_data(public_records, "regional_community_context")
    storm_context = _latest_record_data(public_records, "storm_event_context")
    return {
        "listing": listing,
        "property": prop,
        "address": join_address(prop.address_line1, prop.city, prop.state, prop.zip),
        "score": score_row,
        "explanation": explanation,
        "public_records": public_records,
        "source_highlights": _public_record_highlights(public_records),
        "source_links": _public_record_sources(public_records),
        "elementary_zone": elementary_zone,
        "neighborhood_matches": neighborhood_matches or [],
        "highlight_matches": highlight_matches or [],
        "amenity_distances": amenity_distances,
        "montessori_options": _prioritized_childcare(amenity_distances, montessori_only=True),
        "childcare_options": _prioritized_childcare(amenity_distances),
        "commute_estimates": commute_estimates,
        "sale_history_records": sale_history_records,
        "latest_sale_read": _latest_sale_read(listing.list_price, sale_history_records),
        "maintenance_context": maintenance_context,
        "market_context": market_context,
        "community_context": community_context,
        "storm_context": storm_context,
        "parcel_records": parcel_records,
        "tax_records": tax_records,
        "latest_parcel_record": parcel_records[0] if parcel_records else None,
        "latest_tax_record": tax_records[0] if tax_records else None,
        "issue_flags": issue_flags,
        "price_per_sqft": price_per_sqft(listing.list_price, listing.finished_sqft),
        "final_recommendation": final_recommendation(score_row.recommendation_bucket),
        "bucket_label": bucket_label(score_row.recommendation_bucket),
    }


def _comparison_rows(session: Session, preferences: dict) -> list[dict[str, Any]]:
    listings = session.execute(select(Listing)).scalars().all()
    return _comparison_rows_from_listings(session, listings, preferences)


def _comparison_rows_for_listings(
    session: Session, listing_ids: list[int], preferences: dict
) -> list[dict[str, Any]]:
    listings = [
        listing
        for listing_id in listing_ids
        if (listing := session.get(Listing, listing_id)) is not None
    ]
    return _comparison_rows_from_listings(session, listings, preferences)


def _comparison_rows_from_listings(
    session: Session, listings: list[Listing], preferences: dict
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for listing in listings:
        score_row = latest_score(session, listing) or score_listing(session, listing, preferences)
        explanation = explanation_from_score(score_row)
        prop = listing.property
        public_records = _public_records(session, listing.property_id)
        amenities = _amenity_distances(session, listing.property_id)
        parcel_records = _parcel_records(session, listing.property_id)
        tax_records = _tax_records(session, listing.property_id)
        sale_history_records = _sale_history_records(session, listing.property_id)
        maintenance_context = _latest_record_data(public_records, "maintenance_roof_context")
        market_context = _latest_record_data(public_records, "market_heat_context")
        community_context = _latest_record_data(public_records, "regional_community_context")
        storm_context = _latest_record_data(public_records, "storm_event_context")
        rows.append(
            {
                "listing": listing,
                "property": prop,
                "address": join_address(prop.address_line1, prop.city, prop.state, prop.zip),
                "score": score_row,
                "explanation": explanation,
                "bucket": score_row.recommendation_bucket,
                "summary": explanation["recommendation_summary"],
                "top_missing": explanation["overall"]["missing_data"][:4],
                "top_questions": _top_questions(explanation),
                "source_highlights": _public_record_highlights(public_records),
                "source_links": _public_record_sources(public_records),
                **_listing_map_context(session, listing),
                "amenity_distances": amenities,
                "montessori_options": _prioritized_childcare(
                    amenities, montessori_only=True
                ),
                "childcare_options": _prioritized_childcare(amenities),
                "commute_estimates": _commute_summaries(session, listing.property_id),
                "sale_history_records": sale_history_records,
                "latest_sale_read": _latest_sale_read(listing.list_price, sale_history_records),
                "maintenance_context": maintenance_context,
                "market_context": market_context,
                "community_context": community_context,
                "storm_context": storm_context,
                "price_per_sqft": price_per_sqft(listing.list_price, listing.finished_sqft),
                "latest_parcel_record": parcel_records[0] if parcel_records else None,
                "latest_tax_record": tax_records[0] if tax_records else None,
            }
        )
    return sorted(rows, key=lambda row: row["score"].overall_score, reverse=True)


def _listing_map_context(session: Session, listing: Listing) -> dict[str, Any]:
    lookup = identify_property_elementary_zone(session, listing.property)
    zone_payload = lookup.as_dict() if lookup else None
    if zone_payload:
        from realestate.schools import enrich_school_zone_payload

        zone_payload = enrich_school_zone_payload(session, zone_payload)
    from realestate.map_highlights import property_highlight_context

    return {
        "elementary_zone": zone_payload,
        "neighborhood_matches": property_neighborhood_context(session, listing.property_id),
        "highlight_matches": property_highlight_context(session, listing.property_id),
    }


def _neighborhood_rows(session: Session) -> list[dict[str, Any]]:
    neighborhoods = session.execute(select(SavedNeighborhood)).scalars().all()
    rows = []
    for neighborhood in neighborhoods:
        context = neighborhood_report_context(session, neighborhood)
        rows.append(
            {
                **context,
                "homes_inside_count": len(context["homes_inside"]),
                "homes_nearby_count": len(context["homes_nearby"]),
                "zone_count": len(context["elementary_zones"]),
            }
        )
    rating_order = {"favorite": 0, "strong_like": 1, "like": 2, "maybe": 3, "avoid": 4}
    return sorted(
        rows,
        key=lambda row: (
            rating_order.get(row["neighborhood"].rating, 9),
            -(row["homes_inside_count"] + row["homes_nearby_count"]),
            row["neighborhood"].name,
        ),
    )


def _top_questions(explanation: dict[str, Any]) -> list[str]:
    questions = []
    for message in explanation["risk"]["negative_drivers"][:3]:
        if "public-record adapters were skipped" in message.lower():
            continue
        questions.append(_question_from_driver(message))
    for missing in explanation["overall"]["missing_data"][:3]:
        questions.append(f"Can you verify: {missing}")
    return questions[:5]


def _question_from_driver(driver: str) -> str:
    if "sewer" in driver.lower():
        return "Has the sewer line been scoped recently, and are records available?"
    if "basement" in driver.lower() or "water" in driver.lower():
        return "Are there any disclosure notes or history of water intrusion, seepage, or drain tile work?"
    if "tax" in driver.lower():
        return "What are the current and proposed annual taxes, and are assessments changing?"
    if "price" in driver.lower():
        return "What comparable sales justify the current asking price?"
    return f"What documentation addresses this concern: {driver}"


def _public_records(session: Session, property_id: int) -> list[PublicRecord]:
    return session.execute(
        select(PublicRecord)
        .where(PublicRecord.property_id == property_id)
        .order_by(PublicRecord.retrieved_at.desc())
    ).scalars().all()


def _amenity_distances(session: Session, property_id: int) -> list[AmenityDistance]:
    return session.execute(
        select(AmenityDistance)
        .where(AmenityDistance.property_id == property_id)
        .order_by(AmenityDistance.amenity_type, AmenityDistance.distance_miles)
    ).scalars().all()


def _commute_estimates(session: Session, property_id: int) -> list[CommuteEstimate]:
    return session.execute(
        select(CommuteEstimate)
        .where(CommuteEstimate.property_id == property_id)
        .order_by(CommuteEstimate.duration_minutes)
    ).scalars().all()


def _commute_summaries(session: Session, property_id: int) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for estimate in _commute_estimates(session, property_id):
        anchor = session.get(LifeAnchor, estimate.anchor_id)
        summaries.append(
            {
                "anchor_name": anchor.name if anchor else f"Anchor {estimate.anchor_id}",
                "anchor_category": anchor.category if anchor else "unknown",
                "distance_miles": estimate.distance_miles,
                "duration_minutes": estimate.duration_minutes,
                "source_name": estimate.source_name,
                "time_of_day": estimate.time_of_day,
            }
        )
    return summaries


def _sale_history_records(session: Session, property_id: int) -> list[SaleHistoryRecord]:
    return session.execute(
        select(SaleHistoryRecord)
        .where(SaleHistoryRecord.property_id == property_id)
        .order_by(SaleHistoryRecord.id.desc())
    ).scalars().all()


def _parcel_records(session: Session, property_id: int) -> list[ParcelRecord]:
    return session.execute(
        select(ParcelRecord).where(ParcelRecord.property_id == property_id).order_by(ParcelRecord.id.desc())
    ).scalars().all()


def _tax_records(session: Session, property_id: int) -> list[TaxRecord]:
    return session.execute(
        select(TaxRecord).where(TaxRecord.property_id == property_id).order_by(TaxRecord.id.desc())
    ).scalars().all()


def _issue_flags(session: Session, listing_id: int) -> list[IssueFlag]:
    return session.execute(
        select(IssueFlag).where(IssueFlag.listing_id == listing_id).order_by(IssueFlag.severity.desc())
    ).scalars().all()


def _get_listing(session: Session, listing_id: int) -> Listing:
    listing = session.get(Listing, listing_id)
    if listing is None:
        raise ValueError(f"Listing {listing_id} not found.")
    return listing


def _write_template(template_name: str, context: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(default_for_string=False, enabled_extensions=()),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["money"] = money
    env.filters["score"] = score_filter
    env.filters["bucket_label"] = bucket_label
    text = env.get_template(template_name).render(**context)
    assert_guardrail_safe(text)
    path.write_text(text, encoding="utf-8")


def _record_report(
    session: Session, report_type: str, listing_id: int | None, path: Path, summary: str
) -> None:
    session.add(
        Report(
            report_type=report_type,
            listing_id=listing_id,
            path=str(path),
            summary=summary,
        )
    )


def _listing_slug(listing: Listing) -> str:
    base = listing.property.normalized_address or f"listing_{listing.id}"
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", base.lower()).strip("_")
    return f"{slug}_{listing.id}"


def _neighborhood_slug(neighborhood: SavedNeighborhood) -> str:
    base = neighborhood.name or f"saved_neighborhood_{neighborhood.id}"
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", base.lower()).strip("_")
    return f"{slug}_{neighborhood.id}"


def money(value: float | int | None) -> str:
    if value is None:
        return "Unknown"
    return f"${value:,.0f}"


def score_filter(value: float | int | None) -> str:
    if value is None:
        return "Unknown"
    return f"{float(value):.1f}/100"


def bucket_label(bucket: str) -> str:
    return bucket.replace("_", " ").title()


def final_recommendation(bucket: str) -> str:
    return {
        "must_review": "Tour immediately",
        "strong_tour_candidate": "Tour immediately",
        "worth_reviewing": "Ask agent questions first",
        "watch": "Watch for price reduction",
        "agent_question_first": "Ask agent questions first",
        "low_priority": "Keep as backup",
        "likely_skip": "Skip unless price changes",
    }.get(bucket, "Ask agent questions first")


def _public_record_highlights(public_records: list[PublicRecord]) -> list[str]:
    highlights: list[str] = []
    for record in public_records:
        parsed = _parsed_public_record(record)
        status = parsed.get("status")
        data = parsed.get("data") or {}
        if status not in {"found", "manual_review_required"}:
            continue
        if record.record_type == "geocode":
            matched = data.get("matched_address")
            if matched:
                highlights.append(f"US Census matched address: {matched}.")
        elif record.record_type == "parcel_gis":
            feature_count = data.get("feature_count")
            method = data.get("match_method")
            if feature_count:
                highlights.append(f"Parcel GIS matched {feature_count} feature(s) via {method or 'public GIS lookup'}.")
        elif record.record_type == "school_district_boundary":
            district = data.get("district_name")
            if district:
                highlights.append(f"Official school-boundary lookup: {district}.")
        elif record.record_type == "flood_zone":
            zone = data.get("flood_zone")
            subtype = data.get("zone_subtype")
            if zone:
                highlights.append(f"FEMA flood zone: {zone}{' - ' + subtype if subtype else ''}.")
        elif record.record_type == "traffic_volume":
            volume = data.get("highest_current_volume")
            radius = data.get("radius_meters")
            if volume:
                highlights.append(f"MnDOT nearby traffic screen: max {int(volume):,} AADT within {radius} m.")
        elif record.record_type == "environmental_site_proximity":
            count = data.get("site_count")
            active = data.get("active_site_count")
            if count is not None:
                highlights.append(f"MPCA environmental screen: {count} nearby site(s), {active or 0} active.")
        elif record.record_type == "amenity_distance":
            count = data.get("amenity_count")
            if count:
                highlights.append(f"OSM amenity screen found {count} tagged nearby amenities.")
        elif record.record_type == "municipal_permits":
            count = data.get("permit_count")
            if count is not None:
                highlights.append(f"Minneapolis permit lookup found {count} matching permit record(s).")
        elif record.record_type == "listing_discovery":
            price = data.get("list_price")
            beds = data.get("beds")
            baths = data.get("baths")
            sqft = data.get("finished_sqft")
            status = data.get("status")
            mls = data.get("mls_number")
            pieces = []
            if status:
                pieces.append(str(status).replace("_", " "))
            if price:
                pieces.append(money(price))
            if beds and baths:
                pieces.append(f"{beds:g} bed / {baths:g} bath")
            if sqft:
                pieces.append(f"{int(sqft):,} sqft")
            if mls:
                pieces.append(f"MLS {mls}")
            if pieces:
                highlights.append("Recent listing discovery: " + ", ".join(pieces) + ".")
            notes = data.get("notes") or record.notes
            if notes:
                highlights.append(f"Listing discovery note: {notes}")
        elif record.record_type == "maintenance_roof_context":
            roof_summary = data.get("roof_summary")
            if roof_summary:
                highlights.append(f"Roof/maintenance signal: {roof_summary}")
        elif record.record_type == "market_heat_context":
            label = data.get("market_label")
            change = data.get("zhvi_12m_change_pct")
            if label and change is not None:
                highlights.append(f"ZIP market trend: {label}; Zillow ZHVI 12-month change {change}%.")
        elif record.record_type == "regional_community_context":
            tract = data.get("tract_geoid")
            owner_pct = data.get("owner_occupied_pct")
            older_pct = data.get("housing_built_1979_or_earlier_pct")
            if tract and owner_pct is not None:
                highlights.append(
                    f"ACS community context tract {tract}: {owner_pct}% owner-occupied; "
                    f"{older_pct}% housing built 1979 or earlier."
                )
        elif record.record_type == "storm_event_context":
            count = data.get("severe_event_count_2024_2025")
            county = data.get("county")
            if count is not None and county:
                highlights.append(
                    f"NOAA storm screen: {count} county-level hail/wind/tornado event(s) in {county} during 2024-2025."
                )
        elif record.record_type == "daycare_montessori_discovery":
            options = data.get("options") or []
            if options:
                nearest = options[0]
                highlights.append(
                    "Montessori/daycare discovery: "
                    f"{nearest.get('name')} ({nearest.get('distance_miles')} mi, {nearest.get('license_status')})."
                )
    return highlights[:12]


def _latest_record_data(public_records: list[PublicRecord], record_type: str) -> dict[str, Any]:
    for record in public_records:
        if record.record_type != record_type:
            continue
        parsed = _parsed_public_record(record)
        if parsed.get("status") not in {"found", "manual_review_required"}:
            continue
        data = parsed.get("data")
        return data if isinstance(data, dict) else {}
    return {}


def _public_record_sources(public_records: list[PublicRecord]) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for record in public_records:
        if not record.source_url:
            continue
        key = (record.source_name, record.source_url, record.record_type)
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            {
                "source_name": record.source_name,
                "source_url": record.source_url,
                "record_type": record.record_type,
                "confidence": record.confidence,
            }
        )
    return sources[:12]


def _latest_sale_read(
    list_price: float | None, sale_history_records: list[SaleHistoryRecord]
) -> dict[str, Any] | None:
    if not list_price or not sale_history_records:
        return None
    sale = sale_history_records[0]
    if not sale.sale_price:
        return None
    age_years = _sale_age_years(sale.sale_date)
    delta = list_price - sale.sale_price
    return {
        "sale_date": sale.sale_date,
        "sale_price": sale.sale_price,
        "delta": delta,
        "abs_delta": abs(delta),
        "direction": "above" if delta > 0 else "below",
        "age_years": round(age_years, 1) if age_years is not None else None,
        "is_stale": age_years is None or age_years > 5,
    }


def _sale_age_years(sale_date: str | None) -> float | None:
    if not sale_date:
        return None
    try:
        parsed = date.fromisoformat(sale_date)
    except ValueError:
        return None
    return (date.today() - parsed).days / 365.25


def _prioritized_childcare(
    amenities: list[AmenityDistance], montessori_only: bool = False
) -> list[AmenityDistance]:
    childcare_types = {"childcare", "kindergarten", "school"}
    candidates = [
        amenity
        for amenity in amenities
        if amenity.amenity_type in childcare_types
        and amenity.amenity_name
        and (not montessori_only or "montessori" in amenity.amenity_name.lower())
    ]
    return sorted(
        candidates,
        key=lambda amenity: (
            0 if "montessori" in (amenity.amenity_name or "").lower() else 1,
            amenity.distance_miles if amenity.distance_miles is not None else 99,
        ),
    )


def _parsed_public_record(record: PublicRecord) -> dict[str, Any]:
    if not record.parsed_json:
        return {}
    try:
        return json.loads(record.parsed_json)
    except json.JSONDecodeError:
        return {}
