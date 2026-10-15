'use strict';
/* Formatting helpers shared by every view (classic script: these names are global). */
const $ = id => document.getElementById(id);
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const num = (v, d = 0) => v == null || v !== v ? '' : Number(v).toLocaleString('en-US', {maximumFractionDigits: d, minimumFractionDigits: d});
const money = (v, cur, d = 2) => v == null ? '' : (cur ? cur + ' ' : '') + num(v, d);
const pct = (v, d = 0) => v == null || v !== v ? '–' : num(v * 100, d) + '%';
const ago = t => {
  const s = Date.now() / 1000 - t;
  return s < 90 ? 'just now' : s < 3600 ? Math.round(s / 60) + ' min ago' : s < 86400 ? Math.round(s / 3600) + ' h ago' : Math.round(s / 86400) + ' d ago';
};
const compact = v => v == null ? '' : Math.abs(v) >= 1e6 ? num(v / 1e6, 1) + 'M' : Math.abs(v) >= 1e4 ? num(v / 1e3, 0) + 'K' : Math.abs(v) >= 1e3 ? num(v / 1e3, 1) + 'K' : num(v);
const MK = {AU: 'Australia', AE: 'UAE', US: 'USA'};
const MK_SHORT = {AU: 'AU', AE: 'UAE', US: 'US'};
const MK_PROOF = {GB: 'UK', DE: 'Germany'};             // proof markets of the Gap Finder: not in the market pickers
const COUNTRY = {AU: 'Australia', AE: 'United Arab Emirates', US: 'United States', CN: 'China', HK: 'Hong Kong', GB: 'United Kingdom',
  DE: 'Germany', IN: 'India', JP: 'Japan', KR: 'South Korea', TW: 'Taiwan', VN: 'Vietnam', CA: 'Canada', TR: 'Turkey', SG: 'Singapore',
  FR: 'France', IT: 'Italy', ES: 'Spain', NL: 'Netherlands', PL: 'Poland', MY: 'Malaysia', TH: 'Thailand', PK: 'Pakistan', ID: 'Indonesia'};
const DRAWN_FLAGS = new Set(['AU', 'AE', 'US', 'CN', 'HK', 'GB', 'DE', 'IN', 'JP', 'KR', 'TW', 'VN', 'CA', 'TR', 'SG']);
/* A country's flag as HTML (code: 'AU', 'CN'...). label '' = decorative (the name is written next to it). Countries
   without a drawn flag get their two letters. */
function cflag(code, label) {
  const c = String(code || '').toUpperCase();
  if (!/^[A-Z]{2}$/.test(c)) return '';
  const name = label ?? (COUNTRY[c] || c), a11y = name ? `role="img" aria-label="${esc(name)}"` : 'aria-hidden="true"';
  return DRAWN_FLAGS.has(c) ? `<svg class="cflag" ${a11y}>${name ? '<title>' + esc(name) + '</title>' : ''}<use href="#f-${c}"/></svg>`
    : `<span class="cflag code" ${a11y}${name ? ` title="${esc(name)}"` : ''}>${c}</span>`;
}
const mkFlag = (m, short) => cflag(m, '') + esc((short ? MK_SHORT : MK)[m] || MK_PROOF[m] || m);   // flag + market name
/* PDF mode: the hidden browser that makes a PDF opens a report as /?print=summary,sellers,... (the parts to keep). */
const PRINT = (() => { const p = new URLSearchParams(location.search).get('print'); return p == null ? null : p.split(',').filter(Boolean); })();
if (PRINT) document.documentElement.classList.add('print');
