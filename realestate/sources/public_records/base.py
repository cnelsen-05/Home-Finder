from __future__ import annotations

import json
from collections.abc import Iterable

from sqlalchemy.orm import Session

from realestate.models import Property, PublicRecord
from realestate.sources.base import AdapterResult, SourceAdapter


class PublicRecordAdapter(SourceAdapter):
    record_type = "public_record"

    def lookup_property(self, prop: Property) -> AdapterResult:
        return AdapterResult(
            source_name=self.source_name,
            record_type=self.record_type,
            status="skipped",
            notes="Adapter is not configured.",
        )


def store_adapter_results(
    session: Session, prop: Property, results: Iterable[AdapterResult]
) -> list[PublicRecord]:
    records: list[PublicRecord] = []
    for result in results:
        record = PublicRecord(
            property=prop,
            source_name=result.source_name,
            source_url=result.source_url,
            retrieved_at=result.retrieved_at,
            record_type=result.record_type,
            parsed_json=json.dumps(
                {"status": result.status, "data": result.parsed}, sort_keys=True, default=str
            ),
            raw_json=json.dumps(result.raw, sort_keys=True, default=str) if result.raw is not None else None,
            confidence=result.confidence,
            notes=result.notes,
        )
        session.add(record)
        records.append(record)
    return records
