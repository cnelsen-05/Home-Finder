from __future__ import annotations

import re
from typing import Any

import httpx

from realestate.models import Property
from realestate.sources.base import AdapterResult
from realestate.sources.public_records.base import PublicRecordAdapter


class MinneapolisOpenDataAdapter(PublicRecordAdapter):
    source_name = "Minneapolis Open Data"
    record_type = "municipal_permits"
    service_url = (
        "https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/"
        "CCS_Permits/FeatureServer/0/query"
    )

    def lookup_property(self, prop: Property) -> AdapterResult:
        if (prop.city or "").strip().lower() != "minneapolis":
            return AdapterResult(
                source_name=self.source_name,
                record_type=self.record_type,
                status="skipped",
                confidence="low",
                notes="Property is not identified as Minneapolis.",
            )
        parsed_address = _parse_address_for_query(prop.address_line1)
        if parsed_address is None:
            return AdapterResult(
                source_name=self.source_name,
                source_url=self.service_url,
                record_type=self.record_type,
                status="skipped",
                confidence="low",
                notes="Address could not be parsed for Minneapolis permit lookup.",
            )
        number, street_term = parsed_address
        where = f"Display LIKE '%{number}%' AND UPPER(Display) LIKE '%{street_term}%'"
        params: dict[str, Any] = {
            "f": "json",
            "where": where,
            "outFields": (
                "Display,APN,permitNumber,permitType,workType,status,milestone,value,"
                "comments,issueDate,completeDate"
            ),
            "returnGeometry": "false",
            "resultRecordCount": "25",
            "orderByFields": "issueDate DESC",
        }
        try:
            response = httpx.get(self.service_url, params=params, timeout=20)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return AdapterResult(
                source_name=self.source_name,
                source_url=self.service_url,
                record_type=self.record_type,
                status="error",
                confidence="low",
                parsed={"address": prop.address_line1},
                notes=f"Minneapolis permit lookup failed: {exc}",
            )
        return result_from_permit_payload(payload, self.service_url)


def result_from_permit_payload(payload: dict[str, Any], source_url: str) -> AdapterResult:
    features = payload.get("features", [])
    permits = []
    for feature in features:
        attrs = feature.get("attributes") or {}
        permits.append(
            {
                "address": attrs.get("Display"),
                "apn": attrs.get("APN"),
                "permit_number": attrs.get("permitNumber"),
                "permit_type": attrs.get("permitType"),
                "work_type": attrs.get("workType"),
                "status": attrs.get("status"),
                "milestone": attrs.get("milestone"),
                "value": attrs.get("value"),
                "comments": attrs.get("comments"),
                "issue_date": attrs.get("issueDate"),
                "complete_date": attrs.get("completeDate"),
            }
        )
    return AdapterResult(
        source_name=MinneapolisOpenDataAdapter.source_name,
        source_url=source_url,
        record_type=MinneapolisOpenDataAdapter.record_type,
        status="found" if permits else "not_found",
        confidence="medium" if permits else "low",
        parsed={
            "permit_count": len(permits),
            "recent_permits": permits[:10],
        },
        raw=payload,
        notes=None if permits else "No Minneapolis CCS permits matched the parsed address query.",
    )


def _parse_address_for_query(address: str | None) -> tuple[str, str] | None:
    if not address:
        return None
    match = re.match(r"^\s*(\d+)\s+(.+?)\s*$", address)
    if not match:
        return None
    number = match.group(1)
    street = re.sub(
        r"\b(CIR|CIRCLE|LN|LANE|RD|ROAD|DR|DRIVE|AVE|AVENUE|ST|STREET|TER|TERRACE|CT|COURT|PKWY|PARKWAY|N|S|E|W|NE|NW|SE|SW)\b",
        "",
        match.group(2),
        flags=re.I,
    )
    street = re.sub(r"[^A-Za-z0-9 ]+", " ", street).upper()
    street = re.sub(r"\s+", " ", street).strip()
    if not street:
        return None
    return number, street.split()[0]
