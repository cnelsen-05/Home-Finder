from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from realestate.models import Favorite, LifeAnchor, Listing, ListingSnapshot, Property, utcnow
from realestate.parsing.address_parser import normalize_address, simple_county_hint
from realestate.parsing.csv_mapper import normalize_row
from realestate.parsing.price_parser import parse_float, parse_int, parse_price


def import_favorites_csv(path: Path, session: Session) -> list[Favorite]:
    rows = _read_csv(path)
    favorites = []
    for row in rows:
        favorite = upsert_favorite_from_row(normalize_row(row), session)
        favorites.append(favorite)
    session.flush()
    return favorites


def import_listings_csv(path: Path, session: Session) -> list[Listing]:
    rows = _read_csv(path)
    listings = []
    for row in rows:
        listing = upsert_listing_from_row(normalize_row(row), session)
        listings.append(listing)
    session.flush()
    return listings


def import_life_anchors_file(
    path: Path, session: Session, replace: bool = False
) -> list[LifeAnchor]:
    if replace:
        session.execute(delete(LifeAnchor))
        session.flush()
    if path.suffix.lower() in {".yaml", ".yml"}:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rows = data.get("anchors", data if isinstance(data, list) else [])
    else:
        rows = _read_csv(path)
    anchors = [upsert_life_anchor(dict(row), session) for row in rows]
    session.flush()
    return anchors


def upsert_favorite_from_row(row: dict[str, Any], session: Session) -> Favorite:
    listing = upsert_listing_from_row(row, session)
    external_url = _clean(row.get("url") or row.get("listing_url"))
    favorite = _find_favorite(session, listing.id, external_url)
    if favorite is None:
        favorite = Favorite(listing=listing, external_url=external_url)
        session.add(favorite)
    if (user_rating := _clean(row.get("user_rating"))) is not None:
        favorite.user_rating = user_rating
    if (user_notes := _clean(row.get("user_notes"))) is not None:
        favorite.user_notes = user_notes
    favorite.imported_source = _clean(row.get("source")) or "manual"
    favorite.imported_at = utcnow()
    return favorite


def upsert_listing_from_row(row: dict[str, Any], session: Session) -> Listing:
    prop = upsert_property_from_row(row, session)
    session.flush()
    listing = _find_listing(session, row, prop)
    if listing is None:
        listing = Listing(property=prop, source=_clean(row.get("source")) or "manual")
        session.add(listing)
        session.flush()
        listing.first_seen_at = utcnow()
    listing.source = _clean(row.get("source")) or listing.source or "manual"
    if (source_listing_id := _clean(row.get("source_listing_id"))) is not None:
        listing.source_listing_id = source_listing_id
    if (mls_number := _clean(row.get("mls_number"))) is not None:
        listing.mls_number = mls_number
    if (listing_url := _clean(row.get("url") or row.get("listing_url"))) is not None:
        listing.listing_url = listing_url
    if (status := _clean(row.get("status"))) is not None:
        listing.status = status
    parsed_price = parse_price(row.get("price") or row.get("list_price"))
    if listing.original_list_price is None and parsed_price is not None:
        listing.original_list_price = parsed_price
    if parsed_price is not None:
        listing.list_price = parsed_price
    if (beds := parse_float(row.get("beds"))) is not None:
        listing.beds = beds
    if (baths := parse_float(row.get("baths"))) is not None:
        listing.baths = baths
    if (finished_sqft := parse_float(row.get("finished_sqft"))) is not None:
        listing.finished_sqft = finished_sqft
    if (lot_size_sqft := parse_float(row.get("lot_size") or row.get("lot_size_sqft"))) is not None:
        listing.lot_size_sqft = lot_size_sqft
    if (year_built := parse_int(row.get("year_built"))) is not None:
        listing.year_built = year_built
    if (property_type := _clean(row.get("property_type"))) is not None:
        listing.property_type = property_type
    if (style := _clean(row.get("style"))) is not None:
        listing.style = style
    if (garage_spaces := parse_float(row.get("garage_spaces"))) is not None:
        listing.garage_spaces = garage_spaces
    if (school_district := _clean(row.get("school_district"))) is not None:
        listing.school_district = school_district
    if (annual_taxes := parse_price(row.get("annual_taxes"))) is not None:
        listing.annual_taxes = annual_taxes
    if (hoa_fee := parse_price(row.get("hoa_fee"))) is not None:
        listing.hoa_fee = hoa_fee
    if (description := _clean(row.get("description"))) is not None:
        listing.description = description
    listing.raw_payload_json = json.dumps(row, sort_keys=True)
    listing.last_seen_at = utcnow()
    session.flush()
    create_snapshot_if_changed(listing, row, session)
    return listing


def upsert_property_from_row(row: dict[str, Any], session: Session) -> Property:
    address = _clean(row.get("address") or row.get("address_line1"))
    city = _clean(row.get("city"))
    state = _clean(row.get("state")) or "MN"
    zip_code = _clean(row.get("zip"))
    normalized = normalize_address(address)
    existing = session.execute(
        select(Property).where(
            Property.normalized_address == normalized,
            Property.city == city,
            Property.state == state,
            Property.zip == zip_code,
        )
    ).scalar_one_or_none()
    if existing:
        prop = existing
    else:
        prop = Property(
            normalized_address=normalized,
            address_line1=address,
            city=city,
            state=state,
            zip=zip_code,
            county=simple_county_hint(city),
        )
        session.add(prop)
    prop.address_line1 = address or prop.address_line1
    prop.city = city or prop.city
    prop.state = state or prop.state or "MN"
    prop.zip = zip_code or prop.zip
    prop.normalized_address = normalized or prop.normalized_address
    prop.county = _clean(row.get("county")) or prop.county or simple_county_hint(city)
    prop.latitude = parse_float(row.get("latitude")) or prop.latitude
    prop.longitude = parse_float(row.get("longitude")) or prop.longitude
    prop.parcel_id = _clean(row.get("parcel_id")) or prop.parcel_id
    return prop


def upsert_life_anchor(row: dict[str, Any], session: Session) -> LifeAnchor:
    name = _clean(row.get("name"))
    category = (_clean(row.get("category")) or "other").lower()
    address = _format_anchor_address(row)
    existing = session.execute(
        select(LifeAnchor).where(LifeAnchor.name == name, LifeAnchor.category == category)
    ).scalar_one_or_none()
    if existing:
        anchor = existing
    else:
        anchor = LifeAnchor(name=name or "Unnamed Anchor", category=category)
        session.add(anchor)
    anchor.address = address
    anchor.latitude = parse_float(row.get("latitude"))
    anchor.longitude = parse_float(row.get("longitude"))
    anchor.priority = parse_int(row.get("priority")) or 1
    anchor.notes = _clean(row.get("notes"))
    return anchor


def create_snapshot_if_changed(
    listing: Listing, raw_payload: dict[str, Any], session: Session
) -> ListingSnapshot | None:
    description_hash = _hash_text(listing.description)
    photo_hash = _hash_text(listing.photo_urls_json)
    latest = listing.snapshots[-1] if listing.snapshots else None
    changed = latest is None or any(
        [
            latest.price != listing.list_price,
            latest.status != listing.status,
            latest.description_hash != description_hash,
            latest.photo_hash != photo_hash,
        ]
    )
    if not changed:
        return None
    snapshot = ListingSnapshot(
        listing=listing,
        price=listing.list_price,
        status=listing.status,
        description_hash=description_hash,
        photo_hash=photo_hash,
        raw_payload_json=json.dumps(raw_payload, sort_keys=True),
    )
    session.add(snapshot)
    return snapshot


def detect_price_change(listing: Listing) -> dict[str, Any]:
    snapshots = sorted(listing.snapshots, key=lambda snap: _datetime_sort_key(snap.observed_at))
    priced = [snap for snap in snapshots if snap.price is not None]
    if len(priced) < 2:
        return {"changed": False, "direction": None, "amount": 0, "from": None, "to": None}
    previous, current = priced[-2], priced[-1]
    amount = (current.price or 0) - (previous.price or 0)
    direction = "reduction" if amount < 0 else "increase" if amount > 0 else "unchanged"
    return {
        "changed": amount != 0,
        "direction": direction,
        "amount": abs(amount),
        "from": previous.price,
        "to": current.price,
    }


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _datetime_sort_key(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _find_listing(session: Session, row: dict[str, Any], prop: Property) -> Listing | None:
    source = _clean(row.get("source")) or "manual"
    source_listing_id = _clean(row.get("source_listing_id"))
    listing_url = _clean(row.get("url") or row.get("listing_url"))
    mls_number = _clean(row.get("mls_number"))
    if source_listing_id:
        found = session.execute(
            select(Listing).where(
                Listing.source == source, Listing.source_listing_id == source_listing_id
            )
        ).scalar_one_or_none()
        if found:
            return found
    if mls_number:
        found = session.execute(select(Listing).where(Listing.mls_number == mls_number)).scalar_one_or_none()
        if found:
            return found
    if listing_url:
        found = session.execute(select(Listing).where(Listing.listing_url == listing_url)).scalar_one_or_none()
        if found:
            return found
    return session.execute(
        select(Listing).where(Listing.property_id == prop.id, Listing.source == source)
    ).scalar_one_or_none()


def _find_favorite(session: Session, listing_id: int | None, external_url: str | None) -> Favorite | None:
    if listing_id:
        found = session.execute(select(Favorite).where(Favorite.listing_id == listing_id)).scalar_one_or_none()
        if found:
            return found
    if external_url:
        return session.execute(select(Favorite).where(Favorite.external_url == external_url)).scalar_one_or_none()
    return None


def _hash_text(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _format_anchor_address(row: dict[str, Any]) -> str | None:
    address = _clean(row.get("address"))
    city = _clean(row.get("city"))
    state = _clean(row.get("state"))
    zip_code = _clean(row.get("zip"))
    if address and (city or state or zip_code):
        return ", ".join(piece for piece in [address, city, state, zip_code] if piece)
    return address
