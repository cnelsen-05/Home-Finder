from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from realestate.models import AmenityDistance, CommuteEstimate, LifeAnchor, Listing, ParcelRecord
from realestate.schemas import ScoreComponent
from realestate.scoring.explanations import component, fact, keyword_hits


def score_daily_life(listing: Listing, preferences: dict, session: Session) -> ScoreComponent:
    score = 64.0
    positive: list[str] = []
    negative: list[str] = []
    missing: list[str] = []
    prop = listing.property
    parcel_lot_size = _best_public_lot_size(session, prop.id)
    lot_size = listing.lot_size_sqft or parcel_lot_size
    facts = [
        fact("city", prop.city),
        fact("garage_spaces", listing.garage_spaces),
        fact("lot_size_sqft", lot_size, "listing_import" if listing.lot_size_sqft else "public_parcel"),
    ]
    locations = preferences.get("locations", {})
    daily = preferences.get("daily_life", {})
    preferred = {city.lower() for city in locations.get("preferred_cities", [])}
    secondary = {city.lower() for city in locations.get("secondary_cities", [])}
    excluded = {city.lower() for city in locations.get("excluded_cities", [])}
    city = (prop.city or "").lower()

    if not city:
        missing.append("City missing; location preference fit is unknown.")
    elif city in excluded:
        negative.append("City is in the configured excluded list.")
        score -= 30
    elif city in preferred:
        positive.append("City is in the configured preferred list.")
        score += 10
    elif city in secondary:
        positive.append("City is in the configured secondary list.")
        score += 5
    elif locations.get("infer_preferred_locations_from_favorites") and not preferred:
        missing.append("Location preferences are set to be inferred from favorited homes.")
    else:
        negative.append("City is outside configured preferred and secondary lists.")
        score -= 5

    if listing.garage_spaces is not None and listing.garage_spaces >= 2:
        positive.append("Garage setup should help with winter mornings and storage.")
        score += 6
    elif listing.garage_spaces is None:
        missing.append("Garage information missing; winter practicality is uncertain.")
    else:
        negative.append("Garage setup may add daily-life friction in winter.")
        score -= 5

    desc = listing.description or ""
    desired_hits = keyword_hits(desc, preferences.get("desired_features", []))
    negative_hits = keyword_hits(desc, preferences.get("negative_features", []))
    for hit in desired_hits[:5]:
        positive.append(f"Listing mentions desired feature: {hit}.")
    for hit in negative_hits[:5]:
        negative.append(f"Listing mentions negative feature: {hit}; verify practical impact.")
    score += min(8, len(desired_hits) * 2)
    score -= min(14, len(negative_hits) * 4)

    quiet_hits = keyword_hits(desc, ["quiet street", "cul-de-sac", "low road noise"])
    road_noise_hits = keyword_hits(
        desc,
        ["busy road", "freeway", "train noise", "major road noise", "awkward traffic access"],
    )
    if quiet_hits:
        positive.append("Listing language supports the quiet-street preference; verify in person.")
        score += 6
    if road_noise_hits and daily.get("quiet_street_priority") == "high":
        negative.append("Road/freeway/train or traffic-access language conflicts with quiet-street priority.")
        score -= 14

    estimates = session.execute(
        select(CommuteEstimate).where(CommuteEstimate.property_id == prop.id)
    ).scalars().all()
    anchors = session.execute(select(LifeAnchor)).scalars().all()
    if anchors and not estimates:
        missing.append(
            "Life anchors exist, but commute/amenity estimates are unavailable until addresses or geocoding are added."
        )
        score -= 4
    for estimate in estimates:
        anchor = session.get(LifeAnchor, estimate.anchor_id)
        if anchor is None or estimate.duration_minutes is None:
            continue
        facts.append(
            fact(
                f"commute_{anchor.category}_{anchor.name}",
                estimate.duration_minutes,
                estimate.source_name or "commute_estimate",
            )
        )
        _apply_commute_signal(preferences, estimate, anchor, positive, negative)
        score += _commute_delta(preferences, estimate, anchor)

    amenities = session.execute(
        select(AmenityDistance).where(AmenityDistance.property_id == prop.id)
    ).scalars().all()
    score += _apply_amenity_signals(amenities, preferences, positive, negative, missing, facts)

    if lot_size is None:
        missing.append("Lot size missing; yard usability needs verification.")
    elif lot_size >= 7500:
        positive.append("Lot size meets the configured 7,500+ sq ft yard preference.")
        score += 6
    elif lot_size >= 6000:
        positive.append("Lot size may support a usable yard; verify shape, slope, and privacy.")
        score += 1
    elif lot_size < 4000:
        negative.append("Small lot may constrain yard usability.")
        score -= 4
    else:
        negative.append("Lot size is below the configured 7,500 sq ft preference; verify yard usability.")
        score -= 4

    return component(score, positive, negative, missing, facts)


def _best_public_lot_size(session: Session, property_id: int) -> float | None:
    parcel = (
        session.execute(
            select(ParcelRecord)
            .where(ParcelRecord.property_id == property_id)
            .order_by(ParcelRecord.id.desc())
        )
        .scalars()
        .first()
    )
    return parcel.lot_size_sqft if parcel else None


def _commute_limit(preferences: dict, category: str) -> float | None:
    daily = preferences.get("daily_life", {})
    mapping = {
        "work": "max_preferred_drive_to_work_minutes",
        "daycare": "max_preferred_drive_to_daycare_minutes",
        "preschool": "max_preferred_drive_to_daycare_minutes",
        "gym": "max_preferred_drive_to_gym_minutes",
    }
    key = mapping.get(category)
    return float(daily[key]) if key and daily.get(key) is not None else None


def _commute_delta(preferences: dict, estimate: CommuteEstimate, anchor: LifeAnchor) -> float:
    limit = _commute_limit(preferences, anchor.category)
    if limit is None or estimate.duration_minutes is None:
        return 0
    daily = preferences.get("daily_life", {})
    if anchor.category == "work":
        ideal = daily.get("ideal_drive_to_work_minutes")
        hard = daily.get("hard_drive_to_work_minutes")
        if hard is not None and estimate.duration_minutes > float(hard):
            return -14
        if ideal is not None and estimate.duration_minutes <= float(ideal):
            return 7
    if estimate.duration_minutes <= limit:
        return 4 if anchor.priority == 1 else 2
    return -6 if anchor.priority == 1 else -3


def _apply_commute_signal(
    preferences: dict,
    estimate: CommuteEstimate,
    anchor: LifeAnchor,
    positive: list[str],
    negative: list[str],
) -> None:
    limit = _commute_limit(preferences, anchor.category)
    if limit is None or estimate.duration_minutes is None:
        return
    label = f"{anchor.name} ({anchor.category})"
    daily = preferences.get("daily_life", {})
    hard = daily.get("hard_drive_to_work_minutes") if anchor.category == "work" else None
    ideal = daily.get("ideal_drive_to_work_minutes") if anchor.category == "work" else None
    if hard is not None and estimate.duration_minutes > float(hard):
        negative.append(
            f"Estimated drive to {label} is {estimate.duration_minutes:.1f} minutes, above the hard commute comfort threshold."
        )
    elif ideal is not None and estimate.duration_minutes <= float(ideal):
        positive.append(
            f"Estimated drive to {label} is {estimate.duration_minutes:.1f} minutes, inside the ideal commute range."
        )
    elif estimate.duration_minutes <= limit:
        positive.append(
            f"Estimated drive to {label} is {estimate.duration_minutes:.1f} minutes, within preference."
        )
    else:
        negative.append(
            f"Estimated drive to {label} is {estimate.duration_minutes:.1f} minutes, above preference."
        )


def _apply_amenity_signals(
    amenities: list[AmenityDistance],
    preferences: dict,
    positive: list[str],
    negative: list[str],
    missing: list[str],
    facts: list[dict],
) -> float:
    if not amenities:
        missing.append("Amenity lookup has not found nearby parks, playgrounds, gyms, or childcare yet.")
        return -2
    delta = 0.0
    daily = preferences.get("daily_life", {})
    nearest_by_type = _nearest_by_type(amenities)
    for amenity_type, amenity in sorted(nearest_by_type.items()):
        facts.append(
            fact(
                f"nearest_{amenity_type}",
                {
                    "name": amenity.amenity_name,
                    "distance_miles": amenity.distance_miles,
                },
                amenity.source_name or "amenity_lookup",
            )
        )

    park = _nearest_of(nearest_by_type, {"park", "playground", "nature_reserve", "trail"})
    if daily.get("prefer_near_parks") or daily.get("prefer_near_playgrounds") or daily.get("prefer_near_trails"):
        if park and park.distance_miles is not None and park.distance_miles <= 1.5:
            positive.append(
                f"Nearby outdoor amenity found: {park.amenity_name} "
                f"({park.distance_miles:.1f} mi, {_amenity_source_label(park)})."
            )
            delta += 5
        elif park:
            positive.append(
                f"Outdoor amenity found but may require a short drive: {park.amenity_name} "
                f"({park.distance_miles:.1f} mi, {_amenity_source_label(park)})."
            )
            delta += 1
        else:
            missing.append("No nearby park/playground/trail was found in the amenity lookup.")
            delta -= 2

    gym = _nearest_of(nearest_by_type, {"gym"})
    if daily.get("prioritize_gym_access"):
        if gym and gym.distance_miles is not None and gym.distance_miles <= 4:
            positive.append(
                f"Nearby gym option found: {gym.amenity_name} "
                f"({gym.distance_miles:.1f} mi, {_amenity_source_label(gym)})."
            )
            delta += 2
        elif gym:
            negative.append(
                f"Nearest tagged gym is farther away: {gym.amenity_name} "
                f"({gym.distance_miles:.1f} mi, {_amenity_source_label(gym)})."
            )
            delta -= 1
        else:
            missing.append("No nearby gym was found in the amenity lookup.")

    childcare = _nearest_of(nearest_by_type, {"childcare", "kindergarten", "school"})
    if daily.get("prioritize_daycare_commute"):
        if childcare and childcare.distance_miles is not None:
            text = (
                f"Nearby childcare/school option found: {childcare.amenity_name} "
                f"({childcare.distance_miles:.1f} mi, {_amenity_source_label(childcare)})."
            )
            if "montessori" in (childcare.amenity_name or "").lower():
                positive.append(text + " Name matches Montessori preference.")
                delta += 3
            elif childcare.distance_miles <= 3:
                positive.append(text)
                delta += 1
        else:
            missing.append("No nearby childcare/school amenity was found in the amenity lookup.")
    return delta


def _amenity_source_label(amenity: AmenityDistance) -> str:
    source = amenity.source_name or "amenity lookup"
    if source == "OpenStreetMap":
        return "OSM"
    if source == "MN DHS Licensing Lookup":
        return "MN DHS"
    return source


def _nearest_by_type(amenities: list[AmenityDistance]) -> dict[str, AmenityDistance]:
    result: dict[str, AmenityDistance] = {}
    for amenity in amenities:
        amenity_type = amenity.amenity_type
        if not amenity_type:
            continue
        current = result.get(amenity_type)
        if current is None:
            result[amenity_type] = amenity
            continue
        if amenity.distance_miles is None:
            continue
        if current.distance_miles is None or amenity.distance_miles < current.distance_miles:
            result[amenity_type] = amenity
    return result


def _nearest_of(
    nearest_by_type: dict[str, AmenityDistance], amenity_types: set[str]
) -> AmenityDistance | None:
    candidates = [
        amenity
        for amenity_type, amenity in nearest_by_type.items()
        if amenity_type in amenity_types and amenity.distance_miles is not None
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda amenity: amenity.distance_miles or 99)[0]
