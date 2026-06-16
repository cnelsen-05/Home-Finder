from __future__ import annotations

import hmac
import os
import time
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from realestate.config import load_environment
from realestate.db import (
    HostedDatabaseNotConfigured,
    assert_hosted_database_configured,
    database_mode,
    session_scope,
)
from realestate.map_api import handle_api_request, parse_json_body, response_json
from realestate.paths import IMPORTS_DIR, REPORTS_DIR

WEB_DIR = Path(__file__).resolve().parent / "web"
STATIC_DIR = WEB_DIR
AUTH_COOKIE = "homeanalyze_session"
AUTH_TTL_SECONDS = 60 * 60 * 24 * 30

app = FastAPI(title="HomeAnalyze Map Hub")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(HostedDatabaseNotConfigured)
async def hosted_database_not_configured_handler(
    request: Request,
    exc: HostedDatabaseNotConfigured,
) -> Response:
    if request.url.path.startswith("/api/") or request.url.path == "/health":
        return JSONResponse(
            {
                "status": "misconfigured",
                "error": str(exc),
                "required_env": ["DATABASE_URL or POSTGRES_URL"],
                "recovery": "Set a persistent hosted Postgres URL and migrate local data.",
            },
            status_code=503,
        )
    return HTMLResponse(_database_setup_html(str(exc)), status_code=503)


@app.middleware("http")
async def require_private_access(request: Request, call_next):
    if not _access_code():
        return await call_next(request)
    if _is_public_path(request.url.path) or _is_authenticated(request):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            {"error": "Authentication required."},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return RedirectResponse(f"/login?next={quote(str(request.url.path))}", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    assert_hosted_database_configured()
    return HTMLResponse((WEB_DIR / "map.html").read_text(encoding="utf-8"))


@app.get("/service-worker.js")
async def service_worker() -> FileResponse:
    return FileResponse(WEB_DIR / "service-worker.js", media_type="application/javascript")


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request) -> HTMLResponse:
    next_path = request.query_params.get("next") or "/"
    return HTMLResponse(_login_html(next_path, error=None))


@app.post("/login")
async def login_submit(request: Request) -> Response:
    body = (await request.body()).decode("utf-8")
    form = {key: values[0] for key, values in parse_qs(body).items()}
    next_path = form.get("next") or "/"
    if not _valid_next_path(next_path):
        next_path = "/"
    if not hmac.compare_digest(form.get("access_code") or "", _access_code() or ""):
        return HTMLResponse(_login_html(next_path, error="Invalid access code."), status_code=401)
    response = RedirectResponse(next_path, status_code=303)
    response.set_cookie(
        AUTH_COOKIE,
        _signed_session_token(),
        httponly=True,
        secure=_secure_cookie(request),
        samesite="lax",
        max_age=AUTH_TTL_SECONDS,
    )
    return response


@app.post("/logout")
async def logout() -> Response:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(AUTH_COOKIE)
    return response


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def api_proxy(path: str, request: Request) -> Response:
    raw_path = f"/api/{path}"
    if request.url.query:
        raw_path = f"{raw_path}?{request.url.query}"
    body: dict[str, Any] | None = None
    if request.method in {"POST", "PUT", "PATCH"}:
        body = parse_json_body(await request.body())
    with session_scope() as session:
        api_response = handle_api_request(session, request.method, raw_path, body)
    return Response(
        content=response_json(api_response),
        status_code=api_response.status,
        media_type="application/json; charset=utf-8",
    )


@app.get("/report")
async def report_file(path: str = "") -> Response:
    try:
        report_path = _allowed_report_path(path)
    except ValueError:
        return JSONResponse({"error": "Report path is not allowed."}, status_code=403)
    if not report_path.exists():
        return JSONResponse({"error": "Report file not found in this deployment."}, status_code=404)
    return FileResponse(report_path)


@app.get("/health")
async def health() -> dict[str, Any]:
    assert_hosted_database_configured()
    return {"status": "ok", "database": database_mode()}


def _is_public_path(path: str) -> bool:
    return path in {"/login", "/health", "/service-worker.js"} or path.startswith("/static/")


def _access_code() -> str | None:
    load_environment()
    code = os.getenv("HOMEANALYZE_ACCESS_CODE") or os.getenv("HOMEANALYZE_AUTH_PASSWORD")
    return code.strip() if code and code.strip() else None


def _auth_secret() -> str | None:
    load_environment()
    secret = os.getenv("HOMEANALYZE_AUTH_SECRET") or _access_code()
    return secret.strip() if secret and secret.strip() else None


def _is_authenticated(request: Request) -> bool:
    bearer = request.headers.get("authorization", "")
    if bearer.lower().startswith("bearer "):
        token = bearer.split(" ", 1)[1].strip()
        return bool(_access_code()) and hmac.compare_digest(token, _access_code() or "")
    cookie = request.cookies.get(AUTH_COOKIE)
    return _valid_session_token(cookie)


def _signed_session_token(timestamp: int | None = None) -> str:
    issued_at = timestamp or int(time.time())
    message = str(issued_at)
    signature = hmac.new((_auth_secret() or "").encode(), message.encode(), sha256).hexdigest()
    return f"{message}.{signature}"


def _valid_session_token(token: str | None) -> bool:
    if not token or "." not in token or not _auth_secret():
        return False
    raw_timestamp, signature = token.split(".", 1)
    try:
        issued_at = int(raw_timestamp)
    except ValueError:
        return False
    if issued_at < int(time.time()) - AUTH_TTL_SECONDS:
        return False
    expected = hmac.new((_auth_secret() or "").encode(), raw_timestamp.encode(), sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def _secure_cookie(request: Request) -> bool:
    return request.url.scheme == "https" or bool(os.getenv("VERCEL"))


def _valid_next_path(path: str) -> bool:
    return path.startswith("/") and not path.startswith("//")


def _allowed_report_path(raw_path: str) -> Path:
    if not raw_path:
        raise ValueError("Missing file path")
    path = Path(unquote(raw_path)).resolve()
    allowed_roots = [REPORTS_DIR.resolve(), IMPORTS_DIR.resolve()]
    if not any(path == root or root in path.parents for root in allowed_roots):
        raise ValueError("File path is outside allowed report/import directories")
    return path


def _login_html(next_path: str, error: str | None) -> str:
    error_html = f'<p class="login-error">{error}</p>' if error else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HomeAnalyze Sign In</title>
  <link rel="stylesheet" href="/static/map.css">
</head>
<body class="login-page">
  <main class="login-panel">
    <h1>HomeAnalyze</h1>
    <p class="meta">Private Twin Cities home-search hub</p>
    {error_html}
    <form method="post" action="/login" class="form-grid">
      <input type="hidden" name="next" value="{_escape_html(next_path)}">
      <label class="field-label" for="accessCode">Access Code</label>
      <input id="accessCode" name="access_code" class="text-input" type="password" autocomplete="current-password" autofocus>
      <button type="submit">Open Map</button>
    </form>
  </main>
</body>
</html>"""


def _database_setup_html(error: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HomeAnalyze Database Setup Required</title>
  <link rel="stylesheet" href="/static/map.css">
</head>
<body class="login-page">
  <main class="login-panel">
    <h1>Database setup required</h1>
    <p class="meta">{_escape_html(error)}</p>
    <p class="meta">Set <code>DATABASE_URL</code> or <code>POSTGRES_URL</code> in Vercel, then migrate the local SQLite database before using the hosted map.</p>
    <pre>realestate db migrate-sqlite --sqlite-path data/realestate.db --replace</pre>
  </main>
</body>
</html>"""


def _escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
