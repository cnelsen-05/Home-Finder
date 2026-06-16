from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low", "unknown"]


class SourceFact(BaseModel):
    key: str
    value: Any
    source_name: str
    source_url: str | None = None
    source_identifier: str | None = None
    retrieved_at: datetime
    confidence: Confidence = "unknown"
    parse_method: str = "manual_or_unknown"
    raw_payload: dict[str, Any] | list[Any] | str | None = None
    warning: str | None = None


class ScoreComponent(BaseModel):
    score: float = Field(ge=0, le=100)
    confidence: Confidence
    positive_drivers: list[str] = Field(default_factory=list)
    negative_drivers: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    source_facts: list[dict[str, Any]] = Field(default_factory=list)


class ScoreExplanation(BaseModel):
    quality: ScoreComponent
    value: ScoreComponent
    daily_life: ScoreComponent
    risk: ScoreComponent
    preference: ScoreComponent
    overall: ScoreComponent
    recommendation_bucket: str
    recommendation_summary: str
    guardrail_note: str = (
        "Decision support only. No appraisal, legal, inspection, or brokerage conclusion. "
        "Scores avoid demographic or protected-class characteristics."
    )
