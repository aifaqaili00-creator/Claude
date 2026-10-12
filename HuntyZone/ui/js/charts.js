'use strict';
/* In-house SVG charts, no library. Rules (see the dataviz guide):
   - one y-axis per chart, never two; demand and supply are separate panels on one shared timeline
   - one look per kind of number: solid = measured, dashed + band = estimate, dotted + band = forecast,
     hatched = backcast, diamond = Helium 10 (reported), a gap = no valid data (blocked, captcha...)
   - thin marks, hairline grid, legend for 2+ series, hover crosshair, and a Table view on every chart
   - colours come from CSS variables, so switching the theme needs no redraw
   - text is inserted with textContent (titles and labels can come from Amazon pages) */
const Charts = (() => {
  const NS = 'http://www.w3.org/2000/svg';
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const groups = new Map();
  let uid = 0;

  /* ---------- small helpers ---------- */
  function svgEl(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs || {}) if (attrs[k] != null) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function htmlEl(tag, cls, parent, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }
  const finite = v => typeof v === 'number' && isFinite(v);
  function niceStep(span, n) {
    const raw = span / Math.max(1, n), mag = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / mag;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * mag;
  }
  function ticks(lo, hi, n = 4) {
    if (!(hi > lo)) hi = lo + 1;
    const step = niceStep(hi - lo, n), out = [];
    const first = Math.floor(lo / step + 1e-9) * step, last = Math.ceil(hi / step - 1e-9) * step;   // always covers hi
    for (let v = first; v <= last + step * 1e-6; v += step) out.push(+v.toFixed(10));
    return out;
  }
  const monthKey = t => { const d = new Date(t); return d.getUTCFullYear() + '-' + String(d.getUTCMonth() + 1).padStart(2, '0'); };
  const monthT = key => { const [y, m] = String(key).split('-').map(Number); return Date.UTC(y, (m || 1) - 1, 1); };
  function monthLabel(t, withYear) {
    const d = new Date(t);
    return MONTHS[d.getUTCMonth()] + (withYear ? ' \u2019' + String(d.getUTCFullYear()).slice(2) : '');
  }
  const dayLabel = t => { const d = new Date(t); return d.getUTCDate() + ' ' + MONTHS[d.getUTCMonth()]; };
  const fmtDefault = v => v == null ? '–' : compact(v);
  const BASIS = {measured: 'measured', reported: 'Helium 10 (reported)', estimated: 'estimate', backcast: 'backcast (from search trend)',
                 forecast: 'forecast', index: 'index (0–100)', assumed: 'assumed'};

  /* ---------- the frame every chart shares ---------- */
  function frame(el, o) {
    el.innerHTML = '';
    el.classList.add('chart');
    const head = htmlEl('div', 'chart-head', el);
    const titles = htmlEl('div', '', head);
    if (o.title) htmlEl('h3', '', titles, o.title);
    if (o.sub) htmlEl('div', 'small', titles, o.sub);
    const tools = htmlEl('div', 'chart-tools', head);
    const legend = htmlEl('div', 'legend chart-legend', tools);
    if (o.more) {                                       // {on, toggle}: extra series for the curious
      const mb = htmlEl('button', 'btn slim ghost', tools, o.more.on ? 'Less detail' : 'More detail');
      mb.type = 'button';
      mb.setAttribute('aria-pressed', String(!!o.more.on));
      mb.addEventListener('click', o.more.toggle);
    }
    const tbtn = htmlEl('button', 'btn slim ghost', tools, 'Table');
    tbtn.type = 'button';
    tbtn.setAttribute('aria-pressed', 'false');
    if (o.takeaway) htmlEl('p', 'takeaway', el, o.takeaway);
    const body = htmlEl('div', 'chart-body', el);
    const table = htmlEl('div', 'chart-table', el);
    table.hidden = true;
    const foot = htmlEl('div', 'chart-foot', el);
    if (o.foot) for (const f of [].concat(o.foot)) htmlEl('span', 'pill line', foot, f);
    tbtn.addEventListener('click', () => {
      const on = table.hidden;
      table.hidden = !on; body.hidden = on;
      tbtn.setAttribute('aria-pressed', String(on));
      tbtn.textContent = on ? 'Chart' : 'Table';
    });
    return {head, legend, body, table, foot};
  }
  function legendItems(box, items) {
    box.innerHTML = '';
    if (items.length < 2) return;
    for (const it of items) {
      const s = htmlEl('span', '', box);
      const key = htmlEl('i', it.kind === 'line' ? 'key-line' : '', s);
      key.style.setProperty('--c', it.color);
      if (it.dash) key.classList.add('dash');
      if (it.kind === 'diamond') key.classList.add('key-diamond');
      s.appendChild(document.createTextNode(it.label));
    }
  }
  function fillTable(box, cols, rows) {
    box.innerHTML = '';
    const wrap = htmlEl('div', 'tablewrap', box), t = htmlEl('table', '', wrap);
    const tr = htmlEl('tr', '', htmlEl('thead', '', t));
    cols.forEach((c, i) => htmlEl('th', i ? 'r' : '', tr, c));
    const tb = htmlEl('tbody', '', t);
    for (const r of rows) {
      const row = htmlEl('tr', '', tb);
      r.forEach((v, i) => htmlEl('td', i ? 'r' : '', row, v == null ? '–' : String(v)));
    }
  }
  function tooltip(body) {
    let tip = body.querySelector('.chart-tip');
    if (!tip) { tip = htmlEl('div', 'chart-tip', body); tip.setAttribute('role', 'status'); }
    return tip;
  }
  function showTip(tip, body, x, title, rows) {
    tip.innerHTML = '';
    htmlEl('div', 'tip-title', tip, title);
    for (const r of rows) {
      const line = htmlEl('div', 'tip-row', tip);
      const k = htmlEl('i', 'key-line' + (r.dash ? ' dash' : ''), line);
      k.style.setProperty('--c', r.color);
      htmlEl('b', '', line, r.value);
      htmlEl('span', '', line, r.label);
    }
    tip.hidden = false;
    const w = tip.offsetWidth, bw = body.clientWidth;
    tip.style.left = Math.max(0, Math.min(bw - w, x + 14 > bw - w ? x - w - 14 : x + 14)) + 'px';
    tip.style.top = '6px';
  }
  /* 45-degree hatching in a series colour (a pattern cannot inherit the colour of what uses it) */
  function patterns(svg, id) {
    const defs = svgEl('defs', {}, svg), made = new Map();
    return color => {
      if (!made.has(color)) {
        const pid = 'hatch' + id + '-' + made.size;
        const p = svgEl('pattern', {id: pid, width: 6, height: 6, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)'}, defs);
        svgEl('line', {x1: 0, y1: 0, x2: 0, y2: 6, style: `stroke: ${color}; stroke-width: 2; opacity: .55`}, p);
        made.set(color, pid);
      }
      return 'url(#' + made.get(color) + ')';
    };
  }
  function observe(el, render) {
    let w = 0, pending = 0;
    const go = () => { pending = 0; const nw = Math.floor(el.clientWidth); if (nw && nw !== w) { w = nw; render(nw); } };
    if (window.ResizeObserver) new ResizeObserver(() => { if (!pending) pending = requestAnimationFrame(go); }).observe(el);
    go();
    return () => { w = 0; go(); };
  }

  /* ---------- time series: lines with bands, gaps and markers ---------- */
  function timeseries(el, o) {
    const id = ++uid, f = frame(el, o);
    const fmt = o.yFormat || fmtDefault, H = o.height || 220;
    const series = (o.series || []).filter(s => s.points && s.points.some(p => finite(p.y)));
    const gaps = o.gaps || [];
    legendItems(f.legend, series.map(s => ({label: s.label, color: s.color, kind: s.basis === 'reported' ? 'diamond' : 'line',
                                            dash: ['estimated', 'forecast', 'backcast'].includes(s.basis)})));
    if (!series.length) {
      htmlEl('div', 'chart-empty', f.body, o.empty || 'No data yet.');
      return {update() {}};
    }
    const allT = series.flatMap(s => s.points.map(p => p.t)).concat(gaps.map(g => g.t));
    let t0 = Math.min(...allT), t1 = Math.max(...allT);
    if (o.xDomain) { t0 = o.xDomain[0]; t1 = o.xDomain[1]; }
    if (t1 === t0) { t0 -= 15 * 864e5; t1 += 15 * 864e5; }
    const ys = series.flatMap(s => s.points.flatMap(p => [p.y, p.lo, p.hi])).filter(finite);
    let y0 = o.yMin != null ? o.yMin : Math.min(0, ...ys), y1 = Math.max(...ys, y0 + 1);
    if (o.yMax != null) y1 = o.yMax;
    const yt = ticks(y0, y1, 4);
    y1 = yt[yt.length - 1];
    y0 = yt[0];
    const monthly = o.x === 'month';
    const rows = new Map();                  // t -> per-series values, for the tooltip and table
    series.forEach((s, si) => s.points.forEach(p => {
      if (!rows.has(p.t)) rows.set(p.t, []);
      rows.get(p.t)[si] = p;
    }));
    const times = [...rows.keys()].sort((a, b) => a - b);
    fillTable(f.table, [monthly ? 'Month' : 'Date', ...series.map(s => s.label)],
      times.map(t => [monthly ? monthKey(t) : new Date(t).toISOString().slice(0, 10),
                      ...series.map((s, si) => { const p = rows.get(t)[si]; return p && finite(p.y) ? fmt(p.y) + (finite(p.lo) && finite(p.hi) ? ' (' + fmt(p.lo) + '–' + fmt(p.hi) + ')' : '') : null; })]));

    let state = null;
    function render(W) {
      f.body.querySelectorAll('svg').forEach(s => s.remove());
      const single = series.length === 1 && series[0].endLabel !== false;
      const m = {l: 44, r: single ? 50 : 14, t: 10, b: 26}, w = W - m.l - m.r, h = H - m.t - m.b;
      const X = t => m.l + (t - t0) / (t1 - t0) * w, Y = v => m.t + h - (v - y0) / (y1 - y0) * h;
      const svg = svgEl('svg', {width: W, height: H, class: 'chart-svg', role: 'img',
                                'aria-label': (o.title || 'Chart') + ': ' + series.map(s => s.label).join(', ')}, null);
      f.body.insertBefore(svg, f.body.firstChild);
      const hatch = patterns(svg, id);
      const grid = svgEl('g', {class: 'grid'}, svg);
      for (const v of yt) {
        svgEl('line', {x1: m.l, x2: m.l + w, y1: Y(v), y2: Y(v), class: v === 0 ? 'base' : ''}, grid);
        svgEl('text', {x: m.l - 8, y: Y(v) + 4, 'text-anchor': 'end', class: 'tick'}, grid).textContent = fmt(v);
      }
      // x ticks: months (with the year on January and the first tick) or days
      const xt = [], span = t1 - t0;
      if (monthly || span > 120 * 864e5) {
        const every = span > 4 * 365 * 864e5 ? 12 : span > 2 * 365 * 864e5 ? 6 : span > 365 * 864e5 ? 3 : span > 180 * 864e5 ? 2 : 1;
        const d = new Date(t0); let t = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + (d.getUTCDate() > 1 ? 1 : 0), 1);
        for (; t <= t1; t = Date.UTC(new Date(t).getUTCFullYear(), new Date(t).getUTCMonth() + 1, 1))
          if (new Date(t).getUTCMonth() % every === 0) xt.push([t, monthLabel(t, !xt.length || new Date(t).getUTCMonth() === 0)]);
      } else {
        const n = Math.max(2, Math.min(6, Math.floor(w / 90)));
        const short = span < 2 * 864e5;              // checks on the same day: show the time
        for (let i = 0; i <= n; i++) {
          const t = t0 + span * i / n;
          xt.push([t, short ? new Date(t).toLocaleTimeString(undefined, {hour: '2-digit', minute: '2-digit'}) : dayLabel(t)]);
        }
      }
      let lastX = -1e9, lastLab = null;
      for (const [t, lab] of xt) {
        const x = X(t);
        if (x - lastX < 34 || lab === lastLab) continue;
        lastX = x;
        lastLab = lab;
        svgEl('text', {x, y: H - 6, 'text-anchor': 'middle', class: 'tick'}, grid).textContent = lab;
      }
      for (const g of gaps) {            // a blocked fetch: a small grey mark on the baseline, never a zero
        const x = X(g.t);
        const mk = svgEl('g', {class: 'gapmark'}, svg);
        svgEl('line', {x1: x, x2: x, y1: Y(y0) - 6, y2: Y(y0), style: 'stroke: var(--s-other); stroke-width: 2'}, mk);
        svgEl('title', {}, mk).textContent = 'No data: ' + (g.status || 'blocked');
      }
      const layer = svgEl('g', {}, svg);
      series.forEach(s => {
        const pts = s.points.filter(p => finite(p.y)).sort((a, b) => a.t - b.t);
        const color = s.color || 'var(--s-demand)';
        // band (lo-hi)
        const banded = pts.filter(p => finite(p.lo) && finite(p.hi));
        if (banded.length > 1 && s.band !== false) {
          const top = banded.map(p => X(p.t) + ',' + Y(p.hi)).join(' '), bot = banded.slice().reverse().map(p => X(p.t) + ',' + Y(p.lo)).join(' ');
          if (s.basis === 'backcast') svgEl('polygon', {points: top + ' ' + bot, style: `fill: ${color}; opacity: .06`}, layer);
          svgEl('polygon', {points: top + ' ' + bot, style: s.basis === 'backcast' ? `fill: ${hatch(color)}; opacity: .5` : `fill: ${color}; opacity: .12`}, layer);
        }
        if (s.area) {
          const d = pts.map((p, i) => (i ? 'L' : 'M') + X(p.t) + ' ' + Y(p.y)).join(' ') + ` L${X(pts[pts.length - 1].t)} ${Y(Math.max(y0, 0))} L${X(pts[0].t)} ${Y(Math.max(y0, 0))} Z`;
          svgEl('path', {d, style: `fill: ${color}; opacity: .10`}, layer);
        }
        // the line, broken where points are missing or more than maxGap apart
        if (s.basis !== 'reported') {
          const maxGap = s.maxGap || (monthly ? 40 * 864e5 : Infinity);
          let d = '', prev = null;
          for (const p of pts) {
            d += (prev && p.t - prev.t <= maxGap && !p.breakBefore ? 'L' : 'M') + X(p.t).toFixed(1) + ' ' + Y(p.y).toFixed(1) + ' ';
            prev = p;
          }
          const dash = s.basis === 'estimated' ? '6 4' : s.basis === 'forecast' ? '1.5 4' : s.basis === 'backcast' ? '3 3' : null;
          svgEl('path', {d, class: 'line', 'stroke-dasharray': dash, style: `stroke: ${color}`}, layer);
        }
        // markers: Helium 10 diamonds; a lone point gets a dot so it is visible at all
        pts.forEach((p, i) => {
          const lone = (!pts[i - 1] || p.t - pts[i - 1].t > (s.maxGap || Infinity)) && (!pts[i + 1] || pts[i + 1].t - p.t > (s.maxGap || Infinity));
          if (s.basis === 'reported' || p.basis === 'reported') {
            const x = X(p.t), y = Y(p.y), r = 5;
            svgEl('path', {d: `M${x} ${y - r} L${x + r} ${y} L${x} ${y + r} L${x - r} ${y} Z`, class: 'dot', style: `fill: ${color}`}, layer);
          } else if (lone || pts.length === 1) {
            svgEl('circle', {cx: X(p.t), cy: Y(p.y), r: 4, class: 'dot', style: `fill: ${color}`}, layer);
          }
        });
        // the newest value: an end dot, and its number when this is the only series
        if (s.endLabel !== false && pts.length && s.basis !== 'reported') {
          const p = pts[pts.length - 1];
          svgEl('circle', {cx: X(p.t), cy: Y(p.y), r: 4, class: 'dot', style: `fill: ${color}`}, layer);
          if (single) svgEl('text', {x: X(p.t) + 8, y: Y(p.y) + 4, class: 'vlabel'}, layer).textContent = fmt(p.y);
        }
      });
      for (const mk of o.markers || []) {          // e.g. "order by" or "today"
        const x = X(mk.t);
        if (x < m.l || x > m.l + w) continue;
        svgEl('line', {x1: x, x2: x, y1: m.t, y2: m.t + h, class: 'marker'}, svg);
        svgEl('text', {x: x + 4, y: m.t + 10, class: 'tick'}, svg).textContent = mk.label;
      }
      const cross = svgEl('line', {y1: m.t, y2: m.t + h, class: 'cross', visibility: 'hidden'}, svg);
      const dots = series.map(s => svgEl('circle', {r: 4, class: 'dot hoverdot', visibility: 'hidden', style: `fill: ${s.color}`}, svg));
      const hit = svgEl('rect', {x: m.l, y: m.t, width: w, height: h, fill: 'transparent', tabindex: 0}, svg);
      state = {X, Y, m, w, h, cross, dots, svg};
      const near = px => {
        const t = t0 + (px - m.l) / w * (t1 - t0);
        let best = times[0];
        for (const tt of times) if (Math.abs(tt - t) < Math.abs(best - t)) best = tt;
        return best;
      };
      hit.addEventListener('pointermove', e => {
        const r = svg.getBoundingClientRect();
        hover(near(e.clientX - r.left), true);
      });
      hit.addEventListener('pointerleave', () => hover(null, true));
      let ki = times.length - 1;
      hit.addEventListener('focus', () => hover(times[ki], true));
      hit.addEventListener('blur', () => hover(null, true));
      hit.addEventListener('keydown', e => {
        if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
          ki = Math.max(0, Math.min(times.length - 1, ki + (e.key === 'ArrowLeft' ? -1 : 1)));
          hover(times[ki], true); e.preventDefault();
        }
      });
    }
    const tip = tooltip(f.body);
    tip.hidden = true;
    function draw(t, own) {
      if (!state) return;
      if (t == null || t < t0 || t > t1) {
        state.cross.setAttribute('visibility', 'hidden');
        state.dots.forEach(d => d.setAttribute('visibility', 'hidden'));
        tip.hidden = true;
        return;
      }
      const x = state.X(t);
      state.cross.setAttribute('x1', x); state.cross.setAttribute('x2', x);
      state.cross.setAttribute('visibility', 'visible');
      const tr = [];
      series.forEach((s, si) => {
        let p = (rows.get(t) || [])[si];
        if (!p && !monthly) {                    // panels on a shared timeline: nearest point of this series
          const pts = s.points.filter(q => finite(q.y));
          p = pts.reduce((b, q) => !b || Math.abs(q.t - t) < Math.abs(b.t - t) ? q : b, null);
          if (p && Math.abs(p.t - t) > 3 * 864e5) p = null;
        }
        const d = state.dots[si];
        if (p && finite(p.y)) {
          d.setAttribute('cx', state.X(p.t)); d.setAttribute('cy', state.Y(p.y)); d.setAttribute('visibility', 'visible');
          tr.push({color: s.color, dash: ['estimated', 'forecast', 'backcast'].includes(s.basis), label: s.label + (p.note ? ' · ' + p.note : ''),
                   value: fmt(p.y) + (finite(p.lo) && finite(p.hi) ? ' (' + fmt(p.lo) + '–' + fmt(p.hi) + ')' : '')});
        } else d.setAttribute('visibility', 'hidden');
      });
      const gap = gaps.find(g => Math.abs(g.t - t) < 864e5);
      if (gap) tr.push({color: 'var(--s-other)', label: 'no data: ' + (gap.status || 'blocked'), value: '–'});
      if (own && tr.length) showTip(tip, f.body, x, monthly ? monthLabel(t, true) : new Date(t).toLocaleDateString(), tr);
      else tip.hidden = true;
    }
    function hover(t, own) {
      draw(t, own);
      if (own && o.group) for (const c of groups.get(o.group) || []) if (c !== api) c.draw(t, false);
    }
    const api = {draw, el};
    if (o.group) {
      if (!groups.has(o.group)) groups.set(o.group, new Set());
      for (const c of [...groups.get(o.group)]) if (!c.el.isConnected) groups.get(o.group).delete(c);
      groups.get(o.group).add(api);
    }
    observe(f.body, render);
    return api;
  }

  /* ---------- columns: monthly units or revenue, one series, reported vs estimated ---------- */
  function columns(el, o) {
    const id = ++uid, f = frame(el, o), fmt = o.yFormat || fmtDefault, H = o.height || 200;
    const items = (o.items || []).filter(it => finite(it.y));
    const color = o.color || 'var(--s-demand)';
    const kinds = [...new Set(items.map(it => it.basis || 'measured'))];
    legendItems(f.legend, kinds.map(k => ({label: o.basisLabels && o.basisLabels[k] || BASIS[k] || k, color, kind: 'rect'})));
    if (kinds.length > 1) f.legend.querySelectorAll('i').forEach((i, n) => { if (kinds[n] !== 'reported' && kinds[n] !== 'measured') i.classList.add('key-hatch'); });
    fillTable(f.table, [o.xTitle || 'Month', o.yTitle || 'Value', 'Range', 'Basis'],
      items.map(it => [it.label, fmt(it.y), finite(it.lo) && finite(it.hi) ? fmt(it.lo) + '–' + fmt(it.hi) : null, BASIS[it.basis] || it.basis || '']));
    if (!items.length) { htmlEl('div', 'chart-empty', f.body, o.empty || 'No data yet.'); return; }
    const tip = tooltip(f.body);
    tip.hidden = true;
    observe(f.body, W => {
      f.body.querySelectorAll('svg').forEach(s => s.remove());
      const m = {l: 44, r: 10, t: 12, b: 26}, w = W - m.l - m.r, h = H - m.t - m.b;
      const hiV = Math.max(...items.map(it => finite(it.hi) ? it.hi : it.y), 1);
      const yt = ticks(0, hiV, 4), y1 = yt[yt.length - 1];
      const Y = v => m.t + h - v / y1 * h;
      const svg = svgEl('svg', {width: W, height: H, class: 'chart-svg', role: 'img', 'aria-label': o.title || 'Columns'});
      f.body.insertBefore(svg, f.body.firstChild);
      const hatch = patterns(svg, id);
      const grid = svgEl('g', {class: 'grid'}, svg);
      for (const v of yt) {
        svgEl('line', {x1: m.l, x2: m.l + w, y1: Y(v), y2: Y(v), class: v === 0 ? 'base' : ''}, grid);
        svgEl('text', {x: m.l - 8, y: Y(v) + 4, 'text-anchor': 'end', class: 'tick'}, grid).textContent = fmt(v);
      }
      const band = w / items.length, bw = Math.max(3, Math.min(24, band - 4));
      const every = Math.ceil(items.length / Math.max(1, Math.floor(w / 40)));
      items.forEach((it, i) => {
        const cx = m.l + band * i + band / 2, x = cx - bw / 2, y = Y(it.y), hh = Math.max(1, Y(0) - y);
        const r = Math.min(4, bw / 2, hh);
        const d = `M${x} ${Y(0)} V${y + r} Q${x} ${y} ${x + r} ${y} H${x + bw - r} Q${x + bw} ${y} ${x + bw} ${y + r} V${Y(0)} Z`;
        const reported = !it.basis || it.basis === 'reported' || it.basis === 'measured';
        const g = svgEl('g', {class: 'colgrp', tabindex: 0}, svg);
        svgEl('rect', {x: m.l + band * i, y: m.t, width: band, height: h, fill: 'transparent'}, g);
        if (reported) svgEl('path', {d, class: 'col', style: `fill: ${color}`}, g);
        else {                                   // estimates are hatched; a backcast is fainter still
          const back = it.basis === 'backcast';
          svgEl('path', {d, class: 'col', style: `fill: ${color}; opacity: ${back ? .08 : .25}`}, g);
          svgEl('path', {d, class: 'col', style: `fill: ${hatch(color)}; opacity: ${back ? .55 : 1}`}, g);
        }
        if (finite(it.lo) && finite(it.hi) && it.hi > it.lo) {
          svgEl('line', {x1: cx, x2: cx, y1: Y(it.hi), y2: Y(it.lo), class: 'whisker'}, g);
          svgEl('line', {x1: cx - 4, x2: cx + 4, y1: Y(it.hi), y2: Y(it.hi), class: 'whisker'}, g);
          svgEl('line', {x1: cx - 4, x2: cx + 4, y1: Y(it.lo), y2: Y(it.lo), class: 'whisker'}, g);
        }
        if (i % every === 0) svgEl('text', {x: cx, y: H - 6, 'text-anchor': 'middle', class: 'tick'}, svg).textContent = it.short || it.label;
        const show = () => showTip(tip, f.body, cx, it.label, [{color, label: (BASIS[it.basis] || '') + (it.note ? ' · ' + it.note : ''),
          value: fmt(it.y) + (finite(it.lo) && finite(it.hi) ? ' (' + fmt(it.lo) + '–' + fmt(it.hi) + ')' : '')}]);
        g.addEventListener('pointerenter', show); g.addEventListener('focus', show);
        g.addEventListener('pointerleave', () => { tip.hidden = true; }); g.addEventListener('blur', () => { tip.hidden = true; });
      });
      if (items.length <= 14) {                       // label the newest column's value at its cap
        const it = items[items.length - 1], cx = m.l + band * (items.length - 1) + band / 2;
        svgEl('text', {x: cx, y: Y(finite(it.hi) ? Math.max(it.hi, it.y) : it.y) - 5, 'text-anchor': 'middle', class: 'vlabel'}, svg).textContent = fmt(it.y);
      }
    });
  }

  /* ---------- 100% stacked columns: share of local / overseas / unknown per snapshot ---------- */
  function shares(el, o) {
    const f = frame(el, o), H = o.height || 170;
    const cols = (o.columns || []).filter(c => c.parts && c.parts.some(p => p.n > 0));
    const keys = o.parts || [];
    legendItems(f.legend, keys.map(k => ({label: k.label, color: k.color, kind: 'rect'})));
    fillTable(f.table, [o.xTitle || 'Date', ...keys.map(k => k.label)],
      cols.map(c => [c.label, ...keys.map(k => { const p = c.parts.find(q => q.key === k.key); return p ? p.n : 0; })]));
    if (!cols.length) { htmlEl('div', 'chart-empty', f.body, o.empty || 'No data yet.'); return; }
    const tip = tooltip(f.body);
    tip.hidden = true;
    observe(f.body, W => {
      f.body.querySelectorAll('svg').forEach(s => s.remove());
      const m = {l: 44, r: 10, t: 8, b: 26}, w = W - m.l - m.r, h = H - m.t - m.b;
      const svg = svgEl('svg', {width: W, height: H, class: 'chart-svg', role: 'img', 'aria-label': o.title || 'Shares'});
      f.body.insertBefore(svg, f.body.firstChild);
      const grid = svgEl('g', {class: 'grid'}, svg);
      for (const v of [0, .5, 1]) {
        const y = m.t + h - v * h;
        svgEl('line', {x1: m.l, x2: m.l + w, y1: y, y2: y, class: v === 0 ? 'base' : ''}, grid);
        svgEl('text', {x: m.l - 8, y: y + 4, 'text-anchor': 'end', class: 'tick'}, grid).textContent = Math.round(v * 100) + '%';
      }
      const band = w / cols.length, bw = Math.max(3, Math.min(24, band - 4));
      const every = Math.ceil(cols.length / Math.max(1, Math.floor(w / 46)));
      cols.forEach((c, i) => {
        const tot = c.parts.reduce((s, p) => s + (p.n || 0), 0) || 1, cx = m.l + band * i + band / 2;
        let acc = 0;
        const g = svgEl('g', {class: 'colgrp', tabindex: 0}, svg);
        svgEl('rect', {x: m.l + band * i, y: m.t, width: band, height: h, fill: 'transparent'}, g);
        for (const k of keys) {
          const p = c.parts.find(q => q.key === k.key);
          if (!p || !p.n) continue;
          const y = m.t + h - (acc + p.n) / tot * h, hh = p.n / tot * h;
          svgEl('rect', {x: cx - bw / 2, y: y + 1, width: bw, height: Math.max(0, hh - 2), style: `fill: ${k.color}`}, g);   // 2px surface gap
          acc += p.n;
        }
        if (i % every === 0) svgEl('text', {x: cx, y: H - 6, 'text-anchor': 'middle', class: 'tick'}, svg).textContent = c.short || c.label;
        const show = () => showTip(tip, f.body, cx, c.label, keys.map(k => {
          const p = c.parts.find(q => q.key === k.key) || {n: 0};
          return {color: k.color, label: k.label, value: p.n + ' (' + Math.round(p.n / tot * 100) + '%)'};
        }));
        g.addEventListener('pointerenter', show); g.addEventListener('focus', show);
        g.addEventListener('pointerleave', () => { tip.hidden = true; }); g.addEventListener('blur', () => { tip.hidden = true; });
      });
    });
  }

  /* ---------- seasonality strip: 12 months, one hue light -> dark ---------- */
  function seasonStrip(el, o) {
    const f = frame(el, o);
    const v = o.values || [];
    fillTable(f.table, ['Month', 'Index (1 = average month)'], v.map((x, i) => [MONTHS[i], finite(x) ? x.toFixed(2) : null]));
    if (v.length !== 12 || !v.every(finite)) { htmlEl('div', 'chart-empty', f.body, o.empty || 'Not enough history for a seasonal pattern.'); return; }
    const strip = htmlEl('div', 'season', f.body);
    v.forEach((x, i) => {
      const k = Math.max(0, Math.min(1, (x - 0.4) / 1.2)), pctFill = Math.round(10 + k * 90);   // fixed scale 0.4-1.6
      const cell = htmlEl('div', 'season-cell' + (o.peak === i + 1 ? ' peak' : '') + (o.mark === i + 1 ? ' mark' : ''), strip);
      cell.style.background = `color-mix(in srgb, var(--s-demand) ${pctFill}%, var(--surface))`;
      cell.style.color = pctFill > 58 ? '#fff' : 'var(--ink)';
      htmlEl('b', '', cell, MONTHS[i]);
      htmlEl('span', '', cell, x.toFixed(2));
      cell.title = MONTHS[i] + ': ' + x.toFixed(2) + ' × an average month' + (o.peak === i + 1 ? ' (peak)' : '');
      cell.tabIndex = 0;
    });
    if (o.note) htmlEl('div', 'small', f.body, o.note);
  }

  /* ---------- histogram: price or reviews of the listings on page 1 ---------- */
  function histogram(el, o) {
    const id = ++uid, f = frame(el, o), fmt = o.format || fmtDefault, H = o.height || 160;
    const vals = (o.values || []).filter(finite);
    if (vals.length < 3) { htmlEl('div', 'chart-empty', f.body, o.empty || 'Not enough listings.'); return; }
    let edges;
    if (o.log) {
      const a = Math.log10(Math.max(1, Math.min(...vals) + 1)), b = Math.log10(Math.max(...vals) + 1);
      const n = Math.min(8, Math.max(4, Math.round(Math.sqrt(vals.length))));
      edges = Array.from({length: n + 1}, (_, i) => Math.round(Math.pow(10, a + (b - a) * i / n) - 1));
    } else {
      const t = ticks(Math.min(...vals), Math.max(...vals), Math.min(8, Math.max(4, Math.round(Math.sqrt(vals.length)))));
      edges = t.length > 1 ? t : [t[0], t[0] + 1];
    }
    edges = [...new Set(edges)];
    const bins = edges.slice(0, -1).map((e, i) => ({lo: e, hi: edges[i + 1], n: 0}));
    for (const v of vals) { const b = bins.find((x, i) => v >= x.lo && (v < x.hi || i === bins.length - 1)); if (b) b.n++; }
    fillTable(f.table, [o.xTitle || 'Range', 'Listings'], bins.map(b => [fmt(b.lo) + '–' + fmt(b.hi), b.n]));
    const tip = tooltip(f.body);
    tip.hidden = true;
    const color = o.color || 'var(--s-demand)';
    observe(f.body, W => {
      f.body.querySelectorAll('svg').forEach(s => s.remove());
      const m = {l: 30, r: 8, t: 12, b: 26}, w = W - m.l - m.r, h = H - m.t - m.b;
      const top = Math.max(...bins.map(b => b.n), 1), yt = ticks(0, top, 3), y1 = yt[yt.length - 1];
      const Y = v => m.t + h - v / y1 * h;
      const svg = svgEl('svg', {width: W, height: H, class: 'chart-svg', role: 'img', 'aria-label': o.title || 'Histogram'});
      f.body.insertBefore(svg, f.body.firstChild);
      const grid = svgEl('g', {class: 'grid'}, svg);
      for (const v of yt) {
        svgEl('line', {x1: m.l, x2: m.l + w, y1: Y(v), y2: Y(v), class: v === 0 ? 'base' : ''}, grid);
        svgEl('text', {x: m.l - 6, y: Y(v) + 4, 'text-anchor': 'end', class: 'tick'}, grid).textContent = v;
      }
      const band = w / bins.length;
      bins.forEach((b, i) => {
        const x = m.l + band * i + 1, bw = band - 2, y = Y(b.n);      // touching bins keep a 2px gap
        const g = svgEl('g', {class: 'colgrp', tabindex: 0}, svg);
        svgEl('rect', {x: m.l + band * i, y: m.t, width: band, height: h, fill: 'transparent'}, g);
        if (b.n) svgEl('rect', {x, y, width: bw, height: Y(0) - y, class: 'col', style: `fill: ${color}`}, g);
        if (o.mark != null && o.mark >= b.lo && o.mark < b.hi) svgEl('line', {x1: x + bw / 2, x2: x + bw / 2, y1: m.t, y2: Y(0), class: 'marker'}, svg);
        const show = () => showTip(tip, f.body, x + bw / 2, fmt(b.lo) + '–' + fmt(b.hi), [{color, label: 'listings', value: String(b.n)}]);
        g.addEventListener('pointerenter', show); g.addEventListener('focus', show);
        g.addEventListener('pointerleave', () => { tip.hidden = true; }); g.addEventListener('blur', () => { tip.hidden = true; });
      });
      [0, bins.length].forEach(i => svgEl('text', {x: m.l + band * i, y: H - 6, 'text-anchor': i ? 'end' : 'start', class: 'tick'}, svg)
        .textContent = fmt(i ? bins[bins.length - 1].hi : bins[0].lo));
    });
    void id;
  }

  /* ---------- sparkline (inline, for tables and cards) ---------- */
  /* ---------- sales per seller: one horizontal bar per listing, coloured by where it ships from ---------- */
  const ORIGIN = {local: {label: 'Local seller', color: 'var(--s-local)'}, overseas: {label: 'Ships from overseas', color: 'var(--s-overseas)'},
                  unknown: {label: 'Origin unknown', color: 'var(--s-other)'}};
  function sellerBars(el, o) {
    const id = ++uid, f = frame(el, o), max = o.max || 12, cur = o.currency || '';
    const price = v => o.priceFmt ? o.priceFmt(v) : cur + ' ' + num(v, 2);
    const all = (o.listings || []).filter(x => x.units && finite(x.units.v));
    const noBadge = (o.listings || []).length - all.length;
    const shown = all.slice(0, max), rest = all.length - shown.length;
    const key = x => ORIGIN[x.origin] ? x.origin : 'unknown';
    const used = [...new Set(shown.map(key))];
    legendItems(f.legend, ['local', 'overseas', 'unknown'].filter(k => used.includes(k)).map(k => ({label: ORIGIN[k].label, color: ORIGIN[k].color})));
    const fmtU = u => '≈ ' + compact(u.v) + ' (' + compact(u.lo) + '–' + compact(u.hi) + (u.basis === 'assumed' ? '+' : '') + ')';
    fillTable(f.table, ['Listing', 'Sales / month', 'Seller', 'Price', 'Delivery'],
      (o.listings || []).map(x => [x.title || x.asin, x.units ? fmtU(x.units) : 'no badge', ORIGIN[key(x)].label,
                                   x.price != null ? price(x.price) : null, x.days != null ? x.days + (x.days === 1 ? ' day' : ' days') : null]));
    if (!shown.length) { htmlEl('div', 'chart-empty', f.body, o.empty || 'No listing shows a “bought in past month” badge, so sales per seller are unknown.'); return; }
    const tip = tooltip(f.body);
    tip.hidden = true;
    const note = htmlEl('div', 'small muted chart-note', el);
    note.textContent = [rest ? `+${rest} more listing${rest > 1 ? 's' : ''} selling less` : '',
                        noBadge ? `${noBadge} listing${noBadge > 1 ? 's' : ''} show no badge (sales unknown, not zero)` : ''].filter(Boolean).join(' · ');
    el.insertBefore(note, f.foot);
    observe(f.body, W => {
      f.body.querySelectorAll('svg').forEach(s => s.remove());
      const row = 20, gap = 2, m = {l: 8, r: 64, t: 4, b: 22}, w = W - m.l - m.r;
      const H = m.t + shown.length * (row + gap) + m.b;
      const hiV = Math.max(...shown.map(x => x.units.hi || x.units.v), 1);
      const xt = ticks(0, hiV, Math.max(2, Math.floor(w / 90))), x1 = xt[xt.length - 1];
      const X = v => m.l + v / x1 * w;
      const svg = svgEl('svg', {width: W, height: H, class: 'chart-svg', role: 'img', 'aria-label': o.title || 'Sales per seller'});
      f.body.insertBefore(svg, f.body.firstChild);
      const hatch = patterns(svg, id);
      const grid = svgEl('g', {class: 'grid'}, svg);
      for (const v of xt) {
        svgEl('line', {x1: X(v), x2: X(v), y1: m.t, y2: H - m.b, class: v === 0 ? 'base' : ''}, grid);
        svgEl('text', {x: X(v), y: H - 6, 'text-anchor': 'middle', class: 'tick'}, grid).textContent = compact(v);
      }
      shown.forEach((x, i) => {
        const y = m.t + i * (row + gap), bh = row - 6, by = y + 3, c = ORIGIN[key(x)].color, u = x.units;
        const len = Math.max(2, X(u.v) - m.l), r = Math.min(4, bh / 2, len);
        const d = `M${m.l} ${by} H${m.l + len - r} Q${m.l + len} ${by} ${m.l + len} ${by + r} V${by + bh - r} Q${m.l + len} ${by + bh} ${m.l + len - r} ${by + bh} H${m.l} Z`;
        const g = svgEl('g', {class: 'colgrp', tabindex: 0}, svg);
        svgEl('rect', {x: 0, y, width: W, height: row, fill: 'transparent'}, g);
        if (u.basis === 'assumed') {                 // open top bucket: an assumption, hatched
          svgEl('path', {d, class: 'col', style: `fill: ${c}; opacity: .3`}, g);
          svgEl('path', {d, class: 'col', style: `fill: ${hatch(c)}`}, g);
        } else svgEl('path', {d, class: 'col', style: `fill: ${c}`}, g);
        if (finite(u.lo) && finite(u.hi) && u.hi > u.lo) {
          const cy = by + bh / 2;
          svgEl('line', {x1: X(u.lo), x2: X(u.hi), y1: cy, y2: cy, class: 'whisker'}, g);
          svgEl('line', {x1: X(u.hi), x2: X(u.hi), y1: cy - 3, y2: cy + 3, class: 'whisker'}, g);
        }
        svgEl('text', {x: Math.max(X(u.hi || u.v), m.l + len) + 6, y: by + bh - 2, class: 'vlabel'}, g).textContent = compact(u.v);
        const show = () => showTip(tip, f.body, X(u.v), x.title || x.asin, [
          {color: c, label: 'sales / month (badge estimate)', value: fmtU(u)},
          {color: c, label: ORIGIN[key(x)].label + (x.days != null ? ' · arrives in ' + x.days + (x.days === 1 ? ' day' : ' days') : ''), value: x.price != null ? price(x.price) : ''}]);
        g.addEventListener('pointerenter', show); g.addEventListener('focus', show);
        g.addEventListener('pointerleave', () => { tip.hidden = true; }); g.addEventListener('blur', () => { tip.hidden = true; });
      });
    });
  }

  /* ---------- opportunity map: buyers a month (log) against local sellers, one dot per keyword ---------- */
  function oppMap(el, o) {
    const f = frame(el, o), H = o.height || 340, pts = (o.points || []).filter(p => finite(p.demand) && p.demand > 0);
    const xLine = o.xLine ?? 4.5, yLine = o.yLine, price = v => v == null ? null : o.priceFmt ? o.priceFmt(v) : num(v, 2);
    fillTable(f.table, ['Keyword', 'Buyers / month', 'Local sellers', 'Overseas sellers', 'Buyers per local seller', 'Typical price', 'Checked'],
      pts.map(p => [p.display, '≈ ' + compact(p.demand), p.local, p.overseas, p.per_local_seller != null ? '≈ ' + compact(p.per_local_seller) : 'no local seller yet',
                    price(p.price_med), p.ts ? new Date(p.ts * 1000).toLocaleDateString() : null]));
    if (!pts.length) { htmlEl('div', 'chart-empty', f.body, o.empty || 'No checked products with sales badges yet.'); return; }
    const tip = tooltip(f.body);
    tip.hidden = true;
    observe(f.body, W => {
      f.body.querySelectorAll('svg').forEach(s => s.remove());
      const m = {l: 48, r: 16, t: 14, b: 34}, w = W - m.l - m.r, h = H - m.t - m.b;
      const xMax = Math.max(10, ...pts.map(p => p.local)) + 2;
      const dMin = Math.min(...pts.map(p => p.demand)), dMax = Math.max(...pts.map(p => p.demand));   // padded, so no dot sits on an edge
      const lo = Math.pow(10, Math.floor(Math.log10(dMin / 2))), hi = Math.pow(10, Math.ceil(Math.log10(dMax * 2)));
      const L = v => Math.log10(v), X = v => m.l + v / xMax * w, Y = v => m.t + h - (L(v) - L(lo)) / (L(hi) - L(lo) || 1) * h;
      const svg = svgEl('svg', {width: W, height: H, class: 'chart-svg', role: 'img', 'aria-label': o.title || 'Opportunity map'});
      f.body.insertBefore(svg, f.body.firstChild);
      const grid = svgEl('g', {class: 'grid'}, svg);
      for (let v = lo; v <= hi * 1.0001; v *= 10) {
        svgEl('line', {x1: m.l, x2: m.l + w, y1: Y(v), y2: Y(v), class: v === lo ? 'base' : ''}, grid);
        svgEl('text', {x: m.l - 8, y: Y(v) + 4, 'text-anchor': 'end', class: 'tick'}, grid).textContent = compact(v);
      }
      for (const v of ticks(0, xMax, Math.max(2, Math.floor(w / 80)))) {
        if (v > xMax) continue;
        svgEl('text', {x: X(v), y: m.t + h + 16, 'text-anchor': 'middle', class: 'tick'}, grid).textContent = num(v);
      }
      svgEl('text', {x: m.l + w, y: H - 4, 'text-anchor': 'end', class: 'tick'}, grid).textContent = 'local sellers on page 1 →';
      svgEl('text', {x: m.l, y: m.t - 3, class: 'tick'}, grid).textContent = '↑ buyers a month';
      const q = svgEl('g', {class: 'quad'}, svg);
      const xl = X(Math.min(xLine, xMax)), yl = pts.length >= 3 && finite(yLine) && yLine > lo && yLine < hi ? Y(yLine) : null;
      svgEl('line', {x1: xl, x2: xl, y1: m.t, y2: m.t + h, class: 'quad-line'}, q);
      if (yl != null) svgEl('line', {x1: m.l, x2: m.l + w, y1: yl, y2: yl, class: 'quad-line'}, q);
      const cap = (x, y, anchor, t, cls) => { const e = svgEl('text', {x, y, 'text-anchor': anchor, class: 'quad-cap ' + (cls || '')}, q); e.textContent = t; };
      cap(m.l + 6, m.t + 14, 'start', 'Opportunity: many buyers, few local sellers', 'good');
      cap(m.l + w - 4, m.t + 14, 'end', 'Crowded');
      if (yl != null) { cap(m.l + 6, m.t + h - 6, 'start', 'Small market'); cap(m.l + w - 4, m.t + h - 6, 'end', 'Avoid: few buyers, many sellers'); }
      const best = new Set(pts.filter(p => p.local < xLine && (yl == null || p.demand >= yLine)).slice(0, 6).map(p => p.kw));
      if (pts.length <= 3) pts.forEach(p => best.add(p.kw));              // a few dots: name them all
      const dots = svgEl('g', {}, svg), same = new Map(), placed = [];
      const key = p => p.local + ':' + Math.round(L(p.demand) * 50);
      pts.forEach(p => same.set(key(p), (same.get(key(p)) || 0) + 1));     // dots that land on one spot are nudged apart
      const seen = new Map();
      for (const p of pts) {
        const k = key(p), n = seen.get(k) || 0, size = same.get(k);
        seen.set(k, n + 1);
        const cx = X(p.local) + n * 7, cy = Y(p.demand);
        const g = svgEl('g', {class: 'dotgrp', tabindex: 0, role: 'link'}, dots);
        svgEl('circle', {cx, cy, r: 12, fill: 'transparent'}, g);
        svgEl('circle', {cx, cy, r: 5, class: 'dot'}, g);
        if (best.has(p.kw)) {                        // beside the whole group of dots; left of it near the right edge; stacked
          const t = p.display.length > 28 ? p.display.slice(0, 27) + '…' : p.display, right = cx > m.l + w * 0.7;
          const lx = right ? X(p.local) - 9 : X(p.local) + (size - 1) * 7 + 9, tw = t.length * 6.5;
          let ly = cy + 4;
          while (placed.some(b => Math.abs(b.y - ly) < 13 && Math.abs((right ? lx - tw / 2 : lx + tw / 2) - b.c) < (tw + b.w) / 2)) ly += 13;
          placed.push({y: ly, c: right ? lx - tw / 2 : lx + tw / 2, w: tw});
          svgEl('text', {x: lx, y: ly, 'text-anchor': right ? 'end' : 'start', class: 'vlabel'}, g).textContent = t;
        }
        const show = () => showTip(tip, f.body, cx, p.display, [
          {color: 'var(--s-demand)', label: 'buyers a month (badge estimate)', value: '≈ ' + compact(p.demand)},
          {color: 'var(--s-local)', label: 'local sellers', value: String(p.local)},
          {color: 'var(--s-overseas)', label: 'ship from overseas', value: String(p.overseas)},
          {color: 'var(--s-demand)', label: 'buyers per local seller', value: p.per_local_seller != null ? '≈ ' + compact(p.per_local_seller) : 'no local seller yet'}]
          .concat(p.price_med != null ? [{color: 'var(--s-other)', label: 'typical price on page 1', value: price(p.price_med)}] : []));
        g.addEventListener('pointerenter', show); g.addEventListener('focus', show);
        g.addEventListener('pointerleave', () => { tip.hidden = true; }); g.addEventListener('blur', () => { tip.hidden = true; });
        const open = () => { if (o.onOpen) o.onOpen(p); };
        g.addEventListener('click', open);
        g.addEventListener('keydown', e => { if (e.key === 'Enter') open(); });
      }
    });
  }

  function spark(values, o = {}) {
    const v = (values || []).map(x => finite(x) ? x : null);
    const pts = v.map((y, i) => [i, y]).filter(p => p[1] != null);
    const W = o.width || 96, H = o.height || 26;
    if (pts.length < 2) return '<span class="muted small">–</span>';
    const lo = Math.min(...pts.map(p => p[1])), hi = Math.max(...pts.map(p => p[1])), span = hi - lo || 1;
    const X = i => 2 + i / (v.length - 1) * (W - 6), Y = y => H - 3 - (y - lo) / span * (H - 6);
    let d = '', prev = -2;
    for (const [i, y] of pts) { d += (i === prev + 1 ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(y).toFixed(1); prev = i; }
    const [li, ly] = pts[pts.length - 1];
    return `<svg class="spark" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(o.label || 'trend')}">` +
      `<path d="${d}" style="fill:none;stroke:${o.color || 'var(--s-demand)'};stroke-width:1.6;stroke-linejoin:round;stroke-linecap:round"/>` +
      `<circle cx="${X(li)}" cy="${Y(ly)}" r="2.6" style="fill:${o.color || 'var(--s-demand)'}"/></svg>`;
  }

  /* ---------- horizontal bars in HTML, a flag next to the label when there is one ----------
     o.items: [{label, flag ('CN'), tag, value (bar length), text (shown at the end), color}]; o.columns: table head. */
  function bars(el, o) {
    const f = frame(el, o), items = o.items || [];
    legendItems(f.legend, o.legend || []);
    fillTable(f.table, o.columns || ['Name', 'Value'], items.map(i => [i.label + (i.tag ? ' (' + i.tag + ')' : ''), i.text]));
    if (!items.length) { htmlEl('div', 'chart-empty', f.body, o.empty || 'No data yet.'); return; }
    const max = Math.max(...items.map(i => i.value || 0)) || 1, list = htmlEl('div', 'hbars', f.body);
    for (const it of items) {
      const row = htmlEl('div', 'hbar', list), lab = htmlEl('div', 'hbar-label', row);
      if (it.flag) lab.insertAdjacentHTML('beforeend', cflag(it.flag));
      htmlEl('span', 'hbar-name', lab, it.label).title = it.label;
      if (it.tag) htmlEl('span', 'pill line', lab, it.tag);
      const fill = htmlEl('span', '', htmlEl('div', 'hbar-track', row));
      fill.style.width = Math.max(1.5, (it.value || 0) / max * 100) + '%';
      fill.style.background = it.color || 'var(--s-demand)';
      htmlEl('div', 'hbar-val tnum', row, it.text);
    }
  }

  return {timeseries, columns, shares, seasonStrip, histogram, spark, sellerBars, oppMap, bars, monthT, monthKey, monthLabel, MONTHS};
})();
