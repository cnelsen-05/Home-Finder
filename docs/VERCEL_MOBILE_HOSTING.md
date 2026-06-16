# Vercel Mobile Hosting Plan

HomeAnalyze now has a Vercel-compatible hosted app while preserving the local
SQLite/map-server workflow.

## Hosted Architecture

- Root `app.py` exposes the ASGI app Vercel expects for zero-configuration
  FastAPI deployment and remains available for local `uvicorn app:app` smoke
  tests.
- `realestate.hosted_app` serves the map UI, static assets, auth, reports, and
  `/api/*` routes.
- `realestate.map_api` remains the shared API implementation for local and
  hosted use.
- `realestate.db` uses local SQLite by default and switches to Postgres when
  `DATABASE_URL` or `POSTGRES_URL` is set.
- Supabase Postgres is the preferred free-tier shared database path. The app
  connects server-side with SQLAlchemy; the browser never receives database
  credentials.
- Household profiles are app-level records. Homes, school layers, highlights,
  and saved areas stay shared; per-person ratings and notes are stored as
  profile feedback rows.
- Static Leaflet and Leaflet Draw assets are bundled under
  `realestate/web/vendor` so the hosted app does not depend on CDN loading.

## Required Vercel Environment

Set these in the Vercel project before sharing a generated `*.vercel.app` link:

```text
HOMEANALYZE_ACCESS_CODE=
HOMEANALYZE_AUTH_SECRET=
DATABASE_URL=<Supabase Postgres connection string>
HOMEANALYZE_HOUSEHOLD_NAME=Home Search
HOMEANALYZE_PROFILE_NAMES=Adult 1,Adult 2
```

`POSTGRES_URL` is also accepted. Local development keeps using
`REAL_ESTATE_DB_PATH` unless `DATABASE_URL` is set in that shell.

For Supabase, copy a Postgres connection string from the Supabase project
settings and keep SSL enabled. If Supabase offers both direct and pooler
connection strings, prefer the pooler string for Vercel serverless deployments.
The app uses short-lived SQLAlchemy connections in hosted mode by default; set
`HOMEANALYZE_DB_POOL=null` explicitly if you want that behavior outside Vercel.

Hosted deployments refuse to fall back to SQLite unless
`HOMEANALYZE_ALLOW_EPHEMERAL_SQLITE=1` is set. Do not use that override for the
shared family app; Vercel's runtime filesystem is not durable app storage and an
empty SQLite fallback will look like lost homes, neighborhoods, and school
layers.

## Supabase Setup

Create the Supabase project on the free plan, then use its project password and
Postgres connection string only as private environment variables. Do not commit
the connection string to Git.

Recommended order:

```powershell
# In a local shell only, after copying the Supabase Postgres URL:
$env:DATABASE_URL = "<supabase postgres url with sslmode=require>"
$env:HOMEANALYZE_HOUSEHOLD_NAME = "Home Search"
$env:HOMEANALYZE_PROFILE_NAMES = "Adult 1,Adult 2"

realestate db status
realestate profiles init --household-name "Home Search" --profile "Adult 1" --profile "Adult 2"
```

Then add the same non-secret profile variables and the secret database/auth
variables to Vercel.

## Database Migration

Back up local data first:

```powershell
realestate db backup --output data/exports/database_backup_before_vercel.json
```

Confirm the local source-of-truth counts:

```powershell
realestate db status
```

After `DATABASE_URL` is available in your shell, clone local SQLite into hosted
Postgres:

```powershell
$env:DATABASE_URL = "<supabase postgres url with sslmode=require>"
realestate db migrate-sqlite --sqlite-path data/realestate.db --replace
realestate profiles init --household-name "Home Search" --profile "Adult 1" --profile "Adult 2"
```

If you only have a portable JSON backup available, restore that instead:

```powershell
realestate db restore --input data/exports/database_backup_before_vercel.json --replace
realestate profiles init --household-name "Home Search" --profile "Adult 1" --profile "Adult 2"
```

Use `--append` only when you know the target database is empty or can accept
duplicate primary keys.

Run `realestate db status` again with the hosted `DATABASE_URL` set. The hosted
database should show nonzero counts for `favorites`, `listings`,
`saved_neighborhoods`, and `school_attendance_zones` before relying on the
deployed map.

## Shared Profile UX

- Use the profile selector in the map sidebar to switch between household
  members.
- Home ratings/notes saved from a selected profile are personal responses.
- Saved-area boundaries, shared tags, school zones, parks, and imported layers
  remain common household data.
- Saved areas also have a separate personal response card for each profile.
- The simple `HOMEANALYZE_ACCESS_CODE` gate protects the private link. Full
  Supabase Auth with row-level security can be added later if separate logins or
  invite management become necessary.

## Local Hosted-App Smoke Test

```powershell
$env:HOMEANALYZE_ACCESS_CODE = "test-code"
$env:HOMEANALYZE_AUTH_SECRET = "local-dev-secret"
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8790
```

Open `http://127.0.0.1:8790`.

## Vercel Deploy

```powershell
npx vercel
```

Use the generated Vercel URL. A custom domain is optional.

## Mobile UX Included

- Full-screen map on phones.
- Bottom-sheet menu and details panels.
- Mobile action bar for menu, details, current location, and liked-area capture.
- Add-home flow can use geocoding or the last map tap/current location as the
  home pin.
- Static PWA manifest and service worker for cached shell assets.
- Quick notes queue locally on the phone and retry when the app is online.

## Current Limits

- Hosted report files only work for files present in the deployment. Long-term,
  generated reports should be stored in Postgres or Blob storage.
- Photo uploads are not implemented yet. Use Vercel Blob when attachments are
  needed.
- Heavy layers are already lazy-loaded, but large school-zone payloads should
  eventually be served by viewport/bounds for better low-bandwidth mobile use.
