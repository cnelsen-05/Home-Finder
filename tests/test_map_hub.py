from __future__ import annotations

import json

from sqlalchemy import select

from realestate.geospatial import circle_polygon
from realestate.map_api import handle_api_request
from realestate.map_data import build_map_data_exports
from realestate.map_highlights import create_map_highlight, property_highlight_context
from realestate.map_layers import import_parks_trails_playgrounds, parks_trails_playgrounds_geojson
from realestate.models import (
    Favorite,
    Listing,
    LLMExtraction,
    MapFeature,
    Property,
    PropertyNeighborhoodMatch,
    Report,
    SavedNeighborhood,
    SavedNeighborhoodScore,
    SchoolAcademicProfile,
)
from realestate.neighborhood_scoring import score_saved_neighborhood
from realestate.neighborhoods import (
    create_saved_neighborhood,
    export_saved_neighborhoods,
    import_saved_neighborhoods,
    match_homes_to_neighborhoods,
)
from realestate.reports.render import render_neighborhood_report
from realestate.school_zones import identify_elementary_zone, import_attendance_zones
from realestate.schools import (
    import_niche_rankings,
    import_school_locations,
    import_us_news_rankings,
    school_locations_geojson,
)


def test_school_zone_import_lookup_and_near_boundary(session, tmp_path) -> None:
    path = tmp_path / "attendance_2026.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "OBJECTID": 1,
                            "ELEM_NAME": "Birch Elementary",
                            "SDPREFNAME": "Example Public Schools",
                        },
                        "geometry": _square(-94.0, 45.0, -93.0, 46.0),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    imported = import_attendance_zones(session, path, school_year="2026")
    session.flush()

    assert imported == 1
    result = identify_elementary_zone(session, lat=45.5, lon=-93.5).as_dict()
    assert result["school_name"] == "Birch Elementary"
    assert result["source_name"] == "Minnesota School Attendance Areas Current View"
    assert result["school_year"] == "2026"

    near = identify_elementary_zone(
        session,
        lat=45.5,
        lon=-93.999,
        boundary_threshold_miles=0.06,
    ).as_dict()
    assert near["near_boundary"] is True
    assert "verify directly" in near["warning"].lower()


def test_saved_neighborhood_matching_inside_near_and_same_zone(session, tmp_path) -> None:
    _import_test_zone(session, tmp_path)
    inside = _add_listing(session, "100 Pocket Ave", -93.50, 45.50)
    near = _add_listing(session, "200 Nearby Ave", -93.195, 45.50)
    neighborhood = create_saved_neighborhood(
        session,
        name="Favorite pocket",
        geometry=_square(-93.8, 45.2, -93.2, 45.8),
        rating="favorite",
        tags=["quiet_street", "parks"],
        notes="Quiet streets and mature trees.",
    )
    session.flush()

    count = match_homes_to_neighborhoods(session, near_miles=0.5)
    session.flush()

    assert count >= 3
    matches = session.execute(select(PropertyNeighborhoodMatch)).scalars().all()
    inside_match = [
        match
        for match in matches
        if match.property_id == inside.property_id and match.saved_neighborhood_id == neighborhood.id
    ]
    near_match = [
        match
        for match in matches
        if match.property_id == near.property_id and match.saved_neighborhood_id == neighborhood.id
    ]
    assert {match.relation for match in inside_match} >= {"inside", "same_zone"}
    assert {match.relation for match in near_match} >= {"near", "same_zone"}


def test_neighborhood_geojson_export_import(session, tmp_path) -> None:
    create_saved_neighborhood(
        session,
        name="Trail pocket",
        geometry=circle_polygon(-93.4, 45.1, 0.2),
        rating="like",
        tags=["trails"],
        notes="Good trail access.",
    )
    session.flush()
    export_path = tmp_path / "areas.geojson"

    export_saved_neighborhoods(session, export_path)
    session.query(SavedNeighborhood).delete()
    session.flush()
    count = import_saved_neighborhoods(session, export_path)
    session.flush()

    assert count == 1
    imported = session.execute(select(SavedNeighborhood)).scalars().one()
    assert imported.name == "Trail pocket"
    assert "trails" in imported.tags_json


def test_neighborhood_report_rendering(session, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _import_test_zone(session, tmp_path)
    _add_listing(session, "100 Pocket Ave", -93.50, 45.50)
    neighborhood = create_saved_neighborhood(
        session,
        name="Golden Valley pocket",
        geometry=_square(-93.8, 45.2, -93.2, 45.8),
        rating="strong_like",
        tags=["quiet_street"],
        notes="Liked this pocket more than surrounding area.",
    )
    session.flush()
    match_homes_to_neighborhoods(session)
    session.flush()

    path = render_neighborhood_report(session, neighborhood.id)

    text = path.read_text(encoding="utf-8")
    assert "Neighborhood Area Report" in text
    assert "Golden Valley pocket" in text
    assert "Birch Elementary" in text
    assert "Verify" in text


def test_map_api_create_neighborhood_identify_and_map_exports(session, tmp_path) -> None:
    _import_test_zone(session, tmp_path)
    listing = _add_listing(session, "100 Pocket Ave", -93.50, 45.50)
    response = handle_api_request(
        session,
        "POST",
        "/api/neighborhoods",
        {
            "name": "API pocket",
            "rating": "maybe",
            "geometry": _square(-93.8, 45.2, -93.2, 45.8),
            "tags": ["tour_again"],
        },
    )
    session.flush()

    assert response.status == 201
    assert response.payload["properties"]["name"] == "API pocket"

    lookup = handle_api_request(
        session,
        "POST",
        "/api/school-zones/identify",
        {"lat": 45.5, "lon": -93.5},
    )
    assert lookup.status == 200
    assert lookup.payload["school_name"] == "Birch Elementary"

    match_homes_to_neighborhoods(session)
    session.flush()
    payload = handle_api_request(session, "GET", "/api/map-data")
    assert payload.status == 200
    assert payload.payload["homes"]["features"][0]["properties"]["listing_id"] == listing.id
    assert "school_zones" not in payload.payload
    assert payload.payload["lazy_layers"]["school_zones"]["feature_count"] == 1
    assert "map_highlights" in payload.payload
    school_layer = handle_api_request(session, "GET", "/api/school-zones")
    assert school_layer.payload["features"][0]["properties"]["school_name"] == "Birch Elementary"
    exports = build_map_data_exports(session, output_dir=tmp_path / "map_data")
    assert exports["map_payload"].exists()
    assert exports["school_zones"].exists()
    assert exports["map_highlights"].exists()


def test_map_api_adds_address_only_home_and_deletes_it(session) -> None:
    response = handle_api_request(
        session,
        "POST",
        "/api/homes",
        {
            "address": "1000 Sample Garden Rd, Shorewood, MN 55331",
            "rating": "like",
            "notes": "Quiet street candidate.",
            "geocode": False,
        },
    )
    session.flush()

    assert response.status == 201
    assert response.payload["geometry"] is None
    props = response.payload["properties"]
    assert props["address"] == "1000 Sample Garden Rd, Shorewood, MN, 55331"
    assert props["city"] == "Shorewood"
    assert props["zip"] == "55331"
    assert props["has_location"] is False
    assert props["map_status"] == "needs_location"

    listing_id = props["listing_id"]
    session.add(Report(report_type="favorite_home_review", listing_id=listing_id, path="report.md"))
    session.add(LLMExtraction(listing_id=listing_id, provider="test", task="extract"))
    session.flush()

    homes = handle_api_request(session, "GET", "/api/homes")
    assert homes.status == 200
    assert homes.payload["features"][0]["properties"]["listing_id"] == listing_id

    delete_response = handle_api_request(session, "DELETE", f"/api/homes/{listing_id}")
    session.flush()

    assert delete_response.status == 200
    assert delete_response.payload == {"deleted": True, "listing_id": listing_id}
    assert session.get(Listing, listing_id) is None
    assert session.execute(select(Favorite).where(Favorite.listing_id == listing_id)).scalars().all() == []
    assert session.execute(select(Report)).scalars().one().listing_id is None
    assert session.execute(select(LLMExtraction)).scalars().one().listing_id is None
    assert handle_api_request(session, "GET", "/api/homes").payload["features"] == []


def test_map_api_adds_home_with_clicked_map_pin(session, tmp_path) -> None:
    _import_test_zone(session, tmp_path)
    response = handle_api_request(
        session,
        "POST",
        "/api/homes",
        {
            "address": "2000 Sample 43rd Ave N, Plymouth, MN 55446",
            "rating": "maybe",
            "latitude": 45.5,
            "longitude": -93.5,
            "geocode": False,
        },
    )
    session.flush()

    assert response.status == 201
    assert response.payload["geometry"] == {"type": "Point", "coordinates": [-93.5, 45.5]}
    props = response.payload["properties"]
    assert props["has_location"] is True
    assert props["elementary_zone"]["school_name"] == "Birch Elementary"


def test_school_locations_and_niche_rankings_enrich_zone_api(session, tmp_path) -> None:
    _import_test_zone(session, tmp_path)
    locations_path = tmp_path / "school_locations.geojson"
    locations_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "OBJECTID": 10,
                            "formid": "0001-01-001",
                            "gisname": "Birch Elementary",
                            "mdename": "Birch Elementary School",
                            "mdeaddr": "1 Birch Rd, Plymouth, MN 55446",
                            "graderange": "K-5",
                        },
                        "geometry": {"type": "Point", "coordinates": [-93.52, 45.51]},
                    },
                    {
                        "type": "Feature",
                        "properties": {
                            "OBJECTID": 11,
                            "gisname": "Birch High",
                            "mdename": "Birch High School",
                            "graderange": "9-12",
                        },
                        "geometry": {"type": "Point", "coordinates": [-93.62, 45.61]},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    rankings_path = tmp_path / "niche.csv"
    rankings_path.write_text(
        "rank,school_name,district_name,niche_grade,students,student_teacher_ratio\n"
        "7,Birch Elementary School,Example Public Schools,A+,525,15\n",
        encoding="utf-8",
    )
    us_news_path = tmp_path / "us_news.csv"
    us_news_path.write_text(
        "rank,school_name,district_name,source_url\n"
        "2,Birch Elementary School,Example Public Schools,https://www.usnews.com/education/k12/elementary-schools/minnesota\n",
        encoding="utf-8",
    )

    assert import_school_locations(session, locations_path) == 1
    assert import_niche_rankings(session, rankings_path, school_year="2026") == 1
    assert import_us_news_rankings(session, us_news_path, school_year="2026") == 1
    session.flush()

    collection = school_locations_geojson(session)
    assert len(collection["features"]) == 1
    assert collection["features"][0]["properties"]["niche_rank"] == 7
    assert collection["features"][0]["properties"]["us_news_rank"] == 2
    assert [
        profile["source_name"]
        for profile in collection["features"][0]["properties"]["academic_profiles"]
    ] == ["Niche", "U.S. News & World Report"]
    lookup = handle_api_request(
        session,
        "POST",
        "/api/school-zones/identify",
        {"lat": 45.5, "lon": -93.5},
    )
    assert lookup.status == 200
    assert lookup.payload["school_location"]["address"] == "1 Birch Rd, Plymouth, MN 55446"
    assert lookup.payload["niche_rank"] == 7
    assert lookup.payload["us_news_rank"] == 2
    school_layer = handle_api_request(session, "GET", "/api/school-locations")
    assert school_layer.status == 200
    assert school_layer.payload["features"][0]["properties"]["niche_grade"] == "A+"
    assert school_layer.payload["features"][0]["properties"]["us_news_rank"] == 2


def test_niche_html_import_parser(session, tmp_path) -> None:
    path = tmp_path / "niche.html"
    path.write_text(
        """
        <html><body>
        <a>#3 Best Public Elementary Schools in Minnesota Plymouth Creek Elementary School Wayzata Public Schools, MN K-5 grade A+ Overall Niche Grade Students 678 Student-Teacher Ratio 15 to 1</a>
        </body></html>
        """,
        encoding="utf-8",
    )

    count = import_niche_rankings(session, path, school_year="2026")
    session.flush()

    assert count == 1
    profile = session.execute(select(SchoolAcademicProfile)).scalars().one()
    assert profile.school_name == "Plymouth Creek Elementary School"
    assert profile.district_name == "Wayzata Public Schools"
    assert profile.state_rank == 3
    assert profile.rating_label == "A+"


def test_us_news_html_import_parser(session, tmp_path) -> None:
    path = tmp_path / "us_news.html"
    path.write_text(
        """
        <html><body>
        The top 25 public elementary schools in Minnesota are:
        1. Gate 4/5 \u2013 Stillwater Area Public School Dist.
        2. Scenic Heights Elementary \u2013 Minnetonka Public School District
        </body></html>
        """,
        encoding="utf-8",
    )

    count = import_us_news_rankings(session, path, school_year="2026")
    session.flush()

    assert count == 2
    profiles = session.execute(
        select(SchoolAcademicProfile).order_by(SchoolAcademicProfile.state_rank)
    ).scalars().all()
    assert profiles[0].school_name == "Gate 4/5"
    assert profiles[0].district_name == "Stillwater Area Public School Dist."
    assert profiles[0].state_rank == 1
    assert profiles[1].school_name == "Scenic Heights Elementary"
    assert profiles[1].source_name == "U.S. News & World Report"


def test_us_news_page_context_parser_and_not_ranked_status(session, tmp_path) -> None:
    _import_test_zone(session, tmp_path)
    path = tmp_path / "us_news_context.html"
    page_context = {
        "src/containers/pages/education/k-12/search/index.js": {
            "data": {
                "items": [
                    {
                        "name": "Other Elementary",
                        "school": {
                            "district": "Other District",
                            "profile_url": "https://www.usnews.com/education/k12/minnesota/other-000001",
                        },
                        "student_pop": 410,
                        "math_prof": 81,
                        "reading_prof": 78,
                        "data": [{"label": "Student-Teacher Ratio", "raw_value": "14:1"}],
                        "ranks": [
                            {
                                "value": "250",
                                "label": "Minnesota Elementary Schools",
                                "is_ranked": True,
                            }
                        ],
                    }
                ]
            }
        }
    }
    path.write_text(
        "<script>window['__PAGE_CONTEXT_QUERY_STATE__'] = "
        + json.dumps(page_context)
        + ";</script>",
        encoding="utf-8",
    )

    assert import_us_news_rankings(session, path, school_year="2026") == 1
    session.flush()

    profile = session.execute(select(SchoolAcademicProfile)).scalars().one()
    assert profile.school_name == "Other Elementary"
    assert profile.state_rank == 250
    assert profile.math_proficiency == 81
    assert profile.reading_proficiency == 78
    assert profile.student_teacher_ratio == 14

    lookup = handle_api_request(
        session,
        "POST",
        "/api/school-zones/identify",
        {"lat": 45.5, "lon": -93.5},
    )
    assert lookup.status == 200
    statuses = {row["source_name"]: row for row in lookup.payload["ranking_statuses"]}
    assert statuses["U.S. News & World Report"]["status"] == "not_ranked"
    assert statuses["U.S. News & World Report"]["display_label"] == "Not ranked in imported top 250"


def test_map_highlight_api_and_property_context_for_liked_and_avoid_streets(session) -> None:
    listing = _add_listing(session, "100 Pocket Ave", -93.50, 45.50)
    response = handle_api_request(
        session,
        "POST",
        "/api/map-highlights",
        {
            "name": "Liked curve",
            "highlight_type": "liked_street",
            "sentiment": "like",
            "tags": ["quiet_street"],
            "geometry": {
                "type": "LineString",
                "coordinates": [[-93.505, 45.495], [-93.495, 45.505]],
            },
        },
    )
    avoid = create_map_highlight(
        session,
        name="Busy road",
        highlight_type="avoid_area",
        sentiment="avoid",
        tags=["road_noise"],
        geometry=_square(-93.9, 45.1, -93.7, 45.2),
    )
    session.flush()

    assert response.status == 201
    rows = property_highlight_context(session, listing.property_id, near_miles=0.2)
    assert rows[0]["name"] == "Liked curve"
    assert rows[0]["relation"] == "near_street"
    assert all(row["id"] != avoid.id for row in rows)
    layer = handle_api_request(session, "GET", "/api/map-highlights")
    assert layer.status == 200
    assert len(layer.payload["features"]) == 2


def test_parks_trails_layer_import_geojson_and_api(session, tmp_path) -> None:
    path = tmp_path / "parks.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "name": "Pocket Park",
                            "category": "park",
                            "source_name": "OpenStreetMap",
                            "source_key": "node:1",
                        },
                        "geometry": {"type": "Point", "coordinates": [-93.51, 45.51]},
                    },
                    {
                        "type": "Feature",
                        "properties": {
                            "name": "Trail Spur",
                            "category": "trail",
                            "source_name": "OpenStreetMap",
                            "source_key": "way:2",
                        },
                        "geometry": {"type": "Point", "coordinates": [-93.49, 45.49]},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    count = import_parks_trails_playgrounds(session, path)
    session.flush()

    assert count == 2
    assert session.execute(select(MapFeature)).scalars().first().layer_type == "parks_trails_playgrounds"
    collection = parks_trails_playgrounds_geojson(session)
    assert len(collection["features"]) == 2
    api_response = handle_api_request(session, "GET", "/api/parks-trails-playgrounds")
    assert api_response.status == 200
    assert api_response.payload["features"][0]["properties"]["source_name"] == "OpenStreetMap"


def test_saved_neighborhood_fit_scoring_uses_tags_and_nearby_amenities(session, tmp_path) -> None:
    _import_test_zone(session, tmp_path)
    parks_path = tmp_path / "parks.geojson"
    parks_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "name": "Pocket Playground",
                            "category": "playground",
                            "source_name": "OpenStreetMap",
                            "source_key": "node:play",
                        },
                        "geometry": {"type": "Point", "coordinates": [-93.5, 45.5]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    import_parks_trails_playgrounds(session, parks_path)
    neighborhood = create_saved_neighborhood(
        session,
        name="Scored pocket",
        geometry=_square(-93.8, 45.2, -93.2, 45.8),
        rating="favorite",
        tags=["quiet_street", "parks", "playgrounds"],
    )
    session.flush()

    score = score_saved_neighborhood(session, neighborhood, persist=True)
    session.flush()

    assert score["overall_score"] > 70
    assert score["amenity_score"] > 50
    assert score["nearby_amenities"][0]["name"] == "Pocket Playground"
    assert session.execute(select(SavedNeighborhoodScore)).scalars().one().overall_score == score["overall_score"]


def _import_test_zone(session, tmp_path) -> None:
    path = tmp_path / "attendance_2026.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "OBJECTID": 1,
                            "ELEM_NAME": "Birch Elementary",
                            "SDPREFNAME": "Example Public Schools",
                        },
                        "geometry": _square(-94.0, 45.0, -92.9, 46.0),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    import_attendance_zones(session, path, school_year="2026")
    session.flush()


def _add_listing(session, address: str, lon: float, lat: float) -> Listing:
    prop = Property(
        normalized_address=address.upper(),
        address_line1=address,
        city="Plymouth",
        state="MN",
        zip="55446",
        latitude=lat,
        longitude=lon,
    )
    listing = Listing(
        property=prop,
        source="manual",
        list_price=650000,
        beds=4,
        baths=3,
        finished_sqft=2600,
        lot_size_sqft=10000,
        property_type="single_family",
    )
    session.add(listing)
    session.flush()
    session.add(Favorite(listing=listing, user_rating="like"))
    return listing


def _square(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [min_lon, min_lat],
                [max_lon, min_lat],
                [max_lon, max_lat],
                [min_lon, max_lat],
                [min_lon, min_lat],
            ]
        ],
    }
