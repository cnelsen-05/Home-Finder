from __future__ import annotations

from typing import Any

import httpx

from realestate.models import Property
from realestate.sources.base import AdapterResult
from realestate.sources.public_records.base import PublicRecordAdapter


class OSMAdapter(PublicRecordAdapter):
    source_name = "OpenStreetMap"
    record_type = "amenity_distance"
    overpass_url = "https://overpass-api.de/api/interpreter"

    def __init__(self, radius_meters: int = 3500) -> None:
        self.radius_meters = radius_meters

    def is_configured(self) -> bool:
        return True

    def lookup_property(self, prop: Property) -> AdapterResult:
        if prop.latitude is None or prop.longitude is None:
            return AdapterResult(
                source_name=self.source_name,
                record_type=self.record_type,
                status="skipped",
                confidence="low",
                notes="Property has no latitude/longitude; OSM nearby amenity lookup cannot run.",
            )
        query = overpass_amenity_query(prop.latitude, prop.longitude, self.radius_meters)
        try:
            response = httpx.post(
                self.overpass_url,
                data={"data": query},
                headers={"User-Agent": "HomeAnalyze personal real-estate research assistant"},
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return AdapterResult(
                source_name=self.source_name,
                source_url=self.overpass_url,
                record_type=self.record_type,
                status="error",
                confidence="low",
                parsed={"radius_meters": self.radius_meters},
                notes=f"OpenStreetMap/Overpass amenity lookup failed: {exc}",
            )
        return result_from_overpass_payload(payload, self.overpass_url, self.radius_meters)


def overpass_amenity_query(latitude: float, longitude: float, radius_meters: int) -> str:
    return f"""
[out:json][timeout:25];
(
  node(around:{radius_meters},{latitude},{longitude})["leisure"~"park|playground|fitness_centre|nature_reserve"];
  way(around:{radius_meters},{latitude},{longitude})["leisure"~"park|playground|fitness_centre|nature_reserve"];
  relation(around:{radius_meters},{latitude},{longitude})["leisure"~"park|playground|fitness_centre|nature_reserve"];
  node(around:{radius_meters},{latitude},{longitude})["amenity"~"childcare|kindergarten|school"];
  way(around:{radius_meters},{latitude},{longitude})["amenity"~"childcare|kindergarten|school"];
  relation(around:{radius_meters},{latitude},{longitude})["amenity"~"childcare|kindergarten|school"];
  node(around:{radius_meters},{latitude},{longitude})["route"~"hiking|bicycle"];
  way(around:{radius_meters},{latitude},{longitude})["highway"~"path|cycleway|footway"]["name"];
);
out center tags 80;
"""


def result_from_overpass_payload(
    payload: dict[str, Any], source_url: str, radius_meters: int
) -> AdapterResult:
    amenities = []
    seen: set[tuple[str, str, str]] = set()
    for element in payload.get("elements", []):
        tags = element.get("tags") or {}
        name = tags.get("name")
        amenity_type = _amenity_type(tags)
        lat, lon = _element_point(element)
        if not amenity_type or not name or lat is None or lon is None:
            continue
        key = (amenity_type, name, str(element.get("id")))
        if key in seen:
            continue
        seen.add(key)
        amenities.append(
            {
                "osm_type": element.get("type"),
                "osm_id": element.get("id"),
                "name": name,
                "amenity_type": amenity_type,
                "latitude": lat,
                "longitude": lon,
                "tags": {
                    key: value
                    for key, value in tags.items()
                    if key in {"amenity", "leisure", "name", "brand", "operator", "highway", "route"}
                },
            }
        )
    return AdapterResult(
        source_name=OSMAdapter.source_name,
        source_url=source_url,
        record_type=OSMAdapter.record_type,
        status="found" if amenities else "not_found",
        parsed={
            "radius_meters": radius_meters,
            "amenity_count": len(amenities),
            "amenities": amenities[:50],
        },
        raw=payload,
        confidence="medium" if amenities else "low",
        notes=(
            f"OpenStreetMap amenity screen within {radius_meters} meters; coverage depends on community tagging."
            if amenities
            else f"No tagged OSM amenities found within {radius_meters} meters."
        ),
    )


def _element_point(element: dict[str, Any]) -> tuple[float | None, float | None]:
    if element.get("lat") is not None and element.get("lon") is not None:
        return float(element["lat"]), float(element["lon"])
    center = element.get("center") or {}
    if center.get("lat") is not None and center.get("lon") is not None:
        return float(center["lat"]), float(center["lon"])
    return None, None


def _amenity_type(tags: dict[str, Any]) -> str | None:
    leisure = tags.get("leisure")
    amenity = tags.get("amenity")
    highway = tags.get("highway")
    route = tags.get("route")
    if leisure == "fitness_centre":
        return "gym"
    if leisure in {"park", "playground", "nature_reserve"}:
        return str(leisure)
    if amenity in {"childcare", "kindergarten", "school"}:
        return str(amenity)
    if route in {"hiking", "bicycle"} or highway in {"path", "cycleway", "footway"}:
        return "trail"
    return None
