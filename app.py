"""Web app: `python app.py` then open http://localhost:8000

Uses only the Python standard library (http.server + sqlite3), so there is nothing to install.
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from sqldoctor import analyze

ROOT = Path(__file__).parent
STATIC = ROOT / "static"
MAX_ROWS = 500_000
MAX_BODY = 200_000


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/example":
            self._json(200, {
                "schema": (ROOT / "examples" / "college.sql").read_text(),
                "query": "SELECT amount, method, paid_at\nFROM fee_payments\nWHERE student_id = 4211\nORDER BY paid_at;",
            })
        else:
            self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        if self.path != "/api/analyze":
            return self._json(404, {"error": "Not found"})
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_BODY:
            return self._json(413, {"error": "Request is too large."})
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
            rows = min(int(data.get("rows", 50_000)), MAX_ROWS)
            report = analyze(data.get("schema", ""), data.get("query", "").rstrip("; \n"), rows=rows)
            self._json(200, report.to_dict())
        except Exception as exc:  # show the SQLite or parsing error to the user
            self._json(400, {"error": str(exc)})

    def log_message(self, fmt, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main() -> None:
    port = int(os.environ.get("PORT", 8000))
    print(f"SQL Query Doctor running at http://localhost:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
