from __future__ import annotations

import html
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from realestate.db import session_scope
from realestate.input_batch import ResearchBatchResult, run_research_batch_from_text
from realestate.paths import IMPORTS_DIR, REPORTS_DIR


def run_gui_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), ReportRequestHandler)
    print(f"HomeAnalyze report GUI running at http://{host}:{port}")
    server.serve_forever()


class ReportRequestHandler(BaseHTTPRequestHandler):
    server_version = "HomeAnalyzeGUI/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(_render_page())
            return
        if parsed.path == "/report":
            self._send_report_file(parsed.query)
            return
        self.send_error(404, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/run":
            self.send_error(404, "Not found")
            return
        raw_input = ""
        label = "pasted homes"
        try:
            form = self._read_form()
            raw_input = form.get("inputs", [""])[0]
            label = form.get("label", ["pasted homes"])[0]
            pilot_limit = _parse_limit(form.get("pilot_limit", ["25"])[0])
            with session_scope() as session:
                result = run_research_batch_from_text(
                    session,
                    raw_input,
                    label=label,
                    pilot_limit=pilot_limit,
            )
            self._send_html(_render_page(raw_input=raw_input, label=label, result=result))
        except Exception as exc:
            self._send_html(_render_page(raw_input=raw_input, label=label, error=str(exc)))

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_form(self) -> dict[str, list[str]]:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        return parse_qs(body, keep_blank_values=True)

    def _send_html(self, body: str, status: int = 200) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_report_file(self, query: str) -> None:
        params = parse_qs(query)
        requested = params.get("path", [""])[0]
        try:
            path = _allowed_file_path(requested)
        except ValueError:
            self.send_error(403, "Report path is not allowed")
            return
        if not path.exists() or not path.is_file():
            self.send_error(404, "Report file not found")
            return
        content_type = mimetypes.guess_type(path.name)[0] or "text/plain"
        payload = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _render_page(
    *,
    raw_input: str = "",
    label: str = "pasted homes",
    result: ResearchBatchResult | None = None,
    error: str | None = None,
) -> str:
    result_html = _render_result(result) if result else ""
    error_html = f'<div class="notice error">{html.escape(error)}</div>' if error else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HomeAnalyze Reports</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f6f2;
      --panel: #ffffff;
      --ink: #1d2520;
      --muted: #5d6861;
      --line: #d9ded7;
      --accent: #27615a;
      --accent-strong: #18463f;
      --warn: #8a4b12;
      --err: #a52822;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      background: var(--bg);
      color: var(--ink);
    }}
    main {{
      width: min(1120px, calc(100vw - 32px));
      margin: 24px auto 40px;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 16px;
      margin-bottom: 18px;
    }}
    h1 {{
      font-size: 28px;
      line-height: 1.1;
      margin: 0;
      letter-spacing: 0;
    }}
    .subtle {{
      color: var(--muted);
      font-size: 13px;
      margin-top: 6px;
    }}
    form, .results {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
    }}
    label {{
      display: block;
      font-size: 13px;
      font-weight: 700;
      margin-bottom: 7px;
    }}
    textarea, input {{
      width: 100%;
      border: 1px solid #bac4bd;
      border-radius: 6px;
      color: var(--ink);
      font: inherit;
      font-size: 14px;
      padding: 10px 11px;
      background: #fff;
    }}
    textarea {{
      min-height: 270px;
      resize: vertical;
      line-height: 1.45;
    }}
    .grid {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 140px;
      gap: 14px;
      margin: 14px 0;
    }}
    button {{
      background: var(--accent);
      color: white;
      border: 0;
      border-radius: 6px;
      min-height: 42px;
      padding: 0 16px;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }}
    button:hover {{ background: var(--accent-strong); }}
    .toolbar {{
      display: flex;
      justify-content: flex-end;
      align-items: center;
      margin-top: 12px;
    }}
    .results {{
      margin-top: 18px;
    }}
    .result-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin: 0 0 14px;
    }}
    .metric {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      min-height: 72px;
    }}
    .metric strong {{
      display: block;
      font-size: 22px;
      margin-bottom: 4px;
    }}
    .metric span {{ color: var(--muted); font-size: 12px; }}
    h2 {{
      font-size: 18px;
      margin: 0 0 12px;
      letter-spacing: 0;
    }}
    ul {{
      margin: 8px 0 0;
      padding-left: 20px;
    }}
    li {{ margin: 6px 0; }}
    a {{ color: var(--accent-strong); }}
    .notice {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px 12px;
      margin-top: 12px;
      background: #fffaf0;
      color: var(--warn);
      font-size: 14px;
    }}
    .error {{
      background: #fff4f2;
      color: var(--err);
      border-color: #edb7b2;
    }}
    @media (max-width: 720px) {{
      main {{ width: min(100vw - 20px, 1120px); margin-top: 12px; }}
      header {{ display: block; }}
      .grid, .result-grid {{ grid-template-columns: 1fr; }}
      textarea {{ min-height: 220px; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>HomeAnalyze Reports</h1>
        <div class="subtle">Address-first buyer reports with cited public/GIS sources.</div>
      </div>
    </header>
    <form method="post" action="/run">
      <label for="inputs">Addresses or listing links</label>
      <textarea id="inputs" name="inputs" spellcheck="false" placeholder="1570 Creek Run Trl, Excelsior, MN 55331&#10;https://example.com/listing/1831-koehnen-circle-excelsior-mn-55331&#10;4167 Hallgren Ln, Excelsior, MN 55331">{html.escape(raw_input)}</textarea>
      <div class="grid">
        <div>
          <label for="label">Batch label</label>
          <input id="label" name="label" value="{html.escape(label)}">
        </div>
        <div>
          <label for="pilot_limit">Report limit</label>
          <input id="pilot_limit" name="pilot_limit" type="number" min="1" max="100" value="25">
        </div>
      </div>
      <div class="toolbar">
        <button type="submit">Generate Reports</button>
      </div>
    </form>
    {error_html}
    {result_html}
  </main>
</body>
</html>"""


def _render_result(result: ResearchBatchResult) -> str:
    batch_links = []
    if result.pilot_html_path:
        batch_links.append(_report_link("Batch HTML report", result.pilot_html_path))
    if result.pilot_markdown_path:
        batch_links.append(_report_link("Batch Markdown report", result.pilot_markdown_path))
    batch_links.append(_report_link("Import CSV", result.import_path))
    favorite_links = [_report_link(path.stem.replace("_", " "), path) for path in result.favorite_report_paths]
    parse_errors = "".join(_warning_line(error.source_line, error.message) for error in result.parse_errors)
    run_errors = "".join(_warning_line(error.address, error.message) for error in result.run_errors)
    warning_html = (
        f'<div class="notice"><strong>Warnings</strong><ul>{parse_errors}{run_errors}</ul></div>'
        if parse_errors or run_errors
        else ""
    )
    return f"""
    <section class="results">
      <h2>Generated Reports</h2>
      <div class="result-grid">
        <div class="metric"><strong>{result.imported_count}</strong><span>homes imported or updated</span></div>
        <div class="metric"><strong>{len(result.listing_ids)}</strong><span>homes in this report run</span></div>
        <div class="metric"><strong>{len(result.favorite_report_paths)}</strong><span>individual reports</span></div>
      </div>
      <ul>{''.join(f'<li>{link}</li>' for link in batch_links)}</ul>
      <h2 style="margin-top:18px;">Individual Reviews</h2>
      <ul>{''.join(f'<li>{link}</li>' for link in favorite_links)}</ul>
      {warning_html}
    </section>
"""


def _report_link(label: str, path: Path) -> str:
    encoded = quote(str(path.resolve()))
    return f'<a href="/report?path={encoded}" target="_blank" rel="noopener">{html.escape(label)}</a>'


def _warning_line(label: str, message: str) -> str:
    return f"<li><strong>{html.escape(label)}</strong>: {html.escape(message)}</li>"


def _parse_limit(value: str) -> int | None:
    try:
        parsed = int(value)
    except ValueError:
        return None
    return max(1, min(parsed, 100))


def _allowed_file_path(raw_path: str) -> Path:
    if not raw_path:
        raise ValueError("Missing file path")
    path = Path(unquote(raw_path)).resolve()
    allowed_roots = [REPORTS_DIR.resolve(), IMPORTS_DIR.resolve()]
    if not any(path == root or root in path.parents for root in allowed_roots):
        raise ValueError("File path is outside allowed report/import directories")
    return path
