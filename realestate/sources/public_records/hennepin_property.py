from __future__ import annotations

import os

from realestate.models import Property
from realestate.parsing.address_parser import join_address
from realestate.sources.base import AdapterResult
from realestate.sources.public_records.base import PublicRecordAdapter


class HennepinPropertyAdapter(PublicRecordAdapter):
    source_name = "Hennepin County Property Information Search"
    record_type = "property_tax_assessor_link"
    search_url = "https://www.hennepin.us/residents/property/property-information-search"

    def is_configured(self) -> bool:
        return os.getenv("ENABLE_HENNEPIN_PROPERTY_LOOKUP", "").lower() == "true"

    def lookup_property(self, prop: Property) -> AdapterResult:
        address = join_address(prop.address_line1, prop.city, prop.state, prop.zip)
        if not self.is_configured():
            return AdapterResult(
                source_name=self.source_name,
                source_url=self.search_url,
                record_type=self.record_type,
                status="skipped",
                confidence="low",
                parsed={"address": address, "county_hint": prop.county},
                notes=(
                    "Automated Hennepin property lookup is disabled. Use this source link or a "
                    "user-provided export, then store facts with source metadata."
                ),
            )
        return AdapterResult(
            source_name=self.source_name,
            source_url=self.search_url,
            record_type=self.record_type,
            status="manual_review_required",
            confidence="low",
            parsed={"address": address},
            notes=(
                "No compliant machine endpoint has been configured. This adapter intentionally "
                "does not scrape the public search website."
            ),
        )
