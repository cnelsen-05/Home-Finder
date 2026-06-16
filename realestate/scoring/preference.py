from __future__ import annotations

from datetime import UTC, datetime

from realestate.models import Listing
from realestate.schemas import ScoreComponent
from realestate.scoring.explanations import component, fact, keyword_hits


def score_preference(listing: Listing, preferences: dict) -> ScoreComponent:
    score = 60.0
    positive: list[str] = []
    negative: list[str] = []
    missing: list[str] = []
    facts = [
        fact("city", listing.property.city),
        fact("property_type", listing.property_type),
        fact("beds", listing.beds),
        fact("baths", listing.baths),
        fact("finished_sqft", listing.finished_sqft),
        fact("lot_size_sqft", listing.lot_size_sqft),
    ]

    home_requirements = preferences.get("home_requirements", {})
    budget = preferences.get("budget", {})
    target = float(budget.get("target_max_price") or 750000)

    if listing.list_price is None:
        missing.append("Price missing; budget fit unknown.")
    elif listing.list_price <= target:
        positive.append("Price fits configured target maximum.")
        score += 8
    else:
        negative.append("Price is above configured target maximum.")
        score -= 12

    allowed_types = {item.lower() for item in home_requirements.get("allowed_property_types", [])}
    property_type = (listing.property_type or "").lower()
    if allowed_types and not property_type:
        missing.append("Property type missing; single-family requirement cannot be verified.")
        score -= 4
    elif allowed_types and property_type not in allowed_types:
        negative.append("Property type does not match the single-family-only requirement.")
        score -= 30
    elif allowed_types:
        positive.append("Property type matches the single-family-only requirement.")
        score += 8

    _score_minimum("beds", listing.beds, home_requirements.get("min_beds"), positive, negative)
    score += _minimum_delta(listing.beds, home_requirements.get("min_beds"))
    _score_minimum("baths", listing.baths, home_requirements.get("min_baths"), positive, negative)
    score += _minimum_delta(listing.baths, home_requirements.get("min_baths"))
    _score_minimum(
        "finished sqft",
        listing.finished_sqft,
        home_requirements.get("min_finished_sqft"),
        positive,
        negative,
    )
    score += _minimum_delta(listing.finished_sqft, home_requirements.get("min_finished_sqft"))
    _score_minimum(
        "lot size",
        listing.lot_size_sqft,
        home_requirements.get("min_lot_size_sqft"),
        positive,
        negative,
    )
    score += _minimum_delta(listing.lot_size_sqft, home_requirements.get("min_lot_size_sqft"))

    if home_requirements.get("garage_required") and listing.garage_spaces is None:
        missing.append("Garage requirement configured, but listing garage data is missing.")
        score -= 3
    elif home_requirements.get("garage_required"):
        min_spaces = float(home_requirements.get("min_garage_spaces") or 1)
        if listing.garage_spaces and listing.garage_spaces >= min_spaces:
            positive.append("Garage meets configured requirement.")
            score += 8
        else:
            negative.append("Garage appears below configured requirement.")
            score -= 14

    if home_requirements.get("new_construction_interest") is False:
        max_age = int(home_requirements.get("max_new_construction_years_old") or 3)
        if listing.year_built is None:
            missing.append("Year built missing; new-construction preference cannot be verified.")
        elif listing.year_built >= datetime.now(UTC).year - max_age:
            negative.append("New construction or near-new home does not match current preference.")
            score -= 12

    desired_hits = keyword_hits(listing.description or "", preferences.get("desired_features", []))
    negative_hits = keyword_hits(listing.description or "", preferences.get("negative_features", []))
    score += min(12, len(desired_hits) * 2)
    score -= min(16, len(negative_hits) * 4)
    for hit in desired_hits[:5]:
        positive.append(f"Matches desired feature: {hit}.")
    for hit in negative_hits[:5]:
        negative.append(f"Matches negative feature: {hit}.")

    return component(score, positive, negative, missing, facts)


def _score_minimum(
    label: str,
    value: float | None,
    minimum: float | int | None,
    positive: list[str],
    negative: list[str],
) -> None:
    if minimum is None:
        return
    if value is not None and value >= float(minimum):
        positive.append(f"{label.title()} meets configured minimum.")
    else:
        negative.append(f"{label.title()} does not clearly meet configured minimum.")


def _minimum_delta(value: float | None, minimum: float | int | None) -> float:
    if minimum is None:
        return 0
    if value is not None and value >= float(minimum):
        return 5
    return -10
