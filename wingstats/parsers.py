"""Readers for the file formats Garmin Connect exports.

Every reader returns a ``RawTrack``: parallel lists of samples at the
device's native (irregular, "smart recording") cadence.  Only formats
that carry timestamps can be analysed:

* TCX  - time, position, distance, speed, altitude, heart rate   (best)
* GPX  - time, position, altitude, heart rate; speed is derived
* CSV  - lap summary only (used to cross-check totals)
* KML  - coordinates without time; cannot be analysed
"""
from __future__ import annotations

import csv
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_TCX = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
_TCX_EXT = "http://www.garmin.com/xmlschemas/ActivityExtension/v2"
_GPX = "http://www.topografix.com/GPX/1/1"
_GPX_TPX = "http://www.garmin.com/xmlschemas/TrackPointExtension/v1"


@dataclass
class RawTrack:
    """Irregularly sampled track as exported by the device."""

    time: list[float] = field(default_factory=list)      # unix seconds
    lat: list[float] = field(default_factory=list)
    lon: list[float] = field(default_factory=list)
    alt: list[Optional[float]] = field(default_factory=list)
    dist: list[Optional[float]] = field(default_factory=list)   # cumulative metres
    speed: list[Optional[float]] = field(default_factory=list)  # m/s from device
    hr: list[Optional[int]] = field(default_factory=list)
    name: str = ""
    sport: str = ""
    source: str = ""
    device: str = ""
    has_device_speed: bool = False

    def __len__(self) -> int:
        return len(self.time)

    @property
    def start(self) -> datetime:
        return datetime.fromtimestamp(self.time[0], tz=timezone.utc)


@dataclass
class LapSummary:
    """Totals from the Garmin CSV lap export (for cross-checking)."""

    duration_s: float
    distance_m: float
    avg_speed_kmh: float
    max_speed_kmh: float
    avg_hr: Optional[int]
    max_hr: Optional[int]
    calories: Optional[int]


def _parse_time(text: str) -> float:
    text = text.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text).timestamp()


def _float(node) -> Optional[float]:
    if node is None or node.text is None:
        return None
    try:
        return float(node.text)
    except ValueError:
        return None


def read_tcx(path: Path) -> RawTrack:
    root = ET.parse(path).getroot()
    ns = {"t": _TCX, "x": _TCX_EXT}
    tr = RawTrack(source=str(path))
    act = root.find(".//t:Activity", ns)
    if act is not None:
        tr.sport = act.get("Sport", "")
        creator = act.find("t:Creator/t:Name", ns)
        if creator is not None and creator.text:
            tr.device = creator.text
    for tp in root.iter(f"{{{_TCX}}}Trackpoint"):
        pos = tp.find("t:Position", ns)
        t = tp.find("t:Time", ns)
        if pos is None or t is None or t.text is None:
            continue  # points without a fix are useless for us
        lat = _float(pos.find("t:LatitudeDegrees", ns))
        lon = _float(pos.find("t:LongitudeDegrees", ns))
        if lat is None or lon is None:
            continue
        spd = _float(tp.find("t:Extensions/x:TPX/x:Speed", ns))
        hr = _float(tp.find("t:HeartRateBpm/t:Value", ns))
        tr.time.append(_parse_time(t.text))
        tr.lat.append(lat)
        tr.lon.append(lon)
        tr.alt.append(_float(tp.find("t:AltitudeMeters", ns)))
        tr.dist.append(_float(tp.find("t:DistanceMeters", ns)))
        tr.speed.append(spd)
        tr.hr.append(int(hr) if hr is not None else None)
        if spd is not None:
            tr.has_device_speed = True
    _dedupe(tr)
    return tr


def read_gpx(path: Path) -> RawTrack:
    root = ET.parse(path).getroot()
    ns = {"g": _GPX, "e": _GPX_TPX}
    tr = RawTrack(source=str(path))
    trk = root.find("g:trk", ns)
    if trk is not None:
        name = trk.find("g:name", ns)
        typ = trk.find("g:type", ns)
        tr.name = (name.text or "") if name is not None else ""
        tr.sport = (typ.text or "") if typ is not None else ""
    for pt in root.iter(f"{{{_GPX}}}trkpt"):
        t = pt.find("g:time", ns)
        if t is None or t.text is None:
            continue
        tr.time.append(_parse_time(t.text))
        tr.lat.append(float(pt.get("lat")))
        tr.lon.append(float(pt.get("lon")))
        tr.alt.append(_float(pt.find("g:ele", ns)))
        hr = _float(pt.find("g:extensions/e:TrackPointExtension/e:hr", ns))
        tr.hr.append(int(hr) if hr is not None else None)
        tr.dist.append(None)
        tr.speed.append(None)
    _dedupe(tr)
    return tr


def read_csv_summary(path: Path) -> Optional[LapSummary]:
    """Garmin's lap CSV uses locale decimals (``15,63``); pick the Summary row."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    row = next((r for r in rows if r.get("Laps", "").lower() == "summary"), None)
    if row is None and rows:
        row = rows[-1]
    if row is None:
        return None

    def num(key: str) -> Optional[float]:
        v = row.get(key)
        if v is None or v.strip() in ("", "--"):
            return None
        return float(v.replace(",", "."))

    def duration(txt: str) -> float:
        parts = [float(p) for p in txt.split(":")]
        while len(parts) < 3:
            parts.insert(0, 0.0)
        h, m, s = parts
        return h * 3600 + m * 60 + s

    km = num("Distance")
    hr_avg = num("Avg HR")
    hr_max = num("Max HR")
    cal = num("Calories")
    return LapSummary(
        duration_s=duration(row["Time"]),
        distance_m=(km or 0.0) * 1000.0,
        avg_speed_kmh=num("Avg Speed (kph)") or 0.0,
        max_speed_kmh=num("Max Speed (kph)") or 0.0,
        avg_hr=int(hr_avg) if hr_avg else None,
        max_hr=int(hr_max) if hr_max else None,
        calories=int(cal) if cal else None,
    )


def read_kml_coordinates(path: Path) -> list[tuple[float, float]]:
    """KML holds lon,lat pairs only - no timestamps, so no analysis possible."""
    root = ET.parse(path).getroot()
    coords: list[tuple[float, float]] = []
    for node in root.iter():
        if node.tag.endswith("coordinates") and node.text:
            for token in node.text.split():
                parts = token.split(",")
                if len(parts) >= 2:
                    coords.append((float(parts[1]), float(parts[0])))
    return coords


def _dedupe(tr: RawTrack) -> None:
    """Drop samples that repeat a timestamp (keep the last one)."""
    keep = [i for i in range(len(tr.time)) if i == len(tr.time) - 1 or tr.time[i + 1] > tr.time[i]]
    if len(keep) == len(tr.time):
        return
    for name in ("time", "lat", "lon", "alt", "dist", "speed", "hr"):
        vals = getattr(tr, name)
        setattr(tr, name, [vals[i] for i in keep])


def load_track(path: Path) -> RawTrack:
    ext = path.suffix.lower()
    if ext == ".tcx":
        return read_tcx(path)
    if ext == ".gpx":
        return read_gpx(path)
    if ext == ".kml":
        raise ValueError("KML exports contain coordinates but no timestamps; use the TCX or GPX export")
    if ext == ".csv":
        raise ValueError("The CSV export is a lap summary without a track; use the TCX or GPX export")
    raise ValueError(f"Unsupported file type: {path.suffix}")


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
