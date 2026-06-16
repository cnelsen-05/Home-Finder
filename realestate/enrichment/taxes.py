from __future__ import annotations

from sqlalchemy.orm import Session

from realestate.models import Property
from realestate.sources.public_records.base import store_adapter_results
from realestate.sources.public_records.hennepin_property import HennepinPropertyAdapter


def enrich_tax_context(session: Session, prop: Property):
    adapter = HennepinPropertyAdapter()
    if (prop.county or "").lower() != "hennepin":
        return []
    return store_adapter_results(session, prop, [adapter.lookup_property(prop)])
