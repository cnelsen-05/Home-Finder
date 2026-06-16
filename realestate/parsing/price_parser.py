from __future__ import annotations

import re
from typing import Any


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower()
    multiplier = 1.0
    if "acre" in lowered:
        multiplier = 43560.0
    cleaned = re.sub(r"[^0-9.\-]", "", text)
    if not cleaned or cleaned in {".", "-", "-."}:
        return None
    try:
        return float(cleaned) * multiplier
    except ValueError:
        return None


def parse_int(value: Any) -> int | None:
    parsed = parse_float(value)
    if parsed is None:
        return None
    return int(round(parsed))


def parse_price(value: Any) -> float | None:
    return parse_float(value)


def price_per_sqft(price: float | None, sqft: float | None) -> float | None:
    if not price or not sqft or sqft <= 0:
        return None
    return round(price / sqft, 2)
