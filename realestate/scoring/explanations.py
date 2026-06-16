from __future__ import annotations

from typing import Any

from realestate.schemas import ScoreComponent


def clamp(score: float) -> float:
    return round(max(0.0, min(100.0, score)), 1)


def confidence_from_missing(missing: list[str], important_field_count: int = 8) -> str:
    if not missing:
        return "high"
    if len(missing) <= max(2, important_field_count // 3):
        return "medium"
    return "low"


def fact(key: str, value: Any, source_name: str = "listing_import") -> dict[str, Any]:
    return {"key": key, "value": value, "source_name": source_name}


def component(
    score: float,
    positive: list[str] | None = None,
    negative: list[str] | None = None,
    missing: list[str] | None = None,
    facts: list[dict[str, Any]] | None = None,
    confidence: str | None = None,
) -> ScoreComponent:
    missing = missing or []
    return ScoreComponent(
        score=clamp(score),
        confidence=confidence or confidence_from_missing(missing),
        positive_drivers=positive or [],
        negative_drivers=negative or [],
        missing_data=missing,
        source_facts=facts or [],
    )


def keyword_hits(description: str | None, keywords: list[str]) -> list[str]:
    text = (description or "").lower()
    return [keyword for keyword in keywords if keyword.lower() in text]


def summary_for_bucket(bucket: str) -> str:
    return {
        "must_review": "Tour immediately or review with your agent as a top candidate.",
        "strong_tour_candidate": "Strong tour candidate if logistics and disclosures check out.",
        "worth_reviewing": "Worth reviewing; compare against stronger homes before scheduling.",
        "watch": "Watch for new information, price changes, or better supporting facts.",
        "agent_question_first": "Ask targeted agent questions before spending tour time.",
        "low_priority": "Low priority unless price, facts, or preferences change.",
        "likely_skip": "Likely skip based on current facts and unknowns.",
    }.get(bucket, "Review with current facts and verify key unknowns.")
