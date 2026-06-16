from __future__ import annotations

from realestate.input_batch import parse_home_input_line, parse_pasted_home_inputs


def test_parse_pasted_full_address() -> None:
    parsed = parse_home_input_line("1570 Creek Run Trl, Excelsior, MN 55331")

    assert not hasattr(parsed, "message")
    assert parsed.row["address"] == "1570 Creek Run Trl"
    assert parsed.row["city"] == "Excelsior"
    assert parsed.row["state"] == "MN"
    assert parsed.row["zip"] == "55331"
    assert parsed.row["source"] == "manual_address_list"


def test_parse_address_bearing_listing_url_slug() -> None:
    parsed = parse_home_input_line(
        "https://results.net/real-estate/1831-koehnen-circle-excelsior-mn-55331/769251436/"
    )

    assert not hasattr(parsed, "message")
    assert parsed.row["address"] == "1831 Koehnen Circle"
    assert parsed.row["city"] == "Excelsior"
    assert parsed.row["state"] == "MN"
    assert parsed.row["zip"] == "55331"
    assert parsed.row["url"].startswith("https://results.net/")
    assert parsed.row["source"] == "pasted_listing_link"


def test_parse_url_with_typed_address_prefers_typed_address() -> None:
    parsed = parse_home_input_line(
        "https://example.invalid/listing 4167 Hallgren Ln, Excelsior, MN 55331"
    )

    assert not hasattr(parsed, "message")
    assert parsed.row["address"] == "4167 Hallgren Ln"
    assert parsed.row["city"] == "Excelsior"


def test_parse_copied_markdown_location_links_as_address_text() -> None:
    parsed = parse_home_input_line(
        "3690 Yuma Ln N [Plymouth, MN](https://www.homes.com/plymouth-mn/) "
        "[55446](https://www.homes.com/minneapolis-mn/55446/)"
    )

    assert not hasattr(parsed, "message")
    assert parsed.row["address"] == "3690 Yuma Ln N"
    assert parsed.row["city"] == "Plymouth"
    assert parsed.row["state"] == "MN"
    assert parsed.row["zip"] == "55446"
    assert parsed.row["url"] == ""
    assert parsed.row["source"] == "manual_address_list"


def test_invalid_link_without_address_reports_parse_error() -> None:
    parsed = parse_home_input_line("https://example.invalid/listing/abc123")

    assert hasattr(parsed, "message")
    assert "Could not parse" in parsed.message


def test_multiline_batch_keeps_valid_rows_and_errors() -> None:
    batch = parse_pasted_home_inputs(
        """
        1570 Creek Run Trl, Excelsior, MN 55331
        https://example.invalid/listing/abc123
        4167 Hallgren Ln Excelsior MN 55331
        """
    )

    assert len(batch.rows) == 2
    assert len(batch.errors) == 1
    assert batch.rows[1].row["address"] == "4167 Hallgren Ln"


def test_user_sample_addresses_parse_without_errors() -> None:
    batch = parse_pasted_home_inputs(
        """
        18005 45th Ave N, Plymouth, MN 55446
        4085 Everest Ln N, Plymouth, MN 55446
        3690 Yuma Ln N [Plymouth, MN](https://www.homes.com/plymouth-mn/) [55446](https://www.homes.com/minneapolis-mn/55446/)
        """
    )

    assert len(batch.rows) == 3
    assert not batch.errors
    assert [row.row["address"] for row in batch.rows] == [
        "18005 45th Ave N",
        "4085 Everest Ln N",
        "3690 Yuma Ln N",
    ]
