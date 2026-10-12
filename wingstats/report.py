"""Plain-text and JSON output."""
from __future__ import annotations

import json
from typing import Optional

from .analysis import Analysis, to_dict
from .parsers import LapSummary


def mmss(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def compass(deg: Optional[float]) -> str:
    if deg is None:
        return "?"
    names = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return names[int((deg + 11.25) // 22.5) % 16]


def text_report(a: Analysis, csv_summary: Optional[LapSummary] = None) -> str:
    s = a.stats
    w = a.wind
    L: list[str] = []
    add = L.append
    title = s.name or "Wingfoil session"
    add("=" * 72)
    add(f"{title}  -  {s.start_utc}")
    add(f"source: {s.source}  device: {s.device or '?'}  speed: {s.speed_source}")
    if s.gps_points_dropped:
        add(f"GPS cleaning: {s.gps_points_dropped} of {s.gps_points_total} points rejected "
            f"(implied more than {s.gps_speed_cap_kmh:.0f} km/h) and bridged by interpolation")
    add("=" * 72)
    add("")
    add("SESSION")
    add(f"  Duration            {mmss(s.duration_s):>10}   distance {s.distance_m/1000:.2f} km")
    add(f"  Moving              {mmss(s.moving_s):>10}   avg {s.avg_speed_moving_kmh:.1f} km/h, max {s.max_speed_kmh:.1f} km/h")
    add(f"  In the water        {mmss(s.stopped_s):>10}   (< {s.params.stop_kmh:.0f} km/h)")
    add(f"  Taxiing on the board{mmss(s.taxi_s):>10}   (moving but not flying)")
    if s.hr_avg is not None:
        add(f"  Heart rate          avg {s.hr_avg:.0f}  max {s.hr_max:.0f} bpm")
    if csv_summary:
        add(f"  Garmin CSV summary  {mmss(csv_summary.duration_s)}  {csv_summary.distance_m/1000:.2f} km  "
            f"max {csv_summary.max_speed_kmh:.1f} km/h  (cross-check)")
    add("")
    add("TIME IN THE AIR (foiling)")
    add(f"  Flying time         {mmss(s.flying_s):>10}   {s.flying_pct:.1f}% of the session")
    add(f"  Flying distance     {s.flying_distance_m/1000:10.2f} km")
    add(f"  Flights             {s.flights:>10}   mean {mmss(s.mean_flight_s)}, median {mmss(s.median_flight_s)}")
    add(f"  Longest flight      {mmss(s.longest_flight_s):>10}   {s.longest_flight_m/1000:.2f} km")
    add(f"  Avg speed flying    {s.avg_speed_flying_kmh:10.1f} km/h")
    add("")
    add("JIBES")
    add(f"  Attempts            {s.jibes_total:>10}")
    add(f"  Clean (stayed up)   {s.jibes_clean:>10}   {s.jibe_success_pct:.0f}% success")
    add(f"  Touched down        {s.jibes_touchdown:>10}   (lost flight, kept moving)")
    add(f"  Full stop           {s.jibes_stop:>10}   (came to a standstill)")
    add(f"  Speed retention     {s.jibe_avg_retention_pct:9.0f}%   exit/entry speed, jibes without a full stop")
    add(f"  Avg quality score   {s.jibe_avg_score:10.0f}   /100 (a full stop scores 0)")
    add(f"  Turning right       {s.jibes_right_clean}/{s.jibes_right} clean      turning left {s.jibes_left_clean}/{s.jibes_left} clean")
    if s.tacks_total:
        add(f"  Tacks               {s.tacks_total:>10}   {s.tacks_clean} clean")
    add("")
    add("FULL STOPS  (speed dropped below %.0f km/h after a flight)" % s.params.stop_kmh)
    add(f"  Full stops          {s.stops:>10}   {s.stops_per_hour:.1f} per hour")
    add(f"  ...during a jibe    {s.stops_in_turns:>10}")
    add(f"  ...on a straight    {s.stops - s.stops_in_turns:>10}")
    add(f"  Quick restart       {s.stops_quick:>10}   flying again within {s.params.water_stop_s} s (probably stayed on the board)")
    add(f"  Long stop           {s.stops_long:>10}   longer than {s.params.water_stop_s} s (probably in the water)")
    add("  The watch cannot tell water from board; the split is by recovery time (--water-stop).")
    add("")
    add("WIND")
    if w.from_deg is None:
        add(f"  Unknown ({w.method}); turns reported as jibes.")
    else:
        add(f"  From {w.from_deg:.0f}° ({compass(w.from_deg)})  downwind {w.downwind_deg:.0f}°  "
            f"via {w.method}, confidence {w.confidence}")
        if w.method == "turn headings":
            add(f"  Based on {w.turns_used} turns whose mid-turn headings agree {w.turn_agreement * 100:.0f}%")
        if w.method != "user" and w.drift_downwind_deg is not None:
            add(f"  Cross-check: drifted {w.drift_m:.0f} m over {mmss(w.drift_s)} while stopped, "
                f"towards {w.drift_downwind_deg:.0f}° ({compass(w.drift_downwind_deg)})")
    if w.reach_headings_deg:
        add("  Reach headings      " + "  ".join(f"{h:.0f}° ({compass(h)})" for h in w.reach_headings_deg))
    add("")
    add("JIBE LIST")
    add("   #   time   dir    kind   entry   min  exit  +10s  retain score grade outcome")
    for t in a.turns:
        flag = " (aborted)" if t.aborted else ""
        add(f"  {t.idx:2d}  {mmss(t.mid_s):>6} {t.direction:5s} {t.kind:6s} "
            f"{t.entry_speed_kmh:5.1f} {t.min_speed_kmh:5.1f} {t.exit_speed_kmh:5.1f} {t.speed_after_kmh:5.1f} "
            f"{t.retention*100:5.0f}% {t.score:5.0f}   {t.grade}   {t.outcome}{flag}")
    add("")
    add("FULL STOP LIST")
    add("   #   time   speed before  stopped  back flying   likely          where")
    for f in a.stops:
        where = f"during jibe {f.in_turn}" if f.in_turn else "on a straight"
        rec = mmss(f.recovery_s) if f.recovery_s is not None else "never"
        add(f"  {f.idx:2d}  {mmss(f.time_s):>6}  {f.speed_before_kmh:9.1f}     {mmss(f.stopped_s):>6}  {rec:>10}   {f.likely:14s}  {where}")
    add("")
    add("FLIGHT LIST")
    add("   #   start    end   length   dist   max    ended by")
    for f in a.flights:
        add(f"  {f.idx:2d}  {mmss(f.start_s):>6} {mmss(f.end_s):>6}  {mmss(f.duration_s):>6}  {f.distance_m:5.0f}m  "
            f"{f.max_speed_kmh:4.1f}   {f.ended_by}")
    add("")
    p = s.params
    add(f"Thresholds: flying >= {p.fly_on_kmh:.0f} km/h (lost < {p.fly_off_kmh:.0f}), stopped < {p.stop_kmh:.0f} km/h, "
        f"turn >= {p.turn_min_deg:.0f}°, stop window {p.fall_window_s} s, water stop > {p.water_stop_s} s")
    return "\n".join(L)


def json_report(a: Analysis, csv_summary: Optional[LapSummary] = None) -> str:
    d = to_dict(a)
    if csv_summary:
        d["garmin_csv_summary"] = csv_summary.__dict__
    return json.dumps(d, indent=2, default=_json_default)


def _json_default(o):
    import numpy as np
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if hasattr(o, "__dict__"):
        return o.__dict__
    return str(o)
