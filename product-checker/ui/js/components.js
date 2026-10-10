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

/* ---------- your price range (Product ideas, Trend radar, Settings) ---------- */
const CUR_SYM = {USD: 'US$', EUR: '€', AUD: 'A$', AED: 'AED'};
const MK_CUR = {US: 'USD', AU: 'AUD', AE: 'AED'};
function rangeText(mk) {
  const r = ((ST.fx || {}).ranges || {})[mk];
  return r ? `${CUR_SYM[MK_CUR[mk]]} ${num(r[0], r[0] < 10 ? 1 : 0)}–${num(r[1], r[1] < 10 ? 1 : 0)}` : '';
}
function fxNote() {
  const f = ST.fx || {};
  return f.source === 'ECB' ? `ECB rates of ${f.as_of}` : 'approximate rates (offline)';
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
      <div class="seg slim" data-pbcur><button type="button" data-v="USD" aria-pressed="${cur === 'USD'}">US$</button><button type="button" data-v="EUR" aria-pressed="${cur === 'EUR'}">€</button></div>
      <span class="small muted" data-pbconv title="${esc(fxNote())}">${mk !== 'US' || cur !== 'USD' ? '= ' + esc(rangeText(mk)) : ''}</span>
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
  el.addEventListener('click', e => {
    const b = e.target.closest('[data-pbcur] button'); if (b) save({price_currency: b.dataset.v});
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
  const cur = (ST.settings || {}).price_currency || 'USD';
  return `<button class="btn slim" data-alibaba="${esc(key)}" title="Search suppliers for this product on Alibaba">Find on Alibaba${icon('external')}</button>
    <span class="profit" data-pkey="${esc(key)}"><input type="number" min="0" step="0.01" class="pbnum" data-cogs="${esc(key)}" placeholder="Cost ${CUR_SYM[cur]}" aria-label="Supplier price per piece in ${CUR_SYM[cur]}" title="Supplier price per piece (${CUR_SYM[cur]})">
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
    const s = ST.settings || {}, per = ((ST.fx || {}).per_usd) || {USD: 1, EUR: 0.92};
    const cogsUsd = cost / (per[s.price_currency || 'USD'] || 1);
    out.innerHTML = busy('…');
    try {
      const r = await api('/api/profit', {market: it.market, price: it.price, category: it.category || '', cogs_usd: cogsUsd,
                                          registered: !!((s.registered || {})[it.market])});
      const e2 = r.economics, c = CUR_SYM[MK_CUR[it.market]], m = e2.Margin || 0;
      const fees = e2.Referral + e2.FBA + e2.Storage + e2.Placement + e2.FeeTax;
      const tip = `Rough estimate at ${c} ${num(it.price, 2)}: Amazon fees ${c} ${num(fees, 2)}, product + shipping + duty ${c} ${num(e2.Landed, 2)}, ` +
        `ads ${c} ${num(e2.Ads, 2)}, returns ${c} ${num(e2.Returns, 2)}. Assumes a small standard-size item and sea freight; ` +
        'check the real size and weight before ordering.';
      out.innerHTML = `<span class="pill ${e2.Profit > 0 && m >= 0.15 ? 'good' : e2.Profit > 0 ? 'warn' : 'bad'}" title="${esc(tip)}">≈ ${c} ${num(e2.Profit, 2)} profit / sale · ${Math.round(m * 100)}% margin</span>`;
    } catch (err) { out.innerHTML = failPill(err.message); }
  });
}
