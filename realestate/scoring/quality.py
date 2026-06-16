from __future__ import annotations

from realestate.models import Listing
from realestate.schemas import ScoreComponent
from realestate.scoring.explanations import component, fact, keyword_hits


def score_quality(listing: Listing, preferences: dict) -> ScoreComponent:
    score = 72.0
    positive: list[str] = []
    negative: list[str] = []
    missing: list[str] = []
    facts = [
        fact("year_built", listing.year_built),
        fact("garage_spaces", listing.garage_spaces),
        fact("description", bool(listing.description)),
    ]
    description = listing.description or ""
    risk_hits = keyword_hits(description, preferences.get("risk_keywords", []))
    watch_hits = keyword_hits(description, preferences.get("watch_keywords", []))

    if listing.year_built is None:
        missing.append("Year built missing; old-house maintenance risk cannot be calibrated.")
        score -= 4
    elif (
        preferences.get("home_requirements", {}).get("new_construction_interest") is False
        and listing.year_built >= 2023
    ):
        negative.append("Near-new construction is not a current preference for this search.")
        score -= 6
    elif listing.year_built < 1940:
        negative.append(
            "Older Twin Cities home; verify foundation, drainage, sewer line, attic ventilation, and basement condition."
        )
        score -= 8
    elif listing.year_built < 1970:
        negative.append("Mid-century home; verify mechanicals, electrical, sewer line, and drainage history.")
        score -= 4
    else:
        positive.append("Newer construction era reduces some old-house diligence burden.")
        score += 4

    if listing.garage_spaces is None:
        missing.append("Garage spaces missing; winter practicality needs verification.")
    elif listing.garage_spaces >= 2:
        positive.append("Two-car-or-better garage supports storage and winter practicality.")
        score += 5
    elif listing.garage_spaces < 1:
        negative.append("No garage shown; verify parking, storage, and winter setup.")
        score -= 10
    else:
        negative.append("Less than a two-car garage; verify storage and winter practicality.")
        score -= 4

    if not listing.description:
        missing.append("Listing description missing; fewer condition clues available.")
        score -= 5

    if listing.list_price and listing.list_price >= 700000:
        high_budget_project_hits = keyword_hits(
            description,
            [
                "as-is",
                "needs TLC",
                "bring your ideas",
                "dated",
                "cosmetic flip",
                "missing basement photos",
                "missing mechanical photos",
                "older roof",
                "original mechanicals",
            ],
        )
        if high_budget_project_hits:
            negative.append(
                "At the top of budget, project or omission signals conflict with the low-renovation preference."
            )
            score -= 14

    if risk_hits:
        for hit in risk_hits[:5]:
            negative.append(f"Listing language includes '{hit}'; verify rather than assuming a defect.")
        score -= min(25, len(risk_hits) * 5)

    for hit in watch_hits[:5]:
        positive.append(f"Listing mentions '{hit}', which may reduce diligence burden if documented.")
    score += min(10, len(watch_hits) * 2)

    if listing.finished_sqft is None:
        missing.append("Finished square footage missing; layout/space usefulness is uncertain.")
    if listing.beds is None or listing.baths is None:
        missing.append("Bed/bath count missing; functional fit is uncertain.")

    return component(score, positive, negative, missing, facts)
