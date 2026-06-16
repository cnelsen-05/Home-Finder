from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from realestate.geospatial import json_dumps, json_loads
from realestate.map_layers import (
    PARKS_TRAILS_CATEGORIES,
    count_map_features,
    nearby_map_features_for_neighborhood,
)
from realestate.models import (
    CommuteEstimate,
    LifeAnchor,
    PropertyNeighborhoodMatch,
    SavedNeighborhood,
    SavedNeighborhoodScore,
)
from realestate.neighborhoods import identify_neighborhood_zone_name

RATING_BASE = {
    "favorite": 95.0,
    "strong_like": 88.0,
    "like": 76.0,
    "maybe": 55.0,
    "avoid": 20.0,
}


def score_saved_neighborhood(
    session: Session,
    neighborhood: SavedNeighborhood,
    *,
    persist: bool = False,
) -> dict[str, Any]:
    tags = set(json_loads(neighborhood.tags_json, []))
    nearby_features = nearby_map_features_for_neighborhood(
        session,
        neighborhood,
        categories=PARKS_TRAILS_CATEGORIES,
        within_miles=1.0,
        limit=12,
    )
    user_score, user_notes = _user_signal_score(neighborhood.rating, tags)
    amenity_score, amenity_notes, amenity_missing = _amenity_score(
        session,
        nearby_features,
    )
    commute_score, commute_notes, commute_missing = _commute_score(session, neighborhood, tags)
    school_score, school_notes, school_missing = _school_context_score(session, neighborhood, tags)
    risk_score, risk_notes = _quiet_street_risk_score(tags)
    overall = round(
        user_score * 0.30
        + amenity_score * 0.25
        + commute_score * 0.20
        + school_score * 0.10
        + risk_score * 0.15,
        1,
    )
    missing_data = [*amenity_missing, *commute_missing, *school_missing]
    confidence = _confidence(missing_data)
    explanation = {
        "overall_score": overall,
        "user_signal_score": round(user_score, 1),
        "amenity_score": round(amenity_score, 1),
        "commute_score": round(commute_score, 1),
        "school_score": round(school_score, 1),
        "risk_score": round(risk_score, 1),
        "confidence": confidence,
        "positive_drivers": [
            *user_notes["positive"],
            *amenity_notes["positive"],
            *commute_notes["positive"],
            *school_notes["positive"],
            *risk_notes["positive"],
        ][:8],
        "concerns": [
            *user_notes["concerns"],
            *amenity_notes["concerns"],
            *commute_notes["concerns"],
            *school_notes["concerns"],
            *risk_notes["concerns"],
        ][:8],
        "missing_data": missing_data,
        "nearby_amenities": nearby_features,
        "source_note": (
            "Neighborhood fit combines user observations with neutral sourced facts such as "
            "nearby mapped amenities, commute estimates, and attendance-zone data confidence. "
            "It does not use demographic or protected-class data."
        ),
    }
    if persist:
        session.add(
            SavedNeighborhoodScore(
                saved_neighborhood_id=neighborhood.id,
                overall_score=overall,
                user_signal_score=round(user_score, 1),
                amenity_score=round(amenity_score, 1),
                commute_score=round(commute_score, 1),
                school_score=round(school_score, 1),
                risk_score=round(risk_score, 1),
                confidence=confidence,
                explanation_json=json_dumps(explanation),
            )
        )
        session.flush()
    return explanation


def score_all_saved_neighborhoods(session: Session, *, persist: bool = True) -> list[dict[str, Any]]:
    neighborhoods = session.execute(select(SavedNeighborhood).order_by(SavedNeighborhood.name)).scalars().all()
    return [score_saved_neighborhood(session, neighborhood, persist=persist) for neighborhood in neighborhoods]


def latest_neighborhood_score(session: Session, neighborhood_id: int) -> dict[str, Any] | None:
    row = session.execute(
        select(SavedNeighborhoodScore)
        .where(SavedNeighborhoodScore.saved_neighborhood_id == neighborhood_id)
        .order_by(SavedNeighborhoodScore.scored_at.desc())
    ).scalars().first()
    if row is None:
        return None
    payload = json_loads(row.explanation_json, {})
    payload.setdefault("overall_score", row.overall_score)
    payload.setdefault("confidence", row.confidence)
    return payload


def _user_signal_score(rating: str, tags: set[str]) -> tuple[float, dict[str, list[str]]]:
    score = RATING_BASE.get(rating, 55.0)
    positive = []
    concerns = []
    if rating in {"favorite", "strong_like", "like"}:
        positive.append(f"User rating is {rating.replace('_', ' ')}.")
    if "favorite_pocket" in tags:
        score += 5
        positive.append("Tagged as a favorite pocket.")
    if "tour_again" in tags:
        score += 3
        positive.append("Tagged to tour again.")
    if "needs_more_research" in tags:
        score -= 4
        concerns.append("Tagged as needing more research.")
    if rating == "avoid":
        concerns.append("User rating is avoid.")
    return _clamp(score), {"positive": positive, "concerns": concerns}


def _amenity_score(
    session: Session,
    nearby_features: list[dict[str, Any]],
) -> tuple[float, dict[str, list[str]], list[str]]:
    total_features = count_map_features(session)
    if total_features == 0:
        return (
            45.0,
            {
                "positive": [],
                "concerns": ["Parks/trails/playgrounds layer has not been imported yet."],
            },
            ["parks_trails_playgrounds_layer"],
        )
    categories = {feature["category"] for feature in nearby_features}
    within_half_mile = [feature for feature in nearby_features if feature["distance_miles"] <= 0.5]
    score = 45.0 + len(categories) * 12.0 + min(len(within_half_mile), 4) * 5.0
    positive = []
    concerns = []
    if nearby_features:
        nearest = nearby_features[0]
        positive.append(
            f"Nearest mapped {nearest['category'].replace('_', ' ')} is "
            f"{nearest['name']} at {nearest['distance_miles']:.2f} mi."
        )
    else:
        concerns.append("No imported park, trail, playground, or nature-reserve feature is within 1 mile.")
    return _clamp(score), {"positive": positive, "concerns": concerns}, []


def _commute_score(
    session: Session,
    neighborhood: SavedNeighborhood,
    tags: set[str],
) -> tuple[float, dict[str, list[str]], list[str]]:
    property_ids = [
        match.property_id
        for match in session.execute(
            select(PropertyNeighborhoodMatch).where(
                PropertyNeighborhoodMatch.saved_neighborhood_id == neighborhood.id,
                PropertyNeighborhoodMatch.relation.in_(["inside", "near", "manually_linked"]),
            )
        ).scalars().all()
    ]
    estimates: list[CommuteEstimate] = []
    if property_ids:
        estimates = session.execute(
            select(CommuteEstimate).where(
                CommuteEstimate.property_id.in_(property_ids),
            )
        ).scalars().all()
    work_durations = [
        estimate.duration_minutes
        for estimate in estimates
        if estimate.duration_minutes is not None and _anchor_is_work(session, estimate)
    ]
    if work_durations:
        avg = sum(work_durations) / len(work_durations)
        if avg <= 20:
            score = 92.0
        elif avg <= 25:
            score = 78.0
        elif avg <= 30:
            score = 58.0
        else:
            score = 35.0
        return (
            score,
            {
                "positive": [f"Matched-home work commute average is {avg:.0f} minutes."],
                "concerns": [] if avg <= 30 else ["Matched-home work commute average exceeds 30 minutes."],
            },
            [],
        )
    if "good_commute" in tags:
        return 72.0, {"positive": ["User tagged good commute."], "concerns": []}, []
    return (
        50.0,
        {"positive": [], "concerns": ["Area-level commute is not routed yet."]},
        ["area_level_commute_to_work_anchor"],
    )


def _school_context_score(
    session: Session,
    neighborhood: SavedNeighborhood,
    tags: set[str],
) -> tuple[float, dict[str, list[str]], list[str]]:
    school_name = identify_neighborhood_zone_name(session, neighborhood)
    if school_name:
        positive = [f"Centroid falls in likely elementary zone: {school_name}."]
        if "school_zone_interest" in tags:
            positive.append("User tagged school-zone interest.")
        return 70.0, {"positive": positive, "concerns": []}, []
    return (
        45.0,
        {"positive": [], "concerns": ["No likely elementary zone was found for the area centroid."]},
        ["elementary_attendance_zone_for_area"],
    )


def _quiet_street_risk_score(tags: set[str]) -> tuple[float, dict[str, list[str]]]:
    score = 70.0
    positive = []
    concerns = []
    if "quiet_street" in tags:
        score += 15
        positive.append("User tagged quiet-street fit.")
    if "mature_trees" in tags:
        score += 5
        positive.append("User tagged mature trees.")
    if "road_noise" in tags:
        score -= 25
        concerns.append("User tagged road-noise concern.")
    if "feels_too_busy" in tags:
        score -= 18
        concerns.append("User tagged the area as feeling too busy.")
    if "expensive" in tags:
        score -= 8
        concerns.append("User tagged the area as expensive.")
    return _clamp(score), {"positive": positive, "concerns": concerns}


def _anchor_is_work(session: Session, estimate: CommuteEstimate) -> bool:
    anchor = session.get(LifeAnchor, estimate.anchor_id)
    return bool(anchor and anchor.category == "work")


def _confidence(missing_data: list[str]) -> str:
    if not missing_data:
        return "high"
    if len(missing_data) <= 2:
        return "medium"
    return "low"


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))
