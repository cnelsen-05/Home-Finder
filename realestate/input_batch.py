from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from sqlalchemy.orm import Session

from realestate.config import load_preferences
from realestate.enrichment.public_record_pipeline import enrich_property
from realestate.models import Favorite, PublicRecord
from realestate.paths import IMPORTS_DIR
from realestate.reports.render import (
    render_favorite_review,
    render_pilot_report,
    render_pilot_report_html,
)
from realestate.scoring.overall import score_listing
from realestate.sources.listing_discovery import classify_listing_url
from realestate.sources.manual_csv import import_favorites_csv

CSV_FIELDS = [
    "source",
    "url",
    "address",
    "city",
    "state",
    "zip",
    "price",
    "beds",
    "baths",
    "finished_sqft",
    "lot_size",
    "year_built",
    "property_type",
    "status",
    "description",
    "user_rating",
    "user_notes",
]

URL_RE = re.compile(r"https?://[^\s]+", re.I)
STATE_ZIP_RE = re.compile(r"\b(?P<state>[A-Z]{2})\b[\s,]*(?P<zip>\d{5}(?:-\d{4})?)?\b", re.I)
CITY_STATE_ZIP_RE = re.compile(
    r"^(?P<street>\d+\s+.+?)\s+(?P<city>[A-Za-z][A-Za-z .'-]+?)\s+"
    r"(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$",
    re.I,
)

STREET_SUFFIXES = {
    "ave",
    "avenue",
    "blvd",
    "boulevard",
    "cir",
    "circle",
    "ct",
    "court",
    "dr",
    "drive",
    "hwy",
    "lane",
    "ln",
    "path",
    "pkwy",
    "parkway",
    "pl",
    "place",
    "rd",
    "road",
    "st",
    "street",
    "ter",
    "terrace",
    "trail",
    "trl",
    "way",
}

DIRECTIONS = {"n", "s", "e", "w", "ne", "nw", "se", "sw"}


@dataclass(frozen=True)
class ParsedInputRow:
    source_line: str
    row: dict[str, str]


@dataclass(frozen=True)
class InputParseError:
    source_line: str
    message: str


@dataclass(frozen=True)
class ResearchRunError:
    source_line: str
    address: str
    message: str


@dataclass(frozen=True)
class ParsedInputBatch:
    rows: list[ParsedInputRow] = field(default_factory=list)
    errors: list[InputParseError] = field(default_factory=list)


@dataclass(frozen=True)
class ResearchBatchResult:
    imported_count: int
    listing_ids: list[int]
    import_path: Path
    pilot_markdown_path: Path | None
    pilot_html_path: Path | None
    favorite_report_paths: list[Path]
    parse_errors: list[InputParseError]
    run_errors: list[ResearchRunError] = field(default_factory=list)


def parse_pasted_home_inputs(text: str) -> ParsedInputBatch:
    rows: list[ParsedInputRow] = []
    errors: list[InputParseError] = []
    for raw_line in _input_lines(text):
        parsed = parse_home_input_line(raw_line)
        if isinstance(parsed, InputParseError):
            errors.append(parsed)
        else:
            rows.append(parsed)
    return ParsedInputBatch(rows=rows, errors=errors)


def parse_home_input_line(line: str) -> ParsedInputRow | InputParseError:
    original = line.strip()
    urls = _extract_urls(original)
    address_text = URL_RE.sub("", _markdown_links_to_text(original)).strip(" ,;\t")
    parsed_address = _parse_address_text(address_text) if address_text else None
    url = None
    if parsed_address is None and urls:
        url, parsed_address = _parse_address_from_urls(urls)
    elif parsed_address is not None:
        url = _select_reference_url(urls, parsed_address)
    if parsed_address is None:
        return InputParseError(
            source_line=original,
            message=(
                "Could not parse a full street/city/state/ZIP address. "
                "Paste the address next to the link, or use 'Street, City, ST ZIP'."
            ),
        )
    row = {
        field: ""
        for field in CSV_FIELDS
    }
    row.update(
        {
            "source": "pasted_listing_link" if url else "manual_address_list",
            "url": url or "",
            "address": parsed_address["address"],
            "city": parsed_address["city"],
            "state": parsed_address["state"],
            "zip": parsed_address["zip"],
        }
    )
    return ParsedInputRow(source_line=original, row=row)


def write_pasted_input_csv(batch: ParsedInputBatch, label: str | None = None) -> Path:
    target_dir = IMPORTS_DIR / "gui_requests"
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    slug = _slug(label or "pasted_homes")
    path = target_dir / f"{timestamp}_{slug}.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for parsed in batch.rows:
            writer.writerow(parsed.row)
    return path


def run_research_batch_from_text(
    session: Session,
    text: str,
    *,
    label: str | None = None,
    pilot_limit: int | None = None,
    preferences: dict[str, Any] | None = None,
) -> ResearchBatchResult:
    batch = parse_pasted_home_inputs(text)
    if not batch.rows:
        raise ValueError("No valid addresses or address-bearing links were found.")
    import_path = write_pasted_input_csv(batch, label=label)
    favorites = import_favorites_csv(import_path, session)
    session.flush()
    for favorite, parsed_row in zip(favorites, batch.rows, strict=False):
        _store_pasted_listing_link(session, favorite, parsed_row.row)
    selected_favorites = favorites[: pilot_limit or len(favorites)]
    prefs = preferences or load_preferences()
    run_errors: list[ResearchRunError] = []
    successful_listing_ids: list[int] = []
    for favorite, parsed_row in zip(selected_favorites, batch.rows, strict=False):
        if favorite.listing_id is None:
            continue
        try:
            _enrich_and_score_favorite(session, favorite, prefs)
        except Exception as exc:
            run_errors.append(
                ResearchRunError(
                    source_line=parsed_row.source_line,
                    address=_row_address(parsed_row.row),
                    message=str(exc),
                )
            )
            continue
        successful_listing_ids.append(favorite.listing_id)
    pilot_markdown_path = (
        render_pilot_report(session, successful_listing_ids, prefs)
        if successful_listing_ids
        else None
    )
    pilot_html_path = (
        render_pilot_report_html(session, successful_listing_ids, prefs)
        if successful_listing_ids
        else None
    )
    favorite_report_paths = [
        render_favorite_review(session, listing_id, prefs)
        for listing_id in successful_listing_ids
    ]
    return ResearchBatchResult(
        imported_count=len(favorites),
        listing_ids=successful_listing_ids,
        import_path=import_path,
        pilot_markdown_path=pilot_markdown_path,
        pilot_html_path=pilot_html_path,
        favorite_report_paths=favorite_report_paths,
        parse_errors=batch.errors,
        run_errors=run_errors,
    )


def _enrich_and_score_favorite(session: Session, favorite: Favorite, preferences: dict[str, Any]) -> None:
    if favorite.listing is None:
        return
    enrich_property(session, favorite.listing.property)
    score_listing(session, favorite.listing, preferences)


def _store_pasted_listing_link(
    session: Session, favorite: Favorite, row: dict[str, str]
) -> None:
    url = row.get("url")
    if not url or favorite.listing is None:
        return
    decision = classify_listing_url(url)
    confidence = "low" if decision.status == "reference_only" else "medium"
    record = PublicRecord(
        property=favorite.listing.property,
        source_name="Pasted Listing Link",
        source_url=url,
        record_type="listing_discovery",
        parsed_json=json.dumps(
            {
                "domain": decision.domain,
                "status": decision.status,
                "automated_extraction": False,
            },
            sort_keys=True,
        ),
        confidence=confidence,
        notes=decision.reason,
    )
    session.add(record)


def _input_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.replace("\r\n", "\n").split("\n")
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _extract_urls(line: str) -> list[str]:
    return [match.group(0).rstrip(").,;]") for match in URL_RE.finditer(line)]


def _markdown_links_to_text(line: str) -> str:
    return re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r" \1 ", line)


def _parse_address_from_urls(urls: list[str]) -> tuple[str | None, dict[str, str] | None]:
    for url in urls:
        parsed = _parse_address_from_url(url)
        if parsed is not None:
            return url, parsed
    return None, None


def _select_reference_url(urls: list[str], parsed_address: dict[str, str]) -> str | None:
    if not urls:
        return None
    for url in urls:
        if _url_matches_address(url, parsed_address):
            return url
    if len(urls) == 1:
        return urls[0]
    return None


def _parse_address_text(text: str) -> dict[str, str] | None:
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return None
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) >= 3:
        street = parts[0]
        city = parts[1]
        state_zip = " ".join(parts[2:])
        state_match = STATE_ZIP_RE.search(state_zip)
        if state_match and state_match.group("zip"):
            return _address_dict(street, city, state_match.group("state"), state_match.group("zip"))
    if len(parts) == 2:
        state_match = STATE_ZIP_RE.search(parts[1])
        if state_match and state_match.group("zip"):
            city = parts[1][: state_match.start()].strip(" ,")
            if city:
                return _address_dict(parts[0], city, state_match.group("state"), state_match.group("zip"))
    suffix_parsed = _parse_space_separated_address(text)
    if suffix_parsed:
        return suffix_parsed
    match = CITY_STATE_ZIP_RE.match(text)
    if match:
        return _address_dict(
            match.group("street"),
            match.group("city"),
            match.group("state"),
            match.group("zip"),
        )
    return None


def _parse_space_separated_address(text: str) -> dict[str, str] | None:
    tokens = re.findall(r"[A-Za-z0-9'-]+", text)
    normalized = [token.lower() for token in tokens]
    state_index = _find_state_index(normalized)
    if state_index is None or state_index + 1 >= len(tokens):
        return None
    zip_code = _first_zip(normalized[state_index + 1 :])
    if zip_code is None or not normalized[0].isdigit():
        return None
    street_end = _find_street_end(normalized, 0, state_index)
    if street_end is None or street_end >= state_index:
        return None
    city_tokens = tokens[street_end + 1 : state_index]
    if not city_tokens:
        return None
    return _address_dict(
        " ".join(tokens[: street_end + 1]),
        " ".join(city_tokens),
        tokens[state_index],
        zip_code,
    )


def _parse_address_from_url(url: str) -> dict[str, str] | None:
    parsed = urlparse(url)
    text = unquote(" ".join([parsed.netloc, parsed.path]))
    text = re.sub(r"[_/+,.-]+", " ", text)
    text = re.sub(r"\s+", " ", text).lower()
    tokens = [token for token in text.split() if token]
    state_index = _find_state_index(tokens)
    if state_index is None or state_index + 1 >= len(tokens):
        return None
    zip_code = _first_zip(tokens[state_index + 1 :])
    if zip_code is None:
        return None
    street_start = next((idx for idx, token in enumerate(tokens[:state_index]) if token.isdigit()), None)
    if street_start is None:
        return None
    street_end = _find_street_end(tokens, street_start, state_index)
    if street_end is None or street_end >= state_index:
        return None
    street_tokens = tokens[street_start : street_end + 1]
    city_tokens = tokens[street_end + 1 : state_index]
    if not city_tokens:
        return None
    return _address_dict(
        _title_address_tokens(street_tokens),
        _title_address_tokens(city_tokens),
        tokens[state_index].upper(),
        zip_code,
    )


def _url_matches_address(url: str, parsed_address: dict[str, str]) -> bool:
    parsed = urlparse(url)
    haystack = unquote(" ".join([parsed.netloc, parsed.path])).lower()
    haystack_tokens = set(re.findall(r"[a-z0-9]+", haystack))
    street_tokens = [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9]+", parsed_address["address"])
        if token.lower() not in STREET_SUFFIXES and token.lower() not in DIRECTIONS
    ]
    if not street_tokens:
        return False
    number = street_tokens[0]
    name_tokens = street_tokens[1:]
    return number in haystack_tokens and any(token in haystack_tokens for token in name_tokens)


def _row_address(row: dict[str, str]) -> str:
    return ", ".join(
        piece
        for piece in [row.get("address"), row.get("city"), row.get("state"), row.get("zip")]
        if piece
    )


def _find_state_index(tokens: list[str]) -> int | None:
    for idx, token in enumerate(tokens):
        if token == "mn" or token == "minnesota":
            return idx
    return None


def _first_zip(tokens: list[str]) -> str | None:
    for token in tokens:
        match = re.match(r"^(\d{5})(?:\D|$)", token)
        if match:
            return match.group(1)
    return None


def _find_street_end(tokens: list[str], start: int, stop: int) -> int | None:
    for idx in range(start + 1, stop):
        token = tokens[idx]
        if token in STREET_SUFFIXES:
            next_idx = idx + 1
            if next_idx < stop and tokens[next_idx] in DIRECTIONS:
                return next_idx
            return idx
    return None


def _address_dict(street: str, city: str, state: str, zip_code: str) -> dict[str, str]:
    return {
        "address": _clean_component(street),
        "city": _clean_component(city),
        "state": state.upper(),
        "zip": zip_code[:5],
    }


def _clean_component(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip(" ,")).strip()


def _title_address_tokens(tokens: list[str]) -> str:
    return " ".join(token.upper() if token in DIRECTIONS else token.capitalize() for token in tokens)


def _slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", value.strip().lower()).strip("_")
    return text[:60] or "pasted_homes"
