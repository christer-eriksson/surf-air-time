# wingstats - wingfoil air time, jibes and full stops from Garmin Connect exports

Takes the TCX (or GPX) export of a Garmin "windsurfing" activity and tells you
how long you were flying on the foil, how many jibes you landed and how much
speed you kept through them, and how often you came to a full stop.

```
python3 -m wingstats data/            # picks the .tcx in the folder
python3 -m wingstats data/ --open     # ...and opens the HTML report in your browser
python3 -m wingstats data/activity_24669790521.tcx --out out
```

Writes three files to `out/`: a text report (also printed), a JSON dump of every
flight / jibe / full stop, and a self-contained HTML report with hoverable charts.

Requires Python 3.10+ and numpy. `pip install -e .[dev]` adds a `wingstats`
command and pytest; `python3 -m pytest` runs the tests (synthetic track + the
sample export).

## Web page (for anyone to use)

`web/` is a static page that does the whole analysis in the visitor's browser.
Copy the two files to any web server (a Raspberry Pi running nginx, GitHub
Pages, a NAS, anything that serves files) and you are done:

```
web/index.html      the page: upload, charts, tables, local history
web/wingstats.js    the analysis (a line-for-line port of the Python package)
```

* Several files can be dropped at once. Files that are not .tcx/.gpx are
  ignored, and sessions already in the history (same file name, or the same
  start time from another export) are skipped; the status line says what
  happened to each.
* The track never leaves the visitor's computer, and the server does no work
  beyond serving two files, so there is nothing to overload or attack.
* Each visitor's sessions are remembered in their own browser (IndexedDB) and
  shown as a history with trend charts on a calendar axis (air time as % of
  each session, time surfed with time in the air per session, jibes landed);
  click a bar or the Open button to reopen a session. Nothing is stored on the
  server.
  History can be exported as JSON or deleted from the page. The original file
  is stored with each row, so when the analysis changes (the `VERSION` in
  `wingstats.js`) stored rows are recomputed on the next visit and always match
  what "Open" shows.
* Every turn entered at foiling speed is listed. Turns classified as tacks are
  shown greyed and hollow and are not counted as jibes; the page warns when the
  wind estimate is too weak to trust that split.
* A report can be saved as a self-contained HTML file.
* `index.html?file=rides/2026-10-10.tcx` analyses a file served next to the
  page, handy for sharing a session as a link.
* It works from a plain `file://` double-click too, except the `?file=` link.

### Deploying it for other people

The page needs nothing but a place that serves two static files. Visitors'
Garmin files are parsed in their own browser, nothing is ever uploaded, and
their history lives in their browser's IndexedDB. Pick whichever is handiest:

* **Raspberry Pi with nginx**: `deploy/nginx-wingstats.conf` has the config
  and the five commands to install it. Point people at
  `http://<pi>/`. The Pi only ever answers GET requests for two small files,
  so there is nothing to overload or break into.
* **GitHub Pages / Cloudflare Pages / Netlify**: push the `web/` folder (or
  the whole repo and set `web` as the site root) and share the URL. Free, no
  server of your own, HTTPS included.
* **No server at all**: send someone `index.html` and `wingstats.js`; the
  page works from a double-click on `index.html` (only the `?file=` link needs
  a server).

Whichever you choose, updating is copying the same two files again. A new
analysis version recomputes every visitor's stored sessions on their next
visit, so old history never goes stale.

`tests/test_web_port.py` runs the page in headless Chrome on the sample export
and checks every number against the Python package, so the two stay in sync.
`tests/test_web_history.py` drives the page over the DevTools protocol (needs
Node 22+) and checks that stored history rows match what "Open" shows.

There is also `wingstats/server.py`, a tiny upload server (Python standard
library only) with a systemd unit in `deploy/`, if you ever prefer server-side
analysis. For a shared Pi the static page is the better choice.

## Which export to use

| File | Contains | Use |
|---|---|---|
| `.tcx` | time, position, distance, **device speed**, altitude, heart rate | **best - use this** |
| `.gpx` | time, position, altitude, heart rate (speed is derived from position) | fallback |
| `.csv` | one lap summary line (duration, distance, max speed, HR) | cross-check only |
| `.kml` | coordinates without timestamps | not usable |

Garmin smart-records (1-20 s between points), so the track is resampled to 1 Hz
before anything is measured. Barometric altitude on the watch is far too noisy
(-115 m to +31 m in the sample) to detect foiling height, so flight is inferred
from speed.

## What is measured

* **Flying** - speed reaches `--fly-on` (13 km/h) and stays above `--fly-off`
  (10 km/h). Total time, share of session, number of flights, longest/median.
* **Jibes** - a sustained heading change of 100° or more entered at foiling
  speed. Each one gets entry / minimum / exit speed, speed 10 s later, the
  retention ratio (exit ÷ entry), a 0-100 score, a grade, and an outcome:
  *clean* (never dropped below the flying threshold), *touchdown* (lost flight
  but kept moving) or *full stop* (came to a standstill).
* **GPS cleaning** - after a dropout the watch often reports a position far
  from the real one and then "bounces" back, which shows up as a 60-120 km/h
  spike and a phantom turn. A sample is kept only if it is reachable from the
  neighbouring kept samples, before and after, at a plausible speed (1.4 x the
  session's 95th-percentile speed, at least 35 km/h, at most 70 km/h). Rejected
  samples are bridged by interpolation and the report says how many there were.
* **Jibe vs tack** - wingfoil turns are nearly always jibes, so the average
  mid-turn heading of the session's turns is taken as downwind (with a
  confidence from how tightly they agree). Drift while stopped in the water is
  only used when there are fewer than three turns, since it is easily fooled by
  current. Turns through downwind are jibes, through upwind tacks. Override
  with `--wind-from 240` if you know the wind.
* **Full stops** - a flight followed by a standstill (< 3 km/h) within 15 s,
  during a jibe or on a straight. GPS speed is zero whether you are swimming or
  standing on the board, so stops are also split by recovery time: *quick
  restart* (flying again within `--water-stop`, default 30 s, probably stayed
  on the board) versus *long stop* (probably in the water).

All thresholds are command line flags (`--fly-on`, `--fly-off`, `--stop`,
`--min-flight`, `--wind-from`, `--water-stop`); see `python3 -m wingstats -h`.

## Layout

```
wingstats/parsers.py      TCX / GPX / CSV / KML readers
wingstats/track.py        1 Hz resampling, local XY, heading
wingstats/analysis.py     flights, full stops, wind estimate, turns, summary
wingstats/report.py       text + JSON
wingstats/html_report.py  HTML with inline SVG charts
wingstats/server.py       optional upload server (stdlib http.server)
web/                      static browser version (index.html + wingstats.js)
tests/                    synthetic-track tests, sample-export checks, JS/Python equivalence
```
