"""Tests on a synthetic track with known flights, jibes and full stops, plus a
smoke test on the real export when it is present."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from wingstats.analysis import Params, analyse, detect_flying
from wingstats.parsers import RawTrack, load_track, read_csv_summary
from wingstats.track import resample

DATA = Path(__file__).resolve().parent.parent / "data"


def synthetic_track() -> RawTrack:
    """Build a 1 Hz track from a scripted speed/heading programme.

    Programme (seconds):
      0-30     stopped, drifting towards 90° (east) at 0.4 m/s  -> wind from west
      30-40    accelerate 0 -> 22 km/h heading 45°
      40-100   fly 22 km/h heading 45°
      100-112  jibe: heading 45 -> 225 through 135 (downwind-ish), speed dips to 16
      112-170  fly 22 km/h heading 225°
      170-182  jibe: heading 225 -> 45 through 135, speed drops to 0 (full stop)
      182-230  stopped, drifting east
      230-240  accelerate to 22 heading 45
      240-300  fly, then abrupt full stop on a straight at 300
      300-330  stopped, drifting east
    """
    speed = []
    heading = []
    for t in range(451):
        if t < 30:
            v, h = 1.44, 90.0
        elif t < 40:
            v, h = 22 * (t - 30) / 10, 45.0
        elif t < 100:
            v, h = 22, 45.0
        elif t < 112:
            k = (t - 100) / 12
            v, h = 22 - 6 * math.sin(math.pi * k), 45 + 180 * k
        elif t < 170:
            v, h = 22, 225.0
        elif t < 182:
            k = (t - 170) / 12
            v, h = 22 * max(0.0, 1 - 1.6 * k), 225 - 180 * k
        elif t < 230:
            v, h = 1.44, 90.0
        elif t < 240:
            v, h = 22 * (t - 230) / 10, 45.0
        elif t < 300:
            v, h = 22, 45.0
        elif t < 303:
            v, h = 22 * (303 - t) / 3, 45.0
        elif t < 330:
            v, h = 1.44, 90.0
        elif t < 340:
            v, h = 22 * (t - 330) / 10, 45.0
        elif t < 400:
            v, h = 22, 45.0
        elif t < 403:
            v, h = 22 * (403 - t) / 3, 45.0
        else:
            v, h = 1.44, 90.0
        speed.append(v / 3.6)
        heading.append(h)
    lat, lon = 57.63, 11.77
    tr = RawTrack(name="synthetic", has_device_speed=True)
    x = y = 0.0
    dist = 0.0
    for t, (v, h) in enumerate(zip(speed, heading)):
        tr.time.append(1_700_000_000 + t)
        tr.lat.append(lat + y / 111_320)
        tr.lon.append(lon + x / (111_320 * math.cos(math.radians(lat))))
        tr.speed.append(v)
        tr.dist.append(dist)
        tr.alt.append(0.0)
        tr.hr.append(120)
        x += v * math.sin(math.radians(h))
        y += v * math.cos(math.radians(h))
        dist += v
    return tr


def test_flying_hysteresis():
    sp = np.array([0, 5, 12, 14, 20, 11, 9, 20, 20, 20, 20, 20, 5, 0], dtype=float)
    fly = detect_flying(sp, Params(min_flight_s=1))
    assert fly.tolist() == [0, 0, 0, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0, 0]


def test_synthetic_session():
    a = analyse(resample(synthetic_track()))
    s = a.stats
    # three flights: ~34-174 (containing a clean jibe), ~234-302, ~334-402
    assert s.flights == 3
    assert 230 <= s.flying_s <= 300
    # wind: drifting east -> wind from the west (270)
    assert a.wind.from_deg is not None
    assert abs(((a.wind.from_deg - 270) + 180) % 360 - 180) < 25
    # two jibe attempts: one clean, one full stop
    assert s.jibes_total == 2
    assert s.jibes_clean == 1
    assert s.jibes_stop == 1
    assert s.tacks_total == 0
    clean = next(t for t in a.turns if t.outcome == "clean")
    stop = next(t for t in a.turns if t.outcome == "stop")
    assert stop.grade == "F" and stop.score == 0
    assert 0.85 <= clean.retention <= 1.1
    assert 14 <= clean.min_speed_kmh <= 18
    assert clean.grade in ("A", "B")
    # full stops: one in the jibe (~59 s recovery) and one on a straight (~37 s);
    # both longer than 30 s so both "long stop".  The final stop is the session end.
    assert s.stops == 2
    assert s.stops_in_turns == 1
    assert s.stops_long == 2
    assert 45 <= a.stops[0].recovery_s <= 70
    assert 30 <= a.stops[1].recovery_s <= 45
    assert a.flights[-1].ended_by == "session end"
    # a generous water-stop threshold flips both to quick restarts
    b = analyse(resample(synthetic_track()), Params(water_stop_s=90))
    assert b.stats.stops_quick == 2


def test_user_wind_override_turns_jibes_into_tacks():
    a = analyse(resample(synthetic_track()), Params(wind_from_deg=135.0))
    assert a.stats.tacks_total == 2
    assert a.stats.jibes_total == 0


@pytest.mark.skipif(not (DATA / "activity_24669790521.tcx").exists(), reason="sample export not present")
def test_real_export_matches_garmin_summary():
    tr = resample(load_track(DATA / "activity_24669790521.tcx"))
    a = analyse(tr)
    summary = read_csv_summary(DATA / "activity_24669790521.csv")
    assert abs(a.stats.recorded_duration_s - summary.duration_s) <= 2
    assert a.stats.duration_s <= a.stats.recorded_duration_s
    assert abs(a.stats.recorded_distance_m - summary.distance_m) < 20
    assert abs(a.stats.max_speed_kmh - summary.max_speed_kmh) < 0.5
    assert a.stats.flights > 5
    assert a.stats.jibes_total > 10
    assert a.stats.stops >= a.stats.jibes_stop
    assert a.stats.stops == a.stats.stops_quick + a.stats.stops_long


@pytest.mark.skipif(not (DATA / "activity_24669790521.gpx").exists(), reason="sample export not present")
def test_gpx_derived_speed_agrees_with_tcx():
    gpx = analyse(resample(load_track(DATA / "activity_24669790521.gpx")))
    tcx = analyse(resample(load_track(DATA / "activity_24669790521.tcx")))
    assert gpx.track.speed_source == "derived"
    assert abs(gpx.stats.flying_s - tcx.stats.flying_s) / tcx.stats.flying_s < 0.08
    assert abs(gpx.stats.jibes_total - tcx.stats.jibes_total) <= 3


def test_kml_and_csv_are_rejected_with_a_hint(tmp_path):
    for ext in (".kml", ".csv"):
        f = tmp_path / f"x{ext}"
        f.write_text("x")
        with pytest.raises(ValueError):
            load_track(f)


def test_server_analyses_an_upload():
    from wingstats.server import analyse_upload, _parse_multipart, _params

    body = (b"--XX\r\nContent-Disposition: form-data; name=\"water_stop\"\r\n\r\n45\r\n"
            b"--XX\r\nContent-Disposition: form-data; name=\"wind_from\"\r\n\r\n\r\n"
            b"--XX\r\nContent-Disposition: form-data; name=\"file\"; filename=\"ride.tcx\"\r\n"
            b"Content-Type: application/octet-stream\r\n\r\n<x/>\r\n--XX--\r\n")
    fields, upload = _parse_multipart("multipart/form-data; boundary=XX", body)
    assert upload == ("ride.tcx", b"<x/>")
    p = _params(fields)
    assert p.water_stop_s == 45 and p.wind_from_deg is None
    with pytest.raises(ValueError):
        analyse_upload("x.kml", b"<kml/>", p)
    if (DATA / "activity_24669790521.tcx").exists():
        page = analyse_upload("ride.tcx", (DATA / "activity_24669790521.tcx").read_bytes(), p)
        assert "Analyse another file" in page and "longer than 45 s" in page or "within 45 s" in page


# ----------------------------------------------------------------------------
# GPS cleaning and the turn-based wind estimate
# ----------------------------------------------------------------------------

def test_gps_glitch_is_removed():
    """A dropout followed by a 100 m position jump that bounces back must not
    create a speed spike or a phantom turn."""
    from wingstats.track import resample
    raw = synthetic_track()
    clean = resample(raw)
    glitched = synthetic_track()
    lat_m = 1 / 111_320
    for k, dlat in ((60, 0.0), (61, 100 * lat_m), (62, 110 * lat_m), (63, 15 * lat_m)):
        glitched.lat[k] += dlat  # 100 m north and back within 3 s while flying at 22 km/h
    tr = resample(glitched)
    assert tr.gps_points_total == len(glitched.time)
    assert 2 <= tr.gps_points_dropped <= 3
    assert tr.speed.max() < 30, tr.speed.max()
    a_clean, a_glitch = analyse(clean), analyse(tr)
    assert a_glitch.stats.turns_total == a_clean.stats.turns_total


def test_wind_from_turn_headings_beats_drift():
    """Six jibes all turning through 135° fix downwind at 135° even though the
    rider drifts east (which the old drift method would read as wind from the west)."""
    from wingstats.track import resample
    speed, heading = [], []
    t = 0
    def seg(n, v, h):
        for _ in range(n):
            speed.append(v / 3.6); heading.append(h)
    seg(40, 1.44, 90.0)                       # drifting east while stopped
    seg(10, 22, 45.0)
    for k in range(6):
        h0, h1 = (45.0, 225.0) if k % 2 == 0 else (225.0, 45.0)
        seg(60, 22, h0)
        for i in range(12):                   # a clean jibe through 135°
            f = i / 12
            speed.append((22 - 5 * math.sin(math.pi * f)) / 3.6)
            heading.append(h0 + (180 if h0 < h1 else -180) * f)
    seg(60, 22, 45.0)  # the sixth jibe ends on the 45° reach
    seg(3, 5, 45.0)
    seg(60, 1.44, 90.0)
    lat, lon = 57.63, 11.77
    raw = RawTrack(name="six jibes", has_device_speed=True)
    x = y = 0.0
    for i, (v, h) in enumerate(zip(speed, heading)):
        raw.time.append(1_700_000_000 + i)
        raw.lat.append(lat + y / 111_320)
        raw.lon.append(lon + x / (111_320 * math.cos(math.radians(lat))))
        raw.speed.append(v); raw.alt.append(None); raw.dist.append(None); raw.hr.append(None)
        x += v * math.sin(math.radians(h)); y += v * math.cos(math.radians(h))
    a = analyse(resample(raw), Params(trim=False))  # keep the misleading drift before and after
    assert a.wind.method == "turn headings"
    assert a.wind.turns_used >= 5 and a.wind.turn_agreement > 0.95
    assert abs(((a.wind.downwind_deg - 135) + 180) % 360 - 180) < 15
    assert a.wind.drift_downwind_deg is not None and abs(((a.wind.drift_downwind_deg - 90) + 180) % 360 - 180) < 15
    assert a.stats.jibes_total == 6 and a.stats.tacks_total == 0


@pytest.mark.parametrize("name", sorted(p.name for p in DATA.glob("*.gpx")))
def test_real_sessions_are_jibes_with_sane_speeds(name):
    """The rider never tacks; every real export must come out as jibes, with the
    GPS spikes (60-120 km/h) cleaned away."""
    from wingstats.track import resample
    a = analyse(resample(load_track(DATA / name)))
    s = a.stats
    assert s.max_speed_kmh < 35, s.max_speed_kmh
    assert s.turns_total >= 10
    assert s.tacks_total <= 1, (s.tacks_total, s.jibes_total)
    assert a.wind.method == "turn headings" and a.wind.confidence in ("high", "medium")


def test_session_is_trimmed_to_the_surf():
    """Walking out before the first take-off and back after the last landing is cut."""
    from wingstats.track import resample
    raw = synthetic_track()
    walk_in, walk_out = 120, 90
    t_first = raw.time[0]
    # prepend a walk/swim out at 4 km/h and append the way back
    pre = RawTrack(name="walk", has_device_speed=True)
    for i in range(walk_in):
        pre.time.append(t_first - walk_in + i); pre.lat.append(raw.lat[0] - (walk_in - i) * 1.1 / 111_320)
        pre.lon.append(raw.lon[0]); pre.speed.append(4 / 3.6); pre.alt.append(None); pre.dist.append(None); pre.hr.append(None)
    for name in ("time", "lat", "lon", "speed", "alt", "dist", "hr"):
        setattr(raw, name, getattr(pre, name) + getattr(raw, name))
    t_last = raw.time[-1]
    for i in range(1, walk_out + 1):
        raw.time.append(t_last + i); raw.lat.append(raw.lat[-1] + 1.1 / 111_320); raw.lon.append(raw.lon[-1])
        raw.speed.append(4 / 3.6); raw.alt.append(None); raw.dist.append(None); raw.hr.append(None)
    full = analyse(resample(raw), Params(trim=False))
    a = analyse(resample(raw))
    s = a.stats
    assert s.recorded_duration_s == full.stats.duration_s
    assert s.trimmed_start_s >= walk_in + 25        # the walk plus the stopped drift before the run-up
    assert s.trimmed_end_s >= walk_out + 40         # the drift after the last stop plus the walk back
    assert abs(s.duration_s + s.trimmed_start_s + s.trimmed_end_s - s.recorded_duration_s) <= 1
    assert s.flying_s == full.stats.flying_s and s.flights == full.stats.flights
    assert s.jibes_total == full.stats.jibes_total
    # untrimmed, the walk back makes the final stop look like a fall; trimmed it is the session end
    assert s.stops == 2 and full.stats.stops == 3
    assert s.flying_pct > full.stats.flying_pct
    assert a.flights[-1].ended_by == "session end"
