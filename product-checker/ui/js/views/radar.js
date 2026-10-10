'use strict';
/* Trend radar: products climbing Amazon's Movers & Shakers and New Releases, grouped into similar products,
   plus the Google Trends lanes of your watched keywords. */
(() => {
  let data = null, local = {};
  view('radar', {title: 'Trend radar', sub: 'Products climbing Amazon’s lists, and where search interest is going',
                 onShow: () => load()});
  seg($('rMk'), () => { local = {}; load(); });
  $('rHide').addEventListener('change', render);
  $('rScan').addEventListener('click', async () => {
    $('rScan').disabled = true;
    $('rProg').innerHTML = busy('Starting…');
    try {
      const {job} = await api('/api/radar/scan', {market: segVal($('rMk'))});
      data = await waitJob(job, l => { $('rProg').innerHTML = busy(l); });
      $('rProg').textContent = '';
      render();
    } catch (e) { $('rProg').innerHTML = failPill(e.message); }
    $('rScan').disabled = false;
  });

  async function load() {
    try { data = await api('/api/radar?market=' + segVal($('rMk'))); render(); }
    catch (e) { $('radarOut').innerHTML = failPill(e.message); }
  }
  const LAB = {'New on the lists': 'good', Rising: 'accent', Moving: 'weak', 'Deal-driven': 'warn'};

  function render() {
    if (!data) return;
    const d = data, hide = $('rHide').checked, cur = (ST.markets[d.market] || {}).currency || '';
    $('rWhen').textContent = d.last_scan ? `${d.scans} list scans in 14 days · last ${ago(d.last_scan)}` : '';
    const items = d.items.filter(i => !hide || (!i.deal && !i.flags.length));
    const checks = d.market === 'US' ? ['AU', 'AE'] : [d.market === 'AU' ? 'AE' : 'AU'];
    const lanes = d.trends || {};
    $('radarOut').innerHTML = `
      ${!d.scans ? `<div class="panel empty"><h2>No list scans for ${esc(MK[d.market])} yet</h2><p>Press <b>Scan now</b>, or leave automatic refresh on: the lists are read every 12 hours (USA) or daily (Australia, UAE).<br>Rising products need at least two scans to show up.</p></div>` : ''}
      ${d.clusters.length ? `<div class="panel"><h3 style="margin-bottom:8px">Groups of similar products moving together</h3><div class="row" style="gap:6px">${d.clusters.map(c => `<span class="pill ${c.surge >= 60 ? 'good' : 'weak'}" title="${c.size} similar products">${esc(c.key || 'similar products')} · ${c.size} · surge ${Math.round(c.surge)}</span>`).join('')}</div></div>` : ''}
      ${items.length ? `<div class="igrid">${items.map(i => `<div class="panel icard">
        <div class="pic">${i.image ? `<img src="${esc(i.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : ''}</div>
        <div class="body">
          <div class="row" style="gap:6px"><span class="pill ${LAB[i.label] || 'weak'}">${esc(i.label)}</span><span class="pill line" title="Surge score: on the list often, climbing fast">surge ${Math.round(i.surge || 0)}</span>
            ${i.pct ? `<span class="small">+${num(i.pct)}%</span>` : ''}<span class="small muted" style="margin-left:auto">${esc(i.category)}</span></div>
          <a class="clamp2" data-open="${esc(i.url)}" style="cursor:pointer;color:var(--ink);font-weight:550;text-decoration:none">${esc(i.title)}</a>
          <div class="small">${i.price != null ? money(i.price, cur) : 'no price'} · ${i.reviews != null ? num(i.reviews) + ' reviews' : 'no reviews yet'}${i.streak_days ? ' · on the list ' + i.streak_days + ' days running' : ''}
            ${i.spark.length > 1 ? Charts.spark(i.spark.map(r => -r), {label: 'sales rank, higher is better'}) : ''}</div>
          ${i.flags.length ? '<div class="flags">' + i.flags.map(f => '<span class="flag">' + esc(f) + '</span>').join('') + '</div>' : ''}
        </div>
        <div class="acts">
          ${checks.map(mk => localPill(local[i.asin + ':' + mk], mk)).join('')}
          <button class="btn slim" data-rcheck="${esc(i.asin)}">Check ${checks.map(m => MK_SHORT[m]).join(' & ')}</button>
          <button class="btn slim" data-rrep="${esc(i.search)}">${icon('chart')}Report</button>
          <a class="btn slim" href="#/p/${esc(data.market)}/${esc(i.asin)}">Product</a>
          <button class="btn slim" data-rwatch="${esc(i.search)}">${icon('eye')}Watch</button>
        </div></div>`).join('')}</div>` : d.scans ? '<div class="panel empty">Nothing left after hiding risky and deal-driven products.</div>' : ''}
      <h2 class="section">Search interest of your watched keywords (Google Trends)</h2>
      <div class="lanes">${Object.entries(lanes).map(([lane, list]) => `<div class="panel lane"><h3>${esc(lane)} <span class="muted small">${list.length}</span></h3>
        ${list.map(t => `<a class="lane-item" href="#/k/${t.geo}/${encodeURIComponent(t.term)}"><b>${esc(t.term)}</b><span class="small">${esc(MK_SHORT[t.geo] || t.geo)} · ${esc(t.label)}${t.score != null ? ' · ' + Math.round(t.score) : ''}</span></a>`).join('') || '<span class="small muted">none</span>'}</div>`).join('')}</div>`;
  }

  $('radarOut').addEventListener('click', async e => {
    const c = e.target.closest('[data-rcheck]'), r = e.target.closest('[data-rrep]'), w = e.target.closest('[data-rwatch]');
    if (r) go('k', data.market, r.dataset.rrep);
    if (w) {
      try { await api('/api/watch', {kind: 'keyword', target: w.dataset.rwatch, markets: ['US', 'AU', 'AE']}); toast('Watching “' + w.dataset.rwatch + '” in all three markets'); }
      catch (err) { toast(err.message); }
    }
    if (c) {
      const it = data.items.find(i => i.asin === c.dataset.rcheck);
      const checks = data.market === 'US' ? ['AU', 'AE'] : [data.market === 'AU' ? 'AE' : 'AU'];
      const items = checks.map(mk => ({key: it.asin + ':' + mk, market: mk, search: it.search}));
      items.forEach(i => { local[i.key] = 'busy'; }); render();
      try {
        const {job} = await api('/api/check_batch', {items});
        Object.assign(local, await waitJob(job));
      } catch (err) { items.forEach(i => { local[i.key] = {error: err.message}; }); }
      render();
    }
  });
})();
