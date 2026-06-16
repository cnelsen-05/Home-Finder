from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from realestate.geospatial import (
    distance_to_geometry_miles,
    feature_collection,
    geometries_intersect_approx,
    geometry_centroid,
    geometry_feature,
    geometry_to_geojson,
    json_dumps,
    json_loads,
    point_in_geometry,
)
from realestate.models import (
    Favorite,
    Listing,
    MapHighlight,
    ProfileNeighborhoodFeedback,
    Property,
    PropertyNeighborhoodMatch,
    SavedNeighborhood,
    SchoolAttendanceZone,
)
from realestate.parsing.address_parser import join_address
from realestate.profiles import all_neighborhood_feedback_for_area, neighborhood_feedback_for_area
from realestate.school_zones import identify_elementary_zone

NEIGHBORHOOD_RATINGS = {"favorite", "strong_like", "like", "maybe", "avoid"}
NEIGHBORHOOD_TAGS = {
    "quiet_street",
    "parks",
    "playgrounds",
    "trails",
    "mature_trees",
    "good_commute",
    "near_lifetime",
    "daycare_nearby",
    "school_zone_interest",
    "feels_too_busy",
    "road_noise",
    "expensive",
    "needs_more_research",
    "tour_again",
    "favorite_pocket",
}


def create_saved_neighborhood(
    session: Session,
    *,
    name: str,
    geometry: dict[str, Any] | str,
    rating: str = "maybe",
    notes: str | None = None,
    tags: list[str] | None = None,
    city: str | None = None,
    source: str = "user_drawn",
) -> SavedNeighborhood:
    rating = _normalize_rating(rating)
    clean_tags = _normalize_tags(tags or [])
    neighborhood = SavedNeighborhood(
        name=name.strip() or "Untitled saved area",
        geometry_geojson=geometry_to_geojson(geometry),
        rating=rating,
        notes=notes,
        tags_json=json_dumps(clean_tags),
        city=city,
        source=source,
    )
    session.add(neighborhood)
    session.flush()
    return neighborhood


def update_saved_neighborhood(
    session: Session,
    neighborhood_id: int,
    updates: dict[str, Any],
) -> SavedNeighborhood:
    neighborhood = session.get(SavedNeighborhood, neighborhood_id)
    if neighborhood is None:
        raise ValueError(f"Saved neighborhood {neighborhood_id} not found.")
    if "name" in updates:
        neighborhood.name = str(updates["name"]).strip() or neighborhood.name
    if "geometry" in updates:
        neighborhood.geometry_geojson = geometry_to_geojson(updates["geometry"])
    if "geometry_geojson" in updates:
        neighborhood.geometry_geojson = geometry_to_geojson(updates["geometry_geojson"])
    if "rating" in updates:
        neighborhood.rating = _normalize_rating(str(updates["rating"]))
    if "notes" in updates:
        neighborhood.notes = updates["notes"]
    if "tags" in updates:
        neighborhood.tags_json = json_dumps(_normalize_tags(updates["tags"] or []))
    if "city" in updates:
        neighborhood.city = updates["city"]
    session.flush()
    return neighborhood


def delete_saved_neighborhood(session: Session, neighborhood_id: int) -> bool:
    neighborhood = session.get(SavedNeighborhood, neighborhood_id)
    if neighborhood is None:
        return False
    session.execute(
        delete(ProfileNeighborhoodFeedback).where(
            ProfileNeighborhoodFeedback.saved_neighborhood_id == neighborhood_id
        )
    )
    session.execute(
        update(MapHighlight)
        .where(MapHighlight.related_neighborhood_id == neighborhood_id)
        .values(related_neighborhood_id=None)
    )
    session.delete(neighborhood)
    session.flush()
    return True


def saved_neighborhoods_geojson(
    session: Session,
    include_scores: bool = True,
    profile_id: int | None = None,
) -> dict[str, Any]:
    neighborhoods = session.execute(
        select(SavedNeighborhood).order_by(SavedNeighborhood.rating, SavedNeighborhood.name)
    ).scalars().all()
    features = []
    for item in neighborhoods:
        score = None
        if include_scores:
            from realestate.neighborhood_scoring import score_saved_neighborhood

            score = score_saved_neighborhood(session, item, persist=False)
        features.append(saved_neighborhood_feature(session, item, score=score, profile_id=profile_id))
    return feature_collection(features)


def saved_neighborhood_feature(
    session: Session,
    neighborhood: SavedNeighborhood,
    score: dict[str, Any] | None = None,
    profile_id: int | None = None,
) -> dict[str, Any]:
    selected_feedback = neighborhood_feedback_for_area(session, neighborhood.id, profile_id)
    selected_rating = (
        selected_feedback.rating if selected_feedback and selected_feedback.rating else neighborhood.rating
    )
    selected_notes = (
        selected_feedback.notes
        if selected_feedback and selected_feedback.notes is not None
        else neighborhood.notes
    )
    return geometry_feature(
        neighborhood.geometry_geojson,
        {
            "id": neighborhood.id,
            "name": neighborhood.name,
            "rating": selected_rating,
            "notes": selected_notes,
            "household_rating": neighborhood.rating,
            "household_notes": neighborhood.notes,
            "profile_feedback": {
                "profile_id": profile_id,
                "rating": selected_feedback.rating if selected_feedback else None,
                "notes": selected_feedback.notes if selected_feedback else None,
                "tags": json_loads(selected_feedback.tags_json, []) if selected_feedback else [],
                "updated_at": selected_feedback.updated_at.isoformat()
                if selected_feedback and selected_feedback.updated_at
                else None,
            },
            "profile_ratings": all_neighborhood_feedback_for_area(session, neighborhood.id),
            "tags": json_loads(neighborhood.tags_json, []),
            "city": neighborhood.city,
            "source": neighborhood.source,
            "fit_score": score,
            "created_at": neighborhood.created_at.isoformat() if neighborhood.created_at else None,
            "updated_at": neighborhood.updated_at.isoformat() if neighborhood.updated_at else None,
        },
        feature_id=neighborhood.id,
    )


def export_saved_neighborhoods(session: Session, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_dumps(saved_neighborhoods_geojson(session)), encoding="utf-8")
    return path


def import_saved_neighborhoods(session: Session, path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("type") != "FeatureCollection":
        raise ValueError("Neighborhood import expects a GeoJSON FeatureCollection.")
    count = 0
    for feature in payload.get("features") or []:
        props = feature.get("properties") or {}
        geometry = feature.get("geometry")
        if not geometry:
            continue
        create_saved_neighborhood(
            session,
            name=str(props.get("name") or "Imported saved area"),
            geometry=geometry,
            rating=str(props.get("rating") or "maybe"),
            notes=props.get("notes"),
            tags=props.get("tags") or [],
            city=props.get("city"),
            source=str(props.get("source") or "geojson_import"),
        )
        count += 1
    return count


def match_homes_to_neighborhoods(
    session: Session,
    *,
    near_miles: float = 1.0,
    include_same_zone: bool = True,
) -> int:
    session.execute(
        delete(PropertyNeighborhoodMatch).where(
            PropertyNeighborhoodMatch.relation != "manually_linked"
        )
    )
    properties = session.execute(
        select(Property).where(Property.latitude.is_not(None), Property.longitude.is_not(None))
    ).scalars().all()
    neighborhoods = session.execute(select(SavedNeighborhood)).scalars().all()
    created = 0
    neighborhood_zone_cache: dict[int, str | None] = {}
    for prop in properties:
        prop_zone_name = None
        if include_same_zone:
            lookup = identify_property_zone_name(session, prop)
            prop_zone_name = lookup
        for neighborhood in neighborhoods:
            geometry = json_loads(neighborhood.geometry_geojson, {})
            if not geometry:
                continue
            assert prop.longitude is not None
            assert prop.latitude is not None
            if point_in_geometry(prop.longitude, prop.latitude, geometry):
                _add_match(session, prop.id, neighborhood.id, "inside", 0.0, "high")
                created += 1
            else:
                distance = distance_to_geometry_miles(prop.longitude, prop.latitude, geometry)
                if distance is not None and distance <= near_miles:
                    _add_match(
                        session,
                        prop.id,
                        neighborhood.id,
                        "near",
                        round(distance, 3),
                        "medium",
                    )
                    created += 1
            if include_same_zone and prop_zone_name:
                neighborhood_zone = neighborhood_zone_cache.get(neighborhood.id)
                if neighborhood.id not in neighborhood_zone_cache:
                    neighborhood_zone = identify_neighborhood_zone_name(session, neighborhood)
                    neighborhood_zone_cache[neighborhood.id] = neighborhood_zone
                if neighborhood_zone and neighborhood_zone == prop_zone_name:
                    _add_match(session, prop.id, neighborhood.id, "same_zone", None, "medium")
                    created += 1
    session.flush()
    return created


def property_neighborhood_context(session: Session, property_id: int) -> list[dict[str, Any]]:
    matches = session.execute(
        select(PropertyNeighborhoodMatch)
        .where(PropertyNeighborhoodMatch.property_id == property_id)
        .order_by(PropertyNeighborhoodMatch.relation, PropertyNeighborhoodMatch.distance_miles)
    ).scalars().all()
    context = []
    for match in matches:
        neighborhood = match.saved_neighborhood
        context.append(
            {
                "id": neighborhood.id,
                "name": neighborhood.name,
                "rating": neighborhood.rating,
                "relation": match.relation,
                "distance_miles": match.distance_miles,
                "confidence": match.confidence,
                "tags": json_loads(neighborhood.tags_json, []),
                "notes": neighborhood.notes,
            }
        )
    return context


def neighborhood_report_context(session: Session, neighborhood: SavedNeighborhood) -> dict[str, Any]:
    matches = session.execute(
        select(PropertyNeighborhoodMatch).where(
            PropertyNeighborhoodMatch.saved_neighborhood_id == neighborhood.id
        )
    ).scalars().all()
    homes_inside = _matched_home_rows(session, [m for m in matches if m.relation == "inside"])
    homes_nearby = _matched_home_rows(session, [m for m in matches if m.relation == "near"])
    same_zone_homes = _matched_home_rows(session, [m for m in matches if m.relation == "same_zone"])
    zones = _intersecting_school_zone_rows(session, neighborhood)
    centroid = geometry_centroid(neighborhood.geometry_geojson)
    from realestate.map_highlights import neighborhood_highlight_context
    from realestate.map_layers import nearby_map_features_for_neighborhood
    from realestate.neighborhood_scoring import score_saved_neighborhood

    nearby_amenities = nearby_map_features_for_neighborhood(session, neighborhood, within_miles=1.0)
    highlight_matches = neighborhood_highlight_context(session, neighborhood)
    fit_score = score_saved_neighborhood(session, neighborhood, persist=True)
    return {
        "neighborhood": neighborhood,
        "tags": json_loads(neighborhood.tags_json, []),
        "homes_inside": homes_inside,
        "homes_nearby": homes_nearby,
        "same_zone_homes": same_zone_homes,
        "elementary_zones": zones,
        "nearby_amenities": nearby_amenities,
        "highlight_matches": highlight_matches,
        "fit_score": fit_score,
        "centroid": {"lon": centroid[0], "lat": centroid[1]} if centroid else None,
    }


def identify_property_zone_name(session: Session, prop: Property) -> str | None:
    if prop.latitude is None or prop.longitude is None:
        return None
    lookup = identify_elementary_zone(session, lat=prop.latitude, lon=prop.longitude)
    return lookup.zone.school_name if lookup.zone else None


def identify_neighborhood_zone_name(session: Session, neighborhood: SavedNeighborhood) -> str | None:
    centroid = geometry_centroid(neighborhood.geometry_geojson)
    if centroid is None:
        return None
    lon, lat = centroid
    lookup = identify_elementary_zone(session, lat=lat, lon=lon)
    return lookup.zone.school_name if lookup.zone else None


def _add_match(
    session: Session,
    property_id: int,
    neighborhood_id: int,
    relation: str,
    distance_miles: float | None,
    confidence: str,
) -> None:
    session.add(
        PropertyNeighborhoodMatch(
            property_id=property_id,
            saved_neighborhood_id=neighborhood_id,
            relation=relation,
            distance_miles=distance_miles,
            confidence=confidence,
        )
    )


def _matched_home_rows(
    session: Session,
    matches: list[PropertyNeighborhoodMatch],
) -> list[dict[str, Any]]:
    rows = []
    for match in matches:
        prop = match.property
        listing = session.execute(
            select(Listing).where(Listing.property_id == prop.id).order_by(Listing.id.desc())
        ).scalars().first()
        favorite = (
            session.execute(select(Favorite).where(Favorite.listing_id == listing.id)).scalars().first()
            if listing
            else None
        )
        rows.append(
            {
                "property": prop,
                "listing": listing,
                "favorite": favorite,
                "address": join_address(prop.address_line1, prop.city, prop.state, prop.zip),
                "relation": match.relation,
                "distance_miles": match.distance_miles,
                "confidence": match.confidence,
            }
        )
    return rows


def _intersecting_school_zone_rows(
    session: Session,
    neighborhood: SavedNeighborhood,
) -> list[dict[str, Any]]:
    rows = []
    from realestate.schools import school_context_for_zone

    for zone in session.execute(select(SchoolAttendanceZone)).scalars().all():
        if geometries_intersect_approx(neighborhood.geometry_geojson, zone.geometry_geojson):
            school_context = school_context_for_zone(session, zone)
            rows.append(
                {
                    "school_name": zone.school_name,
                    "district_name": zone.district_name,
                    "school_year": zone.school_year,
                    "source_name": zone.source_name,
                    "source_url": zone.source_url,
                    "confidence": zone.confidence,
                    "school_location": school_context.get("school_location"),
                    "academic_profiles": school_context.get("academic_profiles", []),
                    "niche_rank": school_context.get("niche_rank"),
                    "niche_grade": school_context.get("niche_grade"),
                }
            )
    return rows


def _normalize_rating(value: str) -> str:
    rating = (value or "maybe").strip().lower()
    if rating not in NEIGHBORHOOD_RATINGS:
        raise ValueError(f"rating must be one of {sorted(NEIGHBORHOOD_RATINGS)}")
    return rating


def _normalize_tags(tags: list[str]) -> list[str]:
    clean = []
    for tag in tags:
        normalized = str(tag).strip().lower().replace("-", "_").replace(" ", "_")
        if normalized and normalized not in clean:
            clean.append(normalized)
    return clean
