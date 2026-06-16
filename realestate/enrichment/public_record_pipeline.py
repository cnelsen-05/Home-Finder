from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from realestate.enrichment.amenities import enrich_amenity_context
from realestate.enrichment.commute import refresh_approximate_commutes
from realestate.enrichment.environment import enrich_environment_context
from realestate.enrichment.flood import enrich_flood_context
from realestate.enrichment.geocoding import enrich_geocode_context
from realestate.enrichment.parcel import enrich_parcel_context
from realestate.enrichment.school_district import enrich_school_district_context
from realestate.enrichment.taxes import enrich_tax_context
from realestate.enrichment.traffic import enrich_traffic_context
from realestate.models import Listing, Property
from realestate.sources.public_records.base import store_adapter_results
from realestate.sources.public_records.county_generic import GenericCountyPublicRecordAdapter
from realestate.sources.public_records.minneapolis_open_data import MinneapolisOpenDataAdapter
from realestate.sources.public_records.mn_geospatial_commons import MNGeospatialCommonsAdapter


def resolve_property(session: Session, property_or_listing_id: int) -> Property | None:
    prop = session.get(Property, property_or_listing_id)
    if prop:
        return prop
    listing = session.get(Listing, property_or_listing_id)
    return listing.property if listing else None


def enrich_property(session: Session, prop: Property) -> int:
    records = []
    record_count = enrich_geocode_context(session, prop)
    records.extend(enrich_tax_context(session, prop))
    records.extend(enrich_parcel_context(session, prop))
    records.extend(enrich_flood_context(session, prop))
    records.extend(enrich_amenity_context(session, prop))
    records.extend(enrich_school_district_context(session, prop))
    records.extend(enrich_traffic_context(session, prop))
    records.extend(enrich_environment_context(session, prop))
    for adapter in [MinneapolisOpenDataAdapter(), MNGeospatialCommonsAdapter(), GenericCountyPublicRecordAdapter()]:
        records.extend(store_adapter_results(session, prop, [adapter.lookup_property(prop)]))
    refresh_approximate_commutes(session, prop)
    session.flush()
    return record_count + len(records)


def enrich_all_favorites(session: Session) -> int:
    listings = session.execute(select(Listing)).scalars().all()
    seen: set[int] = set()
    count = 0
    for listing in listings:
        if listing.property_id in seen:
            continue
        seen.add(listing.property_id)
        count += enrich_property(session, listing.property)
    return count
