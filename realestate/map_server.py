from __future__ import annotations

import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from realestate.db import session_scope
from realestate.map_api import handle_api_request, parse_json_body, response_json
from realestate.paths import IMPORTS_DIR, REPORTS_DIR

WEB_DIR = Path(__file__).resolve().parent / "web"


def run_map_server(host: str = "127.0.0.1", port: int = 8770) -> None:
    server = ThreadingHTTPServer((host, port), MapRequestHandler)
    print(f"HomeAnalyze map hub running at http://{host}:{port}")
    server.serve_forever()


class MapRequestHandler(BaseHTTPRequestHandler):
    server_version = "HomeAnalyzeMap/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_file(WEB_DIR / "map.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/service-worker.js":
            self._send_file(WEB_DIR / "service-worker.js", "application/javascript; charset=utf-8")
            return
        if parsed.path.startswith("/static/"):
            self._send_static(parsed.path.removeprefix("/static/"))
            return
        if parsed.path == "/report":
            self._send_report_file(parsed.query)
            return
        if parsed.path.startswith("/api/"):
            self._send_api("GET", self.path)
            return
        self.send_error(404, "Not found")

    def do_POST(self) -> None:
        self._send_api("POST", self.path, self._read_json_body())

    def do_PUT(self) -> None:
        self._send_api("PUT", self.path, self._read_json_body())

    def do_PATCH(self) -> None:
        self._send_api("PATCH", self.path, self._read_json_body())

    def do_DELETE(self) -> None:
        self._send_api("DELETE", self.path)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        return parse_json_body(self.rfile.read(length))

    def _send_api(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> None:
        with session_scope() as session:
            response = handle_api_request(session, method, path, body)
        payload = response_json(response)
        self.send_response(response.status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_static(self, raw_name: str) -> None:
        try:
            path = (WEB_DIR / raw_name).resolve()
        except OSError:
            self.send_error(404, "Not found")
            return
        if path != WEB_DIR.resolve() and WEB_DIR.resolve() not in path.parents:
            self.send_error(404, "Not found")
            return
        self._send_file(path, mimetypes.guess_type(path.name)[0] or "application/octet-stream")

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(404, "File not found")
            return
        payload = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_report_file(self, query: str) -> None:
        params = parse_qs(query)
        requested = params.get("path", [""])[0]
        try:
            path = _allowed_report_path(requested)
        except ValueError:
            self.send_error(403, "Report path is not allowed")
            return
        self._send_file(path, mimetypes.guess_type(path.name)[0] or "text/plain")


def report_url(path: Path) -> str:
    return f"/report?path={quote(str(path.resolve()))}"


def _allowed_report_path(raw_path: str) -> Path:
    if not raw_path:
        raise ValueError("Missing file path")
    path = Path(unquote(raw_path)).resolve()
    allowed_roots = [REPORTS_DIR.resolve(), IMPORTS_DIR.resolve()]
    if not any(path == root or root in path.parents for root in allowed_roots):
        raise ValueError("File path is outside allowed report/import directories")
    return path
