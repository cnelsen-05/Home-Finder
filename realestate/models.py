from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Property(Base, TimestampMixin):
    __tablename__ = "properties"
    __table_args__ = (
        UniqueConstraint(
            "normalized_address", "city", "state", "zip", name="uq_property_normalized_location"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    normalized_address: Mapped[str] = mapped_column(String(255), index=True)
    address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    state: Mapped[str | None] = mapped_column(String(10), nullable=True, default="MN")
    zip: Mapped[str | None] = mapped_column(String(20), nullable=True)
    county: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    parcel_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)

    listings: Mapped[list[Listing]] = relationship(back_populates="property", cascade="all,delete")
    public_records: Mapped[list[PublicRecord]] = relationship(
        back_populates="property", cascade="all,delete"
    )


class Listing(Base, TimestampMixin):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"), index=True)
    source: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    source_listing_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    mls_number: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    listing_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    list_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_list_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    beds: Mapped[float | None] = mapped_column(Float, nullable=True)
    baths: Mapped[float | None] = mapped_column(Float, nullable=True)
    finished_sqft: Mapped[float | None] = mapped_column(Float, nullable=True)
    above_grade_sqft: Mapped[float | None] = mapped_column(Float, nullable=True)
    below_grade_sqft: Mapped[float | None] = mapped_column(Float, nullable=True)
    lot_size_sqft: Mapped[float | None] = mapped_column(Float, nullable=True)
    year_built: Mapped[int | None] = mapped_column(Integer, nullable=True)
    property_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    style: Mapped[str | None] = mapped_column(String(120), nullable=True)
    garage_spaces: Mapped[float | None] = mapped_column(Float, nullable=True)
    school_district: Mapped[str | None] = mapped_column(String(160), nullable=True)
    annual_taxes: Mapped[float | None] = mapped_column(Float, nullable=True)
    hoa_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_urls_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    virtual_tour_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow)
    raw_payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    property: Mapped[Property] = relationship(back_populates="listings")
    snapshots: Mapped[list[ListingSnapshot]] = relationship(
        back_populates="listing", cascade="all,delete", order_by="ListingSnapshot.observed_at"
    )
    favorites: Mapped[list[Favorite]] = relationship(back_populates="listing", cascade="all,delete")
    scores: Mapped[list[ReviewScore]] = relationship(
        back_populates="listing", cascade="all,delete", order_by="ReviewScore.scored_at"
    )
    issue_flags: Mapped[list[IssueFlag]] = relationship(
        back_populates="listing", cascade="all,delete"
    )


class ListingSnapshot(Base):
    __tablename__ = "listing_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    description_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    photo_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    open_house_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    listing: Mapped[Listing] = relationship(back_populates="snapshots")


class Favorite(Base):
    __tablename__ = "favorites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"), nullable=True)
    external_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_rating: Mapped[str | None] = mapped_column(String(40), nullable=True)
    user_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    imported_source: Mapped[str | None] = mapped_column(String(80), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    listing: Mapped[Listing | None] = relationship(back_populates="favorites")


class PublicRecord(Base):
    __tablename__ = "public_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"), index=True)
    source_name: Mapped[str] = mapped_column(String(160))
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    record_type: Mapped[str] = mapped_column(String(120))
    parsed_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[str] = mapped_column(String(40), default="low")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    property: Mapped[Property] = relationship(back_populates="public_records")


class TaxRecord(Base):
    __tablename__ = "tax_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"), index=True)
    tax_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    annual_tax: Mapped[float | None] = mapped_column(Float, nullable=True)
    assessed_market_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    taxable_market_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    homestead_status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SaleHistoryRecord(Base):
    __tablename__ = "sale_history_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"), index=True)
    sale_date: Mapped[str | None] = mapped_column(String(40), nullable=True)
    sale_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    transaction_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    confidence: Mapped[str] = mapped_column(String(40), default="low")


class ParcelRecord(Base):
    __tablename__ = "parcel_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"), index=True)
    parcel_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    lot_size_sqft: Mapped[float | None] = mapped_column(Float, nullable=True)
    lot_dimensions: Mapped[str | None] = mapped_column(String(120), nullable=True)
    zoning: Mapped[str | None] = mapped_column(String(120), nullable=True)
    land_use: Mapped[str | None] = mapped_column(String(120), nullable=True)
    municipality: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    geometry_geojson: Mapped[str | None] = mapped_column(Text, nullable=True)


class LifeAnchor(Base):
    __tablename__ = "life_anchors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True, default=1)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Household(Base, TimestampMixin):
    __tablename__ = "households"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    shared_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    profiles: Mapped[list[HouseholdProfile]] = relationship(
        "HouseholdProfile",
        back_populates="household",
        cascade="all,delete",
    )


class HouseholdProfile(Base, TimestampMixin):
    __tablename__ = "household_profiles"
    __table_args__ = (
        UniqueConstraint("household_id", "display_name", name="uq_household_profile_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(120), index=True)
    role: Mapped[str] = mapped_column(String(40), default="member")
    color: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    auth_email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    supabase_user_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    household: Mapped[Household] = relationship("Household", back_populates="profiles")
    home_feedback: Mapped[list[ProfileHomeFeedback]] = relationship(
        "ProfileHomeFeedback",
        back_populates="profile",
        cascade="all,delete",
    )
    neighborhood_feedback: Mapped[list[ProfileNeighborhoodFeedback]] = relationship(
        "ProfileNeighborhoodFeedback",
        back_populates="profile",
        cascade="all,delete",
    )


class ProfileHomeFeedback(Base, TimestampMixin):
    __tablename__ = "profile_home_feedback"
    __table_args__ = (
        UniqueConstraint("profile_id", "listing_id", name="uq_profile_home_feedback"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("household_profiles.id"), index=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    rating: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    profile: Mapped[HouseholdProfile] = relationship(
        "HouseholdProfile",
        back_populates="home_feedback",
    )
    listing: Mapped[Listing] = relationship("Listing")


class ProfileNeighborhoodFeedback(Base, TimestampMixin):
    __tablename__ = "profile_neighborhood_feedback"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "saved_neighborhood_id",
            name="uq_profile_neighborhood_feedback",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("household_profiles.id"), index=True)
    saved_neighborhood_id: Mapped[int] = mapped_column(
        ForeignKey("saved_neighborhoods.id"),
        index=True,
    )
    rating: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    profile: Mapped[HouseholdProfile] = relationship(
        "HouseholdProfile",
        back_populates="neighborhood_feedback",
    )
    saved_neighborhood: Mapped[SavedNeighborhood] = relationship("SavedNeighborhood")


class MapLayer(Base, TimestampMixin):
    __tablename__ = "map_layers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(180), index=True)
    layer_type: Mapped[str] = mapped_column(String(80), index=True)
    source_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    geometry_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    style_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled_by_default: Mapped[bool] = mapped_column(Boolean, default=False)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class MapFeature(Base, TimestampMixin):
    __tablename__ = "map_features"
    __table_args__ = (
        UniqueConstraint(
            "layer_type",
            "source_name",
            "source_key",
            name="uq_map_feature_source_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    layer_type: Mapped[str] = mapped_column(String(80), index=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str | None] = mapped_column(String(220), nullable=True, index=True)
    source_name: Mapped[str] = mapped_column(String(180))
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_key: Mapped[str | None] = mapped_column(String(180), nullable=True, index=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    geometry_geojson: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[str] = mapped_column(String(40), default="medium")
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class SchoolAttendanceZone(Base, TimestampMixin):
    __tablename__ = "school_attendance_zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_name: Mapped[str] = mapped_column(String(256), index=True)
    school_level: Mapped[str] = mapped_column(String(40), default="elementary", index=True)
    district_name: Mapped[str | None] = mapped_column(String(180), nullable=True, index=True)
    school_year: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    source_name: Mapped[str] = mapped_column(String(180))
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    geometry_geojson: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(40), default="medium")
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class SchoolAcademicProfile(Base, TimestampMixin):
    __tablename__ = "school_academic_profiles"
    __table_args__ = (
        UniqueConstraint(
            "source_name",
            "school_name",
            "district_name",
            "school_year",
            name="uq_school_profile_source_year",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_name: Mapped[str] = mapped_column(String(256), index=True)
    district_name: Mapped[str | None] = mapped_column(String(180), nullable=True, index=True)
    school_year: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    source_name: Mapped[str] = mapped_column(String(180), index=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    state_rank: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    rating_label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    math_proficiency: Mapped[float | None] = mapped_column(Float, nullable=True)
    reading_proficiency: Mapped[float | None] = mapped_column(Float, nullable=True)
    enrollment: Mapped[int | None] = mapped_column(Integer, nullable=True)
    student_teacher_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[str] = mapped_column(String(40), default="medium")
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class SavedNeighborhood(Base, TimestampMixin):
    __tablename__ = "saved_neighborhoods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(180), index=True)
    geometry_geojson: Mapped[str] = mapped_column(Text)
    rating: Mapped[str] = mapped_column(String(40), default="maybe", index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(80), default="user_drawn")

    property_matches: Mapped[list[PropertyNeighborhoodMatch]] = relationship(
        back_populates="saved_neighborhood",
        cascade="all,delete",
    )
    map_notes: Mapped[list[MapNote]] = relationship(
        back_populates="saved_neighborhood",
        cascade="all,delete",
    )
    scores: Mapped[list[SavedNeighborhoodScore]] = relationship(
        back_populates="saved_neighborhood",
        cascade="all,delete",
    )


class PropertyNeighborhoodMatch(Base, TimestampMixin):
    __tablename__ = "property_neighborhood_matches"
    __table_args__ = (
        UniqueConstraint(
            "property_id",
            "saved_neighborhood_id",
            "relation",
            name="uq_property_neighborhood_relation",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"), index=True)
    saved_neighborhood_id: Mapped[int] = mapped_column(
        ForeignKey("saved_neighborhoods.id"),
        index=True,
    )
    relation: Mapped[str] = mapped_column(String(40), index=True)
    distance_miles: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[str] = mapped_column(String(40), default="medium")

    property: Mapped[Property] = relationship()
    saved_neighborhood: Mapped[SavedNeighborhood] = relationship(back_populates="property_matches")


class MapNote(Base, TimestampMixin):
    __tablename__ = "map_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    geometry_geojson: Mapped[str | None] = mapped_column(Text, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    note_type: Mapped[str] = mapped_column(String(80), default="observation", index=True)
    title: Mapped[str | None] = mapped_column(String(180), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_property_id: Mapped[int | None] = mapped_column(
        ForeignKey("properties.id"),
        nullable=True,
        index=True,
    )
    related_neighborhood_id: Mapped[int | None] = mapped_column(
        ForeignKey("saved_neighborhoods.id"),
        nullable=True,
        index=True,
    )

    related_property: Mapped[Property | None] = relationship()
    saved_neighborhood: Mapped[SavedNeighborhood | None] = relationship(
        back_populates="map_notes",
    )


class SavedNeighborhoodScore(Base):
    __tablename__ = "saved_neighborhood_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    saved_neighborhood_id: Mapped[int] = mapped_column(
        ForeignKey("saved_neighborhoods.id"),
        index=True,
    )
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    overall_score: Mapped[float] = mapped_column(Float)
    user_signal_score: Mapped[float] = mapped_column(Float)
    amenity_score: Mapped[float] = mapped_column(Float)
    commute_score: Mapped[float] = mapped_column(Float)
    school_score: Mapped[float] = mapped_column(Float)
    risk_score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[str] = mapped_column(String(40), default="medium")
    explanation_json: Mapped[str] = mapped_column(Text)

    saved_neighborhood: Mapped[SavedNeighborhood] = relationship(back_populates="scores")


class MapHighlight(Base, TimestampMixin):
    __tablename__ = "map_highlights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(180), index=True)
    geometry_geojson: Mapped[str] = mapped_column(Text)
    highlight_type: Mapped[str] = mapped_column(String(60), index=True)
    sentiment: Mapped[str] = mapped_column(String(40), default="like", index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    style_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(80), default="user_drawn")
    related_property_id: Mapped[int | None] = mapped_column(
        ForeignKey("properties.id"),
        nullable=True,
        index=True,
    )
    related_neighborhood_id: Mapped[int | None] = mapped_column(
        ForeignKey("saved_neighborhoods.id"),
        nullable=True,
        index=True,
    )

    related_property: Mapped[Property | None] = relationship()
    related_neighborhood: Mapped[SavedNeighborhood | None] = relationship()


class CommuteEstimate(Base):
    __tablename__ = "commute_estimates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"), index=True)
    anchor_id: Mapped[int] = mapped_column(ForeignKey("life_anchors.id"), index=True)
    mode: Mapped[str] = mapped_column(String(40), default="drive")
    distance_miles: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_of_day: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AmenityDistance(Base):
    __tablename__ = "amenity_distances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"), index=True)
    amenity_type: Mapped[str] = mapped_column(String(80))
    amenity_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    distance_miles: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewScore(Base):
    __tablename__ = "review_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    quality_score: Mapped[float] = mapped_column(Float)
    value_score: Mapped[float] = mapped_column(Float)
    daily_life_score: Mapped[float] = mapped_column(Float)
    risk_score: Mapped[float] = mapped_column(Float)
    preference_score: Mapped[float] = mapped_column(Float)
    overall_score: Mapped[float] = mapped_column(Float)
    recommendation_bucket: Mapped[str] = mapped_column(String(80))
    explanation_json: Mapped[str] = mapped_column(Text)

    listing: Mapped[Listing] = relationship(back_populates="scores")


class IssueFlag(Base):
    __tablename__ = "issue_flags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    category: Mapped[str] = mapped_column(String(80))
    severity: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(180))
    description: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(160), nullable=True)
    confidence: Mapped[str] = mapped_column(String(40), default="medium")

    listing: Mapped[Listing] = relationship(back_populates="issue_flags")


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_type: Mapped[str] = mapped_column(String(80), index=True)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"), nullable=True)
    path: Mapped[str] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class LLMExtraction(Base):
    __tablename__ = "llm_extractions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"), nullable=True)
    provider: Mapped[str] = mapped_column(String(80))
    task: Mapped[str] = mapped_column(String(120))
    input_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_source_of_truth: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
