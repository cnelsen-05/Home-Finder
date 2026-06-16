from __future__ import annotations

import csv
import json
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from realestate.geospatial import feature_collection, json_dumps, json_loads, point_feature
from realestate.models import MapFeature, MapLayer, SchoolAcademicProfile, SchoolAttendanceZone
from realestate.paths import MAP_LAYER_CACHE_DIR

SCHOOL_LOCATIONS_LAYER_TYPE = "elementary_school_locations"
SCHOOL_LOCATIONS_SOURCE_NAME = "Minnesota School Program Locations Current View"
SCHOOL_LOCATIONS_LAYER_URL = (
    "https://services.arcgis.com/GXwOsvnLQI6EDOp7/arcgis/rest/services/"
    "Minnesota_School_Program_Locations_Current_View/FeatureServer/0"
)
SCHOOL_LOCATIONS_QUERY_URL = f"{SCHOOL_LOCATIONS_LAYER_URL}/query"

NICHE_SOURCE_NAME = "Niche"
NICHE_MN_ELEMENTARY_RANKINGS_URL = (
    "https://www.niche.com/k12/search/best-public-elementary-schools/s/minnesota/"
)
US_NEWS_SOURCE_NAME = "U.S. News & World Report"
US_NEWS_MN_ELEMENTARY_RANKINGS_URL = "https://www.usnews.com/education/k12/elementary-schools/minnesota"
US_NEWS_2026_RELEASE_URL = (
    "https://www.prnewswire.com/news-releases/"
    "us-news-reveals-2026-best-elementary-and-middle-schools-rankings-302595889.html"
)
RANKING_SOURCE_NAMES = (NICHE_SOURCE_NAME, US_NEWS_SOURCE_NAME)


def download_school_locations(
    output_path: Path | None = None,
    source_url: str = SCHOOL_LOCATIONS_QUERY_URL,
    page_size: int = 2000,
) -> Path:
    """Download/cache official Minnesota school program point locations."""

    MAP_LAYER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    output = output_path or MAP_LAYER_CACHE_DIR / "mn_school_program_locations_current.geojson"
    features: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = httpx.get(
            source_url,
            params={
                "f": "geojson",
                "where": "1=1",
                "outFields": "*",
                "outSR": "4326",
                "returnGeometry": "true",
                "resultOffset": str(offset),
                "resultRecordCount": str(page_size),
            },
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        page_features = payload.get("features") or []
        features.extend(page_features)
        if len(page_features) < page_size:
            break
        offset += page_size
    output.write_text(json_dumps(feature_collection(features)), encoding="utf-8")
    return output


def import_school_locations(
    session: Session,
    path: Path,
    *,
    elementary_only: bool = True,
    replace: bool = True,
) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("type") != "FeatureCollection":
        raise ValueError("School-location import expects a GeoJSON FeatureCollection.")
    if replace:
        session.execute(delete(MapFeature).where(MapFeature.layer_type == SCHOOL_LOCATIONS_LAYER_TYPE))
    count = 0
    for feature in payload.get("features") or []:
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates") or []
        if geometry.get("type") != "Point" or len(coords) < 2:
            continue
        props = _casefold_props(feature.get("properties") or {})
        grade_range = str(_first_nonblank(props, "graderange") or "")
        school_name = _first_nonblank(props, "mdename", "gisname", "altname")
        if not school_name:
            continue
        if elementary_only and not _looks_elementary_grade_range(grade_range):
            continue
        lon = float(coords[0])
        lat = float(coords[1])
        source_key = str(_first_nonblank(props, "formid", "objectid", "schnumber") or f"school:{count}")
        session.add(
            MapFeature(
                layer_type=SCHOOL_LOCATIONS_LAYER_TYPE,
                category="elementary_school",
                name=str(school_name),
                source_name=SCHOOL_LOCATIONS_SOURCE_NAME,
                source_url=SCHOOL_LOCATIONS_LAYER_URL,
                source_key=source_key,
                latitude=lat,
                longitude=lon,
                geometry_geojson=json_dumps({"type": "Point", "coordinates": [lon, lat]}),
                confidence="high",
                metadata_json=json_dumps(
                    {
                        "gis_name": props.get("gisname"),
                        "mde_name": props.get("mdename"),
                        "address": _first_nonblank(props, "mdeaddr", "gisaddr"),
                        "grade_range": grade_range,
                        "organization_number": props.get("orgnumber"),
                        "school_number": props.get("schnumber"),
                        "form_id": props.get("formid"),
                        "location_type": props.get("loctype"),
                        "class": props.get("class"),
                        "raw_properties": props,
                    }
                ),
            )
        )
        count += 1
    _upsert_school_locations_layer(session, count)
    session.flush()
    return count


def school_locations_geojson(session: Session) -> dict[str, Any]:
    features = session.execute(
        select(MapFeature)
        .where(MapFeature.layer_type == SCHOOL_LOCATIONS_LAYER_TYPE)
        .order_by(MapFeature.name)
    ).scalars().all()
    return feature_collection([school_location_feature(session, feature) for feature in features])


def school_location_feature(session: Session, feature: MapFeature) -> dict[str, Any]:
    metadata = json_loads(feature.metadata_json, {})
    profiles = school_academic_profiles_for_school(
        session,
        feature.name or "",
        district_name=metadata.get("district_name"),
    )
    return point_feature(
        feature.longitude or 0.0,
        feature.latitude or 0.0,
        {
            "id": feature.id,
            "layer_type": feature.layer_type,
            "category": feature.category,
            "name": feature.name,
            "address": metadata.get("address"),
            "grade_range": metadata.get("grade_range"),
            "source_name": feature.source_name,
            "source_url": feature.source_url,
            "confidence": feature.confidence,
            "metadata": metadata,
            "academic_profiles": profiles,
            "ranking_statuses": school_ranking_statuses(session, profiles),
            "niche_rank": _first_profile_value(profiles, NICHE_SOURCE_NAME, "state_rank"),
            "niche_grade": _first_profile_value(profiles, NICHE_SOURCE_NAME, "rating_label"),
            "us_news_rank": _first_profile_value(profiles, US_NEWS_SOURCE_NAME, "state_rank"),
            "us_news_rating": _first_profile_value(profiles, US_NEWS_SOURCE_NAME, "rating_label"),
        },
        feature_id=feature.id,
    )


def enrich_school_zone_payload(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("found") and not payload.get("school_name"):
        return payload
    school_name = str(payload.get("school_name") or "")
    district_name = payload.get("district_name")
    location = school_location_for_school(session, school_name, district_name=district_name)
    profiles = school_academic_profiles_for_school(session, school_name, district_name=district_name)
    enriched = dict(payload)
    enriched["school_location"] = location
    enriched["academic_profiles"] = profiles
    enriched["ranking_statuses"] = school_ranking_statuses(session, profiles)
    enriched["niche_rank"] = _first_profile_value(profiles, NICHE_SOURCE_NAME, "state_rank")
    enriched["niche_grade"] = _first_profile_value(profiles, NICHE_SOURCE_NAME, "rating_label")
    enriched["us_news_rank"] = _first_profile_value(profiles, US_NEWS_SOURCE_NAME, "state_rank")
    enriched["us_news_rating"] = _first_profile_value(profiles, US_NEWS_SOURCE_NAME, "rating_label")
    return enriched


def school_context_for_zone(session: Session, zone: SchoolAttendanceZone) -> dict[str, Any]:
    payload = {
        "school_name": zone.school_name,
        "district_name": zone.district_name,
        "school_year": zone.school_year,
        "source_name": zone.source_name,
        "source_url": zone.source_url,
        "confidence": zone.confidence,
    }
    return enrich_school_zone_payload(session, payload)


def school_location_for_school(
    session: Session,
    school_name: str,
    *,
    district_name: str | None = None,
) -> dict[str, Any] | None:
    candidates = session.execute(
        select(MapFeature).where(MapFeature.layer_type == SCHOOL_LOCATIONS_LAYER_TYPE)
    ).scalars().all()
    target = _school_key(school_name)
    ranked: list[tuple[int, MapFeature]] = []
    for feature in candidates:
        key = _school_key(feature.name or "")
        if not key:
            continue
        if key == target:
            score = 0
        elif key in target or target in key:
            score = 1
        else:
            continue
        metadata = json_loads(feature.metadata_json, {})
        if district_name and _district_key(district_name) in _district_key(metadata.get("district_name") or ""):
            score -= 1
        ranked.append((score, feature))
    if not ranked:
        return None
    feature = sorted(ranked, key=lambda item: (item[0], item[1].name or ""))[0][1]
    metadata = json_loads(feature.metadata_json, {})
    return {
        "id": feature.id,
        "name": feature.name,
        "address": metadata.get("address"),
        "grade_range": metadata.get("grade_range"),
        "lat": feature.latitude,
        "lon": feature.longitude,
        "source_name": feature.source_name,
        "source_url": feature.source_url,
        "confidence": feature.confidence,
    }


def school_academic_profiles_for_school(
    session: Session,
    school_name: str,
    *,
    district_name: str | None = None,
    limit: int = 4,
) -> list[dict[str, Any]]:
    target = _school_key(school_name)
    if not target:
        return []
    profiles = session.execute(select(SchoolAcademicProfile)).scalars().all()
    rows: list[tuple[int, SchoolAcademicProfile]] = []
    district_target = _district_key(district_name or "")
    for profile in profiles:
        key = _school_key(profile.school_name)
        if key == target:
            score = 0
        else:
            continue
        if district_target and profile.district_name:
            district_key = _district_key(profile.district_name)
            if district_key == district_target or district_target in district_key or district_key in district_target:
                score -= 1
        rows.append((score, profile))
    sorted_profiles = sorted(
        rows,
        key=lambda item: (
            item[0],
            _profile_source_priority(item[1].source_name),
            item[1].state_rank or 999999,
            item[1].school_name,
        ),
    )
    return [_academic_profile_payload(profile) for _score, profile in sorted_profiles[:limit]]


def count_school_locations(session: Session) -> int:
    return int(
        session.execute(
            select(func.count()).select_from(MapFeature).where(MapFeature.layer_type == SCHOOL_LOCATIONS_LAYER_TYPE)
        ).scalar_one()
    )


def count_school_academic_profiles(session: Session, source_name: str | None = None) -> int:
    stmt = select(func.count()).select_from(SchoolAcademicProfile)
    if source_name:
        stmt = stmt.where(SchoolAcademicProfile.source_name == source_name)
    return int(session.execute(stmt).scalar_one())


def school_ranking_statuses(session: Session, profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    profiles_by_source = {profile.get("source_name"): profile for profile in profiles}
    statuses: list[dict[str, Any]] = []
    for source_name in RANKING_SOURCE_NAMES:
        profile = profiles_by_source.get(source_name)
        metadata = _ranking_layer_metadata(session, source_name)
        ranking_cutoff = _to_int(metadata.get("ranking_cutoff") or metadata.get("profile_count"))
        if profile:
            status = dict(profile)
            status["source_name"] = source_name
            status["status"] = "ranked"
            status["ranking_cutoff"] = ranking_cutoff
            status["display_label"] = _ranking_status_label(source_name, profile, ranking_cutoff)
            statuses.append(status)
            continue
        if ranking_cutoff and ranking_cutoff >= 250:
            label = f"Not ranked in imported top {ranking_cutoff}"
            status = "not_ranked"
        elif ranking_cutoff:
            label = f"Top {ranking_cutoff} imported; top 250 not imported"
            status = "top_250_not_imported"
        else:
            label = "Ranking not imported"
            status = "not_imported"
        statuses.append(
            {
                "source_name": source_name,
                "source_url": _ranking_source_url(source_name),
                "status": status,
                "ranking_cutoff": ranking_cutoff,
                "display_label": label,
                "confidence": metadata.get("confidence") or "medium",
            }
        )
    return statuses


def download_niche_elementary_rankings(
    output_path: Path | None = None,
    *,
    url: str = NICHE_MN_ELEMENTARY_RANKINGS_URL,
    top_count: int = 250,
    page_size: int = 25,
) -> Path:
    """Cache public Niche ranking pages when reachable without CAPTCHA."""

    MAP_LAYER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    output = output_path or MAP_LAYER_CACHE_DIR / "niche_mn_elementary_rankings_top250.html"
    page_count = max(1, (max(1, top_count) + page_size - 1) // page_size)
    page_texts: list[str] = []
    for page in range(1, page_count + 1):
        page_url = _ranking_page_url(url, page)
        response = httpx.get(
            page_url,
            headers={"User-Agent": "HomeAnalyze personal real-estate map hub"},
            follow_redirects=True,
            timeout=60,
        )
        if response.status_code in {401, 403} or "px-captcha" in response.text.lower():
            raise ValueError(
                "Niche blocked automated download with CAPTCHA/access controls. Save ranking pages "
                "1-10 as HTML or CSV in a browser and run school-rankings import-niche on that file "
                "or directory. The app will not bypass CAPTCHA."
            )
        response.raise_for_status()
        page_texts.append(f"\n<!-- source_url={page_url} -->\n{response.text}")
    output.write_text("\n".join(page_texts), encoding="utf-8")
    return output


def download_us_news_elementary_rankings(
    output_path: Path | None = None,
    *,
    url: str = US_NEWS_MN_ELEMENTARY_RANKINGS_URL,
    top_count: int = 250,
    page_size: int = 10,
) -> Path:
    """Cache U.S. News elementary ranking rows when reachable without access gates."""

    MAP_LAYER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    output = output_path or MAP_LAYER_CACHE_DIR / "us_news_mn_elementary_rankings_top250.json"
    page_count = max(1, (max(1, top_count) + page_size - 1) // page_size)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None]] = set()
    for page in range(1, page_count + 1):
        page_url = _ranking_page_url(url, page)
        response = httpx.get(
            page_url,
            headers={"User-Agent": "HomeAnalyze personal real-estate map hub"},
            follow_redirects=True,
            timeout=60,
        )
        blocked_text = response.text.lower()
        if response.status_code in {401, 403} or "captcha" in blocked_text or "access denied" in blocked_text:
            raise ValueError(
                "U.S. News blocked automated download. Save the ranking page or a credible source summary "
                "as HTML/CSV in a browser and run school-rankings import-us-news on that file."
            )
        response.raise_for_status()
        page_rows = _parse_us_news_html(response.text)
        for row in page_rows:
            rank = _to_int(row.get("state_rank") or row.get("rank"))
            key = (str(row.get("school_name") or ""), rank)
            if key in seen:
                continue
            seen.add(key)
            row["ranking_page_url"] = page_url
            rows.append(row)
            if len(rows) >= top_count:
                break
        if len(rows) >= top_count or not page_rows:
            break
    output.write_text(
        json_dumps(
            {
                "source_name": US_NEWS_SOURCE_NAME,
                "source_url": url,
                "retrieved_at": datetime.now(UTC).isoformat(),
                "top_count": top_count,
                "rows": rows[:top_count],
            }
        ),
        encoding="utf-8",
    )
    return output


def import_niche_rankings(
    session: Session,
    path: Path,
    *,
    school_year: str | None = "2026",
    replace: bool = True,
) -> int:
    rows = _load_niche_rows(path)
    ranking_cutoff = _ranking_cutoff_from_rows(rows)
    if replace:
        session.execute(
            delete(SchoolAcademicProfile).where(
                SchoolAcademicProfile.source_name == NICHE_SOURCE_NAME,
                SchoolAcademicProfile.school_year == school_year,
            )
        )
    count = 0
    for row in rows:
        school_name = str(row.get("school_name") or "").strip()
        if not school_name:
            continue
        _upsert_academic_profile(
            session,
            SchoolAcademicProfile(
                school_name=school_name,
                district_name=_clean_blank(row.get("district_name")),
                school_year=school_year,
                source_name=NICHE_SOURCE_NAME,
                source_url=str(row.get("source_url") or NICHE_MN_ELEMENTARY_RANKINGS_URL),
                state_rank=_to_int(row.get("state_rank") or row.get("rank")),
                rating_label=_clean_blank(row.get("rating_label") or row.get("niche_grade")),
                enrollment=_to_int(row.get("enrollment") or row.get("students")),
                student_teacher_ratio=_to_float(row.get("student_teacher_ratio")),
                confidence="medium",
                metadata_json=json_dumps(
                    {
                        "ranking_scope": "Best Public Elementary Schools in Minnesota",
                        "source_note": (
                            "Third-party Niche ranking. Treat as context, not a school-assignment "
                            "or neighborhood-quality claim."
                        ),
                        "retrieved_or_imported_at": datetime.now(UTC).isoformat(),
                        "raw_row": row,
                    }
                ),
            ),
        )
        count += 1
    _upsert_rankings_layer(
        session,
        count,
        school_year,
        source_name=NICHE_SOURCE_NAME,
        source_url=NICHE_MN_ELEMENTARY_RANKINGS_URL,
        layer_name="Niche elementary school rankings",
        warning="Third-party rankings are context only and should be verified at the source.",
        ranking_cutoff=ranking_cutoff,
    )
    session.flush()
    return count


def import_us_news_rankings(
    session: Session,
    path: Path,
    *,
    school_year: str | None = "2026",
    replace: bool = True,
) -> int:
    rows = _load_us_news_rows(path)
    ranking_cutoff = _ranking_cutoff_from_rows(rows)
    if replace:
        session.execute(
            delete(SchoolAcademicProfile).where(
                SchoolAcademicProfile.source_name == US_NEWS_SOURCE_NAME,
                SchoolAcademicProfile.school_year == school_year,
            )
        )
    count = 0
    for row in rows:
        school_name = str(row.get("school_name") or "").strip()
        if not school_name:
            continue
        _upsert_academic_profile(
            session,
            SchoolAcademicProfile(
                school_name=school_name,
                district_name=_clean_blank(row.get("district_name")),
                school_year=school_year,
                source_name=US_NEWS_SOURCE_NAME,
                source_url=str(row.get("source_url") or US_NEWS_MN_ELEMENTARY_RANKINGS_URL),
                state_rank=_to_int(row.get("state_rank") or row.get("rank")),
                rating_label=_clean_blank(row.get("rating_label") or row.get("score") or row.get("overall_score")),
                math_proficiency=_to_float(row.get("math_proficiency") or row.get("math_proficiency_pct")),
                reading_proficiency=_to_float(
                    row.get("reading_proficiency")
                    or row.get("reading_proficiency_pct")
                    or row.get("reading_language_arts_proficiency")
                ),
                enrollment=_to_int(row.get("enrollment") or row.get("students")),
                student_teacher_ratio=_to_float(row.get("student_teacher_ratio")),
                confidence=str(row.get("confidence") or "medium"),
                metadata_json=json_dumps(
                    {
                        "ranking_scope": "Best Elementary Schools in Minnesota",
                        "source_note": (
                            "U.S. News & World Report ranking context. Treat as source-labeled "
                            "academic context, not a school-assignment or neighborhood-quality claim."
                        ),
                        "methodology_note": (
                            "U.S. News says the K-8 rankings use publicly available U.S. Department "
                            "of Education data; verify methodology and current rank at the source."
                        ),
                        "release_url": US_NEWS_2026_RELEASE_URL,
                        "retrieved_or_imported_at": datetime.now(UTC).isoformat(),
                        "raw_row": row,
                    }
                ),
            ),
        )
        count += 1
    _upsert_rankings_layer(
        session,
        count,
        school_year,
        source_name=US_NEWS_SOURCE_NAME,
        source_url=US_NEWS_MN_ELEMENTARY_RANKINGS_URL,
        layer_name="U.S. News elementary school rankings",
        warning="U.S. News rankings are third-party academic context and should be verified at the source.",
        ranking_cutoff=ranking_cutoff,
    )
    session.flush()
    return count


def _upsert_academic_profile(session: Session, incoming: SchoolAcademicProfile) -> None:
    existing = session.execute(
        select(SchoolAcademicProfile).where(
            SchoolAcademicProfile.source_name == incoming.source_name,
            SchoolAcademicProfile.school_name == incoming.school_name,
            SchoolAcademicProfile.district_name == incoming.district_name,
            SchoolAcademicProfile.school_year == incoming.school_year,
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(incoming)
        return
    existing.source_url = incoming.source_url
    existing.state_rank = incoming.state_rank
    existing.rating_label = incoming.rating_label
    existing.math_proficiency = incoming.math_proficiency
    existing.reading_proficiency = incoming.reading_proficiency
    existing.enrollment = incoming.enrollment
    existing.student_teacher_ratio = incoming.student_teacher_ratio
    existing.confidence = incoming.confidence
    existing.metadata_json = incoming.metadata_json


def _load_niche_rows(path: Path) -> list[dict[str, Any]]:
    if path.is_dir():
        rows: list[dict[str, Any]] = []
        for child in sorted(path.iterdir()):
            if child.suffix.lower() in {".csv", ".json", ".html", ".htm"}:
                rows.extend(_load_niche_rows(child))
        return rows
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [_normalize_row_keys(row) for row in csv.DictReader(handle)]
    raw = path.read_text(encoding="utf-8")
    if suffix == ".json":
        payload = json.loads(raw)
        if isinstance(payload, dict):
            payload = payload.get("rows") or payload.get("rankings") or payload.get("data") or []
        return [_normalize_row_keys(row) for row in payload if isinstance(row, dict)]
    return _parse_niche_html(raw)


def _load_us_news_rows(path: Path) -> list[dict[str, Any]]:
    if path.is_dir():
        rows: list[dict[str, Any]] = []
        for child in sorted(path.iterdir()):
            if child.suffix.lower() in {".csv", ".json", ".html", ".htm"}:
                rows.extend(_load_us_news_rows(child))
        return rows
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [_normalize_row_keys(row) for row in csv.DictReader(handle)]
    raw = path.read_text(encoding="utf-8")
    if suffix == ".json":
        payload = json.loads(raw)
        if isinstance(payload, dict):
            payload = payload.get("rows") or payload.get("rankings") or payload.get("data") or []
        return [_normalize_row_keys(row) for row in payload if isinstance(row, dict)]
    return _parse_us_news_html(raw)


def _parse_niche_html(raw_html: str) -> list[dict[str, Any]]:
    text = _html_to_text(raw_html)
    matches = list(re.finditer(r"#(?P<rank>\d+)\s+Best Public Elementary Schools in Minnesota", text))
    rows: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunk = text[start:end]
        head = re.split(
            r"\b(?:Rating|Read|Featured Review|View nearby homes|Virtual tour|Add to List|Overall Niche Grade|Students)\b",
            chunk,
            maxsplit=1,
        )[0]
        school_name, district_name = _split_niche_school_and_district(head)
        if not school_name:
            continue
        grade = _regex_first(chunk, r"Overall Niche Grade:\s*([A-F][+-]?)")
        if not grade:
            grade = _regex_first(chunk, r"\bgrade\s+([A-F][+-]?)\s+Overall Niche Grade")
        students = _regex_first(chunk, r"Students\s+([\d,]+)")
        ratio = _regex_first(chunk, r"Student-Teacher Ratio\s+(\d+(?:\.\d+)?)\s*(?:to\s*)?1")
        rows.append(
            {
                "state_rank": int(match.group("rank")),
                "school_name": school_name,
                "district_name": district_name,
                "rating_label": grade,
                "enrollment": students,
                "student_teacher_ratio": ratio,
                "source_url": NICHE_MN_ELEMENTARY_RANKINGS_URL,
            }
        )
    return rows


def _parse_us_news_html(raw_html: str) -> list[dict[str, Any]]:
    page_rows = _parse_us_news_page_context(raw_html)
    if page_rows:
        return page_rows
    text = _html_to_text(raw_html)
    dash_pattern = "[-\u2013\u2014]"
    pattern = re.compile(
        r"(?P<rank>\d{1,3})\.\s+(?P<school>.+?)\s+"
        + dash_pattern
        + r"\s+(?P<district>.+?)(?=\s+\d{1,3}\.\s+|$)"
    )
    rows: list[dict[str, Any]] = []
    for match in pattern.finditer(text):
        school_name = _clean_blank(match.group("school"))
        district_name = _clean_blank(match.group("district"))
        if not school_name:
            continue
        rows.append(
            {
                "state_rank": int(match.group("rank")),
                "school_name": school_name,
                "district_name": district_name,
                "source_url": US_NEWS_MN_ELEMENTARY_RANKINGS_URL,
            }
        )
    return rows


def _parse_us_news_page_context(raw_html: str) -> list[dict[str, Any]]:
    match = re.search(r"window\['__PAGE_CONTEXT_QUERY_STATE__'\] = (.*?);\s*</script>", raw_html, re.S)
    if not match:
        return []
    raw = re.sub(r"(?<=[:,\[])undefined(?=[,}\]])", "null", match.group(1))
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    page = payload.get("src/containers/pages/education/k-12/search/index.js", {}).get("data", {})
    rows: list[dict[str, Any]] = []
    for item in page.get("items") or []:
        if not isinstance(item, dict):
            continue
        school_name = _clean_blank(item.get("name"))
        if not school_name:
            continue
        rank_payload = _first_us_news_rank(item.get("ranks") or [])
        school_payload = item.get("school") or {}
        rows.append(
            {
                "state_rank": _to_int(rank_payload.get("value")),
                "school_name": school_name,
                "district_name": _clean_blank(school_payload.get("district")),
                "source_url": school_payload.get("profile_url") or US_NEWS_MN_ELEMENTARY_RANKINGS_URL,
                "math_proficiency": item.get("math_prof"),
                "reading_proficiency": item.get("reading_prof"),
                "enrollment": item.get("student_pop"),
                "student_teacher_ratio": _us_news_data_value(item, "Student-Teacher Ratio"),
                "profile_url": school_payload.get("profile_url"),
                "district_page_url": school_payload.get("district_page_url"),
                "location": school_payload.get("location") or item.get("location_string"),
                "usnews_id": item.get("usnews_hs_id"),
                "ranking_label": rank_payload.get("label"),
            }
        )
    return rows


def _split_niche_school_and_district(value: str) -> tuple[str | None, str | None]:
    text = re.sub(r"\s+", " ", value).strip(" ,")
    text = re.sub(r"\b(?:PK|K|EC)?-?\d+\b.*$", "", text).strip(" ,")
    before_mn = text.split(", MN", 1)[0].strip(" ,")
    if not before_mn:
        return None, None
    school_match = re.match(
        r"(?P<school>.+?(?:Intermediate Elementary School|Elementary School|Lower School|Academy|School))\s+(?P<rest>.+)$",
        before_mn,
    )
    if school_match:
        return school_match.group("school").strip(" ,"), school_match.group("rest").strip(" ,")
    district_match = re.match(
        r"(?P<school>.+?)\s+(?P<district>[^,]+(?:Public Schools|Public School District|Schools|School District|Charter School))$",
        before_mn,
    )
    if district_match:
        return district_match.group("school").strip(" ,"), district_match.group("district").strip(" ,")
    return before_mn, None


def _academic_profile_payload(profile: SchoolAcademicProfile) -> dict[str, Any]:
    return {
        "id": profile.id,
        "school_name": profile.school_name,
        "district_name": profile.district_name,
        "school_year": profile.school_year,
        "source_name": profile.source_name,
        "source_url": profile.source_url,
        "state_rank": profile.state_rank,
        "rating_label": profile.rating_label,
        "math_proficiency": profile.math_proficiency,
        "reading_proficiency": profile.reading_proficiency,
        "enrollment": profile.enrollment,
        "student_teacher_ratio": profile.student_teacher_ratio,
        "confidence": profile.confidence,
        "metadata": json_loads(profile.metadata_json, {}),
    }


def _upsert_school_locations_layer(session: Session, count: int) -> None:
    layer = session.execute(
        select(MapLayer).where(
            MapLayer.name == "Elementary school locations",
            MapLayer.layer_type == SCHOOL_LOCATIONS_LAYER_TYPE,
        )
    ).scalar_one_or_none()
    if layer is None:
        layer = MapLayer(
            name="Elementary school locations",
            layer_type=SCHOOL_LOCATIONS_LAYER_TYPE,
            geometry_type="Point",
            enabled_by_default=False,
        )
        session.add(layer)
    layer.source_name = SCHOOL_LOCATIONS_SOURCE_NAME
    layer.source_url = SCHOOL_LOCATIONS_LAYER_URL
    layer.retrieved_at = datetime.now(UTC)
    layer.metadata_json = json_dumps(
        {
            "feature_count": count,
            "filter": "Imported program points whose grade range appears elementary-serving.",
        }
    )
    layer.style_json = json_dumps({"marker": "#204c7a"})


def _upsert_rankings_layer(
    session: Session,
    count: int,
    school_year: str | None,
    *,
    source_name: str,
    source_url: str,
    layer_name: str,
    warning: str,
    ranking_cutoff: int | None,
) -> None:
    layer = session.execute(
        select(MapLayer).where(
            MapLayer.name == layer_name,
            MapLayer.layer_type == "school_academic_profiles",
        )
    ).scalar_one_or_none()
    if layer is None:
        layer = MapLayer(
            name=layer_name,
            layer_type="school_academic_profiles",
            geometry_type="None",
            enabled_by_default=False,
        )
        session.add(layer)
    layer.source_name = source_name
    layer.source_url = source_url
    layer.retrieved_at = datetime.now(UTC)
    layer.metadata_json = json_dumps(
        {
            "school_year": school_year,
            "profile_count": count,
            "ranking_cutoff": ranking_cutoff,
            "warning": warning,
        }
    )


def _looks_elementary_grade_range(value: str) -> bool:
    normalized = value.strip().upper()
    if not normalized:
        return False
    if any(token in normalized for token in ("PK", "EC", "KG", "K")):
        return True
    numbers = [int(item) for item in re.findall(r"\d+", normalized)]
    return bool(numbers and min(numbers) <= 6)


def _school_key(value: str) -> str:
    text = _normalize_search_text(value)
    for suffix in (
        "elementary school",
        "elementary",
        "lower school",
        "intermediate elementary school",
        "school",
    ):
        text = re.sub(rf"\b{re.escape(suffix)}\b", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _district_key(value: str) -> str:
    text = _normalize_search_text(value)
    for suffix in ("public schools", "public school district", "schools", "school district"):
        text = re.sub(rf"\b{re.escape(suffix)}\b", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _profile_source_priority(source_name: str | None) -> int:
    if source_name == NICHE_SOURCE_NAME:
        return 0
    if source_name == US_NEWS_SOURCE_NAME:
        return 1
    return 2


def _ranking_cutoff_from_rows(rows: list[dict[str, Any]]) -> int | None:
    ranks = [
        rank
        for rank in (_to_int(row.get("state_rank") or row.get("rank")) for row in rows)
        if rank is not None
    ]
    return max(ranks) if ranks else None


def _ranking_layer_metadata(session: Session, source_name: str) -> dict[str, Any]:
    layer = session.execute(
        select(MapLayer)
        .where(
            MapLayer.layer_type == "school_academic_profiles",
            MapLayer.source_name == source_name,
        )
        .order_by(MapLayer.retrieved_at.desc())
    ).scalar_one_or_none()
    if layer is None:
        return {}
    metadata = json_loads(layer.metadata_json, {})
    metadata["source_url"] = layer.source_url
    metadata["retrieved_at"] = layer.retrieved_at.isoformat() if layer.retrieved_at else None
    return metadata


def _ranking_source_url(source_name: str) -> str:
    if source_name == NICHE_SOURCE_NAME:
        return NICHE_MN_ELEMENTARY_RANKINGS_URL
    if source_name == US_NEWS_SOURCE_NAME:
        return US_NEWS_MN_ELEMENTARY_RANKINGS_URL
    return ""


def _ranking_status_label(
    source_name: str,
    profile: dict[str, Any],
    ranking_cutoff: int | None,
) -> str:
    rank = profile.get("state_rank")
    label = f"#{rank}" if rank else "Ranked"
    if profile.get("rating_label"):
        label = f"{label} {profile['rating_label']}"
    if ranking_cutoff and ranking_cutoff >= 250:
        return f"{label} in imported top {ranking_cutoff}"
    return label


def _ranking_page_url(url: str, page: int) -> str:
    if page <= 1:
        return url
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["page"] = str(page)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _first_us_news_rank(ranks: list[dict[str, Any]]) -> dict[str, Any]:
    for rank in ranks:
        if "Elementary" in str(rank.get("label") or ""):
            return rank
    return ranks[0] if ranks else {}


def _us_news_data_value(item: dict[str, Any], label: str) -> Any:
    for row in item.get("data") or []:
        if row.get("label") == label:
            return row.get("raw_value") or row.get("display_value")
    return None


def _normalize_search_text(value: str | None) -> str:
    text = str(value or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _casefold_props(props: dict[str, Any]) -> dict[str, Any]:
    return {str(key).lower(): value for key, value in props.items()}


def _first_nonblank(props: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = props.get(key.lower())
        if value not in (None, ""):
            return value
    return None


def _first_profile_value(profiles: list[dict[str, Any]], source_name: str, key: str) -> Any:
    for profile in profiles:
        if profile.get("source_name") == source_name and profile.get(key) is not None:
            return profile[key]
    return None


def _normalize_row_keys(row: dict[str, Any]) -> dict[str, Any]:
    return {str(key).strip().lower().replace(" ", "_"): value for key, value in row.items()}


def _clean_blank(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _to_int(value: Any) -> int | None:
    text = str(value or "").replace(",", "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _to_float(value: Any) -> float | None:
    text = str(value or "").replace("to 1", "").replace(":1", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _regex_first(value: str, pattern: str) -> str | None:
    match = re.search(pattern, value, flags=re.IGNORECASE)
    return match.group(1).strip() if match else None


def _html_to_text(raw_html: str) -> str:
    parser = _TextParser()
    parser.feed(raw_html)
    return re.sub(r"\s+", " ", " ".join(parser.parts))


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data.strip():
            self.parts.append(data.strip())
