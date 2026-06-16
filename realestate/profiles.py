from __future__ import annotations

import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from realestate.config import load_environment
from realestate.geospatial import json_dumps, json_loads
from realestate.models import (
    Household,
    HouseholdProfile,
    ProfileHomeFeedback,
    ProfileNeighborhoodFeedback,
)

DEFAULT_PROFILE_COLORS = ["#27615a", "#2f5f92", "#9a6a16", "#6d5a8d"]


def configured_household_name() -> str:
    load_environment()
    return (os.getenv("HOMEANALYZE_HOUSEHOLD_NAME") or "Home Search").strip() or "Home Search"


def configured_profile_names() -> list[str]:
    load_environment()
    raw = os.getenv("HOMEANALYZE_PROFILE_NAMES") or ""
    names = [name.strip() for name in raw.split(",") if name.strip()]
    return names or ["Me", "Partner"]


def ensure_household_profiles(
    session: Session,
    *,
    household_name: str | None = None,
    profile_names: list[str] | None = None,
) -> Household:
    name = (household_name or configured_household_name()).strip() or "Home Search"
    household = session.execute(select(Household).where(Household.name == name)).scalar_one_or_none()
    if household is None:
        household = Household(name=name)
        session.add(household)
        session.flush()

    existing = {
        profile.display_name.lower(): profile
        for profile in session.execute(
            select(HouseholdProfile).where(HouseholdProfile.household_id == household.id)
        ).scalars()
    }
    if profile_names is not None:
        names = profile_names
    elif not existing:
        names = configured_profile_names()
    else:
        names = []
    for index, display_name in enumerate(names):
        clean_name = display_name.strip()
        if not clean_name or clean_name.lower() in existing:
            continue
        profile = HouseholdProfile(
            household_id=household.id,
            display_name=clean_name,
            role="owner" if not existing and index == 0 else "member",
            color=DEFAULT_PROFILE_COLORS[len(existing) % len(DEFAULT_PROFILE_COLORS)],
            is_default=not existing,
        )
        session.add(profile)
        existing[clean_name.lower()] = profile
    session.flush()
    return household


def profiles_payload(
    session: Session,
    selected_profile_id: int | None = None,
    household_name: str | None = None,
) -> dict[str, Any]:
    household = ensure_household_profiles(session, household_name=household_name)
    profiles = session.execute(
        select(HouseholdProfile)
        .where(HouseholdProfile.household_id == household.id)
        .order_by(HouseholdProfile.is_default.desc(), HouseholdProfile.display_name)
    ).scalars().all()
    selected = resolve_profile(session, selected_profile_id) if selected_profile_id else None
    if selected is None and profiles:
        selected = profiles[0]
    return {
        "household": {
            "id": household.id,
            "name": household.name,
        },
        "profiles": [_profile_payload(profile) for profile in profiles],
        "current_profile": _profile_payload(selected) if selected else None,
    }


def create_profile(
    session: Session,
    *,
    display_name: str,
    household_name: str | None = None,
    color: str | None = None,
    auth_email: str | None = None,
) -> HouseholdProfile:
    household = ensure_household_profiles(session, household_name=household_name)
    clean_name = display_name.strip()
    if not clean_name:
        raise ValueError("Profile display name is required.")
    existing = session.execute(
        select(HouseholdProfile).where(
            HouseholdProfile.household_id == household.id,
            HouseholdProfile.display_name == clean_name,
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    profile_count = session.execute(
        select(HouseholdProfile).where(HouseholdProfile.household_id == household.id)
    ).scalars().all()
    profile = HouseholdProfile(
        household_id=household.id,
        display_name=clean_name,
        role="member",
        color=color or DEFAULT_PROFILE_COLORS[len(profile_count) % len(DEFAULT_PROFILE_COLORS)],
        auth_email=auth_email,
    )
    session.add(profile)
    session.flush()
    return profile


def resolve_profile(session: Session, profile_id: int | None) -> HouseholdProfile | None:
    if profile_id is None:
        return None
    return session.get(HouseholdProfile, profile_id)


def home_feedback_for_listing(
    session: Session,
    listing_id: int,
    profile_id: int | None,
) -> ProfileHomeFeedback | None:
    if profile_id is None:
        return None
    return session.execute(
        select(ProfileHomeFeedback).where(
            ProfileHomeFeedback.profile_id == profile_id,
            ProfileHomeFeedback.listing_id == listing_id,
        )
    ).scalar_one_or_none()


def all_home_feedback_for_listing(session: Session, listing_id: int) -> list[dict[str, Any]]:
    rows = session.execute(
        select(ProfileHomeFeedback, HouseholdProfile)
        .join(HouseholdProfile, HouseholdProfile.id == ProfileHomeFeedback.profile_id)
        .where(ProfileHomeFeedback.listing_id == listing_id)
        .order_by(HouseholdProfile.display_name)
    ).all()
    return [
        {
            "profile": _profile_payload(profile),
            "rating": feedback.rating,
            "notes": feedback.notes,
            "updated_at": feedback.updated_at.isoformat() if feedback.updated_at else None,
        }
        for feedback, profile in rows
    ]


def upsert_home_feedback(
    session: Session,
    *,
    profile_id: int,
    listing_id: int,
    rating: str | None = None,
    notes: str | None = None,
) -> ProfileHomeFeedback:
    feedback = home_feedback_for_listing(session, listing_id, profile_id)
    if feedback is None:
        feedback = ProfileHomeFeedback(profile_id=profile_id, listing_id=listing_id)
        session.add(feedback)
    if rating is not None:
        feedback.rating = "rejected" if rating == "reject" else rating
    if notes is not None:
        feedback.notes = notes
    session.flush()
    return feedback


def neighborhood_feedback_for_area(
    session: Session,
    neighborhood_id: int,
    profile_id: int | None,
) -> ProfileNeighborhoodFeedback | None:
    if profile_id is None:
        return None
    return session.execute(
        select(ProfileNeighborhoodFeedback).where(
            ProfileNeighborhoodFeedback.profile_id == profile_id,
            ProfileNeighborhoodFeedback.saved_neighborhood_id == neighborhood_id,
        )
    ).scalar_one_or_none()


def all_neighborhood_feedback_for_area(session: Session, neighborhood_id: int) -> list[dict[str, Any]]:
    rows = session.execute(
        select(ProfileNeighborhoodFeedback, HouseholdProfile)
        .join(HouseholdProfile, HouseholdProfile.id == ProfileNeighborhoodFeedback.profile_id)
        .where(ProfileNeighborhoodFeedback.saved_neighborhood_id == neighborhood_id)
        .order_by(HouseholdProfile.display_name)
    ).all()
    return [
        {
            "profile": _profile_payload(profile),
            "rating": feedback.rating,
            "notes": feedback.notes,
            "tags": json_loads(feedback.tags_json, []),
            "updated_at": feedback.updated_at.isoformat() if feedback.updated_at else None,
        }
        for feedback, profile in rows
    ]


def upsert_neighborhood_feedback(
    session: Session,
    *,
    profile_id: int,
    neighborhood_id: int,
    rating: str | None = None,
    notes: str | None = None,
    tags: list[str] | None = None,
) -> ProfileNeighborhoodFeedback:
    feedback = neighborhood_feedback_for_area(session, neighborhood_id, profile_id)
    if feedback is None:
        feedback = ProfileNeighborhoodFeedback(
            profile_id=profile_id,
            saved_neighborhood_id=neighborhood_id,
        )
        session.add(feedback)
    if rating is not None:
        feedback.rating = rating
    if notes is not None:
        feedback.notes = notes
    if tags is not None:
        feedback.tags_json = json_dumps(tags)
    session.flush()
    return feedback


def _profile_payload(profile: HouseholdProfile | None) -> dict[str, Any] | None:
    if profile is None:
        return None
    return {
        "id": profile.id,
        "household_id": profile.household_id,
        "display_name": profile.display_name,
        "role": profile.role,
        "color": profile.color,
        "is_default": profile.is_default,
        "auth_email": profile.auth_email,
    }
