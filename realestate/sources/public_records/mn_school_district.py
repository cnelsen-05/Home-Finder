from __future__ import annotations

from typing import Any

import httpx

from realestate.models import Property
from realestate.sources.base import AdapterResult
from realestate.sources.public_records.base import PublicRecordAdapter


class MNSchoolDistrictAdapter(PublicRecordAdapter):
    source_name = "Minnesota School District Boundaries"
    record_type = "school_district_boundary"
    service_url = (
        "https://services.arcgis.com/GXwOsvnLQI6EDOp7/ArcGIS/rest/services/"
        "Minnesota_School_District_Boundaries_2026/FeatureServer/0/query"
    )

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
                notes="Property has no latitude/longitude; school-boundary lookup cannot run.",
            )
        params: dict[str, Any] = {
            "f": "json",
            "geometry": f"{prop.longitude},{prop.latitude}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*",
            "returnGeometry": "false",
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
                notes=f"Minnesota school-boundary lookup failed: {exc}",
            )
        return result_from_school_payload(payload, self.service_url)


def result_from_school_payload(payload: dict[str, Any], source_url: str) -> AdapterResult:
    features = payload.get("features", [])
    attrs = (features[0].get("attributes") if features else {}) or {}
    status = "found" if attrs else "not_found"
    return AdapterResult(
        source_name=MNSchoolDistrictAdapter.source_name,
        source_url=source_url,
        record_type=MNSchoolDistrictAdapter.record_type,
        status=status,
        parsed={
            "feature_count": len(features),
            "district_name": attrs.get("prefname") or attrs.get("shortname"),
            "district_number": attrs.get("sdnumber"),
            "district_type": attrs.get("sdtype"),
            "form_id": attrs.get("formid"),
            "website": attrs.get("web_url"),
        },
        raw=payload,
        confidence="high" if attrs else "low",
        notes=None if attrs else "No Minnesota school district boundary intersected the point.",
    )
