// Drives the static page in headless Chrome over the DevTools protocol (no dependencies: Node >= 22
// has fetch and WebSocket).  Seeds a stale history record, reloads, opens it, and prints a JSON
// summary that tests/test_web_history.py asserts on.  Usage: node web_history_check.mjs <page url> <profile dir>
import { spawn } from 'node:child_process';
import fs from 'node:fs';
const [BASE, prof] = process.argv.slice(2);
const chromeBin = process.env.CHROME || 'google-chrome';
fs.rmSync(prof, { recursive: true, force: true }); fs.mkdirSync(prof, { recursive: true });
const chrome = spawn(chromeBin, ['--headless=new', '--disable-gpu', '--no-sandbox', `--user-data-dir=${prof}`, '--remote-debugging-port=0', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
const wsUrl = await new Promise((res, rej) => { let buf = ''; chrome.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\/\/\S+)/); if (m) res(m[1]); }); setTimeout(() => rej(new Error('no devtools url\n' + buf)), 20000); });
const port = new URL(wsUrl).port;
const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })).json();
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise(r => ws.onopen = r);
let id = 0; const pending = new Map(); const logs = [];
ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } else if (m.method === 'Runtime.consoleAPICalled') logs.push(m.params.args.map(a => a.value ?? a.description).join(' ')); };
const send = (method, params = {}) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function ev(expr) { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }); if (r.result.exceptionDetails) throw new Error('page error: ' + (r.result.exceptionDetails.exception?.description || JSON.stringify(r.result.exceptionDetails)).slice(0, 600)); return r.result.result.value; }
async function goto(url) { await send('Page.navigate', { url }); for (let i = 0; i < 100; i++) { await sleep(100); if (await ev('document.readyState') === 'complete' && await ev('typeof window.__wingstats') === 'object') break; } }
async function waitFor(expr, ms = 20000) { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (await ev(expr)) return true; await sleep(150); } return false; }
const rows = () => ev(`[...document.querySelectorAll('#history-table tbody tr')].map(tr => [...tr.querySelectorAll('td')].map(td => td.textContent.trim()))`);
const status = () => ev(`document.querySelector('#status').textContent`);
await send('Runtime.enable'); await send('Page.enable');
const out = {};
try {
  await goto(BASE);
  out.emptyAtStart = await ev(`!document.querySelector('#history-empty').classList.contains('hidden')`);
  // A record written by an older build: wrong numbers, old version, wind forced from north so the session gets tacks.
  await ev(`(async () => { const text = await (await fetch('../data/activity_24669790521.tcx')).text();
    await __wingstats.dbPut({ id: 'seed', version: 'old', filename: 'activity_24669790521.tcx', name: 'Seeded stale row', start_utc: '2026-10-10 10:00:00 UTC', start_ms: Date.UTC(2026, 9, 10, 10),
      duration_s: 3357, distance_m: 15630, flying_s: 2472, flying_pct: 74, flights: 16, longest_flight_s: 556, jibes_total: 99, jibes_clean: 98, jibe_success_pct: 99, jibe_avg_retention_pct: 99,
      stops: 13, stops_long: 6, max_speed_kmh: 29.5, params: { windFrom: 0 }, text }); return true; })()`);
  await goto(BASE);
  await waitFor(`document.querySelector('#status').textContent.startsWith('Updated')`);
  out.statusAfterReload = await status(); out.rowsAfterReload = await rows();
  out.expected = await ev(`(async () => { const text = await (await fetch('../data/activity_24669790521.tcx')).text(); const s = wingstats.analyse(wingstats.resample(wingstats.loadTrack('a.tcx', text)), Object.assign(wingstats.defaultParams(), { windFrom: 0 })).stats; return { jibes: s.jibes_clean + ' / ' + s.jibes_total, tacks: String(s.tacks_total), turns: s.turns_total }; })()`);
  out.storedVersions = await ev(`__wingstats.dbAll().then(r => r.map(x => x.version + ' ' + x.id))`);
  await ev(`document.querySelector('#history-table [data-open]').click(); true`);
  await waitFor(`!document.querySelector('#report').classList.contains('hidden') && document.querySelector('#report .kpi')`);
  await sleep(500);
  out.kpiJibes = await ev(`[...document.querySelectorAll('#report .kpi')].map(k => k.querySelector('.label').textContent + ': ' + k.querySelector('.value').textContent + ' (' + k.querySelector('.note').textContent + ')').filter(x => x.startsWith('Jibes'))[0]`);
  out.turnCaption = await ev(`[...document.querySelectorAll('#report p.sub')].map(p => p.textContent).find(t => t.includes('entered at foiling speed'))`);
  out.tackRowsInTable = await ev(`document.querySelectorAll('#report tr.tack').length`);
  out.hollowMarkers = await ev(`document.querySelectorAll('#report svg .m.tack').length`);
  out.retentionBars = await ev(`document.querySelectorAll('#report svg[aria-label="Speed retained through each jibe"] g[data-tip]').length`);
  out.tipTitles = await ev(`[...new Set([...document.querySelectorAll('#report svg g[data-tip]')].map(g => JSON.parse(g.dataset.tip)[0][0].split(' ')[0]))]`);
  out.rowsAfterOpen = await rows();
  out.mapTiles = await ev(`[...document.querySelectorAll('#track-map image')].map(i => new URL(i.getAttribute('href')).host)`);
  out.trimNote = await ev(`document.querySelector('#report p.sub').textContent.includes('trimmed')`);
  await ev(`(() => { const c = document.querySelector('#basemap-toggle'); c.checked = false; c.dispatchEvent(new Event('change')); return true; })()`);
  out.mapTilesOff = await ev(`document.querySelectorAll('#track-map image').length`);
  out.mapPrefStored = await ev(`localStorage.getItem('wingstats.basemap')`);
  await ev(`(() => { const c = document.querySelector('#basemap-toggle'); c.checked = true; c.dispatchEvent(new Event('change')); return true; })()`);
  await goto(BASE); await sleep(1500);
  out.statusSecondReload = await status(); out.rowsSecondReload = await rows();
  // Clicking a bar in the history charts opens that session
  out.chartBars = await ev(`document.querySelectorAll('#history-charts svg g[data-open]').length`);
  out.chartCount = await ev(`document.querySelectorAll('#history-charts svg').length`);
  await ev(`document.querySelector('#report').classList.add('hidden'); document.querySelector('#history-charts svg g[data-open]').dispatchEvent(new MouseEvent('click', { bubbles: true })); true`);
  await waitFor(`!document.querySelector('#report').classList.contains('hidden') && document.querySelector('#report h1')`);
  out.openedFromChart = await ev(`document.querySelector('#report h1')?.textContent`);
  out.consoleErrors = logs.filter(l => /error|could not/i.test(l));  // before the deliberately broken file below
  // Multi-file drop: new sessions are analysed, duplicates (same name or same start time), other
  // formats and unreadable files are reported and skipped.
  out.multi = await ev(`(async () => { const text = await (await fetch('../data/activity_24669790521.tcx')).text();
    const shift = y => text.replace(/<Time>2026-/g, '<Time>' + y + '-').replace(/<Id>2026-/g, '<Id>' + y + '-');
    const f = (name, body) => new File([body], name, { type: 'text/plain' });
    await __wingstats.handleFiles([f('ride_a.tcx', shift(2025)), f('ride_b.tcx', shift(2024)), f('ride_c.tcx', text), f('ride_a.tcx', shift(2025)), f('notes.txt', 'hello'), f('bad.tcx', '<nonsense>')]);
    return { status: document.querySelector('#status').textContent, error: document.querySelector('#error').textContent, errorHidden: document.querySelector('#error').classList.contains('hidden'),
      rows: [...document.querySelectorAll('#history-table tbody tr')].map(tr => tr.querySelector('td').textContent.trim()), reportTitle: document.querySelector('#report h1')?.textContent }; })()`);
  // Clear all data: sessions, settings and the open report are gone
  await ev(`(async () => { localStorage.setItem('wingstats.basemap', 'off'); await __wingstats.clearAllData(); return true; })()`);
  out.cleared = await ev(`(async () => ({ rows: (await __wingstats.dbAll()).length, prefs: Object.keys(localStorage).filter(k => k.startsWith('wingstats.')).length,
    reportHidden: document.querySelector('#report').classList.contains('hidden'), emptyShown: !document.querySelector('#history-empty').classList.contains('hidden'),
    button: document.querySelector('#clear-history').textContent }))()`);
} catch (e) { out.error = String(e); }
console.log(JSON.stringify(out));
ws.close(); chrome.kill();
