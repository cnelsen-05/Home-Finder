from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
IMPORTS_DIR = DATA_DIR / "imports"
RAW_DIR = DATA_DIR / "raw"
CACHE_DIR = DATA_DIR / "cache"
EXPORTS_DIR = DATA_DIR / "exports"
REPORTS_DIR = DATA_DIR / "reports"
FAVORITE_REPORTS_DIR = REPORTS_DIR / "favorites"
COMPARISON_REPORTS_DIR = REPORTS_DIR / "comparisons"
DAILY_REPORTS_DIR = REPORTS_DIR / "daily"
WEEKLY_REPORTS_DIR = REPORTS_DIR / "weekly"
TOUR_CHECKLISTS_DIR = REPORTS_DIR / "tour_checklists"
AGENT_QUESTIONS_DIR = REPORTS_DIR / "agent_questions"
HTML_REPORTS_DIR = REPORTS_DIR / "html"
NEIGHBORHOOD_REPORTS_DIR = REPORTS_DIR / "neighborhoods"
MAP_EXPORTS_DIR = EXPORTS_DIR / "map_data"
SCHOOL_ZONE_CACHE_DIR = CACHE_DIR / "school_zones"
MAP_LAYER_CACHE_DIR = CACHE_DIR / "map_layers"
DEFAULT_DB_PATH = DATA_DIR / "realestate.db"

REPORT_SUBDIRS = [
    FAVORITE_REPORTS_DIR,
    COMPARISON_REPORTS_DIR,
    DAILY_REPORTS_DIR,
    WEEKLY_REPORTS_DIR,
    TOUR_CHECKLISTS_DIR,
    AGENT_QUESTIONS_DIR,
    HTML_REPORTS_DIR,
    NEIGHBORHOOD_REPORTS_DIR,
]


def ensure_project_dirs() -> None:
    for path in [
        CONFIG_DIR,
        IMPORTS_DIR,
        RAW_DIR,
        CACHE_DIR,
        SCHOOL_ZONE_CACHE_DIR,
        MAP_LAYER_CACHE_DIR,
        EXPORTS_DIR,
        MAP_EXPORTS_DIR,
        REPORTS_DIR,
        *REPORT_SUBDIRS,
    ]:
        path.mkdir(parents=True, exist_ok=True)
