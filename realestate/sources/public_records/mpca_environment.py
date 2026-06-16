from __future__ import annotations

from typing import Any

import httpx

from realestate.enrichment.commute import haversine_miles
from realestate.models import Property
from realestate.sources.base import AdapterResult
from realestate.sources.public_records.base import PublicRecordAdapter


class MPCAEnvironmentAdapter(PublicRecordAdapter):
    source_name = "MPCA What's In My Neighborhood"
    record_type = "environmental_site_proximity"
    service_url = (
        "https://enterprise.gisdata.mn.gov/aghost/rest/services/us_mn_state_pca/"
        "env_my_neighborhood/FeatureServer/0/query"
    )

    def __init__(self, radius_meters: int = 1609) -> None:
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
                notes="Property has no latitude/longitude; MPCA proximity lookup cannot run.",
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
                "site_id,name,active_flag,address_street,address_city,address_zip,county,"
                "site_url,activity,activity_list,program_name,program_name_list,latitude,longitude"
            ),
            "returnGeometry": "false",
            "resultRecordCount": "25",
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
                notes=f"MPCA WIMN lookup failed: {exc}",
            )
        return result_from_mpca_payload(payload, self.service_url, self.radius_meters, prop)


def result_from_mpca_payload(
    payload: dict[str, Any], source_url: str, radius_meters: int, prop: Property
) -> AdapterResult:
    features = payload.get("features", [])
    sites = []
    for feature in features:
        attrs = feature.get("attributes") or {}
        distance = haversine_miles(
            prop.latitude,
            prop.longitude,
            _as_float(attrs.get("latitude")),
            _as_float(attrs.get("longitude")),
        )
        sites.append(
            {
                "name": attrs.get("name"),
                "active": attrs.get("active_flag"),
                "activity": attrs.get("activity") or attrs.get("activity_list"),
                "program": attrs.get("program_name") or attrs.get("program_name_list"),
                "address": _site_address(attrs),
                "site_url": attrs.get("site_url"),
                "distance_miles": round(distance, 2) if distance is not None else None,
            }
        )
    sites = sorted(sites, key=lambda site: site["distance_miles"] if site["distance_miles"] is not None else 99)
    active_count = sum(1 for site in sites if str(site.get("active")).upper() == "Y")
    return AdapterResult(
        source_name=MPCAEnvironmentAdapter.source_name,
        source_url=source_url,
        record_type=MPCAEnvironmentAdapter.record_type,
        status="found" if sites else "not_found",
        parsed={
            "radius_meters": radius_meters,
            "site_count": len(sites),
            "active_site_count": active_count,
            "nearest_sites": sites[:10],
        },
        raw=payload,
        confidence="medium" if sites else "low",
        notes=(
            f"Environmental-site proximity screen within {radius_meters} meters; proximity is a diligence flag, not proof of property impact."
            if sites
            else f"No MPCA WIMN sites found within {radius_meters} meters."
        ),
    )


def _site_address(attrs: dict[str, Any]) -> str:
    return ", ".join(
        str(piece).strip()
        for piece in [attrs.get("address_street"), attrs.get("address_city"), attrs.get("address_zip")]
        if piece
    )


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
