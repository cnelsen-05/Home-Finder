# Vercel Mobile Hosting Plan

HomeAnalyze now has a Vercel-compatible hosted app while preserving the local
SQLite/map-server workflow.

## Hosted Architecture

- `api/index.py` exposes the ASGI app Vercel expects for the serverless
  function. Root `app.py` remains available for local `uvicorn app:app` smoke
  tests.
- `realestate.hosted_app` serves the map UI, static assets, auth, reports, and
  `/api/*` routes.
- `realestate.map_api` remains the shared API implementation for local and
  hosted use.
- `realestate.db` uses local SQLite by default and switches to Postgres when
  `DATABASE_URL` or `POSTGRES_URL` is set.
- Static Leaflet and Leaflet Draw assets are bundled under
  `realestate/web/vendor` so the hosted app does not depend on CDN loading.

## Required Vercel Environment

Set these in the Vercel project before sharing a generated `*.vercel.app` link:

```text
HOMEANALYZE_ACCESS_CODE=
HOMEANALYZE_AUTH_SECRET=
DATABASE_URL=<marketplace Postgres URL>
```

`POSTGRES_URL` is also accepted if your Vercel Marketplace integration uses that
name. Local development keeps using `REAL_ESTATE_DB_PATH`.

## Database Migration

Back up local data first:

```powershell
realestate db backup --output data/exports/database_backup_before_vercel.json
```

After `DATABASE_URL` is available in your shell, clone local SQLite into hosted
Postgres:

```powershell
$env:DATABASE_URL = "<hosted postgres url>"
realestate db migrate-sqlite --sqlite-path data/realestate.db --replace
```

Use `--append` only when you know the target database is empty or can accept
duplicate primary keys.

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
