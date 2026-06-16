from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from realestate.geospatial import (
    distance_to_geometry_miles,
    feature_collection,
    geometry_bbox,
    geometry_feature,
    geometry_to_geojson,
    json_dumps,
    json_loads,
    point_feature,
)
from realestate.models import MapFeature, MapLayer, Property, SavedNeighborhood
from realestate.paths import MAP_LAYER_CACHE_DIR

OSM_SOURCE_NAME = "OpenStreetMap"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
PARKS_TRAILS_LAYER_TYPE = "parks_trails_playgrounds"
PARKS_TRAILS_CATEGORIES = {"park", "playground", "trail", "nature_reserve"}
DEFAULT_TWIN_CITIES_BBOX = (-94.10, 44.55, -92.75, 45.40)


def download_parks_trails_playgrounds(
    session: Session,
    output_path: Path | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    overpass_url: str = OVERPASS_URL,
) -> Path:
    """Download a cached OSM/Overpass parks, trails, and playgrounds payload."""

    MAP_LAYER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    output = output_path or MAP_LAYER_CACHE_DIR / "parks_trails_playgrounds_overpass.json"
    query_bbox = bbox or tracked_map_bbox(session) or DEFAULT_TWIN_CITIES_BBOX
    query = overpass_parks_trails_query(query_bbox)
    response = httpx.post(
        overpass_url,
        data={"data": query},
        headers={"User-Agent": "HomeAnalyze personal real-estate map hub"},
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    payload["_homeanalyze"] = {
        "source_name": OSM_SOURCE_NAME,
        "source_url": overpass_url,
        "bbox": query_bbox,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "note": "OpenStreetMap coverage depends on community tagging; verify details locally.",
    }
    output.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return output


def import_parks_trails_playgrounds(
    session: Session,
    path: Path,
    *,
    replace: bool = True,
) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if replace:
        session.execute(
            delete(MapFeature).where(MapFeature.layer_type == PARKS_TRAILS_LAYER_TYPE)
        )
    if payload.get("type") == "FeatureCollection":
        count = _import_geojson_features(session, payload)
    else:
        count = _import_overpass_features(session, payload)
    _upsert_parks_layer(session, count, payload)
    session.flush()
    return count


def parks_trails_playgrounds_geojson(session: Session) -> dict[str, Any]:
    features = session.execute(
        select(MapFeature)
        .where(MapFeature.layer_type == PARKS_TRAILS_LAYER_TYPE)
        .order_by(MapFeature.category, MapFeature.name)
    ).scalars().all()
    return feature_collection([map_feature_geojson(feature) for feature in features])


def map_feature_geojson(feature: MapFeature) -> dict[str, Any]:
    props = {
        "id": feature.id,
        "layer_type": feature.layer_type,
        "category": feature.category,
        "name": feature.name,
        "source_name": feature.source_name,
        "source_url": feature.source_url,
        "source_key": feature.source_key,
        "confidence": feature.confidence,
        "metadata": json_loads(feature.metadata_json, {}),
    }
    if feature.geometry_geojson:
        return geometry_feature(feature.geometry_geojson, props, feature_id=feature.id)
    if feature.latitude is not None and feature.longitude is not None:
        return point_feature(feature.longitude, feature.latitude, props, feature_id=feature.id)
    return geometry_feature({"type": "Point", "coordinates": [0, 0]}, props, feature_id=feature.id)


def nearby_map_features_for_neighborhood(
    session: Session,
    neighborhood: SavedNeighborhood,
    *,
    categories: set[str] | None = None,
    within_miles: float = 1.0,
    limit: int = 10,
) -> list[dict[str, Any]]:
    categories = categories or PARKS_TRAILS_CATEGORIES
    candidates = session.execute(
        select(MapFeature).where(
            MapFeature.layer_type == PARKS_TRAILS_LAYER_TYPE,
            MapFeature.category.in_(categories),
        )
    ).scalars().all()
    rows: list[dict[str, Any]] = []
    for feature in candidates:
        lon_lat = _feature_lon_lat(feature)
        if lon_lat is None:
            continue
        lon, lat = lon_lat
        distance = distance_to_geometry_miles(lon, lat, neighborhood.geometry_geojson)
        if distance is None or distance > within_miles:
            continue
        rows.append(
            {
                "id": feature.id,
                "name": feature.name or f"Unnamed {feature.category.replace('_', ' ')}",
                "category": feature.category,
                "distance_miles": round(distance, 3),
                "source_name": feature.source_name,
                "source_url": feature.source_url,
                "confidence": feature.confidence,
            }
        )
    return sorted(rows, key=lambda row: (row["distance_miles"], row["category"], row["name"]))[:limit]


def tracked_map_bbox(session: Session, padding_degrees: float = 0.08) -> tuple[float, float, float, float] | None:
    points = [
        (float(lon), float(lat))
        for lat, lon in session.execute(
            select(Property.latitude, Property.longitude).where(
                Property.latitude.is_not(None),
                Property.longitude.is_not(None),
            )
        ).all()
    ]
    for neighborhood in session.execute(select(SavedNeighborhood)).scalars().all():
        bbox = geometry_bbox(neighborhood.geometry_geojson)
        if bbox:
            min_lon, min_lat, max_lon, max_lat = bbox
            points.extend([(min_lon, min_lat), (max_lon, max_lat)])
    if not points:
        return None
    lons = [point[0] for point in points]
    lats = [point[1] for point in points]
    return (
        min(lons) - padding_degrees,
        min(lats) - padding_degrees,
        max(lons) + padding_degrees,
        max(lats) + padding_degrees,
    )


def overpass_parks_trails_query(bbox: tuple[float, float, float, float]) -> str:
    min_lon, min_lat, max_lon, max_lat = bbox
    box = f"{min_lat},{min_lon},{max_lat},{max_lon}"
    return f"""
[out:json][timeout:45];
(
  node["leisure"~"park|playground|nature_reserve"]({box});
  way["leisure"~"park|playground|nature_reserve"]({box});
  relation["leisure"~"park|playground|nature_reserve"]({box});
  node["route"~"hiking|bicycle"]({box});
  way["route"~"hiking|bicycle"]({box});
  relation["route"~"hiking|bicycle"]({box});
  node["highway"~"path|cycleway|footway"]["name"]({box});
  way["highway"~"path|cycleway|footway"]["name"]({box});
);
out center tags 800;
"""


def _import_overpass_features(session: Session, payload: dict[str, Any]) -> int:
    count = 0
    source_url = (payload.get("_homeanalyze") or {}).get("source_url") or OVERPASS_URL
    for element in payload.get("elements", []):
        tags = element.get("tags") or {}
        category = _category_from_tags(tags)
        if category not in PARKS_TRAILS_CATEGORIES:
            continue
        lat, lon = _element_point(element)
        if lat is None or lon is None:
            continue
        name = tags.get("name") or _fallback_name(category)
        source_key = f"{element.get('type')}:{element.get('id')}"
        session.add(
            MapFeature(
                layer_type=PARKS_TRAILS_LAYER_TYPE,
                category=category,
                name=name,
                source_name=OSM_SOURCE_NAME,
                source_url=source_url,
                source_key=source_key,
                latitude=lat,
                longitude=lon,
                geometry_geojson=json_dumps({"type": "Point", "coordinates": [lon, lat]}),
                confidence="medium",
                metadata_json=json_dumps(
                    {
                        "osm_type": element.get("type"),
                        "osm_id": element.get("id"),
                        "tags": {
                            key: value
                            for key, value in tags.items()
                            if key in {"amenity", "leisure", "name", "operator", "highway", "route"}
                        },
                    }
                ),
            )
        )
        count += 1
    return count


def _import_geojson_features(session: Session, payload: dict[str, Any]) -> int:
    count = 0
    for feature in payload.get("features") or []:
        props = feature.get("properties") or {}
        geometry = feature.get("geometry")
        if not geometry:
            continue
        category = _normalize_category(props.get("category") or props.get("amenity_type"))
        if category not in PARKS_TRAILS_CATEGORIES:
            continue
        lon_lat = _geometry_lon_lat(geometry)
        if lon_lat is None:
            continue
        lon, lat = lon_lat
        source_name = str(props.get("source_name") or OSM_SOURCE_NAME)
        source_key = str(props.get("source_key") or props.get("id") or f"geojson:{count}")
        session.add(
            MapFeature(
                layer_type=PARKS_TRAILS_LAYER_TYPE,
                category=category,
                name=props.get("name") or _fallback_name(category),
                source_name=source_name,
                source_url=props.get("source_url"),
                source_key=source_key,
                latitude=lat,
                longitude=lon,
                geometry_geojson=geometry_to_geojson(geometry),
                confidence=str(props.get("confidence") or "medium"),
                metadata_json=json_dumps(
                    {
                        key: value
                        for key, value in props.items()
                        if key
                        not in {"name", "category", "amenity_type", "source_name", "source_url", "confidence"}
                    }
                ),
            )
        )
        count += 1
    return count


def _upsert_parks_layer(session: Session, count: int, payload: dict[str, Any]) -> None:
    layer = session.execute(
        select(MapLayer).where(
            MapLayer.name == "Parks, trails, and playgrounds",
            MapLayer.layer_type == PARKS_TRAILS_LAYER_TYPE,
        )
    ).scalar_one_or_none()
    if layer is None:
        layer = MapLayer(
            name="Parks, trails, and playgrounds",
            layer_type=PARKS_TRAILS_LAYER_TYPE,
            geometry_type="Point",
            enabled_by_default=False,
        )
        session.add(layer)
    layer.source_name = OSM_SOURCE_NAME
    layer.source_url = OVERPASS_URL
    layer.retrieved_at = datetime.now(UTC)
    layer.metadata_json = json_dumps(
        {
            "feature_count": count,
            "categories": sorted(PARKS_TRAILS_CATEGORIES),
            "bbox": (payload.get("_homeanalyze") or {}).get("bbox"),
            "warning": "OpenStreetMap coverage depends on community tagging; verify details locally.",
        }
    )
    layer.style_json = json_dumps(
        {
            "park": "#55936e",
            "playground": "#9a6a16",
            "trail": "#2f5f92",
            "nature_reserve": "#27615a",
        }
    )


def count_map_features(session: Session, layer_type: str = PARKS_TRAILS_LAYER_TYPE) -> int:
    return int(
        session.execute(
            select(func.count()).select_from(MapFeature).where(MapFeature.layer_type == layer_type)
        ).scalar_one()
    )


def _element_point(element: dict[str, Any]) -> tuple[float | None, float | None]:
    if element.get("lat") is not None and element.get("lon") is not None:
        return float(element["lat"]), float(element["lon"])
    center = element.get("center") or {}
    if center.get("lat") is not None and center.get("lon") is not None:
        return float(center["lat"]), float(center["lon"])
    return None, None


def _feature_lon_lat(feature: MapFeature) -> tuple[float, float] | None:
    if feature.longitude is not None and feature.latitude is not None:
        return feature.longitude, feature.latitude
    if not feature.geometry_geojson:
        return None
    return _geometry_lon_lat(json_loads(feature.geometry_geojson, {}))


def _geometry_lon_lat(geometry: dict[str, Any]) -> tuple[float, float] | None:
    if geometry.get("type") == "Point":
        coords = geometry.get("coordinates") or []
        if len(coords) >= 2:
            return float(coords[0]), float(coords[1])
    bbox = geometry_bbox(geometry)
    if not bbox:
        return None
    min_lon, min_lat, max_lon, max_lat = bbox
    return (min_lon + max_lon) / 2, (min_lat + max_lat) / 2


def _category_from_tags(tags: dict[str, Any]) -> str | None:
    leisure = tags.get("leisure")
    highway = tags.get("highway")
    route = tags.get("route")
    if leisure in {"park", "playground", "nature_reserve"}:
        return str(leisure)
    if route in {"hiking", "bicycle"} or highway in {"path", "cycleway", "footway"}:
        return "trail"
    return None


def _normalize_category(value: Any) -> str:
    category = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if category in {"path", "cycleway", "footway", "hiking", "bicycle"}:
        return "trail"
    return category


def _fallback_name(category: str) -> str:
    return {
        "park": "Unnamed park",
        "playground": "Unnamed playground",
        "trail": "Unnamed trail/path",
        "nature_reserve": "Unnamed nature reserve",
    }.get(category, "Unnamed map feature")
