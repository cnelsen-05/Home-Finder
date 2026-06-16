from __future__ import annotations

from realestate.models import Property
from realestate.sources.listing_discovery import address_search_queries, classify_listing_url
from realestate.sources.public_records.fema_flood import result_from_nfhl_payload
from realestate.sources.public_records.minneapolis_open_data import result_from_permit_payload
from realestate.sources.public_records.mn_school_district import result_from_school_payload
from realestate.sources.public_records.mndot_traffic import result_from_traffic_payload
from realestate.sources.public_records.mpca_environment import result_from_mpca_payload
from realestate.sources.public_records.osm import result_from_overpass_payload


def test_fema_nfhl_payload_extracts_zone() -> None:
    result = result_from_nfhl_payload(
        {
            "features": [
                {
                    "attributes": {
                        "FLD_ZONE": "X",
                        "ZONE_SUBTY": "AREA OF MINIMAL FLOOD HAZARD",
                        "SFHA_TF": "F",
                        "DFIRM_ID": "27053C",
                    }
                }
            ]
        },
        "https://example.invalid/fema",
    )

    assert result.status == "found"
    assert result.confidence == "high"
    assert result.parsed["flood_zone"] == "X"


def test_school_boundary_payload_extracts_district() -> None:
    result = result_from_school_payload(
        {
            "features": [
                {
                    "attributes": {
                        "prefname": "Eden Prairie Public School District",
                        "sdnumber": "0272",
                        "formid": "0272-01",
                        "web_url": "https://www.edenpr.org/",
                    }
                }
            ]
        },
        "https://example.invalid/schools",
    )

    assert result.status == "found"
    assert result.parsed["district_name"] == "Eden Prairie Public School District"
    assert result.parsed["district_number"] == "0272"


def test_mndot_traffic_payload_keeps_highest_volume() -> None:
    result = result_from_traffic_payload(
        {
            "features": [
                {"attributes": {"STREET_NAME": "Quiet Lane", "CURRENT_VOLUME": 1200}},
                {"attributes": {"STREET_NAME": "Busy Road", "CURRENT_VOLUME": 18000}},
            ]
        },
        "https://example.invalid/aadt",
        800,
    )

    assert result.status == "found"
    assert result.parsed["highest_current_volume"] == 18000
    assert result.parsed["top_segments"][0]["street_name"] == "Busy Road"


def test_mpca_payload_extracts_nearest_sites() -> None:
    prop = Property(latitude=44.839, longitude=-93.415)
    result = result_from_mpca_payload(
        {
            "features": [
                {
                    "attributes": {
                        "name": "Example Site",
                        "active_flag": "Y",
                        "activity": "Hazardous Waste",
                        "program_name": "Hazardous Waste",
                        "site_url": "https://example.invalid/site",
                        "latitude": 44.84,
                        "longitude": -93.416,
                    }
                }
            ]
        },
        "https://example.invalid/mpca",
        1609,
        prop,
    )

    assert result.status == "found"
    assert result.parsed["active_site_count"] == 1
    assert result.parsed["nearest_sites"][0]["name"] == "Example Site"


def test_osm_payload_extracts_named_amenities() -> None:
    result = result_from_overpass_payload(
        {
            "elements": [
                {
                    "type": "node",
                    "id": 1,
                    "lat": 44.84,
                    "lon": -93.41,
                    "tags": {"name": "Life Time", "leisure": "fitness_centre"},
                },
                {
                    "type": "way",
                    "id": 2,
                    "center": {"lat": 44.83, "lon": -93.40},
                    "tags": {"name": "Neighborhood Playground", "leisure": "playground"},
                },
            ]
        },
        "https://example.invalid/overpass",
        3500,
    )

    assert result.status == "found"
    assert result.parsed["amenity_count"] == 2
    assert {item["amenity_type"] for item in result.parsed["amenities"]} == {"gym", "playground"}


def test_minneapolis_permit_payload_extracts_recent_permits() -> None:
    result = result_from_permit_payload(
        {
            "features": [
                {
                    "attributes": {
                        "Display": "2404 34TH AVE S",
                        "permitNumber": "BLDG1182101",
                        "permitType": "Res",
                        "workType": "Remodel",
                        "status": "Issued",
                        "value": 113000,
                    }
                }
            ]
        },
        "https://example.invalid/permits",
    )

    assert result.status == "found"
    assert result.parsed["permit_count"] == 1
    assert result.parsed["recent_permits"][0]["permit_number"] == "BLDG1182101"


def test_listing_discovery_classifies_major_portals_as_reference_only() -> None:
    decision = classify_listing_url("https://www.zillow.com/homedetails/example")

    assert decision.status == "reference_only"
    assert "do not scrape" in decision.reason.lower()


def test_address_search_queries_start_from_exact_address() -> None:
    queries = address_search_queries("9130 Flyway Cir", "Eden Prairie")

    assert queries[0] == '"9130 Flyway Cir Eden Prairie MN"'
    assert any("property tax" in query for query in queries)
