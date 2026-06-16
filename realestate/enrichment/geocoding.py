from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.orm import Session

from realestate.models import Property
from realestate.parsing.address_parser import join_address
from realestate.sources.base import AdapterResult
from realestate.sources.public_records.base import store_adapter_results


@dataclass(frozen=True)
class GeocodeResult:
    latitude: float | None
    longitude: float | None
    source_name: str
    confidence: str
    warning: str | None = None
    matched_address: str | None = None
    raw_payload: dict[str, Any] | None = None


class Geocoder:
    source_name = "NoOpGeocoder"

    def geocode(self, address: str) -> GeocodeResult:
        return GeocodeResult(
            latitude=None,
            longitude=None,
            source_name=self.source_name,
            confidence="low",
            warning="No geocoding provider configured.",
        )


class CensusGeocoder(Geocoder):
    source_name = "US Census Geocoder"
    source_url = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"

    def __init__(self, timeout_seconds: float = 12.0) -> None:
        self.timeout_seconds = timeout_seconds

    def geocode(self, address: str) -> GeocodeResult:
        if not address:
            return GeocodeResult(
                latitude=None,
                longitude=None,
                source_name=self.source_name,
                confidence="low",
                warning="No address available for geocoding.",
            )
        params = {
            "address": address,
            "benchmark": "Public_AR_Current",
            "format": "json",
        }
        try:
            response = httpx.get(self.source_url, params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return GeocodeResult(
                latitude=None,
                longitude=None,
                source_name=self.source_name,
                confidence="low",
                warning=f"US Census geocoder request failed: {exc}",
            )
        matches = payload.get("result", {}).get("addressMatches", [])
        if not matches:
            return GeocodeResult(
                latitude=None,
                longitude=None,
                source_name=self.source_name,
                confidence="low",
                warning="US Census geocoder returned no address matches.",
                raw_payload=payload,
            )
        match = matches[0]
        coordinates = match.get("coordinates", {})
        longitude = coordinates.get("x")
        latitude = coordinates.get("y")
        return GeocodeResult(
            latitude=float(latitude) if latitude is not None else None,
            longitude=float(longitude) if longitude is not None else None,
            source_name=self.source_name,
            confidence="high" if len(matches) == 1 else "medium",
            matched_address=match.get("matchedAddress"),
            raw_payload=payload,
        )


def enrich_geocode_context(session: Session, prop: Property) -> int:
    address = join_address(prop.address_line1, prop.city, prop.state, prop.zip)
    result = CensusGeocoder().geocode(address)
    if result.latitude is not None and result.longitude is not None:
        prop.latitude = result.latitude
        prop.longitude = result.longitude
    adapter_result = AdapterResult(
        source_name=result.source_name,
        source_url=CensusGeocoder.source_url,
        record_type="geocode",
        status="found" if result.latitude is not None and result.longitude is not None else "not_found",
        parsed={
            "input_address": address,
            "matched_address": result.matched_address,
            "latitude": result.latitude,
            "longitude": result.longitude,
        },
        raw=result.raw_payload,
        confidence=result.confidence,
        notes=result.warning,
    )
    store_adapter_results(session, prop, [adapter_result])
    return 1
