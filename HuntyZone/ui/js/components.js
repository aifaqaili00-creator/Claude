'use strict';
/* Small UI pieces shared by the views. */
function icon(name, cls = 'sm') { return `<svg class="icon ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`; }

function toast(msg, ms = 5000) {
  const box = $('toasts'), t = document.createElement('div');
  t.className = 'toast'; t.textContent = msg;
  box.appendChild(t); setTimeout(() => t.remove(), ms);
  while (box.children.length > 4) box.firstChild.remove();
}

/* Segmented control: single choice, or several when el.dataset.multi is set. */
function seg(el, onChange) {
  el.addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b || !el.contains(b)) return;
    if (el.dataset.multi) b.setAttribute('aria-pressed', b.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
    else el.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', x === b ? 'true' : 'false'));
    if (onChange) onChange(b.dataset.v);
  });
}
const segVal = el => (el.querySelector('[aria-pressed="true"]') || {dataset: {}}).dataset.v;
const pressed = el => [...el.querySelectorAll('[aria-pressed="true"]')].map(b => b.dataset.v);
function setSeg(el, v) { el.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', b.dataset.v === v ? 'true' : 'false')); }

const busy = text => '<span class="spin"></span><span>' + esc(text) + '</span>';
const failPill = msg => '<span class="pill bad">' + icon('x') + esc(msg) + '</span>';

/* Put a product on the board: {title, market, asin, kw, image}. */
async function addToBoard(item) {
  try {
    const r = await api('/api/board/add', item);
    toast((r.exists ? 'Already on your board as PB-' : 'Added to your board as PB-') + r.id, 3500);
    return r;
  } catch (e) { toast(e.message); return null; }
}

/* Delivery level -> status style. Status colours always come with a label. */
const LEVEL = {opportunity: 'good', some: 'warn', crowded: 'bad', none: 'weak', error: 'weak'};
const LEVEL_ICON = {opportunity: 'ok', some: 'warn', crowded: 'x', none: 'dot', error: 'dot'};

/* Origin split, coloured by series (not status): local / overseas / no date. */
const ORIGIN = [
  {key: 'local', label: 'Local', color: 'var(--s-local)'},
  {key: 'overseas', label: 'Overseas', color: 'var(--s-overseas)'},
  {key: 'unknown', label: 'No date shown', color: 'var(--s-other)'},
];
function originBar(parts, total, h = 10) {
  const t = Math.max(1, total);
  return `<div class="bar" style="height:${h}px" role="img" aria-label="${esc(parts.map(p => p.label + ' ' + p.n).join(', '))}">` +
    parts.filter(p => p.n).map(p => `<span title="${esc(p.label)}: ${p.n}" style="width:${p.n / t * 100}%;background:${p.color}"></span>`).join('') + '</div>';
}
function legend(parts) {
  return '<div class="legend">' + parts.filter(p => p.n).map(p => `<span><i style="background:${p.color}"></i>${esc(p.label)} <b class="tnum">${num(p.n)}</b>${p.note ? ' <span class="muted">' + esc(p.note) + '</span>' : ''}</span>`).join('') + '</div>';
}

/* Categorical colours follow the entity (its place in `names`), never its rank. Unknown keys fold into "Other". */
const SERIES = ['var(--s-demand)', 'var(--s-overseas)', 'var(--s-local)', 'var(--s-4)', 'var(--s-5)', 'var(--s-6)', 'var(--s-7)'];
function catParts(obj, names) {
  const keys = Object.keys(names || {}).slice(0, SERIES.length - 1);
  const parts = [];
  let other = 0;
  keys.forEach((k, i) => { const v = (obj || {})[k]; if (v > 0) parts.push({label: names[k], n: v, color: SERIES[i]}); });
  for (const [k, v] of Object.entries(obj || {})) if (!keys.includes(k) && v > 0) other += v;
  if (other) parts.push({label: 'Other', n: other, color: 'var(--s-other)'});
  return parts;
}

/* ---------- currencies: US$, A$, AED and €. The rates arrive with every status poll (ST.fx). ---------- */
const CUR_SYM = {USD: 'US$', EUR: '€', AUD: 'A$', AED: 'AED'};
const CUR_NAME = {USD: 'US dollar', AUD: 'Australian dollar', AED: 'UAE dirham', EUR: 'Euro'};
const MK_CUR = {US: 'USD', AU: 'AUD', AE: 'AED'};
const CURRENCIES = ['USD', 'AUD', 'AED', 'EUR'];
const myCur = () => (ST.settings || {}).price_currency || 'USD';
const costCur = () => (ST.settings || {}).cost_currency || 'USD';
function fxRate(from, to) {
  const p = (ST.fx || {}).per_usd || {};
  return p[from] && p[to] ? p[to] / p[from] : null;
}
function fxConvert(v, from, to) {
  const r = v == null || v !== v ? null : fxRate(from, to);
  return r == null ? null : v * r;
}
const curMoney = (v, code, d = 2) => v == null ? '' : CUR_SYM[code] + ' ' + num(v, d);
/* A marketplace price in your currency ("US$ 30"), or '' when it already is your currency. d = fixed decimals. */
function inMine(v, mk, {short = false, d = null} = {}) {
  const from = MK_CUR[mk] || mk, to = myCur();
  if (from === to) return '';
  const x = fxConvert(v, from, to);
  return x == null ? '' : CUR_SYM[to] + ' ' + (short ? compact(x) : num(x, d ?? (Math.abs(x) >= 100 ? 0 : 2)));
}
const approx = (v, mk, opt) => { const s = inMine(v, mk, opt); return s ? '≈ ' + s : ''; };
const fxSpan = (v, mk, opt) => { const a = approx(v, mk, opt); return a ? ` <span class="fx" title="${esc(fxNote())}">${esc(a)}</span>` : ''; };
const fxSub = (v, mk, opt) => { const a = approx(v, mk, opt); return a ? `<div class="sub fx" title="${esc(fxNote())}">${esc(a)}</div>` : ''; };
const priceFx = (v, mk) => v == null ? '' : curMoney(v, MK_CUR[mk]) + (approx(v, mk) ? ' ' + approx(v, mk) : '');   // plain text
const curSeg = (id, cur, label) => `<div class="seg slim"${id ? ` id="${id}"` : ''} role="group" aria-label="${esc(label)}">` +
  CURRENCIES.map(c => `<button type="button" data-v="${c}" aria-pressed="${c === cur}" title="${CUR_NAME[c]}">${CUR_SYM[c]}</button>`).join('') + '</div>';

/* Supplier prices typed on idea and radar cards, kept across re-renders (key: ASIN), and the last Profit? answer. */
const typedCost = new Map(), profitOut = new Map();
document.addEventListener('input', e => { const k = e.target.dataset && e.target.dataset.cogs; if (k) typedCost.set(k, e.target.value); });
/* A US$ amount in the currency your supplier quotes in. */
const costMoney = usd => { const v = fxConvert(usd, 'USD', costCur()); return v == null ? curMoney(usd, 'USD') : curMoney(v, costCur()); };

/* Save price_currency (your currency) or cost_currency. Changing your currency converts the price range too,
   so it still means the same prices. Views redraw their money on the pc:currency event. */
async function saveCurrency(key, code) {
  const s = ST.settings || {};
  if (!CURRENCIES.includes(code) || (s[key] || 'USD') === code) return;
  const change = {[key]: code};
  const r = key === 'price_currency' && fxRate(s.price_currency || 'USD', code);
  const round = x => Math.max(1, x >= 10 ? Math.round(x) : Math.round(x * 10) / 10);
  if (r && s.price_min != null && s.price_max != null) Object.assign(change, {price_min: round(s.price_min * r), price_max: round(s.price_max * r)});
  try {
    const res = await api('/api/settings', change);
    ST.settings = res.settings; ST.fx = res.fx || ST.fx;
    if (key === 'cost_currency') typedCost.clear();
    window.dispatchEvent(new CustomEvent('pc:currency', {detail: {key}}));
  } catch (e) { toast(e.message); }
}

/* ---------- your price range (Product ideas, Trend radar, Settings) ---------- */
function rangeText(mk) {
  const r = ((ST.fx || {}).ranges || {})[mk];
  return r ? `${CUR_SYM[MK_CUR[mk]]} ${num(r[0], r[0] < 10 ? 1 : 0)}–${num(r[1], r[1] < 10 ? 1 : 0)}` : '';
}
function fxNote() {
  const f = ST.fx || {};
  return (f.source === 'ECB' ? `ECB rates of ${f.as_of}` : 'approximate rates (offline)') + ', AED fixed at 3.6725 per US$';
}
/* Renders min / max / currency and the two filters into el. getMarket() picks which converted range to show.
   onChange() runs after the settings are saved. */
function priceBar(el, getMarket, onChange) {
  const draw = () => {
    const s = ST.settings || {}, mk = getMarket(), cur = s.price_currency || 'USD';
    el.innerHTML = `
      <label class="small"><input type="checkbox" data-pb="price_filter"${s.price_filter !== false ? ' checked' : ''}> Amazon price</label>
      <input type="number" class="pbnum" data-pb="price_min" min="1" step="1" value="${esc(s.price_min ?? 20)}" aria-label="Lowest selling price">
      <span class="small">–</span>
      <input type="number" class="pbnum" data-pb="price_max" min="1" step="1" value="${esc(s.price_max ?? 50)}" aria-label="Highest selling price">
      <span data-pbcur>${curSeg('', cur, 'Your currency')}</span>
      <span class="small muted" data-pbconv title="${esc(fxNote())}">${MK_CUR[mk] !== cur ? '= ' + esc(rangeText(mk)) : ''}</span>
      <label class="small"><input type="checkbox" data-pb="exclude_tech"${s.exclude_tech !== false ? ' checked' : ''}> No phones / laptops</label>`;
  };
  async function save(change) {
    try { const r = await api('/api/settings', change); ST.settings = r.settings; ST.fx = r.fx || ST.fx; draw(); if (onChange) onChange(); }
    catch (e) { toast(e.message); draw(); }
  }
  el.addEventListener('change', e => {
    const k = e.target.dataset.pb; if (!k) return;
    save({[k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value});
  });
  el.addEventListener('click', async e => {
    const b = e.target.closest('[data-pbcur] button'); if (!b || b.dataset.v === (ST.settings || {}).price_currency) return;
    await saveCurrency('price_currency', b.dataset.v);
    draw(); if (onChange) onChange();
  });
  // redraw when the settings or rates arrive or change, but never while you are typing in it
  let last = '';
  const maybe = () => {
    const k = JSON.stringify([ST.settings && [ST.settings.price_min, ST.settings.price_max, ST.settings.price_currency,
                                              ST.settings.price_filter, ST.settings.exclude_tech], (ST.fx || {}).ranges, getMarket()]);
    if (k !== last && !el.contains(document.activeElement)) { last = k; draw(); }
  };
  if (typeof onState === 'function') onState(maybe);
  draw();
  return {draw: maybe};
}

/* ---------- Find on Alibaba + a rough profit check, on idea and radar cards ---------- */
const alibabaUrl = words => 'https://www.alibaba.com/trade/search?SearchText=' + encodeURIComponent((words || '').trim());
function sourcingHtml(key, savedUsd) {
  const c = CUR_SYM[costCur()], saved = savedUsd != null ? fxConvert(savedUsd, 'USD', costCur()) : null;
  const val = typedCost.has(key) ? typedCost.get(key) : saved != null ? +saved.toFixed(2) : '';
  return `<button class="btn slim" data-alibaba="${esc(key)}" title="Search suppliers for this product on Alibaba">Find on Alibaba${icon('external')}</button>
    <span class="profit" data-pkey="${esc(key)}"><input type="number" min="0" step="0.01" class="pbnum" data-cogs="${esc(key)}" value="${esc(val)}" placeholder="Cost" aria-label="Supplier price per piece in ${c}" title="Supplier price per piece in ${c} (change the currency in Settings → Currency)"><span class="small muted">${c}</span>
    <button class="btn slim" data-profit="${esc(key)}" title="Work out the profit and remember this supplier price: the score then uses your real margin">Profit?</button>${savedUsd != null ?
      `<button class="btn slim ghost" data-forget="${esc(key)}" title="Forget your supplier price for this product">Forget</button>` : ''}<span class="small" data-pout="${esc(key)}">${profitOut.get(key) || ''}</span></span>`;
}
/* One shared click handler. find(key) -> {market, price, category, search}; saved() runs after your supplier price
   was remembered or forgotten (the view reloads, so the score uses it). */
function wireSourcing(root, find, saved) {
  const remember = async (key, it, cost) => {
    try { await api('/api/pref', {market: it.market, asin: key, set: {cost, cost_currency: costCur()}}); }
    catch (err) { toast(err.message); return; }
    typedCost.delete(key);
    if (saved) saved();
  };
  root.addEventListener('click', async e => {
    const a = e.target.closest('[data-alibaba]'), p = e.target.closest('[data-profit]'), f = e.target.closest('[data-forget]');
    if (a) { const it = find(a.dataset.alibaba); if (it) openUrl(alibabaUrl(it.search)); }
    if (f) { const it = find(f.dataset.forget); f.disabled = true; profitOut.delete(f.dataset.forget); if (it) remember(f.dataset.forget, it, ''); }
    if (!p) return;
    const key = p.dataset.profit, it = find(key), out = root.querySelector(`[data-pout="${CSS.escape(key)}"]`);
    const inp = root.querySelector(`[data-cogs="${CSS.escape(key)}"]`), cost = parseFloat(inp && inp.value);
    if (!it || !out) return;
    if (!(cost >= 0)) { out.textContent = 'Type the supplier price first'; return; }
    if (it.price == null) { out.textContent = 'No Amazon price to compare with'; return; }
    const s = ST.settings || {};
    out.innerHTML = busy('…');
    try {
      const r = await api('/api/profit', {market: it.market, price: it.price, category: it.category || '', cogs: cost,
                                          cogs_currency: costCur(), registered: !!((s.registered || {})[it.market])});
      const e2 = r.economics, code = MK_CUR[it.market], c = CUR_SYM[code], m = e2.Margin || 0, mine = inMine(e2.Profit, it.market);
      const fees = e2.Referral + e2.FBA + e2.Storage + e2.Placement + e2.FeeTax;
      const tip = `Rough estimate at ${c} ${num(it.price, 2)}: Amazon fees ${c} ${num(fees, 2)}, product + shipping + duty ${c} ${num(e2.Landed, 2)}, ` +
        `ads ${c} ${num(e2.Ads, 2)}, returns ${c} ${num(e2.Returns, 2)}. Assumes a small standard-size item and sea freight; ` +
        'check the real size and weight before ordering.';
      out.innerHTML = `<span class="pill ${e2.Profit > 0 && m >= 0.15 ? 'good' : e2.Profit > 0 ? 'warn' : 'bad'}" title="${esc(tip)}">≈ ${curMoney(e2.Profit, code)}${mine ? ' (' + esc(mine) + ')' : ''} profit / sale · ${Math.round(m * 100)}% margin</span>`;
      profitOut.set(key, out.innerHTML);
      remember(key, it, cost);
    } catch (err) { out.innerHTML = failPill(err.message); }
  });
}

/* ---------- top negative reviews (1–3★) of a keyword's best listings, or of one listing ---------- */
const rvJobs = new Map(), rvTried = new Set();     // report -> running job; listings already read (or tried) this session
function rvHtml(r, q, extra) {
  const items = r ? r.items : [];
  const stars = n => `<span class="rvstars" role="img" aria-label="${n} out of 5 stars">${'★'.repeat(Math.round(n))}<span class="off">${'★'.repeat(5 - Math.round(n))}</span></span>`;
  const when = day => day ? new Date(day + 'T12:00:00').toLocaleDateString(undefined, {day: 'numeric', month: 'short', year: 'numeric'}) : '';
  const list = items.map(i => `<div class="rv">
      <div class="rvhead">${stars(i.stars)}<b>${esc(i.title || 'No title')}</b></div>
      ${i.body ? `<div class="rvbody" tabindex="0" title="Click to read it all">${esc(i.body)}</div>` : ''}
      <div class="sub">${[i.country ? 'Reviewed in ' + cflag(i.country_code, '') + ' ' + esc(i.country) : '', esc(when(i.day)), i.helpful ? num(i.helpful) + ' found it helpful' : '',
        i.verified ? 'verified purchase' : '', q.asin ? '' : `on <a data-open="${esc(i.url)}" title="${esc(i.listing)}">${esc(i.listing.length > 60 ? i.listing.slice(0, 60) + '…' : i.listing)}</a>`,
        `<a data-open="${esc(i.all_url)}" title="Opens in your Chrome">all its critical reviews</a>`].filter(Boolean).join(' · ')}</div>
    </div>`).join('');
  const empty = !r ? '' : !r.searched ? 'Search this keyword first (Refresh now). Then the negative reviews of its top listings are read from Amazon.'
    : r.read_at && !items.length ? 'No 1–3★ reviews among the reviews Amazon showed.' : '';
  const src = r && r.read_at ? `Read ${ago(r.read_at)} from ${!r.critical ? 'the reviews on each product page (Amazon shows only a few there unless you are signed in to Amazon in the checker’s browser)'
    : r.critical < r.read ? 'Amazon’s critical reviews pages and product pages' : 'Amazon’s critical reviews pages'}.` : '';
  const again = r && r.searched && !rvJobs.has(q.market + '|' + (q.asin || q.kw)) ? `<button type="button" class="btn slim noprint" data-rv-read="${r.read_at ? 'force' : ''}">${icon('refresh')}${r.read_at ? 'Read again' : 'Read them now'}</button>` : '';
  return `<div class="head"><h3>Top negative reviews</h3><div class="row">${extra ? `<span class="small">${extra}</span>` : ''}${again}</div></div>
    <div class="small muted">1–3★ reviews ${q.asin ? 'of this listing' : 'of the top ' + ((r && r.asins.length) || 5) + ' listings on page 1'} in ${esc(MK[q.market])}, most helpful first${q.asin ? '' : ', at most 2 from one listing'}.</div>
    ${themesHtml(r)}
    ${list ? `<div class="rvlist">${list}</div>` : empty ? `<div class="small">${empty}</div>` : ''}
    ${src ? `<div class="small muted">${src}</div>` : ''}`;
}
/* What buyers complain about across all the critical reviews read, and what to ask suppliers for. */
function themesHtml(r) {
  const t = (r && r.themes) || [];
  if (!t.length) return '';
  const tone = x => x.share >= 0.25 ? 'bad' : x.share >= 0.1 ? 'warn' : 'line';
  return `<div class="themes stackv"><div class="row tight"><span class="small"><b>What buyers complain about</b> (${num(r.n_reviews)} critical reviews${r.other ? ', ' + num(r.other) + ' about other things' : ''}):</span>
      ${t.map(x => `<span class="pill ${tone(x)}" title="${esc(x.ask)}">${esc(x.name)} · ${x.n}</span>`).join('')}
      <button type="button" class="btn slim noprint" data-rv-brief title="Copy a short brief for suppliers: each complaint and what to ask for">${icon('copy')}Copy supplier brief</button></div>
    <ul class="gapwhy">${t.map(x => `<li><b>${esc(x.name)}</b> <span class="muted">(${x.n} review${x.n === 1 ? '' : 's'}${x.listings > 1 ? ', ' + x.listings + ' listings' : ''})</span>: ${esc(x.ask)}${x.quotes[0] ? ` <span class="muted">“${esc(x.quotes[0])}”</span>` : ''}</li>`).join('')}</ul></div>`;
}
/* Draws into element `id` for q = {market, kw} or {market, asin}. With `auto` it reads the reviews from Amazon by itself
   when they are missing or two weeks old (once per listing and session, and only if you stay on the report). */
async function negReviews(id, q, {auto = true} = {}) {
  const box = $(id);
  if (!box) return;
  const key = q.market + '|' + (q.asin || q.kw);
  const mine = () => { const e = $(id); return e && e.dataset.rv === key ? e : null; };
  let last = null;
  const draw = (r, extra = '') => { const e = mine(); if (e) { last = r || last; e.innerHTML = rvHtml(last, q, extra); } };
  const read = async force => {
    let job = rvJobs.get(key);
    if (!job) {
      ((last && (force ? last.asins : last.todo)) || []).forEach(a => rvTried.add(q.market + a));
      try { job = (await api('/api/report/reviews', {...q, force})).job; } catch (err) { draw(null, failPill(err.message)); return; }
      rvJobs.set(key, job);
    }
    draw(null, busy('Opening Amazon…'));
    try {
      const r = await waitJob(job, line => draw(null, busy(line)));
      rvJobs.delete(key);
      draw(r);
    } catch (err) { rvJobs.delete(key); draw(null, failPill(err.message)); }
  };
  box.dataset.rv = key;
  box.onclick = e => {
    const b = e.target.closest('[data-rv-read]'), t = e.target.closest('.rvbody');
    if (e.target.closest('[data-rv-brief]') && last && last.brief) {
      navigator.clipboard.writeText(last.brief).then(() => toast('Supplier brief copied', 2000), () => toast('Could not copy the brief'));
      return;
    }
    if (b) { b.disabled = true; read(b.dataset.rvRead === 'force'); } else if (t) t.classList.toggle('open');
  };
  box.onkeydown = e => { if (e.key === 'Enter' && e.target.classList.contains('rvbody')) e.target.classList.toggle('open'); };
  try { draw(await api('/api/report/reviews?market=' + q.market + (q.asin ? '&asin=' + encodeURIComponent(q.asin) : '&kw=' + encodeURIComponent(q.kw)))); }
  catch (err) { draw(null, failPill(err.message)); return; }
  if (!last || PRINT) return;
  if (rvJobs.has(key)) read(false);
  else if (auto && last.todo.some(a => !rvTried.has(q.market + a))) setTimeout(() => { if (mine() && !rvJobs.has(key)) read(false); }, 1500);
}

/* ---------- opportunity map: buyers a month against local sellers, for every checked product ---------- */
async function drawOppMap(el, mk) {
  try {
    const d = await api('/api/opportunity_map?market=' + encodeURIComponent(mk));
    Charts.oppMap(el, {title: 'Opportunity map · Amazon ' + (MK[mk] || mk), height: 360,
      sub: 'Top-left = many buyers and few local sellers. Buyers are on a log scale.',
      points: d.points, xLine: d.local_line, yLine: d.demand_line, priceFmt: v => priceFx(v, mk),
      onOpen: p => { location.hash = '#/k/' + mk + '/' + encodeURIComponent(p.kw); },
      empty: 'No checked products with sales badges in ' + (MK[mk] || mk) + ' yet. Run Check sellers or “Check AU & UAE” on some ideas.',
      foot: ['Buyers: “bought in past month” badges (estimate)', 'Latest good check of each product, last ' + d.days + ' days']
        .concat(d.no_badge.length ? [d.no_badge.length + ' checked without sales badges, not shown'] : [])});
  } catch (e) { el.innerHTML = failPill(e.message); }
}

/* ---------- the opportunity matrix (Trend radar, Product ideas) ----------
   One row per product and one heat cell per discovery signal (engine/discovery.py). A signal without data reads
   "Unknown" on a hatched cell, never as a number. */
const SIG_KEYS = ['A', 'S', 'D', 'C', 'E'];
const SIG_HEAD = {A: 'Amazon lists', S: 'Search trend', D: 'Demand', C: 'Competition', E: 'Price & fees'};
const SIG_TIP = {A: 'How steadily it climbs Movers & Shakers and New Releases over two weeks of list scans',
  S: 'Google Trends for its search words in this market', D: 'Units a month from its sales badge, Helium 10 or its sales rank',
  C: 'Its reviews against what a newcomer can compete with',
  E: 'What is left of the price after Amazon fees, ads and an assumed 25% for product and freight'};
const OPP_TONE = {Strong: 'good', 'Worth a look': 'accent', Weak: 'warn', Skip: 'bad', Unknown: 'weak'};
const DEMAND_SRC = {badge: 'sales badge', h10: 'Helium 10', rank: 'sales rank', list: 'list sales rank'};
const MARK = {0: 'read, not on its lists', 1: 'on the lists', 2: 'on the lists with a deal badge'};
const sigOf = (it, k) => ((it.opportunity || {}).parts || []).find(p => p.key === k);
const sigTone = p => !p || !p.known ? 'unk' : p.v >= 0.67 ? 'good' : p.v >= 0.34 ? 'warn' : 'bad';
const sigVal = (it, k) => { const p = sigOf(it, k); return p && p.known ? p.v : -1; };
const oppScore = it => (it.opportunity || {}).score ?? -1;
const trendRead = it => it.trend && ['ok', 'low_volume'].includes(it.trend.status);
const checkSettled = L => L === 'busy' || !!(L && L.total != null);          // a market check running or answered
const moreBtn = (key, isOpen) => `<button type="button" class="btn slim ghost" data-more="${esc(key)}" aria-expanded="${isOpen}">${isOpen ? 'Less' : 'More'}</button>`;
const QUAL_TIP = 'Read what the score is missing: the phrase shoppers type, its Google trend, the product page, and whether Amazon in the other markets suggests it';
/* Something qualifying would still read: Amazon's suggestions, the search trend or a recent product page. */
const needsQualify = it => !it.phrase_at || !trendRead(it) || !it.page_at;
const gapScore = (it, mk) => ((it.gap || {})[mk] || {}).score ?? -1;
/* The sort key of a matrix sort option, or null when the view sorts it itself. 'gap:AU' = the best gap there first. */
const mtxSort = v => v === 'opp' ? (i => -oppScore(i)) : v === 'search' ? (i => -sigVal(i, 'S')) :
  v === 'me' ? (i => -(((i.pref || {}).liked ? 1000 : 0) + Math.max(0, oppScore(i)) * (1 + 0.15 * ((i.pref || {}).fit || 0)))) :
  v.startsWith('gap:') ? (i => -gapScore(i, v.slice(4))) : null;
/* Your star and Not for me on a row, the 'like your star' hint, and the Hidden list with Show again. */
const prefActs = it => {
  const on = !!(it.pref && it.pref.liked);
  return `<button type="button" class="btn slim ghost star" data-dlike="${esc(it.asin)}" aria-pressed="${on}" title="${on ? 'Starred: click to unstar' : 'Interested: star it (Best for me puts starred products first)'}">${on ? '★' : '☆'}</button>` +
    `<button type="button" class="btn slim ghost" data-dhide="${esc(it.asin)}" title="Hide this product. Hidden lists it, with Show again.">Not for me</button>`;
};
const fitPill = it => it.pref && !it.pref.liked && it.pref.like_of ?
  `<span class="pill line" title="${esc('Its title looks like “' + it.pref.like_of + '”, which you starred')}">like your ★</span>` : '';
const hidOpen = new Set();
function hiddenHtml(list, view) {
  if (!(list || []).length) return '';
  return `<details class="hidlist" data-hidview="${view}"${hidOpen.has(view) ? ' open' : ''}><summary>Hidden (${list.length})</summary><div class="row tight">` +
    list.map(h => `<span class="pill line">${esc((h.title || h.asin).slice(0, 60))}<button type="button" class="btn slim ghost" data-dunhide="${esc(h.asin)}">Show again</button></span>`).join('') + '</div></details>';
}
document.addEventListener('toggle', e => {
  const v = e.target.dataset && e.target.dataset.hidview;
  if (v) { if (e.target.open) hidOpen.add(v); else hidOpen.delete(v); }
}, true);
async function setPref(market, asin, set) {
  try { await api('/api/pref', {market, asin, set}); return true; } catch (e) { toast(e.message); return false; }
}
/* The star / Not for me / Show again / Use this phrase clicks of a matrix; reload() redraws the view. */
async function prefClick(e, market, reload) {
  const l = e.target.closest('[data-dlike]'), h = e.target.closest('[data-dhide]'), u = e.target.closest('[data-dunhide]');
  const v = e.target.closest('[data-usevar]'), b = l || h || u || v;
  if (!b) return false;
  b.disabled = true;
  const set = l ? {liked: l.getAttribute('aria-pressed') !== 'true'} : v ? {phrase: v.dataset.usevar} : {hidden: !!h};
  if (await setPref(market, b.dataset.dlike || b.dataset.dhide || b.dataset.dunhide || b.dataset.asin, set)) {
    if (h) toast('Hidden. It is listed under Hidden, with Show again.', 3000);
    if (v) toast('This product is now searched as “' + v.dataset.usevar + '”', 3000);
    await reload();
  } else b.disabled = false;
  return true;
}
/* Only the markets this one's products could sell in next can be sorted by their gap. */
function syncGapSorts(sel, targets) {
  [...sel.options].forEach(o => { if (o.value.startsWith('gap:')) o.hidden = o.disabled = !(targets || []).includes(o.value.slice(4)); });
  if (sel.selectedOptions[0] && sel.selectedOptions[0].disabled) sel.value = 'opp';
}

/* Two weeks on the lists, one square per market day: filled on a list, amber only with a deal badge, grey when its
   lists were read without it, outlined when they were not read in full that day. */
function presenceStrip(marks, days) {
  if (!marks || !marks.some(m => m != null)) return '';
  const on = marks.filter(m => m >= 1).length, read = marks.filter(m => m != null).length;
  return `<span class="strip" role="img" aria-label="On the lists ${on} of the ${read} days its lists were read">` +
    marks.map((m, i) => `<i class="m${m == null ? 'x' : m}" title="${esc((days && days[i]) || '')}: ${m == null ? 'no full list scan' : MARK[m]}"></i>`).join('') + '</span>';
}
function sigCell(it, k, mk, days) {
  const p = sigOf(it, k), r = (p && p.raw) || {};
  let main = 'Unknown', sub = '', act = '';
  if (p && p.known) {
    if (k === 'A') { main = r.surge != null ? 'surge ' + Math.round(r.surge) : r.pct ? '+' + num(r.pct) + '% today' : '#' + r.new_rank + ' new'; sub = r.scans ? `${r.hits}/${r.scans} scans` : 'one reading'; }
    if (k === 'S') { main = String(r.label || '').split(',')[0] + ' · ' + Math.round(r.score); sub = '“' + r.term + '”'; }
    if (k === 'D') { main = '≈ ' + compact(r.units) + '/mo'; sub = DEMAND_SRC[r.src] || 'estimate'; }
    if (k === 'C') { main = num(r.reviews) + (r.reviews === 1 ? ' review' : ' reviews'); sub = 'aim ≤ ' + num(r.target); }
    if (k === 'E' && r.cost_usd != null) { main = Math.round(r.margin * 100) + '% margin'; sub = 'cost ' + costMoney(r.cost_usd); }
    else if (k === 'E') { main = Math.round(r.left_pct * 100) + '% left'; sub = curMoney(r.price, MK_CUR[mk]); }
  } else if (k === 'S' && r.status === 'low_volume') { main = 'Too few searches'; sub = '“' + r.term + '”'; }
  if ((k === 'S' && !trendRead(it)) || (k === 'D' && !(p && p.known) && !it.page_at && trendRead(it))) {
    act = `<button type="button" class="btn slim" data-dqual="${esc(it.asin)}" title="${esc(QUAL_TIP)}">Qualify</button>`;
  }
  const bar = p && p.known ? `<span class="lvl"><i style="width:${Math.max(4, Math.round(p.v * 100))}%"></i></span>` : '';
  return `<td class="sig"><div class="tile ${sigTone(p)}" title="${esc(p ? p.why : '')}">${k === 'A' ? presenceStrip(it.presence, days) : ''}` +
    `<b>${esc(main)}</b>${sub ? `<span class="tsub">${esc(sub)}</span>` : ''}${k === 'E' && p && p.known ? buyLine(it) : ''}${bar}${act}</div></td>`;
}
/* The highest supplier price per piece that leaves your margin, in your supplier's currency. */
function buyLine(it) {
  const b = it.buy;
  if (!b) return '';
  const tip = `The most you can pay your supplier per piece and still keep ${b.margin_pct}% of the price (Settings → Currency): ` +
    `Amazon fees for ${b.sized ? 'this size' : 'a small parcel (size unknown)'}, sea freight, duty, ads 12% and returns 2.5%.`;
  return `<span class="tsub buy" title="${esc(tip)}">${b.max_usd != null ? 'pay ≤ ' + esc(costMoney(b.max_usd)) : 'no ' + b.margin_pct + '% at this price'}</span>`;
}
function oppCell(it) {
  const o = it.opportunity || {}, tone = OPP_TONE[o.label] || 'weak', risk = o.risk || {};
  const tip = `${o.known || 0} of ${o.of || 5} signals measured` + (risk.n ? ` · risks: ${risk.flags.join(', ')}` : '');
  return `<td class="opp"><div class="oppv ${tone}" title="${esc(tip)}"><b>${o.score != null ? Math.round(o.score) : '–'}</b>` +
    `<span class="pill ${tone}">${esc(o.label || 'Unknown')}</span></div><span class="tsub">${o.known || 0} of ${o.of || 5} measured</span>` +
    `<span class="tsub">${esc(o.conf || 'low')} confidence</span></td>`;
}
/* The sticky first cell: picture, title (opens Amazon), the view's own pills, category, title flags and row actions. */
function prodCell(it, pills, acts) {
  return `<td class="mtx-prod"><div class="mtx-pc"><div class="mtx-pic">${it.image ? `<img src="${esc(it.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : ''}</div>
    <div class="mtx-name"><a class="clamp2" data-open="${esc(it.url)}" title="${esc(it.title)}">${esc(it.title)}</a>
      <div class="mtx-meta">${pills}<span class="small muted">${esc(it.category || '')}</span></div>
      ${(it.flags || []).length ? '<div class="flags">' + it.flags.map(f => '<span class="flag">' + esc(f) + '</span>').join('') + '</div>' : ''}
      <div class="mtx-acts">${acts}</div></div></div></td>`;
}
function mtxHead(marketsLabel) {
  return `<thead><tr><th class="mtx-prod" scope="col">Product</th><th scope="col" title="One score from the five signals: an unknown signal counts as neutral and lowers the confidence">Opportunity</th>` +
    SIG_KEYS.map(k => `<th scope="col" title="${esc(SIG_TIP[k])}">${esc(SIG_HEAD[k])}</th>`).join('') + `<th scope="col">${esc(marketsLabel)}</th></tr></thead>`;
}
const MTX_COLS = SIG_KEYS.length + 3;
/* Why the score is what it is: every signal with its reason, then the risks and notes. */
function oppReasons(it) {
  const o = it.opportunity || {}, risk = o.risk || {};
  return `<ul class="reasons">${(o.parts || []).map(p => `<li class="${sigTone(p)}"><span><b>${esc(SIG_HEAD[p.key])}</b> · ${esc(p.why)}${p.known ? ` <span class="muted">(${esc(p.conf)} confidence)</span>` : ''}</span></li>`).join('')}` +
    (risk.n ? `<li class="bad"><span><b>Risks</b> · ${esc(risk.flags.join(', '))}: −${Math.round((1 - risk.factor) * 100)}% on the score</span></li>` : '') +
    ((risk.notes || []).length ? `<li class="warn"><span><b>Note</b> · ${esc(risk.notes.join(' · '))}</span></li>` : '') + '</ul>';
}
function coverageHtml(cov) {
  if (!cov || !cov.n) return '';
  return `<span class="small muted">Measured for the ${num(cov.n)} shown:</span>` + SIG_KEYS.map(k => {
    const n = (cov.known || {})[k] || 0;
    return `<span class="pill ${n === cov.n ? 'good' : n ? 'line' : 'weak'}" title="${esc(SIG_TIP[k])}">${esc(SIG_HEAD[k])} ${num(n)}/${num(cov.n)}</span>`;
  }).join('');
}
/* Opens or closes a row's details without redrawing the matrix; `open` keeps the state for the next redraw. */
function toggleMore(btn, open) {
  const key = btn.dataset.more, tr = btn.closest('table').querySelector(`tr[data-detail="${CSS.escape(key)}"]`);
  if (!tr) return;
  tr.hidden = !tr.hidden;
  btn.setAttribute('aria-expanded', String(!tr.hidden));
  btn.textContent = tr.hidden ? 'More' : 'Less';
  if (tr.hidden) open.delete(key); else open.add(key);
}
/* A market it could sell in next, scored from a search of its phrase there (engine/scores.py arbitrage). */
function gapTile(g, mk) {
  const tone = OPP_TONE[g.label] || 'weak';
  const facts = [g.fast_local != null ? `${num(g.fast_local)} fast seller${g.fast_local === 1 ? '' : 's'}` : '',
    g.osd_share != null ? `${Math.round(g.osd_share * 100)}% of sales overseas` : ''].filter(Boolean).join(' · ');
  const tip = `${MK[mk] || mk}: searched “${g.phrase}”${g.searched_at ? ' ' + ago(g.searched_at) : ''}\n` + (g.why || []).join('\n');
  return `<div class="gap ${tone}" title="${esc(tip)}"><span class="gk">${esc(MK_SHORT[mk] || mk)}</span>` +
    `<b>${g.score != null ? Math.round(g.score) : '–'}</b><span class="pill ${tone}">${esc(g.label || 'Unknown')}</span>` +
    (facts ? `<span class="tsub">${esc(facts)}</span>` : '') + '</div>';
}
/* The markets column: a gap tile per market searched, else the quick check's pill, and a Check button while any is
   missing. L = {asin:market: check result}; attr = the view's check button attribute. */
function marketsCell(it, checks, L, attr) {
  const gap = it.gap || {}, wave = it.wave || {}, todo = checks.filter(m => !gap[m] && !checkSettled(L[it.asin + ':' + m]));
  return `<td class="mk">${checks.map(m => (gap[m] ? gapTile(gap[m], m) : localPill(L[it.asin + ':' + m], m)) + waveLine(wave[m], m)).join('')}` +
    (todo.length ? `<button class="btn slim" ${attr}="${esc(it.asin)}" title="Search “${esc(it.search)}” there">Check ${todo.map(m => MK_SHORT[m]).join(' & ')}</button>` : '') + '</td>';
}
/* The search phrase in use and where it came from, then the other listings grouped into this row. */
function phraseHtml(it) {
  const src = {amazon: 'Amazon suggests it', you: 'your words', title: 'words from the title: no Amazon suggestion fits yet'}[it.phrase_src] || '';
  return `<div class="small"><b>Search phrase</b> “${esc(it.search)}” · ${it.phrase_src === 'amazon' ? icon('ok') : ''}${esc(src)}` +
    (it.phrase_src !== 'title' && it.title_search ? ` <span class="muted">(title: ${esc(it.title_search)})</span>` : '') +
    (it.phrase_at ? ` <span class="muted">· suggestions read ${esc(ago(it.phrase_at))}</span>` : '') + '</div>';
}
function groupHtml(it, mk) {
  const ms = it.members || [];
  if (!ms.length) return '';
  const line = m => `<li><a data-open="${esc(m.url || '')}" title="${esc(m.title || '')}">${esc(m.title || m.asin)}</a> <span class="muted">` +
    [m.price != null ? curMoney(m.price, MK_CUR[mk]) : '', m.reviews != null ? num(m.reviews) + ' reviews' : '',
      m.score != null ? 'score ' + Math.round(m.score) : ''].filter(Boolean).join(' · ') + '</span></li>';
  return `<div class="small members"><b>${ms.length} similar listing${ms.length === 1 ? '' : 's'} in this group</b>` +
    `<ul>${ms.slice(0, 6).map(line).join('')}</ul>${ms.length > 6 ? `<span class="muted">and ${ms.length - 6} more</span>` : ''}</div>`;
}
/* When its rise in searches reaches a market it could sell in next (engine/wave.py). */
const WAVE = {here: ['good', 'here'], coming: ['accent', 'expected'], due: ['warn', 'due now'], early: ['line', 'not there yet']};
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const monthName = k => MON[+k.slice(5, 7) - 1] + ' ' + k.slice(0, 4);
const monthSpan = eta => !eta ? '' : eta[0] === eta[1] ? monthName(eta[0]) : eta[0].slice(0, 4) === eta[1].slice(0, 4)
  ? MON[+eta[0].slice(5, 7) - 1] + '–' + monthName(eta[1]) : monthName(eta[0]) + '–' + monthName(eta[1]);
function waveLine(w, mk) {
  if (!w || !WAVE[w.status]) return '';
  const [tone, word] = WAVE[w.status];
  return `<span class="wave ${tone}" title="${esc(w.why)}">${icon('pulse')}${esc(MK_SHORT[mk] || mk)} wave: ${esc(word + (w.status === 'coming' ? ' ' + monthSpan(w.eta) : ''))}</span>`;
}
/* Why each market gap scores what it does, weakest reason first, and when the wave gets there. */
function gapReasons(it) {
  const waves = Object.entries(it.wave || {}).filter(([, w]) => w).map(([mk, w]) => `<div class="small">${waveLine(w, mk)} ${esc(w.why)}</div>`).join('');
  return waves + Object.entries(it.gap || {}).filter(([, g]) => g).map(([mk, g]) =>
    `<div class="small"><b>${esc(MK[mk] || mk)} gap ${g.score != null ? Math.round(g.score) : '–'} · ${esc(g.label || 'Unknown')}</b>` +
    ` <span class="muted">(${esc(g.conf || 'low')} confidence, searched ${esc(g.searched_at ? ago(g.searched_at) : '')})</span>` +
    `<ul class="gapwhy">${(g.why || []).map(w => `<li>${esc(w)}</li>`).join('')}</ul></div>`).join('');
}
/* Where to launch (reports.launch): the same product, your supplier price and a first order in each market searched.
   compact leaves out the money needed. */
const launchOut = new Map();
function launchHtml(r, compact) {
  const mks = Object.keys(r.markets || {}), pick = r.pick && r.pick.market;
  const rows = [
    ['Typical price', (m, c) => esc(curMoney(m.price, c))],
    ['Profit a sale', (m, c) => m.sim ? `<b class="${m.sim.profit_unit >= 0 ? 'up' : 'down'}">${esc(curMoney(m.sim.profit_unit, c))}</b>` +
      (m.sim.margin != null ? ` <span class="muted">${Math.round(m.sim.margin * 100)}%</span>` : '') : '–'],
    ['Most you can pay', m => m.max_usd != null ? esc(costMoney(m.max_usd)) : 'nothing leaves ' + r.margin_pct + '%'],
    ['A new seller might sell', m => m.newcomer ? `≈ ${num(m.newcomer.v)} a month <span class="muted">(${num(m.newcomer.lo)}–${num(m.newcomer.hi)})</span>`
      : '<span class="muted">no sales badges</span>'],
    ['First order sells out in', m => m.sim && m.sim.months != null ? (m.sim.months < 1 ? 'under a month' : num(m.sim.months, 1) + ' months') : '–'],
    ['Money needed', (m, c) => m.sim ? esc(curMoney(m.sim.cash, c, 0)) : '–'],
    ['Profit a month', (m, c) => m.sim && m.sim.profit_month != null ? esc(curMoney(m.sim.profit_month, c, 0)) : '–'],
    ['Market gap', (m, c, mk) => m.gap ? `<span class="pill ${OPP_TONE[m.gap.label] || 'weak'}">${m.gap.score != null ? Math.round(m.gap.score) : '–'} ${esc(m.gap.label || '')}</span>`
      : mk === r.market ? '<span class="muted">home market</span>' : '–'],
  ].filter(([k]) => !compact || k !== 'Money needed');
  const cell = (mk, f, first) => {
    const m = r.markets[mk];
    if (!m.searched) return first ? `<a href="#/k/${esc(mk)}/${encodeURIComponent(r.kw)}" title="Open the keyword report there and press Refresh now">not searched yet</a>` : '';
    if (m.price == null) return first ? '<span class="muted">no prices on page 1</span>' : '';
    return f(m, MK_CUR[mk], mk);
  };
  const sims = mks.filter(mk => r.markets[mk].sim);
  const foot = r.cost_usd == null ? 'Type your supplier price to compare the profit in each market.'
    : r.pick ? `Start in ${MK[r.pick.market]}: ${r.pick.why}.`
    : sims.length ? 'No market makes money and sells a first order out within six months at this supplier price.' : '';
  return `<div class="tablewrap launch" style="box-shadow:none"><table><thead><tr><th></th>${mks.map(mk => `<th class="r">${esc(MK[mk])}${mk === pick ? ' <span class="pill good">start here</span>' : ''}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(([k, f], i) => `<tr><td>${esc(k)}</td>${mks.map(mk => `<td class="r">${cell(mk, f, i === 0)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
    <div class="small">${esc(foot)} <span class="muted">A new seller is assumed to sell like the page-1 average per seller, less when the top listings have many reviews. Rough estimates.</span></div>`;
}
/* Where to launch for one radar or idea row, into box; uses your saved supplier price when there is one. */
async function launchRow(market, it, kw, box) {
  box.innerHTML = busy('Comparing markets…');
  try {
    const r = await api('/api/launch', {market, kw, cogs: (it.buy || {}).cost_usd, cogs_currency: 'USD', units: 300, boom: it.boom || {}});
    launchOut.set(market + '|' + it.asin, launchHtml(r, true));
    box.innerHTML = launchOut.get(market + '|' + it.asin);
  } catch (e) { box.innerHTML = failPill(e.message); }
}
/* Find the underserved version: Amazon's longer phrases for a row's phrase, each with its gap score in the markets
   it could sell in next, next to the phrase in use now. */
const varOut = new Map();
function variantsHtml(r, asin) {
  const ts = Object.keys(r.parent || {});
  if (!r.variants.length) return `<div class="small">Amazon suggests no longer phrases for “${esc(r.phrase)}”.</div>`;
  const line = (kw, g, act) => `<tr><td><b>${esc(kw)}</b>${act}</td>${ts.map(t => `<td class="mk">${g[t] ? gapTile(g[t], t) : '<span class="small muted">no result</span>'}</td>`).join('')}</tr>`;
  return `<div class="tablewrap launch" style="box-shadow:none"><table><thead><tr><th>Search phrase</th>${ts.map(t => `<th>${esc(MK[t])}</th>`).join('')}</tr></thead><tbody>` +
    line(r.phrase, r.parent, ' <span class="small muted">(now)</span>') +
    r.variants.map(v => line(v.kw, v.gap, `<div class="row tight"><button type="button" class="btn slim" data-usevar="${esc(v.kw)}" data-asin="${esc(asin)}" title="Search, score and check this product by this phrase from now on">Use this phrase</button>` +
      `<a class="btn slim ghost" href="#/k/${esc(ts[0])}/${encodeURIComponent(v.kw)}">Report</a></div>`)).join('') +
    '</tbody></table></div><div class="small muted">Best gap first. Searches from the last few hours are reused.</div>';
}
async function variantsRow(market, it, kw, box) {
  box.innerHTML = busy('Asking Amazon for longer phrases…');
  try {
    const {job} = await api('/api/discovery/variants', {market, phrase: kw, boom: it.boom || {}});
    const r = await waitJob(job, l => { box.innerHTML = busy(l); });
    varOut.set(market + '|' + it.asin, variantsHtml(r, it.asin));
    box.innerHTML = varOut.get(market + '|' + it.asin);
  } catch (e) { box.innerHTML = failPill(e.message); }
}
/* Reads what the score misses for up to 12 products (Amazon's suggestions, Google Trends, the product page, the
   other markets' suggestions); anything read recently is skipped. phrases = {asin: your search words}. Shows the
   progress in progEl; returns the job result, or null when nothing ran. */
async function qualifyRows(market, asins, progEl, phrases) {
  const list = [...new Set(asins.filter(Boolean))].slice(0, 12);
  if (!list.length) { toast('The products shown are already qualified'); return null; }
  progEl.innerHTML = busy('Qualifying…');
  try {
    const {job} = await api('/api/discovery/qualify', {market, asins: list, phrases: phrases || {}});
    const r = await waitJob(job, l => { progEl.innerHTML = busy(l); });
    progEl.innerHTML = `<span class="small muted">${esc('Qualified ' + (r.note || ''))}</span>`;
    return r;
  } catch (e) { progEl.innerHTML = failPill(e.message); return null; }
}
