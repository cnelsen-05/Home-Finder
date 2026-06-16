from __future__ import annotations

from typing import Any

ALIASES = {
    "source": ["source", "site", "origin"],
    "url": ["url", "listing_url", "link"],
    "address": ["address", "street", "address_line1"],
    "city": ["city", "municipality"],
    "state": ["state"],
    "zip": ["zip", "zipcode", "postal_code"],
    "price": ["price", "list_price", "asking_price"],
    "beds": ["beds", "bedrooms"],
    "baths": ["baths", "bathrooms"],
    "finished_sqft": ["finished_sqft", "sqft", "living_area", "finished_square_feet"],
    "lot_size": ["lot_size", "lot_size_sqft", "lot_sqft", "acres"],
    "year_built": ["year_built", "built"],
    "property_type": ["property_type", "type"],
    "status": ["status"],
    "description": ["description", "remarks", "public_remarks"],
    "user_rating": ["user_rating", "rating"],
    "user_notes": ["user_notes", "notes"],
    "mls_number": ["mls_number", "mls", "mls_id"],
    "garage_spaces": ["garage_spaces", "garage", "garage_stalls"],
    "annual_taxes": ["annual_taxes", "taxes", "property_taxes"],
    "hoa_fee": ["hoa_fee", "hoa"],
    "school_district": ["school_district", "district"],
}


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    lower = {str(key).strip().lower(): value for key, value in row.items()}
    normalized: dict[str, Any] = {}
    for canonical, names in ALIASES.items():
        for name in names:
            if name in lower:
                normalized[canonical] = lower[name]
                break
    for key, value in lower.items():
        normalized.setdefault(key, value)
    return normalized
