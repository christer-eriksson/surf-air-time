"""A tiny upload web page: drop a Garmin TCX/GPX in, get the HTML report back.

Standard library only (http.server), so it runs on a Raspberry Pi with just
numpy installed.  Nothing is stored: the upload is analysed in memory and
the report is returned straight to the browser.

    python3 -m wingstats.server --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import argparse
import html
import io
import sys
import tempfile
import traceback
from email.parser import BytesParser
from email.policy import default as email_policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from .analysis import Params, analyse
from .html_report import html_report
from .parsers import load_track
from .track import resample

MAX_UPLOAD = 40 * 1024 * 1024  # a 1 h TCX is ~1 MB; be generous but bounded

FORM = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wingfoil stats</title>
<style>
:root{color-scheme:light dark;--plane:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink-2:#52514e;--border:rgba(11,11,11,.12);--s1:#2a78d6}
@media (prefers-color-scheme:dark){:root{--plane:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink-2:#c3c2b7;--border:rgba(255,255,255,.12);--s1:#3987e5}}
body{margin:0;background:var(--plane);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:620px;margin:0 auto;padding:40px 16px}
h1{font-size:28px;margin:0 0 6px}
p.sub{color:var(--ink-2);margin:0 0 24px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px}
label{display:block;font-weight:600;margin:14px 0 4px}
small{color:var(--ink-2);font-weight:400}
input[type=number]{width:110px;padding:6px 8px;border:1px solid var(--border);border-radius:6px;background:var(--plane);color:var(--ink);font:inherit}
input[type=file]{width:100%}
.row{display:flex;flex-wrap:wrap;gap:0 24px}
button{margin-top:20px;padding:10px 20px;border:0;border-radius:8px;background:var(--s1);color:#fff;font:inherit;font-weight:600;cursor:pointer}
button:disabled{opacity:.6}
details{margin-top:12px;color:var(--ink-2)} summary{cursor:pointer;color:var(--ink);font-weight:600}
.err{background:#fde8e8;color:#7a1f1f;border-radius:8px;padding:12px 14px;margin-bottom:16px}
@media (prefers-color-scheme:dark){.err{background:#3a1a1a;color:#f3b4b4}}
footer{color:var(--ink-2);font-size:13px;margin-top:24px}
</style></head>
<body><main>
<h1>Wingfoil stats</h1>
<p class="sub">Upload a Garmin Connect export and get your air time, jibes and full stops. Nothing is stored on the server.</p>
<div class="card">
$error
<form method="post" action="/analyse" enctype="multipart/form-data" onsubmit="document.getElementById('go').disabled=true;document.getElementById('go').textContent='Analysing…'">
<label>Activity file <small>(.tcx preferred, .gpx works too; export from the activity page in Garmin Connect)</small></label>
<input type="file" name="file" accept=".tcx,.gpx" required>
<details><summary>Thresholds</summary>
<div class="row">
<div><label>Flying from <small>km/h</small></label><input type="number" name="fly_on" value="13" step="0.5" min="5" max="30"></div>
<div><label>Flight lost below <small>km/h</small></label><input type="number" name="fly_off" value="10" step="0.5" min="3" max="25"></div>
<div><label>Standing still below <small>km/h</small></label><input type="number" name="stop" value="3" step="0.5" min="0.5" max="8"></div>
<div><label>Long stop after <small>seconds</small></label><input type="number" name="water_stop" value="30" step="5" min="5" max="300"></div>
<div><label>Wind from <small>degrees, blank = estimate</small></label><input type="number" name="wind_from" min="0" max="360" step="1" placeholder="auto"></div>
</div>
</details>
<button id="go" type="submit">Analyse</button>
</form>
</div>
<footer>wingstats $version · flight is inferred from speed; see "How these numbers are computed" at the bottom of the report.</footer>
</main></body></html>
"""

BACK_LINK = ('<p style="margin:0 0 16px"><a href="/" style="color:inherit">&larr; Analyse another file</a></p>')


def _form(error: str = "") -> bytes:
    err = f'<div class="err">{html.escape(error)}</div>' if error else ""
    return FORM.replace("$error", err).replace("$version", __version__).encode("utf-8")


def _parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, str], tuple[str, bytes] | None]:
    """Return (fields, (filename, data)) using the email parser (cgi is gone in 3.13)."""
    msg = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
    fields: dict[str, str] = {}
    upload = None
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            upload = (filename, payload)
        elif name:
            fields[name] = payload.decode("utf-8", "replace").strip()
    return fields, upload


def _params(fields: dict[str, str]) -> Params:
    def f(key: str, default: float) -> float:
        v = fields.get(key, "")
        return float(v) if v else default

    wind = fields.get("wind_from", "")
    return Params(
        fly_on_kmh=f("fly_on", Params.fly_on_kmh), fly_off_kmh=f("fly_off", Params.fly_off_kmh),
        stop_kmh=f("stop", Params.stop_kmh), water_stop_s=int(f("water_stop", Params.water_stop_s)),
        wind_from_deg=float(wind) if wind else None,
    )


def analyse_upload(filename: str, data: bytes, params: Params) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in (".tcx", ".gpx"):
        raise ValueError("Please upload a .tcx or .gpx file (the .csv and .kml exports have no usable track)")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        tmp.write(data)
        tmp.flush()
        raw = load_track(Path(tmp.name))
    raw.source = Path(filename).name
    if not raw.name:
        raw.name = Path(filename).stem
    result = analyse(resample(raw), params)
    page = html_report(result)
    return page.replace("<main>", "<main>" + BACK_LINK, 1)


class Handler(BaseHTTPRequestHandler):
    server_version = f"wingstats/{__version__}"

    def log_message(self, fmt, *args):  # quieter, one line per request
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status: HTTPStatus, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(HTTPStatus.OK, _form())
        elif self.path == "/healthz":
            self._send(HTTPStatus.OK, b"ok\n", "text/plain")
        else:
            self._send(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain")

    def do_POST(self):
        if self.path != "/analyse":
            self._send(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain")
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        ctype = self.headers.get("Content-Type", "")
        if length <= 0 or not ctype.startswith("multipart/form-data"):
            self._send(HTTPStatus.BAD_REQUEST, _form("No file received."))
            return
        if length > MAX_UPLOAD:
            self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, _form(f"File too large (limit {MAX_UPLOAD // (1024 * 1024)} MB)."))
            return
        body = self.rfile.read(length)
        try:
            fields, upload = _parse_multipart(ctype, body)
            if upload is None or not upload[1]:
                raise ValueError("No file received.")
            page = analyse_upload(upload[0], upload[1], _params(fields))
        except ValueError as e:
            self._send(HTTPStatus.BAD_REQUEST, _form(str(e)))
            return
        except Exception as e:  # malformed XML etc. - show a friendly message, log the rest
            traceback.print_exc()
            self._send(HTTPStatus.BAD_REQUEST, _form(f"Could not analyse that file ({type(e).__name__}: {e})."))
            return
        self._send(HTTPStatus.OK, page.encode("utf-8"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="wingstats.server", description="Upload page for wingfoil statistics")
    ap.add_argument("--host", default="127.0.0.1", help="bind address (use 0.0.0.0 to serve your LAN)")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args(argv)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"wingstats {__version__} listening on http://{args.host}:{args.port}/  (Ctrl-C to stop)", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
