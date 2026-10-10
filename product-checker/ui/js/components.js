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
