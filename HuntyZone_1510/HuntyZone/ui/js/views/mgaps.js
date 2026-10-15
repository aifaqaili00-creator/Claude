'use strict';
/* Market Gaps: where Amazon Australia and the UAE leave room. A SmartScout export is the baseline (brands, sellers,
   who dominates each niche); Refresh reads Amazon itself to say whether each gap is still open. */
(() => {
  view('mgaps', {title: 'Market Gaps', sub: 'SmartScout baseline · checked live on Amazon Australia and the UAE',
    onShow: p => { if (p[0] && ['AU', 'AE'].includes(p[0])) mk = p[0]; setSeg($('mgMk'), mk); load(); }});
  const TONE = {global_store: 'good', amazon_then_gap: 'good', thin: 'good', us_proven: 'accent', crowded: 'weak'};
  const VTONE = {open: 'good', amazon_only: 'good', few: 'warn', closing: 'warn', closed: 'bad', unread: 'weak'};
  const VICON = {good: 'ok', warn: 'warn', bad: 'x', weak: 'dot', accent: 'dot'};
  const WHO = [
    {key: 'amazon', label: 'Amazon itself', color: 'var(--s-demand)'},
    {key: 'global_store', label: 'Global Store (imports)', color: 'var(--s-overseas)'},
    {key: 'reseller', label: 'Import resellers', color: 'var(--s-5)'},
    {key: 'seller', label: 'Brands & sellers', color: 'var(--s-local)'},
    {key: 'unknown', label: 'Not named', color: 'var(--s-other)'},
  ];
  const BB = [
    {key: 'amazon', label: 'Amazon', color: 'var(--s-demand)'}, {key: 'store', label: 'Global Store', color: 'var(--s-overseas)'},
    {key: 'abroad', label: 'From abroad', color: 'var(--s-5)'}, {key: 'local', label: 'Local seller', color: 'var(--s-local)'},
    {key: 'unknown', label: 'Unknown', color: 'var(--s-other)'},
  ];
  const SORT = {score: r => -r.score, name: r => r.name.toLowerCase(), revenue: r => -r.revenue, cross: r => -r.cross,
    amazon: r => -r.shares.amazon, price: r => r.price ?? Infinity, reviews: r => r.reviews ?? Infinity,
    live: r => r.live ? ({open: 0, amazon_only: 1, few: 2, closing: 3, closed: 4, unread: 5}[r.live.verdict] ?? 6) : 7};
  let mk = 'AU', tab = 'niches', D = null, sortBy = 'score', sortDir = 1, limit = 100;
  const open = new Set();
  const cur = () => MK_CUR[mk];
  const site = () => (D && D.site) || (ST.markets[mk] || {}).site || '';
  const m0 = v => v == null ? '–' : curMoney(v, cur(), 0);
  const kpi = (label, v, sub, cls = '') => `<div class="kpi ${cls}"><span class="eyebrow">${esc(label)}</span><span class="v">${v}</span><span class="s">${sub}</span></div>`;
  const day = t => t ? new Date(t * 1000).toLocaleDateString(undefined, {day: 'numeric', month: 'short', year: 'numeric'}) : '';
  const share = s => s == null ? '<span class="muted small">–</span>' : `<span class="ishare"><i style="width:${Math.round(s * 100)}%"></i></span> ${Math.round(s * 100)}%`;
  const parts = (obj, defs) => defs.map(d => ({...d, n: (obj || {})[d.key] || 0}));
  const searchUrl = q => site() + '/s?k=' + encodeURIComponent(q || '').replace(/%20/g, '+');

  seg($('mgMk'), v => { mk = v; limit = 100; open.clear(); load(); });
  ['mgType', 'mgLive', 'mgRisky', 'mgAll'].forEach(id => $(id).addEventListener('change', () => { limit = 100; render(); }));
  $('mgMin').addEventListener('input', () => { limit = 100; render(); });
  $('mgFind').addEventListener('input', () => { limit = 100; render(); });
  $('mgTabs').addEventListener('click', e => {
    const b = e.target.closest('[data-tab]'); if (!b) return;
    tab = b.dataset.tab;
    $('mgTabs').querySelectorAll('[data-tab]').forEach(x => x.setAttribute('aria-selected', String(x === b)));
    render();
  });

  async function load() {
    $('mgOut').innerHTML = '<div class="panel">' + busy('Loading…') + '</div>';
    try { D = await api('/api/mgaps?market=' + mk); } catch (e) { $('mgOut').innerHTML = failPill(e.message); return; }
    const sel = $('mgType'), was = sel.value;
    sel.innerHTML = '<option value="">Every gap</option>' + D.types.filter(t => t.type !== 'crowded')
      .map(t => `<option value="${t.type}">${esc(t.label)}</option>`).join('');
    sel.value = was;
    render();
  }

  function head() {
    const k = D.kpis, b = D.baseline;
    $('mgWhen').textContent = (b.as_of ? 'Baseline: SmartScout, ' + day(b.as_of) : 'No baseline yet') +
      (D.last_check ? ' · last live check ' + ago(D.last_check) : '');
    return `<div class="kpis">
      ${kpi('Niches scored', num(k.niches), b.brands ? num(b.brands) + ' brands in the export' : 'import the SmartScout export')}
      ${kpi('Gaps in the baseline', num(k.gaps), 'shipped in, Amazon then a gap, few brands, or bigger in the USA')}
      ${kpi('Shipped in from abroad', k.global_store != null ? pct(k.global_store + (k.resellers || 0)) : '–', 'of the market: Global Store ' + pct(k.global_store) + ', resellers ' + pct(k.resellers))}
      ${kpi('Open now', num(k.open) + '<span class="small muted"> of ' + num(k.checked) + '</span>', k.checked ? 'niches checked live on Amazon ' + esc(MK[mk]) : 'press <b>Refresh</b> to check them live')}
      ${kpi('Automatic', D.auto ? 'On' : 'Off', D.auto ? 'a few niches a day' + (D.auto_last ? ' · last ' + ago(D.auto_last) : '') : '<a href="#/settings">turn on in Settings</a>', 'adv')}
    </div>`;
  }

  function render() {
    if (!D) return;
    $('mgFilters').hidden = tab !== 'niches';
    if (!D.niches.length && tab !== 'live') { $('mgOut').innerHTML = head() + importPanel(); return; }
    const body = tab === 'niches' ? niches() : tab === 'live' ? live() : tab === 'missing' ? missing() : sellers();
    $('mgOut').innerHTML = head() + body;
  }

  function importPanel() {
    return `<div class="panel empty"><h2>No SmartScout baseline for ${esc(MK[mk])} yet</h2>
      <p>Import your SmartScout exports (brands and sellers of Australia, the UAE and the USA). They are read once and kept with their date;
      after that the app keeps the picture fresh by reading Amazon itself.</p>
      <div class="row" style="justify-content:center;margin-top:10px"><input id="mgFolder" value="${esc(D.folder || '')}" placeholder="Folder with the SmartScout .xlsx files" style="width:min(520px,100%)" aria-label="Folder">
      <button class="btn primary" data-import>${icon('upload')}Import this folder</button></div>
      ${D.baseline.files.length ? '<p class="small" style="margin-top:8px">Imported so far: ' + D.baseline.files.map(f => esc(f.file) + ' (' + esc(MK[f.market] || f.market) + ')').join(', ') + '</p>' : ''}</div>`;
  }

  /* ---------- Niches ---------- */
  function filtered() {
    const type = $('mgType').value, lv = $('mgLive').value, risky = $('mgRisky').checked, all = $('mgAll').checked;
    const min = +$('mgMin').value || 0, q = $('mgFind').value.trim().toLowerCase();
    let rows = D.niches.filter(r => (risky || !r.risky) && (all || type || r.gap.gap) && (!type || r.gap.type === type || (r.gap.also || []).some(a => a.type === type))
      && r.revenue >= min && (!lv || (lv === 'open' ? r.live && r.live.gap : lv === 'checked' ? !!r.live : !r.live))
      && (!q || (r.name + ' ' + (r.category || '') + ' ' + r.top.map(t => t.brand).join(' ')).toLowerCase().includes(q)));
    const k = SORT[sortBy];
    rows = rows.slice().sort((a, b) => { const x = k(a), y = k(b); return (x < y ? -1 : x > y ? 1 : 0) * sortDir; });
    return rows;
  }
  const th = (key, label, tip, cls = '') => `<th scope="col" class="${cls}"${tip ? ` title="${esc(tip)}"` : ''} aria-sort="${sortBy === key ? (sortDir > 0 ? 'ascending' : 'descending') : 'none'}">` +
    `<button type="button" class="thsort" data-sort="${key}">${esc(label)}${sortBy === key ? (sortDir > 0 ? ' ▲' : ' ▼') : ''}</button></th>`;

  function niches() {
    const rows = filtered();
    return `<div class="panel"><div class="tablewrap"><table class="mtx mgtable"><thead><tr>${th('name', 'Niche', '', 'mtx-prod')}
      ${th('score', 'Score', 'Demand 30, openness 35, reviews to beat 15, price 10, growth 10')}
      ${th('cross', 'Gap (baseline)', 'From the SmartScout export')}${th('revenue', 'Revenue / month', 'SmartScout estimate, ' + cur())}
      ${th('amazon', 'Who sells most of it', 'Each brand counted with its dominant seller')}${th('price', 'Price · reviews', 'Revenue-weighted typical price, and reviews per listing (the barrier)', 'adv')}
      ${th('live', 'Live on Amazon now')}</tr></thead><tbody>
      ${rows.slice(0, limit).map(row).join('') || '<tr><td colspan="7" class="empty">Nothing to show with these filters.</td></tr>'}</tbody></table></div>
      ${rows.length > limit ? `<div class="row" style="justify-content:center;margin-top:8px"><button class="btn" data-more-rows>Show ${num(Math.min(100, rows.length - limit))} more of ${num(rows.length - limit)}</button></div>` : ''}
      <p class="small muted" style="margin-top:8px">${num(rows.length)} niche${rows.length === 1 ? '' : 's'} shown of ${num(D.niches.length)}. SmartScout files each brand's revenue under its main niche, so a big brand can make a niche look bigger than it is: check it live before deciding.</p></div>`;
  }

  function row(r) {
    const L = r.live, g = r.gap, phrase = (L && L.phrase) || r.name.toLowerCase().replace(/\s*&\s*/g, ' ');
    const pills = `<span class="pill line">${esc(r.category || 'no category')}</span>` + (r.risky ? `<span class="pill warn" title="Phones, laptops, books, renewed and similar niches are hard to enter">${icon('warn')}${esc(r.risk_why)}</span>` : '');
    const acts = `<button class="btn slim" data-open="${esc(searchUrl(phrase))}" title="Search it on Amazon ${esc(MK[mk])}">${icon('external')}Amazon</button>` +
      (L && L.phrase ? `<a class="btn slim" href="#/k/${mk}/${encodeURIComponent(L.phrase)}" title="Keyword report for “${esc(L.phrase)}”">Report</a>` : '') +
      `<button class="btn slim" data-check="${esc(r.key)}" title="Read page 1 and the top buy boxes on Amazon ${esc(MK[mk])} now">${icon('refresh')}Check now</button>` +
      `<button class="btn slim" data-watch="${esc(r.key)}" title="Check this phrase every day (Watchlist)">${icon('eye')}Watch</button>` +
      `<button class="btn slim" data-board="${esc(r.key)}" title="Add to your product board">${icon('board')}Board</button>` +
      moreBtn(r.key, open.has(r.key));
    const also = (g.also || []).map(a => `<span class="pill line" title="${esc(a.why)}">${esc(a.label)}</span>`).join(' ');
    const sc = r.parts, tip = `Demand ${pct(sc.demand)} · openness ${pct(sc.open)} · reviews ${pct(sc.barrier)} · price ${pct(sc.price)} · growth ${pct(sc.momentum)}`;
    const who = parts(r.shares, WHO);
    const top = r.top[0];
    return `<tr>${prodCell({url: searchUrl(phrase), title: r.name, category: '', flags: r.flags}, pills, acts)}
      <td title="${esc(tip)}"><b class="tnum">${r.score}</b><span class="tsub">${r.us_ratio ? 'USA ' + num(r.us_ratio, 1) + '×' : ''}</span></td>
      <td class="gwhy"><span class="pill ${TONE[g.type] || 'weak'}" title="${esc(g.means)}">${icon(VICON[TONE[g.type]] || 'dot')}${esc(g.label)}</span>
        <div class="small">${esc(g.why)}</div>${also ? '<div style="margin-top:4px">' + also + '</div>' : ''}</td>
      <td><b>${m0(r.revenue)}</b><span class="tsub">${num(r.brands)} brand${r.brands === 1 ? '' : 's'} · ${r.avg_sellers != null ? num(r.avg_sellers, 1) + ' sellers each' : ''}</span>
        ${r.growth != null ? `<span class="tsub">${r.growth >= 0 ? '+' : ''}${pct(r.growth)} in 12 months</span>` : ''}</td>
      <td style="min-width:170px">${originBar(who.map(p => ({...p, label: p.label + ' (%)', n: Math.round(p.n * 100)})), 100, 8)}<span class="tsub">${who.filter(p => p.n >= 0.05).map(p => esc(p.label) + ' ' + pct(p.n)).join(' · ')}</span>
        ${top ? `<span class="tsub">Top: <b>${esc(top.brand)}</b> ${pct(top.share)}${top.seller ? ' · ' + esc(top.seller) : ''}</span>` : ''}</td>
      <td class="adv">${r.price != null ? curMoney(r.price, cur(), 0) + fxSub(r.price, mk) : '–'}<span class="tsub">${r.reviews != null ? num(r.reviews) + ' reviews / listing' : ''}</span></td>
      <td>${liveCell(r)}</td></tr>${open.has(r.key) ? detail(r) : ''}`;
  }

  function liveCell(r) {
    const L = r.live;
    if (!L) return '<span class="muted small">not checked yet</span>';
    const t = VTONE[L.verdict] || 'weak';
    const bb = L.buyboxes && Object.values(L.buyboxes).some(Boolean) ? originBar(parts(L.buyboxes, BB), Object.values(L.buyboxes).reduce((a, b) => a + b, 0), 6) : '';
    const hist = (L.history || []).length > 1 ? `<span class="tsub">before: ${L.history.slice(0, -1).map(h => esc((D.verdicts.find(v => v.verdict === h.verdict) || {}).label || h.verdict)).join(' → ')}</span>` : '';
    return `<span class="pill ${t}" title="${esc((D.verdicts.find(v => v.verdict === L.verdict) || {}).means || '')}">${icon(VICON[t])}${esc(L.label)}</span>
      <div class="small">${esc(L.why || '')}</div>${bb ? '<div style="margin-top:4px;max-width:180px">' + bb + '</div>' : ''}
      <span class="tsub">“${esc(L.phrase || '')}” · ${ago(L.ts)}${L.conf ? ' · ' + esc(L.conf) + ' confidence' : ''}${L.price_med != null ? ' · typical ' + esc(curMoney(L.price_med, cur(), 0)) : ''}</span>${hist}
      ${L.stopped ? `<span class="tsub">stopped early: ${esc(L.stopped)}</span>` : ''}`;
  }

  function detail(r) {
    const L = r.live || {};
    const base = r.top.map(t => `<tr><td><b>${esc(t.brand)}</b><div class="sub">${esc(t.seller || 'seller not named')}</div></td><td>${share(t.share)}</td>
      <td class="r">${m0(t.revenue)}</td><td class="r">${t.price != null ? curMoney(t.price, cur(), 0) : '–'}</td><td class="r">${t.sellers != null ? num(t.sellers, 1) : '–'}</td></tr>`).join('');
    const liveB = (L.brands || []).map(b => `<tr><td><b>${esc(b.brand)}</b>${b.from === 'title' ? ' <span class="sub">from the title</span>' : ''}</td><td>${share(b.share)}</td><td class="r">${num(b.listings)}</td></tr>`).join('');
    const tops = (L.top || []).map(t => `<tr><td><a data-open="${esc(site() + '/dp/' + t.asin)}">${esc((t.title || t.asin || '').slice(0, 90))}</a></td>
      <td>${t.origin === 'local' ? '<span class="pill good">local</span>' : t.origin === 'overseas' ? '<span class="pill warn">from abroad</span>' : '<span class="pill weak">unknown</span>'}${t.days != null ? ' <span class="sub">' + t.days + ' d</span>' : ''}</td>
      <td>${t.offer ? esc(t.offer.seller || (t.offer.amazon ? 'Amazon' : 'a seller')) + (t.offer.country ? ' ' + cflag(t.offer.country, '') : '') : '<span class="muted small">not read</span>'}</td>
      <td class="r">${t.bought ? num(t.bought) + '+' : '–'}</td><td class="r">${t.price != null ? curMoney(t.price, cur(), 0) : '–'}</td></tr>`).join('');
    return `<tr class="mgdetail"><td colspan="7"><div class="grid2" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px">
      <div><h4>Top brands in the baseline</h4><div class="tablewrap"><table class="itable"><thead><tr><th>Brand · seller</th><th>Share</th><th class="r">a month</th><th class="r">Price</th><th class="r">Sellers</th></tr></thead><tbody>${base}</tbody></table></div></div>
      <div><h4>Brands on page 1 now</h4>${liveB ? `<div class="tablewrap"><table class="itable"><thead><tr><th>Brand</th><th>Share of sales</th><th class="r">Listings</th></tr></thead><tbody>${liveB}</tbody></table></div>` : '<p class="small muted">Press <b>Check now</b> to read page 1.</p>'}</div>
      ${tops ? `<div style="grid-column:1/-1"><h4>Top listings now</h4><div class="tablewrap"><table class="itable"><thead><tr><th>Listing</th><th>Ships</th><th>Buy box</th><th class="r">Bought</th><th class="r">Price</th></tr></thead><tbody>${tops}</tbody></table></div></div>` : ''}
    </div></td></tr>`;
  }

  /* ---------- Best Sellers read now ---------- */
  function live() {
    const cats = D.categories || [];
    if (!cats.length) return `<div class="panel empty"><h2>No Best Sellers read yet</h2><p>Press <b>Refresh AU &amp; UAE</b>. It reads the Best Sellers of your categories
      on Amazon ${esc(MK[mk])} (the ones picked in Product ideas, else Home, Kitchen, Sports, Pets, Office, Garden and Home Improvement) and who holds the top buy boxes:
      the app's own, always fresh, version of the SmartScout brand table.</p></div>`;
    return cats.map(c => {
      const d = c.data, g = d.gap || {}, t = TONE[g.type] || 'weak';
      const bbTotal = Object.values(d.buyboxes || {}).reduce((a, b) => a + b, 0);
      return `<div class="panel"><div class="row" style="justify-content:space-between"><h3>${esc(c.category)}</h3><span class="small muted">read ${ago(c.ts)}${c.status !== 'ok' ? ' · ' + esc(c.status) : ''}</span></div>
        <div class="row" style="gap:16px;align-items:flex-start;margin:6px 0 10px">
          <div><span class="pill ${t}">${icon(VICON[t])}${esc(g.label || '')}</span><div class="small">${esc(g.why || '')}</div></div>
          <div class="small">${num(d.n_brands)} brands in the top ${num(d.n)} · top 3 take ${pct(d.top3)}${d.units_total ? ' · about ' + compact(d.units_total) + ' sold a month' : ' · shares by rank (no sales curve yet)'}${d.price_med != null ? ' · typical ' + esc(curMoney(d.price_med, cur(), 0)) : ''}${d.reviews_med != null ? ' · ' + num(d.reviews_med) + ' reviews (median)' : ''}</div>
          ${bbTotal ? `<div style="min-width:200px"><div class="small">Top ${bbTotal} buy boxes</div>${originBar(parts(d.buyboxes, BB), bbTotal, 8)}${legend(parts(d.buyboxes, BB))}</div>` : ''}
        </div>
        <div class="grid2" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px">
        <div class="tablewrap"><table class="itable"><thead><tr><th>Brand</th><th>Share</th><th class="r">Listings</th></tr></thead><tbody>
          ${(d.brands || []).slice(0, 8).map(b => `<tr><td><b>${esc(b.brand)}</b>${b.from === 'title' ? ' <span class="sub">from the title</span>' : ''}</td><td>${share(b.share)}</td><td class="r">${num(b.listings)}</td></tr>`).join('')}</tbody></table></div>
        <div class="tablewrap"><table class="itable"><thead><tr><th>#</th><th>Best seller</th><th>Buy box</th><th class="r">Price</th></tr></thead><tbody>
          ${(d.top || []).slice(0, 8).map(x => `<tr><td class="tnum">${x.rank}</td><td><a data-open="${esc(site() + '/dp/' + x.asin)}">${esc((x.title || x.asin).slice(0, 70))}</a></td>
            <td>${x.offer ? esc(x.offer.seller || (x.offer.amazon ? 'Amazon' : 'a seller')) + (x.offer.country ? ' ' + cflag(x.offer.country, '') : '') + (x.offer.stock === 'abroad' ? ' <span class="pill warn">abroad</span>' : '') : '<span class="muted small">–</span>'}</td>
            <td class="r">${x.price != null ? curMoney(x.price, cur(), 0) : '–'}</td></tr>`).join('')}</tbody></table></div></div></div>`;
    }).join('');
  }

  /* ---------- Brands missing here ---------- */
  function missing() {
    const rows = D.missing || [];
    if (!rows.length) return `<div class="panel empty"><h2>Nothing to compare</h2><p>Import the SmartScout US brands export too: brands selling over US$200K a month in the USA that are not in the ${esc(MK[mk])} export show here.</p></div>`;
    const q = $('mgFind').value.trim().toLowerCase();
    const list = rows.filter(b => !q || (b.brand + ' ' + (b.subcategory || '') + ' ' + (b.category || '')).toLowerCase().includes(q));
    return `<div class="panel"><p class="small" style="margin-bottom:8px">Brands selling at least US$200K a month in the USA that are not among the ${num(D.baseline.brands || 0)} brands of the
      ${esc(MK[mk])} export. Proven demand with nobody bringing it in: the brand itself, or a similar product under your own brand.</p>
      <div class="tablewrap"><table class="itable"><thead><tr><th>Brand</th><th>Niche (USA)</th><th class="r">USA a month</th><th class="r">USA price</th><th>Sold in the USA by</th><th></th></tr></thead><tbody>
      ${list.map(b => `<tr><td><b>${esc(b.brand)}</b>${b.risk_why ? ` <span class="pill warn">${esc(b.risk_why)}</span>` : ''}</td><td>${esc(b.subcategory || '–')}<div class="sub">${esc(b.category || '')}</div></td>
        <td class="r">${curMoney(b.us_revenue, 'USD', 0)}</td><td class="r">${b.price != null ? curMoney(b.price, 'USD', 2) : '–'}</td>
        <td>${b.amazon_us ? '<span class="pill accent">Amazon itself</span>' : esc(b.us_seller || '–')}</td>
        <td><button class="btn slim" data-open="${esc(searchUrl(b.brand))}">${icon('external')}Search ${esc(MK_SHORT[mk] || mk)}</button>
          <button class="btn slim" data-open="${esc('https://www.amazon.com/s?k=' + encodeURIComponent(b.brand))}">${icon('external')}USA</button></td></tr>`).join('')}
      </tbody></table></div></div>`;
  }

  /* ---------- Top sellers ---------- */
  function sellers() {
    const s = D.sellers || {};
    if (!s.sellers) return `<div class="panel empty"><h2>No sellers export</h2><p>Import the SmartScout sellers export of ${esc(MK[mk])}.</p></div>`;
    const sh = parts(s.shares, WHO);
    return `<div class="panel"><div class="row" style="gap:24px;align-items:flex-start;margin-bottom:10px">
        <div><span class="eyebrow">Concentration</span><div><b>${num(s.half)}</b> seller${s.half === 1 ? '' : 's'} take half of the money · the top 10 take <b>${pct(s.top10)}</b></div>
          <div class="small muted">${num(s.sellers)} sellers in the export · SmartScout, ${day(D.baseline.sellers_as_of)}</div></div>
        <div style="min-width:260px;flex:1">${originBar(sh.map(p => ({...p, label: p.label + ' (%)', n: Math.round(p.n * 100)})), 100, 10)}${legend(sh.map(p => ({...p, n: Math.round(p.n * 100), note: '%'})))}</div></div>
      <div class="tablewrap"><table class="itable"><thead><tr><th>Seller</th><th>Who</th><th>Share</th><th class="r">a month</th><th>Main niche</th><th class="r">Brands</th><th class="r">FBA</th></tr></thead><tbody>
      ${s.top.map(x => `<tr><td>${x.store ? cflag(x.store, '') + ' ' : ''}<b>${esc(x.name || 'not named')}</b>${x.seller_id ? `<div class="sub"><a data-open="${esc(site() + '/sp?seller=' + x.seller_id)}">${esc(x.seller_id)}</a></div>` : ''}</td>
        <td>${esc((WHO.find(w => w.key === x.role) || {}).label || x.role)}</td><td>${share(x.share)}</td><td class="r">${m0(x.revenue)}</td>
        <td>${esc(x.subcategory || '–')}</td><td class="r">${num(x.brands)}</td><td class="r">${x.pct_fba != null ? pct(x.pct_fba) : '–'}</td></tr>`).join('')}
      </tbody></table></div></div>`;
  }

  /* ---------- actions ---------- */
  const niche = key => D && D.niches.find(r => r.key === key);
  async function runJob(path, body, label) {
    const btn = $('mgRun'); if (btn.disabled) return;
    btn.disabled = true;
    $('mgProg').innerHTML = busy(label);
    try {
      const {job} = await api(path, body);
      const res = await waitJob(job, line => { $('mgProg').innerHTML = busy(line); });
      if (res.niches && res.market === mk) { D = res; render(); } else await load();
      toast((res.notes || []).join(' · ') || 'Done', 9000);
    } catch (err) { toast(err.message); }
    $('mgProg').innerHTML = '';
    btn.disabled = false;
  }
  async function importFolder(folder) {
    if (!folder) return;
    $('mgProg').innerHTML = busy('Importing…');
    try {
      const {job} = await api('/api/mgaps/import', {folder});
      const res = await waitJob(job, line => { $('mgProg').innerHTML = busy(line); });
      const ok = res.files.filter(f => f.status === 'imported'), bad = res.files.filter(f => f.status === 'error');
      toast(ok.length ? `Imported ${ok.map(f => f.file + ' (' + num(f.rows) + ' rows)').join(', ')}` : bad.length ? bad.map(f => f.file + ': ' + f.error).join(' · ')
        : res.files.length ? 'These files were imported before.' : 'No SmartScout export in that folder.', 10000);
      await load();
    } catch (err) { toast(err.message); }
    $('mgProg').innerHTML = '';
  }
  $('mgImport').addEventListener('click', () => {
    const f = prompt('Folder that holds the SmartScout exports (.xlsx):', (D && D.folder) || '');
    if (f) importFolder(f.trim());
  });
  $('mgRun').addEventListener('click', () => runJob('/api/mgaps/refresh', {markets: []}, 'Starting…'));
  $('mgExcel').addEventListener('click', () => download({kind: 'mgaps', market: mk}));
  $('mgOut').addEventListener('click', async e => {
    const s = e.target.closest('[data-sort]');
    if (s) { sortDir = sortBy === s.dataset.sort ? -sortDir : 1; sortBy = s.dataset.sort; render(); return; }
    if (e.target.closest('[data-more-rows]')) { limit += 100; render(); return; }
    if (e.target.closest('[data-import]')) { importFolder(($('mgFolder') || {}).value || ''); return; }
    const m = e.target.closest('[data-more]');
    if (m) { open.has(m.dataset.more) ? open.delete(m.dataset.more) : open.add(m.dataset.more); render(); return; }
    const c = e.target.closest('[data-check]');
    if (c) { runJob('/api/mgaps/check', {market: mk, niches: [c.dataset.check]}, 'Checking ' + (niche(c.dataset.check) || {}).name + '…'); return; }
    const w = e.target.closest('[data-watch]'), b = e.target.closest('[data-board]');
    const r = niche((w || b || {dataset: {}}).dataset[w ? 'watch' : 'board']);
    if (!r) return;
    const phrase = (r.live && r.live.phrase) || r.name.toLowerCase().replace(/\s*&\s*/g, ' ');
    if (w) {
      try { await api('/api/watch', {kind: 'keyword', target: phrase, markets: [mk]}); toast('Watching “' + phrase + '” on Amazon ' + MK[mk] + ': checked every day', 4000); }
      catch (err) { toast(err.message); }
    }
    if (b) addToBoard({title: r.name, market: mk, kw: phrase, asin: '', image: ''});
  });
})();
