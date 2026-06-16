from __future__ import annotations

import json

from realestate.models import Listing, PublicRecord
from realestate.schemas import ScoreComponent
from realestate.scoring.explanations import component, fact, keyword_hits


def score_risk(listing: Listing, preferences: dict, public_records: list[PublicRecord]) -> ScoreComponent:
    score = 82.0
    positive: list[str] = []
    negative: list[str] = []
    missing: list[str] = []
    facts = [fact("public_record_count", len(public_records), "database")]

    required_listing_fields = [
        ("list_price", listing.list_price),
        ("beds", listing.beds),
        ("baths", listing.baths),
        ("finished_sqft", listing.finished_sqft),
        ("lot_size_sqft", listing.lot_size_sqft),
        ("year_built", listing.year_built),
        ("garage_spaces", listing.garage_spaces),
        ("annual_taxes", listing.annual_taxes),
    ]
    for field_name, value in required_listing_fields:
        if value is None:
            missing.append(f"{field_name} missing.")
            score -= 3

    risk_hits = keyword_hits(listing.description or "", preferences.get("risk_keywords", []))
    for hit in risk_hits[:8]:
        negative.append(f"Risk keyword '{hit}' appears in listing language; verify with disclosures/inspection.")
    score -= min(30, len(risk_hits) * 5)

    if not public_records:
        missing.append("No public records stored yet.")
        score -= 8
    else:
        high_or_medium = [record for record in public_records if record.confidence in {"high", "medium"}]
        if high_or_medium:
            positive.append("At least one medium/high-confidence public record is available.")
            score += 4
        skipped = [record for record in public_records if record.parsed_json and "skipped" in record.parsed_json]
        if skipped:
            negative.append("Some public-record adapters were skipped; missing facts should be verified.")
            score -= 2
        score += _apply_public_record_risk_signals(public_records, positive, negative, missing, facts)

    if listing.year_built and listing.year_built < 1940:
        negative.append("Older home with unknown update history increases diligence burden.")
        score -= 5
    if listing.description and "no basement photos" in listing.description.lower():
        negative.append("Missing basement photos should be treated as a tour/disclosure question.")
        score -= 8
    if listing.annual_taxes and listing.list_price and listing.annual_taxes / listing.list_price > 0.018:
        negative.append("Tax burden looks high relative to list price.")
        score -= 4

    if not negative:
        positive.append("No major listing-language risk flags detected.")

    return component(score, positive, negative, missing, facts)


def _apply_public_record_risk_signals(
    public_records: list[PublicRecord],
    positive: list[str],
    negative: list[str],
    missing: list[str],
    facts: list[dict],
) -> float:
    delta = 0.0
    seen = {record.record_type for record in public_records}
    if "flood_zone" not in seen:
        missing.append("FEMA flood-zone lookup has not run yet.")
    if "traffic_volume" not in seen:
        missing.append("Traffic-volume lookup has not run yet.")
    if "environmental_site_proximity" not in seen:
        missing.append("Environmental-site proximity lookup has not run yet.")
    for record in public_records:
        parsed = _parsed_record(record)
        status = parsed.get("status")
        data = parsed.get("data") or {}
        if record.record_type == "flood_zone" and status == "found":
            zone = data.get("flood_zone")
            subtype = data.get("zone_subtype")
            sfha = str(data.get("special_flood_hazard_area") or "").upper()
            facts.append(fact("fema_flood_zone", {"zone": zone, "subtype": subtype}, record.source_name))
            if sfha == "T" or zone in {"A", "AE", "AH", "AO", "VE", "V"}:
                negative.append(f"FEMA flood-zone screen indicates zone {zone}; verify insurance and disclosure impact.")
                delta -= 12
            elif zone:
                positive.append(f"FEMA flood-zone screen found zone {zone} ({subtype or 'no subtype'}).")
                delta += 3
        elif record.record_type == "traffic_volume" and status == "found":
            volume = _as_int(data.get("highest_current_volume"))
            facts.append(fact("mndot_highest_nearby_aadt", volume, record.source_name))
            if volume and volume >= 15000:
                negative.append(f"Nearby MnDOT traffic volume is high ({volume:,} AADT); verify road-noise exposure.")
                delta -= 7
            elif volume and volume >= 8000:
                negative.append(f"Nearby MnDOT traffic volume is moderate ({volume:,} AADT); listen for road noise.")
                delta -= 3
            elif volume:
                positive.append(f"Nearby MnDOT traffic volume screen did not find a high-volume segment ({volume:,} AADT max nearby).")
                delta += 1
        elif record.record_type == "environmental_site_proximity" and status == "found":
            count = _as_int(data.get("site_count")) or 0
            active_count = _as_int(data.get("active_site_count")) or 0
            facts.append(
                fact(
                    "mpca_nearby_environmental_sites",
                    {"site_count": count, "active_site_count": active_count},
                    record.source_name,
                )
            )
            if active_count:
                negative.append(
                    f"MPCA proximity screen found {active_count} active site(s) nearby; review categories and distance."
                )
                delta -= min(8, active_count * 2)
            elif count:
                negative.append(
                    f"MPCA proximity screen found {count} nearby site(s); treat as a diligence check, not a defect."
                )
                delta -= 1
    return delta


def _parsed_record(record: PublicRecord) -> dict:
    if not record.parsed_json:
        return {}
    try:
        return json.loads(record.parsed_json)
    except json.JSONDecodeError:
        return {}


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
