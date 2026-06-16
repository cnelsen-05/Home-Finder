from __future__ import annotations

import math
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from realestate.enrichment.geocoding import CensusGeocoder
from realestate.models import CommuteEstimate, LifeAnchor, Property, utcnow


def haversine_miles(
    lat1: float | None, lon1: float | None, lat2: float | None, lon2: float | None
) -> float | None:
    if None in {lat1, lon1, lat2, lon2}:
        return None
    radius = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def approximate_drive_minutes(distance_miles: float | None) -> float | None:
    if distance_miles is None:
        return None
    base_minutes = 5.0
    average_mph = 24.0
    return round(base_minutes + (distance_miles / average_mph) * 60, 1)


def refresh_approximate_commutes(session: Session, prop: Property) -> list[CommuteEstimate]:
    anchors = session.execute(select(LifeAnchor)).scalars().all()
    estimates: list[CommuteEstimate] = []
    for anchor in anchors:
        _ensure_anchor_geocoded(anchor)
        distance = haversine_miles(prop.latitude, prop.longitude, anchor.latitude, anchor.longitude)
        if distance is None:
            continue
        routed = _osrm_drive_route(prop, anchor)
        existing = session.execute(
            select(CommuteEstimate).where(
                CommuteEstimate.property_id == prop.id,
                CommuteEstimate.anchor_id == anchor.id,
                CommuteEstimate.mode == "drive",
            )
        ).scalar_one_or_none()
        estimate = existing or CommuteEstimate(
            property_id=prop.id,
            anchor_id=anchor.id,
            mode="drive",
        )
        if routed:
            estimate.source_name = "OSRM public demo route"
            estimate.distance_miles = routed["distance_miles"]
            estimate.duration_minutes = routed["duration_minutes"]
            estimate.time_of_day = "no_traffic_unspecified"
        else:
            estimate.source_name = "straight_line_approximation"
            estimate.distance_miles = round(distance, 2)
            estimate.duration_minutes = approximate_drive_minutes(distance)
            estimate.time_of_day = "straight_line_no_traffic"
        estimate.retrieved_at = utcnow()
        if existing is None:
            session.add(estimate)
        estimates.append(estimate)
    return estimates


def _ensure_anchor_geocoded(anchor: LifeAnchor) -> None:
    if anchor.latitude is not None and anchor.longitude is not None:
        return
    if not anchor.address:
        return
    result = CensusGeocoder().geocode(anchor.address)
    if result.latitude is not None and result.longitude is not None:
        anchor.latitude = result.latitude
        anchor.longitude = result.longitude


def _osrm_drive_route(prop: Property, anchor: LifeAnchor) -> dict[str, float] | None:
    if None in {prop.latitude, prop.longitude, anchor.latitude, anchor.longitude}:
        return None
    url = (
        "https://router.project-osrm.org/route/v1/driving/"
        f"{prop.longitude},{prop.latitude};{anchor.longitude},{anchor.latitude}"
    )
    params: dict[str, Any] = {"overview": "false", "alternatives": "false", "steps": "false"}
    try:
        response = httpx.get(url, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return None
    routes = payload.get("routes") or []
    if not routes:
        return None
    first = routes[0]
    try:
        return {
            "distance_miles": round(float(first["distance"]) / 1609.344, 1),
            "duration_minutes": round(float(first["duration"]) / 60, 1),
        }
    except (KeyError, TypeError, ValueError):
        return None
