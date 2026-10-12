'use strict';
/* Trend radar: products climbing Amazon's Movers & Shakers and New Releases, ranked by one opportunity score in a
   matrix of their signals (engine/discovery.py), plus the Google Trends lanes of your watched keywords. */
(() => {
  let data = null, local = {};
  const open = new Set();
  view('radar', {title: 'Trend radar', sub: 'Products climbing Amazon’s lists, scored on demand, competition, price and search interest',
                 onShow: () => load()});
  const rPrice = priceBar($('rPrice'), () => segVal($('rMk')), () => load());
  seg($('rMk'), () => { local = {}; open.clear(); rPrice.draw(); load(); });
  $('rHide').addEventListener('change', render);
  $('rSort').addEventListener('change', render);
  $('rScan').addEventListener('click', async () => {
    $('rScan').disabled = true;
    $('rProg').innerHTML = busy('Starting…');
    try {
      const {job} = await api('/api/radar/scan', {market: segVal($('rMk'))});
      data = await waitJob(job, l => { $('rProg').innerHTML = busy(l); });
      $('rProg').innerHTML = data.scan_note ? `<span class="small muted">${esc(data.scan_note)}</span>` : '';
      render();
    } catch (e) { $('rProg').innerHTML = failPill(e.message); }
    $('rScan').disabled = false;
  });

  async function load() {
    try { data = await api('/api/radar?market=' + segVal($('rMk'))); render(); }
    catch (e) { $('radarOut').innerHTML = failPill(e.message); }
  }
  const LAB = {'New on the lists': 'good', Rising: 'accent', Moving: 'weak', 'Deal-driven': 'warn'};
  const checksFor = mk => mk === 'US' ? ['AU', 'AE'] : [mk === 'AU' ? 'AE' : 'AU'];
  const SORTS = {surge: i => -(i.surge || 0), reviews: i => i.reviews ?? 1e9};
  function shown() {
    const hide = $('rHide').checked, v = $('rSort').value, k = mtxSort(v) || SORTS[v] || mtxSort('opp');
    return data.items.filter(i => !hide || (!i.deal && !i.flags.some(f => f !== 'price unknown'))).sort((a, b) => k(a) - k(b));
  }

  function row(i, checks) {
    const key = i.asin, isOpen = open.has(key), mk = data.market;
    const pills = `<span class="pill ${LAB[i.label] || 'weak'}">${esc(i.label)}</span>${i.pct ? `<span class="small">+${num(i.pct)}%</span>` : ''}` +
      (i.similar ? `<span class="pill accent sim" title="${esc(i.similar + ' similar listings are grouped into this row: see More')}">+${i.similar} similar</span>` : '') + fitPill(i);
    const acts = `<button class="btn slim" data-rrep="${esc(i.search)}">${icon('chart')}Report</button>${prefActs(i)}${moreBtn(key, isOpen)}`;
    return `<tr>${prodCell(i, pills, acts)}${oppCell(i)}${SIG_KEYS.map(k => sigCell(i, k, mk, data.presence_days)).join('')}
      ${marketsCell(i, checks, local, 'data-rcheck')}</tr>
      <tr class="mtx-more" data-detail="${esc(key)}"${isOpen ? '' : ' hidden'}><td colspan="${MTX_COLS}"><div class="mtx-detail">
        <div class="stackv">${oppReasons(i)}${gapReasons(i)}</div>
        <div class="stackv">
          ${phraseHtml(i)}
          <div class="row tight">
            <a class="btn slim" href="#/p/${esc(mk)}/${esc(key)}">Product</a>
            <button class="btn slim" data-rwatch="${esc(i.search)}">${icon('eye')}Watch</button>
            <button class="btn slim" data-rtrend="${esc(i.trend_phrase || i.search)}" title="Google Trends, last 5 years">Trends${icon('external')}</button>
            ${i.trend ? `<a class="btn slim" href="#/k/${esc(mk)}/${encodeURIComponent(i.trend.term)}" title="Keyword report for “${esc(i.trend.term)}”">${icon('chart')}${esc(i.trend.term)}</a>` : ''}
          </div>
          <div class="row tight">${sourcingHtml(key, (i.buy || {}).cost_usd)}</div>
          <div class="row tight"><button class="btn slim" data-dlaunch="${esc(key)}" title="Compare its own market and the ones it could sell in next, at your supplier price">Where to launch?</button>
            <button class="btn slim" data-dvar="${esc(key)}" title="Search the longer phrases Amazon suggests for it in the other markets and rank them by gap">Find the underserved version</button>
            <button class="btn slim" data-dboard="${esc(key)}" title="Start sourcing it: the board tracks the order, the launch and re-orders">${icon('board')}Add to board</button></div>
          ${groupHtml(i, mk)}
          ${dvsHtml(checks.map(m => [m, local[key + ':' + m]]))}
        </div>
        <div class="mtx-wide" data-launch="${esc(key)}">${launchOut.get(mk + '|' + key) || ''}</div>
        <div class="mtx-wide" data-var="${esc(key)}">${varOut.get(mk + '|' + key) || ''}</div></div></td></tr>`;
  }

  function render() {
    if (!data) return;
    const d = data, checks = d.targets || checksFor(d.market);
    syncGapSorts($('rSort'), checks);
    const items = shown();
    const f = d.filtered || {}, hidden = [];
    if (f.out_of_range) hidden.push(`${num(f.out_of_range)} outside ${rangeText(d.market)}`);
    if (f.tech) hidden.push(`${num(f.tech)} phone / computer`);
    const lanes = d.trends || {};
    $('rWhen').textContent = d.last_scan ? `${d.scans} list scans in 14 days · last ${ago(d.last_scan)}` +
      (hidden.length ? ' · hidden: ' + hidden.join(', ') : '') : '';
    $('radarOut').innerHTML = `
      ${!d.scans ? `<div class="panel empty"><h2>No list scans for ${esc(MK[d.market])} yet</h2><p>Press <b>Scan now</b>, or leave automatic refresh on: the lists are read every 12 hours (USA) or daily (Australia, UAE).<br>Rising products need at least two scans to show up.</p></div>` : ''}
      ${d.clusters.length ? `<div class="panel"><h3 style="margin-bottom:8px">Groups of similar products moving together</h3><div class="row" style="gap:6px">${d.clusters.map(c => `<span class="pill ${c.surge >= 60 ? 'good' : 'weak'}" title="${c.size} similar products">${esc(c.key || 'similar products')} · ${c.size} · surge ${Math.round(c.surge)}</span>`).join('')}</div></div>` : ''}
      ${hiddenHtml(d.hidden, 'radar')}
      ${items.length ? `<div class="mtx-bar">${coverageHtml(d.coverage)}<button class="btn slim" data-dqual-top title="${esc(QUAL_TIP)} (the top 12 rows that miss something)">${icon('search')}Qualify top 12</button></div>
        <div class="tablewrap mtxwrap"><table class="mtx">${mtxHead(checks.map(m => MK_SHORT[m]).join(' & '))}<tbody>${items.map(i => row(i, checks)).join('')}</tbody></table></div>
        <p class="small muted">Green is strong, amber middling, red weak. Hatched cells are not measured yet: they count as neutral and lower the confidence. Squares: the last 14 days on the lists (blue on a list, amber only with a deal, grey read without it).</p>`
        : d.scans ? '<div class="panel empty">Nothing left after hiding risky and deal-driven products.</div>' : ''}
      ${risingHtml(d, checks)}
      <h2 class="section">Search interest of your watched keywords (Google Trends)</h2>
      <div class="lanes">${Object.entries(lanes).map(([lane, list]) => `<div class="panel lane"><h3>${esc(lane)} <span class="muted small">${list.length}</span></h3>
        ${list.map(t => `<a class="lane-item" href="#/k/${t.geo}/${encodeURIComponent(t.term)}"><b>${esc(t.term)}</b><span class="small">${esc(MK_SHORT[t.geo] || t.geo)} · ${esc(t.label)}${t.score != null ? ' · ' + Math.round(t.score) : ''}</span></a>`).join('') || '<span class="small muted">none</span>'}</div>`).join('')}</div>`;
  }

  /* Searches Google marks Breakout or rising fast next to the terms read here: demand that may not be on Amazon yet. */
  function risingHtml(d, checks) {
    const list = d.rising || [];
    if (!list.length) return '';
    const opp = o => o ? `<span class="pill ${OPP_TONE[o.label] || 'weak'}">${o.score != null ? Math.round(o.score) : '–'} ${esc(o.label || '')}</span>` : '<span class="small muted">not searched yet</span>';
    return `<h2 class="section">Rising on Google in ${esc(MK[d.market])}</h2>
      <div class="small muted">Related searches Google marks Breakout or rising fast. Amazon may not have caught up yet: Check searches them on Amazon here and in ${esc(checks.map(m => MK[m]).join(' and '))}.</div>
      <div class="tablewrap" style="box-shadow:none"><table class="rising"><thead><tr><th>Search</th><th>Rise</th><th>Found next to</th><th>Amazon ${esc(MK_SHORT[d.market])}</th>${checks.map(m => `<th>${esc(MK[m])}</th>`).join('')}<th></th></tr></thead><tbody>
      ${list.map(f => `<tr><td><b>${esc(f.query)}</b></td>
        <td>${f.breakout ? '<span class="pill good">Breakout</span>' : `<span class="pill accent">+${num(f.rise)}%</span>`}</td>
        <td class="small">${f.from.map(esc).join(', ')}</td><td>${opp(f.opportunity)}</td>
        ${checks.map(m => `<td class="mk">${f.gap[m] ? gapTile(f.gap[m], m) : '<span class="small muted">–</span>'}</td>`).join('')}
        <td><div class="row tight">${f.opportunity && checks.every(m => f.gap[m]) ? '' : `<button class="btn slim" data-gcheck="${esc(f.query)}">Check</button>`}
          <button class="btn slim" data-rrep="${esc(f.query)}">${icon('chart')}Report</button><button class="btn slim" data-rwatch="${esc(f.query)}">${icon('eye')}Watch</button></div></td></tr>`).join('')}
      </tbody></table></div>`;
  }

  async function qualify(asins) {
    if (await qualifyRows(data.market, asins, $('rProg'))) await load();
  }
  wireSourcing($('radarOut'), asin => {
    const i = data && data.items.find(x => x.asin === asin);
    return i && {market: data.market, price: i.price, category: i.category, search: i.search};
  }, () => load());
  window.addEventListener('pc:currency', () => { if (data) render(); });
  $('radarOut').addEventListener('click', async e => {
    if (await prefClick(e, data.market, load)) return;
    const g = e.target.closest('[data-gcheck]');
    if (g) {
      g.disabled = true;
      const items = [data.market, ...(data.targets || checksFor(data.market))].map(mk => ({key: g.dataset.gcheck + '|' + mk, market: mk, search: g.dataset.gcheck}));
      $('rProg').innerHTML = busy('Searching “' + g.dataset.gcheck + '” on Amazon…');
      try {
        const {job} = await api('/api/check_batch', {items});
        await waitJob(job, l => { $('rProg').innerHTML = busy(l); });
        $('rProg').innerHTML = '';
      } catch (err) { $('rProg').innerHTML = failPill(err.message); }
      await load();
      return;
    }
    const c = e.target.closest('[data-rcheck]'), r = e.target.closest('[data-rrep]'), w = e.target.closest('[data-rwatch]');
    const m = e.target.closest('[data-more]'), q = e.target.closest('[data-dqual]'), top = e.target.closest('[data-dqual-top]');
    const t = e.target.closest('[data-rtrend]');
    if (m) toggleMore(m, open);
    const L = e.target.closest('[data-dlaunch]'), V = e.target.closest('[data-dvar]'), bd = e.target.closest('[data-dboard]');
    if (bd) {
      const it = data.items.find(i => i.asin === bd.dataset.dboard);
      if (it) addToBoard({title: it.title, market: data.market, asin: it.asin, kw: it.search, image: it.image});
    }
    if (L) {
      const it = data.items.find(i => i.asin === L.dataset.dlaunch);
      if (it) launchRow(data.market, it, it.search, $('radarOut').querySelector(`[data-launch="${CSS.escape(it.asin)}"]`));
    }
    if (V) {
      const it = data.items.find(i => i.asin === V.dataset.dvar);
      if (it) { V.disabled = true; variantsRow(data.market, it, it.search, $('radarOut').querySelector(`[data-var="${CSS.escape(it.asin)}"]`)).finally(() => { V.disabled = false; }); }
    }
    if (q) { q.disabled = true; qualify([q.dataset.dqual]); }
    if (top) { top.disabled = true; qualify(shown().filter(needsQualify).slice(0, 12).map(i => i.asin)).finally(() => { top.disabled = false; }); }
    if (t) openUrl(trends(t.dataset.rtrend, data.market));
    if (r) go('k', data.market, r.dataset.rrep);
    if (w) {
      try { await api('/api/watch', {kind: 'keyword', target: w.dataset.rwatch, markets: ['US', 'AU', 'AE']}); toast('Watching “' + w.dataset.rwatch + '” in all three markets'); }
      catch (err) { toast(err.message); }
    }
    if (c) {
      const it = data.items.find(i => i.asin === c.dataset.rcheck), gap = it.gap || {};
      const items = (data.targets || checksFor(data.market)).filter(mk => !gap[mk])
        .map(mk => ({key: it.asin + ':' + mk, market: mk, search: it.search}));
      items.forEach(i => { local[i.key] = 'busy'; }); render();
      try {
        const {job} = await api('/api/check_batch', {items});
        Object.assign(local, await waitJob(job));
      } catch (err) { items.forEach(i => { local[i.key] = {error: err.message}; }); }
      await load();                                        // the search there gives the row its market gap score
    }
  });
})();
