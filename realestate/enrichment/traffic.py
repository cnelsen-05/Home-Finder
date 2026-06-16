from __future__ import annotations

from sqlalchemy.orm import Session

from realestate.models import Property
from realestate.sources.public_records.base import store_adapter_results
from realestate.sources.public_records.mndot_traffic import MnDOTTrafficAdapter


def enrich_traffic_context(session: Session, prop: Property):
    adapter = MnDOTTrafficAdapter()
    return store_adapter_results(session, prop, [adapter.lookup_property(prop)])
