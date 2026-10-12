"""Self-contained HTML report with inline SVG charts and hover tooltips.

No external assets: everything (CSS, SVG, a little JS, the data) is inlined
so the file can be opened from disk or e-mailed.
"""
from __future__ import annotations

import html
import json
from string import Template
from typing import Optional

import numpy as np

from .analysis import Analysis, Turn
from .parsers import LapSummary
from .report import compass, mmss

# Status colours are reserved for outcome (good / warning / critical); the
# single data series (speed, duration, retention) uses categorical slot 1.
CSS = """
:root{color-scheme:light;
 --surface:#fcfcfb;--plane:#f9f9f7;--ink:#0b0b0b;--ink-2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;
 --border:rgba(11,11,11,.10);--s1:#2a78d6;--s1-wash:rgba(42,120,214,.10);--off:#b8b7b0;
 --good:#0ca30c;--warn:#fab219;--crit:#d03b3b;--tip-bg:#ffffff}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){color-scheme:dark;
 --surface:#1a1a19;--plane:#0d0d0d;--ink:#f0efec;--ink-2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;
 --border:rgba(255,255,255,.10);--s1:#3987e5;--s1-wash:rgba(57,135,229,.14);--off:#55554f;--tip-bg:#242423}}
:root[data-theme="dark"]{color-scheme:dark;
 --surface:#1a1a19;--plane:#0d0d0d;--ink:#f0efec;--ink-2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;
 --border:rgba(255,255,255,.10);--s1:#3987e5;--s1-wash:rgba(57,135,229,.14);--off:#55554f;--tip-bg:#242423}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--ink);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:26px;margin:0 0 4px;font-weight:650}
h2{font-size:17px;margin:32px 0 10px;font-weight:600}
.sub{color:var(--ink-2);margin:0 0 20px;font-size:14px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.kpi .label{color:var(--ink-2);font-size:13px}
.kpi .value{font-size:30px;font-weight:600;line-height:1.1;margin:4px 0 2px}
.kpi .note{color:var(--muted);font-size:12.5px}
.kpi.hero .value{font-size:48px}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;color:var(--ink-2);font-size:13px;margin:0 0 8px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.key-line{width:18px;height:0;border-top:2px solid var(--s1)}
.key-rect{width:14px;height:12px;background:var(--s1-wash);border:1px solid var(--s1)}
.key-dot{width:10px;height:10px;border-radius:50%}
svg{display:block;width:100%;height:auto;overflow:visible}
svg text{font-family:inherit;fill:var(--muted);font-size:12px}
svg .tick{font-variant-numeric:tabular-nums}
svg .grid{stroke:var(--grid);stroke-width:1}
svg .axis{stroke:var(--axis);stroke-width:1}
svg .line{fill:none;stroke:var(--s1);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
svg .wash{fill:var(--s1-wash)}
svg .thresh{stroke:var(--axis);stroke-width:1}
svg .track-off{fill:none;stroke:var(--off);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
svg .track-on{fill:none;stroke:var(--s1);stroke-width:2.5;stroke-linejoin:round;stroke-linecap:round}
svg .m{stroke:var(--surface);stroke-width:2}
svg .m.clean{fill:var(--good)} svg .m.touchdown{fill:var(--warn)} svg .m.stop{fill:var(--crit)}
svg .m.tack{fill:var(--surface);stroke:var(--ink-2);stroke-width:2}
tr.tack td{color:var(--muted)}
.notice{background:var(--surface);border:1px solid var(--axis);border-radius:8px;padding:10px 14px;margin:0 0 16px;color:var(--ink-2);font-size:14px}
svg .x-fall{stroke:var(--crit);stroke-width:2;stroke-linecap:round}
svg .bar{fill:var(--s1)} svg .bar:hover{fill-opacity:.8}
svg .hit{fill:transparent;stroke:none;cursor:crosshair}
svg .cross{stroke:var(--ink-2);stroke-width:1;display:none}
svg .lbl{fill:var(--ink-2);font-size:12px}
svg .num{fill:var(--ink);font-size:11px;font-weight:600}
.tip{position:fixed;pointer-events:none;background:var(--tip-bg);color:var(--ink);border:1px solid var(--border);
 border-radius:8px;padding:8px 10px;font-size:13px;box-shadow:0 4px 14px rgba(0,0,0,.12);display:none;z-index:9;min-width:150px}
.tip b{font-size:15px}
.tip .k{color:var(--ink-2);margin-left:6px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{padding:7px 8px;text-align:right;border-bottom:1px solid var(--grid);font-variant-numeric:tabular-nums;white-space:nowrap}
th{color:var(--ink-2);font-weight:600} th:first-child,td:first-child,th.l,td.l{text-align:left}
tr:hover td{background:var(--s1-wash)}
.pill{display:inline-flex;align-items:center;gap:5px;color:var(--ink);font-weight:500}
.pill i{width:9px;height:9px;border-radius:50%;display:inline-block}
.pill.clean i{background:var(--good)}.pill.touchdown i{background:var(--warn)}.pill.stop i{background:var(--crit)}
details{margin-top:24px;color:var(--ink-2);font-size:14px} summary{cursor:pointer;color:var(--ink);font-weight:600}
details p{margin:8px 0}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media (max-width:760px){.two{grid-template-columns:1fr}}
"""

JS = """
(function(){
const D=JSON.parse(document.getElementById('data').textContent);
const tip=document.getElementById('tip');
function esc(s){return String(s)}
function show(ev,rows){tip.replaceChildren();rows.forEach(([v,k])=>{const d=document.createElement('div');
 const b=document.createElement('b');b.textContent=esc(v);d.appendChild(b);if(k){const s=document.createElement('span');s.className='k';s.textContent=esc(k);d.appendChild(s);}tip.appendChild(d);});
 tip.style.display='block';move(ev);}
function move(ev){const w=tip.offsetWidth,h=tip.offsetHeight;let x=ev.clientX+14,y=ev.clientY+14;
 if(x+w>window.innerWidth-8)x=ev.clientX-w-14;if(y+h>window.innerHeight-8)y=ev.clientY-h-14;tip.style.left=x+'px';tip.style.top=y+'px';}
function hide(){tip.style.display='none'}
const mmss=s=>{s=Math.round(s);const m=Math.floor(s/60),r=s%60;return m+':'+(r<10?'0':'')+r};
// --- speed timeline crosshair ---
const svg=document.getElementById('timeline');
if(svg){const hit=svg.querySelector('.hit'),cross=svg.querySelector('.cross'),dot=svg.querySelector('.cdot');
 const x0=+svg.dataset.x0,x1=+svg.dataset.x1,y0=+svg.dataset.y0,y1=+svg.dataset.y1,vmax=+svg.dataset.vmax,n=D.speed.length;
 const toSvg=ev=>{const r=svg.getBoundingClientRect();const vb=svg.viewBox.baseVal;return (ev.clientX-r.left)*vb.width/r.width};
 hit.addEventListener('pointermove',ev=>{const sx=toSvg(ev);let i=Math.round((sx-x0)/(x1-x0)*(n-1));i=Math.max(0,Math.min(n-1,i));
  const px=x0+(x1-x0)*i/(n-1),py=y1-(y1-y0)*D.speed[i]/vmax;cross.setAttribute('x1',px);cross.setAttribute('x2',px);cross.style.display='block';
  dot.setAttribute('cx',px);dot.setAttribute('cy',py);dot.style.display='block';
  const rows=[[D.speed[i].toFixed(1)+' km/h','speed'],[D.flying[i]?'flying':(D.speed[i]<D.stop?'in the water':'taxiing'),'state'],[mmss(i),'time']];
  if(D.hr[i]!=null)rows.push([Math.round(D.hr[i])+' bpm','heart rate']);
  if(D.heading[i]!=null)rows.push([Math.round(D.heading[i])+'°','heading']);
  show(ev,rows);});
 hit.addEventListener('pointerleave',()=>{cross.style.display='none';dot.style.display='none';hide();});}
// --- per-mark tooltips (bars, markers, map) ---
document.querySelectorAll('[data-tip]').forEach(el=>{
 el.addEventListener('pointermove',ev=>{show(ev,JSON.parse(el.dataset.tip))});
 el.addEventListener('pointerleave',hide);
 el.addEventListener('focus',ev=>{const r=el.getBoundingClientRect();show({clientX:r.left+r.width/2,clientY:r.top},JSON.parse(el.dataset.tip))});
 el.addEventListener('blur',hide);});
})();
"""

PAGE = Template("""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>$title</title><style>$css</style></head>
<body><main>
<h1>$title</h1>
<p class="sub">$subtitle</p>
$notice<div class="kpis">$kpis</div>

<h2>Speed over the session</h2>
<div class="card">
<div class="legend"><span><i class="key-line"></i>Speed (km/h)</span><span><i class="key-rect"></i>Flying</span>
<span><i class="key-dot" style="background:var(--good)"></i>Jibe, clean</span><span><i class="key-dot" style="background:var(--warn)"></i>Jibe, touchdown</span>
<span><i class="key-dot" style="background:var(--crit)"></i>Jibe, full stop</span><span><b style="color:var(--crit)">×</b>Full stop on a straight</span></div>
$timeline
</div>

<div class="two">
<div><h2>Track</h2><div class="card">
<div class="legend"><span><i class="key-line"></i>Flying</span><span><i class="key-line" style="border-color:var(--off)"></i>Not flying</span><span>Numbered dots = jibes, hollow = tacks (hover for details)</span></div>
$map</div></div>
<div><h2>Jibe speed retention</h2><div class="card">
<div class="legend"><span><i class="key-rect"></i>Exit speed as % of entry speed</span><span><b style="color:var(--crit)">×</b>Full stop</span></div>
$retention</div></div>
</div>

<h2>Flights</h2>
<div class="card">
<div class="legend"><span><i class="key-rect"></i>Flight length (seconds), in order</span></div>
$flights_chart
</div>

<h2>Jibes and tacks</h2>
<div class="card" style="overflow:auto">$jibe_table</div>

<div class="two">
<div><h2>Flight list</h2><div class="card" style="overflow:auto">$flight_table</div></div>
<div><h2>Full stops</h2><div class="card" style="overflow:auto">$stop_table</div></div>
</div>

<details><summary>How these numbers are computed</summary>
$method
</details>
</main>
<div id="tip" class="tip"></div>
<script type="application/json" id="data">$data</script>
<script>$js</script>
</body></html>
""")


def _fmt(v: float, d: int = 1) -> str:
    return f"{v:.{d}f}"


def _tip(rows: list[tuple[str, str]]) -> str:
    return html.escape(json.dumps(rows), quote=True)


def _kpi(label: str, value: str, note: str = "", hero: bool = False) -> str:
    cls = "card kpi hero" if hero else "card kpi"
    return (f'<div class="{cls}"><div class="label">{html.escape(label)}</div>'
            f'<div class="value">{html.escape(value)}</div><div class="note">{html.escape(note)}</div></div>')


def _nice_max(v: float, step: float) -> float:
    return float(np.ceil(v / step) * step) if v > 0 else step


# ----------------------------------------------------------------------------
# Charts
# ----------------------------------------------------------------------------

def _timeline(a: Analysis) -> str:
    tr, p = a.track, a.params
    W, H = 1060, 280
    x0, x1, y0, y1 = 44, W - 12, 14, H - 36
    n = len(tr)
    vmax = _nice_max(tr.speed.max() * 1.05, 5)
    X = lambda i: x0 + (x1 - x0) * i / (n - 1)
    Y = lambda v: y1 - (y1 - y0) * v / vmax
    out = [f'<svg id="timeline" viewBox="0 0 {W} {H}" data-x0="{x0}" data-x1="{x1}" data-y0="{y0}" data-y1="{y1}" data-vmax="{vmax}" role="img" aria-label="Speed over time with flying periods and jibe markers">']
    # flying washes
    for f in a.flights:
        s, e = int(f.start_s), int(f.end_s) + 1
        out.append(f'<rect class="wash" x="{X(s):.1f}" y="{y0}" width="{max(1.0, X(e) - X(s)):.1f}" height="{y1 - y0}"/>')
    # grid + y ticks
    step = 5 if vmax <= 40 else 10
    for v in np.arange(0, vmax + 0.1, step):
        out.append(f'<line class="grid" x1="{x0}" x2="{x1}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/>')
        out.append(f'<text class="tick" x="{x0 - 6}" y="{Y(v) + 4:.1f}" text-anchor="end">{v:.0f}</text>')
    # flight threshold
    out.append(f'<line class="thresh" x1="{x0}" x2="{x1}" y1="{Y(p.fly_off_kmh):.1f}" y2="{Y(p.fly_off_kmh):.1f}" stroke-dasharray="none"/>')
    out.append(f'<text class="lbl" x="{x0 + 4}" y="{Y(p.fly_off_kmh) - 4:.1f}">flight lost below {p.fly_off_kmh:.0f} km/h</text>')
    # x ticks every 5 min
    for s in range(0, n, 300):
        out.append(f'<line class="axis" x1="{X(s):.1f}" x2="{X(s):.1f}" y1="{y1}" y2="{y1 + 4}"/>')
        out.append(f'<text class="tick" x="{X(s):.1f}" y="{y1 + 18}" text-anchor="middle">{mmss(s)}</text>')
    out.append(f'<line class="axis" x1="{x0}" x2="{x1}" y1="{y1}" y2="{y1}"/>')
    # speed line (downsample to pixel resolution)
    pts = []
    stride = max(1, n // 1400)
    for i in range(0, n, stride):
        pts.append(f"{X(i):.1f},{Y(tr.speed[i]):.1f}")
    out.append(f'<polyline class="line" points="{" ".join(pts)}"/>')
    # full stops on straights
    for f in a.stops:
        if f.in_turn is None:
            x, y = X(int(f.time_s)), Y(f.speed_before_kmh) - 12
            out.append(f'<g class="x-fall" tabindex="0" data-tip="{_tip(_stop_rows(f))}">'
                       f'<line x1="{x - 5:.1f}" y1="{y - 5:.1f}" x2="{x + 5:.1f}" y2="{y + 5:.1f}"/><line x1="{x - 5:.1f}" y1="{y + 5:.1f}" x2="{x + 5:.1f}" y2="{y - 5:.1f}"/>'
                       f'<circle class="hit" cx="{x:.1f}" cy="{y:.1f}" r="12" style="pointer-events:all"/></g>')
    # jibe markers
    for t in a.turns:
        x, y = X(int(t.mid_s)), Y(t.entry_speed_kmh) - 14
        out.append(f'<g tabindex="0" data-tip="{_tip(_turn_rows(t))}"><circle class="m {'tack' if t.kind == 'tack' else t.outcome}" cx="{x:.1f}" cy="{y:.1f}" r="5"/>'
                   f'<circle class="hit" cx="{x:.1f}" cy="{y:.1f}" r="12" style="pointer-events:all"/></g>')
    out.append(f'<line class="cross" x1="0" x2="0" y1="{y0}" y2="{y1}"/>')
    out.append(f'<circle class="cdot m" cx="0" cy="0" r="5" style="display:none;fill:var(--s1)"/>')
    out.append(f'<rect class="hit" x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}"/>')
    out.append("</svg>")
    return "\n".join(out)


def _stop_rows(f) -> list[list[str]]:
    rec = mmss(f.recovery_s) if f.recovery_s is not None else "never"
    return [[f"Full stop ({f.likely})", ""], [mmss(f.time_s), "time"], [f"{f.speed_before_kmh:.1f} km/h", "speed before"],
            [mmss(f.stopped_s), "standing still"], [rec, "back flying after"]]


def _turn_rows(t: Turn) -> list[list[str]]:
    rows = [[f"{'Tack' if t.kind == 'tack' else 'Jibe'} {t.idx}: {t.outcome}" + (" (aborted)" if t.aborted else ""), ""],
            [mmss(t.mid_s), "time"],
            [f"{t.entry_speed_kmh:.1f} → {t.min_speed_kmh:.1f} → {t.exit_speed_kmh:.1f} km/h", "entry → min → exit"],
            [f"{t.speed_after_kmh:.1f} km/h", "10 s after"],
            [f"{t.retention * 100:.0f}%", "speed retained"],
            [f"{t.score:.0f} ({t.grade})", "score"],
            [f"turning {t.direction}, {abs(t.heading_change_deg):.0f}°", "turn"]]
    return rows


def _map(a: Analysis) -> str:
    tr = a.track
    W = 520
    xs, ys = tr.x, tr.y
    xr = xs.max() - xs.min() or 1.0
    yr = ys.max() - ys.min() or 1.0
    pad = 24
    scale = (W - 2 * pad) / xr
    H = int(yr * scale + 2 * pad)
    H = max(260, min(H, 700))
    scale = min((W - 2 * pad) / xr, (H - 2 * pad) / yr)
    cx, cy = (xs.max() + xs.min()) / 2, (ys.max() + ys.min()) / 2
    X = lambda x: W / 2 + (x - cx) * scale
    Y = lambda y: H / 2 - (y - cy) * scale
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="GPS track coloured by flying state">']
    # split path by state
    fly = a.flying
    n = len(tr)
    seg_start = 0
    for i in range(1, n + 1):
        if i == n or fly[i] != fly[seg_start]:
            idx = range(seg_start, min(i + 1, n))
            pts = " ".join(f"{X(xs[k]):.1f},{Y(ys[k]):.1f}" for k in idx)
            cls = "track-on" if fly[seg_start] else "track-off"
            out.append(f'<polyline class="{cls}" points="{pts}"/>')
            seg_start = i
    # scale bar 200 m
    bar = 200 * scale
    out.append(f'<line class="axis" x1="{pad}" x2="{pad + bar:.1f}" y1="{H - 12}" y2="{H - 12}"/>')
    out.append(f'<text class="tick" x="{pad}" y="{H - 16}">200 m</text>')
    # wind arrow
    if a.wind.downwind_deg is not None:
        ang = np.radians(a.wind.downwind_deg)
        ax, ay = W - 40, 40
        dx, dy = 18 * np.sin(ang), -18 * np.cos(ang)
        out.append(f'<g><line class="axis" x1="{ax - dx:.1f}" y1="{ay - dy:.1f}" x2="{ax + dx:.1f}" y2="{ay + dy:.1f}" stroke-width="2"/>'
                   f'<circle cx="{ax + dx:.1f}" cy="{ay + dy:.1f}" r="3" fill="var(--axis)"/>'
                   f'<text class="lbl" x="{ax}" y="{ay + 34}" text-anchor="middle">wind {compass(a.wind.from_deg)}</text></g>')
    # jibe markers
    for t in a.turns:
        # locate the map position from lat/lon via nearest sample
        i = int(t.mid_s)
        x, y = X(xs[i]), Y(ys[i])
        out.append(f'<g tabindex="0" data-tip="{_tip(_turn_rows(t))}"><circle class="m {'tack' if t.kind == 'tack' else t.outcome}" cx="{x:.1f}" cy="{y:.1f}" r="7"/>'
                   f'<text class="num" x="{x:.1f}" y="{y - 10:.1f}" text-anchor="middle">{t.idx}</text>'
                   f'<circle class="hit" cx="{x:.1f}" cy="{y:.1f}" r="14" style="pointer-events:all"/></g>')
    out.append("</svg>")
    return "\n".join(out)


def _bars(items: list[dict], ymax: float, ylabel_fmt, W: int = 520, H: int = 260, ystep: float = 25.0,
          xlabel: str = "", baseline_marks: Optional[list[int]] = None, highlight_max: bool = True,
          aria: str = "") -> str:
    x0, x1, y0, y1 = 40, W - 10, 14, H - 30
    n = max(1, len(items))
    band = (x1 - x0) / n
    bw = min(24.0, band - 2)
    Y = lambda v: y1 - (y1 - y0) * v / ymax
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{html.escape(aria)}">']
    for v in np.arange(0, ymax + 0.01, ystep):
        out.append(f'<line class="grid" x1="{x0}" x2="{x1}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/>')
        out.append(f'<text class="tick" x="{x0 - 6}" y="{Y(v) + 4:.1f}" text-anchor="end">{ylabel_fmt(v)}</text>')
    best = max(range(len(items)), key=lambda k: items[k]["v"]) if items and highlight_max else None
    for k, it in enumerate(items):
        cx = x0 + band * (k + 0.5)
        v = min(it["v"], ymax)
        h = (y1 - Y(v))
        if it.get("x_mark"):
            out.append(f'<g class="x-fall" tabindex="0" data-tip="{_tip(it["tip"])}"><line x1="{cx - 5:.1f}" y1="{y1 - 14}" x2="{cx + 5:.1f}" y2="{y1 - 4}"/>'
                       f'<line x1="{cx - 5:.1f}" y1="{y1 - 4}" x2="{cx + 5:.1f}" y2="{y1 - 14}"/>'
                       f'<rect class="hit" x="{cx - band / 2:.1f}" y="{y0}" width="{band:.1f}" height="{y1 - y0}" style="pointer-events:all"/></g>')
        else:
            r = min(4.0, h / 2)
            path = (f'M{cx - bw / 2:.1f},{y1} V{y1 - h + r:.1f} Q{cx - bw / 2:.1f},{y1 - h:.1f} {cx - bw / 2 + r:.1f},{y1 - h:.1f} '
                    f'H{cx + bw / 2 - r:.1f} Q{cx + bw / 2:.1f},{y1 - h:.1f} {cx + bw / 2:.1f},{y1 - h + r:.1f} V{y1} Z')
            out.append(f'<g tabindex="0" data-tip="{_tip(it["tip"])}"><path class="bar" d="{path}"/>'
                       f'<rect class="hit" x="{cx - band / 2:.1f}" y="{y0}" width="{band:.1f}" height="{y1 - y0}"/></g>')
            if k == best:
                out.append(f'<text class="num" x="{cx:.1f}" y="{y1 - h - 5:.1f}" text-anchor="middle">{it["label"]}</text>')
        if band >= 16 or k % 2 == 0:
            out.append(f'<text class="tick" x="{cx:.1f}" y="{y1 + 16}" text-anchor="middle">{it["x"]}</text>')
    if baseline_marks:
        for v in baseline_marks:
            out.append(f'<line class="thresh" x1="{x0}" x2="{x1}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/>')
    out.append(f'<line class="axis" x1="{x0}" x2="{x1}" y1="{y1}" y2="{y1}"/>')
    if xlabel:
        out.append(f'<text class="lbl" x="{x1}" y="{y1 + 28}" text-anchor="end">{html.escape(xlabel)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def _retention_chart(a: Analysis) -> str:
    items = []
    for t in a.turns:
        if t.kind == "tack":
            continue
        tip = _turn_rows(t)
        if t.outcome == "stop":
            items.append({"v": 0, "x": str(t.idx), "tip": tip, "x_mark": True, "label": ""})
        else:
            items.append({"v": t.retention * 100, "x": str(t.idx), "tip": tip, "label": f"{t.retention * 100:.0f}%"})
    ymax = _nice_max(max([i["v"] for i in items] + [100]) * 1.05, 25)
    return _bars(items, ymax, lambda v: f"{v:.0f}%", xlabel="jibe #", baseline_marks=[100],
                 aria="Speed retained through each jibe")


def _flights_chart(a: Analysis) -> str:
    items = []
    for f in a.flights:
        tip = [[mmss(f.duration_s), f"flight {f.idx}"], [f"{f.distance_m / 1000:.2f} km", "distance"],
               [f"{f.max_speed_kmh:.1f} km/h", "max speed"], [f"{f.avg_speed_kmh:.1f} km/h", "avg speed"],
               [mmss(f.start_s), "start"], [f.ended_by, "ended by"]]
        items.append({"v": f.duration_s, "x": str(f.idx), "tip": tip, "label": mmss(f.duration_s)})
    top = max([i["v"] for i in items] + [60])
    step = 60 if top <= 600 else 120 if top <= 1500 else 300
    ymax = _nice_max(top * 1.08, step)
    return _bars(items, ymax, lambda v: mmss(v), W=1060, H=240, ystep=step, xlabel="flight #",
                 aria="Duration of each flight")


# ----------------------------------------------------------------------------
# Tables
# ----------------------------------------------------------------------------

def _pill(outcome: str, aborted: bool = False) -> str:
    label = {"clean": "clean", "touchdown": "touchdown", "stop": "full stop"}[outcome]
    if aborted:
        label += " (aborted)"
    return f'<span class="pill {outcome}"><i></i>{label}</span>'


def _wind_notice(a: Analysis) -> str:
    """Warn when the jibe/tack split rests on a weak wind estimate."""
    w, s = a.wind, a.stats
    if w.method == "user" or not s.turns_total:
        return ""
    if w.from_deg is None:
        return '<p class="notice">Wind direction could not be estimated (not enough drift while standing still), so every turn is counted as a jibe.</p>'
    if w.confidence == "low" or (s.tacks_total and s.tacks_total >= s.jibes_total):
        return (f'<p class="notice">Wind direction is a rough estimate ({compass(w.from_deg)}, from {w.method}), so the split into '
                f'{s.jibes_total} jibe{"" if s.jibes_total == 1 else "s"} and {s.tacks_total} tack{"" if s.tacks_total == 1 else "s"} may be wrong. The turn table lists every turn.</p>')
    return ""


def _jibe_table(a: Analysis) -> str:
    rows = ["<table><thead><tr><th>#</th><th>time</th><th class=l>turn</th><th class=l>kind</th><th>entry</th><th>min</th>"
            "<th>exit</th><th>+10 s</th><th>retained</th><th>score</th><th class=l>outcome</th></tr></thead><tbody>"]
    for t in a.turns:
        rows.append(f"<tr class=\"{t.kind}\"><td>{t.idx}</td><td>{mmss(t.mid_s)}</td><td class=l>{t.direction} {abs(t.heading_change_deg):.0f}°</td>"
                    f"<td class=l>{t.kind}</td><td>{t.entry_speed_kmh:.1f}</td><td>{t.min_speed_kmh:.1f}</td><td>{t.exit_speed_kmh:.1f}</td>"
                    f"<td>{t.speed_after_kmh:.1f}</td><td>{t.retention * 100:.0f}%</td><td>{t.score:.0f} {t.grade}</td>"
                    f"<td class=l>{_pill(t.outcome, t.aborted)}</td></tr>")
    rows.append("</tbody></table><p class=sub style='margin:8px 0 0'>Speeds in km/h. Retained = exit speed ÷ entry speed (6 s averages either side of the turn).</p>")
    s = a.stats
    cap = f"{s.turns_total} turn{'' if s.turns_total == 1 else 's'} entered at foiling speed: {s.jibes_total} jibe{'' if s.jibes_total == 1 else 's'} (counted in the summary above)"
    if s.tacks_total:
        cap += f" and {s.tacks_total} tack{'' if s.tacks_total == 1 else 's'} (shown greyed, not counted as jibes)"
    if not a.turns:
        return "<p class=sub>No turns detected.</p>"
    return f"<p class=sub style='margin:0 0 10px'>{cap}.</p>" + "".join(rows)


def _flight_table(a: Analysis) -> str:
    rows = ["<table><thead><tr><th>#</th><th>start</th><th>length</th><th>distance</th><th>max</th><th>avg</th><th class=l>ended by</th></tr></thead><tbody>"]
    for f in a.flights:
        rows.append(f"<tr><td>{f.idx}</td><td>{mmss(f.start_s)}</td><td>{mmss(f.duration_s)}</td><td>{f.distance_m / 1000:.2f} km</td>"
                    f"<td>{f.max_speed_kmh:.1f}</td><td>{f.avg_speed_kmh:.1f}</td><td class=l>{html.escape(f.ended_by)}</td></tr>")
    rows.append("</tbody></table>")
    return "".join(rows)


def _stop_table(a: Analysis) -> str:
    if not a.stops:
        return "<p class=sub>No full stops detected.</p>"
    rows = ["<table><thead><tr><th>#</th><th>time</th><th>speed before</th><th>still</th><th>back flying</th><th class=l>likely</th><th class=l>where</th></tr></thead><tbody>"]
    for f in a.stops:
        where = f"during jibe {f.in_turn}" if f.in_turn else "on a straight"
        rec = mmss(f.recovery_s) if f.recovery_s is not None else "never"
        rows.append(f"<tr><td>{f.idx}</td><td>{mmss(f.time_s)}</td><td>{f.speed_before_kmh:.1f} km/h</td><td>{mmss(f.stopped_s)}</td>"
                    f"<td>{rec}</td><td class=l>{html.escape(f.likely)}</td><td class=l>{where}</td></tr>")
    rows.append(f"</tbody></table><p class=sub style='margin:8px 0 0'>Quick restart = flying again within {a.params.water_stop_s} s "
                "(probably stayed on the board). Long stop = probably in the water. The watch cannot tell the two apart directly.</p>")
    return "".join(rows)


def _method(a: Analysis) -> str:
    p = a.params
    w = a.wind
    s = a.stats
    if w.method == "user":
        wind_txt = "set by you"
    elif w.method == "turn headings":
        wind_txt = (f"estimated from your turns: wingfoil turns are nearly always jibes, so the average mid-turn heading of "
                    f"{w.turns_used} turns (which agree {w.turn_agreement * 100:.0f}%) is taken as downwind, confidence {w.confidence}")
        if w.drift_downwind_deg is not None:
            wind_txt += (f"; as a cross-check you drifted {w.drift_m:.0f} m over {mmss(w.drift_s)} while stopped, towards "
                         f"{compass(w.drift_downwind_deg)}")
    elif w.from_deg is not None:
        wind_txt = (f"estimated from how you drifted while stopped ({w.drift_m:.0f} m over {mmss(w.drift_s)}), "
                    f"confidence {w.confidence}, because there were too few turns to use")
    else:
        wind_txt = "unknown, so every turn is reported as a jibe"
    gps_txt = (f" {s.gps_points_dropped} of {s.gps_points_total} GPS points were rejected as glitches (they implied more than "
               f"{s.gps_speed_cap_kmh:.0f} km/h, typically a dropout followed by a position jump) and bridged by interpolation."
               if s.gps_points_dropped else "")
    return f"""
<p><b>Resampling.</b> The Garmin track is "smart recorded" (a point only when something changes), so it is first
interpolated to one sample per second. Speed comes from the {a.track.speed_source} ({a.track.device or 'watch'}).{gps_txt}</p>
<p><b>Flying.</b> The watch cannot see the board, so flight is inferred from speed: you are counted as flying once speed
reaches {p.fly_on_kmh:.0f} km/h and until it drops below {p.fly_off_kmh:.0f} km/h. Flights shorter than {p.min_flight_s} s are ignored.
Speeds between {p.stop_kmh:.0f} and {p.fly_off_kmh:.0f} km/h count as taxiing; below {p.stop_kmh:.0f} km/h you are in the water.</p>
<p><b>Turns.</b> Heading is taken from the GPS track over a 4 s window. A sustained heading rate above {p.turn_rate_deg_s:.0f}°/s that adds
up to at least {p.turn_min_deg:.0f}° is a turn. A smaller turn (over {p.aborted_turn_min_deg:.0f}°) that ends in a full stop is counted as an aborted attempt.
Only turns entered at foiling speed are listed.</p>
<p><b>Jibe or tack.</b> Wind direction is {wind_txt}. A turn whose mid-point heading is within 90° of downwind is a jibe, otherwise a tack.</p>
<p><b>Outcome and quality.</b> <i>Clean</i>: speed never dropped below {p.fly_off_kmh:.0f} km/h through the turn and 5 s after.
<i>Touchdown</i>: flight was lost but you kept moving. <i>Full stop</i>: speed fell below {p.stop_kmh:.0f} km/h within {p.fall_window_s} s after the turn.
Retention is the mean speed in the {p.exit_window_s} s after the turn divided by the mean speed in the {p.entry_window_s} s before.
Score = 60% retention + 40% (minimum speed ÷ entry speed), capped at 100; a touchdown caps at 54 and a full stop scores 0.
Grades: A ≥ 85, B ≥ 70, C ≥ 55, D below, F for a full stop.</p>
<p><b>Full stops.</b> Any flight followed by a standstill (below {p.stop_kmh:.0f} km/h) within {p.fall_window_s} s. The stop at the end of the
session is not counted. GPS speed reads zero whether you are in the water or standing on the board, so stops are split by how long it took
to fly again: within {p.water_stop_s} s is a <i>quick restart</i> (probably still on the board), longer is a <i>long stop</i>
(probably a swim). Tune the split with <code>--water-stop</code>.</p>
"""


# ----------------------------------------------------------------------------

def html_report(a: Analysis, csv_summary: Optional[LapSummary] = None) -> str:
    s = a.stats
    tr = a.track
    title = html.escape(s.name or "Wingfoil session")
    sub = f"{html.escape(s.start_utc)} · {s.distance_m / 1000:.2f} km in {mmss(s.duration_s)} · {html.escape(s.device or 'Garmin')}"
    if csv_summary:
        sub += f" · Garmin summary: {mmss(csv_summary.duration_s)}, {csv_summary.distance_m / 1000:.2f} km, max {csv_summary.max_speed_kmh:.1f} km/h"
    kpis = "".join([
        _kpi("Time in the air", mmss(s.flying_s), f"{s.flying_pct:.0f}% of the session · {s.flying_distance_m / 1000:.1f} km flying", hero=True),
        _kpi("Flights", str(s.flights), f"longest {mmss(s.longest_flight_s)} · median {mmss(s.median_flight_s)}"),
        _kpi("Jibes landed", f"{s.jibes_clean} / {s.jibes_total}", f"{s.jibe_success_pct:.0f}% clean · {s.jibes_touchdown} touchdown · {s.jibes_stop} full stop"
             + (f" · {s.tacks_total} tack{'' if s.tacks_total == 1 else 's'} not counted" if s.tacks_total else "")),
        _kpi("Speed kept through jibes", f"{s.jibe_avg_retention_pct:.0f}%", f"avg score {s.jibe_avg_score:.0f}/100 · right {s.jibes_right_clean}/{s.jibes_right}, left {s.jibes_left_clean}/{s.jibes_left}"),
        _kpi("Full stops", str(s.stops), f"{s.stops_quick} quick restart · {s.stops_long} long (likely in water) · {s.stops_in_turns} in jibes"),
        _kpi("Top speed (km/h)", f"{s.max_speed_kmh:.1f}", f"avg flying {s.avg_speed_flying_kmh:.1f} km/h · in water {mmss(s.stopped_s)}"),
    ])
    data = {
        "speed": [round(float(v), 1) for v in tr.speed],
        "flying": [int(v) for v in a.flying],
        "hr": [None if np.isnan(v) else round(float(v)) for v in tr.hr],
        "heading": [None if np.isnan(v) else round(float(v)) for v in tr.heading],
        "stop": a.params.stop_kmh,
    }
    page = PAGE.substitute(
        title=title, subtitle=sub, css=CSS, js=JS, kpis=kpis, notice=_wind_notice(a),
        timeline=_timeline(a), map=_map(a), retention=_retention_chart(a), flights_chart=_flights_chart(a),
        jibe_table=_jibe_table(a), flight_table=_flight_table(a), stop_table=_stop_table(a), method=_method(a),
        data=json.dumps(data).replace("</", "<\\/"),
    )
    return page
