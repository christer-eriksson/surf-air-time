"""The page's browser-side history must stay consistent with what "Open" shows: rows written by
an older build are recomputed on load, opening a row never duplicates it, and tacks are shown
apart from jibes.  Needs Chrome and Node >= 22 (built-in fetch + WebSocket); skipped otherwise."""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "activity_24669790521.tcx"
CHROME = next((c for c in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser") if shutil.which(c)), None)
NODE = shutil.which("node")


def _node_ok() -> bool:
    if not NODE:
        return False
    out = subprocess.run([NODE, "--version"], capture_output=True, text=True).stdout.strip().lstrip("v")
    return int(out.split(".")[0]) >= 22


@pytest.mark.skipif(CHROME is None or not DATA.exists() or not _node_ok(), reason="needs Chrome, Node >= 22 and the sample export")
def test_history_rows_match_open(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"], cwd=ROOT,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        proc = subprocess.run([NODE, str(ROOT / "tests" / "web_history_check.mjs"), f"http://127.0.0.1:{port}/web/index.html?debug=1",
                               str(tmp_path / "profile")], capture_output=True, text=True, timeout=180, env={"CHROME": CHROME, "PATH": "/usr/bin:/bin:/usr/local/bin"})
    finally:
        server.terminate()
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert "error" not in out, out["error"]
    assert out["emptyAtStart"]
    assert out["statusAfterReload"].startswith("Updated 1 stored session")
    jibes_col, tacks_col = 4, 5
    assert len(out["rowsAfterReload"]) == 1
    row = out["rowsAfterReload"][0]
    assert row[jibes_col] == out["expected"]["jibes"] and row[tacks_col] == out["expected"]["tacks"]
    assert all(v.startswith("0.5.1 ") for v in out["storedVersions"])
    assert out["kpiJibes"].startswith(f"Jibes landed: {out['expected']['jibes']} ")
    assert f"{out['expected']['tacks']} tacks" in out["turnCaption"]
    assert out["tackRowsInTable"] == int(out["expected"]["tacks"])
    assert out["hollowMarkers"] == 2 * int(out["expected"]["tacks"])  # timeline + map
    assert out["retentionBars"] == 0  # tacks are not jibes, so no retention bars
    assert "Jibe" not in out["tipTitles"] and "Tack" in out["tipTitles"]
    assert out["rowsAfterOpen"] == out["rowsAfterReload"], "Open must not duplicate the row"
    assert out["statusSecondReload"] == "" and out["rowsSecondReload"] == out["rowsAfterReload"]
    assert out["chartCount"] == 3 and out["chartBars"] == 3, out  # three charts, one stored session each
    assert out["openedFromChart"], out
    assert out["mapTiles"] and set(out["mapTiles"]) == {"tile.openstreetmap.org"}, out["mapTiles"]
    assert out["mapTilesOff"] == 0 and out["mapPrefStored"] == "off"
    assert out["trimNote"]
    marks = out["marks"]
    assert marks["mapRed"] == marks["tlRed"] and marks["mapGrey"] == marks["tlGrey"], marks  # same marks on map and timeline
    fm = out["fullMap"]
    assert fm["open"] and fm["closed"], fm
    assert fm["bar"] and fm["barZoomed"] and fm["bar"] != fm["barZoomed"], fm  # zooming changed the scale bar
    multi = out["multi"]
    assert "Analysed 2 files" in multi["status"], multi
    assert "skipped 2 files already in your history (ride_c.tcx, ride_a.tcx)" in multi["status"], multi
    assert "ignored 1 file that is not .tcx or .gpx" in multi["status"], multi
    assert "could not read bad.tcx" in multi["status"], multi
    assert not multi["errorHidden"] and multi["error"].startswith("bad.tcx: "), multi  # ride_c is the stored session re-exported under another name
    assert len(multi["rows"]) == 3 and sum("ride_a.tcx" in r for r in multi["rows"]) == 1 and sum("ride_b.tcx" in r for r in multi["rows"]) == 1, multi
    assert out["consoleErrors"] == []
    assert out["cleared"] == {"rows": 0, "prefs": 0, "reportHidden": True, "emptyShown": True, "button": "Clear all data"}, out["cleared"]
