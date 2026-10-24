'use strict';
/* App shell: hash router (#/view/param...), theme, and the status poll every view shares. */
const VIEWS = {};
const DEFAULT_VIEW = 'home';
let ST = {settings: {}, markets: {}, locations: {}, profiles: []};
let lastMsg = 0;
let currentView = null;
const stateListeners = [];

/* Register a view. def = {title, sub, onShow(params), onHide()} */
function view(name, def) { VIEWS[name] = def; }
/* Called with (state, newMessages) after every poll. */
function onState(fn) { stateListeners.push(fn); }

function parseHash() {
  const parts = (location.hash || '').replace(/^#\/?/, '').split('/').filter(Boolean).map(p => {
    try { return decodeURIComponent(p); } catch (e) { return p; }
  });
  return {name: parts[0] || '', params: parts.slice(1)};
}
function go(name, ...params) {
  const h = '#/' + [name, ...params].map(encodeURIComponent).join('/');
  if (location.hash === h) route(); else location.hash = h;
}
function route() {
  let {name, params} = parseHash();
  if (!VIEWS[name]) { name = DEFAULT_VIEW; params = []; }
  const v = VIEWS[name], changed = name !== currentView;
  if (changed && currentView && VIEWS[currentView].onHide) VIEWS[currentView].onHide();
  // A window left open on the page you leave (a board card, the PDF options) would stay modal while hidden and make every
  // click on the new page do nothing: close it.
  if (changed) document.querySelectorAll('dialog[open]').forEach(d => { try { d.close(); } catch (e) { d.removeAttribute('open'); } });
  currentView = name;
  document.querySelectorAll('#nav a').forEach(a => {
    if (a.dataset.view === name) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  if ($('navMore').querySelector('[aria-current]')) showMore(true);
  document.querySelectorAll('section.view').forEach(s => s.classList.toggle('on', s.id === 'v-' + (v.section || name)));
  $('pageTitle').textContent = typeof v.title === 'function' ? v.title(params) : v.title;
  $('pageSub').textContent = (typeof v.sub === 'function' ? v.sub(params) : v.sub) || '';
  document.title = $('pageTitle').textContent + ' · Product Checker';
  try { localStorage.setItem('pcView', name); } catch (e) {}
  if (changed) window.scrollTo(0, 0);
  trail();
  if (v.onShow) v.onShow(params);
}
window.addEventListener('hashchange', route);

/* ---------- Back: every page, tab, market and niche you open is its own address, so Back returns to the one before ---------- */
const TRAIL = [];
function trail() {
  const h = location.hash || '#/';
  if (TRAIL.length > 1 && TRAIL[TRAIL.length - 2] === h) TRAIL.pop();       // you went back
  else if (TRAIL[TRAIL.length - 1] !== h) TRAIL.push(h);
  if (TRAIL.length > 200) TRAIL.splice(0, TRAIL.length - 200);
  $('btnBack').hidden = TRAIL.length < 2;
}
/* Change the address for a step inside a page (a tab, a market, a niche) without reading the page again. */
function step(name, ...params) {
  const h = '#/' + [name, ...params.filter(p => p != null && p !== '')].map(encodeURIComponent).join('/');
  if (location.hash !== h) location.hash = h;
}
$('btnBack').addEventListener('click', () => history.back());

/* ---------- theme: light (default), dark, or the same as Windows ---------- */
const darkMQ = window.matchMedia ? matchMedia('(prefers-color-scheme: dark)') : null;
function themePref() { return document.documentElement.getAttribute('data-theme-pref') || 'light'; }
function applyTheme(pref) {
  pref = ['light', 'dark', 'system'].includes(pref) ? pref : 'light';
  const dark = pref === 'dark' || (pref === 'system' && !!darkMQ && darkMQ.matches);
  const root = document.documentElement, was = root.getAttribute('data-theme');
  root.setAttribute('data-theme', dark ? 'dark' : 'light');
  root.setAttribute('data-theme-pref', pref);
  try { localStorage.setItem('pcTheme', pref); } catch (e) {}
  ['themeSeg', 'themeSeg2'].forEach(id => { if ($(id)) setSeg($(id), pref); });
  if (was !== root.getAttribute('data-theme')) window.dispatchEvent(new CustomEvent('pc:theme'));   // charts redraw
}
function setTheme(pref) {
  applyTheme(pref);
  api('/api/settings', {theme: pref}).then(r => { ST.settings = r.settings; }).catch(e => toast(e.message));
}
if (darkMQ) darkMQ.addEventListener('change', () => applyTheme(themePref()));
seg($('themeSeg'), setTheme);
seg($('themeSeg2'), setTheme);
applyTheme(PRINT ? 'light' : themePref());

/* ---------- the simple view: parts marked .adv show only with "Show all details"; a PDF always has everything ---------- */
function applyDetails(all) {
  all = !!all || !!PRINT;
  document.documentElement.classList.toggle('simple', !all);
  if (!PRINT) try { localStorage.setItem('pcDetails', all ? '1' : '0'); } catch (e) {}
  ['detailsSw', 'detailsSw2'].forEach(id => { if ($(id)) $(id).checked = all; });
  window.dispatchEvent(new CustomEvent('pc:details'));
}
function setDetails(all) {
  applyDetails(all);
  api('/api/settings', {show_details: all}).then(r => { ST.settings = r.settings; }).catch(e => toast(e.message));
}
['detailsSw', 'detailsSw2'].forEach(id => $(id).addEventListener('change', e => setDetails(e.target.checked)));
$('detailsOn').addEventListener('click', () => setDetails(true));
applyDetails(!document.documentElement.classList.contains('simple'));

/* ---------- the sidebar's More group: closed until you open it, and open while you are on one of its pages ---------- */
function showMore(open, remember) {
  $('navMore').hidden = !open;
  $('navMoreBtn').setAttribute('aria-expanded', String(open));
  if (remember) try { localStorage.setItem('pcNavMore', open ? '1' : '0'); } catch (e) {}
}
$('navMoreBtn').addEventListener('click', () => showMore($('navMore').hidden, true));
try { showMore(localStorage.getItem('pcNavMore') === '1'); } catch (e) { showMore(false); }

/* ---------- status ---------- */
const BROWSER_STATE = {ready: ['good', 'Checker ready'], starting: ['warn', 'Starting checker…'], error: ['bad', 'Checker error'],
                       stopped: ['weak', 'Checker stopped']};
function renderStatus() {
  const b = ST.browser || {};
  const bs = BROWSER_STATE[b.state] || ['weak', b.state || 'Checker'];
  if (ST.version) $('appVersion').textContent = 'Version ' + ST.version;
  $('checkerPill').className = 'pill ' + bs[0];
  $('checkerPill').title = b.error || (b.visible ? 'The checker browser is visible' : 'Runs minimised in the background');
  $('checkerPill').innerHTML = '<i></i><span>' + esc(bs[1]) + '</span>';
  let h = ST.jobs_running ? `<button type="button" class="pill accent" id="jobsPill" aria-expanded="${jobsOpen}" title="Checks and scans you started are still running. Click to see them or stop one."><span class="spin" style="width:11px;height:11px"></span>Working${ST.jobs_running > 1 ? ' (' + ST.jobs_running + ')' : ''} · Stop…</button>` : '';
  const up = ST.update, skip = (() => { try { return localStorage.getItem('pcSkipUpdate'); } catch (e) { return null; } })();
  if (up && up.version !== skip) {
    h = `<button type="button" class="pill good" id="updPill" aria-expanded="${updOpen}" title="A newer Product Checker is published">${icon('download')}Version ${esc(up.version)} available</button>` + h;
    if (updOpen) h += `<div class="jobs-pop" role="dialog" aria-label="Update"><b>Product Checker ${esc(up.version)}</b> <span class="small muted">(you have ${esc(ST.version)})</span>
      ${up.notes ? `<div class="small">${esc(up.notes)}</div>` : ''}
      <div class="small muted">${ST.frozen ? 'Download it, close Product Checker, then put the new ProductChecker.exe in place of the old one and start it. Your data stays.'
        : 'Download the new version (or run git pull in the app folder), then start it with start.bat. Your data stays.'}</div>
      <div class="row"><button type="button" class="btn slim primary" data-upd="get">${icon('download')}Download</button>
        ${up.page && up.page !== up.download ? '<button type="button" class="btn slim" data-upd="page">What’s new</button>' : ''}
        <button type="button" class="btn slim ghost" data-upd="skip">Not now</button></div></div>`;
  }
  if (!ST.jobs_running) jobsOpen = false;
  if (jobsOpen) h += `<div class="jobs-pop" role="dialog" aria-label="Running jobs">${(ST.jobs || []).map(j => `<div class="row" style="justify-content:space-between;gap:8px">
      <span class="small"><b>${esc(JOB_NAMES[j.kind] || j.kind)}</b> · ${esc(j.line)}<span class="muted"> · ${ago(j.started)}</span></span>
      <button type="button" class="btn slim" data-stopjob="${esc(j.id)}">${icon('x')}Stop</button></div>`).join('')}</div>`;
  for (const c of Object.keys(MK)) {
    const l = (ST.locations || {})[c] || {}, m = (ST.markets || {})[c] || {};
    const cls = l.ok === true ? 'good' : l.ok === false ? 'warn' : 'weak';
    const tip = l.ok === false ? 'Amazon shows “' + (l.text || '?') + '”, wanted ' + (m.location || '') + '. Open Settings to fix it.'
      : l.text ? 'Amazon delivers to: ' + l.text : 'Checked on the first search';
    h += `<span class="pill ${cls}${l.ok === false ? '' : ' adv'}" title="${esc(tip)}"><i></i>${mkFlag(c, true)} · ${esc(m.location || '')}</span>`;
  }
  const prof = (ST.profiles || []).find(p => p.id === (ST.settings || {}).profile);
  h += `<span class="pill line adv" title="Links and Helium 10 open in this Chrome profile">Chrome: ${esc(prof ? prof.name : 'default')}</span>`;
  $('status').innerHTML = h;
}

/* What happened in this window goes to app.log (clicks, messages shown, script errors): it is how a hang is found. */
const pageLog = (kind, text) => { try { PC.request('/api/clientlog', {method: 'POST', body: {kind, text: String(text).slice(0, 300)}}).catch(() => {}); } catch (e) {} };
document.addEventListener('click', e => {
  const b = e.target.closest('button, a.btn, a[href^="#/"], [data-open]');
  if (b) pageLog('click', (currentView || '') + ' | ' + (b.id ? '#' + b.id + ' ' : '') + (b.textContent || b.title || '').trim().slice(0, 60) +
    (b.disabled ? ' (disabled)' : '') + (b.dataset.open ? ' → ' + b.dataset.open.slice(0, 80) : ''));
}, true);
window.addEventListener('error', e => pageLog('error', (e.message || 'error') + ' at ' + (e.filename || '').split('/').pop() + ':' + (e.lineno || '')));
window.addEventListener('unhandledrejection', e => pageLog('error', 'unhandled: ' + ((e.reason && (e.reason.message || e.reason)) || '?')));
let jobsOpen = false, updOpen = false;
const JOB_NAMES = {mgaps: 'Market Gaps refresh', stock: 'Out of stock check', gaps: 'Gap Finder', check: 'Check', ideas: 'Product ideas',
  report: 'Keyword report', top: 'Reading the top listings', mgaps_import: 'SmartScout import', import: 'Helium 10 import',
  shortlist: 'Shortlist'};
$('status').addEventListener('click', async e => {
  if (e.target.closest('#jobsPill')) { jobsOpen = !jobsOpen; renderStatus(); return; }
  if (e.target.closest('#updPill')) { updOpen = !updOpen; renderStatus(); return; }
  const u = e.target.closest('[data-upd]');
  if (u && ST.update) {
    if (u.dataset.upd === 'get') openUrl(ST.update.download);
    if (u.dataset.upd === 'page') openUrl(ST.update.page);
    if (u.dataset.upd === 'skip') { try { localStorage.setItem('pcSkipUpdate', ST.update.version); } catch (err) {} }
    updOpen = false; renderStatus(); return;
  }
  const b = e.target.closest('[data-stopjob]');
  if (b) {
    b.disabled = true;
    try { await api('/api/job/stop', {id: b.dataset.stopjob}); toast('Stopped. What was read so far is kept.', 3000); }
    catch (err) { toast(err.message); }
    poll();
  }
});
let polling = false, lost = 0;
async function poll() {
  if (polling) return;
  polling = true;
  try {
    const s = await api('/api/state?since=' + lastMsg, undefined);
    if (lost >= 2) { location.reload(); return; }                            // the app is back: start fresh with it
    lost = 0;
    const first = !ST.version;
    ST = s;
    if (first && !PRINT && s.settings && s.settings.theme && s.settings.theme !== themePref()) applyTheme(s.settings.theme);
    if (first && !PRINT && s.settings && s.settings.show_details !== undefined) applyDetails(s.settings.show_details);
    const fresh = s.messages || [];
    fresh.forEach(m => { lastMsg = Math.max(lastMsg, m.t); });
    renderStatus();
    const n = s.alerts_unseen || 0;
    $('alertCount').hidden = !n; $('alertCount').textContent = n > 99 ? '99+' : n;
    stateListeners.forEach(fn => { try { fn(s, fresh); } catch (e) { console.error(e); } });
  } catch (e) {
    $('status').innerHTML = '<span class="pill bad"><i></i>' + esc(e.status === 0 ? 'App stopped. Start it again with start.bat' : e.message) + '</span>';
    if (e.status === 0 && ++lost >= 2) $('lostBanner').hidden = false;      // this window lost the app: say so plainly
  } finally { polling = false; }
}

$('btnShow').addEventListener('click', () => api('/api/browser', {action: 'show'}).catch(e => toast(e.message)));
$('btnH10').addEventListener('click', () => openUrl('https://members.helium10.com/'));

/* ---------- currency converter (top bar) ---------- */
let fxFrom = 'USD';
try { fxFrom = localStorage.getItem('pcFxFrom') || 'USD'; } catch (e) {}
const fxOpen = () => !$('fxPop').hidden;
function drawFxOut() {
  const a = parseFloat($('fxAmt').value), amt = a >= 0 ? a : 0;
  $('fxOut').innerHTML = CURRENCIES.filter(c => c !== fxFrom).map(c => {
    const r = fxRate(fxFrom, c), v = r == null ? null : amt * r;
    return `<div class="fxrow"><span class="fxcode" title="${CUR_NAME[c]}">${CUR_SYM[c]}</span>
      <button type="button" class="fxval tnum" data-copy="${v == null ? '' : v.toFixed(2)}" title="Copy">${v == null ? '–' : num(v, 2)}${icon('copy')}</button>
      <span class="small muted tnum">1 ${CUR_SYM[fxFrom]} = ${r == null ? '–' : num(r, 4)}</span></div>`;
  }).join('');
  $('fxSrc').textContent = fxNote();
}
function drawConverter() {
  if (!CURRENCIES.includes(fxFrom)) fxFrom = 'USD';
  $('fxFromWrap').innerHTML = curSeg('fxFrom', fxFrom, 'Convert from');
  $('fxMineWrap').innerHTML = curSeg('fxMine', myCur(), 'Your currency');
  drawFxOut();
}
function showConverter(open) {
  $('fxPop').hidden = !open;
  $('btnFxPop').setAttribute('aria-expanded', String(open));
  if (open) { drawConverter(); $('fxAmt').focus(); $('fxAmt').select(); }
}
$('btnFxPop').addEventListener('click', () => showConverter(!fxOpen()));
$('fxClose').addEventListener('click', () => { showConverter(false); $('btnFxPop').focus(); });
$('fxAmt').addEventListener('input', drawFxOut);
$('fxFromWrap').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  fxFrom = b.dataset.v;
  try { localStorage.setItem('pcFxFrom', fxFrom); } catch (err) {}
  drawConverter();
});
$('fxMineWrap').addEventListener('click', e => { const b = e.target.closest('button'); if (b) saveCurrency('price_currency', b.dataset.v); });
$('fxOut').addEventListener('click', e => {
  const b = e.target.closest('[data-copy]'); if (!b || !b.dataset.copy) return;
  navigator.clipboard.writeText(b.dataset.copy).then(() => toast('Copied ' + b.dataset.copy, 1500), () => toast('Could not copy'));
});
$('fxRefresh').addEventListener('click', async () => {
  $('fxRefresh').disabled = true;
  try { ST.fx = await api('/api/fx/refresh', {}); drawFxOut(); toast(ST.fx.source === 'ECB' ? 'Rates updated' : 'Could not reach the ECB; using saved rates', 2500); }
  catch (e) { toast(e.message); }
  $('fxRefresh').disabled = false;
});
document.addEventListener('click', e => {
  const path = e.composedPath();                       // still right when a click redrew the button it landed on
  if (fxOpen() && !path.includes($('fxPop')) && !path.includes($('btnFxPop'))) showConverter(false);
});
document.addEventListener('keydown', e => { if (e.key === 'Escape' && fxOpen()) { showConverter(false); $('btnFxPop').focus(); } });
window.addEventListener('pc:currency', () => { if (fxOpen()) drawConverter(); });
let fxKey = '';
onState(s => {
  const f = s.fx || {}, k = JSON.stringify([f.per_usd, f.as_of, f.source]);
  if (k === fxKey) return;
  fxKey = k;
  if (f.per_usd) $('btnFxPop').title = `1 US$ = A$ ${num(f.per_usd.AUD, 4)} = AED ${num(f.per_usd.AED, 4)} = € ${num(f.per_usd.EUR, 4)} (${fxNote()}). Click to convert.`;
  if (fxOpen()) drawFxOut();
});

/* Views register themselves in the scripts that follow; start routing once everything is loaded. */
window.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.seg button[data-v]').forEach(b => {          // market buttons get their flag
    if (MK[b.dataset.v] && !b.querySelector('.cflag')) b.insertAdjacentHTML('afterbegin', cflag(b.dataset.v, ''));
  });
  if (!location.hash) {
    let v = null;
    try { v = localStorage.getItem('pcView'); } catch (e) {}
    if (v && VIEWS[v]) { history.replaceState(null, '', '#/' + v); }
  }
  route();
  poll();
  setInterval(poll, 2000);
  /* tell the app when you are looking at it, so it does not send you Windows notifications meanwhile */
  const beat = () => { if (document.hasFocus()) api('/api/beat', {focused: true}).catch(() => {}); };
  window.addEventListener('focus', beat);
  setInterval(beat, 15000);
});
