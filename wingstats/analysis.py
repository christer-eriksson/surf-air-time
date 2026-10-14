"""Detect flights, turns (jibes / tacks), full stops and derive session stats.

Everything works on the 1 Hz ``Track``.  Thresholds live in ``Params`` so
they can be tuned per rider / foil from the command line.

Terminology
-----------
flying      board out of the water, only the foil submerged.  Detected from
            speed with hysteresis: starts above ``fly_on_kmh``, ends when
            speed drops below ``fly_off_kmh``.
touchdown   flight lost but speed stayed above ``stop_kmh``.
full stop   flight lost and speed went below ``stop_kmh`` shortly after.
            The watch cannot tell a fall into the water from standing still
            on the board, so stops are split by recovery time instead:
            "quick restart" (flying again within ``water_stop_s``) versus
            "long stop" (likely in the water).
turn        heading change >= ``turn_min_deg`` at a sustained rate.
jibe        a turn whose mid-turn heading points downwind (bearing away).
tack        a turn whose mid-turn heading points upwind.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np

from .track import Track, smooth, unwrap_heading, ang_diff


@dataclass
class Params:
    fly_on_kmh: float = 13.0        # speed needed to count as flying
    fly_off_kmh: float = 10.0       # speed below which flight is lost
    min_flight_s: int = 5           # shorter "flights" are noise / a bounce
    stop_kmh: float = 3.0           # below this you are in the water
    fall_window_s: int = 15         # a stop within this many s after losing flight = full stop
    water_stop_s: int = 30          # recovery longer than this = long stop (likely in the water)
    turn_rate_deg_s: float = 5.0    # heading rate that marks a turn in progress
    turn_edge_rate_deg_s: float = 2.0  # rate used to extend the turn edges
    turn_min_deg: float = 100.0     # total heading change for a full turn
    aborted_turn_min_deg: float = 55.0  # a smaller turn ending in a full stop still counts as an attempt
    turn_merge_gap_s: int = 4       # merge same-direction turn pieces closer than this
    max_turn_jump_deg: float = 90.0  # a heading jump above this in one second is a crash flip, which ends the turn
    entry_window_s: int = 6         # speed averaging window before the turn
    exit_window_s: int = 6          # speed averaging window after the turn
    after_s: int = 10               # "speed regained" look-ahead after the turn
    wind_from_deg: Optional[float] = None  # override wind direction (degrees FROM)
    trim: bool = True               # cut the walk/swim out before the first flight and back after the last
    still_min_s: int = 10           # a standstill at least this long that is not a full stop is still marked
    trim_lead_s: int = 30           # keep at most this much moving time before the first take-off
    trim_tail_s: int = 30           # and at most this much after the last landing


@dataclass
class Flight:
    idx: int
    start_s: float
    end_s: float
    duration_s: float
    distance_m: float
    max_speed_kmh: float
    avg_speed_kmh: float
    ended_by: str = ""           # "full stop", "touchdown", "session end"


@dataclass
class Turn:
    idx: int
    start_s: float
    end_s: float
    mid_s: float
    duration_s: float
    heading_change_deg: float    # signed; + = clockwise (turning right)
    direction: str               # "right" / "left"
    entry_heading_deg: float
    exit_heading_deg: float
    mid_heading_deg: float
    kind: str                    # "jibe" / "tack" / "turn" (unknown wind)
    entry_speed_kmh: float
    min_speed_kmh: float
    exit_speed_kmh: float
    speed_after_kmh: float       # mean speed around `after_s` after the turn
    retention: float             # exit / entry
    min_ratio: float             # min / entry
    score: float                 # 0-100 quality score (0 for a full stop)
    grade: str                   # A/B/C/D/F
    outcome: str                 # "clean" / "touchdown" / "stop"
    aborted: bool = False        # heading change < turn_min_deg, ended in a full stop
    lat: float = 0.0
    lon: float = 0.0


@dataclass
class Stop:
    """A full stop (speed below ``stop_kmh``) shortly after a flight."""
    idx: int
    time_s: float
    flight_end_s: float
    speed_before_kmh: float
    stopped_s: float             # contiguous seconds below stop_kmh
    recovery_s: Optional[float]  # seconds until flying again (None if never)
    likely: str                  # "quick restart" / "long stop"
    in_turn: Optional[int] = None  # turn idx if the stop belongs to a turn
    lat: float = 0.0
    lon: float = 0.0


@dataclass
class Standstill:
    """Standing still (below ``stop_kmh``) for a while without a flight just before it.

    A full stop is the standstill right after a flight (a crash or a planned stop).
    These are the others: resting, waiting for wind, swimming after taxiing.
    """
    idx: int
    start_s: float
    duration_s: float
    lat: float = 0.0
    lon: float = 0.0


@dataclass
class WindEstimate:
    from_deg: Optional[float]
    downwind_deg: Optional[float]
    method: str
    drift_m: float
    drift_s: float
    confidence: str               # "high" / "medium" / "low" / "none"
    reach_headings_deg: list[float] = field(default_factory=list)
    turns_used: int = 0           # turns the heading-based estimate rests on
    turn_agreement: float = 0.0   # 0-1, how tightly those turns agree
    drift_downwind_deg: Optional[float] = None  # the drift-based estimate, kept as a cross-check


@dataclass
class SessionStats:
    name: str
    sport: str
    device: str
    source: str
    speed_source: str
    start_utc: str
    duration_s: float
    distance_m: float
    moving_s: float
    stopped_s: float
    taxi_s: float
    flying_s: float
    flying_pct: float
    flying_distance_m: float
    flights: int
    longest_flight_s: float
    longest_flight_m: float
    mean_flight_s: float
    median_flight_s: float
    max_speed_kmh: float
    avg_speed_moving_kmh: float
    avg_speed_flying_kmh: float
    turns_total: int
    jibes_total: int
    jibes_clean: int
    jibes_touchdown: int
    jibes_stop: int
    jibe_success_pct: float
    jibe_avg_retention_pct: float
    jibe_avg_score: float
    jibes_right: int
    jibes_right_clean: int
    jibes_left: int
    jibes_left_clean: int
    tacks_total: int
    tacks_clean: int
    stops: int
    stops_in_turns: int
    stops_quick: int
    stops_long: int
    stops_per_hour: float
    standstills: int
    standstill_s: float
    hr_avg: Optional[float]
    hr_max: Optional[float]
    wind: WindEstimate
    params: Params
    gps_points_total: int = 0
    gps_points_dropped: int = 0
    gps_speed_cap_kmh: float = 0.0
    recorded_duration_s: float = 0.0  # length of the recording before trimming
    recorded_distance_m: float = 0.0  # distance of the recording before trimming
    trimmed_start_s: float = 0.0      # cut before the first take-off
    trimmed_end_s: float = 0.0        # cut after the last landing


@dataclass
class Analysis:
    track: Track
    params: Params
    flying: np.ndarray            # bool per second
    flights: list[Flight]
    turns: list[Turn]
    stops: list[Stop]
    wind: WindEstimate
    stats: SessionStats
    standstills: list[Standstill] = field(default_factory=list)


# ----------------------------------------------------------------------------
# Flights
# ----------------------------------------------------------------------------

def detect_flying(speed: np.ndarray, p: Params) -> np.ndarray:
    n = len(speed)
    fly = np.zeros(n, dtype=bool)
    on = False
    for i in range(n):
        if not on and speed[i] >= p.fly_on_kmh:
            on = True
        elif on and speed[i] < p.fly_off_kmh:
            on = False
        fly[i] = on
    # remove flights shorter than min_flight_s
    for s, e in _segments(fly):
        if e - s < p.min_flight_s:
            fly[s:e] = False
    return fly


def _segments(mask: np.ndarray) -> list[tuple[int, int]]:
    """Half-open [start, end) runs of True."""
    segs = []
    s = None
    for i, v in enumerate(mask):
        if v and s is None:
            s = i
        elif not v and s is not None:
            segs.append((s, i))
            s = None
    if s is not None:
        segs.append((s, len(mask)))
    return segs


def build_flights(tr: Track, fly: np.ndarray) -> list[Flight]:
    out = []
    for k, (s, e) in enumerate(_segments(fly)):
        sp = tr.speed[s:e]
        out.append(Flight(
            idx=k + 1, start_s=float(tr.t[s]), end_s=float(tr.t[e - 1]), duration_s=float(e - s),
            distance_m=float(tr.dist[e - 1] - tr.dist[s]), max_speed_kmh=float(sp.max()),
            avg_speed_kmh=float(sp.mean()),
        ))
    return out


# ----------------------------------------------------------------------------
# Full stops
# ----------------------------------------------------------------------------

def detect_stops(tr: Track, fly: np.ndarray, flights: list[Flight], p: Params) -> list[Stop]:
    n = len(tr)
    stops: list[Stop] = []
    for fl in flights:
        e = int(fl.end_s) + 1  # first non-flying second
        if e >= n:
            fl.ended_by = "session end"
            continue
        look = tr.speed[e:min(n, e + p.fall_window_s)]
        below = np.where(look < p.stop_kmh)[0]
        if not len(below):
            fl.ended_by = "touchdown"
            continue
        stop_i = e + int(below[0])
        later = np.where(fly[stop_i:])[0]
        if not len(later):
            # No more flying.  If the rider never really moved again either,
            # this is the end of the session, not a stop.
            taxi_after = int((tr.speed[stop_i:] >= p.stop_kmh).sum())
            if taxi_after < 10:
                fl.ended_by = "session end"
                continue
        fl.ended_by = "full stop"
        j = stop_i
        while j < n and tr.speed[j] < p.stop_kmh:
            j += 1
        recovery = float(later[0]) if len(later) else None
        likely = "long stop" if recovery is None or recovery > p.water_stop_s else "quick restart"
        stops.append(Stop(
            idx=len(stops) + 1, time_s=float(tr.t[stop_i]), flight_end_s=fl.end_s,
            speed_before_kmh=float(tr.speed[max(0, e - 6):e].mean()),
            stopped_s=float(j - stop_i), recovery_s=recovery, likely=likely,
            lat=float(tr.lat[stop_i]), lon=float(tr.lon[stop_i]),
        ))
    return stops


# ----------------------------------------------------------------------------
# Wind
# ----------------------------------------------------------------------------

def _drift(tr: Track, p: Params) -> tuple[Optional[float], float, float]:
    """Direction the rider drifts while stopped (downwind, if the current is weak)."""
    stopped = tr.speed < p.stop_kmh
    both = stopped[1:] & stopped[:-1]
    dx = float(np.diff(tr.x)[both].sum())
    dy = float(np.diff(tr.y)[both].sum())
    drift = float(np.hypot(dx, dy))
    secs = float(both.sum())
    if secs < 30 or drift < 15:
        return None, drift, secs
    return float(np.degrees(np.arctan2(dx, dy)) % 360), drift, secs


def _circular_mean(deg: list[float]) -> tuple[float, float]:
    """Mean direction and resultant length (1 = all identical, 0 = spread out)."""
    r = np.radians(deg)
    sx, sy = float(np.sin(r).sum()), float(np.cos(r).sum())
    return float(np.degrees(np.arctan2(sx, sy)) % 360), float(np.hypot(sx, sy) / len(deg))


def detect_standstills(tr: Track, stops: list[Stop], p: Params) -> list[Standstill]:
    """Standstills of at least ``still_min_s`` that are not the full stop after a flight."""
    out: list[Standstill] = []
    stop_times = [f.time_s for f in stops]
    for s, e in _segments(tr.speed < p.stop_kmh):
        if e - s < p.still_min_s:
            continue
        if any(tr.t[s] - 1 <= st <= tr.t[e - 1] + 1 for st in stop_times):
            continue  # that one is a full stop already
        mid = (s + e) // 2
        out.append(Standstill(idx=len(out) + 1, start_s=float(tr.t[s]), duration_s=float(e - s),
                              lat=float(tr.lat[mid]), lon=float(tr.lon[mid])))
    return out


def estimate_wind(tr: Track, fly: np.ndarray, p: Params, turns: Optional[list] = None) -> WindEstimate:
    """Where is downwind?

    Wingfoil turns are nearly always jibes, i.e. the rider passes through the
    downwind direction mid-turn, so the mean mid-turn heading of the session's
    turns is the most reliable downwind estimate.  Drift while standing still
    (used when there are too few turns) is easily fooled by current and by the
    short, noisy stops of a good session.
    """
    reach = _reach_headings(tr, fly)
    drift_down, drift_m, drift_s = _drift(tr, p)
    if p.wind_from_deg is not None:
        return WindEstimate(from_deg=p.wind_from_deg % 360, downwind_deg=(p.wind_from_deg + 180) % 360,
                            method="user", drift_m=drift_m, drift_s=drift_s, confidence="high",
                            reach_headings_deg=reach, drift_downwind_deg=drift_down)
    cands = [t.mid_heading_deg for t in (turns or [])
             if not t.aborted and p.turn_min_deg <= abs(t.heading_change_deg) <= 270]
    if len(cands) >= 3:
        down, agreement = _circular_mean(cands)
        conf = "high" if agreement >= 0.9 and len(cands) >= 5 else "medium" if agreement >= 0.75 else "low"
        if len(reach) == 2:
            bis = (reach[0] + ang_diff(reach[0], reach[1]) / 2.0) % 360
            off = min(abs(ang_diff(down, bis)), abs(ang_diff(down, (bis + 180) % 360)))
            if off > 45:
                conf = {"high": "medium", "medium": "low", "low": "low"}[conf]
        return WindEstimate(from_deg=(down + 180) % 360, downwind_deg=down, method="turn headings",
                            drift_m=drift_m, drift_s=drift_s, confidence=conf, reach_headings_deg=reach,
                            turns_used=len(cands), turn_agreement=agreement, drift_downwind_deg=drift_down)
    if drift_down is None:
        return WindEstimate(None, None, "drift (insufficient)", drift_m, drift_s, "none", reach,
                            drift_downwind_deg=None)
    down = drift_down
    conf = "medium"
    if len(reach) == 2:
        bis = (reach[0] + ang_diff(reach[0], reach[1]) / 2.0) % 360
        off = min(abs(ang_diff(down, bis)), abs(ang_diff(down, (bis + 180) % 360)))
        conf = "high" if off <= 35 else "medium" if off <= 60 else "low"
    if drift_m < 30:
        conf = "low"
    return WindEstimate(from_deg=(down + 180) % 360, downwind_deg=down, method="drift while stopped",
                        drift_m=drift_m, drift_s=drift_s, confidence=conf, reach_headings_deg=reach,
                        drift_downwind_deg=drift_down)


def _reach_headings(tr: Track, fly: np.ndarray) -> list[float]:
    """The two dominant headings while flying (the two reaches)."""
    h = tr.heading[fly & ~np.isnan(tr.heading)]
    if len(h) < 30:
        return []
    bins = np.arange(0, 361, 10)
    cnt, _ = np.histogram(h, bins=bins)
    order = np.argsort(cnt)[::-1]
    peaks: list[float] = []
    for b in order:
        centre = float(bins[b] + 5)
        if all(abs(ang_diff(centre, q)) > 60 for q in peaks):
            peaks.append(centre)
        if len(peaks) == 2:
            break
    return peaks


# ----------------------------------------------------------------------------
# Turns
# ----------------------------------------------------------------------------

def detect_turns(tr: Track, fly: np.ndarray, stops: list[Stop], p: Params) -> list[Turn]:
    n = len(tr)
    u = smooth(unwrap_heading(tr.heading), 3)
    rate = np.gradient(u)                       # deg/s at 1 Hz
    moving = tr.speed >= 4.0                     # heading is meaningless slower than this
    active = (np.abs(rate) >= p.turn_rate_deg_s) & moving

    pieces = _segments(active)
    # merge nearby same-direction pieces
    merged: list[tuple[int, int]] = []
    for s, e in pieces:
        if merged:
            ps, pe = merged[-1]
            same_dir = np.sign(u[e - 1] - u[s]) == np.sign(u[pe - 1] - u[ps])
            if s - pe <= p.turn_merge_gap_s and same_dir:
                merged[-1] = (ps, e)
                continue
        merged.append((s, e))

    stop_times = np.array([f.time_s for f in stops]) if stops else np.array([])
    raw_jump = np.abs(np.diff(unwrap_heading(tr.heading), prepend=np.nan))  # unsmoothed 1 s heading change
    turns: list[Turn] = []
    for k, (s, e) in enumerate(merged):
        # extend edges while the heading is still changing
        sign = np.sign(u[e - 1] - u[s]) or 1.0
        while s > 0 and moving[s - 1] and sign * rate[s - 1] >= p.turn_edge_rate_deg_s:
            s -= 1
        while e < n and moving[e - 1] and sign * rate[e - 1] >= p.turn_edge_rate_deg_s:
            e += 1
        e = min(e, n)
        # A board cannot turn 90° in a second at foiling speed: such a jump is the
        # heading flipping as the rider crashes.  The turn, if any, ended before it.
        # The smoothed heading already feels the flip one sample early, so cut one before it.
        for j in range(s + 1, e):
            if raw_jump[j] > p.max_turn_jump_deg:
                e = j - 1
                break
        if e - s < 3:
            continue  # a real turn takes longer than that
        change = float(u[e - 1] - u[s])
        if abs(change) < p.aborted_turn_min_deg:
            continue
        # full stop shortly after, and before the next turn begins?
        stop_idx = None
        if len(stop_times):
            d = stop_times - tr.t[e - 1]
            ok = (d >= -2) & (d <= p.fall_window_s + 2)
            if k + 1 < len(merged):
                ok &= stop_times < tr.t[merged[k + 1][0]]
            cand = np.where(ok)[0]
            if len(cand):
                stop_idx = int(cand[0])
        aborted = abs(change) < p.turn_min_deg
        if aborted and stop_idx is None:
            continue  # a wobble, not a turn attempt
        entry = tr.speed[max(0, s - p.entry_window_s):s]
        if len(entry) == 0 or entry.mean() < p.fly_off_kmh:
            continue  # not a foiling turn attempt
        turns.append(_make_turn(tr, fly, p, s, e, change, stop_idx, aborted))

    # number them and link stops
    for k, t in enumerate(turns):
        t.idx = k + 1
    for t in reversed(turns):  # a stop belongs to the latest turn before it
        if t.outcome == "stop":
            for f in stops:
                if t.start_s - 2 <= f.time_s <= t.end_s + p.fall_window_s + 2 and f.in_turn is None:
                    f.in_turn = t.idx
                    break
    return turns


def _make_turn(tr, fly, p, s, e, change, stop_idx, aborted) -> Turn:
    n = len(tr)
    entry = float(tr.speed[max(0, s - p.entry_window_s):s].mean())
    exit_sl = tr.speed[e:min(n, e + p.exit_window_s)]
    exit_v = float(exit_sl.mean()) if len(exit_sl) else 0.0
    core = tr.speed[s:min(n, e + 5)]
    min_v = float(core.min()) if len(core) else 0.0
    a0 = min(n - 1, e + p.after_s - 2)
    a1 = min(n, e + p.after_s + 3)
    after = float(tr.speed[a0:a1].mean()) if a1 > a0 else 0.0

    if stop_idx is not None:
        outcome = "stop"
    elif fly[s:min(n, e + 5)].all():
        outcome = "clean"
    else:
        outcome = "touchdown"

    retention = exit_v / entry if entry > 0 else 0.0
    min_ratio = min_v / entry if entry > 0 else 0.0
    if outcome == "stop":
        score = 0.0
    else:
        score = 100.0 * min(1.0, 0.6 * min(retention, 1.1) + 0.4 * min(min_ratio, 1.0))
        if outcome == "touchdown":
            score = min(score, 54.0)
    grade = "F" if outcome == "stop" else "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D"

    mid = (s + e) // 2
    entry_h = float(tr.heading[s] if not np.isnan(tr.heading[s]) else _nearest_heading(tr.heading, s))
    exit_h = float((entry_h + change) % 360)
    # Heading at the mid-point of the turn, used to tell jibes from tacks.  An
    # aborted turn never got there, so assume the rider meant a ~180° turn; a
    # turn beyond 180° (a spin after a touchdown) is judged by its first half.
    half = np.sign(change) * 90.0 if aborted else np.sign(change) * min(abs(change) / 2, 90.0)
    mid_h = float((entry_h + half) % 360)
    kind = "turn"  # set by classify_turns once the wind is known

    return Turn(
        idx=0, start_s=float(tr.t[s]), end_s=float(tr.t[e - 1]), mid_s=float(tr.t[mid]),
        duration_s=float(e - s), heading_change_deg=change, direction="right" if change > 0 else "left",
        entry_heading_deg=entry_h, exit_heading_deg=exit_h, mid_heading_deg=mid_h, kind=kind,
        entry_speed_kmh=entry, min_speed_kmh=min_v, exit_speed_kmh=exit_v, speed_after_kmh=after,
        retention=retention, min_ratio=min_ratio, score=score, grade=grade, outcome=outcome,
        aborted=aborted, lat=float(tr.lat[mid]), lon=float(tr.lon[mid]),
    )


def classify_turns(turns: list[Turn], wind: WindEstimate) -> None:
    """A turn whose mid-point heading is within 90° of downwind is a jibe, else a tack."""
    for t in turns:
        if wind.downwind_deg is None:
            t.kind = "turn"
        else:
            t.kind = "jibe" if abs(ang_diff(t.mid_heading_deg, wind.downwind_deg)) <= 90 else "tack"


def _nearest_heading(h: np.ndarray, i: int) -> float:
    for k in range(1, 10):
        for j in (i - k, i + k):
            if 0 <= j < len(h) and not np.isnan(h[j]):
                return float(h[j])
    return 0.0


# ----------------------------------------------------------------------------
# Session summary
# ----------------------------------------------------------------------------

def summarise(tr: Track, fly: np.ndarray, flights, turns, stops, wind, p: Params, stills=None) -> SessionStats:
    from datetime import datetime, timezone

    n = len(tr)
    moving = tr.speed >= p.stop_kmh
    stopped_s = float((~moving).sum())
    moving_s = float(moving.sum())
    flying_s = float(fly.sum())
    taxi_s = float((moving & ~fly).sum())
    fly_dist = sum(f.distance_m for f in flights)
    durations = [f.duration_s for f in flights]
    longest = max(flights, key=lambda f: f.duration_s) if flights else None

    jibes = [t for t in turns if t.kind in ("jibe", "turn")]
    tacks = [t for t in turns if t.kind == "tack"]
    clean = [t for t in jibes if t.outcome == "clean"]
    td = [t for t in jibes if t.outcome == "touchdown"]
    stopped = [t for t in jibes if t.outcome == "stop"]
    scored = [t for t in jibes if t.outcome != "stop"]
    right = [t for t in jibes if t.direction == "right"]
    left = [t for t in jibes if t.direction == "left"]

    hr_ok = tr.hr[~np.isnan(tr.hr)]
    hours = tr.duration_s / 3600.0 if tr.duration_s > 0 else 1.0

    return SessionStats(
        name=tr.name, sport=tr.sport, device=tr.device, source=tr.source, speed_source=tr.speed_source,
        start_utc=datetime.fromtimestamp(tr.t0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        duration_s=tr.duration_s, distance_m=float(tr.dist[-1] - tr.dist[0]),
        moving_s=moving_s, stopped_s=stopped_s, taxi_s=taxi_s,
        flying_s=flying_s, flying_pct=100.0 * flying_s / n if n else 0.0, flying_distance_m=float(fly_dist),
        flights=len(flights),
        longest_flight_s=longest.duration_s if longest else 0.0,
        longest_flight_m=longest.distance_m if longest else 0.0,
        mean_flight_s=float(np.mean(durations)) if durations else 0.0,
        median_flight_s=float(np.median(durations)) if durations else 0.0,
        max_speed_kmh=float(tr.speed.max()),
        avg_speed_moving_kmh=float(tr.speed[moving].mean()) if moving.any() else 0.0,
        avg_speed_flying_kmh=float(tr.speed[fly].mean()) if fly.any() else 0.0,
        turns_total=len(turns), jibes_total=len(jibes), jibes_clean=len(clean), jibes_touchdown=len(td),
        jibes_stop=len(stopped),
        jibe_success_pct=100.0 * len(clean) / len(jibes) if jibes else 0.0,
        jibe_avg_retention_pct=100.0 * float(np.mean([t.retention for t in scored])) if scored else 0.0,
        jibe_avg_score=float(np.mean([t.score for t in jibes])) if jibes else 0.0,
        jibes_right=len(right), jibes_right_clean=sum(t.outcome == "clean" for t in right),
        jibes_left=len(left), jibes_left_clean=sum(t.outcome == "clean" for t in left),
        tacks_total=len(tacks), tacks_clean=sum(t.outcome == "clean" for t in tacks),
        stops=len(stops), stops_in_turns=sum(f.in_turn is not None for f in stops),
        stops_quick=sum(f.likely == "quick restart" for f in stops),
        stops_long=sum(f.likely == "long stop" for f in stops),
        stops_per_hour=len(stops) / hours,
        standstills=len(stills or []), standstill_s=float(sum(x.duration_s for x in (stills or []))),
        hr_avg=float(hr_ok.mean()) if len(hr_ok) else None,
        hr_max=float(hr_ok.max()) if len(hr_ok) else None,
        gps_points_total=tr.gps_points_total, gps_points_dropped=tr.gps_points_dropped, gps_speed_cap_kmh=tr.gps_speed_cap_kmh,
        wind=wind, params=p,
    )


def session_window(tr: Track, fly: np.ndarray, p: Params) -> Optional[tuple[int, int]]:
    """The surf itself: from the run-up to the first take-off to the stop after the last landing.

    Before the first flight the rider is walking or swimming out, and after the last one
    coming back in; neither says anything about foiling.  The run-up is the uninterrupted
    stretch of movement (>= stop speed) just before take-off, at most trim_lead_s; the end
    is the first standstill after the last landing, at most trim_tail_s later.
    """
    idx = np.where(fly)[0]
    if not len(idx):
        return None
    n = len(tr)
    s = int(idx[0])
    lead = 0
    while s > 0 and lead < p.trim_lead_s and tr.speed[s - 1] >= p.stop_kmh:
        s -= 1
        lead += 1
    e = int(idx[-1]) + 1
    tail = 0
    while e < n and tail < p.trim_tail_s:
        e += 1
        tail += 1
        if tr.speed[e - 1] < p.stop_kmh:
            break
    return s, e


def analyse(tr: Track, p: Optional[Params] = None) -> Analysis:
    from .track import trim_track
    p = p or Params()
    recorded, recorded_m = tr.duration_s, float(tr.dist[-1] - tr.dist[0])
    cut_start = cut_end = 0.0
    if p.trim:
        win = session_window(tr, detect_flying(tr.speed, p), p)
        if win and (win[0] > 0 or win[1] < len(tr)):
            s, e = win
            cut_start, cut_end = float(tr.t[s]), float(tr.t[-1] - tr.t[e - 1])
            tr = trim_track(tr, s, e)
    fly = detect_flying(tr.speed, p)
    flights = build_flights(tr, fly)
    stops = detect_stops(tr, fly, flights, p)
    turns = detect_turns(tr, fly, stops, p)
    wind = estimate_wind(tr, fly, p, turns)
    classify_turns(turns, wind)
    stills = detect_standstills(tr, stops, p)
    stats = summarise(tr, fly, flights, turns, stops, wind, p, stills)
    stats.recorded_duration_s, stats.recorded_distance_m = recorded, recorded_m
    stats.trimmed_start_s, stats.trimmed_end_s = cut_start, cut_end
    return Analysis(track=tr, params=p, flying=fly, flights=flights, turns=turns, stops=stops, wind=wind, stats=stats,
                    standstills=stills)


def to_dict(a: Analysis) -> dict:
    d = {
        "stats": asdict(a.stats),
        "flights": [asdict(f) for f in a.flights],
        "turns": [asdict(t) for t in a.turns],
        "stops": [asdict(f) for f in a.stops],
        "standstills": [asdict(x) for x in a.standstills],
    }
    return d
