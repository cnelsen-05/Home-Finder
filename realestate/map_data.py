from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from realestate.geospatial import (
    feature_collection,
    geometry_feature,
    json_dumps,
    json_loads,
    point_feature,
)
from realestate.map_highlights import map_highlights_geojson, property_highlight_context
from realestate.map_layers import (
    PARKS_TRAILS_LAYER_TYPE,
    count_map_features,
    parks_trails_playgrounds_geojson,
)
from realestate.models import (
    AmenityDistance,
    Favorite,
    LifeAnchor,
    MapLayer,
    MapNote,
    Report,
    ReviewScore,
    SchoolAttendanceZone,
)
from realestate.neighborhoods import property_neighborhood_context, saved_neighborhoods_geojson
from realestate.parsing.address_parser import join_address
from realestate.paths import MAP_EXPORTS_DIR
from realestate.school_zones import identify_property_elementary_zone, school_zones_geojson
from realestate.schools import (
    count_school_academic_profiles,
    count_school_locations,
    enrich_school_zone_payload,
    school_locations_geojson,
)
from realestate.scoring.overall import latest_score


def favorite_homes_geojson(session: Session) -> dict[str, Any]:
    favorites = session.execute(select(Favorite).where(Favorite.listing_id.is_not(None))).scalars().all()
    features = []
    for favorite in favorites:
        feature = favorite_home_feature(session, favorite)
        if feature is not None:
            features.append(feature)
    return feature_collection(features)


def favorite_home_feature(session: Session, favorite: Favorite) -> dict[str, Any] | None:
    listing = favorite.listing
    if listing is None:
        return None
    prop = listing.property
    score = latest_score(session, listing)
    has_location = prop.latitude is not None and prop.longitude is not None
    zone_payload = None
    if has_location:
        zone_lookup = identify_property_elementary_zone(session, prop)
        zone_payload = zone_lookup.as_dict() if zone_lookup else None
        if zone_payload:
            zone_payload = enrich_school_zone_payload(session, zone_payload)
    properties = {
        "favorite_id": favorite.id,
        "listing_id": listing.id,
        "property_id": prop.id,
        "address": join_address(prop.address_line1, prop.city, prop.state, prop.zip),
        "city": prop.city,
        "state": prop.state,
        "zip": prop.zip,
        "price": listing.list_price,
        "beds": listing.beds,
        "baths": listing.baths,
        "finished_sqft": listing.finished_sqft,
        "lot_size_sqft": listing.lot_size_sqft,
        "garage_spaces": listing.garage_spaces,
        "year_built": listing.year_built,
        "user_rating": favorite.user_rating,
        "user_notes": favorite.user_notes,
        "score": _score_payload(score),
        "elementary_zone": zone_payload,
        "neighborhood_matches": property_neighborhood_context(session, prop.id),
        "highlight_matches": property_highlight_context(session, prop.id),
        "report_path": _latest_report_path(session, listing.id),
        "listing_url": listing.listing_url,
        "has_location": has_location,
        "map_status": "mapped" if has_location else "needs_location",
        "location_warning": None
        if has_location
        else "No coordinates yet. Run enrichment or add from a clicked map point to place this home on the map.",
    }
    if has_location:
        return point_feature(prop.longitude, prop.latitude, properties, feature_id=listing.id)
    return {
        "type": "Feature",
        "geometry": None,
        "properties": properties,
        "id": listing.id,
    }


def map_notes_geojson(session: Session) -> dict[str, Any]:
    notes = session.execute(select(MapNote).order_by(MapNote.updated_at.desc())).scalars().all()
    features = []
    for note in notes:
        props = {
            "id": note.id,
            "note_type": note.note_type,
            "title": note.title,
            "body": note.body,
            "tags": json_loads(note.tags_json, []),
            "related_property_id": note.related_property_id,
            "related_neighborhood_id": note.related_neighborhood_id,
            "created_at": note.created_at.isoformat() if note.created_at else None,
            "updated_at": note.updated_at.isoformat() if note.updated_at else None,
        }
        if note.geometry_geojson:
            features.append(geometry_feature(note.geometry_geojson, props, feature_id=note.id))
        elif note.latitude is not None and note.longitude is not None:
            features.append(point_feature(note.longitude, note.latitude, props, feature_id=note.id))
    return feature_collection(features)


def life_anchor_geojson(session: Session) -> dict[str, Any]:
    anchors = session.execute(
        select(LifeAnchor).where(LifeAnchor.latitude.is_not(None), LifeAnchor.longitude.is_not(None))
    ).scalars().all()
    return feature_collection(
        [
            point_feature(
                anchor.longitude,
                anchor.latitude,
                {
                    "id": anchor.id,
                    "name": anchor.name,
                    "category": anchor.category,
                    "address": anchor.address,
                    "priority": anchor.priority,
                    "notes": anchor.notes,
                },
                feature_id=anchor.id,
            )
            for anchor in anchors
            if anchor.latitude is not None and anchor.longitude is not None
        ]
    )


def amenity_summary_for_property(session: Session, property_id: int) -> list[dict[str, Any]]:
    amenities = session.execute(
        select(AmenityDistance)
        .where(AmenityDistance.property_id == property_id)
        .order_by(AmenityDistance.amenity_type, AmenityDistance.distance_miles)
    ).scalars().all()
    return [
        {
            "amenity_type": amenity.amenity_type,
            "amenity_name": amenity.amenity_name,
            "distance_miles": amenity.distance_miles,
            "source_name": amenity.source_name,
        }
        for amenity in amenities[:12]
    ]


def layer_manifest(session: Session) -> list[dict[str, Any]]:
    layers = session.execute(select(MapLayer).order_by(MapLayer.name)).scalars().all()
    return [
        {
            "id": layer.id,
            "name": layer.name,
            "layer_type": layer.layer_type,
            "source_name": layer.source_name,
            "source_url": layer.source_url,
            "geometry_type": layer.geometry_type,
            "style": json_loads(layer.style_json, {}),
            "enabled_by_default": layer.enabled_by_default,
            "retrieved_at": layer.retrieved_at.isoformat() if layer.retrieved_at else None,
            "metadata": json_loads(layer.metadata_json, {}),
        }
        for layer in layers
    ]


def map_payload(session: Session) -> dict[str, Any]:
    return {
        "homes": favorite_homes_geojson(session),
        "saved_neighborhoods": saved_neighborhoods_geojson(session),
        "map_highlights": map_highlights_geojson(session),
        "map_notes": map_notes_geojson(session),
        "life_anchors": life_anchor_geojson(session),
        "layers": layer_manifest(session),
        "lazy_layers": {
            "school_zones": {
                "url": "/api/school-zones",
                "feature_count": _school_zone_count(session),
                "loaded": False,
            },
            "parks_trails_playgrounds": {
                "url": "/api/parks-trails-playgrounds",
                "feature_count": count_map_features(session, PARKS_TRAILS_LAYER_TYPE),
                "loaded": False,
            },
            "school_locations": {
                "url": "/api/school-locations",
                "feature_count": count_school_locations(session),
                "loaded": False,
            },
        },
        "disclaimers": {
            "school_zones": (
                "Likely attendance zones are based on imported public data and must be verified "
                "with the district before relying. Near-boundary points require direct district verification."
            ),
            "user_notes": "Saved-neighborhood notes and ratings are user opinions, not sourced facts.",
            "parks_trails_playgrounds": (
                "Parks, trails, and playgrounds are sourced from cached OpenStreetMap data. "
                "Coverage depends on community tagging and should be verified locally."
            ),
            "school_rankings": (
                f"Imported school academic/ranking profiles include {count_school_academic_profiles(session)} "
                "source-labeled third-party or official rows. Rankings are context only; verify at the source."
            ),
        },
    }


def build_map_data_exports(session: Session, output_dir: Path | None = None) -> dict[str, Path]:
    output = output_dir or MAP_EXPORTS_DIR
    output.mkdir(parents=True, exist_ok=True)
    exports = {
        "favorite_homes": output / "favorite_homes.geojson",
        "saved_neighborhoods": output / "saved_neighborhoods.geojson",
        "map_highlights": output / "map_highlights.geojson",
        "school_zones": output / "school_attendance_zones.geojson",
        "school_locations": output / "school_locations.geojson",
        "parks_trails_playgrounds": output / "parks_trails_playgrounds.geojson",
        "map_notes": output / "map_notes.geojson",
        "life_anchors": output / "life_anchors.geojson",
        "map_payload": output / "map_payload.json",
    }
    payload = map_payload(session)
    exports["favorite_homes"].write_text(json_dumps(payload["homes"]), encoding="utf-8")
    exports["saved_neighborhoods"].write_text(
        json_dumps(payload["saved_neighborhoods"]),
        encoding="utf-8",
    )
    exports["map_highlights"].write_text(json_dumps(payload["map_highlights"]), encoding="utf-8")
    exports["school_zones"].write_text(json_dumps(school_zones_geojson(session)), encoding="utf-8")
    exports["school_locations"].write_text(json_dumps(school_locations_geojson(session)), encoding="utf-8")
    exports["parks_trails_playgrounds"].write_text(
        json_dumps(parks_trails_playgrounds_geojson(session)),
        encoding="utf-8",
    )
    exports["map_notes"].write_text(json_dumps(payload["map_notes"]), encoding="utf-8")
    exports["life_anchors"].write_text(json_dumps(payload["life_anchors"]), encoding="utf-8")
    exports["map_payload"].write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return exports


def _score_payload(score: ReviewScore | None) -> dict[str, Any] | None:
    if score is None:
        return None
    return {
        "quality_score": score.quality_score,
        "value_score": score.value_score,
        "daily_life_score": score.daily_life_score,
        "risk_score": score.risk_score,
        "preference_score": score.preference_score,
        "overall_score": score.overall_score,
        "recommendation_bucket": score.recommendation_bucket,
    }


def _latest_report_path(session: Session, listing_id: int) -> str | None:
    report = session.execute(
        select(Report)
        .where(Report.listing_id == listing_id, Report.report_type == "favorite_home_review")
        .order_by(Report.generated_at.desc())
    ).scalars().first()
    return report.path if report else None


def _school_zone_count(session: Session) -> int:
    from sqlalchemy import func

    return int(
        session.execute(
            select(func.count())
            .select_from(SchoolAttendanceZone)
            .where(SchoolAttendanceZone.school_level == "elementary")
        ).scalar_one()
    )
