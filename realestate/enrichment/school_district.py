from __future__ import annotations

from sqlalchemy.orm import Session

from realestate.models import Listing, Property
from realestate.sources.public_records.base import store_adapter_results
from realestate.sources.public_records.mn_school_district import MNSchoolDistrictAdapter


def enrich_school_district_context(session: Session, prop: Property):
    adapter = MNSchoolDistrictAdapter()
    result = adapter.lookup_property(prop)
    records = store_adapter_results(session, prop, [result])
    if result.status == "found":
        district = result.parsed.get("district_name") or result.parsed.get("form_id")
        if district:
            for listing in session.query(Listing).filter(Listing.property_id == prop.id).all():
                listing.school_district = district
    return records
