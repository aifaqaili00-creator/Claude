'use strict';
/* Market intelligence (like SmartScout, for Australia, the UAE and the USA): sellers, brands, category gaps, where
   sellers are based, related products, ad pressure and a listing scorecard. Built from what HuntyZone already read. */
(() => {
  const TABS = {
    sellers: ['Sellers', 'Every seller read on a product page in the last 90 days. A listing\u2019s sales go to its buy-box seller; estimates come from Amazon\u2019s \u201cbought in past month\u201d badges (a floor, shown as a range) or the sales-rank curve. A seller whose profile page was not read has no country yet.'],
    brands: ['Brands', 'Brands on page 1 of the keywords you searched: their share of page-1 sales (from the badges), growth against a search about a month older, and whether Amazon itself sells there. A brand marked \u201cfrom the title\u201d is the title\u2019s first word, because its product page was not read.'],
    categories: ['Category gaps', 'The Gap Finder\u2019s proven products (Best Sellers in the USA, UK and Germany) per category: how many are open in Australia or the UAE, and whether the rest are imported, sold by Amazon only, or sold locally. Not-read checks are counted apart.'],
    origins: ['Seller origins', 'Where sellers are based, from their Amazon profile pages: for every seller read in the market, or for one niche\u2019s best page-1 listings.'],
    related: ['Related products', 'From the \u201ccustomers also bought / also viewed\u201d and \u201cfrequently bought together\u201d carousels on product pages: products like it are competitors, products bought with it but unlike it are add-on or bundle ideas.'],
    ads: ['Ad watch', 'How hard brands advertise on each keyword: the share of the first 20 places that are ads, a brand banner at the top, video ads and how many brands advertise, as one score from 0 to 100. Searches saved before this version only show the sponsored listings.'],
    scorecard: ['Listing scorecard', 'Your listing against the top 10 of its keyword: title, bullet points, pictures, video, A+ content, rating, reviews and price, with what to fix first.'],
  };
  let tab = 'sellers', mk = 'AU', kwSel = '', relAsin = '', D = null;
  view('intel', {title: 'Market intelligence', sub: 'Sellers, brands, categories, ads and listings in your markets',
    onShow: p => {                                 // #/intel/<tab>/<market>/<niche or ASIN>: every step has its own address
      if (p[0] && TABS[p[0]]) tab = p[0];
      if (p[1] && MK[p[1]]) mk = p[1];
      if (tab === 'related') relAsin = p[2] || ''; else kwSel = p[2] || '';
      drawTabs(); load();
    }});
  const to = (t, m, x) => step('intel', t, m, x);
  seg($('intelMk'), v => to(tab, v));
  $('iTabs').addEventListener('click', e => {
    const b = e.target.closest('[data-tab]'); if (!b) return;
    to(b.dataset.tab, mk);
  });
  function drawTabs() {
    $('iTabs').querySelectorAll('[data-tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
    setSeg($('intelMk'), mk);
    $('iMeans').innerHTML = explain(esc(TABS[tab][1]));
  }
  const out = () => $('intelOut');
  const est = u => u && u.v != null ? `<b>~${compact(u.v)}</b>/mo <span class="sub">${compact(u.lo)}\u2013${compact(u.hi)} \u00b7 ${esc([].concat(u.src || []).join(', '))}</span>` : '<span class="muted small">not known</span>';
  const share = s => s == null ? '<span class="muted small">\u2013</span>' : `<span class="ishare"><i style="width:${Math.round(s * 100)}%"></i></span> ${Math.round(s * 100)}%`;
  const when = t => t ? new Date(t * 1000).toLocaleDateString(undefined, {day: 'numeric', month: 'short', year: '2-digit'}) : '\u2013';
  const site = () => (ST.markets[mk] || {}).site || '';
  const empty = (h, p) => `<div class="panel empty"><h2>${esc(h)}</h2><p>${p}</p></div>`;
  const kwPicker = (list, sel, allLabel) => `<label class="small">Niche <select id="iKw" aria-label="Niche"><option value="">${esc(allLabel)}</option>${(list || []).map(k => `<option value="${esc(k.kw)}" ${k.kw === sel ? 'selected' : ''}>${esc(k.display || k.kw)}</option>`).join('')}</select></label>`;

  let seq = 0;
  const STALE = {};
  async function load() {
    const my = ++seq;                              // an answer to an earlier click never replaces the newer page
    out().innerHTML = '<div class="panel">' + busy('Loading\u2026') + '</div>';
    const fresh = r => { if (my !== seq) throw STALE; return r; };
    const q = async (path, extra = {}) => fresh(await api(path + '?' + new URLSearchParams({market: mk, ...extra})));
    try {
      if (tab === 'sellers') { D = await q('/api/intel/sellers'); sellers(); }
      else if (tab === 'brands') { D = await q('/api/intel/brands', kwSel ? {kw: kwSel} : {}); if (!kwSel) brandsAll(); else brandsNiche(await q('/api/intel/brands')); }
      else if (tab === 'categories') { D = fresh(await api('/api/intel/categories')); categories(); }
      else if (tab === 'origins') { D = await q('/api/intel/origins', kwSel ? {kw: kwSel} : {}); origins(kwSel ? null : await q('/api/intel/brands')); }
      else if (tab === 'related') { D = await q('/api/intel/related', relAsin ? {asin: relAsin} : {}); related(relAsin ? await q('/api/intel/related') : D); }
      else if (tab === 'ads') { D = await q('/api/intel/ads', kwSel ? {kw: kwSel} : {}); kwSel ? adsNiche() : adsAll(); }
      else scorecardForm();
    } catch (e) {
      if (e === STALE) return;
      console.error(e);
      out().innerHTML = '<div class="panel">' + failPill(e.message || 'This view could not be drawn') + '</div>';
    }
  }

  /* ---------- Sellers ---------- */
  function sellers() {
    const rows = D.sellers;
    if (!rows.length) { out().innerHTML = empty('No sellers read yet', 'Open a keyword report and press <b>Read the top listings</b>, or let the automatic refresh read the product pages of your watched products. Each product page names its buy-box seller.'); return; }
    out().innerHTML = `<div class="panel"><div class="row" style="justify-content:space-between;margin-bottom:8px"><span class="small">${num(rows.length)} sellers from ${num(D.pages)} product pages \u00b7 ${num(D.profiles)} with a known country</span>
      <input type="search" id="iFind" placeholder="Find a seller or brand" aria-label="Find a seller or brand" style="width:220px"></div>
      <div class="tablewrap"><table class="itable"><thead><tr><th>Seller</th><th class="r">Listings</th><th class="r">Buy box</th><th>Brands</th><th>Ships</th><th>Sales a month</th><th>Share</th><th>First seen</th></tr></thead><tbody>
      ${rows.map(r => `<tr data-find="${esc(((r.name || '') + ' ' + r.brands.map(b => b.brand).join(' ')).toLowerCase())}"><td>${r.country ? cflag(r.country) + ' ' : ''}<b>${esc(r.name || r.seller_id || 'Unknown')}</b>${r.amazon ? ' <span class="pill accent">Amazon itself</span>' : ''}
          <div class="sub">${r.country ? esc(COUNTRY[r.country] || r.country) : 'country not read'}${r.rating_pct != null ? ' \u00b7 ' + Math.round(r.rating_pct) + '% positive' + (r.ratings ? ' (' + num(r.ratings) + ')' : '') : ''}${r.seller_id ? ` \u00b7 <a data-open="${esc(site() + '/sp?seller=' + r.seller_id)}">profile</a>` : ''}</div></td>
        <td class="r">${num(r.listings)}</td><td class="r">${num(r.buybox)}</td>
        <td>${r.brands.map(b => `<span class="pill line">${esc(b.brand)}${b.listings > 1 ? ' \u00b7 ' + b.listings : ''}</span>`).join(' ') || '<span class="muted small">\u2013</span>'}</td>
        <td>${esc(r.ships || '\u2013')}</td><td>${est(r.units)}</td><td>${share(r.share)}</td><td>${when(r.first_seen)}</td></tr>`).join('')}
      </tbody></table></div></div>`;
    $('iFind').addEventListener('input', e => {
      const t = e.target.value.trim().toLowerCase();
      out().querySelectorAll('tr[data-find]').forEach(tr => { tr.hidden = !!t && !tr.dataset.find.includes(t); });
    });
  }

  /* ---------- Brands ---------- */
  function brandsAll() {
    const rows = D.brands;
    if (!rows.length) { out().innerHTML = empty('No niches searched yet', 'Check a product (Check sellers) or watch a keyword: its page 1 fills this table.'); return; }
    out().innerHTML = `<div class="panel"><div class="row" style="justify-content:space-between;margin-bottom:8px">${kwPicker(D.keywords, kwSel, 'All ' + D.niches + ' niches')}<span class="small">Brands that appear in the most niches first</span></div>
      <div class="tablewrap"><table class="itable"><thead><tr><th>Brand</th><th class="r">Niches</th><th>Biggest shares</th><th class="r">Listings</th><th>Page-1 sales a month</th><th>Amazon</th></tr></thead><tbody>
      ${rows.map(b => `<tr><td><b>${esc(b.brand)}</b>${b.src === 'title' ? ' <span class="sub">from the title</span>' : ''}</td><td class="r">${num(b.n_niches)}</td>
        <td>${b.niches.map(n => `<button class="pill line" data-kw="${esc(n.kw)}">${esc(n.display)}${n.share != null ? ' \u00b7 ' + Math.round(n.share * 100) + '%' : ''}</button>`).join(' ')}</td>
        <td class="r">${num(b.listings)}</td><td>${b.units ? '\u2265 ' + compact(b.units) + ' <span class="sub">badges</span>' : '<span class="muted small">no badges</span>'}</td>
        <td>${b.amazon ? '<span class="pill warn">Amazon sells it</span>' : ''}</td></tr>`).join('')}
      </tbody></table></div></div>`;
  }
  function brandsNiche(all) {
    const d = D;
    if (!d.searched) { out().innerHTML = empty('Not searched yet', 'Check this keyword first.'); return; }
    out().innerHTML = `<div class="panel stackv"><div class="row" style="justify-content:space-between"><div class="row"><button class="btn slim ghost" data-allkw>← All niches</button>${kwPicker(all.keywords, kwSel, 'All niches')}</div>
        <a class="btn slim" href="#/k/${mk}/${encodeURIComponent(d.kw)}">Keyword report</a></div>
      <div class="kpis">
        <div class="kpi"><span class="eyebrow">Brands on page 1</span><span class="v">${num(d.brands.length)}</span><span class="s">${num(d.listings)} organic listings</span></div>
        <div class="kpi"><span class="eyebrow">Biggest brand</span><span class="v">${d.brands[0] ? esc(d.brands[0].brand) : '\u2013'}</span><span class="s">${d.top_share != null ? Math.round(d.top_share * 100) + '% of page-1 sales' : 'no badges to compare'}</span></div>
        <div class="kpi"><span class="eyebrow">Amazon competes</span><span class="v">${d.amazon_listings ? 'Yes' : 'No'}</span><span class="s">${d.amazon_listings ? 'Amazon sells ' + d.amazon_listings + ' of the listings' : 'not among the product pages read'}</span></div>
        <div class="kpi"><span class="eyebrow">Page-1 sales</span><span class="v">${d.known_units ? '\u2265 ' + compact(d.known_units) : '\u2013'}</span><span class="s">a month, from the badges (a floor)</span></div>
      </div>
      <div class="tablewrap"><table class="itable"><thead><tr><th>Brand</th><th>Share of page-1 sales</th><th class="r">Listings</th><th>Sales a month</th><th>Growth</th><th>Amazon</th></tr></thead><tbody>
      ${d.brands.map(b => `<tr><td><b>${esc(b.brand)}</b>${b.src === 'title' ? ' <span class="sub">from the title</span>' : ''}</td><td>${share(b.share)}</td><td class="r">${num(b.listings)}</td><td>${est(b.units)}</td>
        <td>${b.new ? '<span class="pill accent">new on page 1</span>' : b.growth == null ? '<span class="muted small">\u2013</span>' : `<span class="${b.growth >= 0 ? 'up' : 'down'}">${b.growth >= 0 ? '+' : ''}${Math.round(b.growth * 100)}%</span>`}</td>
        <td>${b.amazon ? '<span class="pill warn">Amazon</span>' : ''}</td></tr>`).join('')}
      </tbody></table></div>
      <p class="small">Searched ${when(d.checked_at)}${d.compared_with ? ', growth against ' + when(d.compared_with) : '; growth needs a search of this keyword 3 to 6 weeks older'}.</p></div>`;
  }

  /* ---------- Category gaps ---------- */
  function categories() {
    const rows = D.rows;
    if (!rows.length) { out().innerHTML = empty('No Gap Finder checks yet', 'Open the <a href="#/gaps">Gap Finder</a> and press <b>Check now</b>.'); return; }
    const pc = v => v == null ? '\u2013' : Math.round(v * 100) + '%';
    out().innerHTML = `<div class="panel"><div class="tablewrap"><table class="itable"><thead><tr><th>Category</th><th>Market</th><th class="r">Checked</th><th>Open</th><th class="r">Imported</th><th class="r">Amazon only</th><th class="r">Missing</th><th class="r">Local</th><th class="r">Not read</th></tr></thead><tbody>
      ${rows.map(r => `<tr><td><b>${esc(r.category)}</b></td><td>${mkFlag(r.market, true)}</td><td class="r">${num(r.checked)}</td><td>${share(r.share_open)}</td>
        <td class="r">${num(r.imported)} <span class="sub">${pc(r.share_imported)}</span></td><td class="r">${num(r.amazon_only)} <span class="sub">${pc(r.share_amazon)}</span></td>
        <td class="r">${num(r.missing)}</td><td class="r">${num(r.local)} <span class="sub">${pc(r.share_local)}</span></td><td class="r">${r.unread ? num(r.unread) : ''}</td></tr>`).join('')}
      </tbody></table></div></div>`;
  }

  /* ---------- Seller origins ---------- */
  function origins(all) {
    const d = D, niche = !!d.kw;
    const items = niche ? (d.countries || []).map(c => ({label: COUNTRY[c.code] || c.code || 'Not read', flag: c.code, value: c.listings,
      text: `${c.listings} listing${c.listings === 1 ? '' : 's'}${c.share != null ? ' \u00b7 ' + Math.round(c.share * 100) + '% of sales' : ''}`}))
      : (d.countries || []).map(c => ({label: c.country ? (COUNTRY[c.country] || c.country) : 'Not read yet', flag: c.country, value: c.share != null ? c.share : c.sellers,
        text: `${c.sellers} seller${c.sellers === 1 ? '' : 's'} \u00b7 ${c.listings} listings${c.share != null ? ' \u00b7 ' + Math.round(c.share * 100) + '% of sales' : ''}`}));
    out().innerHTML = `<div class="panel stackv"><div class="row" style="justify-content:space-between">${kwPicker((all || {}).keywords || [], kwSel, 'Every seller read')}
      ${niche ? `<a class="btn slim" href="#/k/${mk}/${encodeURIComponent(d.kw)}">Keyword report</a>` : ''}</div><div id="iOrig"></div></div>`;
    if (niche && !d.searched) { $('iOrig').innerHTML = '<p class="small">This keyword was not searched in this market yet.</p>'; return; }
    Charts.bars($('iOrig'), {title: niche ? 'Where the best page-1 sellers are based' : 'Where the sellers are based',
      sub: niche ? 'The keyword\u2019s 10 best organic listings' : 'Every seller read in the last 90 days, by share of the sales they win',
      items, columns: ['Country', 'Sellers'], empty: 'No seller profiles read yet.'});
  }

  /* ---------- Related products ---------- */
  function related(index) {
    const list = (index.read || []).map(r => `<button type="button" class="irel${r.asin === relAsin ? ' on' : ''}" data-rel="${esc(r.asin)}">${r.image ? `<img src="${esc(r.image)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ''}<span>${esc(r.title || r.asin)}<span class="sub">${r.links} links \u00b7 ${when(r.ts)}</span></span></button>`).join('');
    const pick = `<form class="row" id="iRelForm"><input type="text" id="iRelAsin" placeholder="ASIN, e.g. B01EIG6A4Q" value="${esc(relAsin)}" style="width:170px" aria-label="ASIN"><button class="btn slim">Show</button></form>`;
    if (!relAsin) {
      out().innerHTML = `<div class="panel stackv">${pick}${list ? `<div class="irels">${list}</div>` : '<p class="small">No product page with carousels read yet. Pages read from this version on keep their \u201ccustomers also bought\u201d links; open a product report and press <b>Refresh</b>.</p>'}</div>`;
      return;
    }
    const g = D;
    out().innerHTML = `<div class="irelwrap"><div class="panel stackv">${pick}<div class="irels">${list}</div></div>
      <div class="panel stackv"><div class="row" style="justify-content:space-between"><h3>${esc(g.title || g.asin)}</h3>
        <div class="row"><button class="btn slim" id="iRelRead">${icon('refresh')}Read its page now</button><a class="btn slim" href="#/p/${mk}/${esc(g.asin)}">Product report</a></div></div>
        ${g.read ? `<div id="iGraph" class="igraph"></div>
        <div class="irelcols"><div><h3>Competitors (${g.competitors.length})</h3>${relList(g.competitors)}</div><div><h3>Add-on and bundle ideas (${g.addons.length})</h3>${relList(g.addons)}</div></div>
        ${g.brands.length ? `<p class="small">Brands around it: ${g.brands.map(b => esc(b.brand) + (b.n > 1 ? ' (' + b.n + ')' : '')).join(', ')}</p>` : ''}`
        : `<p class="small">Its page has not been read with its carousels yet${g.page_read_at ? ' (last read ' + when(g.page_read_at) + ', before this version)' : ''}. Press <b>Read its page now</b>.</p>`}</div></div>`;
    if (g.read) graph($('iGraph'), g);
  }
  const relList = ns => ns.length ? '<ul class="irellist">' + ns.slice(0, 12).map(n => `<li><button type="button" class="linkish" data-rel="${esc(n.asin)}" title="Show its network">${esc(n.title || n.asin)}</button>
    <span class="sub">${n.kinds.map(k => ({bought: 'also bought', viewed: 'also viewed', fbt: 'bought together', sponsored: 'sponsored on its page'})[k] || k).join(' \u00b7 ')}${n.links_back ? ' \u00b7 links back' : ''}${n.brand ? ' \u00b7 ' + esc(n.brand) : ''}</span></li>`).join('') + '</ul>' : '<p class="small muted">None found.</p>';
  function graph(el, g) {
    const W = 720, H = 380, cx = W / 2, cy = H / 2, nodes = g.nodes.slice(0, 24);
    const ring = n => n.role === 'add-on' ? 105 : 165;
    const col = {competitor: 'var(--s-overseas)', 'add-on': 'var(--s-local)', related: 'var(--s-other)'};
    const byRing = {};
    nodes.forEach(n => (byRing[ring(n)] = byRing[ring(n)] || []).push(n));
    let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Products around ${esc(g.title || g.asin)}">`;
    const pts = [];
    for (const [r, ns] of Object.entries(byRing)) ns.forEach((n, i) => {
      const a = (i / ns.length) * Math.PI * 2 - Math.PI / 2 + (+r === 105 ? 0.3 : 0);
      pts.push({n, x: cx + Math.cos(a) * +r * 1.7, y: cy + Math.sin(a) * +r});
    });
    for (const p of pts) svg += `<line x1="${cx}" y1="${cy}" x2="${p.x}" y2="${p.y}" stroke="${col[p.n.role]}" stroke-width="${p.n.kinds.includes('fbt') ? 3 : 1.5}" stroke-opacity=".55"${p.n.kinds.length ? '' : ' stroke-dasharray="4 4"'}/>`;
    for (const p of pts) svg += `<g class="inode" data-rel="${esc(p.n.asin)}" tabindex="0"><title>${esc((p.n.title || p.n.asin) + ' \u00b7 ' + p.n.role)}</title><circle cx="${p.x}" cy="${p.y}" r="9" fill="${col[p.n.role]}"/><text x="${p.x}" y="${p.y + 22}" text-anchor="middle">${esc((p.n.brand || p.n.title || p.n.asin).slice(0, 16))}</text></g>`;
    svg += `<circle cx="${cx}" cy="${cy}" r="16" fill="var(--accent)"/><text x="${cx}" y="${cy + 34}" text-anchor="middle" class="icenter">${esc((g.brand || g.title || g.asin).slice(0, 24))}</text></svg>`;
    el.innerHTML = svg + `<div class="legend"><span><i style="background:var(--s-overseas)"></i>Competitor</span><span><i style="background:var(--s-local)"></i>Add-on / bundle idea</span><span><i style="background:var(--s-other)"></i>Other related</span><span class="small muted">Thick line: frequently bought together \u00b7 dashed: it links back here only</span></div>`;
  }

  /* ---------- Ad watch ---------- */
  const adPill = r => r.score == null ? '<span class="pill weak">not read</span>' : `<span class="pill ${r.label === 'High' ? 'bad' : r.label === 'Medium' ? 'warn' : 'good'}">${esc(r.label)} \u00b7 ${r.score}</span>`;
  function adsAll() {
    const rows = D.rows;
    if (!rows.length) { out().innerHTML = empty('No niches searched yet', 'Check a product or watch a keyword: each search reads its ads too.'); return; }
    out().innerHTML = `<div class="panel"><div class="tablewrap"><table class="itable"><thead><tr><th>Keyword</th><th>Ad pressure</th><th>Why</th><th>Brands advertising</th><th>Searched</th></tr></thead><tbody>
      ${rows.map(r => `<tr><td><button type="button" class="linkish" data-kw="${esc(r.kw)}"><b>${esc(r.display)}</b></button></td><td>${adPill(r)}<div class="sub">${esc(r.conf)} confidence</div></td>
        <td class="small">${esc(r.why)}</td><td>${r.brands.map(b => `<span class="pill line" title="${esc(b.formats.join(', '))}">${esc(b.brand)} \u00b7 ${b.slots}</span>`).join(' ')}</td><td>${when(r.checked_at)}</td></tr>`).join('')}
      </tbody></table></div></div>`;
  }
  function adsNiche() {
    const d = D;
    out().innerHTML = `<div class="panel stackv"><div class="row" style="justify-content:space-between"><div class="row"><button class="btn slim ghost" data-kw="">\u2190 All keywords</button><h3>\u201c${esc(d.display || d.kw)}\u201d</h3></div>
        <a class="btn slim" href="#/k/${mk}/${encodeURIComponent(d.kw)}">Keyword report</a></div>
      <div class="row">${adPill(d)}<span class="small">${esc(d.why || '')}</span></div>
      ${(d.parts || []).length ? `<ul class="reasons">${d.parts.map(p => `<li class="${p.value >= 0.67 ? 'bad' : p.value >= 0.34 ? 'warn' : 'good'}"><span><b>${esc(p.label)}</b> \u00b7 ${esc(p.why)}</span></li>`).join('')}</ul>` : ''}
      ${(d.brands || []).length ? `<div class="tablewrap"><table class="itable"><thead><tr><th>Brand</th><th class="r">Ad places</th><th>Formats</th><th>Brand from</th></tr></thead><tbody>
        ${d.brands.map(b => `<tr><td><b>${esc(b.brand)}</b></td><td class="r">${b.slots}</td><td>${esc(b.formats.join(', '))}</td><td class="small">${b.src === 'listing' ? 'the listing' : 'the title'}</td></tr>`).join('')}</tbody></table></div>` : '<p class="small">No ads found on page 1.</p>'}</div>`;
  }

  /* ---------- Listing scorecard ---------- */
  let sc = {asin: '', kw: ''};
  function scorecardForm(res) {
    out().innerHTML = `<div class="panel stackv"><form class="row" id="iScForm" autocomplete="off">
        <input type="text" id="iScAsin" placeholder="Your listing's ASIN" value="${esc(sc.asin)}" style="width:170px" aria-label="Your listing's ASIN">
        <input type="text" id="iScKw" placeholder="Its keyword (optional)" value="${esc(sc.kw)}" style="width:240px" aria-label="Its keyword">
        <button class="btn primary">Compare</button></form><div id="iSc"></div></div>`;
    if (res) scorecard(res);
  }
  function scorecard(s) {
    const tone = {ahead: 'good', level: 'line', behind: 'bad', unknown: 'weak', info: 'line'};
    const word = {ahead: 'ahead', level: 'level', behind: 'behind', unknown: 'not read', info: ''};
    const val = v => v === true ? 'yes' : v === false ? 'no' : v == null ? '\u2013' : typeof v === 'number' ? num(v, v % 1 ? 1 : 0) : esc(v);
    $('iSc').innerHTML = `${!s.kw ? '<p class="small">Which keyword? Type the phrase shoppers use for it.</p>' : !s.searched ? `<p class="small">\u201c${esc(s.kw)}\u201d was not searched in this market yet: <a href="#/k/${mk}/${encodeURIComponent(s.kw)}">open its report</a> and check it.</p>` : ''}
      ${s.read ? `<div class="row" style="gap:16px;align-items:center"><div class="iscore ${s.score == null ? '' : s.score >= 67 ? 'good' : s.score >= 40 ? 'warn' : 'bad'}"><b>${s.score ?? '\u2013'}</b><span>of 100</span></div>
        <div><h3>${esc(s.title || s.asin)}</h3><div class="small">${s.n_top ? `Against ${s.n_top} of the top 10 listings for \u201c${esc(s.kw || '')}\u201d` : s.kw ? `The top 10 for \u201c${esc(s.kw)}\u201d have not been read yet: press the button below` : 'No keyword yet'}${s.page_read_at ? ' \u00b7 your page read ' + when(s.page_read_at) : ''}</div></div></div>
        <div class="tablewrap"><table class="itable"><thead><tr><th>Item</th><th class="r">You</th><th class="r">Top 10 typical</th><th>Verdict</th><th>What to do</th></tr></thead><tbody>
        ${s.items.map(i => `<tr><td><b>${esc(i.label)}</b></td><td class="r">${val(i.mine)}</td><td class="r">${val(i.typical)}</td><td>${word[i.verdict] ? `<span class="pill ${tone[i.verdict]}">${word[i.verdict]}</span>` : ''}</td><td class="small">${esc(i.tip || '')}</td></tr>`).join('')}
        </tbody></table></div>` : '<p class="small">Your listing\u2019s page has not been read yet.</p>'}
      <div class="row">${s.mine_todo ? `<button class="btn" id="iScMine">${icon('refresh')}Read my page</button>` : ''}
        ${s.kw && s.todo.length ? `<button class="btn" id="iScTop">${icon('refresh')}Read the ${s.todo.length} top-10 page${s.todo.length === 1 ? '' : 's'} still missing</button>` : ''}
        <span class="small" id="iScProg"></span></div>`;
    if ($('iScMine')) $('iScMine').addEventListener('click', () => runJob('/api/report/product/refresh', {market: mk, asin: s.asin}));
    if ($('iScTop')) $('iScTop').addEventListener('click', () => runJob('/api/report/top', {market: mk, kw: s.kw, force: true}));
  }
  async function runJob(path, body) {
    try {
      const {job} = await api(path, body);
      await waitJob(job, line => { if ($('iScProg')) $('iScProg').innerHTML = busy(line); else toast(line, 1500); });
      toast('Read', 1500);
      if (tab === 'scorecard') compare(); else load();
    } catch (e) { toast(e.message); }
  }
  async function compare() {
    try { scorecardForm(await api('/api/intel/scorecard?' + new URLSearchParams({market: mk, asin: sc.asin, ...(sc.kw ? {kw: sc.kw} : {})}))); }
    catch (e) { scorecardForm(); $('iSc').innerHTML = failPill(e.message); }
  }

  out().addEventListener('change', e => { if (e.target.id === 'iKw') to(tab, mk, e.target.value); });
  out().addEventListener('submit', e => {
    e.preventDefault();
    if (e.target.id === 'iRelForm') to('related', mk, $('iRelAsin').value.trim().toUpperCase());
    if (e.target.id === 'iScForm') { sc = {asin: $('iScAsin').value.trim().toUpperCase(), kw: $('iScKw').value.trim()}; compare(); }
  });
  out().addEventListener('click', e => {
    const k = e.target.closest('[data-kw]'), r = e.target.closest('[data-rel]');
    if (e.target.closest('[data-allkw]')) { to(tab, mk); return; }
    if (k) { if (tab === 'brands' || tab === 'ads') to(tab, mk, k.dataset.kw); else kwSel = k.dataset.kw; }
    if (r) to('related', mk, r.dataset.rel);
    if (e.target.closest('#iRelRead')) runJob('/api/report/product/refresh', {market: mk, asin: relAsin});
  });
  out().addEventListener('keydown', e => { const r = e.target.closest('.inode'); if (r && e.key === 'Enter') to('related', mk, r.dataset.rel); });
})();
