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

/* Supplier prices typed on idea and radar cards, kept across re-renders (key: ASIN). */
const typedCost = new Map();
document.addEventListener('input', e => { const k = e.target.dataset && e.target.dataset.cogs; if (k) typedCost.set(k, e.target.value); });

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
function sourcingHtml(key) {
  const c = CUR_SYM[costCur()];
  return `<button class="btn slim" data-alibaba="${esc(key)}" title="Search suppliers for this product on Alibaba">Find on Alibaba${icon('external')}</button>
    <span class="profit" data-pkey="${esc(key)}"><input type="number" min="0" step="0.01" class="pbnum" data-cogs="${esc(key)}" value="${esc(typedCost.get(key) || '')}" placeholder="Cost" aria-label="Supplier price per piece in ${c}" title="Supplier price per piece in ${c} (change the currency in Settings → Currency)"><span class="small muted">${c}</span>
    <button class="btn slim" data-profit="${esc(key)}">Profit?</button><span class="small" data-pout="${esc(key)}"></span></span>`;
}
/* One shared click handler. find(key) -> {market, price, category, search}. */
function wireSourcing(root, find) {
  root.addEventListener('click', async e => {
    const a = e.target.closest('[data-alibaba]'), p = e.target.closest('[data-profit]');
    if (a) { const it = find(a.dataset.alibaba); if (it) openUrl(alibabaUrl(it.search)); }
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
      <div class="sub">${[i.country ? 'Reviewed in ' + esc(i.country) : '', esc(when(i.day)), i.helpful ? num(i.helpful) + ' found it helpful' : '',
        i.verified ? 'verified purchase' : '', q.asin ? '' : `on <a data-open="${esc(i.url)}" title="${esc(i.listing)}">${esc(i.listing.length > 60 ? i.listing.slice(0, 60) + '…' : i.listing)}</a>`,
        `<a data-open="${esc(i.all_url)}" title="Opens in your Chrome">all its critical reviews</a>`].filter(Boolean).join(' · ')}</div>
    </div>`).join('');
  const empty = !r ? '' : !r.searched ? 'Search this keyword first (Refresh now). Then the negative reviews of its top listings are read from Amazon.'
    : r.read_at && !items.length ? 'No 1–3★ reviews among the reviews Amazon showed.' : '';
  const src = r && r.read_at ? `Read ${ago(r.read_at)} from ${!r.critical ? 'the reviews on each product page (Amazon shows only a few there unless you are signed in to Amazon in the checker’s browser)'
    : r.critical < r.read ? 'Amazon’s critical reviews pages and product pages' : 'Amazon’s critical reviews pages'}.` : '';
  const again = r && r.searched && !rvJobs.has(q.market + '|' + (q.asin || q.kw)) ? `<button type="button" class="btn slim" data-rv-read="${r.read_at ? 'force' : ''}">${icon('refresh')}${r.read_at ? 'Read again' : 'Read them now'}</button>` : '';
  return `<div class="head"><h3>Top negative reviews</h3><div class="row">${extra ? `<span class="small">${extra}</span>` : ''}${again}</div></div>
    <div class="small muted">1–3★ reviews ${q.asin ? 'of this listing' : 'of the top ' + ((r && r.asins.length) || 5) + ' listings on page 1'} in ${esc(MK[q.market])}, most helpful first${q.asin ? '' : ', at most 2 from one listing'}.</div>
    ${list ? `<div class="rvlist">${list}</div>` : empty ? `<div class="small">${empty}</div>` : ''}
    ${src ? `<div class="small muted">${src}</div>` : ''}`;
}
/* Draws into element `id` for q = {market, kw} or {market, asin}, and reads the reviews from Amazon by itself
   when they are missing or two weeks old (once per listing and session, and only if you stay on the report). */
async function negReviews(id, q) {
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
    if (b) { b.disabled = true; read(b.dataset.rvRead === 'force'); } else if (t) t.classList.toggle('open');
  };
  box.onkeydown = e => { if (e.key === 'Enter' && e.target.classList.contains('rvbody')) e.target.classList.toggle('open'); };
  try { draw(await api('/api/report/reviews?market=' + q.market + (q.asin ? '&asin=' + encodeURIComponent(q.asin) : '&kw=' + encodeURIComponent(q.kw)))); }
  catch (err) { draw(null, failPill(err.message)); return; }
  if (!last) return;
  if (rvJobs.has(key)) read(false);
  else if (last.todo.some(a => !rvTried.has(q.market + a))) setTimeout(() => { if (mine() && !rvJobs.has(key)) read(false); }, 1500);
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
