from __future__ import annotations

import re

SUFFIXES = {
    "AVENUE": "AVE",
    "AV": "AVE",
    "STREET": "ST",
    "ROAD": "RD",
    "BOULEVARD": "BLVD",
    "DRIVE": "DR",
    "LANE": "LN",
    "COURT": "CT",
    "CIRCLE": "CIR",
    "PLACE": "PL",
    "TERRACE": "TER",
    "PARKWAY": "PKWY",
    "HIGHWAY": "HWY",
}

DIRECTIONS = {
    "NORTH": "N",
    "SOUTH": "S",
    "EAST": "E",
    "WEST": "W",
}


def normalize_address(address: str | None) -> str:
    if not address:
        return ""
    text = address.upper()
    text = re.sub(r"#\s*\w+", "", text)
    text = re.sub(r"\b(APT|UNIT|STE|SUITE)\s+\w+\b", "", text)
    text = re.sub(r"[^A-Z0-9\s]", " ", text)
    parts = [part for part in text.split() if part]
    normalized = []
    for part in parts:
        part = DIRECTIONS.get(part, part)
        part = SUFFIXES.get(part, part)
        normalized.append(part)
    return " ".join(normalized)


def join_address(address: str | None, city: str | None, state: str | None, zip_code: str | None) -> str:
    pieces = [address, city, state, zip_code]
    return ", ".join(str(piece).strip() for piece in pieces if piece)


def simple_county_hint(city: str | None) -> str | None:
    if not city:
        return None
    hennepin = {
        "MINNEAPOLIS",
        "EDINA",
        "ST. LOUIS PARK",
        "ST LOUIS PARK",
        "HOPKINS",
        "MINNETONKA",
        "GOLDEN VALLEY",
        "RICHFIELD",
        "BLOOMINGTON",
        "EDEN PRAIRIE",
        "PLYMOUTH",
        "WAYZATA",
        "EXCELSIOR",
        "MAPLE GROVE",
    }
    ramsey = {"ST. PAUL", "ST PAUL", "ROSEVILLE", "MAPLEWOOD", "SHOREVIEW"}
    normalized = city.strip().upper()
    if normalized in hennepin:
        return "Hennepin"
    if normalized in ramsey:
        return "Ramsey"
    return None
