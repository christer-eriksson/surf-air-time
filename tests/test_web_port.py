"""The JavaScript port (web/wingstats.js) must produce the same numbers as the
Python package.  Runs the static page in headless Chrome; skipped when no
Chrome is installed."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "activity_24669790521.tcx"
CHROME = next((c for c in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser") if shutil.which(c)), None)


@pytest.mark.skipif(CHROME is None or not DATA.exists(), reason="needs Chrome and the sample export")
def test_js_port_matches_python(tmp_path):
    from wingstats.analysis import analyse, to_dict
    from wingstats.parsers import load_track
    from wingstats.track import resample

    py = to_dict(analyse(resample(load_track(DATA))))
    url = f"file://{ROOT}/web/index.html?file=../data/{DATA.name}&debug=1"
    proc = subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--allow-file-access-from-files",
         f"--user-data-dir={tmp_path}/profile", "--enable-logging=stderr", "--v=0", "--virtual-time-budget=20000",
         "--dump-dom", url],
        capture_output=True, text=True, timeout=120,
    )
    m = re.search(r'WINGSTATS_JSON (.*?)", source:', proc.stderr, re.S)
    assert m, "page did not log its result:\n" + proc.stderr[-2000:]
    js = json.loads(m.group(1))

    def close(a, b):
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return abs(a - b) <= 1e-6 * max(1.0, abs(a)) or abs(a - b) <= 1e-3
        return a == b

    skip = {"name", "sport", "device", "source", "speed_source", "start_utc", "start_local", "wind", "params"}
    for k, v in py["stats"].items():
        if k not in skip:
            assert close(v, js["stats"][k]), f"stats.{k}: {v} != {js['stats'][k]}"
    for sec in ("flights", "turns", "stops", "standstills"):
        assert len(py[sec]) == len(js[sec]), sec
        for p, j in zip(py[sec], js[sec]):
            for k, v in p.items():
                assert close(v, j[k]), f"{sec}[{p['idx']}].{k}: {v} != {j[k]}"
    for k in ("from_deg", "downwind_deg", "confidence", "reach_headings_deg"):
        assert close(py["stats"]["wind"][k], js["stats"]["wind"][k]) or py["stats"]["wind"][k] == js["stats"]["wind"][k], k
