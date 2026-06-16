# Home-Finder

A local/private decision assistant for a Minneapolis / Twin Cities home search.
It imports favorited homes, normalizes listing data into SQLite, scores each home
with deterministic rules, and generates Markdown reports for tour, watch, agent
question, or skip decisions.

This is personal decision support. It is not a public brokerage website, an
appraisal, legal advice, inspection advice, or a replacement for a licensed real
estate professional.

## Compliance Boundaries

- The project does not scrape Zillow, Redfin, Realtor.com, MLS portals, broker
  portals, or protected websites.
- Listing URLs are stored as references only unless you provide an authorized API
  or a file/email you personally received.
- MLS Grid access is disabled until credentials, licensing, and usage rights are
  configured through environment variables.
- Missing data remains unknown. Reports surface unavailable or uncertain facts
  instead of treating them as positive signals.
- Scoring avoids protected-class characteristics, demographic conclusions, or
  steering language. It uses neutral facts such as price, taxes, parcel context,
  commute anchors, parks/trails distance, user-stated city preferences, and
  property characteristics.
- Bundled browser map assets are documented in
  `docs/THIRD_PARTY_NOTICES.md`.

## Setup

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
realestate init
```

If `py` is unavailable, use any Python 3.11+ interpreter:

```powershell
<path-to-python.exe> -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## First Workflow

Edit these files first:

- `config/preferences.yaml`
- `config/life_anchors.yaml`
- `data/imports/favorites.csv`

Then run:

```powershell
realestate init
realestate import-life-anchors config/life_anchors.yaml
realestate import-favorites data/imports/favorites.csv
realestate enrich-all
realestate score
realestate review-favorites
realestate compare-favorites
```

Reports are written under `data/reports/`.

For address-first research from an address CSV, run:

```powershell
realestate research-addresses data/imports/liked_homes_initial.csv --pilot-limit 10
```

This imports the addresses, geocodes them, runs legally accessible public/GIS
sources, scores the homes with confidence-aware missing data, and writes
comparison plus pilot reports.

For paste-to-report usage, start the local GUI:

```powershell
realestate gui --port 8765
```

Open `http://127.0.0.1:8765`, paste one address or address-bearing listing URL
per line, and generate the batch HTML report plus individual Markdown reviews.
Listing links are kept as references unless the address can be parsed from the
URL or typed next to it.

You can run the same workflow from a text file:

```powershell
realestate research-input data/imports/my_addresses.txt --label "weekend tours"
```

## Map-Based Evaluation Hub

Start the private map workspace:

```powershell
realestate map serve --port 8770
```

Open `http://127.0.0.1:8770`. The map shows geocoded favorite homes, saved
neighborhood pockets, imported elementary attendance zones, life anchors, and
map notes. Draw tools can save polygons, rectangles, and circles as reusable
saved neighborhoods with ratings, tags, and notes.

School attendance zones are cached/imported explicitly:

```powershell
realestate school-zones download
realestate school-zones import data/cache/school_zones/mn_school_attendance_areas_current.geojson --school-year 2026
realestate school-zones identify --lat 45.0123 --lon -93.4567
```

Attendance-zone results are likely matches from current public data only.
Boundaries can change, and near-boundary points are flagged. Verify assignment
directly with the district before relying on it.

Official school point locations and source-labeled school ranking context are
separate imports. Niche may block automated download with CAPTCHA; if so, save
the ranking page as HTML or prepare a CSV and import it. The included seed CSV is
partial and should be refreshed/verified at Niche before relying on it:

```powershell
realestate school-locations download
realestate school-locations import data/cache/map_layers/mn_school_program_locations_current.geojson
realestate school-rankings download-niche --top-count 250
realestate school-rankings import-niche data/imports/niche_mn_elementary_rankings_seed.csv --school-year 2026
realestate school-rankings download-us-news --top-count 250
realestate school-rankings import-us-news data/cache/map_layers/us_news_mn_elementary_rankings_top250.json --school-year 2026
```

U.S. News & World Report rankings are also third-party context. The downloader
paginates the public Minnesota elementary ranking page and caches the imported
top 250 as JSON when reachable. Niche often blocks automated download with
CAPTCHA; when that happens, save ranking pages 1-10 as HTML or CSV in a browser,
place them in a folder, and run `realestate school-rankings import-niche
path\to\saved-niche-pages --school-year 2026`. School rankings are never used
as assignment guarantees or neighborhood demographic conclusions.

Use the map's Quick Highlight buttons to draw liked/avoided pockets and street
segments while touring. Highlights stay separate from saved neighborhoods so a
quick "liked street" or "avoid this busy edge" note can be captured without
turning it into a formal neighborhood area.

After saving pockets or importing new homes, update relationships and reports:

```powershell
realestate match-homes-to-neighborhoods
realestate neighborhoods score
realestate neighborhoods report
realestate map-data build
realestate compare-favorites
```

Optional map layers are cached locally. Parks, trails, playgrounds, and nature
areas use OpenStreetMap/Overpass; coverage depends on community tagging and
should be verified locally:

```powershell
realestate map-layers download-parks
realestate map-layers import-parks data/cache/map_layers/parks_trails_playgrounds_overpass.json
realestate neighborhoods score
realestate map-data build
```

Saved neighborhoods can be backed up or moved as GeoJSON:

```powershell
realestate neighborhoods export
realestate neighborhoods import data/exports/saved_neighborhoods.geojson
```

## Hosted Mobile App

The same map hub can run as a private Vercel-hosted mobile app without a custom
domain. The Vercel FastAPI entrypoint is root `app.py`, which exposes the ASGI
application as `app`.

Before sharing a generated `*.vercel.app` link, configure:

```text
HOMEANALYZE_ACCESS_CODE=
HOMEANALYZE_AUTH_SECRET=
DATABASE_URL=<Vercel Marketplace Postgres URL>
```

The hosted app will not use a fallback SQLite database on Vercel. If
`DATABASE_URL` / `POSTGRES_URL` is missing, it returns a setup error instead of
showing an empty workspace.

Back up and migrate local data:

```powershell
realestate db backup --output data/exports/database_backup_before_vercel.json
realestate db status
$env:DATABASE_URL = "<hosted postgres url>"
realestate db migrate-sqlite --sqlite-path data/realestate.db --replace
realestate db status
```

Local hosted-app smoke test:

```powershell
$env:HOMEANALYZE_ACCESS_CODE = "test-code"
$env:HOMEANALYZE_AUTH_SECRET = "local-dev-secret"
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8790
```

See `docs/VERCEL_MOBILE_HOSTING.md` for the hosted architecture, Vercel env
vars, migration path, and mobile UX notes.

## CLI Commands

```powershell
realestate init
realestate db backup
realestate db migrate-sqlite --sqlite-path data/realestate.db --replace
realestate import-favorites data/imports/favorites.csv
realestate import-listing-text data/imports/listing_text_example.md
realestate import-life-anchors config/life_anchors.yaml
realestate import-listings data/imports/favorites.csv
realestate research-addresses data/imports/liked_homes_initial.csv --pilot-limit 10
realestate research-input data/imports/my_addresses.txt --label "weekend tours"
realestate gui --port 8765
realestate map serve --port 8770
realestate school-zones download
realestate school-zones import data/cache/school_zones/mn_school_attendance_areas_current.geojson --school-year 2026
realestate school-zones identify --lat 45.0123 --lon -93.4567
realestate school-locations download
realestate school-locations import data/cache/map_layers/mn_school_program_locations_current.geojson
realestate school-rankings download-niche --top-count 250
realestate school-rankings import-niche data/imports/niche_mn_elementary_rankings_seed.csv --school-year 2026
realestate school-rankings download-us-news --top-count 250
realestate school-rankings import-us-news data/cache/map_layers/us_news_mn_elementary_rankings_top250.json --school-year 2026
realestate map-layers download-parks
realestate map-layers import-parks data/cache/map_layers/parks_trails_playgrounds_overpass.json
realestate neighborhoods export
realestate neighborhoods import data/exports/saved_neighborhoods.geojson
realestate highlights export
realestate highlights import data/exports/map_highlights.geojson
realestate neighborhoods report
realestate neighborhoods score
realestate match-homes-to-neighborhoods
realestate map-data build
realestate enrich 1
realestate enrich-all
realestate score
realestate review 1
realestate review-favorites
realestate compare-favorites
realestate agent-questions 1
realestate tour-checklist 1
realestate report daily
realestate report weekly
realestate feedback 1 --rating like --notes "Good layout, verify basement."
realestate run
```

## CSV Formats

`favorites.csv`

```csv
source,url,address,city,state,zip,price,beds,baths,finished_sqft,lot_size,year_built,property_type,status,description,user_rating,user_notes
manual,https://example.invalid/listing/1,4521 Example Ave S,Minneapolis,MN,55419,699000,4,3,2650,7405,1938,single_family,active,"Updated kitchen and finished basement.",strong_like,"Verify sewer line."
```

`life_anchors.csv`

```csv
name,category,address,city,state,zip,priority,notes
Example Work,work,100 Washington Ave S,Minneapolis,MN,55401,1,Replace with your real work address.
```

## Public Records

The address-first workflow uses legally accessible public/GIS sources where
available:

- US Census geocoding
- MetroGIS Regional Parcels for parcel, lot, tax, EMV, year built, and school code facts
- Minnesota school district boundaries for official district lookup
- Minnesota school attendance area boundaries for likely elementary attendance
  zone lookup, with source/year metadata and district-verification warnings
- FEMA NFHL flood-zone checks
- MnDOT current AADT traffic-volume proximity checks
- MPCA What's In My Neighborhood environmental-site proximity checks
- Minneapolis CCS Permits open data where the address is in Minneapolis
- OpenStreetMap / Overpass nearby amenities for parks, playgrounds, trails, gyms, and childcare
- Cached OpenStreetMap / Overpass map features for the map-level
  parks/trails/playgrounds layer

Major listing portals are not scraped by default. Store listing URLs as
references, use user-provided listing text/exports, or configure a licensed API
such as MLS Grid when usage rights are available. Each public fact stores source
name, source URL, retrieval timestamp, confidence, parsed payload, raw payload
when available, and warning notes.

## Scoring

All scores are 0 to 100. `Risk / Unknowns` is a safety/knownness score: higher
means lower diligence burden and fewer unknowns. The default overall score uses:

- Daily Life: 35%
- Quality: 30%
- Value: 25%
- Preference: 10%

A risk penalty is applied when unknowns are materially high.
