/* wingstats.js - wingfoil session statistics, in the browser.
 *
 * A straight port of the Python package (wingstats/parsers.py, track.py,
 * analysis.py).  Pure functions, no DOM except DOMParser for the XML.
 *
 *   const raw   = wingstats.loadTrack(filename, text);
 *   const track = wingstats.resample(raw);
 *   const a     = wingstats.analyse(track, wingstats.defaultParams());
 */
const wingstats = (() => {
  'use strict';

  // ---------------------------------------------------------------- params
  function defaultParams() {
    return {
      flyOn: 13.0, flyOff: 10.0, minFlight: 5, stop: 3.0, fallWindow: 15, waterStop: 30,
      turnRate: 5.0, turnEdgeRate: 2.0, turnMinDeg: 100.0, abortedTurnMinDeg: 55.0, turnMergeGap: 4,
      entryWindow: 6, exitWindow: 6, after: 10, windFrom: null,
      trim: true, trimLead: 30, trimTail: 30,  // cut the walk/swim out before the first flight and back after the last
      stillMin: 10,  // a standstill at least this long that is not a full stop is still marked
    };
  }

  // --------------------------------------------------------------- parsers
  const TCX = 'http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2';
  const TCX_EXT = 'http://www.garmin.com/xmlschemas/ActivityExtension/v2';
  const GPX = 'http://www.topografix.com/GPX/1/1';
  const GPX_TPX = 'http://www.garmin.com/xmlschemas/TrackPointExtension/v1';

  function emptyRaw() {
    return { time: [], lat: [], lon: [], alt: [], dist: [], speed: [], hr: [], name: '', sport: '', device: '', source: '', hasDeviceSpeed: false };
  }
  function parseXml(text) {
    const doc = new DOMParser().parseFromString(text, 'application/xml');
    const err = doc.getElementsByTagName('parsererror')[0];
    if (err) throw new Error('Not a valid XML file: ' + err.textContent.split('\n')[0]);
    return doc;
  }
  function childNS(el, ns, name) {
    if (!el) return null;
    for (const c of el.children) if (c.localName === name && (!ns || c.namespaceURI === ns)) return c;
    return null;
  }
  function pathNS(el, ns, ...names) { for (const n of names) { el = childNS(el, ns, n); if (!el) return null; } return el; }
  function num(el) { if (!el || el.textContent == null) return null; const v = parseFloat(el.textContent); return isNaN(v) ? null : v; }
  function parseTime(txt) { const t = Date.parse(txt.trim()); if (isNaN(t)) throw new Error('Bad timestamp ' + txt); return t / 1000; }

  function readTcx(text, source) {
    const doc = parseXml(text);
    const tr = emptyRaw(); tr.source = source;
    const act = doc.getElementsByTagNameNS(TCX, 'Activity')[0];
    if (act) { tr.sport = act.getAttribute('Sport') || ''; const nm = pathNS(act, TCX, 'Creator', 'Name'); if (nm) tr.device = nm.textContent; }
    for (const tp of doc.getElementsByTagNameNS(TCX, 'Trackpoint')) {
      const pos = childNS(tp, TCX, 'Position'), t = childNS(tp, TCX, 'Time');
      if (!pos || !t) continue;
      const lat = num(childNS(pos, TCX, 'LatitudeDegrees')), lon = num(childNS(pos, TCX, 'LongitudeDegrees'));
      if (lat == null || lon == null) continue;
      const spd = num(pathNS(tp, null, 'Extensions', 'TPX', 'Speed'));
      const hr = num(pathNS(tp, TCX, 'HeartRateBpm', 'Value'));
      tr.time.push(parseTime(t.textContent)); tr.lat.push(lat); tr.lon.push(lon);
      tr.alt.push(num(childNS(tp, TCX, 'AltitudeMeters'))); tr.dist.push(num(childNS(tp, TCX, 'DistanceMeters')));
      tr.speed.push(spd); tr.hr.push(hr == null ? null : Math.round(hr));
      if (spd != null) tr.hasDeviceSpeed = true;
    }
    dedupe(tr); return tr;
  }
  function readGpx(text, source) {
    const doc = parseXml(text);
    const tr = emptyRaw(); tr.source = source;
    const trk = doc.getElementsByTagNameNS(GPX, 'trk')[0];
    if (trk) { const n = childNS(trk, GPX, 'name'), ty = childNS(trk, GPX, 'type'); tr.name = n ? n.textContent : ''; tr.sport = ty ? ty.textContent : ''; }
    for (const pt of doc.getElementsByTagNameNS(GPX, 'trkpt')) {
      const t = childNS(pt, GPX, 'time'); if (!t) continue;
      tr.time.push(parseTime(t.textContent)); tr.lat.push(parseFloat(pt.getAttribute('lat'))); tr.lon.push(parseFloat(pt.getAttribute('lon')));
      tr.alt.push(num(childNS(pt, GPX, 'ele')));
      const hr = num(pathNS(pt, null, 'extensions', 'TrackPointExtension', 'hr'));
      tr.hr.push(hr == null ? null : Math.round(hr)); tr.dist.push(null); tr.speed.push(null);
    }
    dedupe(tr); return tr;
  }
  function dedupe(tr) {
    const keep = []; for (let i = 0; i < tr.time.length; i++) if (i === tr.time.length - 1 || tr.time[i + 1] > tr.time[i]) keep.push(i);
    if (keep.length === tr.time.length) return;
    for (const k of ['time', 'lat', 'lon', 'alt', 'dist', 'speed', 'hr']) tr[k] = keep.map(i => tr[k][i]);
  }
  function loadTrack(filename, text) {
    const ext = (filename.match(/\.([^.]+)$/) || ['', ''])[1].toLowerCase();
    if (ext === 'tcx') return readTcx(text, filename);
    if (ext === 'gpx') return readGpx(text, filename);
    if (ext === 'kml') throw new Error('KML exports contain coordinates but no timestamps; use the TCX or GPX export');
    if (ext === 'csv') throw new Error('The CSV export is a lap summary without a track; use the TCX or GPX export');
    throw new Error('Unsupported file type: .' + ext + ' (use .tcx or .gpx)');
  }

  // ----------------------------------------------------------------- maths
  function interp(T, xs, ys) {           // numpy.interp: linear, clamped at the ends
    const out = new Float64Array(T.length); let j = 0;
    for (let i = 0; i < T.length; i++) {
      const t = T[i];
      if (t <= xs[0]) { out[i] = ys[0]; continue; }
      if (t >= xs[xs.length - 1]) { out[i] = ys[ys.length - 1]; continue; }
      while (xs[j + 1] < t) j++;
      const f = (t - xs[j]) / (xs[j + 1] - xs[j]); out[i] = ys[j] + f * (ys[j + 1] - ys[j]);
    }
    return out;
  }
  function interpOptional(T, rel, vals) {
    const xs = [], ys = [];
    for (let i = 0; i < vals.length; i++) if (vals[i] != null && !isNaN(vals[i])) { xs.push(rel[i]); ys.push(vals[i]); }
    if (xs.length < 2) return new Float64Array(T.length).fill(NaN);
    return interp(T, xs, ys);
  }
  function smooth(a, window) {           // centred moving average tolerant of NaN ('same' convolution)
    if (window <= 1) return Float64Array.from(a);
    const n = a.length, out = new Float64Array(n), half = Math.floor(window / 2);
    for (let i = 0; i < n; i++) {
      let s = 0, c = 0;
      for (let k = i - half; k <= i - half + window - 1; k++) if (k >= 0 && k < n && !isNaN(a[k])) { s += a[k]; c++; }
      out[i] = c ? s / c : NaN;
    }
    return out;
  }
  function gradient(a) {
    const n = a.length, g = new Float64Array(n); if (n < 2) return g;
    g[0] = a[1] - a[0]; g[n - 1] = a[n - 1] - a[n - 2];
    for (let i = 1; i < n - 1; i++) g[i] = (a[i + 1] - a[i - 1]) / 2;
    return g;
  }
  const angDiff = (a, b) => ((b - a + 180) % 360 + 360) % 360 - 180;   // (-180, 180]
  const mod360 = v => ((v % 360) + 360) % 360;
  function unwrapHeading(h) {
    const n = h.length, out = new Float64Array(n); let last = NaN;
    for (let k = 0; k < n; k++) { if (isNaN(h[k])) out[k] = last; else { out[k] = h[k]; last = h[k]; } }
    let first = 0; for (const v of out) if (!isNaN(v)) { first = v; break; }
    for (let k = 0; k < n; k++) if (isNaN(out[k])) out[k] = first;
    for (let k = 1; k < n; k++) { let d = out[k] - out[k - 1]; d = ((d + 180) % 360 + 360) % 360 - 180; if (d === -180 && out[k] - out[k - 1] > 0) d = 180; out[k] = out[k - 1] + d; }
    return out;
  }
  const mean = a => a.length ? a.reduce((s, v) => s + v, 0) / a.length : 0;
  const median = a => { if (!a.length) return 0; const b = [...a].sort((x, y) => x - y), m = b.length >> 1; return b.length % 2 ? b[m] : (b[m - 1] + b[m]) / 2; };
  const slice = (a, s, e) => Array.from(a.subarray ? a.subarray(Math.max(0, s), Math.max(0, e)) : a.slice(Math.max(0, s), Math.max(0, e)));

  // ------------------------------------------------------------- resample
  const EARTH_R = 6371000.0;
  // numpy-style linear-interpolation percentile (the Python package uses np.percentile)
  function percentile(values, q) {
    const a = Float64Array.from(values).sort(); if (!a.length) return 0;
    const pos = q / 100 * (a.length - 1), lo = Math.floor(pos), hi = Math.min(a.length - 1, lo + 1);
    return a[lo] + (a[hi] - a[lo]) * (pos - lo);
  }
  // Flag raw GPS samples that imply an impossible speed (see clean_gps in the Python package):
  // a sample is kept only if it is reachable from the last kept sample before it AND after it
  // at no more than `cap` km/h, where cap adapts to the session.
  function cleanGps(time, lat, lon, hardCap = 70.0, minCap = 35.0, capFactor = 1.4) {
    const n = time.length, keepAll = new Uint8Array(n).fill(1);
    if (n < 3) return { keep: keepAll, cap: hardCap };
    const latMean = mean(lat), lonMean = mean(lon), lat0 = latMean * Math.PI / 180;
    const x = lon.map(v => (v - lonMean) * Math.PI / 180 * EARTH_R * Math.cos(lat0)), y = lat.map(v => (v - latMean) * Math.PI / 180 * EARTH_R);
    const step = []; for (let i = 1; i < n; i++) { const dt = time[i] - time[i - 1]; step.push(Math.hypot(x[i] - x[i - 1], y[i] - y[i - 1]) / (dt > 0 ? dt : 1e-9) * 3.6); }
    const cap = Math.min(hardCap, Math.max(minCap, capFactor * percentile(step, 95)));
    const gate = order => { const ok = new Uint8Array(n); let last = order[0]; ok[last] = 1;
      for (let k = 1; k < order.length; k++) { const i = order[k], d = Math.hypot(x[i] - x[last], y[i] - y[last]), gap = Math.abs(time[i] - time[last]);
        if (d / Math.max(gap, 1e-9) * 3.6 <= cap) { ok[i] = 1; last = i; } }
      return ok; };
    const fwd = Array.from({ length: n }, (_, i) => i), f = gate(fwd), b = gate(fwd.slice().reverse());
    const keep = new Uint8Array(n); let kept = 0; for (let i = 0; i < n; i++) { keep[i] = f[i] && b[i] ? 1 : 0; kept += keep[i]; }
    return kept < 2 ? { keep: keepAll, cap } : { keep, cap };
  }
  function resample(rawIn) {
    if (rawIn.time.length < 2) throw new Error('Track has fewer than two timestamped samples');
    const { keep, cap } = cleanGps(rawIn.time, rawIn.lat, rawIn.lon);
    const raw = Object.assign({}, rawIn);
    for (const k of ['time', 'lat', 'lon', 'alt', 'dist', 'speed', 'hr']) if (Array.isArray(rawIn[k])) raw[k] = rawIn[k].filter((_, i) => keep[i]);
    const gpsTotal = rawIn.time.length, gpsDropped = gpsTotal - raw.time.length;
    const t0 = raw.time[0], rel = raw.time.map(t => t - t0);
    const n = Math.floor(rel[rel.length - 1]) + 1;
    const T = Float64Array.from({ length: n }, (_, i) => i);
    const lat = interp(T, rel, raw.lat), lon = interp(T, rel, raw.lon);
    const latMean = mean(Array.from(lat)), lonMean = mean(Array.from(lon)), lat0 = latMean * Math.PI / 180;
    const x = lat.map((_, i) => (lon[i] - lonMean) * Math.PI / 180 * EARTH_R * Math.cos(lat0));
    const y = lat.map(v => (v - latMean) * Math.PI / 180 * EARTH_R);
    let speed, speedSource;
    if (raw.hasDeviceSpeed) { speed = interpOptional(T, rel, raw.speed).map(v => v * 3.6); speedSource = 'device'; }
    else {
      const step = new Float64Array(n); for (let i = 1; i < n; i++) step[i] = Math.hypot(x[i] - x[i - 1], y[i] - y[i - 1]);
      speed = smooth(step, 3).map(v => v * 3.6); speedSource = 'derived';
    }
    for (let i = 0; i < n; i++) speed[i] = isNaN(speed[i]) ? 0 : Math.max(0, speed[i]);
    let dist = interpOptional(T, rel, raw.dist);
    if (dist.every(v => isNaN(v))) { dist = new Float64Array(n); for (let i = 1; i < n; i++) dist[i] = dist[i - 1] + Math.hypot(x[i] - x[i - 1], y[i] - y[i - 1]); }
    else for (let i = 0; i < n; i++) if (isNaN(dist[i])) dist[i] = 0;
    const hr = interpOptional(T, rel, raw.hr);
    const heading = computeHeading(x, y, speed, 4.0, 2);
    return { t: T, t0, lat, lon, x, y, speed, dist, hr, heading, speedSource, gpsPointsTotal: gpsTotal, gpsPointsDropped: gpsDropped, gpsSpeedCapKmh: cap,
             name: raw.name, sport: raw.sport, device: raw.device, source: raw.source, n,
             get durationS() { return this.t[this.n - 1]; } };
  }
  function computeHeading(x, y, speed, minSpeed, hw) {
    const n = x.length, h = new Float64Array(n).fill(NaN);
    for (let i = hw; i < n - hw; i++) { if (speed[i] < minSpeed) continue; h[i] = mod360(Math.atan2(x[i + hw] - x[i - hw], y[i + hw] - y[i - hw]) * 180 / Math.PI); }
    return h;
  }

  // -------------------------------------------------------------- flights
  function segments(mask) { const out = []; let s = null; for (let i = 0; i < mask.length; i++) { if (mask[i] && s === null) s = i; else if (!mask[i] && s !== null) { out.push([s, i]); s = null; } } if (s !== null) out.push([s, mask.length]); return out; }
  function detectFlying(speed, p) {
    const n = speed.length, fly = new Uint8Array(n); let on = false;
    for (let i = 0; i < n; i++) { if (!on && speed[i] >= p.flyOn) on = true; else if (on && speed[i] < p.flyOff) on = false; fly[i] = on ? 1 : 0; }
    for (const [s, e] of segments(fly)) if (e - s < p.minFlight) fly.fill(0, s, e);
    return fly;
  }
  function buildFlights(tr, fly) {
    return segments(fly).map(([s, e], k) => { const sp = slice(tr.speed, s, e); return {
      idx: k + 1, start_s: tr.t[s], end_s: tr.t[e - 1], duration_s: e - s, distance_m: tr.dist[e - 1] - tr.dist[s],
      max_speed_kmh: Math.max(...sp), avg_speed_kmh: mean(sp), ended_by: '' }; });
  }

  // ---------------------------------------------------------------- stops
  function detectStops(tr, fly, flights, p) {
    const n = tr.n, stops = [];
    for (const fl of flights) {
      const e = Math.floor(fl.end_s) + 1;
      if (e >= n) { fl.ended_by = 'session end'; continue; }
      let stopI = -1; for (let k = e; k < Math.min(n, e + p.fallWindow); k++) if (tr.speed[k] < p.stop) { stopI = k; break; }
      if (stopI < 0) { fl.ended_by = 'touchdown'; continue; }
      let later = -1; for (let k = stopI; k < n; k++) if (fly[k]) { later = k - stopI; break; }
      if (later < 0) { let taxi = 0; for (let k = stopI; k < n; k++) if (tr.speed[k] >= p.stop) taxi++; if (taxi < 10) { fl.ended_by = 'session end'; continue; } }
      fl.ended_by = 'full stop';
      let j = stopI; while (j < n && tr.speed[j] < p.stop) j++;
      const recovery = later < 0 ? null : later;
      stops.push({ idx: stops.length + 1, time_s: tr.t[stopI], flight_end_s: fl.end_s, speed_before_kmh: mean(slice(tr.speed, e - 6, e)),
        stopped_s: j - stopI, recovery_s: recovery, likely: (recovery === null || recovery > p.waterStop) ? 'long stop' : 'quick restart',
        in_turn: null, lat: tr.lat[stopI], lon: tr.lon[stopI] });
    }
    return stops;
  }

  // Standstills of at least stillMin s that are not the full stop after a flight (resting, waiting, swimming after taxiing).
  function detectStandstills(tr, stops, p) {
    const out = [], stopTimes = stops.map(f => f.time_s), still = new Uint8Array(tr.n);
    for (let i = 0; i < tr.n; i++) still[i] = tr.speed[i] < p.stop ? 1 : 0;
    for (const [s, e] of segments(still)) {
      if (e - s < p.stillMin) continue;
      if (stopTimes.some(st => tr.t[s] - 1 <= st && st <= tr.t[e - 1] + 1)) continue;
      const mid = Math.floor((s + e) / 2);
      out.push({ idx: out.length + 1, start_s: tr.t[s], duration_s: e - s, lat: tr.lat[mid], lon: tr.lon[mid] });
    }
    return out;
  }

  // ----------------------------------------------------------------- wind
  function reachHeadings(tr, fly) {
    const h = []; for (let i = 0; i < tr.n; i++) if (fly[i] && !isNaN(tr.heading[i])) h.push(tr.heading[i]);
    if (h.length < 30) return [];
    const cnt = new Array(36).fill(0); for (const v of h) cnt[Math.min(35, Math.floor(v / 10))]++;
    const order = cnt.map((c, b) => [c, b]).sort((a, b) => b[0] - a[0] || a[1] - b[1]);
    const peaks = [];
    for (const [, b] of order) { const c = b * 10 + 5; if (peaks.every(q => Math.abs(angDiff(c, q)) > 60)) peaks.push(c); if (peaks.length === 2) break; }
    return peaks;
  }
  function drift(tr, p) {
    let dx = 0, dy = 0, secs = 0;
    for (let i = 1; i < tr.n; i++) if (tr.speed[i] < p.stop && tr.speed[i - 1] < p.stop) { dx += tr.x[i] - tr.x[i - 1]; dy += tr.y[i] - tr.y[i - 1]; secs++; }
    const m = Math.hypot(dx, dy);
    return { down: (secs < 30 || m < 15) ? null : mod360(Math.atan2(dx, dy) * 180 / Math.PI), m, secs };
  }
  function circularMean(deg) {
    let sx = 0, sy = 0; for (const d of deg) { sx += Math.sin(d * Math.PI / 180); sy += Math.cos(d * Math.PI / 180); }
    return { mean: mod360(Math.atan2(sx, sy) * 180 / Math.PI), r: Math.hypot(sx, sy) / deg.length };
  }
  // Wingfoil turns are nearly always jibes, i.e. the rider passes through downwind mid-turn, so the
  // mean mid-turn heading is the most reliable downwind estimate. Drift while stopped is the fallback.
  function estimateWind(tr, fly, p, turns) {
    const reach = reachHeadings(tr, fly), d = drift(tr, p);
    if (p.windFrom != null) return { from_deg: mod360(p.windFrom), downwind_deg: mod360(p.windFrom + 180), method: 'user', drift_m: d.m, drift_s: d.secs, confidence: 'high', reach_headings_deg: reach, turns_used: 0, turn_agreement: 0, drift_downwind_deg: d.down };
    const cands = (turns || []).filter(t => !t.aborted && Math.abs(t.heading_change_deg) >= p.turnMinDeg && Math.abs(t.heading_change_deg) <= 270).map(t => t.mid_heading_deg);
    const offBisector = down => { if (reach.length !== 2) return 0; const bis = mod360(reach[0] + angDiff(reach[0], reach[1]) / 2); return Math.min(Math.abs(angDiff(down, bis)), Math.abs(angDiff(down, mod360(bis + 180)))); };
    if (cands.length >= 3) {
      const { mean: down, r } = circularMean(cands);
      let conf = (r >= 0.9 && cands.length >= 5) ? 'high' : r >= 0.75 ? 'medium' : 'low';
      if (reach.length === 2 && offBisector(down) > 45) conf = conf === 'high' ? 'medium' : 'low';
      return { from_deg: mod360(down + 180), downwind_deg: down, method: 'turn headings', drift_m: d.m, drift_s: d.secs, confidence: conf, reach_headings_deg: reach, turns_used: cands.length, turn_agreement: r, drift_downwind_deg: d.down };
    }
    if (d.down == null) return { from_deg: null, downwind_deg: null, method: 'drift (insufficient)', drift_m: d.m, drift_s: d.secs, confidence: 'none', reach_headings_deg: reach, turns_used: 0, turn_agreement: 0, drift_downwind_deg: null };
    const down = d.down;
    let conf = 'medium';
    if (reach.length === 2) { const off = offBisector(down); conf = off <= 35 ? 'high' : off <= 60 ? 'medium' : 'low'; }
    if (d.m < 30) conf = 'low';
    return { from_deg: mod360(down + 180), downwind_deg: down, method: 'drift while stopped', drift_m: d.m, drift_s: d.secs, confidence: conf, reach_headings_deg: reach, turns_used: 0, turn_agreement: 0, drift_downwind_deg: d.down };
  }

  // ---------------------------------------------------------------- turns
  function nearestHeading(h, i) { for (let k = 1; k < 10; k++) for (const j of [i - k, i + k]) if (j >= 0 && j < h.length && !isNaN(h[j])) return h[j]; return 0; }
  const sign = v => v > 0 ? 1 : v < 0 ? -1 : 0;
  function detectTurns(tr, fly, stops, p) {
    const n = tr.n, u = smooth(unwrapHeading(tr.heading), 3), rate = gradient(u);
    const moving = Array.from(tr.speed, v => v >= 4.0);
    const active = new Uint8Array(n); for (let i = 0; i < n; i++) active[i] = (Math.abs(rate[i]) >= p.turnRate && moving[i]) ? 1 : 0;
    const merged = [];
    for (const [s, e] of segments(active)) {
      if (merged.length) { const [ps, pe] = merged[merged.length - 1]; if (s - pe <= p.turnMergeGap && sign(u[e - 1] - u[s]) === sign(u[pe - 1] - u[ps])) { merged[merged.length - 1] = [ps, e]; continue; } }
      merged.push([s, e]);
    }
    const stopTimes = stops.map(f => f.time_s), turns = [];
    for (let [s, e] of merged) {
      const sg = sign(u[e - 1] - u[s]) || 1;
      while (s > 0 && moving[s - 1] && sg * rate[s - 1] >= p.turnEdgeRate) s--;
      while (e < n && moving[e - 1] && sg * rate[e - 1] >= p.turnEdgeRate) e++;
      e = Math.min(e, n);
      const change = u[e - 1] - u[s];
      if (Math.abs(change) < p.abortedTurnMinDeg) continue;
      let stopIdx = null;
      for (let k = 0; k < stopTimes.length; k++) { const d = stopTimes[k] - tr.t[e - 1]; if (d >= -2 && d <= p.fallWindow + 2) { stopIdx = k; break; } }
      const aborted = Math.abs(change) < p.turnMinDeg;
      if (aborted && stopIdx === null) continue;
      const entry = slice(tr.speed, s - p.entryWindow, s);
      if (!entry.length || mean(entry) < p.flyOff) continue;
      turns.push(makeTurn(tr, fly, p, s, e, change, stopIdx, aborted));
    }
    turns.forEach((t, k) => { t.idx = k + 1; });
    for (const t of turns) if (t.outcome === 'stop') for (const f of stops) if (t.start_s - 2 <= f.time_s && f.time_s <= t.end_s + p.fallWindow + 2 && f.in_turn === null) { f.in_turn = t.idx; break; }
    return turns;
  }
  function makeTurn(tr, fly, p, s, e, change, stopIdx, aborted) {
    const n = tr.n;
    const entry = mean(slice(tr.speed, s - p.entryWindow, s));
    const exitSl = slice(tr.speed, e, Math.min(n, e + p.exitWindow)); const exitV = exitSl.length ? mean(exitSl) : 0;
    const core = slice(tr.speed, s, Math.min(n, e + 5)); const minV = core.length ? Math.min(...core) : 0;
    const a0 = Math.min(n - 1, e + p.after - 2), a1 = Math.min(n, e + p.after + 3); const after = a1 > a0 ? mean(slice(tr.speed, a0, a1)) : 0;
    let outcome;
    if (stopIdx !== null) outcome = 'stop';
    else { let all = true; for (let k = s; k < Math.min(n, e + 5); k++) if (!fly[k]) { all = false; break; } outcome = all ? 'clean' : 'touchdown'; }
    const retention = entry > 0 ? exitV / entry : 0, minRatio = entry > 0 ? minV / entry : 0;
    let score;
    if (outcome === 'stop') score = 0;
    else { score = 100 * Math.min(1, 0.6 * Math.min(retention, 1.1) + 0.4 * Math.min(minRatio, 1)); if (outcome === 'touchdown') score = Math.min(score, 54); }
    const grade = outcome === 'stop' ? 'F' : score >= 85 ? 'A' : score >= 70 ? 'B' : score >= 55 ? 'C' : 'D';
    const mid = Math.floor((s + e) / 2);
    const entryH = !isNaN(tr.heading[s]) ? tr.heading[s] : nearestHeading(tr.heading, s);
    // mid-point heading: an aborted turn is assumed to be a ~180° attempt; a spin beyond 180° is judged by its first half
    const half = aborted ? sign(change) * 90 : sign(change) * Math.min(Math.abs(change) / 2, 90);
    const midH = mod360(entryH + half);
    const kind = 'turn';  // set by classifyTurns once the wind is known
    return { idx: 0, start_s: tr.t[s], end_s: tr.t[e - 1], mid_s: tr.t[mid], duration_s: e - s, heading_change_deg: change,
      direction: change > 0 ? 'right' : 'left', entry_heading_deg: entryH, exit_heading_deg: mod360(entryH + change), mid_heading_deg: midH, kind,
      entry_speed_kmh: entry, min_speed_kmh: minV, exit_speed_kmh: exitV, speed_after_kmh: after, retention, min_ratio: minRatio, score, grade, outcome, aborted,
      lat: tr.lat[mid], lon: tr.lon[mid] };
  }

  function classifyTurns(turns, wind) { for (const t of turns) t.kind = wind.downwind_deg == null ? 'turn' : (Math.abs(angDiff(t.mid_heading_deg, wind.downwind_deg)) <= 90 ? 'jibe' : 'tack'); }

  // -------------------------------------------------------------- summary
  function summarise(tr, fly, flights, turns, stops, wind, p, stills) {
    const n = tr.n; let movingS = 0, flyingS = 0, taxiS = 0, hrSum = 0, hrN = 0, hrMax = -Infinity, flySpeedSum = 0, moveSpeedSum = 0, maxSpeed = 0;
    for (let i = 0; i < n; i++) {
      const mv = tr.speed[i] >= p.stop; if (mv) { movingS++; moveSpeedSum += tr.speed[i]; } if (fly[i]) { flyingS++; flySpeedSum += tr.speed[i]; } if (mv && !fly[i]) taxiS++;
      if (!isNaN(tr.hr[i])) { hrSum += tr.hr[i]; hrN++; hrMax = Math.max(hrMax, tr.hr[i]); } maxSpeed = Math.max(maxSpeed, tr.speed[i]);
    }
    const durations = flights.map(f => f.duration_s);
    const longest = flights.length ? flights.reduce((a, b) => b.duration_s > a.duration_s ? b : a) : null;
    const jibes = turns.filter(t => t.kind === 'jibe' || t.kind === 'turn'), tacks = turns.filter(t => t.kind === 'tack');
    const clean = jibes.filter(t => t.outcome === 'clean'), td = jibes.filter(t => t.outcome === 'touchdown'), stopped = jibes.filter(t => t.outcome === 'stop');
    const scored = jibes.filter(t => t.outcome !== 'stop'), right = jibes.filter(t => t.direction === 'right'), left = jibes.filter(t => t.direction === 'left');
    const hours = tr.durationS > 0 ? tr.durationS / 3600 : 1;
    return {
      name: tr.name, sport: tr.sport, device: tr.device, source: tr.source, speed_source: tr.speedSource,
      start_utc: new Date(tr.t0 * 1000).toISOString().replace('T', ' ').slice(0, 19) + ' UTC', start_local: new Date(tr.t0 * 1000).toLocaleString(),
      duration_s: tr.durationS, distance_m: tr.dist[n - 1] - tr.dist[0], moving_s: movingS, stopped_s: n - movingS, taxi_s: taxiS,
      flying_s: flyingS, flying_pct: n ? 100 * flyingS / n : 0, flying_distance_m: flights.reduce((s, f) => s + f.distance_m, 0),
      flights: flights.length, longest_flight_s: longest ? longest.duration_s : 0, longest_flight_m: longest ? longest.distance_m : 0,
      mean_flight_s: mean(durations), median_flight_s: median(durations), max_speed_kmh: maxSpeed,
      avg_speed_moving_kmh: movingS ? moveSpeedSum / movingS : 0, avg_speed_flying_kmh: flyingS ? flySpeedSum / flyingS : 0,
      turns_total: turns.length, jibes_total: jibes.length, jibes_clean: clean.length, jibes_touchdown: td.length, jibes_stop: stopped.length,
      jibe_success_pct: jibes.length ? 100 * clean.length / jibes.length : 0,
      jibe_avg_retention_pct: scored.length ? 100 * mean(scored.map(t => t.retention)) : 0,
      jibe_avg_score: jibes.length ? mean(jibes.map(t => t.score)) : 0,
      jibes_right: right.length, jibes_right_clean: right.filter(t => t.outcome === 'clean').length,
      jibes_left: left.length, jibes_left_clean: left.filter(t => t.outcome === 'clean').length,
      tacks_total: tacks.length, tacks_clean: tacks.filter(t => t.outcome === 'clean').length,
      stops: stops.length, stops_in_turns: stops.filter(f => f.in_turn !== null).length,
      stops_quick: stops.filter(f => f.likely === 'quick restart').length, stops_long: stops.filter(f => f.likely === 'long stop').length,
      stops_per_hour: stops.length / hours, standstills: (stills || []).length, standstill_s: (stills || []).reduce((a, x) => a + x.duration_s, 0), hr_avg: hrN ? hrSum / hrN : null, hr_max: hrN ? hrMax : null, wind, params: p,
      gps_points_total: tr.gpsPointsTotal, gps_points_dropped: tr.gpsPointsDropped, gps_speed_cap_kmh: tr.gpsSpeedCapKmh,
    };
  }
  // The surf itself: from the run-up to the first take-off (uninterrupted movement, at most trimLead s)
  // to the first standstill after the last landing (at most trimTail s). See session_window in Python.
  function sessionWindow(tr, fly, p) {
    let first = -1, last = -1; for (let i = 0; i < tr.n; i++) if (fly[i]) { if (first < 0) first = i; last = i; }
    if (first < 0) return null;
    let s = first, lead = 0; while (s > 0 && lead < p.trimLead && tr.speed[s - 1] >= p.stop) { s--; lead++; }
    let e = last + 1, tail = 0; while (e < tr.n && tail < p.trimTail) { e++; tail++; if (tr.speed[e - 1] < p.stop) break; }
    return [s, e];
  }
  function trimTrack(tr, s, e) {
    const cut = a => a.slice(s, e), t = cut(tr.t).map(v => v - tr.t[s]), dist = cut(tr.dist).map(v => v - tr.dist[s]);
    return Object.assign({}, tr, { t, t0: tr.t0 + tr.t[s], lat: cut(tr.lat), lon: cut(tr.lon), x: cut(tr.x), y: cut(tr.y), speed: cut(tr.speed), dist,
      hr: cut(tr.hr), heading: cut(tr.heading), n: e - s, get durationS() { return this.t[this.n - 1]; } });
  }
  function analyse(tr, p) {
    p = p || defaultParams();
    const recorded = tr.durationS, recordedM = tr.dist[tr.n - 1] - tr.dist[0]; let cutStart = 0, cutEnd = 0;
    if (p.trim) {
      const win = sessionWindow(tr, detectFlying(tr.speed, p), p);
      if (win && (win[0] > 0 || win[1] < tr.n)) { const [s, e] = win; cutStart = tr.t[s]; cutEnd = tr.t[tr.n - 1] - tr.t[e - 1]; tr = trimTrack(tr, s, e); }
    }
    const fly = detectFlying(tr.speed, p), flights = buildFlights(tr, fly), stops = detectStops(tr, fly, flights, p);
    const turns = detectTurns(tr, fly, stops, p), wind = estimateWind(tr, fly, p, turns); classifyTurns(turns, wind);
    const stills = detectStandstills(tr, stops, p), stats = summarise(tr, fly, flights, turns, stops, wind, p, stills);
    Object.assign(stats, { recorded_duration_s: recorded, recorded_distance_m: recordedM, trimmed_start_s: cutStart, trimmed_end_s: cutEnd });
    return { track: tr, params: p, flying: fly, flights, turns, stops, wind, stats, standstills: stills };
  }
  function toJSON(a) { return { stats: a.stats, flights: a.flights, turns: a.turns, stops: a.stops, standstills: a.standstills }; }

  // Bump when the analysis changes: the web page recomputes stored sessions whose version differs.
  const VERSION = '0.5.0';

  return { VERSION, defaultParams, loadTrack, readTcx, readGpx, resample, analyse, toJSON, angDiff, mod360 };
})();
if (typeof module !== 'undefined') module.exports = wingstats;
