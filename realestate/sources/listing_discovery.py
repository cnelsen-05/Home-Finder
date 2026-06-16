from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

REFERENCE_ONLY_DOMAINS = {
    "zillow.com",
    "www.zillow.com",
    "realtor.com",
    "www.realtor.com",
    "homes.com",
    "www.homes.com",
    "redfin.com",
    "www.redfin.com",
}


@dataclass(frozen=True)
class ListingSourceDecision:
    url: str
    domain: str
    status: str
    reason: str


def classify_listing_url(url: str) -> ListingSourceDecision:
    domain = (urlparse(url).netloc or "").lower()
    domain = domain.removeprefix("www.")
    if domain in {item.removeprefix("www.") for item in REFERENCE_ONLY_DOMAINS}:
        return ListingSourceDecision(
            url=url,
            domain=domain,
            status="reference_only",
            reason=(
                "Major listing portal detected. Store the URL and use snippets/manual user-provided text; "
                "do not scrape page content unless terms or API rights permit it."
            ),
        )
    if not domain:
        return ListingSourceDecision(
            url=url,
            domain="",
            status="invalid",
            reason="URL has no domain.",
        )
    return ListingSourceDecision(
        url=url,
        domain=domain,
        status="candidate_for_review",
        reason=(
            "Non-blocklisted listing/source URL. Check robots/terms and prefer official broker, city, county, "
            "or API sources before automated extraction."
        ),
    )


def address_search_queries(address: str, city: str | None = None, state: str | None = "MN") -> list[str]:
    location = " ".join(piece for piece in [address, city, state] if piece)
    quoted = f'"{location.strip()}"'
    return [
        quoted,
        f"{quoted} real estate listing",
        f"{quoted} property tax parcel permit",
    ]
