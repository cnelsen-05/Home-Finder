from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import Session

from realestate.enrichment.commute import haversine_miles
from realestate.models import AmenityDistance, Property
from realestate.sources.public_records.base import store_adapter_results
from realestate.sources.public_records.osm import OSMAdapter


def enrich_amenity_context(session: Session, prop: Property):
    adapter = OSMAdapter()
    result = adapter.lookup_property(prop)
    records = store_adapter_results(session, prop, [result])
    if result.status == "found":
        _replace_osm_amenity_distances(session, prop, result.parsed.get("amenities", []))
    return records


def _replace_osm_amenity_distances(session: Session, prop: Property, amenities: list[dict]) -> None:
    session.execute(
        delete(AmenityDistance).where(
            AmenityDistance.property_id == prop.id,
            AmenityDistance.source_name == "OpenStreetMap",
        )
    )
    for amenity in amenities:
        distance = haversine_miles(
            prop.latitude,
            prop.longitude,
            amenity.get("latitude"),
            amenity.get("longitude"),
        )
        session.add(
            AmenityDistance(
                property_id=prop.id,
                amenity_type=amenity.get("amenity_type") or "other",
                amenity_name=amenity.get("name"),
                distance_miles=round(distance, 2) if distance is not None else None,
                source_name="OpenStreetMap",
            )
        )
