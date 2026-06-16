from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
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
from realestate.models import MapHighlight, Property, SavedNeighborhood

HIGHLIGHT_TYPES = {
    "liked_area",
    "avoid_area",
    "liked_street",
    "avoid_street",
    "question_area",
    "tour_note",
}
HIGHLIGHT_SENTIMENTS = {"favorite", "like", "maybe", "avoid"}
LIKED_TYPES = {"liked_area", "liked_street"}
AVOID_TYPES = {"avoid_area", "avoid_street"}


def create_map_highlight(
    session: Session,
    *,
    name: str,
    geometry: dict[str, Any] | str,
    highlight_type: str,
    sentiment: str | None = None,
    notes: str | None = None,
    tags: list[str] | None = None,
    style: dict[str, Any] | None = None,
    source: str = "user_drawn",
    related_property_id: int | None = None,
    related_neighborhood_id: int | None = None,
) -> MapHighlight:
    normalized_type = _normalize_highlight_type(highlight_type)
    highlight = MapHighlight(
        name=name.strip() or _default_name(normalized_type),
        geometry_geojson=geometry_to_geojson(geometry),
        highlight_type=normalized_type,
        sentiment=_normalize_sentiment(sentiment or _sentiment_from_type(normalized_type)),
        notes=notes,
        tags_json=json_dumps(_normalize_tags(tags or [])),
        style_json=json_dumps(style or {}),
        source=source,
        related_property_id=related_property_id,
        related_neighborhood_id=related_neighborhood_id,
    )
    session.add(highlight)
    session.flush()
    return highlight


def update_map_highlight(
    session: Session,
    highlight_id: int,
    updates: dict[str, Any],
) -> MapHighlight:
    highlight = session.get(MapHighlight, highlight_id)
    if highlight is None:
        raise ValueError(f"Map highlight {highlight_id} not found.")
    if "name" in updates:
        highlight.name = str(updates["name"]).strip() or highlight.name
    if "geometry" in updates:
        highlight.geometry_geojson = geometry_to_geojson(updates["geometry"])
    if "geometry_geojson" in updates:
        highlight.geometry_geojson = geometry_to_geojson(updates["geometry_geojson"])
    if "highlight_type" in updates:
        highlight.highlight_type = _normalize_highlight_type(str(updates["highlight_type"]))
    if "sentiment" in updates:
        highlight.sentiment = _normalize_sentiment(str(updates["sentiment"]))
    if "notes" in updates:
        highlight.notes = updates["notes"]
    if "tags" in updates:
        highlight.tags_json = json_dumps(_normalize_tags(updates["tags"] or []))
    if "style" in updates:
        highlight.style_json = json_dumps(updates["style"] or {})
    if "related_property_id" in updates:
        highlight.related_property_id = updates["related_property_id"]
    if "related_neighborhood_id" in updates:
        highlight.related_neighborhood_id = updates["related_neighborhood_id"]
    session.flush()
    return highlight


def delete_map_highlight(session: Session, highlight_id: int) -> bool:
    highlight = session.get(MapHighlight, highlight_id)
    if highlight is None:
        return False
    session.delete(highlight)
    session.flush()
    return True


def map_highlights_geojson(session: Session) -> dict[str, Any]:
    highlights = session.execute(
        select(MapHighlight).order_by(MapHighlight.sentiment, MapHighlight.name)
    ).scalars().all()
    return feature_collection([map_highlight_feature(item) for item in highlights])


def map_highlight_feature(highlight: MapHighlight) -> dict[str, Any]:
    return geometry_feature(
        highlight.geometry_geojson,
        {
            "id": highlight.id,
            "name": highlight.name,
            "highlight_type": highlight.highlight_type,
            "sentiment": highlight.sentiment,
            "notes": highlight.notes,
            "tags": json_loads(highlight.tags_json, []),
            "style": json_loads(highlight.style_json, {}),
            "source": highlight.source,
            "related_property_id": highlight.related_property_id,
            "related_neighborhood_id": highlight.related_neighborhood_id,
            "created_at": highlight.created_at.isoformat() if highlight.created_at else None,
            "updated_at": highlight.updated_at.isoformat() if highlight.updated_at else None,
        },
        feature_id=highlight.id,
    )


def property_highlight_context(
    session: Session,
    property_id: int,
    *,
    near_miles: float = 0.25,
) -> list[dict[str, Any]]:
    prop = session.get(Property, property_id)
    if prop is None or prop.latitude is None or prop.longitude is None:
        return []
    rows: list[dict[str, Any]] = []
    for highlight in session.execute(select(MapHighlight)).scalars().all():
        relation = _point_highlight_relation(prop.longitude, prop.latitude, highlight, near_miles)
        if relation is None:
            continue
        rows.append(_highlight_context_row(highlight, relation["relation"], relation["distance_miles"]))
    return sorted(rows, key=lambda row: (row["distance_miles"] or 0.0, row["sentiment"], row["name"]))


def neighborhood_highlight_context(
    session: Session,
    neighborhood: SavedNeighborhood,
    *,
    near_miles: float = 0.25,
) -> list[dict[str, Any]]:
    centroid = geometry_centroid(neighborhood.geometry_geojson)
    rows: list[dict[str, Any]] = []
    for highlight in session.execute(select(MapHighlight)).scalars().all():
        relation = None
        distance = None
        try:
            if geometries_intersect_approx(neighborhood.geometry_geojson, highlight.geometry_geojson):
                relation = "intersects"
                distance = 0.0
        except ValueError:
            relation = None
        if relation is None and centroid:
            lon, lat = centroid
            distance = distance_to_geometry_miles(lon, lat, highlight.geometry_geojson)
            if distance is not None and distance <= near_miles:
                relation = "near"
        if relation:
            rows.append(_highlight_context_row(highlight, relation, distance))
    return sorted(rows, key=lambda row: (row["sentiment"] == "avoid", row["distance_miles"] or 0.0, row["name"]))


def export_map_highlights(session: Session, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_dumps(map_highlights_geojson(session)), encoding="utf-8")
    return path


def import_map_highlights(session: Session, path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("type") != "FeatureCollection":
        raise ValueError("Map-highlight import expects a GeoJSON FeatureCollection.")
    count = 0
    for feature in payload.get("features") or []:
        props = feature.get("properties") or {}
        geometry = feature.get("geometry")
        if not geometry:
            continue
        create_map_highlight(
            session,
            name=str(props.get("name") or "Imported highlight"),
            geometry=geometry,
            highlight_type=str(props.get("highlight_type") or "tour_note"),
            sentiment=props.get("sentiment"),
            notes=props.get("notes"),
            tags=props.get("tags") or [],
            style=props.get("style") or {},
            source=str(props.get("source") or "geojson_import"),
            related_property_id=props.get("related_property_id"),
            related_neighborhood_id=props.get("related_neighborhood_id"),
        )
        count += 1
    return count


def _point_highlight_relation(
    lon: float,
    lat: float,
    highlight: MapHighlight,
    near_miles: float,
) -> dict[str, Any] | None:
    geometry = json_loads(highlight.geometry_geojson, {})
    relation = "near"
    try:
        if geometry.get("type") in {"Polygon", "MultiPolygon"} and point_in_geometry(lon, lat, geometry):
            return {"relation": "inside", "distance_miles": 0.0}
    except ValueError:
        return None
    distance = distance_to_geometry_miles(lon, lat, geometry)
    if distance is None or distance > near_miles:
        return None
    if geometry.get("type") in {"LineString", "MultiLineString"}:
        relation = "near_street"
    return {"relation": relation, "distance_miles": round(distance, 3)}


def _highlight_context_row(
    highlight: MapHighlight,
    relation: str,
    distance_miles: float | None,
) -> dict[str, Any]:
    return {
        "id": highlight.id,
        "name": highlight.name,
        "highlight_type": highlight.highlight_type,
        "sentiment": highlight.sentiment,
        "relation": relation,
        "distance_miles": round(distance_miles, 3) if distance_miles is not None else None,
        "notes": highlight.notes,
        "tags": json_loads(highlight.tags_json, []),
        "source": highlight.source,
    }


def _normalize_highlight_type(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized not in HIGHLIGHT_TYPES:
        raise ValueError(f"highlight_type must be one of {sorted(HIGHLIGHT_TYPES)}")
    return normalized


def _normalize_sentiment(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized not in HIGHLIGHT_SENTIMENTS:
        raise ValueError(f"sentiment must be one of {sorted(HIGHLIGHT_SENTIMENTS)}")
    return normalized


def _sentiment_from_type(highlight_type: str) -> str:
    if highlight_type in AVOID_TYPES:
        return "avoid"
    if highlight_type in LIKED_TYPES:
        return "like"
    return "maybe"


def _default_name(highlight_type: str) -> str:
    return {
        "liked_area": "Liked area",
        "avoid_area": "Avoid area",
        "liked_street": "Liked street",
        "avoid_street": "Avoid street",
        "question_area": "Question area",
        "tour_note": "Tour note",
    }.get(highlight_type, "Map highlight")


def _normalize_tags(tags: list[str]) -> list[str]:
    clean = []
    for tag in tags:
        normalized = str(tag).strip().lower().replace("-", "_").replace(" ", "_")
        if normalized and normalized not in clean:
            clean.append(normalized)
    return clean
