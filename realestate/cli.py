from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import select

from realestate.config import load_preferences
from realestate.db import HostedDatabaseNotConfigured, database_mode, init_database, session_scope
from realestate.db_transfer import (
    backup_database_to_json,
    database_status,
    migrate_sqlite_to_database,
    restore_database_from_json,
)
from realestate.enrichment.public_record_pipeline import (
    enrich_all_favorites,
    enrich_property,
    resolve_property,
)
from realestate.gui import run_gui_server
from realestate.input_batch import run_research_batch_from_text
from realestate.map_data import build_map_data_exports
from realestate.map_highlights import export_map_highlights, import_map_highlights
from realestate.map_layers import download_parks_trails_playgrounds, import_parks_trails_playgrounds
from realestate.map_server import run_map_server
from realestate.models import Favorite, Listing
from realestate.neighborhood_scoring import score_all_saved_neighborhoods, score_saved_neighborhood
from realestate.neighborhoods import (
    export_saved_neighborhoods,
    import_saved_neighborhoods,
    match_homes_to_neighborhoods,
)
from realestate.paths import EXPORTS_DIR, ensure_project_dirs
from realestate.profiles import create_profile, ensure_household_profiles, profiles_payload
from realestate.reports.render import (
    render_agent_questions,
    render_all_favorite_reviews,
    render_all_neighborhood_reports,
    render_comparison_report,
    render_daily_report,
    render_favorite_review,
    render_neighborhood_report,
    render_pilot_report,
    render_pilot_report_html,
    render_tour_checklist,
    render_weekly_report,
)
from realestate.school_zones import (
    MN_ATTENDANCE_AREAS_QUERY_URL,
    download_attendance_zones,
    identify_elementary_zone,
    import_attendance_zones,
)
from realestate.schools import (
    NICHE_MN_ELEMENTARY_RANKINGS_URL,
    SCHOOL_LOCATIONS_QUERY_URL,
    US_NEWS_MN_ELEMENTARY_RANKINGS_URL,
    download_niche_elementary_rankings,
    download_school_locations,
    download_us_news_elementary_rankings,
    enrich_school_zone_payload,
    import_niche_rankings,
    import_school_locations,
    import_us_news_rankings,
)
from realestate.scoring.overall import score_all_listings
from realestate.sources.listing_text import import_listing_text as import_listing_text_file
from realestate.sources.manual_csv import (
    import_favorites_csv,
    import_life_anchors_file,
    import_listings_csv,
)

app = typer.Typer(help="Private local real-estate decision assistant.")
map_app = typer.Typer(help="Local map workspace commands.")
school_zones_app = typer.Typer(help="Elementary attendance-zone commands.")
school_locations_app = typer.Typer(help="Elementary school location commands.")
school_rankings_app = typer.Typer(help="School academic/ranking import commands.")
neighborhoods_app = typer.Typer(help="Saved neighborhood commands.")
highlights_app = typer.Typer(help="Liked/avoided map highlight commands.")
profiles_app = typer.Typer(help="Household profile commands.")
map_data_app = typer.Typer(help="Map-data export commands.")
map_layers_app = typer.Typer(help="Optional map-layer cache/import commands.")
db_app = typer.Typer(help="Database backup and hosted migration commands.")

app.add_typer(map_app, name="map")
app.add_typer(school_zones_app, name="school-zones")
app.add_typer(school_locations_app, name="school-locations")
app.add_typer(school_rankings_app, name="school-rankings")
app.add_typer(neighborhoods_app, name="neighborhoods")
app.add_typer(highlights_app, name="highlights")
app.add_typer(profiles_app, name="profiles")
app.add_typer(map_data_app, name="map-data")
app.add_typer(map_layers_app, name="map-layers")
app.add_typer(db_app, name="db")


@app.command("init")
def init_project() -> None:
    """Create directories and initialize the SQLite database."""

    ensure_project_dirs()
    init_database()
    typer.echo("Initialized real-estate assistant workspace and SQLite schema.")
    typer.echo("Edit config/preferences.yaml, config/life_anchors.yaml, and data/imports/favorites.csv.")


@db_app.command("backup")
def db_backup(
    output: Annotated[
        Path | None,
        typer.Option("--output", help="JSON backup path. Defaults to data/exports/database_backup.json."),
    ] = None,
) -> None:
    """Export the configured database to a portable JSON backup."""

    path = output or EXPORTS_DIR / "database_backup.json"
    written = backup_database_to_json(path)
    typer.echo(f"Wrote database backup to {written}")


@db_app.command("status")
def db_status() -> None:
    """Show database mode, persistence, and table counts."""

    try:
        status = database_status()
    except HostedDatabaseNotConfigured as exc:
        database = database_mode()
        typer.echo(f"Mode: {database['mode']}")
        typer.echo(f"URL: {database['url']}")
        typer.echo(f"Hosted runtime: {database['hosted']}")
        typer.echo(f"Persistent: {database['persistent']}")
        typer.echo(f"Error: {exc}")
        raise typer.Exit(2) from exc
    database = status["database"]
    typer.echo(f"Mode: {database['mode']}")
    typer.echo(f"URL: {database['url']}")
    typer.echo(f"Hosted runtime: {database['hosted']}")
    typer.echo(f"Persistent: {database['persistent']}")
    for table, count in status["counts"].items():
        if count:
            typer.echo(f"{table}: {count}")


@db_app.command("migrate-sqlite")
def db_migrate_sqlite(
    sqlite_path: Annotated[
        Path,
        typer.Option("--sqlite-path", help="Local SQLite DB to clone into the configured hosted DB."),
    ] = Path("data/realestate.db"),
    replace: Annotated[
        bool,
        typer.Option("--replace/--append", help="Delete hosted rows before inserting SQLite rows."),
    ] = False,
) -> None:
    """Clone local SQLite rows into DATABASE_URL/POSTGRES_URL."""

    if not sqlite_path.exists():
        raise typer.BadParameter(f"SQLite database not found: {sqlite_path}")
    counts = migrate_sqlite_to_database(sqlite_path, replace=replace)
    total = sum(counts.values())
    typer.echo(f"Migrated {total} rows across {len(counts)} tables.")
    for table, count in counts.items():
        if count:
            typer.echo(f"{table}: {count}")


@db_app.command("restore")
def db_restore(
    input_path: Annotated[
        Path,
        typer.Option("--input", help="Portable JSON backup created by `realestate db backup`."),
    ],
    replace: Annotated[
        bool,
        typer.Option("--replace/--append", help="Delete destination rows before restoring backup rows."),
    ] = False,
) -> None:
    """Restore a portable JSON backup into the configured database."""

    if not input_path.exists():
        raise typer.BadParameter(f"Backup not found: {input_path}")
    counts = restore_database_from_json(input_path, replace=replace)
    total = sum(counts.values())
    typer.echo(f"Restored {total} rows across {len(counts)} tables.")
    for table, count in counts.items():
        if count:
            typer.echo(f"{table}: {count}")


@app.command("import-favorites")
def import_favorites(path: Annotated[Path, typer.Argument(help="CSV of favorited homes.")]) -> None:
    with session_scope() as session:
        favorites = import_favorites_csv(path, session)
        typer.echo(f"Imported or updated {len(favorites)} favorites.")


@app.command("import-listing-text")
def import_listing_text(path: Annotated[Path, typer.Argument(help="User-provided listing text file.")]) -> None:
    with session_scope() as session:
        favorite = import_listing_text_file(path, session)
        listing_id = favorite.listing_id if hasattr(favorite, "listing_id") else favorite.id
        typer.echo(f"Imported listing text as favorite/listing {listing_id}.")


@app.command("import-life-anchors")
def import_life_anchors(
    path: Annotated[Path, typer.Argument(help="YAML or CSV life anchors file.")],
    replace: Annotated[
        bool,
        typer.Option("--replace", help="Replace existing life anchors before importing."),
    ] = False,
) -> None:
    with session_scope() as session:
        anchors = import_life_anchors_file(path, session, replace=replace)
        typer.echo(f"Imported or updated {len(anchors)} life anchors.")


@app.command("import-listings")
def import_listings(path: Annotated[Path, typer.Argument(help="Manual listing CSV export.")]) -> None:
    with session_scope() as session:
        listings = import_listings_csv(path, session)
        typer.echo(f"Imported or updated {len(listings)} listings.")


@profiles_app.command("init")
def profiles_init(
    household_name: Annotated[
        str,
        typer.Option("--household-name", help="Shared household/workspace name."),
    ] = "Home Search",
    profile: Annotated[
        list[str] | None,
        typer.Option("--profile", help="Profile display name. Repeat for each household member."),
    ] = None,
) -> None:
    """Create the shared household and app-level profiles."""

    with session_scope() as session:
        household = ensure_household_profiles(
            session,
            household_name=household_name,
            profile_names=profile or None,
        )
        payload = profiles_payload(session, household_name=household_name)
        typer.echo(f"Household: {household.name}")
        for item in payload["profiles"]:
            default = " (default)" if item.get("is_default") else ""
            typer.echo(f"{item['id']}: {item['display_name']}{default}")


@profiles_app.command("add")
def profiles_add(
    display_name: Annotated[str, typer.Argument(help="Profile display name.")],
    color: Annotated[str | None, typer.Option("--color", help="Optional profile color hex.")] = None,
    auth_email: Annotated[
        str | None,
        typer.Option("--auth-email", help="Optional future Supabase Auth email mapping."),
    ] = None,
) -> None:
    """Add a profile to the shared household."""

    with session_scope() as session:
        profile = create_profile(
            session,
            display_name=display_name,
            color=color,
            auth_email=auth_email,
        )
        typer.echo(f"{profile.id}: {profile.display_name}")


@profiles_app.command("list")
def profiles_list() -> None:
    """List household profiles."""

    with session_scope() as session:
        payload = profiles_payload(session)
        typer.echo(f"Household: {payload['household']['name']}")
        for item in payload["profiles"]:
            default = " (default)" if item.get("is_default") else ""
            typer.echo(f"{item['id']}: {item['display_name']}{default}")


@app.command("enrich")
def enrich(property_or_listing_id: Annotated[int, typer.Argument(help="Property ID or Listing ID.")]) -> None:
    with session_scope() as session:
        prop = resolve_property(session, property_or_listing_id)
        if prop is None:
            raise typer.BadParameter(f"No property or listing found for ID {property_or_listing_id}.")
        count = enrich_property(session, prop)
        typer.echo(f"Stored {count} public-record/enrichment records for property {prop.id}.")


@app.command("enrich-all")
def enrich_all() -> None:
    with session_scope() as session:
        count = enrich_all_favorites(session)
        typer.echo(f"Stored {count} public-record/enrichment records across tracked properties.")


@app.command("research-addresses")
def research_addresses(
    path: Annotated[Path, typer.Argument(help="CSV of addresses or favorited homes.")],
    pilot_limit: Annotated[
        int,
        typer.Option("--pilot-limit", help="Number of imported listings to include in the pilot HTML/Markdown report."),
    ] = 10,
) -> None:
    """Run the address-first public-record research workflow."""

    preferences = load_preferences()
    with session_scope() as session:
        favorites = import_favorites_csv(path, session)
        session.flush()
        enrich_count = enrich_all_favorites(session)
        scores = score_all_listings(session, preferences)
        comparison_path = render_comparison_report(session, preferences)
        listing_ids = [
            favorite.listing_id
            for favorite in favorites[:pilot_limit]
            if favorite.listing_id is not None
        ]
        pilot_path = render_pilot_report(session, listing_ids, preferences) if listing_ids else None
        pilot_html_path = render_pilot_report_html(session, listing_ids, preferences) if listing_ids else None
        typer.echo(
            "Address research complete. "
            f"Imported/updated {len(favorites)} favorites; stored {enrich_count} enrichment records; "
            f"scored {len(scores)} listings; comparison: {comparison_path}"
        )
        if pilot_path:
            typer.echo(f"Pilot Markdown: {pilot_path}")
        if pilot_html_path:
            typer.echo(f"Pilot HTML: {pilot_html_path}")


@app.command("research-input")
def research_input(
    path: Annotated[
        Path,
        typer.Argument(help="Text file containing one address or address-bearing listing URL per line."),
    ],
    label: Annotated[str, typer.Option("--label", help="Label for the generated import batch.")] = "pasted homes",
    pilot_limit: Annotated[int, typer.Option("--pilot-limit", help="Maximum homes to include.")] = 25,
) -> None:
    """Run the paste-to-report workflow from a plain text input file."""

    raw_input = path.read_text(encoding="utf-8")
    with session_scope() as session:
        result = run_research_batch_from_text(
            session,
            raw_input,
            label=label,
            pilot_limit=pilot_limit,
        )
        typer.echo(
            "Research input complete. "
            f"Imported/updated {result.imported_count} homes; "
            f"generated {len(result.favorite_report_paths)} individual reports."
        )
        typer.echo(f"Import CSV: {result.import_path}")
        if result.pilot_markdown_path:
            typer.echo(f"Batch Markdown: {result.pilot_markdown_path}")
        if result.pilot_html_path:
            typer.echo(f"Batch HTML: {result.pilot_html_path}")
        for path in result.favorite_report_paths:
            typer.echo(f"Review: {path}")
        for error in result.parse_errors:
            typer.echo(f"Skipped: {error.source_line} - {error.message}")
        for error in result.run_errors:
            typer.echo(f"Warning: {error.address} - {error.message}")


@app.command("gui")
def gui(
    host: Annotated[str, typer.Option("--host", help="Local interface host.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Local interface port.")] = 8765,
) -> None:
    """Start the local paste-to-report web interface."""

    run_gui_server(host=host, port=port)


@map_app.command("serve")
def map_serve(
    host: Annotated[str, typer.Option("--host", help="Local interface host.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Local map port.")] = 8770,
) -> None:
    """Start the local map-based evaluation hub."""

    run_map_server(host=host, port=port)


@school_zones_app.command("download")
def school_zones_download(
    output: Annotated[
        Path | None,
        typer.Option("--output", help="GeoJSON cache path. Defaults to data/cache/school_zones."),
    ] = None,
    source_url: Annotated[
        str,
        typer.Option("--source-url", help="ArcGIS query URL for the attendance-area layer."),
    ] = MN_ATTENDANCE_AREAS_QUERY_URL,
) -> None:
    """Download/cache official Minnesota school attendance areas as GeoJSON."""

    path = download_attendance_zones(output_path=output, source_url=source_url)
    typer.echo(f"Downloaded attendance zones to {path}")


@school_zones_app.command("import")
def school_zones_import(
    path: Annotated[Path, typer.Argument(help="Attendance-area GeoJSON FeatureCollection.")],
    school_year: Annotated[
        str | None,
        typer.Option("--school-year", help="School year label to store, e.g. 2026."),
    ] = None,
    replace: Annotated[
        bool,
        typer.Option("--replace/--append", help="Replace existing rows for this source/year."),
    ] = True,
) -> None:
    """Import elementary attendance zones into SQLite."""

    with session_scope() as session:
        count = import_attendance_zones(session, path, school_year=school_year, replace=replace)
        typer.echo(f"Imported {count} elementary attendance zones.")


@school_zones_app.command("identify")
def school_zones_identify(
    lat: Annotated[float, typer.Option("--lat", help="Latitude.")],
    lon: Annotated[float, typer.Option("--lon", help="Longitude.")],
    boundary_threshold_miles: Annotated[
        float,
        typer.Option("--boundary-threshold-miles", help="Near-boundary warning distance."),
    ] = 0.10,
) -> None:
    """Identify the likely elementary attendance zone for a point."""

    with session_scope() as session:
        result = identify_elementary_zone(
            session,
            lat=lat,
            lon=lon,
            boundary_threshold_miles=boundary_threshold_miles,
        ).as_dict()
        result = enrich_school_zone_payload(session, result)
        if result["found"]:
            typer.echo(
                f"{result['school_name']} | {result['district_name']} | "
                f"{result['school_year']} | {result['confidence']}"
            )
            for ranking in result.get("ranking_statuses") or []:
                if ranking.get("status") == "ranked":
                    rank = f" #{ranking['state_rank']}" if ranking.get("state_rank") else ""
                    label = f" {ranking['rating_label']}" if ranking.get("rating_label") else ""
                    typer.echo(f"{ranking['source_name']}{rank}{label} | verify at source")
                else:
                    typer.echo(f"{ranking['source_name']}: {ranking['display_label']}")
        else:
            typer.echo("No imported elementary attendance zone contains this point.")
        typer.echo(result["warning"])
        if result.get("source_url"):
            typer.echo(f"Source: {result['source_name']} - {result['source_url']}")


@school_locations_app.command("download")
def school_locations_download(
    output: Annotated[
        Path | None,
        typer.Option("--output", help="GeoJSON cache path. Defaults to data/cache/map_layers."),
    ] = None,
    source_url: Annotated[
        str,
        typer.Option("--source-url", help="ArcGIS query URL for school program locations."),
    ] = SCHOOL_LOCATIONS_QUERY_URL,
) -> None:
    """Download/cache official Minnesota school program point locations."""

    path = download_school_locations(output_path=output, source_url=source_url)
    typer.echo(f"Downloaded school locations to {path}")


@school_locations_app.command("import")
def school_locations_import(
    path: Annotated[Path, typer.Argument(help="School-location GeoJSON FeatureCollection.")],
    elementary_only: Annotated[
        bool,
        typer.Option("--elementary-only/--all-programs", help="Import only elementary-serving locations."),
    ] = True,
    replace: Annotated[
        bool,
        typer.Option("--replace/--append", help="Replace existing school-location features."),
    ] = True,
) -> None:
    """Import official school point locations into the map feature layer."""

    with session_scope() as session:
        count = import_school_locations(
            session,
            path,
            elementary_only=elementary_only,
            replace=replace,
        )
        typer.echo(f"Imported {count} elementary school locations.")


@school_rankings_app.command("download-niche")
def school_rankings_download_niche(
    output: Annotated[
        Path | None,
        typer.Option("--output", help="HTML cache path. Defaults to data/cache/map_layers."),
    ] = None,
    url: Annotated[
        str,
        typer.Option("--url", help="Niche ranking URL to cache."),
    ] = NICHE_MN_ELEMENTARY_RANKINGS_URL,
    top_count: Annotated[
        int,
        typer.Option("--top-count", help="Number of ranked schools to cache, paginating when possible."),
    ] = 250,
) -> None:
    """Download/cache Niche Minnesota elementary ranking pages if reachable."""

    try:
        path = download_niche_elementary_rankings(output_path=output, url=url, top_count=top_count)
    except ValueError as exc:
        typer.echo(f"Download blocked: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Downloaded Niche rankings to {path}")


@school_rankings_app.command("import-niche")
def school_rankings_import_niche(
    path: Annotated[Path, typer.Argument(help="Niche ranking CSV, JSON, or saved HTML page.")],
    school_year: Annotated[
        str | None,
        typer.Option("--school-year", help="School/ranking year label, e.g. 2026."),
    ] = "2026",
    replace: Annotated[
        bool,
        typer.Option("--replace/--append", help="Replace existing Niche rows for this year."),
    ] = True,
) -> None:
    """Import Niche Minnesota elementary rankings as source-labeled third-party context."""

    with session_scope() as session:
        count = import_niche_rankings(session, path, school_year=school_year, replace=replace)
        typer.echo(f"Imported {count} Niche school ranking rows.")


@school_rankings_app.command("download-us-news")
def school_rankings_download_us_news(
    output: Annotated[
        Path | None,
        typer.Option("--output", help="JSON cache path. Defaults to data/cache/map_layers."),
    ] = None,
    url: Annotated[
        str,
        typer.Option("--url", help="U.S. News ranking URL to cache."),
    ] = US_NEWS_MN_ELEMENTARY_RANKINGS_URL,
    top_count: Annotated[
        int,
        typer.Option("--top-count", help="Number of ranked schools to cache, paginating when possible."),
    ] = 250,
) -> None:
    """Download/cache U.S. News Minnesota elementary ranking rows if reachable."""

    try:
        path = download_us_news_elementary_rankings(output_path=output, url=url, top_count=top_count)
    except ValueError as exc:
        typer.echo(f"Download blocked: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Downloaded U.S. News rankings to {path}")


@school_rankings_app.command("import-us-news")
def school_rankings_import_us_news(
    path: Annotated[Path, typer.Argument(help="U.S. News ranking CSV, JSON, or saved HTML page.")],
    school_year: Annotated[
        str | None,
        typer.Option("--school-year", help="School/ranking year label, e.g. 2026."),
    ] = "2026",
    replace: Annotated[
        bool,
        typer.Option("--replace/--append", help="Replace existing U.S. News rows for this year."),
    ] = True,
) -> None:
    """Import U.S. News elementary rankings as source-labeled third-party context."""

    with session_scope() as session:
        count = import_us_news_rankings(session, path, school_year=school_year, replace=replace)
        typer.echo(f"Imported {count} U.S. News school ranking rows.")


@neighborhoods_app.command("export")
def neighborhoods_export(
    path: Annotated[
        Path,
        typer.Argument(help="GeoJSON export path."),
    ] = EXPORTS_DIR / "saved_neighborhoods.geojson",
) -> None:
    """Export saved neighborhoods as GeoJSON."""

    with session_scope() as session:
        output = export_saved_neighborhoods(session, path)
        typer.echo(f"Exported saved neighborhoods to {output}")


@neighborhoods_app.command("import")
def neighborhoods_import(
    path: Annotated[Path, typer.Argument(help="Saved-neighborhood GeoJSON FeatureCollection.")],
) -> None:
    """Import saved neighborhood areas from GeoJSON."""

    with session_scope() as session:
        count = import_saved_neighborhoods(session, path)
        typer.echo(f"Imported {count} saved neighborhoods.")


@neighborhoods_app.command("report")
def neighborhoods_report(
    neighborhood_id: Annotated[
        int | None,
        typer.Option("--neighborhood-id", help="Specific saved-neighborhood ID."),
    ] = None,
) -> None:
    """Generate saved-neighborhood report(s)."""

    with session_scope() as session:
        if neighborhood_id is not None:
            path = render_neighborhood_report(session, neighborhood_id)
            typer.echo(f"Wrote {path}")
        else:
            paths = render_all_neighborhood_reports(session)
            typer.echo(f"Wrote {len(paths)} neighborhood reports.")


@neighborhoods_app.command("score")
def neighborhoods_score(
    neighborhood_id: Annotated[
        int | None,
        typer.Option("--neighborhood-id", help="Specific saved-neighborhood ID."),
    ] = None,
) -> None:
    """Score saved neighborhood fit using user notes plus neutral map facts."""

    with session_scope() as session:
        if neighborhood_id is not None:
            from realestate.models import SavedNeighborhood

            neighborhood = session.get(SavedNeighborhood, neighborhood_id)
            if neighborhood is None:
                raise typer.BadParameter(f"Saved neighborhood {neighborhood_id} not found.")
            result = score_saved_neighborhood(session, neighborhood, persist=True)
            typer.echo(
                f"{neighborhood.name}: {result['overall_score']}/100 "
                f"({result['confidence']} confidence)"
            )
        else:
            results = score_all_saved_neighborhoods(session, persist=True)
            typer.echo(f"Scored {len(results)} saved neighborhoods.")


@highlights_app.command("export")
def highlights_export(
    path: Annotated[
        Path,
        typer.Argument(help="GeoJSON export path."),
    ] = EXPORTS_DIR / "map_highlights.geojson",
) -> None:
    """Export liked/avoided map highlights as GeoJSON."""

    with session_scope() as session:
        output = export_map_highlights(session, path)
        typer.echo(f"Exported map highlights to {output}")


@highlights_app.command("import")
def highlights_import(
    path: Annotated[Path, typer.Argument(help="Map-highlight GeoJSON FeatureCollection.")],
) -> None:
    """Import liked/avoided map highlights from GeoJSON."""

    with session_scope() as session:
        count = import_map_highlights(session, path)
        typer.echo(f"Imported {count} map highlights.")


@map_data_app.command("build")
def map_data_build(
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", help="Directory for generated map GeoJSON/JSON files."),
    ] = None,
) -> None:
    """Prepare GeoJSON files for frontend rendering or backup."""

    with session_scope() as session:
        exports = build_map_data_exports(session, output_dir=output_dir)
        for name, path in exports.items():
            typer.echo(f"{name}: {path}")


@map_layers_app.command("download-parks")
def map_layers_download_parks(
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Overpass JSON cache path. Defaults to data/cache/map_layers."),
    ] = None,
    bbox: Annotated[
        str | None,
        typer.Option(
            "--bbox",
            help="Optional min_lon,min_lat,max_lon,max_lat override. Defaults to tracked map extent.",
        ),
    ] = None,
) -> None:
    """Download/cache OSM parks, trails, playgrounds, and nature areas."""

    parsed_bbox = _parse_bbox(bbox) if bbox else None
    with session_scope() as session:
        path = download_parks_trails_playgrounds(session, output_path=output, bbox=parsed_bbox)
        typer.echo(f"Downloaded parks/trails/playgrounds to {path}")


@map_layers_app.command("import-parks")
def map_layers_import_parks(
    path: Annotated[Path, typer.Argument(help="Overpass JSON or GeoJSON FeatureCollection.")],
    replace: Annotated[
        bool,
        typer.Option("--replace/--append", help="Replace existing parks/trails/playgrounds features."),
    ] = True,
) -> None:
    """Import cached parks, trails, playgrounds, and nature areas."""

    with session_scope() as session:
        count = import_parks_trails_playgrounds(session, path, replace=replace)
        typer.echo(f"Imported {count} parks/trails/playgrounds features.")


@app.command("match-homes-to-neighborhoods")
def match_homes_to_neighborhoods_command(
    near_miles: Annotated[
        float,
        typer.Option("--near-miles", help="Distance threshold for near saved areas."),
    ] = 1.0,
) -> None:
    """Update home-to-saved-neighborhood relationships."""

    with session_scope() as session:
        count = match_homes_to_neighborhoods(session, near_miles=near_miles)
        typer.echo(f"Stored {count} property/neighborhood relationships.")


@app.command("score")
def score() -> None:
    preferences = load_preferences()
    with session_scope() as session:
        scores = score_all_listings(session, preferences)
        typer.echo(f"Scored {len(scores)} listings.")


@app.command("review")
def review(listing_id: Annotated[int, typer.Argument(help="Listing ID.")]) -> None:
    preferences = load_preferences()
    with session_scope() as session:
        path = render_favorite_review(session, listing_id, preferences)
        typer.echo(f"Wrote {path}")


@app.command("review-favorites")
def review_favorites() -> None:
    preferences = load_preferences()
    with session_scope() as session:
        paths = render_all_favorite_reviews(session, preferences)
        typer.echo(f"Wrote {len(paths)} favorite review reports.")


@app.command("compare-favorites")
def compare_favorites() -> None:
    preferences = load_preferences()
    with session_scope() as session:
        path = render_comparison_report(session, preferences)
        typer.echo(f"Wrote {path}")


@app.command("pilot-report")
def pilot_report(
    listing_ids: Annotated[
        list[int],
        typer.Argument(help="Listing IDs to include in a concise pilot analysis."),
    ],
) -> None:
    preferences = load_preferences()
    with session_scope() as session:
        path = render_pilot_report(session, listing_ids, preferences)
        typer.echo(f"Wrote {path}")


@app.command("pilot-report-html")
def pilot_report_html(
    listing_ids: Annotated[
        list[int],
        typer.Argument(help="Listing IDs to include in a styled HTML pilot analysis."),
    ],
) -> None:
    preferences = load_preferences()
    with session_scope() as session:
        path = render_pilot_report_html(session, listing_ids, preferences)
        typer.echo(f"Wrote {path}")


@app.command("agent-questions")
def agent_questions(listing_id: Annotated[int, typer.Argument(help="Listing ID.")]) -> None:
    preferences = load_preferences()
    with session_scope() as session:
        path = render_agent_questions(session, listing_id, preferences)
        typer.echo(f"Wrote {path}")


@app.command("tour-checklist")
def tour_checklist(listing_id: Annotated[int, typer.Argument(help="Listing ID.")]) -> None:
    preferences = load_preferences()
    with session_scope() as session:
        path = render_tour_checklist(session, listing_id, preferences)
        typer.echo(f"Wrote {path}")


@app.command("report")
def report(kind: Annotated[str, typer.Argument(help="daily or weekly")]) -> None:
    preferences = load_preferences()
    with session_scope() as session:
        if kind == "daily":
            path = render_daily_report(session, preferences)
        elif kind == "weekly":
            path = render_weekly_report(session, preferences)
        else:
            raise typer.BadParameter("kind must be 'daily' or 'weekly'.")
        typer.echo(f"Wrote {path}")


@app.command("run")
def run() -> None:
    """Import default examples/configured files, enrich, score, and generate reports."""

    preferences = load_preferences()
    with session_scope() as session:
        anchors_path = Path("config/life_anchors.yaml")
        favorites_path = Path("data/imports/favorites.csv")
        if anchors_path.exists():
            import_life_anchors_file(anchors_path, session, replace=True)
        if favorites_path.exists():
            import_favorites_csv(favorites_path, session)
        enrich_all_favorites(session)
        score_all_listings(session, preferences)
        favorite_paths = render_all_favorite_reviews(session, preferences)
        comparison_path = render_comparison_report(session, preferences)
        daily_path = render_daily_report(session, preferences)
        weekly_path = render_weekly_report(session, preferences)
        typer.echo(
            "Run complete. "
            f"Reviews: {len(favorite_paths)}, comparison: {comparison_path}, "
            f"daily: {daily_path}, weekly: {weekly_path}"
        )


@app.command("feedback")
def feedback(
    listing_id: Annotated[int, typer.Argument(help="Listing ID.")],
    rating: Annotated[
        str,
        typer.Option(
            "--rating",
            help="like, strong_like, maybe, dislike, or reject",
        ),
    ],
    notes: Annotated[str, typer.Option("--notes", help="Feedback notes.")] = "",
) -> None:
    allowed = {"like", "strong_like", "maybe", "dislike", "reject", "rejected"}
    if rating not in allowed:
        raise typer.BadParameter(f"rating must be one of {sorted(allowed)}")
    with session_scope() as session:
        listing = session.get(Listing, listing_id)
        if listing is None:
            raise typer.BadParameter(f"Listing {listing_id} not found.")
        favorite = session.execute(
            select(Favorite).where(Favorite.listing_id == listing_id)
        ).scalar_one_or_none()
        if favorite is None:
            favorite = Favorite(listing=listing, external_url=listing.listing_url)
            session.add(favorite)
        favorite.user_rating = "rejected" if rating == "reject" else rating
        favorite.user_notes = notes
        typer.echo(f"Stored feedback for listing {listing_id}.")


def _parse_bbox(raw: str) -> tuple[float, float, float, float]:
    pieces = [piece.strip() for piece in raw.split(",")]
    if len(pieces) != 4:
        raise typer.BadParameter("bbox must be min_lon,min_lat,max_lon,max_lat")
    try:
        min_lon, min_lat, max_lon, max_lat = (float(piece) for piece in pieces)
    except ValueError as exc:
        raise typer.BadParameter("bbox values must be numeric") from exc
    if min_lon >= max_lon or min_lat >= max_lat:
        raise typer.BadParameter("bbox must be ordered min_lon,min_lat,max_lon,max_lat")
    return min_lon, min_lat, max_lon, max_lat


if __name__ == "__main__":
    app()
