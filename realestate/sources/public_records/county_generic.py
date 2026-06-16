from __future__ import annotations

from realestate.models import Property
from realestate.sources.base import AdapterResult
from realestate.sources.public_records.base import PublicRecordAdapter


class GenericCountyPublicRecordAdapter(PublicRecordAdapter):
    source_name = "Generic County Public Records"
    record_type = "county_fallback"

    def lookup_property(self, prop: Property) -> AdapterResult:
        return AdapterResult(
            source_name=f"{prop.county or 'Unknown'} County Public Records",
            record_type=self.record_type,
            status="skipped",
            confidence="low",
            parsed={"county": prop.county, "address": prop.address_line1},
            notes=(
                "No county-specific adapter is configured. Check assessor, property-tax, and GIS "
                "portals manually or add a compliant adapter."
            ),
        )
