'use strict';
/* Product report: #/p/<market>/<asin>. Sales rank over time, estimated units and revenue per month, price,
   reviews, offers, and the same product in the other markets. */
(() => {
  view('p', {title: p => p[1] || 'Product report', sub: p => p[1] ? 'Product report · Amazon ' + (MK[p[0]] || p[0]) : '',
             section: 'p', onShow: p => load(p[0] in MK ? p[0] : 'US', (p[1] || '').toUpperCase())});
  const fmtU = v => v == null ? '–' : compact(v);
  const range = e => e && e.lo != null && e.hi != null ? fmtU(e.lo) + '–' + fmtU(e.hi) : '';

  async function load(market, asin) {
    if (!asin) { $('pOut').innerHTML = '<div class="panel empty"><h2>Which product?</h2></div>'; return; }
    try { render(await api('/api/report/product?market=' + market + '&asin=' + encodeURIComponent(asin))); }
    catch (e) { $('pOut').innerHTML = failPill(e.message); }
  }

  function render(d) {
    const info = d.info || {}, now = d.now, cur = d.currency, cv = d.curve, last = d.monthly[d.monthly.length - 1];
    $('pageTitle').textContent = info.title ? info.title.slice(0, 80) + (info.title.length > 80 ? '…' : '') : d.asin;
    const calib = !cv ? '<span class="pill warn">' + icon('warn') + 'no sales curve yet</span>'
      : cv.status === 'calibrated' ? `<span class="pill good">${icon('ok')}curve calibrated on ${cv.n_obs} readings</span>`
      : `<span class="pill warn" title="Fewer than 30 readings in ${esc(MK[d.market])}: the curve leans on default values">${icon('warn')}curve not calibrated yet (${cv.n_obs || 0} reading${cv.n_obs === 1 ? '' : 's'})</span>`;
    $('pOut').innerHTML = `
      <div class="panel" style="display:grid;grid-template-columns:96px 1fr;gap:16px;align-items:start">
        <div class="icard"><div class="pic" style="width:96px;height:96px">${info.image ? `<img src="${esc(info.image)}" alt="" referrerpolicy="no-referrer" onerror="this.remove()">` : ''}</div></div>
        <div class="stackv" style="gap:10px">
          <div class="row" style="gap:8px"><div class="seg" id="pMk">${Object.keys(MK).map(m => `<button type="button" data-v="${m}" aria-pressed="${m === d.market}">${MK[m]}</button>`).join('')}</div>
            <span class="pill line">${esc(d.asin)}</span>${info.root_name ? `<span class="pill line">${esc(info.root_name)}</span>` : ''}${calib}</div>
          <div class="row">
            <button class="btn primary" id="pRefresh">${icon('refresh')}Read the product page now</button>
            <button class="btn" data-open="${esc(d.url)}">Amazon${icon('external')}</button>
            <button class="btn" id="pTrack">${icon('eye')}Track daily</button>
          </div>
          <div class="progress" id="pProg">${now ? 'Last read ' + ago(now.ts) : 'Not read in ' + MK[d.market] + ' yet. Press “Read the product page now”.'}</div>
        </div>
      </div>
      <div class="kpis">
        <div class="kpi"><span class="eyebrow">Best Sellers Rank</span><span class="v">${now && now.root_rank ? '#' + num(now.root_rank) : '–'}</span><span class="s">${esc(info.root_name || '')}</span></div>
        <div class="kpi"><span class="eyebrow">Units / month now</span><span class="v">${d.units_now ? fmtU(d.units_now.v) : '–'}</span><span class="s">${d.units_now ? range(d.units_now) + ' · from the sales rank' : 'needs a rank and a curve'}</span></div>
        <div class="kpi"><span class="eyebrow">Revenue, ${last ? esc(last.month) : 'this month'}</span><span class="v">${last && last.revenue ? esc(cur) + ' ' + compact(last.revenue.v) : '–'}</span><span class="s">${last && last.revenue ? range(last.revenue) : ''}</span></div>
        <div class="kpi"><span class="eyebrow">Price</span><span class="v">${now && now.price ? esc(cur) + ' ' + num(now.price, 2) : '–'}</span><span class="s">${now && now.badge_low ? num(now.badge_low) + '+ bought last month' : 'no sales badge'}</span></div>
        <div class="kpi"><span class="eyebrow">Reviews</span><span class="v">${now && now.reviews != null ? num(now.reviews) : '–'}</span><span class="s">${now && now.rating ? '★ ' + now.rating : ''}</span></div>
        <div class="kpi"><span class="eyebrow">Sellers</span><span class="v">${now && now.offers != null ? now.offers : '–'}</span><span class="s">${now ? esc(now.buybox_seller || '') + (now.sold_by_amazon ? ' · Amazon sells it' : '') : ''}</span></div>
      </div>
      <div class="panel" id="pRank"></div>
      <div class="grid2"><div class="panel" id="pUnits"></div><div class="panel" id="pRev"></div></div>
      <div class="grid2"><div class="panel" id="pPrice"></div><div class="panel" id="pReviews"></div></div>
      <h2 class="section">Other markets</h2>
      <div class="tablewrap"><table><thead><tr><th>Market</th><th class="r">Rank</th><th class="r">Price</th><th class="r">Sellers</th><th>Ships from</th><th>Read</th><th></th></tr></thead><tbody>
        ${Object.entries(d.other_markets).map(([m, r]) => `<tr><td><b>${MK[m]}</b></td><td class="r">${r && r.root_rank ? '#' + num(r.root_rank) : '–'}</td><td class="r">${r && r.price ? num(r.price, 2) : '–'}</td>
          <td class="r">${r && r.offers != null ? r.offers : '–'}</td><td>${r ? esc(r.origin || '') : '<span class="muted small">not read</span>'}</td><td>${r ? ago(r.ts) : '–'}</td>
          <td><a class="btn slim" href="#/p/${m}/${d.asin}">Open</a></td></tr>`).join('')}
      </tbody></table></div>
      <div class="note">Units come from the sales rank through the ${esc(MK[d.market])} sales curve, fitted on listings that show both a rank and a “bought in past month” badge (plus your Helium 10 exports). Each month adds up the daily estimates; it never uses the average rank. ${cv && cv.status !== 'calibrated' ? 'Until 30 readings exist the curve leans on default values, so treat the numbers as rough (often 2–4× off).' : ''}</div>`;

    seg($('pMk'), mk => go('p', mk, d.asin));
    $('pRefresh').addEventListener('click', () => refresh(d));
    $('pTrack').addEventListener('click', () => api('/api/watch', {kind: 'asin', target: d.asin, markets: [d.market]})
      .then(() => toast('Tracking ' + d.asin + ' daily in ' + MK[d.market])).catch(e => toast(e.message)));

    const hist = d.history.filter(h => ['ok', 'partial'].includes(h.status));
    const gaps = d.history.filter(h => !['ok', 'empty', 'partial'].includes(h.status)).map(h => ({t: h.ts * 1000, status: h.status}));
    const rk = hist.filter(h => h.root_rank).map(h => ({t: h.ts * 1000, y: Math.log10(h.root_rank)}));
    Charts.timeseries($('pRank'), {title: 'Best Sellers Rank', sub: 'Log scale; lower is better (shown upside down, so up = selling more)',
      series: rk.length ? [{label: 'Rank', color: 'var(--s-demand)', basis: 'measured', points: rk.map(p => ({...p, y: -p.y}))}] : [], gaps,
      yFormat: v => '#' + compact(Math.round(Math.pow(10, -v))), empty: 'No rank readings yet.', foot: ['Source: Amazon product page']});
    const u = d.monthly.map(m => ({t: Charts.monthT(m.month), y: m.units.v, lo: m.units.lo, hi: m.units.hi, note: m.partial ? 'part of the month' : ''}));
    const h10 = d.h10.filter(r => r.sales_asin || r.sales_parent).map(r => ({t: Charts.monthT(r.month), y: r.sales_asin || r.sales_parent}));
    Charts.timeseries($('pUnits'), {title: 'Units sold per month', sub: 'Estimate from the sales rank', x: 'month', yMin: 0,
      series: [u.length ? {label: 'From sales rank', color: 'var(--s-demand)', basis: 'estimated', points: u} : null,
               h10.length ? {label: 'Helium 10', color: 'var(--s-demand)', basis: 'reported', points: h10} : null].filter(Boolean),
      empty: 'Needs rank readings and a sales curve.', foot: ['Estimate with an 80% range']});
    Charts.columns($('pRev'), {title: 'Revenue per month', sub: cur, yFormat: v => compact(v),
      items: d.monthly.filter(m => m.revenue).map(m => ({label: m.month, short: Charts.monthLabel(Charts.monthT(m.month), m.month.endsWith('-01')),
        y: m.revenue.v, lo: m.revenue.lo, hi: m.revenue.hi, basis: 'estimated'})), empty: 'No revenue estimate yet.', foot: ['Units × the price seen']});
    Charts.timeseries($('pPrice'), {title: 'Price', sub: cur, series: hist.some(h => h.price) ? [{label: 'Price', color: 'var(--s-demand)', basis: 'measured',
      points: hist.filter(h => h.price).map(h => ({t: h.ts * 1000, y: h.price}))}] : [], yFormat: v => num(v, 0), empty: 'No prices yet.'});
    Charts.timeseries($('pReviews'), {title: 'Reviews', series: hist.some(h => h.reviews != null) ? [{label: 'Reviews', color: 'var(--s-demand)', basis: 'measured',
      points: hist.filter(h => h.reviews != null).map(h => ({t: h.ts * 1000, y: h.reviews}))}] : [], empty: 'No review counts yet.'});
  }

  async function refresh(d) {
    $('pRefresh').disabled = true;
    $('pProg').innerHTML = busy('Starting…');
    try {
      const {job} = await api('/api/report/product/refresh', {market: d.market, asin: d.asin});
      render(await waitJob(job, l => { $('pProg').innerHTML = busy(l); }));
    } catch (e) { $('pProg').innerHTML = failPill(e.message); $('pRefresh').disabled = false; }
  }
})();
