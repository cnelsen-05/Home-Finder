from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from realestate.models import Listing, SaleHistoryRecord, TaxRecord
from realestate.parsing.price_parser import price_per_sqft
from realestate.schemas import ScoreComponent
from realestate.scoring.explanations import component, fact, keyword_hits
from realestate.sources.manual_csv import detect_price_change


def score_value(listing: Listing, preferences: dict, session: Session | None = None) -> ScoreComponent:
    score = 68.0
    positive: list[str] = []
    negative: list[str] = []
    missing: list[str] = []
    facts = [
        fact("list_price", listing.list_price),
        fact("finished_sqft", listing.finished_sqft),
        fact("annual_taxes", listing.annual_taxes),
    ]
    budget = preferences.get("budget", {})
    target_min = float(budget.get("target_min_price") or 0)
    target_max = float(budget.get("target_max_price") or 750000)
    comfort_max = float(budget.get("comfort_max_price") or target_max)
    hard_max = float(budget.get("hard_max_price") or comfort_max)
    description = listing.description or ""
    project_flags = _project_flags(description, preferences)
    exceptional_flags = _exceptional_flags(description, preferences)

    if listing.list_price is None:
        missing.append("List price missing; value cannot be assessed.")
        score -= 18
    else:
        if target_min and listing.list_price < target_min:
            positive.append("Price is below the configured target range; verify condition and tradeoffs.")
            score += 3
        elif listing.list_price <= target_max:
            positive.append("List price is within the configured target budget.")
            score += 8
        elif listing.list_price <= hard_max and budget.get("stretch_allowed"):
            negative.append(
                "List price is a stretch above the comfort zone; require exceptional quality or likely price leverage."
            )
            score -= 8
            if exceptional_flags:
                positive.append(
                    "Listing has stretch-justifying language such as move-in ready, immaculate, or recent major updates."
                )
                score += 5
        else:
            negative.append("List price exceeds configured budget; require strong justification before touring.")
            score -= 22

        score += _budget_adjusted_project_delta(listing.list_price, project_flags, positive, negative)

    ppsf = price_per_sqft(listing.list_price, listing.finished_sqft)
    if ppsf is None:
        missing.append("Price per finished square foot unavailable.")
    else:
        facts.append(fact("price_per_finished_sqft", ppsf))
        if ppsf < 250:
            positive.append(f"Price per finished square foot is relatively low at ${ppsf:,.0f}.")
            score += 8
        elif ppsf > 425:
            negative.append(f"Price per finished square foot is high at ${ppsf:,.0f}; verify condition and comps.")
            score -= 14
        elif ppsf > 350:
            negative.append(f"Price per finished square foot is elevated at ${ppsf:,.0f}; compare carefully.")
            score -= 7
        else:
            positive.append(f"Price per finished square foot is moderate at ${ppsf:,.0f}.")
            score += 3

    if listing.annual_taxes is None:
        missing.append("Annual taxes missing; affordability and municipal tax burden are unknown.")
    elif listing.list_price:
        tax_ratio = listing.annual_taxes / listing.list_price
        facts.append(fact("tax_to_price_ratio", round(tax_ratio, 4)))
        if tax_ratio > 0.018:
            negative.append("Annual taxes look high relative to price; verify current and proposed taxes.")
            score -= 8
        elif tax_ratio < 0.011:
            positive.append("Annual tax burden looks comparatively manageable; verify public record details.")
            score += 4

    price_change = detect_price_change(listing)
    if price_change["changed"] and price_change["direction"] == "reduction":
        positive.append(
            f"Recent price reduction of ${price_change['amount']:,.0f} may create negotiation leverage."
        )
        score += 5
    elif price_change["changed"] and price_change["direction"] == "increase":
        negative.append("Recent price increase reduces value confidence.")
        score -= 3

    if listing.original_list_price and listing.list_price and listing.list_price < listing.original_list_price:
        positive.append("Current list price is below original list price.")
        score += 3

    if session is not None:
        score += _public_value_delta(session, listing, positive, negative, facts)

    return component(score, positive, negative, missing, facts)


def _public_value_delta(
    session: Session,
    listing: Listing,
    positive: list[str],
    negative: list[str],
    facts: list[dict],
) -> float:
    if listing.list_price is None:
        return 0
    delta = 0.0
    sale = session.execute(
        select(SaleHistoryRecord)
        .where(SaleHistoryRecord.property_id == listing.property_id)
        .order_by(SaleHistoryRecord.id.desc())
    ).scalars().first()
    if sale and sale.sale_price:
        sale_gap = listing.list_price - sale.sale_price
        sale_gap_pct = sale_gap / sale.sale_price
        sale_age_years = _sale_age_years(sale.sale_date)
        facts.append(
            fact(
                "latest_public_sale",
                {
                    "sale_date": sale.sale_date,
                    "sale_price": sale.sale_price,
                    "age_years": round(sale_age_years, 1) if sale_age_years is not None else None,
                },
                sale.source_name or "public_sale_history",
            )
        )
        if sale_age_years is None:
            negative.append(
                "Latest parcel sale has no usable date; use it as ownership history, not a pricing anchor."
            )
        elif sale_age_years > 5:
            negative.append(
                f"Latest parcel sale is {sale_age_years:.1f} years old; ask what updates and comps justify the current price."
            )
        elif sale_gap_pct > 0.12:
            negative.append(
                f"List price is {sale_gap_pct:.0%} above the latest parcel-reported sale; require comps and condition proof."
            )
            delta -= 8
        elif sale_gap_pct < -0.05:
            positive.append(
                "List price is below the latest parcel-reported sale; verify why the market is discounting it."
            )
            delta += 3
        else:
            positive.append("List price is close to the latest parcel-reported sale; still verify comps.")
            delta += 1

    tax = session.execute(
        select(TaxRecord)
        .where(TaxRecord.property_id == listing.property_id)
        .order_by(TaxRecord.id.desc())
    ).scalars().first()
    if tax and tax.assessed_market_value:
        emv_gap = listing.list_price - tax.assessed_market_value
        emv_gap_pct = emv_gap / tax.assessed_market_value
        facts.append(
            fact(
                "public_emv",
                {"assessed_market_value": tax.assessed_market_value, "annual_tax": tax.annual_tax},
                tax.source_name or "public_tax_record",
            )
        )
        if emv_gap_pct > 0.30:
            negative.append(
                f"List price is {emv_gap_pct:.0%} above public EMV; verify updates, condition, and comparable sales."
            )
            delta -= 5
        elif emv_gap_pct < -0.05:
            positive.append("List price is below public EMV; verify condition or market reason.")
            delta += 2
    return delta


def _sale_age_years(sale_date: str | None) -> float | None:
    if not sale_date:
        return None
    try:
        parsed = datetime.fromisoformat(sale_date).date()
    except ValueError:
        return None
    return (datetime.now(UTC).date() - parsed).days / 365.25


def _project_flags(description: str, preferences: dict) -> list[str]:
    project_terms = [
        "as-is",
        "investor special",
        "needs TLC",
        "bring your ideas",
        "very dated",
        "dated",
        "cosmetic flip",
        "flipped",
        "water intrusion",
        "foundation",
        "missing basement photos",
        "missing mechanical photos",
        "older roof",
        "original mechanicals",
    ]
    return sorted(
        set(
            keyword_hits(description, project_terms)
            + keyword_hits(description, preferences.get("negative_features", []))
        )
    )


def _exceptional_flags(description: str, preferences: dict) -> list[str]:
    exceptional_terms = [
        "immaculate",
        "move-in ready",
        "new roof",
        "newer mechanicals",
        "updated mechanicals",
        "updated electrical",
        "updated plumbing",
        "pre-inspected",
        "price reduced",
        "motivated seller",
    ]
    return sorted(
        set(
            keyword_hits(description, exceptional_terms)
            + keyword_hits(description, preferences.get("watch_keywords", []))
        )
    )


def _budget_adjusted_project_delta(
    price: float,
    project_flags: list[str],
    positive: list[str],
    negative: list[str],
) -> float:
    if not project_flags:
        return 0
    shown = ", ".join(project_flags[:4])
    if price >= 700000:
        negative.append(
            f"At $700k-$800k, project/uncertainty signals are heavily penalized: {shown}."
        )
        return -22
    if price >= 600000:
        negative.append(f"At $600k-$700k, only light cosmetic projects fit the profile: {shown}.")
        return -10
    if price >= 400000:
        negative.append(
            f"At $400k-$600k, moderate projects may be acceptable only if pricing supports them: {shown}."
        )
        positive.append("Lower-budget project tolerance is higher if the home is priced appropriately.")
        return -3
    negative.append(f"Project/uncertainty signals need verification: {shown}.")
    return -5
