from __future__ import annotations

import json

from sqlalchemy.orm import Session

from realestate.models import Listing, ParcelRecord, Property, SaleHistoryRecord, TaxRecord
from realestate.sources.public_records.base import store_adapter_results
from realestate.sources.public_records.hennepin_gis import HennepinGISAdapter


def enrich_parcel_context(session: Session, prop: Property):
    adapter = HennepinGISAdapter()
    result = adapter.lookup_property(prop)
    records = store_adapter_results(session, prop, [result])
    if result.status == "found":
        _upsert_parcel_record_from_hennepin_result(session, prop, result.parsed)
    return records


def _upsert_parcel_record_from_hennepin_result(
    session: Session, prop: Property, parsed: dict
) -> None:
    feature = parsed.get("first_feature") or {}
    attributes = feature.get("attributes") or {}
    parcel_id = (
        attributes.get("PIN")
        or attributes.get("COUNTY_PIN")
        or attributes.get("STATE_PIN")
        or attributes.get("SERIAL")
        or attributes.get("ACCOUNT")
    )
    acres = attributes.get("ACRES_POLY") or attributes.get("ACRES_DEED") or attributes.get("ACRES")
    if not parcel_id and acres is None:
        return
    prop.parcel_id = parcel_id or prop.parcel_id
    prop.county = attributes.get("CO_NAME") or attributes.get("_matched_county_layer") or prop.county
    existing = None
    if parcel_id:
        existing = (
            session.query(ParcelRecord)
            .filter(ParcelRecord.property_id == prop.id, ParcelRecord.parcel_id == parcel_id)
            .one_or_none()
        )
    parcel = existing or ParcelRecord(property_id=prop.id, parcel_id=parcel_id)
    parcel.lot_size_sqft = float(acres) * 43560 if acres is not None else None
    parcel.land_use = attributes.get("USECLASS1")
    parcel.municipality = attributes.get("CTU_NAME") or attributes.get("POSTCOMM")
    parcel.source_name = "MetroGIS Regional Parcels"
    parcel.geometry_geojson = json.dumps(feature.get("geometry")) if feature.get("geometry") else None
    session.add(parcel)
    _upsert_tax_record(session, prop, attributes)
    _upsert_sale_history_record(session, prop, attributes)
    _hydrate_listings_from_public_record(session, prop, attributes, parcel.lot_size_sqft)


def _upsert_tax_record(session: Session, prop: Property, attributes: dict) -> None:
    if attributes.get("TOTAL_TAX") is None and attributes.get("EMV_TOTAL") is None:
        return
    tax_year = attributes.get("TAX_YEAR")
    existing = None
    if tax_year is not None:
        existing = (
            session.query(TaxRecord)
            .filter(TaxRecord.property_id == prop.id, TaxRecord.tax_year == tax_year)
            .one_or_none()
        )
    tax = existing or TaxRecord(property_id=prop.id, tax_year=tax_year)
    tax.annual_tax = attributes.get("TOTAL_TAX")
    tax.assessed_market_value = attributes.get("EMV_TOTAL")
    tax.homestead_status = attributes.get("HOMESTEAD")
    tax.source_name = "MetroGIS Regional Parcels"
    session.add(tax)


def _upsert_sale_history_record(session: Session, prop: Property, attributes: dict) -> None:
    sale_value = attributes.get("SALE_VALUE")
    sale_date = attributes.get("SALE_DATE")
    if sale_value is None and sale_date is None:
        return
    if sale_value is not None and _positive_number(sale_value) is None:
        return
    sale_date_text = _arcgis_epoch_to_date(sale_date)
    existing = (
        session.query(SaleHistoryRecord)
        .filter(
            SaleHistoryRecord.property_id == prop.id,
            SaleHistoryRecord.sale_date == sale_date_text,
            SaleHistoryRecord.sale_price == sale_value,
        )
        .one_or_none()
    )
    sale = existing or SaleHistoryRecord(property_id=prop.id)
    sale.sale_date = sale_date_text
    sale.sale_price = sale_value
    sale.transaction_type = "parcel_reported_sale"
    sale.source_name = "MetroGIS Regional Parcels"
    sale.confidence = "medium"
    session.add(sale)


def _hydrate_listings_from_public_record(
    session: Session, prop: Property, attributes: dict, lot_size_sqft: float | None
) -> None:
    listings = session.query(Listing).filter(Listing.property_id == prop.id).all()
    for listing in listings:
        public_finished_sqft = _positive_number(attributes.get("FIN_SQ_FT"))
        if listing.finished_sqft is None and public_finished_sqft is not None:
            listing.finished_sqft = public_finished_sqft
        if listing.year_built is None and attributes.get("YEAR_BUILT") is not None:
            listing.year_built = attributes.get("YEAR_BUILT")
        if listing.annual_taxes is None and attributes.get("TOTAL_TAX") is not None:
            listing.annual_taxes = attributes.get("TOTAL_TAX")
        if listing.lot_size_sqft is None and lot_size_sqft is not None:
            listing.lot_size_sqft = lot_size_sqft
        if listing.school_district is None and attributes.get("SCHOOL_DST") is not None:
            listing.school_district = attributes.get("SCHOOL_DST")
        if listing.property_type is None:
            property_type = _normalize_dwelling_type(
                attributes.get("DWELL_TYPE") or attributes.get("USECLASS1") or ""
            )
            listing.property_type = property_type or listing.property_type


def _normalize_dwelling_type(value: str) -> str | None:
    text = value.strip().lower()
    if not text:
        return None
    if "single" in text or "res 1 unit" in text or "1 unit" in text:
        return "single_family"
    if text == "residential":
        return None
    return text.replace(" ", "_")


def _positive_number(value) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _arcgis_epoch_to_date(value) -> str | None:
    if value is None:
        return None
    try:
        from datetime import UTC, datetime

        return datetime.fromtimestamp(float(value) / 1000, UTC).date().isoformat()
    except (OSError, TypeError, ValueError):
        return str(value)
