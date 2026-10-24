'use strict';
/* Gap Finder: products proven in the USA, the UK and Germany that are open in Australia or the UAE.
   Also binds the Gap Finder and SP-API panels in Settings. */
(() => {
  view('gaps', {title: 'Gap Finder', sub: 'Proven in the USA, UK and Germany · open in Australia or the UAE', onShow: () => load()});
  const PROOF = ['US', 'GB', 'DE'];
  const TONE = {lone_abroad: 'good', lone_fba: 'good', amazon_only: 'good', imported: 'good', missing: 'good', few: 'warn',
    crowded: 'bad', unread: 'weak'};
  const ICON = {good: 'ok', warn: 'warn', bad: 'x', weak: 'dot'};
  const LONE = new Set(['lone_abroad', 'lone_fba']);
  const SORT = {title: r => (r.title || '').toLowerCase(), market: r => r.market, proof: r => -r.proof_n,
    gap: r => r.order, sellers: r => r.sellers ?? 99, demand: r => -(r.demand_floor || 0),
    price: r => r.price ?? Infinity, cost: r => -(r.max_cogs_20 || 0)};
  let D = null, sortBy = null, sortDir = 1;
  seg($('gMk'), () => render());
  $('gAll').addEventListener('change', () => render());
  $('gWarn').addEventListener('change', () => render());

  async function load() {
    try { D = await api('/api/gaps'); } catch (e) { $('gapsOut').innerHTML = failPill(e.message); return; }
    render();
  }
  const site = mk => (ST.markets[mk] || {}).site || '';
  const unitsTxt = u => u && u.v != null ? '~' + compact(u.v) + '/mo' + (u.lo != null && u.hi != null ? ` (${compact(u.lo)}–${compact(u.hi)})` : '') : '';
  const kpi = (label, v, sub, cls = '') => `<div class="kpi ${cls}"><span class="eyebrow">${esc(label)}</span><span class="v">${v}</span><span class="s">${sub}</span></div>`;

  function proofCell(r) {
    return '<td class="gproof">' + PROOF.map(m => {
      const p = r.proof[m]; if (!p) return '';
      const tone = p.well ? 'good' : p.well === false ? 'warn' : 'weak';
      const est = p.units ? unitsTxt(p.units) + ' · ' + (p.src || 'estimate') : '';
      return `<div class="gp"><span class="pill ${tone}" title="${esc(p.why || '')}">${cflag(m, '')}${esc(MK_PROOF[m] || MK_SHORT[m] || m)}${p.well ? ' ✓' : ''}</span>` +
        `<span class="tsub">${esc(est || p.why || '')}</span></div>`;
    }).join('') + '</td>';
  }
  function gapCell(r) {
    const t = TONE[r.type] || 'weak';
    return `<td class="gwhy"><span class="pill ${t}" title="${esc(r.means || '')}">${icon(ICON[t])}${esc(r.label)}</span>
      <div class="small">${esc(r.why || '')}</div>
      <span class="tsub">${esc(r.conf || 'low')} confidence${r.source === 'spapi' ? ' · seller API' : ''}${r.stopped ? ' · stopped early: ' + esc(r.stopped) : ''}</span></td>`;
  }
  function sellerCell(r) {
    const ex = r.exporter, out = [];
    if (r.sellers != null) out.push(`<b>${num(r.sellers)}</b> seller${r.sellers === 1 ? '' : 's'}`);
    if (ex) out.push(`<span class="tsub">${ex.country ? cflag(ex.country) + ' ' : ''}${esc(ex.seller || 'one seller')}` +
      `${ex.stock === 'local' ? ' · stock in Amazon\u2019s warehouse' : ex.stock === 'abroad' ? ' · ships from abroad' : ''}${ex.days != null ? ' · ' + ex.days + (ex.days === 1 ? ' day' : ' days') : ''}</span>`);
    if (r.local_rivals != null) out.push(`<span class="tsub">${num(r.local_rivals)} local rival${r.local_rivals === 1 ? '' : 's'} on page 1</span>`);
    return `<td class="adv">${out.join('') || '<span class="muted small">not read</span>'}</td>`;
  }
  function demandCell(r) {
    return `<td>${r.demand_floor ? `<b>≥ ${compact(r.demand_floor)}/mo</b><span class="tsub">page 1, Amazon’s “bought” badges</span>` : '<span class="muted small">no badges on page 1</span>'}` +
      `${r.phrase ? `<span class="tsub">“${esc(r.phrase)}”</span>` : ''}</td>`;
  }
  function moneyCell(r) {
    const tip = `The most you can pay your supplier per piece and still keep 20% of the price: Amazon fees for ${r.size_known ? 'this size' : 'a small parcel (size unknown)'}, sea freight, duty, ads and returns.`;
    const price = r.price != null ? `<b>${curMoney(r.price, MK_CUR[r.market], 2)}</b><span class="tsub">${esc(r.price_basis || '')}</span>${fxSub(r.price, r.market)}`
      : '<span class="muted small">no price read</span>';
    const cost = r.max_cogs_20 ? `<span class="tsub buy" title="${esc(tip)}">pay ≤ ${esc(costMoney(r.max_cogs_20))}${r.size_known ? '' : ' (size assumed)'}</span>`
      : r.price != null ? `<span class="tsub" title="${esc(tip)}">no 20% margin at this price</span>` : '';
    return `<td>${price}${cost}</td>`;
  }
  function actions(r) {
    const url = r.listing_status === 'ok' ? site(r.market) + '/dp/' + r.asin
      : r.phrase ? site(r.market) + '/s?k=' + encodeURIComponent(r.phrase).replace(/%20/g, '+') : '';
    return [url ? `<button class="btn slim" data-open="${esc(url)}" title="Open it on Amazon ${esc(MK[r.market])}">${icon('external')}Amazon</button>` : '',
      r.phrase ? `<a class="btn slim" href="#/k/${r.market}/${encodeURIComponent(r.phrase)}" title="Keyword report for “${esc(r.phrase)}”">Report</a>` : '',
      shortlistBtn(`data-board="${r.id}"`),
      LONE.has(r.type) ? (r.watch_id ? `<button class="btn slim" data-unwatch="${r.watch_id}" title="Watching for a second seller: press to stop">${icon('eye')}Watching</button>`
        : `<button class="btn slim" data-watch="${r.id}" title="Read it every day and tell me when a second seller appears">${icon('eye')}Watch</button>`) : ''].join('');
  }
  function row(r) {
    const pills = (r.brand ? `<span class="pill line">${esc(r.brand)}</span>` : '') +
      r.flags.map(f => `<span class="pill warn" title="${esc(f.why)}">${icon('warn')}${esc(f.label)}</span>`).join('');
    const url = site(r.market) + '/dp/' + r.asin;
    return `<tr>${prodCell({image: r.image, url, title: r.title, category: r.asin, flags: []}, pills, actions(r))}
      <td>${mkFlag(r.market, true)}<span class="tsub">checked ${ago(r.ts)}</span></td>${proofCell(r)}${gapCell(r)}${sellerCell(r)}${demandCell(r)}${moneyCell(r)}</tr>`;
  }

  function render() {
    if (!D) return;
    const mk = segVal($('gMk')), all = $('gAll').checked, warn = $('gWarn').checked;
    const inMk = D.rows.filter(r => !mk || r.market === mk);
    const rows = inMk.filter(r => (all || !['crowded', 'unread'].includes(r.type)) && (warn || !r.flags.length));
    if (sortBy) {
      const k = SORT[sortBy];
      rows.sort((a, b) => { const x = k(a), y = k(b); return (x < y ? -1 : x > y ? 1 : 0) * sortDir; });
    }
    const th = (key, label, tip, cls = '') => `<th scope="col" class="${cls}"${tip ? ` title="${esc(tip)}"` : ''} aria-sort="${sortBy === key ? (sortDir > 0 ? 'ascending' : 'descending') : 'none'}">` +
      `<button type="button" class="thsort" data-sort="${key}">${esc(label)}${sortBy === key ? (sortDir > 0 ? ' ▲' : ' ▼') : ''}</button></th>`;
    const last = Math.max(0, ...(D.tasks || []).filter(t => t.kind === 'gaps').map(t => t.last_run_at || 0));
    $('gWhen').textContent = last ? 'Last automatic check ' + ago(last) : '';
    const sp = D.spapi || {};
    const head = `<div class="kpis">
      ${kpi('Proven products', num(D.proven), 'sell well in 2+ of the USA, UK, Germany')}
      ${kpi('Open gaps', num(inMk.filter(r => r.gap).length), mk ? 'in ' + esc(MK[mk]) : 'in Australia and the UAE')}
      ${kpi('Lone exporters watched', num(D.lone.length), D.lone.length ? 'read again every day' : 'press Watch on a lone exporter', 'adv')}
      ${kpi('Seller API', sp.saved ? 'On' : 'Off', sp.saved ? 'for ' + esc((sp.markets || []).map(m => MK_SHORT[m]).join(', ') || 'no market') : 'product pages are read instead', 'adv')}
      ${kpi('Automatic', D.auto ? 'On' : 'Off', D.auto ? 'a few products a day per market' : '<a href="#/settings">turn on in Settings</a>', 'adv')}
    </div>`;
    if (!D.rows.length) {
      $('gapsOut').innerHTML = head + `<div class="panel empty"><h2>No products checked yet</h2><p>Press <b>Check now</b>. It reads the Best Sellers of your
        categories in the USA, the UK and Germany, finds the products that sell well in at least two of them, and checks the first ones
        in Australia and the UAE: the same listing, the phrase shoppers type there and who sells it. ${D.proven ? num(D.proven) + ' proven products are waiting.' : ''}</p></div>` + loneHtml();
      return;
    }
    const hidden = inMk.length - rows.length;
    $('gapsOut').innerHTML = head + `<div class="panel"><div class="tablewrap"><table class="mtx gtable"><thead><tr>${th('title', 'Product', '', 'mtx-prod')}
      ${th('market', 'Market')}${th('proof', 'Proven in', 'Where it sells well, with the estimate and where it comes from')}
      ${th('gap', 'Gap there')}${th('sellers', 'Who sells it there', '', 'adv')}${th('demand', 'Demand there', 'What shoppers type there, and how much page 1 sold last month (a floor: Amazon’s badges say “50+”)')}
      ${th('cost', 'Price there · your cost', 'Sorted by the most you can pay your supplier and keep 20%')}</tr></thead><tbody>
      ${rows.map(row).join('') || '<tr><td colspan="7" class="empty">Nothing to show with these filters.</td></tr>'}</tbody></table></div>
      ${hidden ? `<p class="small" style="margin-top:8px">${num(hidden)} more hidden by the filters above (crowded, not read, or with warnings).</p>` : ''}</div>` + loneHtml();
  }
  function loneHtml() {
    if (!D.lone.length) return '';
    return `<div class="panel adv"><h3 style="margin-bottom:4px">Lone exporters you watch</h3><p class="small" style="margin-bottom:8px">Read again every day. When a
      second seller appears you get an alert on Home, and a Windows notification when the app is not in front.</p><div class="tablewrap"><table>
      <thead><tr><th>Product</th><th>Market</th><th class="r">Sellers now</th><th>Last read</th><th></th></tr></thead><tbody>
      ${D.lone.map(w => `<tr><td><a data-open="${esc(site(w.market) + '/dp/' + w.asin)}"><b>${esc(w.title)}</b></a><div class="sub">${esc(w.asin)}</div></td>
        <td>${mkFlag(w.market, true)}</td><td class="r">${w.sellers != null ? `<span class="pill ${w.sellers > 1 ? 'warn' : 'good'}">${num(w.sellers)}</span>` : '–'}</td>
        <td>${w.checked_at ? ago(w.checked_at) + (w.source === 'spapi' ? ' · seller API' : '') : '<span class="muted small">waiting for the first reading</span>'}</td>
        <td><button class="btn slim ghost" data-unwatch="${w.id}" title="Stop watching">${icon('x')}</button></td></tr>`).join('')}
      </tbody></table></div></div>`;
  }

  $('gapsOut').addEventListener('click', async e => {
    const s = e.target.closest('[data-sort]');
    if (s) {
      sortDir = sortBy === s.dataset.sort ? -sortDir : 1;
      sortBy = s.dataset.sort;
      render();
      return;
    }
    const b = e.target.closest('[data-board]'), w = e.target.closest('[data-watch]'), u = e.target.closest('[data-unwatch]');
    const r = D && (b || w) ? D.rows.find(x => x.id === +(b || w).dataset[b ? 'board' : 'watch']) : null;
    if (b && r) shortlist({title: r.title, market: r.market, asin: r.asin, kw: r.phrase || '', image: r.image || '',
      source: 'Gap Finder (' + (r.label || 'gap') + ')'}, b);
    if (w && r) {
      try { await api('/api/gaps/watch', {market: r.market, asin: r.asin, title: r.title}); toast('Watching it: you get an alert when a second seller appears', 4000); load(); }
      catch (err) { toast(err.message); }
    }
    if (u) { await api('/api/watch/remove', {id: +u.dataset.unwatch}).catch(err => toast(err.message)); load(); }
  });
  $('gExcel').addEventListener('click', () => download({kind: 'gaps'}));
  $('gRun').addEventListener('click', async () => {
    const btn = $('gRun'); if (btn.disabled) return;
    btn.disabled = true;
    $('gProg').innerHTML = busy('Starting…');
    try {
      const mk = segVal($('gMk'));
      const {job} = await api('/api/gaps/run', {markets: mk ? [mk] : []});
      const res = await waitJob(job, line => { $('gProg').innerHTML = busy(line); });
      D = {...D, ...res};
      render();
      toast((res.notes || []).join(' · ') || 'Done', 9000);
    } catch (err) { toast(err.message); }
    $('gProg').innerHTML = '';
    btn.disabled = false;
  });

  /* Settings: Gap Finder panel */
  let filled = false;
  onState(() => {
    const s = ST.settings || {};
    if (filled || s.gap_auto === undefined) return;
    filled = true;
    document.querySelectorAll('#gapPanel [data-set]').forEach(el => {
      const v = s[el.dataset.set];
      if (el.type === 'checkbox') el.checked = !!v; else el.value = v == null ? '' : v;
    });
    $('locGB').value = (s.locations || {}).GB || ''; $('locDE').value = (s.locations || {}).DE || '';
  });
  $('gapPanel').addEventListener('change', async e => {
    let body;
    if (e.target.id === 'locGB' || e.target.id === 'locDE') body = {locations: {GB: $('locGB').value, DE: $('locDE').value}};
    else {
      const el = e.target.closest('[data-set]'); if (!el) return;
      body = {[el.dataset.set]: el.type === 'checkbox' ? el.checked : el.value};
    }
    try { const r = await api('/api/settings', body); ST.settings = r.settings; toast('Saved', 1500); }
    catch (err) { toast(err.message); filled = false; }
  });

  /* Settings: SP-API panel. The keys are never sent back to the page. */
  async function spState() {
    try {
      const s = await api('/api/spapi');
      const regions = s.regions.filter(r => r.saved).map(r => esc(r.name)).join(', ');
      $('spState').innerHTML = s.saved ? `<span class="pill good">${icon('lock')}Keys saved</span> ${regions || 'no region'}${s.error ? ' · last error: ' + esc(s.error) : ''}`
        : '<span class="pill weak">Not set up</span> The Gap Finder reads product pages instead.';
      $('spForget').disabled = !s.saved; $('spTest').disabled = !s.saved;
    } catch (e) { $('spState').innerHTML = failPill(e.message); }
  }
  window.addEventListener('hashchange', () => { if (/^#\/settings/.test(location.hash)) spState(); });
  if (/^#\/settings/.test(location.hash)) spState();
  $('spSave').addEventListener('click', async () => {
    const body = {client_id: $('spId').value.trim(), client_secret: $('spSecret').value.trim(),
      refresh: {NA: $('spNA').value.trim(), EU: $('spEU').value.trim(), FE: $('spFE').value.trim()}};
    try {
      await api('/api/spapi', body);
      ['spSecret', 'spNA', 'spEU', 'spFE'].forEach(id => { $(id).value = ''; });
      toast('Keys saved, encrypted for your Windows user', 3000); spState();
    } catch (err) { toast(err.message); }
  });
  $('spForget').addEventListener('click', async () => {
    if (!confirm('Forget the SP-API keys saved on this PC?')) return;
    try { await api('/api/spapi', {forget: true}); toast('Keys forgotten', 2000); spState(); } catch (err) { toast(err.message); }
  });
  $('spTest').addEventListener('click', async () => {
    $('spTest').disabled = true;
    try {
      const {job} = await api('/api/spapi/test', {});
      const r = await waitJob(job, line => { $('spState').textContent = line; });
      $('spState').innerHTML = r.regions.map(x => x.ok ? `<span class="pill good">${icon('ok')}${esc(x.region)}</span> ${esc(x.marketplaces.join(', ') || 'no marketplaces')}`
        : `<span class="pill bad">${icon('x')}${esc(x.region)}</span> ${esc(x.error)}`).join('<br>');
    } catch (err) { toast(err.message); }
    $('spTest').disabled = false;
  });
})();
