from __future__ import annotations

import re
from dataclasses import dataclass

from realestate.parsing.price_parser import parse_float, parse_int, parse_price


@dataclass(frozen=True)
class ParsedListingText:
    address: str | None
    city: str | None
    state: str | None
    zip: str | None
    price: float | None
    beds: float | None
    baths: float | None
    finished_sqft: float | None
    lot_size_sqft: float | None
    year_built: int | None
    garage_spaces: float | None
    description: str


CITY_STATE_ZIP = re.compile(
    r"(?P<city>[A-Za-z .'-]+),\s*(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)"
)


def parse_listing_text(text: str) -> ParsedListingText:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    address = _first_address_line(lines)
    city, state, zip_code = _city_state_zip(lines)
    price = _first_money_value(text)
    beds = _first_number_before(text, "bed")
    baths = _first_number_before(text, "bath")
    finished_sqft = _first_sqft(text, lot=False)
    lot_size_sqft = _first_sqft(text, lot=True)
    year_built = _year_built(text)
    garage_spaces = _garage_spaces(text)
    return ParsedListingText(
        address=address,
        city=city,
        state=state,
        zip=zip_code,
        price=price,
        beds=beds,
        baths=baths,
        finished_sqft=finished_sqft,
        lot_size_sqft=lot_size_sqft,
        year_built=year_built,
        garage_spaces=garage_spaces,
        description=text.strip(),
    )


def _first_address_line(lines: list[str]) -> str | None:
    for line in lines[:5]:
        if re.match(r"^\d+\s+[A-Za-z0-9 .'-]+", line) and "$" not in line:
            return line
    return lines[0] if lines else None


def _city_state_zip(lines: list[str]) -> tuple[str | None, str | None, str | None]:
    for line in lines[:8]:
        match = CITY_STATE_ZIP.search(line)
        if match:
            return match.group("city").strip(), match.group("state"), match.group("zip")
    return None, "MN", None


def _first_money_value(text: str) -> float | None:
    match = re.search(r"\$\s*[\d,]+(?:\.\d+)?", text)
    if not match:
        return None
    return parse_price(match.group(0))


def _first_number_before(text: str, word: str) -> float | None:
    match = re.search(rf"(\d+(?:\.\d+)?)\s*{word}", text, flags=re.IGNORECASE)
    return parse_float(match.group(1)) if match else None


def _first_sqft(text: str, lot: bool) -> float | None:
    if lot:
        patterns = [
            r"([\d,]+(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|square feet)\s+lot",
            r"lot(?:\s+size)?[:\s]+([\d,]+(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|square feet)",
            r"([\d,]+(?:\.\d+)?)\s*acre",
        ]
    else:
        patterns = [
            r"([\d,]+(?:\.\d+)?)\s*(?:finished\s+)?(?:sq\.?\s*ft|sqft|square feet)",
        ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            token = match.group(0) if "acre" in match.group(0).lower() else match.group(1)
            return parse_float(token)
    return None


def _year_built(text: str) -> int | None:
    match = re.search(r"(?:built in|year built[:\s]+)(18\d{2}|19\d{2}|20\d{2})", text, re.I)
    if match:
        return parse_int(match.group(1))
    return None


def _garage_spaces(text: str) -> float | None:
    normalized = text.lower()
    if "two-car garage" in normalized or "2 car garage" in normalized:
        return 2.0
    if "three-car garage" in normalized or "3 car garage" in normalized:
        return 3.0
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:car|stall)\s+garage", normalized)
    return parse_float(match.group(1)) if match else None
