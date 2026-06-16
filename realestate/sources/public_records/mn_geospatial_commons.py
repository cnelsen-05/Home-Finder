from __future__ import annotations

from realestate.models import Property
from realestate.sources.base import AdapterResult
from realestate.sources.public_records.base import PublicRecordAdapter


class MNGeospatialCommonsAdapter(PublicRecordAdapter):
    source_name = "Minnesota Geospatial Commons"
    record_type = "state_geospatial_dataset"

    def lookup_property(self, prop: Property) -> AdapterResult:
        return AdapterResult(
            source_name=self.source_name,
            record_type=self.record_type,
            status="skipped",
            confidence="low",
            parsed={"county_hint": prop.county, "city": prop.city},
            notes=(
                "Dataset discovery/import is not configured in Phase 1. Use this adapter for "
                "county parcels, municipal boundaries, school districts, and transportation layers later."
            ),
        )
