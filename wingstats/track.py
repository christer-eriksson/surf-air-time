"""Turn an irregular device track into a regular 1 Hz time series.

Garmin "smart recording" only stores a point when something changes, so
gaps of 1-20 s are normal.  All detection logic works on the resampled
series so that durations are simply sample counts.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .parsers import RawTrack

EARTH_R = 6371000.0


@dataclass
class Track:
    t: np.ndarray          # seconds since start, 1 Hz
    t0: float              # unix time of t == 0
    lat: np.ndarray
    lon: np.ndarray
    x: np.ndarray          # local east metres
    y: np.ndarray          # local north metres
    speed: np.ndarray      # km/h
    dist: np.ndarray       # cumulative metres
    hr: np.ndarray         # bpm, nan when absent
    heading: np.ndarray    # degrees 0-360, nan when too slow to trust
    speed_source: str      # "device" or "derived"
    gps_points_total: int = 0    # raw samples in the file
    gps_points_dropped: int = 0  # raw samples rejected as GPS glitches
    gps_speed_cap_kmh: float = 0.0  # implied-speed cap used for that rejection
    name: str = ""
    sport: str = ""
    device: str = ""
    source: str = ""

    def __len__(self) -> int:
        return len(self.t)

    @property
    def duration_s(self) -> float:
        return float(self.t[-1])


def trim_track(tr: Track, s: int, e: int) -> Track:
    """The part of the track from sample s up to (not including) e, re-based to start at 0 s."""
    sl = slice(s, e)
    return Track(
        t=tr.t[sl] - tr.t[s], t0=tr.t0 + float(tr.t[s]), lat=tr.lat[sl], lon=tr.lon[sl], x=tr.x[sl], y=tr.y[sl],
        speed=tr.speed[sl], dist=tr.dist[sl] - tr.dist[s], hr=tr.hr[sl], heading=tr.heading[sl],
        speed_source=tr.speed_source, gps_points_total=tr.gps_points_total, gps_points_dropped=tr.gps_points_dropped,
        gps_speed_cap_kmh=tr.gps_speed_cap_kmh, name=tr.name, sport=tr.sport, device=tr.device, source=tr.source,
    )


def _interp_optional(T: np.ndarray, rel: np.ndarray, vals: list) -> np.ndarray:
    arr = np.array([np.nan if v is None else float(v) for v in vals], dtype=float)
    ok = ~np.isnan(arr)
    if ok.sum() < 2:
        return np.full_like(T, np.nan, dtype=float)
    return np.interp(T, rel[ok], arr[ok])


def smooth(a: np.ndarray, window: int) -> np.ndarray:
    """Centred moving average that tolerates NaN."""
    if window <= 1:
        return a.copy()
    k = np.ones(window)
    vals = np.nan_to_num(a, nan=0.0)
    cnt = (~np.isnan(a)).astype(float)
    num = np.convolve(vals, k, mode="same")
    den = np.convolve(cnt, k, mode="same")
    with np.errstate(invalid="ignore", divide="ignore"):
        out = num / den
    out[den == 0] = np.nan
    return out


def clean_gps(time: np.ndarray, lat: np.ndarray, lon: np.ndarray, hard_cap_kmh: float = 70.0,
              min_cap_kmh: float = 35.0, cap_factor: float = 1.4) -> tuple[np.ndarray, float]:
    """Flag raw GPS samples that imply an impossible speed.

    After a dropout the watch often reports a position far from the real one and
    then "bounces" back, which shows up as a 60-120 km/h spike and a phantom turn.
    A sample is kept only if it is reachable from the last kept sample *before* it
    and from the last kept sample *after* it at no more than ``cap`` km/h, where
    cap adapts to the session (1.4 x the 95th percentile of step speeds, at least
    35 km/h, at most 70 km/h).  Returns (keep mask, cap).
    """
    n = len(time)
    keep_all = np.ones(n, dtype=bool)
    if n < 3:
        return keep_all, hard_cap_kmh
    lat0 = np.radians(np.mean(lat))
    x = np.radians(lon - np.mean(lon)) * EARTH_R * np.cos(lat0)
    y = np.radians(lat - np.mean(lat)) * EARTH_R
    dt = np.diff(time)
    step = np.hypot(np.diff(x), np.diff(y)) / np.where(dt > 0, dt, 1e-9) * 3.6
    cap = float(min(hard_cap_kmh, max(min_cap_kmh, cap_factor * np.percentile(step, 95))))

    def gate(order):
        ok = np.zeros(n, dtype=bool)
        last = order[0]
        ok[last] = True
        for i in order[1:]:
            d = np.hypot(x[i] - x[last], y[i] - y[last])
            gap = abs(time[i] - time[last])
            if d / max(gap, 1e-9) * 3.6 <= cap:
                ok[i] = True
                last = i
        return ok

    keep = gate(np.arange(n)) & gate(np.arange(n)[::-1])
    if keep.sum() < 2:
        return keep_all, cap
    return keep, cap


def resample(raw: RawTrack, heading_min_speed_kmh: float = 4.0, heading_half_window: int = 2) -> Track:
    if len(raw) < 2:
        raise ValueError("Track has fewer than two timestamped samples")
    t_all = np.asarray(raw.time, dtype=float)
    keep, cap = clean_gps(t_all, np.asarray(raw.lat, dtype=float), np.asarray(raw.lon, dtype=float))
    idx = np.where(keep)[0]
    pick = lambda vals: [vals[i] for i in idx]  # noqa: E731
    raw = RawTrack(time=pick(raw.time), lat=pick(raw.lat), lon=pick(raw.lon), alt=pick(raw.alt), dist=pick(raw.dist),
                   speed=pick(raw.speed), hr=pick(raw.hr), name=raw.name, sport=raw.sport, source=raw.source,
                   device=raw.device, has_device_speed=raw.has_device_speed)
    t_arr = np.asarray(raw.time, dtype=float)
    rel = t_arr - t_arr[0]
    T = np.arange(0.0, np.floor(rel[-1]) + 1.0)

    lat = np.interp(T, rel, np.asarray(raw.lat, dtype=float))
    lon = np.interp(T, rel, np.asarray(raw.lon, dtype=float))

    lat0 = np.radians(lat.mean())
    x = np.radians(lon - lon.mean()) * EARTH_R * np.cos(lat0)
    y = np.radians(lat - lat.mean()) * EARTH_R

    if raw.has_device_speed:
        speed = _interp_optional(T, rel, raw.speed) * 3.6
        speed_source = "device"
    else:
        # Derive speed from position: distance over a 3 s centred window
        # (raw GPS positions are noisy at 1 Hz).
        step = np.hypot(np.diff(x, prepend=x[0]), np.diff(y, prepend=y[0]))
        speed = smooth(step, 3) * 3.6
        speed_source = "derived"
    speed = np.clip(np.nan_to_num(speed, nan=0.0), 0.0, None)

    dist = _interp_optional(T, rel, raw.dist)
    if np.isnan(dist).all():
        dist = np.cumsum(np.hypot(np.diff(x, prepend=x[0]), np.diff(y, prepend=y[0])))
    else:
        dist = np.nan_to_num(dist, nan=0.0)

    hr = _interp_optional(T, rel, raw.hr)

    heading = _heading(x, y, speed, heading_min_speed_kmh, heading_half_window)

    return Track(
        t=T, t0=float(t_arr[0]), lat=lat, lon=lon, x=x, y=y, speed=speed, dist=dist, hr=hr,
        heading=heading, speed_source=speed_source, gps_points_total=int(len(t_all)),
        gps_points_dropped=int(len(t_all) - len(idx)), gps_speed_cap_kmh=cap,
        name=raw.name, sport=raw.sport, device=raw.device, source=raw.source,
    )


def _heading(x: np.ndarray, y: np.ndarray, speed: np.ndarray, min_speed: float, hw: int) -> np.ndarray:
    n = len(x)
    hdg = np.full(n, np.nan)
    if n <= 2 * hw:
        return hdg
    i = np.arange(hw, n - hw)
    dx = x[i + hw] - x[i - hw]
    dy = y[i + hw] - y[i - hw]
    h = np.degrees(np.arctan2(dx, dy)) % 360.0
    hdg[i] = h
    hdg[speed < min_speed] = np.nan
    return hdg


def unwrap_heading(heading: np.ndarray) -> np.ndarray:
    """Continuous heading (degrees) with NaN gaps held at the last value."""
    h = heading.copy()
    n = len(h)
    # forward fill, then back fill the leading NaNs
    last = np.nan
    for k in range(n):
        if np.isnan(h[k]):
            h[k] = last
        else:
            last = h[k]
    first_valid = next((v for v in h if not np.isnan(v)), 0.0)
    h[np.isnan(h)] = first_valid
    return np.degrees(np.unwrap(np.radians(h)))


def ang_diff(a: float, b: float) -> float:
    """Signed smallest angle from a to b in degrees (-180, 180]."""
    return (b - a + 180.0) % 360.0 - 180.0
