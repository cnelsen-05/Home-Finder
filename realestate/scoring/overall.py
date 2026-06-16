from __future__ import annotations

import json

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from realestate.config import load_scoring_weights
from realestate.models import IssueFlag, Listing, PublicRecord, ReviewScore
from realestate.schemas import ScoreComponent, ScoreExplanation
from realestate.scoring.daily_life import score_daily_life
from realestate.scoring.explanations import clamp, component, summary_for_bucket
from realestate.scoring.preference import score_preference
from realestate.scoring.quality import score_quality
from realestate.scoring.risk import score_risk
from realestate.scoring.value import score_value


def score_listing(session: Session, listing: Listing, preferences: dict) -> ReviewScore:
    public_records = session.execute(
        select(PublicRecord).where(PublicRecord.property_id == listing.property_id)
    ).scalars().all()
    quality = score_quality(listing, preferences)
    value = score_value(listing, preferences, session)
    daily = score_daily_life(listing, preferences, session)
    risk = score_risk(listing, preferences, public_records)
    preference = score_preference(listing, preferences)
    overall = _overall_component(quality, value, daily, risk, preference)
    bucket = recommendation_bucket(overall.score, risk.score)
    explanation = ScoreExplanation(
        quality=quality,
        value=value,
        daily_life=daily,
        risk=risk,
        preference=preference,
        overall=overall,
        recommendation_bucket=bucket,
        recommendation_summary=summary_for_bucket(bucket),
    )
    score_row = ReviewScore(
        listing=listing,
        quality_score=quality.score,
        value_score=value.score,
        daily_life_score=daily.score,
        risk_score=risk.score,
        preference_score=preference.score,
        overall_score=overall.score,
        recommendation_bucket=bucket,
        explanation_json=explanation.model_dump_json(indent=2),
    )
    session.add(score_row)
    _replace_issue_flags(session, listing, explanation)
    session.flush()
    return score_row


def score_all_listings(session: Session, preferences: dict) -> list[ReviewScore]:
    listings = session.execute(select(Listing)).scalars().all()
    return [score_listing(session, listing, preferences) for listing in listings]


def latest_score(session: Session, listing: Listing) -> ReviewScore | None:
    return session.execute(
        select(ReviewScore)
        .where(ReviewScore.listing_id == listing.id)
        .order_by(ReviewScore.scored_at.desc())
    ).scalars().first()


def explanation_from_score(score_row: ReviewScore) -> dict:
    return json.loads(score_row.explanation_json)


def recommendation_bucket(overall: float, risk: float) -> str:
    if risk < 35:
        return "likely_skip"
    if risk < 50:
        return "agent_question_first"
    if overall >= 88:
        return "must_review"
    if overall >= 78:
        return "strong_tour_candidate"
    if overall >= 68:
        return "worth_reviewing"
    if overall >= 58:
        return "watch"
    if overall >= 48:
        return "low_priority"
    return "likely_skip"


def _overall_component(
    quality: ScoreComponent,
    value: ScoreComponent,
    daily: ScoreComponent,
    risk: ScoreComponent,
    preference: ScoreComponent,
) -> ScoreComponent:
    weights = load_scoring_weights()
    overall_weights = weights.get("overall", {})
    daily_w = float(overall_weights.get("daily_life_score", 0.35))
    quality_w = float(overall_weights.get("quality_score", 0.30))
    value_w = float(overall_weights.get("value_score", 0.25))
    pref_w = float(overall_weights.get("preference_score", 0.10))
    weighted = (
        daily.score * daily_w
        + quality.score * quality_w
        + value.score * value_w
        + preference.score * pref_w
    )
    penalty_cfg = weights.get("risk_penalty", {})
    neutral = float(penalty_cfg.get("neutral_threshold", 70))
    multiplier = float(penalty_cfg.get("multiplier", 0.30))
    risk_penalty = max(0.0, neutral - risk.score) * multiplier
    score = clamp(weighted - risk_penalty)
    positives = [
        "Overall score weights daily life, quality, value, and preference fit.",
    ]
    negatives = []
    if risk_penalty:
        negatives.append(
            f"Risk/unknowns penalty applied because knownness score is below {neutral:.0f}."
        )
    missing = sorted(
        set(
            quality.missing_data
            + value.missing_data
            + daily.missing_data
            + risk.missing_data
            + preference.missing_data
        )
    )
    confidence = "low" if len(missing) > 8 or risk.score < 50 else "medium" if missing else "high"
    return component(score, positives, negatives, missing, confidence=confidence)


def _replace_issue_flags(session: Session, listing: Listing, explanation: ScoreExplanation) -> None:
    if listing.id is not None:
        session.execute(delete(IssueFlag).where(IssueFlag.listing_id == listing.id))
    flags = []
    for message in explanation.risk.negative_drivers[:8]:
        flags.append(
            IssueFlag(
                listing=listing,
                category="risk",
                severity="medium" if explanation.risk.score >= 50 else "high",
                title="Verify risk/unknown",
                description=message,
                evidence=listing.description,
                source=listing.source or "listing_import",
                confidence=explanation.risk.confidence,
            )
        )
    for message in explanation.value.negative_drivers[:4]:
        flags.append(
            IssueFlag(
                listing=listing,
                category="value",
                severity="medium",
                title="Review value concern",
                description=message,
                evidence=str(listing.list_price),
                source=listing.source or "listing_import",
                confidence=explanation.value.confidence,
            )
        )
    session.add_all(flags)
