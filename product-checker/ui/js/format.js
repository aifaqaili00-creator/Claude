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
