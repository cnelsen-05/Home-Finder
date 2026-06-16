from __future__ import annotations

import os
import re
from typing import Any

import httpx

from realestate.models import Property
from realestate.sources.base import AdapterResult
from realestate.sources.public_records.base import PublicRecordAdapter


class HennepinGISAdapter(PublicRecordAdapter):
    source_name = "MetroGIS Regional Parcels"
    record_type = "parcel_gis"
    default_service_root = "https://arcgis.metc.state.mn.us/data1/rest/services/parcels/Parcels/FeatureServer"
    county_layers = {
        "Anoka": 0,
        "Carver": 1,
        "Dakota": 2,
        "Hennepin": 3,
        "Ramsey": 4,
        "Scott": 5,
        "Washington": 6,
    }

    def __init__(self, feature_service_url: str | None = None) -> None:
        self.feature_service_url = feature_service_url or os.getenv("METROGIS_PARCELS_FEATURE_SERVICE_URL")
        self.service_root = os.getenv("METROGIS_PARCELS_SERVICE_ROOT") or self.default_service_root

    def is_configured(self) -> bool:
        return bool(self.feature_service_url or self.service_root)

    def lookup_property(self, prop: Property) -> AdapterResult:
        if not self.is_configured():
            return AdapterResult(
                source_name=self.source_name,
                record_type=self.record_type,
                status="skipped",
                confidence="low",
                notes=(
                    "A public ArcGIS FeatureServer endpoint is not configured before "
                    "attempting parcel GIS lookup."
                ),
            )
        address_match = self._lookup_by_address(prop)
        if address_match is not None:
            return address_match
        if prop.latitude is None or prop.longitude is None:
            return AdapterResult(
                source_name=self.source_name,
                source_url=self.service_root,
                record_type=self.record_type,
                status="skipped",
                confidence="low",
                notes="No address match found and property has no latitude/longitude for point lookup.",
            )
        return self._lookup_by_point(prop)

    def _lookup_by_point(self, prop: Property) -> AdapterResult:
        all_features: list[dict[str, Any]] = []
        raw_by_county: dict[str, Any] = {}
        for county, layer_id in self.county_layers.items():
            query_url = self._query_url_for_layer(layer_id)
            params: dict[str, Any] = {
                "f": "json",
                "geometry": f"{prop.longitude},{prop.latitude}",
                "geometryType": "esriGeometryPoint",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "*",
                "returnGeometry": "true",
            }
            response = httpx.get(query_url, params=params, timeout=20)
            response.raise_for_status()
            payload = response.json()
            raw_by_county[county] = payload
            for feature in payload.get("features", []):
                feature.setdefault("attributes", {})["_matched_county_layer"] = county
                all_features.append(feature)
        return self._result_from_features(
            all_features,
            raw_by_county,
            self.service_root,
            "point-in-polygon parcel lookup",
            prop,
        )

    def _lookup_by_address(self, prop: Property) -> AdapterResult | None:
        parsed = _parse_street_address(prop.address_line1)
        if parsed is None:
            return None
        number, street_name = parsed
        all_features: list[dict[str, Any]] = []
        raw_by_county: dict[str, Any] = {}
        for county, layer_id in self.county_layers.items():
            query_url = self._query_url_for_layer(layer_id)
            where = f"ANUMBER = {number} AND UPPER(ST_NAME) LIKE '%{street_name.upper()}%'"
            params: dict[str, Any] = {
                "f": "json",
                "where": where,
                "outFields": "*",
                "returnGeometry": "true",
            }
            response = httpx.get(query_url, params=params, timeout=20)
            response.raise_for_status()
            payload = response.json()
            raw_by_county[county] = payload
            for feature in payload.get("features", []):
                feature.setdefault("attributes", {})["_matched_county_layer"] = county
                all_features.append(feature)
        if not all_features:
            return None
        return self._result_from_features(
            all_features,
            raw_by_county,
            self.service_root,
            "parcel address-field lookup",
            prop,
        )

    def _result_from_features(
        self,
        features: list[dict[str, Any]],
        raw: dict[str, Any],
        source_url: str,
        method: str,
        prop: Property,
    ) -> AdapterResult:
        ranked = sorted(features, key=lambda feature: _match_score(feature, prop), reverse=True)
        best = ranked[0] if ranked else None
        status = "found" if best else "not_found"
        confidence = "high" if best and _match_score(best, prop) >= 80 else "medium" if best else "low"
        return AdapterResult(
            source_name=self.source_name,
            source_url=source_url,
            record_type=self.record_type,
            status=status,
            parsed={
                "feature_count": len(features),
                "match_method": method,
                "first_feature": best,
                "candidate_features": ranked[:5],
            },
            raw=raw,
            confidence=confidence,
            notes=None if best else "No MetroGIS parcel feature matched the property.",
        )
        params: dict[str, Any] = {
            "f": "json",
            "geometry": f"{prop.longitude},{prop.latitude}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*",
            "returnGeometry": "true",
        }
        response = httpx.get(self.query_url, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()
        features = payload.get("features", [])
        status = "found" if features else "not_found"
        confidence = "medium" if features else "low"
        return AdapterResult(
            source_name=self.source_name,
            source_url=self.feature_service_url,
            record_type=self.record_type,
            status=status,
            parsed={"feature_count": len(features), "first_feature": features[0] if features else None},
            raw=payload,
            confidence=confidence,
            notes=None if features else "No parcel feature matched the property point.",
        )

    @property
    def query_url(self) -> str:
        if self.feature_service_url.rstrip("/").endswith("/query"):
            return self.feature_service_url
        return f"{self.feature_service_url.rstrip('/')}/query"

    def _query_url_for_layer(self, layer_id: int) -> str:
        if self.feature_service_url:
            return self.query_url
        return f"{self.service_root.rstrip('/')}/{layer_id}/query"


def _parse_street_address(address: str | None) -> tuple[int, str] | None:
    if not address:
        return None
    match = re.match(r"^\s*(\d+)\s+(.+?)\s*$", address)
    if not match:
        return None
    number = int(match.group(1))
    street = re.sub(r"\b(CIR|CIRCLE|LN|LANE|RD|ROAD|DR|DRIVE|AVE|AVENUE|TER|TERRACE|CT|COURT|PKWY|PARKWAY|N|S|E|W)\b", "", match.group(2), flags=re.I)
    street = re.sub(r"\s+", " ", street).strip()
    return (number, street) if street else None


def _match_score(feature: dict[str, Any], prop: Property) -> int:
    attributes = feature.get("attributes") or {}
    score = 0
    if prop.zip and str(attributes.get("ZIP") or "").strip() == str(prop.zip).strip():
        score += 50
    city = (prop.city or "").strip().upper()
    postcomm = str(attributes.get("POSTCOMM") or attributes.get("CTU_NAME") or "").strip().upper()
    if city and city == postcomm:
        score += 30
    if attributes.get("ANUMBER") is not None:
        score += 10
    if attributes.get("ST_NAME"):
        score += 10
    return score
