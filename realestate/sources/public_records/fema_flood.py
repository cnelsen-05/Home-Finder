from __future__ import annotations

from typing import Any

import httpx

from realestate.models import Property
from realestate.sources.base import AdapterResult
from realestate.sources.public_records.base import PublicRecordAdapter


class FEMAFloodAdapter(PublicRecordAdapter):
    source_name = "FEMA National Flood Hazard Layer"
    record_type = "flood_zone"
    service_url = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"

    def is_configured(self) -> bool:
        return True

    def lookup_property(self, prop: Property) -> AdapterResult:
        if prop.latitude is None or prop.longitude is None:
            return AdapterResult(
                source_name=self.source_name,
                record_type=self.record_type,
                status="skipped",
                confidence="low",
                notes="Property has no latitude/longitude; flood-zone lookup cannot run.",
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
                parsed={"latitude": prop.latitude, "longitude": prop.longitude},
                notes=f"FEMA NFHL lookup failed: {exc}",
            )
        return result_from_nfhl_payload(payload, self.service_url)


def result_from_nfhl_payload(payload: dict[str, Any], source_url: str) -> AdapterResult:
    features = payload.get("features", [])
    attrs = (features[0].get("attributes") if features else {}) or {}
    flood_zone = attrs.get("FLD_ZONE")
    subtype = attrs.get("ZONE_SUBTY")
    sfha = attrs.get("SFHA_TF")
    status = "found" if attrs else "not_found"
    confidence = "high" if attrs else "low"
    notes = None if attrs else "No FEMA NFHL flood-hazard zone intersected the property point."
    return AdapterResult(
        source_name=FEMAFloodAdapter.source_name,
        source_url=source_url,
        record_type=FEMAFloodAdapter.record_type,
        status=status,
        parsed={
            "feature_count": len(features),
            "flood_zone": flood_zone,
            "zone_subtype": subtype,
            "special_flood_hazard_area": sfha,
            "dfirm_id": attrs.get("DFIRM_ID"),
            "source_citation": attrs.get("SOURCE_CIT"),
        },
        raw=payload,
        confidence=confidence,
        notes=notes,
    )
