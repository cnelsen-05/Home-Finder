from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class AdapterResult:
    source_name: str
    record_type: str
    status: str
    parsed: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] | list[Any] | str | None = None
    source_url: str | None = None
    confidence: str = "low"
    notes: str | None = None
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class SourceAdapter:
    source_name = "unknown"

    def is_configured(self) -> bool:
        return False

    def lookup(self, *_args: Any, **_kwargs: Any) -> AdapterResult:
        return AdapterResult(
            source_name=self.source_name,
            record_type="unknown",
            status="skipped",
            notes="Adapter is not configured.",
        )
