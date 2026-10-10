'use strict';
/* Keyword report: #/k/<market>/<keyword>. Demand (Google Trends + Amazon), units and revenue per month,
   supply over time, competition, every market side by side, and where each number comes from. */
(() => {
  const BASIS_LABEL = {reported: 'Helium 10', estimated: 'estimate', backcast: 'backcast', forecast: 'forecast', measured: 'measured'};
  let current = null, loading = 0;

  view('k', {
    title: p => p[1] ? '“' + p[1] + '”' : 'Keyword report',
    sub: p => p[1] ? 'Keyword report · Amazon ' + (MK[p[0]] || p[0]) : '',
    onShow: p => load(p[0] in MK ? p[0] : 'AU', p[1] || ''),
  });

  async function load(market, kw) {
    const box = $('kOut');
    if (!kw) { box.innerHTML = '<div class="panel empty"><h2>Which keyword?</h2><p>Open a report from Check sellers, Product ideas or the Library.</p></div>'; return; }
    const token = ++loading;
    if (!current || current.kw_norm !== kw.toLowerCase() || current.market !== market) box.style.opacity = .55;
    try {
      const data = await api('/api/report/keyword?market=' + market + '&kw=' + encodeURIComponent(kw));
      if (token !== loading) return;
      current = data;
      render(data);
    } catch (e) { box.innerHTML = failPill(e.message); }
    box.style.opacity = '';
  }

  const fmtU = v => v == null ? '–' : compact(v);
  const rangeText = e => e && e.lo != null && e.hi != null ? fmtU(e.lo) + '–' + fmtU(e.hi) : '';
  const basisPill = e => e ? `<span class="pill line" title="How this number was made">${esc(BASIS_LABEL[e.basis] || e.basis)}</span>` : '';
  const tMs = t => t * 1000;

  function tile(label, value, sub, extra = '') {
    return `<div class="kpi"><span class="eyebrow">${esc(label)}</span><span class="v">${value}</span>${sub ? `<span class="s">${sub}</span>` : ''}${extra}</div>`;
  }

  function render(d) {
    const box = $('kOut'), cur = d.currency, last = d.monthly[d.monthly.length - 1];
    const tr = d.trends || {}, now = d.now, comp = d.competition;
    const levelPill = now ? `<span class="pill ${LEVEL[now.level] || 'weak'}">${icon(LEVEL_ICON[now.level] || 'dot')}${esc({opportunity: 'Few fast local sellers', some: 'Some local competition', crowded: 'Crowded locally', none: 'No results'}[now.level] || now.level)}</span>` : '';
    const trendPill = tr.label ? `<span class="pill ${/Growing|Emerging|Breakout/.test(tr.label) ? 'good' : /Declining|Fad/.test(tr.label) ? 'bad' : /Spike/.test(tr.label) ? 'warn' : 'weak'}">${esc(tr.label)}${tr.score != null ? ' · ' + Math.round(tr.score) : ''}</span>` : '';
    box.innerHTML = `
      <div class="panel stackv">
        <div class="head">
          <div class="row" style="gap:10px">
            <div class="seg" id="kMk">${Object.keys(MK).map(m => `<button type="button" data-v="${m}" aria-pressed="${m === d.market}">${MK[m]}</button>`).join('')}</div>
            ${levelPill}${trendPill}
          </div>
          <div class="row">
            <button class="btn primary" id="kRefresh">${icon('refresh')}Refresh now</button>
            <button class="btn" data-open="${esc(d.search_url)}">Amazon${icon('external')}</button>
            <button class="btn" data-open="${esc(d.trends_url)}">Google Trends${icon('external')}</button>
            <button class="btn" id="kXray">${icon('scan')}Xray</button>
            <button class="btn" id="kWatch" title="Re-check this keyword every day and fetch Google Trends weekly">${icon('eye')}Watch</button>
          </div>
        </div>
        <div class="progress" id="kProg">${now ? 'Last searched ' + ago(now.checked_at) : 'Not searched in ' + MK[d.market] + ' yet.'}${tr.as_of ? ' · Google Trends ' + ago(tr.as_of) : ''}</div>
      </div>
      <div class="kpis">
        ${tile('Units sold / month', last ? fmtU(last.units.v) : '–', last ? (rangeText(last.units) ? rangeText(last.units) + ' · ' : '') + monthName(last.month) : 'no estimate yet', last ? basisPill(last.units) : '')}
        ${tile('Revenue / month', last && last.revenue && last.revenue.v != null ? esc(cur) + ' ' + compact(last.revenue.v) : '–', last && last.revenue ? rangeText(last.revenue) : '', last && last.revenue ? basisPill(last.revenue) : '')}
        ${tile('Local sellers', now ? now.local : '–', now ? `${now.fast} fast · ${now.overseas} overseas of ${now.total}` : '')}
        ${tile('Search trend', tr.label ? esc(tr.label) : tr.status === 'low_volume' ? 'Low volume' : '–', tr.score != null ? 'score ' + Math.round(tr.score) + ' of 100' : tr.status === 'none' ? 'not fetched yet' : '')}
        ${tile('Review barrier', comp && comp.review_barrier != null ? num(comp.review_barrier) : '–', 'median reviews, top 10')}
        ${d.demand.volume && d.demand.volume.length ? tile('Searches / month', compact(d.demand.volume[d.demand.volume.length - 1].volume), 'Helium 10 ' + esc(d.demand.volume[d.demand.volume.length - 1].source) + ', ' + esc(d.demand.volume[d.demand.volume.length - 1].month)) : ''}
        ${tile('Price', comp && comp.prices.length ? esc(cur) + ' ' + num(median(comp.prices), 0) : '–', comp && comp.prices.length ? 'median of ' + comp.prices.length + ' listings' : '')}
      </div>
      ${tr.why && tr.why.length ? `<div class="panel"><h3 style="margin-bottom:8px">Why “${esc(tr.label)}”</h3><ul class="reasons">${tr.why.map(w => `<li class="${/Declining|Fad/.test(tr.label) ? 'bad' : /Spike|Mixed|Seasonal/.test(tr.label) ? 'warn' : 'good'}">${esc(w)}</li>`).join('')}</ul></div>` : ''}
      <h2 class="section">Demand</h2>
      <div class="grid2">
        <div class="panel" id="kTrends"></div>
        <div class="panel" id="kSeason"></div>
      </div>
      <div class="panel" id="kUnits"></div>
      <div class="panel" id="kRevenue"></div>
      <h2 class="section">Demand and supply since you started checking</h2>
      <div class="grid2">
        <div class="panel" id="kAmz"></div>
        <div class="panel" id="kSupply"></div>
      </div>
      <div class="panel" id="kMix"></div>
      <h2 class="section">Competition now</h2>
      <div class="grid2">
        <div class="panel" id="kPrice"></div>
        <div class="panel" id="kRev"></div>
      </div>
      <div id="kTop"></div>
      <h2 class="section">All markets</h2>
      <div id="kMarkets"></div>
      <h2 class="section">Where these numbers come from</h2>
      <div class="panel stackv" id="kData"></div>`;

    seg($('kMk'), mk => go('k', mk, d.keyword));
    $('kRefresh').addEventListener('click', () => refresh(d));
    $('kWatch').addEventListener('click', () => api('/api/watch', {kind: 'keyword', target: d.keyword, markets: [d.market]})
      .then(() => toast('Watching “' + d.keyword + '” in ' + MK[d.market] + '. It is re-checked every day.')).catch(e => toast(e.message)));
    $('kXray').addEventListener('click', () => { $('xkw').value = d.keyword; setSeg($('xMk'), d.market); go('xray'); });
    drawTrends(d);
    drawSeason(d);
    drawUnits(d);
    drawRevenue(d);
    drawRecent(d);
    drawCompetition(d);
    drawMarkets(d);
    drawData(d);
  }

  const median = v => { const s = [...v].sort((a, b) => a - b); return s.length ? s[Math.floor(s.length / 2)] : null; };
  const monthName = key => Charts.MONTHS[+key.slice(5) - 1] + ' ' + key.slice(0, 4);

  function drawTrends(d) {
    const tr = d.trends || {}, el = $('kTrends');
    const pts = (tr.monthly || []).map(([m, v]) => ({t: Charts.monthT(m), y: v}));
    const empty = tr.status === 'low_volume' ? 'Not enough Google searches for “' + d.keyword + '” in ' + MK[d.market] + '. That is not the same as no demand: check Amazon below.'
      : tr.status === 'none' ? 'Google Trends not fetched yet. Press Refresh now.' : 'No Google Trends data.';
    Charts.timeseries(el, {title: 'Search interest, 5 years', sub: 'Google Trends in ' + MK[d.market] + ' · 100 = the busiest month',
      x: 'month', series: pts.length ? [{label: 'Search interest', color: 'var(--s-demand)', basis: 'index', points: pts}] : [],
      yMin: 0, yMax: 100, empty, foot: ['Source: Google Trends (index, not search counts)', tr.as_of ? 'Fetched ' + ago(tr.as_of) : null,
        tr.q != null ? 'Agreement between samples ' + Math.round(tr.q * 100) + '%' : null].filter(Boolean)});
  }

  function drawSeason(d) {
    const s = (d.trends || {}).seasonality;
    const note = s && s.reliable ? `Peak month: ${Charts.MONTHS[s.peak_month - 1]}. Have stock live by ${Charts.MONTHS[(s.launch_month || 1) - 1]} to catch it.`
      : s ? 'The pattern is weak or the history is short, so treat it as a hint.' : null;
    Charts.seasonStrip($('kSeason'), {title: 'Seasonality', sub: 'Each month compared with an average month (1.00)',
      values: s ? s.si : null, peak: s && s.reliable ? s.peak_month : null, mark: s && s.reliable ? s.launch_month : null,
      note, foot: ['From the Google Trends shape'], empty: 'Needs about two years of Google Trends data.'});
  }

  function drawUnits(d) {
    const series = [];
    const pts = basis => d.monthly.filter(m => m.units.basis === basis).map(m => ({t: Charts.monthT(m.month), y: m.units.v, lo: m.units.lo, hi: m.units.hi,
      note: m.method === 'badge' ? (m.badged || 0) + ' listings with a sales badge' : m.listings ? m.listings + ' listings in the export' : ''}));
    if (d.backcast.length) series.push({label: 'Backcast from search trend', color: 'var(--s-demand)', basis: 'backcast',
      points: d.backcast.map(m => ({t: Charts.monthT(m.month), y: m.units.v, lo: m.units.lo, hi: m.units.hi}))});
    const est = pts('estimated');
    if (est.length) series.push({label: 'Badge estimate', color: 'var(--s-demand)', basis: 'estimated', points: est, maxGap: 70 * 864e5});
    const h10 = pts('reported');
    if (h10.length) series.push({label: 'Helium 10', color: 'var(--s-demand)', basis: 'reported', points: h10});
    if (d.forecast.length) series.push({label: 'Forecast', color: 'var(--s-demand)', basis: 'forecast',
      points: [d.monthly[d.monthly.length - 1], ...d.forecast].map(m => ({t: Charts.monthT(m.month), y: m.units.v, lo: m.units.lo, hi: m.units.hi}))});
    Charts.timeseries($('kUnits'), {title: 'Units sold per month', sub: 'Page 1 of the Amazon search. Each kind of number has its own line style.',
      x: 'month', series, yMin: 0, endLabel: false,
      empty: 'No monthly numbers yet. Run Check sellers for this keyword, or import a Helium 10 Xray export (Library → Helium 10 imports).',
      foot: ['Helium 10 = its own estimate (±25%)', 'Badge estimate = “bought in past month” ranges, listings with a badge only (an undercount)',
        d.backcast.length ? 'Backcast = recent units × the Google Trends shape' : null, d.forecast.length ? 'Forecast = seasonal pattern × yearly trend' : null].filter(Boolean)});
  }

  function drawRevenue(d) {
    const items = [...d.backcast, ...d.monthly].filter(m => m.revenue && m.revenue.v != null).map(m => ({label: monthName(m.month),
      short: Charts.monthLabel(Charts.monthT(m.month), m.month.endsWith('-01')),
      y: m.revenue.v, lo: m.revenue.lo, hi: m.revenue.hi, basis: m.revenue.basis}));
    Charts.columns($('kRevenue'), {title: 'Revenue per month', sub: d.currency + ', page 1 listings', items, yTitle: 'Revenue (' + d.currency + ')',
      yFormat: v => compact(v), basisLabels: {reported: 'Helium 10', estimated: 'Badge estimate', backcast: 'Backcast at today’s price'},
      empty: 'No revenue numbers yet.', foot: ['Units × the price seen at the time', d.backcast.length ? 'Backcast months use today’s price (past prices were not seen)' : null].filter(Boolean)});
  }

  function drawRecent(d) {
    const group = 'kr' + d.market + d.kw_norm;
    const amz = d.demand.amazon.map(p => ({t: tMs(p.t), y: p.v, lo: p.lo, hi: p.hi, note: p.badged + ' with a badge'}));
    const gaps = d.supply.gaps.map(g => ({t: tMs(g.t), status: g.status}));
    const ts = [...amz.map(p => p.t), ...d.supply.points.map(p => tMs(p.t)), ...gaps.map(g => g.t)];
    const dom = ts.length ? [Math.min(...ts), Math.max(...ts)] : null;
    Charts.timeseries($('kAmz'), {title: 'Bought per month on page 1', sub: 'Sum of “bought in past month” badges, each time you checked',
      series: amz.length ? [{label: 'Units', color: 'var(--s-demand)', basis: 'estimated', points: amz}] : [], gaps, xDomain: dom, group,
      yMin: 0, empty: 'No checks yet. Press Refresh now.', foot: ['Source: Amazon search page (estimate from badge ranges)']});
    const sp = d.supply.points;
    Charts.timeseries($('kSupply'), {title: 'Sellers on page 1', sub: 'Local = arrives within ' + (d.now ? d.now.local_days : 9) + ' days',
      series: sp.length ? [
        {label: 'Local', color: 'var(--s-local)', basis: 'measured', points: sp.map(p => ({t: tMs(p.t), y: p.local}))},
        {label: 'Overseas', color: 'var(--s-overseas)', basis: 'measured', points: sp.map(p => ({t: tMs(p.t), y: p.overseas}))},
      ] : [], gaps, xDomain: dom, group, yMin: 0, empty: 'No checks yet.', foot: ['Source: Amazon search page (measured)', gaps.length ? gaps.length + ' blocked or failed check' + (gaps.length > 1 ? 's' : '') + ' shown as grey marks' : null].filter(Boolean)});
    Charts.shares($('kMix'), {title: 'Where page 1 ships from', sub: 'Share of organic listings at each check',
      parts: ORIGIN.map(o => ({key: o.key, label: o.label, color: o.color})),
      columns: sp.map(p => ({label: new Date(tMs(p.t)).toLocaleDateString(), short: new Date(tMs(p.t)).toLocaleDateString(undefined, {day: 'numeric', month: 'short'}),
        parts: [{key: 'local', n: p.local || 0}, {key: 'overseas', n: p.overseas || 0}, {key: 'unknown', n: p.unknown || 0}]})),
      empty: 'No checks yet.', foot: ['Source: Amazon search page (measured)']});
  }

  function drawCompetition(d) {
    const c = d.competition;
    Charts.histogram($('kPrice'), {title: 'Prices on page 1', sub: d.currency, values: c ? c.prices : [], format: v => num(v, 0), xTitle: 'Price',
      empty: 'No listings yet.', foot: ['Latest check']});
    Charts.histogram($('kRev'), {title: 'Reviews on page 1', sub: 'Fewer reviews = easier to compete', values: c ? c.reviews : [], log: true,
      format: v => compact(v), xTitle: 'Reviews', color: 'var(--s-demand)', empty: 'No listings yet.',
      foot: [c && c.low_review_n != null ? c.low_review_n + ' listings under 100 reviews' : 'Latest check']});
    if (!c || !c.top.length) { $('kTop').innerHTML = ''; return; }
    const cur = d.currency;
    $('kTop').innerHTML = `<div class="head" style="margin-bottom:8px"><h3>Top listings now</h3>${c.top3_share != null ? `<span class="small">Top 3 badged listings take ${Math.round(c.top3_share * 100)}% of badge sales</span>` : ''}</div>
      <div class="tablewrap"><table><thead><tr><th class="r">#</th><th>Product</th><th>Ships from</th><th class="r">Price</th><th class="r">Reviews</th><th class="r">Bought/mo</th></tr></thead><tbody>
      ${c.top.map((r, i) => `<tr><td class="r">${i + 1}</td><td class="title"><a data-open="${esc(r.url)}">${esc(r.title)}</a><div class="sub">${esc(r.asin)} · <a href="#/p/${d.market}/${esc(r.asin)}">product report</a></div></td>
        <td>${r.origin === 'local' ? '<span class="pill line"><span class="swatch" style="background:var(--s-local)"></span>local</span>' : r.origin === 'overseas' ? '<span class="pill line"><span class="swatch" style="background:var(--s-overseas)"></span>overseas</span>' : '<span class="pill weak">unknown</span>'}<div class="sub">${esc(r.why || '')}</div></td>
        <td class="r">${money(r.price, '')}</td><td class="r">${num(r.reviews)}</td><td class="r">${r.units ? fmtU(r.units.v) + '<div class="sub">' + rangeText(r.units) + '</div>' : ''}</td></tr>`).join('')}
      </tbody></table></div><p class="small" style="margin-top:6px">Prices in ${esc(cur)}. Bought/mo is the middle of the badge range.</p>`;
  }

  function drawMarkets(d) {
    const rows = Object.entries(d.markets);
    $('kMarkets').innerHTML = `<div class="tablewrap"><table><thead><tr><th>Market</th><th class="r">Local</th><th class="r">Overseas</th><th class="r">Fast</th><th>Sellers</th><th class="r">Units/mo (page 1)</th><th>Search trend</th><th>Last check</th><th></th></tr></thead><tbody>
      ${rows.map(([m, r]) => `<tr${m === d.market ? ' class="fast"' : ''}><td><b>${MK[m]}</b></td><td class="r">${r.local ?? '–'}</td><td class="r">${r.overseas ?? '–'}</td><td class="r">${r.fast ?? '–'}</td>
        <td>${r.level ? `<span class="pill ${LEVEL[r.level] || 'weak'}"><i></i>${esc({opportunity: 'gap', some: 'some', crowded: 'crowded', none: 'none'}[r.level])}</span>` : '<span class="muted small">not checked</span>'}</td>
        <td class="r">${r.units ? fmtU(r.units.v) : '–'}</td><td>${r.trend && r.trend.label ? esc(r.trend.label) : r.trend && r.trend.status === 'low_volume' ? '<span class="muted">low volume</span>' : '–'}</td>
        <td>${r.checked_at ? ago(r.checked_at) : '–'}</td><td><button class="btn slim" data-kmk="${m}">${m === d.market ? 'Shown' : 'Open'}</button></td></tr>`).join('')}
      </tbody></table></div>`;
    $('kMarkets').querySelectorAll('[data-kmk]').forEach(b => b.addEventListener('click', () => go('k', b.dataset.kmk, d.keyword)));
  }

  function drawData(d) {
    const st = s => storageOk(s) ? 'good' : s === 'wrong_location' ? 'warn' : 'bad';
    const snaps = d.provenance.snapshots;
    $('kData').innerHTML = `
      <div><h3>Amazon checks (${snaps.length})</h3><div class="row" style="gap:4px;margin-top:6px">${snaps.slice(-40).map(s => `<span class="pill ${st(s.status)}" title="${esc(new Date(s.t * 1000).toLocaleString() + ' · ' + s.status + (s.error ? ': ' + s.error : '') + ' · ' + (s.source || ''))}"><i></i>${new Date(s.t * 1000).toLocaleDateString(undefined, {day: 'numeric', month: 'short'})}</span>`).join('') || '<span class="muted small">none yet</span>'}</div></div>
      <div><h3>Google Trends samples (${(d.trends.samples || []).length})</h3><div class="small">${(d.trends.samples || []).slice(-10).map(s => esc(new Date(s.t * 1000).toLocaleDateString() + ': ' + s.status)).join(' · ') || 'none yet'}</div></div>
      <div><h3>Helium 10 files</h3><div class="small">${d.provenance.imports.map(i => esc(i.kind + ' · ' + i.month + ' · ' + i.file + ' (' + i.n_rows + ' rows)')).join('<br>') || 'None for this keyword. Export Xray for it and the file is picked up from Downloads.'}</div></div>
      <div class="note">Every estimate shows its range. Badge numbers count only listings that show “bought in past month”, so they are an undercount. Helium 10 numbers are its own estimates (typically 15–30% off). Google Trends is an index of search interest, not a number of searches. A blocked or failed check is a gap, never a zero.</div>`;
  }
  const storageOk = s => ['ok', 'empty', 'partial'].includes(s);

  async function refresh(d) {
    $('kRefresh').disabled = true;
    $('kProg').innerHTML = busy('Starting…');
    try {
      const {job} = await api('/api/report/refresh', {market: d.market, kw: d.keyword});
      const data = await waitJob(job, line => { $('kProg').innerHTML = busy(line); });
      current = data;
      render(data);
      toast('Report updated');
    } catch (e) { $('kProg').innerHTML = failPill(e.message); $('kRefresh').disabled = false; }
  }

  window.openReport = (market, kw) => go('k', market, kw);
})();
