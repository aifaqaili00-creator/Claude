'use strict';
/* App shell: hash router (#/view/param...), theme, and the status poll every view shares. */
const VIEWS = {};
const DEFAULT_VIEW = 'check';
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
  currentView = name;
  document.querySelectorAll('#nav a').forEach(a => {
    if (a.dataset.view === name) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  document.querySelectorAll('section.view').forEach(s => s.classList.toggle('on', s.id === 'v-' + (v.section || name)));
  $('pageTitle').textContent = typeof v.title === 'function' ? v.title(params) : v.title;
  $('pageSub').textContent = (typeof v.sub === 'function' ? v.sub(params) : v.sub) || '';
  document.title = $('pageTitle').textContent + ' · Product Checker';
  try { localStorage.setItem('pcView', name); } catch (e) {}
  if (changed) window.scrollTo(0, 0);
  if (v.onShow) v.onShow(params);
}
window.addEventListener('hashchange', route);

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
applyTheme(themePref());

/* ---------- status ---------- */
const BROWSER_STATE = {ready: ['good', 'Checker ready'], starting: ['warn', 'Starting checker…'], error: ['bad', 'Checker error'],
                       stopped: ['weak', 'Checker stopped']};
function renderStatus() {
  const b = ST.browser || {};
  const bs = BROWSER_STATE[b.state] || ['weak', b.state || 'Checker'];
  $('checkerPill').className = 'pill ' + bs[0];
  $('checkerPill').title = b.error || (b.visible ? 'The checker browser is visible' : 'Runs minimised in the background');
  $('checkerPill').innerHTML = '<i></i><span>' + esc(bs[1]) + '</span>';
  let h = '';
  for (const c of Object.keys(MK)) {
    const l = (ST.locations || {})[c] || {}, m = (ST.markets || {})[c] || {};
    const cls = l.ok === true ? 'good' : l.ok === false ? 'warn' : 'weak';
    const tip = l.ok === false ? 'Amazon shows “' + (l.text || '?') + '”, wanted ' + (m.location || '') + '. Open Settings to fix it.'
      : l.text ? 'Amazon delivers to: ' + l.text : 'Checked on the first search';
    h += `<span class="pill ${cls}" title="${esc(tip)}"><i></i>${MK_SHORT[c]} · ${esc(m.location || '')}</span>`;
  }
  const prof = (ST.profiles || []).find(p => p.id === (ST.settings || {}).profile);
  h += `<span class="pill line" title="Links and Helium 10 open in this Chrome profile">Chrome: ${esc(prof ? prof.name : 'default')}</span>`;
  $('status').innerHTML = h;
}

let polling = false;
async function poll() {
  if (polling) return;
  polling = true;
  try {
    const s = await api('/api/state?since=' + lastMsg, undefined);
    const first = !ST.version;
    ST = s;
    if (first && s.settings && s.settings.theme && s.settings.theme !== themePref()) applyTheme(s.settings.theme);
    const fresh = s.messages || [];
    fresh.forEach(m => { lastMsg = Math.max(lastMsg, m.t); });
    renderStatus();
    stateListeners.forEach(fn => { try { fn(s, fresh); } catch (e) { console.error(e); } });
  } catch (e) {
    $('status').innerHTML = '<span class="pill bad"><i></i>' + esc(e.status === 0 ? 'App stopped. Start it again with start.bat' : e.message) + '</span>';
  } finally { polling = false; }
}

$('btnShow').addEventListener('click', () => api('/api/browser', {action: 'show'}).catch(e => toast(e.message)));
$('btnH10').addEventListener('click', () => openUrl('https://members.helium10.com/'));

/* Views register themselves in the scripts that follow; start routing once everything is loaded. */
window.addEventListener('DOMContentLoaded', () => {
  if (!location.hash) {
    let v = null;
    try { v = localStorage.getItem('pcView'); } catch (e) {}
    if (v && VIEWS[v]) { history.replaceState(null, '', '#/' + v); }
  }
  route();
  poll();
  setInterval(poll, 2000);
});
