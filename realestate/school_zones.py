from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from realestate.geospatial import (
    boundary_distance_miles,
    feature_collection,
    geometry_feature,
    geometry_to_geojson,
    json_dumps,
    json_loads,
    point_in_geometry,
)
from realestate.models import MapLayer, Property, SchoolAttendanceZone
from realestate.paths import SCHOOL_ZONE_CACHE_DIR

MN_ATTENDANCE_AREAS_LAYER_URL = (
    "https://services.arcgis.com/GXwOsvnLQI6EDOp7/ArcGIS/rest/services/"
    "Minnesota_School_Attendance_Areas_Current_View/FeatureServer/0"
)
MN_ATTENDANCE_AREAS_QUERY_URL = f"{MN_ATTENDANCE_AREAS_LAYER_URL}/query"
MN_ATTENDANCE_SOURCE_NAME = "Minnesota School Attendance Areas Current View"
VERIFY_ASSIGNMENT_WARNING = (
    "Likely attendance zone based on current public data. Verify with the district before relying."
)
NEAR_BOUNDARY_WARNING = "Near attendance-area boundary; verify directly with the district."


@dataclass(frozen=True)
class SchoolZoneLookup:
    zone: SchoolAttendanceZone | None
    boundary_distance_miles: float | None
    near_boundary: bool
    warning: str

    def as_dict(self) -> dict[str, Any]:
        if self.zone is None:
            return {
                "found": False,
                "school_name": None,
                "district_name": None,
                "school_year": None,
                "source_name": None,
                "source_url": None,
                "confidence": "low",
                "boundary_distance_miles": self.boundary_distance_miles,
                "near_boundary": self.near_boundary,
                "warning": self.warning,
                "verification": "Check the official district school-finder or boundary map.",
            }
        metadata = json_loads(self.zone.metadata_json, {})
        return {
            "found": True,
            "zone_id": self.zone.id,
            "school_name": self.zone.school_name,
            "school_level": self.zone.school_level,
            "district_name": self.zone.district_name,
            "school_year": self.zone.school_year,
            "source_name": self.zone.source_name,
            "source_url": self.zone.source_url,
            "confidence": self.zone.confidence,
            "boundary_distance_miles": self.boundary_distance_miles,
            "near_boundary": self.near_boundary,
            "warning": self.warning,
            "verification": metadata.get(
                "verification",
                "Verify assignment directly with the district before relying.",
            ),
            "metadata": metadata,
        }


def download_attendance_zones(
    output_path: Path | None = None,
    source_url: str = MN_ATTENDANCE_AREAS_QUERY_URL,
    page_size: int = 2000,
) -> Path:
    """Download the official current-view Minnesota attendance areas as GeoJSON."""

    SCHOOL_ZONE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    output = output_path or SCHOOL_ZONE_CACHE_DIR / "mn_school_attendance_areas_current.geojson"
    features: list[dict[str, Any]] = []
    offset = 0
    while True:
        params = {
            "f": "geojson",
            "where": "1=1",
            "outFields": "*",
            "outSR": "4326",
            "returnGeometry": "true",
            "resultOffset": str(offset),
            "resultRecordCount": str(page_size),
        }
        response = httpx.get(source_url, params=params, timeout=60)
        response.raise_for_status()
        payload = response.json()
        page_features = payload.get("features") or []
        features.extend(page_features)
        if len(page_features) < page_size:
            break
        offset += page_size
    output.write_text(json_dumps(feature_collection(features)), encoding="utf-8")
    return output


def import_attendance_zones(
    session: Session,
    path: Path,
    *,
    source_name: str = MN_ATTENDANCE_SOURCE_NAME,
    source_url: str = MN_ATTENDANCE_AREAS_LAYER_URL,
    school_year: str | None = None,
    replace: bool = True,
) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("type") != "FeatureCollection":
        raise ValueError("Attendance-zone import expects a GeoJSON FeatureCollection.")
    features = payload.get("features") or []
    inferred_year = school_year or _infer_school_year_from_features(features) or _infer_year(path.name)
    if replace:
        session.execute(
            delete(SchoolAttendanceZone).where(
                SchoolAttendanceZone.source_name == source_name,
                SchoolAttendanceZone.school_year == inferred_year,
            )
        )
    imported = 0
    for feature in features:
        props = _casefold_props(feature.get("properties") or {})
        geometry = feature.get("geometry")
        if not geometry:
            continue
        school_name = _first_nonblank(props, "elem_name", "elementary", "elementary_name")
        if not school_name:
            continue
        district_name = _first_nonblank(props, "sdprefname", "district_name", "prefname", "shortname")
        metadata = {
            "source_feature_id": props.get("objectid") or props.get("object_id"),
            "elementary_multi": props.get("elem_multi"),
            "middle_school": props.get("midd_name"),
            "high_school": props.get("high_name"),
            "district_number": props.get("sdnumber"),
            "district_type": props.get("sdtype"),
            "verification": "Verify assignment with the district school finder or enrollment office.",
            "raw_properties": props,
        }
        session.add(
            SchoolAttendanceZone(
                school_name=str(school_name),
                school_level="elementary",
                district_name=str(district_name) if district_name else None,
                school_year=inferred_year,
                source_name=source_name,
                source_url=source_url,
                geometry_geojson=geometry_to_geojson(geometry),
                confidence="high" if source_url else "medium",
                metadata_json=json_dumps(metadata),
            )
        )
        imported += 1
    _upsert_school_layer(session, source_name, source_url, inferred_year, imported)
    return imported


def identify_elementary_zone(
    session: Session,
    *,
    lat: float,
    lon: float,
    boundary_threshold_miles: float = 0.10,
) -> SchoolZoneLookup:
    zones = session.execute(
        select(SchoolAttendanceZone).where(SchoolAttendanceZone.school_level == "elementary")
    ).scalars()
    nearest: tuple[SchoolAttendanceZone, float] | None = None
    for zone in zones:
        geometry = json_loads(zone.geometry_geojson, {})
        if not geometry:
            continue
        if point_in_geometry(lon, lat, geometry):
            distance = boundary_distance_miles(lon, lat, geometry)
            near_boundary = distance is not None and distance <= boundary_threshold_miles
            warning = NEAR_BOUNDARY_WARNING if near_boundary else VERIFY_ASSIGNMENT_WARNING
            return SchoolZoneLookup(zone, distance, near_boundary, warning)
        distance = boundary_distance_miles(lon, lat, geometry)
        if distance is not None and (nearest is None or distance < nearest[1]):
            nearest = (zone, distance)
    warning = (
        f"No imported elementary attendance zone contains this point. {VERIFY_ASSIGNMENT_WARNING}"
    )
    near_boundary = bool(nearest and nearest[1] <= boundary_threshold_miles)
    if near_boundary:
        warning = f"No zone contains this point, but it is close to an imported boundary. {NEAR_BOUNDARY_WARNING}"
    return SchoolZoneLookup(None, nearest[1] if nearest else None, near_boundary, warning)


def identify_property_elementary_zone(
    session: Session,
    prop: Property,
    boundary_threshold_miles: float = 0.10,
) -> SchoolZoneLookup | None:
    if prop.latitude is None or prop.longitude is None:
        return None
    return identify_elementary_zone(
        session,
        lat=prop.latitude,
        lon=prop.longitude,
        boundary_threshold_miles=boundary_threshold_miles,
    )


def school_zones_geojson(session: Session, elementary_only: bool = True) -> dict[str, Any]:
    stmt = select(SchoolAttendanceZone)
    if elementary_only:
        stmt = stmt.where(SchoolAttendanceZone.school_level == "elementary")
    zones = session.execute(stmt.order_by(SchoolAttendanceZone.school_name)).scalars().all()
    from realestate.schools import school_context_for_zone

    features = []
    for zone in zones:
        school_context = school_context_for_zone(session, zone)
        features.append(
            geometry_feature(
                zone.geometry_geojson,
                {
                "id": zone.id,
                "school_name": zone.school_name,
                "school_level": zone.school_level,
                "district_name": zone.district_name,
                "school_year": zone.school_year,
                "source_name": zone.source_name,
                "source_url": zone.source_url,
                "confidence": zone.confidence,
                "warning": VERIFY_ASSIGNMENT_WARNING,
                "school_location": school_context.get("school_location"),
                "academic_profiles": school_context.get("academic_profiles", []),
                "ranking_statuses": school_context.get("ranking_statuses", []),
                "niche_rank": school_context.get("niche_rank"),
                "niche_grade": school_context.get("niche_grade"),
                "us_news_rank": school_context.get("us_news_rank"),
                "us_news_rating": school_context.get("us_news_rating"),
            },
            feature_id=zone.id,
        )
        )
    return feature_collection(features)


def _upsert_school_layer(
    session: Session,
    source_name: str,
    source_url: str,
    school_year: str | None,
    imported_count: int,
) -> None:
    layer = session.execute(
        select(MapLayer).where(
            MapLayer.name == "Elementary attendance zones",
            MapLayer.layer_type == "school_attendance_zone",
        )
    ).scalar_one_or_none()
    if layer is None:
        layer = MapLayer(
            name="Elementary attendance zones",
            layer_type="school_attendance_zone",
            geometry_type="Polygon",
            enabled_by_default=False,
        )
        session.add(layer)
    layer.source_name = source_name
    layer.source_url = source_url
    layer.retrieved_at = datetime.now(UTC)
    layer.metadata_json = json_dumps(
        {
            "school_year": school_year,
            "feature_count": imported_count,
            "assignment_warning": VERIFY_ASSIGNMENT_WARNING,
            "near_boundary_warning": NEAR_BOUNDARY_WARNING,
        }
    )
    layer.style_json = json_dumps(
        {
            "stroke": "#27615a",
            "weight": 1,
            "fillColor": "#7db8a7",
            "fillOpacity": 0.16,
        }
    )


def _casefold_props(props: dict[str, Any]) -> dict[str, Any]:
    return {str(key).lower(): value for key, value in props.items()}


def _first_nonblank(props: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = props.get(key.lower())
        if value not in (None, ""):
            return value
    return None


def _infer_school_year_from_features(features: list[dict[str, Any]]) -> str | None:
    for feature in features[:10]:
        props = _casefold_props(feature.get("properties") or {})
        for key in ("school_year", "schyear", "year", "sy"):
            value = props.get(key)
            if value not in (None, ""):
                return str(value)
    return None


def _infer_year(value: str) -> str | None:
    match = re.search(r"20\d{2}", value)
    return match.group(0) if match else None
