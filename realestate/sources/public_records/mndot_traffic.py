from __future__ import annotations

from typing import Any

import httpx

from realestate.models import Property
from realestate.sources.base import AdapterResult
from realestate.sources.public_records.base import PublicRecordAdapter


class MnDOTTrafficAdapter(PublicRecordAdapter):
    source_name = "MnDOT Current AADT"
    record_type = "traffic_volume"
    service_url = (
        "https://webgis.dot.state.mn.us/65agsf1/rest/services/sdw_incdt/"
        "AADT_SEGMENT_CURRENT/FeatureServer/0/query"
    )

    def __init__(self, radius_meters: int = 800) -> None:
        self.radius_meters = radius_meters

    def is_configured(self) -> bool:
        return True

    def lookup_property(self, prop: Property) -> AdapterResult:
        if prop.latitude is None or prop.longitude is None:
            return AdapterResult(
                source_name=self.source_name,
                source_url=self.service_url,
                record_type=self.record_type,
                status="skipped",
                confidence="low",
                notes="Property has no latitude/longitude; traffic-volume lookup cannot run.",
            )
        params: dict[str, Any] = {
            "f": "json",
            "geometry": f"{prop.longitude},{prop.latitude}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "distance": str(self.radius_meters),
            "units": "esriSRUnit_Meter",
            "outFields": (
                "ROUTE_LABEL,STREET_NAME,LOCATION_DESCRIPTION,COUNTY,COMMUNITY,"
                "CURRENT_YEAR,CURRENT_VOLUME,DATA_TYPE,AADT_COMMENTS"
            ),
            "returnGeometry": "false",
            "resultRecordCount": "20",
        }
        try:
            response = httpx.get(self.service_url, params=params, timeout=20)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return AdapterResult(
                source_name=self.source_name,
                source_url=self.service_url,
                record_type=self.record_type,
                status="error",
                confidence="low",
                parsed={"radius_meters": self.radius_meters},
                notes=f"MnDOT AADT lookup failed: {exc}",
            )
        return result_from_traffic_payload(payload, self.service_url, self.radius_meters)


def result_from_traffic_payload(
    payload: dict[str, Any], source_url: str, radius_meters: int
) -> AdapterResult:
    features = payload.get("features", [])
    rows = [feature.get("attributes") or {} for feature in features]
    rows = sorted(rows, key=lambda row: _volume(row), reverse=True)
    top = rows[:10]
    highest = _volume(top[0]) if top else None
    return AdapterResult(
        source_name=MnDOTTrafficAdapter.source_name,
        source_url=source_url,
        record_type=MnDOTTrafficAdapter.record_type,
        status="found" if rows else "not_found",
        parsed={
            "radius_meters": radius_meters,
            "segment_count": len(rows),
            "highest_current_volume": highest,
            "top_segments": [
                {
                    "street_name": row.get("STREET_NAME"),
                    "route_label": row.get("ROUTE_LABEL"),
                    "location_description": row.get("LOCATION_DESCRIPTION"),
                    "community": row.get("COMMUNITY"),
                    "current_year": row.get("CURRENT_YEAR"),
                    "current_volume": row.get("CURRENT_VOLUME"),
                    "data_type": row.get("DATA_TYPE"),
                    "comments": row.get("AADT_COMMENTS"),
                }
                for row in top
            ],
        },
        raw=payload,
        confidence="medium" if rows else "low",
        notes=(
            f"Traffic-volume screen within {radius_meters} meters; use as a road-noise proxy, "
            "not a measured noise reading."
            if rows
            else f"No MnDOT AADT segments found within {radius_meters} meters."
        ),
    )


def _volume(row: dict[str, Any]) -> int:
    try:
        return int(row.get("CURRENT_VOLUME") or 0)
    except (TypeError, ValueError):
        return 0
