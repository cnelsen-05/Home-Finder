from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from realestate.enrichment.geocoding import enrich_geocode_context
from realestate.geospatial import geometry_to_geojson, json_dumps
from realestate.map_data import (
    favorite_home_feature,
    favorite_homes_geojson,
    map_notes_geojson,
    map_payload,
)
from realestate.map_highlights import (
    create_map_highlight,
    delete_map_highlight,
    map_highlight_feature,
    map_highlights_geojson,
    update_map_highlight,
)
from realestate.map_layers import parks_trails_playgrounds_geojson
from realestate.models import (
    Favorite,
    Listing,
    LLMExtraction,
    MapNote,
    PropertyNeighborhoodMatch,
    Report,
    SavedNeighborhood,
)
from realestate.neighborhood_scoring import score_saved_neighborhood
from realestate.neighborhoods import (
    create_saved_neighborhood,
    delete_saved_neighborhood,
    saved_neighborhood_feature,
    saved_neighborhoods_geojson,
    update_saved_neighborhood,
)
from realestate.school_zones import identify_elementary_zone, school_zones_geojson
from realestate.schools import enrich_school_zone_payload, school_locations_geojson
from realestate.sources.manual_csv import upsert_favorite_from_row


@dataclass(frozen=True)
class ApiResponse:
    status: int
    payload: dict[str, Any] | list[Any]


def handle_api_request(
    session: Session,
    method: str,
    raw_path: str,
    body: dict[str, Any] | None = None,
) -> ApiResponse:
    parsed = urlparse(raw_path)
    path = parsed.path.rstrip("/") or "/"
    body = body or {}

    try:
        if method == "GET" and path == "/api/map-data":
            return ApiResponse(200, map_payload(session))
        if method == "GET" and path == "/api/homes":
            return ApiResponse(200, favorite_homes_geojson(session))
        if method == "POST" and path == "/api/homes":
            favorite = _create_favorite_home(session, body)
            feature = favorite_home_feature(session, favorite)
            if feature is None:
                return ApiResponse(500, {"error": "Favorite was saved but could not be rendered."})
            return ApiResponse(201, feature)
        if method == "DELETE" and path.startswith("/api/homes/"):
            listing_id = _path_id(path, "/api/homes/")
            deleted = _delete_home(session, listing_id)
            return ApiResponse(200 if deleted else 404, {"deleted": deleted, "listing_id": listing_id})
        if method == "GET" and path == "/api/neighborhoods":
            return ApiResponse(200, saved_neighborhoods_geojson(session))
        if method == "POST" and path == "/api/neighborhoods":
            neighborhood = create_saved_neighborhood(
                session,
                name=str(body.get("name") or "Untitled saved area"),
                geometry=body.get("geometry") or body.get("geometry_geojson"),
                rating=str(body.get("rating") or "maybe"),
                notes=body.get("notes"),
                tags=body.get("tags") or [],
                city=body.get("city"),
            )
            return ApiResponse(201, saved_neighborhood_feature(neighborhood))
        if method in {"PUT", "PATCH"} and path.startswith("/api/neighborhoods/"):
            neighborhood_id = _path_id(path, "/api/neighborhoods/")
            neighborhood = update_saved_neighborhood(session, neighborhood_id, body)
            return ApiResponse(200, saved_neighborhood_feature(neighborhood))
        if method == "DELETE" and path.startswith("/api/neighborhoods/"):
            neighborhood_id = _path_id(path, "/api/neighborhoods/")
            deleted = delete_saved_neighborhood(session, neighborhood_id)
            return ApiResponse(200 if deleted else 404, {"deleted": deleted})
        if method == "GET" and path == "/api/map-highlights":
            return ApiResponse(200, map_highlights_geojson(session))
        if method == "POST" and path == "/api/map-highlights":
            highlight = create_map_highlight(
                session,
                name=str(body.get("name") or "Map highlight"),
                geometry=body.get("geometry") or body.get("geometry_geojson"),
                highlight_type=str(body.get("highlight_type") or "tour_note"),
                sentiment=body.get("sentiment"),
                notes=body.get("notes"),
                tags=body.get("tags") or [],
                style=body.get("style") or {},
                related_property_id=body.get("related_property_id"),
                related_neighborhood_id=body.get("related_neighborhood_id"),
            )
            return ApiResponse(201, map_highlight_feature(highlight))
        if method in {"PUT", "PATCH"} and path.startswith("/api/map-highlights/"):
            highlight_id = _path_id(path, "/api/map-highlights/")
            highlight = update_map_highlight(session, highlight_id, body)
            return ApiResponse(200, map_highlight_feature(highlight))
        if method == "DELETE" and path.startswith("/api/map-highlights/"):
            highlight_id = _path_id(path, "/api/map-highlights/")
            deleted = delete_map_highlight(session, highlight_id)
            return ApiResponse(200 if deleted else 404, {"deleted": deleted})
        if method == "GET" and path == "/api/school-zones":
            return ApiResponse(200, school_zones_geojson(session))
        if method == "GET" and path == "/api/school-locations":
            return ApiResponse(200, school_locations_geojson(session))
        if method == "GET" and path == "/api/parks-trails-playgrounds":
            return ApiResponse(200, parks_trails_playgrounds_geojson(session))
        if method == "POST" and path == "/api/school-zones/identify":
            lat = float(body["lat"])
            lon = float(body["lon"])
            threshold = float(body.get("boundary_threshold_miles", 0.10))
            result = identify_elementary_zone(
                session,
                lat=lat,
                lon=lon,
                boundary_threshold_miles=threshold,
            ).as_dict()
            return ApiResponse(200, enrich_school_zone_payload(session, result))
        if method == "GET" and path == "/api/map-notes":
            return ApiResponse(200, map_notes_geojson(session))
        if method == "POST" and path == "/api/map-notes":
            note = _create_map_note(session, body)
            return ApiResponse(
                201,
                {
                    "id": note.id,
                    "note_type": note.note_type,
                    "title": note.title,
                    "body": note.body,
                },
            )
        if method == "POST" and path.startswith("/api/favorites/") and path.endswith("/feedback"):
            listing_id = _path_id(path.removesuffix("/feedback"), "/api/favorites/")
            return ApiResponse(200, _update_favorite_feedback(session, listing_id, body))
        if method == "POST" and path.startswith("/api/neighborhoods/") and path.endswith("/link-home"):
            neighborhood_id = _path_id(path.removesuffix("/link-home"), "/api/neighborhoods/")
            property_id = int(body["property_id"])
            session.add(
                PropertyNeighborhoodMatch(
                    property_id=property_id,
                    saved_neighborhood_id=neighborhood_id,
                    relation="manually_linked",
                    distance_miles=None,
                    confidence="high",
                )
            )
            return ApiResponse(201, {"linked": True})
        if method == "POST" and path.startswith("/api/neighborhoods/") and path.endswith("/score"):
            neighborhood_id = _path_id(path.removesuffix("/score"), "/api/neighborhoods/")
            neighborhood = session.get(SavedNeighborhood, neighborhood_id)
            if neighborhood is None:
                return ApiResponse(404, {"error": "Saved neighborhood not found"})
            return ApiResponse(200, score_saved_neighborhood(session, neighborhood, persist=True))
        return ApiResponse(404, {"error": "Not found"})
    except (KeyError, TypeError, ValueError) as exc:
        return ApiResponse(400, {"error": str(exc)})


def response_json(response: ApiResponse) -> bytes:
    return json_dumps(response.payload).encode("utf-8")


def parse_json_body(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _path_id(path: str, prefix: str) -> int:
    value = path.removeprefix(prefix).strip("/")
    if "/" in value:
        value = value.split("/", 1)[0]
    return int(value)


def _create_map_note(session: Session, body: dict[str, Any]) -> MapNote:
    geometry = body.get("geometry") or body.get("geometry_geojson")
    note = MapNote(
        geometry_geojson=geometry_to_geojson(geometry) if geometry else None,
        latitude=float(body["lat"]) if body.get("lat") is not None else None,
        longitude=float(body["lon"]) if body.get("lon") is not None else None,
        note_type=str(body.get("note_type") or "observation"),
        title=body.get("title"),
        body=body.get("body"),
        tags_json=json_dumps(body.get("tags") or []),
        related_property_id=body.get("related_property_id"),
        related_neighborhood_id=body.get("related_neighborhood_id"),
    )
    session.add(note)
    session.flush()
    return note


def _create_favorite_home(session: Session, body: dict[str, Any]) -> Favorite:
    row = _favorite_row_from_body(body)
    favorite = upsert_favorite_from_row(row, session)
    session.flush()
    listing = favorite.listing
    if (
        listing is not None
        and _truthy(body.get("geocode", True))
        and (listing.property.latitude is None or listing.property.longitude is None)
    ):
        enrich_geocode_context(session, listing.property)
    session.flush()
    return favorite


def _favorite_row_from_body(body: dict[str, Any]) -> dict[str, Any]:
    address_text = _clean(body.get("address") or body.get("address_line1"))
    parsed = _parse_address_text(address_text or "")
    address = _clean(body.get("address_line1")) or parsed["address"] or address_text
    if not address:
        raise ValueError("Address is required.")
    row: dict[str, Any] = {
        "source": _clean(body.get("source")) or "map_app",
        "address": address,
        "city": _clean(body.get("city")) or parsed["city"],
        "state": _clean(body.get("state")) or parsed["state"] or "MN",
        "zip": _clean(body.get("zip")) or parsed["zip"],
        "user_rating": _normalize_home_rating(body.get("rating") or body.get("user_rating")),
        "user_notes": _clean(body.get("notes") or body.get("user_notes")),
    }
    for key in [
        "url",
        "listing_url",
        "price",
        "list_price",
        "beds",
        "baths",
        "finished_sqft",
        "lot_size",
        "lot_size_sqft",
        "year_built",
        "property_type",
        "status",
        "description",
        "garage_spaces",
        "school_district",
        "annual_taxes",
        "hoa_fee",
        "latitude",
        "longitude",
        "parcel_id",
        "source_listing_id",
        "mls_number",
    ]:
        if body.get(key) not in {None, ""}:
            row[key] = body[key]
    return row


def _parse_address_text(value: str) -> dict[str, str | None]:
    parts = [part.strip() for part in value.split(",") if part.strip()]
    address = parts[0] if parts else None
    tail = ", ".join(parts[1:]) if len(parts) > 1 else value
    city = parts[1] if len(parts) >= 3 else None
    state = None
    zip_code = None
    match = re.search(r"\b(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)\b", tail.upper())
    if match:
        state = match.group("state")
        zip_code = match.group("zip")
        if city is None:
            city_text = tail[: match.start()].strip(" ,")
            city = city_text or None
    return {"address": address, "city": city, "state": state, "zip": zip_code}


def _delete_home(session: Session, listing_id: int) -> bool:
    listing = session.get(Listing, listing_id)
    if listing is None:
        return False
    for report in session.execute(select(Report).where(Report.listing_id == listing_id)).scalars():
        report.listing_id = None
    for extraction in session.execute(
        select(LLMExtraction).where(LLMExtraction.listing_id == listing_id)
    ).scalars():
        extraction.listing_id = None
    session.delete(listing)
    session.flush()
    return True


def _update_favorite_feedback(
    session: Session,
    listing_id: int,
    body: dict[str, Any],
) -> dict[str, Any]:
    listing = session.get(Listing, listing_id)
    if listing is None:
        raise ValueError(f"Listing {listing_id} not found.")
    favorite = session.execute(
        select(Favorite).where(Favorite.listing_id == listing_id)
    ).scalar_one_or_none()
    if favorite is None:
        favorite = Favorite(listing=listing, external_url=listing.listing_url)
        session.add(favorite)
    if "rating" in body:
        rating = str(body["rating"])
        favorite.user_rating = "rejected" if rating == "reject" else rating
    if "notes" in body:
        favorite.user_notes = body["notes"]
    session.flush()
    return {
        "listing_id": listing_id,
        "favorite_id": favorite.id,
        "rating": favorite.user_rating,
        "notes": favorite.user_notes,
    }


def _normalize_home_rating(value: Any) -> str:
    rating = _clean(value) or "maybe"
    return "rejected" if rating == "reject" else rating


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None
