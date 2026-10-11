'use strict';
/* Keyword report: #/k/<market>/<keyword>. Demand (Google Trends + Amazon), units and revenue per month,
   supply over time, competition, every market side by side, and where each number comes from. */
(() => {
  const BASIS_LABEL = {reported: 'Helium 10', estimated: 'estimate', backcast: 'backcast', forecast: 'forecast', measured: 'measured'};
  const OTHER_SHOP = {AE: {name: 'Noon', url: 'https://www.noon.com/uae-en/search/?q='},
                      AU: {name: 'eBay', url: 'https://www.ebay.com.au/sch/i.html?_nkw='},
                      US: {name: 'Walmart', url: 'https://www.walmart.com/search?q='}};
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
    const today = new Date().toLocaleDateString(undefined, {day: 'numeric', month: 'long', year: 'numeric'});
    box.innerHTML = `
      <div class="panel stackv flaghead rcover">
        <svg class="flagbg" aria-hidden="true"><use href="#f-${esc(d.market)}"/></svg>
        <div class="printonly ptitle"><div class="eyebrow">Product Checker · ${esc(today)}</div>
          <h1>${cflag(d.market, '')} “${esc(d.keyword)}” on Amazon ${esc(MK[d.market])}</h1></div>
        <div class="head">
          <div class="row" style="gap:10px">
            <div class="seg noprint" id="kMk">${Object.keys(MK).map(m => `<button type="button" data-v="${m}" aria-pressed="${m === d.market}">${mkFlag(m)}</button>`).join('')}</div>
            ${levelPill}${trendPill}
          </div>
          <div class="row noprint">
            <button class="btn primary" id="kRefresh">${icon('refresh')}Refresh now</button>
            <button class="btn" id="kPdf" title="Save the parts of this report you choose as a PDF in Downloads">${icon('pdf')}PDF</button>
            <button class="btn" data-open="${esc(d.search_url)}">Amazon${icon('external')}</button>
            <button class="btn" data-open="${esc(d.trends_url)}">Google Trends${icon('external')}</button>
            <button class="btn" data-open="${esc(alibabaUrl(d.keyword))}" title="Find suppliers for this product on Alibaba">Alibaba${icon('external')}</button>
            <button class="btn" data-open="${esc(OTHER_SHOP[d.market].url + encodeURIComponent(d.keyword))}" title="The same search on ${OTHER_SHOP[d.market].name}, another big shop in ${esc(MK[d.market])}">${OTHER_SHOP[d.market].name}${icon('external')}</button>
            <button class="btn" id="kXray">${icon('scan')}Xray</button>
            <button class="btn" id="kWatch" title="Re-check this keyword every day and fetch Google Trends weekly">${icon('eye')}Watch</button>
          </div>
        </div>
        <div class="progress noprint" id="kProg">${now ? 'Last searched ' + ago(now.checked_at) : 'Not searched in ' + MK[d.market] + ' yet.'}${tr.as_of ? ' · Google Trends ' + ago(tr.as_of) : ''}</div>
        <p class="sentence" id="kSentence"></p>
      </div>
      <section class="rsec stackv" data-sec="summary">
        <div class="panel stackv" id="kScore"></div>
        <div class="kpis">
          ${tile('Units sold / month', last ? fmtU(last.units.v) : '–', last ? (rangeText(last.units) ? rangeText(last.units) + ' · ' : '') + monthName(last.month) : 'no estimate yet', last ? basisPill(last.units) : '')}
          ${tile('Revenue / month', last && last.revenue && last.revenue.v != null ? esc(cur) + ' ' + compact(last.revenue.v) : '–', last && last.revenue ? [approx(last.revenue.v, d.market, {short: true}), rangeText(last.revenue)].filter(Boolean).join(' · ') : '', last && last.revenue ? basisPill(last.revenue) : '')}
          ${tile('Local sellers', now ? now.local : '–', now ? `${now.fast} fast · ${now.overseas} overseas of ${now.total}` : '')}
          ${tile('Search trend', tr.label ? esc(tr.label) : tr.status === 'low_volume' ? 'Low volume' : '–', tr.score != null ? 'score ' + Math.round(tr.score) + ' of 100' : tr.status === 'none' ? 'not fetched yet' : '')}
          ${tile('Review barrier', comp && comp.review_barrier != null ? num(comp.review_barrier) : '–', 'median reviews, top 10')}
          ${d.demand.volume && d.demand.volume.length ? tile('Searches / month', compact(d.demand.volume[d.demand.volume.length - 1].volume), 'Helium 10 ' + esc(d.demand.volume[d.demand.volume.length - 1].source) + ', ' + esc(d.demand.volume[d.demand.volume.length - 1].month)) : ''}
          ${tile('Price', comp && comp.prices.length ? esc(cur) + ' ' + num(median(comp.prices), 0) : '–', comp && comp.prices.length ? [approx(median(comp.prices), d.market, {d: 0}), 'median of ' + comp.prices.length + ' listings'].filter(Boolean).join(' · ') : '')}
        </div>
      </section>
      <section class="rsec stackv" data-sec="sellers">
        <h2 class="section">Who sells it</h2>
        <div class="panel stackv" id="kWho"><div class="progress">${busy('Loading…')}</div></div>
      </section>
      <section class="rsec stackv" data-sec="money">
        <h2 class="section">Money</h2>
        <div id="kMoney" class="stackv"></div>
      </section>
      <section class="rsec stackv" data-sec="demand">
        <h2 class="section">Demand</h2>
        ${tr.why && tr.why.length ? `<div class="panel"><h3 style="margin-bottom:8px">Why the search trend is “${esc(tr.label)}”</h3><ul class="reasons">${tr.why.map(w => `<li class="${/Declining|Fad/.test(tr.label) ? 'bad' : /Spike|Mixed|Seasonal/.test(tr.label) ? 'warn' : 'good'}">${esc(w)}</li>`).join('')}</ul></div>` : ''}
        <div class="grid2">
          <div class="panel" id="kTrends"></div>
          <div class="panel" id="kSeason"></div>
        </div>
        <div class="panel" id="kUnits"></div>
        <div class="panel" id="kRevenue"></div>
        <details class="more panel"${PRINT ? ' open' : ''}><summary>More detail: page-1 sales and sellers at each check</summary>
          <div class="grid2"><div id="kAmz"></div><div id="kSupply"></div></div>
          <div id="kMix"></div>
        </details>
      </section>
      <section class="rsec stackv" data-sec="competition">
        <h2 class="section">Competition</h2>
        <div class="panel stackv" id="kPer"></div>
        <div class="grid2">
          <div class="panel" id="kPrice"></div>
          <div class="panel" id="kRev"></div>
        </div>
        <div id="kTop"></div>
      </section>
      <section class="rsec stackv" data-sec="reviews">
        <h2 class="section">Customer complaints</h2>
        <div class="panel stackv" id="kNeg"></div>
      </section>
      <section class="rsec stackv" data-sec="markets">
        <h2 class="section">Other markets</h2>
        <div id="kMarkets"></div>
      </section>
      <section class="rsec stackv" data-sec="data">
        <details class="more"${PRINT ? ' open' : ''}><summary><h2 class="section">Where these numbers come from</h2></summary>
          <div class="panel stackv" id="kData"></div></details>
      </section>`;
    if (PRINT) box.querySelectorAll('.rsec').forEach(s => { s.hidden = !PRINT.includes(s.dataset.sec); });

    seg($('kMk'), mk => go('k', mk, d.keyword));
    $('kRefresh').addEventListener('click', () => refresh(d));
    $('kPdf').addEventListener('click', () => pdfDialog(d));
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
    const parts = [loadMoney(d), drawWho(d), negReviews('kNeg', {market: d.market, kw: d.keyword}, {auto: false})];
    if (PRINT) Promise.allSettled(parts).then(() => requestAnimationFrame(() => requestAnimationFrame(() => {
      document.documentElement.dataset.ready = '1';                   // the PDF maker waits for this
    })));
  }

  /* ---------- who sells it: the best page-1 listings, their sellers and where they are based ---------- */
  const whoJobs = new Map(), whoTried = new Set();         // report -> running reading; listings and sellers tried already
  const SHIP = {amazon: ['Amazon itself', 'var(--s-8)'], fba: ['FBA: stock in Amazon’s warehouse', 'var(--s-demand)'],
                fbm: ['FBM: the seller ships', 'var(--s-overseas)'], unknown: ['not read yet', 'var(--s-other)']};
  const shipTag = s => ({amazon: 'Amazon', fba: 'FBA', fbm: 'FBM'}[s] || '');
  const sameReport = d => current && current.kw_norm === d.kw_norm && current.market === d.market;
  const ageText = day => {
    if (!day) return '–';
    const mo = Math.round((Date.now() - new Date(day + 'T12:00:00')) / (30.44 * 864e5));
    return mo < 1 ? 'this month' : mo < 24 ? mo + ' mo' : Math.round(mo / 12) + ' y';
  };
  /* One reading of the top listings per report at a time (product pages, negative reviews, sellers). */
  function readTop(d, force) {
    const key = d.market + '|' + d.kw_norm;
    if (!whoJobs.has(key)) whoJobs.set(key, (async () => {
      try {
        const {job} = await api('/api/report/top', {market: d.market, kw: d.keyword, force: !!force});
        return await waitJob(job, line => { if (sameReport(d) && $('kWhoProg')) $('kWhoProg').innerHTML = busy(line); });
      } finally { whoJobs.delete(key); }
    })());
    return whoJobs.get(key);
  }
  /* Show a reading's progress, then redraw what it changed. Resolves true when it worked. */
  async function follow(d, reading) {
    if ($('kWhoProg')) $('kWhoProg').innerHTML = busy('Opening Amazon…');
    if ($('kWhoRead')) $('kWhoRead').disabled = true;
    try {
      const w = await reading;
      if (sameReport(d)) {
        paintWho(d, w);
        loadMoney(d);                                          // sizes and the one-line summary may have changed
        negReviews('kNeg', {market: d.market, kw: d.keyword}, {auto: false});
      }
      return true;
    } catch (e) {
      if (sameReport(d) && $('kWhoProg')) $('kWhoProg').innerHTML = failPill(e.message);
      if ($('kWhoRead')) $('kWhoRead').disabled = false;
      return false;
    }
  }
  async function drawWho(d) {
    let w;
    try { w = await api('/api/report/sellers?market=' + d.market + '&kw=' + encodeURIComponent(d.keyword)); }
    catch (e) { if (sameReport(d)) $('kWho').innerHTML = failPill(e.message); return; }
    if (!sameReport(d)) return;
    paintWho(d, w);
    const key = d.market + '|' + d.kw_norm, todo = [...w.todo.pages, ...w.todo.sellers];
    if (PRINT) return;
    if (whoJobs.has(key)) { follow(d, whoJobs.get(key)); return; }
    if (w.searched && todo.some(x => !whoTried.has(d.market + x))) setTimeout(() => {        // only if you stay on it
      if (!sameReport(d) || whoJobs.has(key)) return;
      todo.forEach(x => whoTried.add(d.market + x));
      follow(d, readTop(d));
    }, 1500);
  }
  function paintWho(d, w) {
    const el = $('kWho');
    if (!el) return;
    if (!w.searched) {
      el.innerHTML = '<h3>Who sells it</h3><div class="small">Search this keyword first (Refresh now). Then the app reads who sells the top listings.</div>';
      return;
    }
    const n = w.n, known = w.known, sales = w.basis === 'sales';
    const shipParts = ['amazon', 'fba', 'fbm', 'unknown'].map(k => ({label: SHIP[k][0], n: w.ships[k] || 0, color: SHIP[k][1]}));
    const rows = w.listings.map((x, i) => `<tr><td class="r">${i + 1}</td>
      <td class="title"><a data-open="${esc(x.url)}">${esc(x.title || x.asin)}</a><div class="sub">${esc(x.asin)}${x.brand ? ' · ' + esc(x.brand) : ''}</div></td>
      <td>${x.seller ? `${cflag(x.country)} ${x.seller_url ? `<a data-open="${esc(x.seller_url)}" title="${esc(x.business || 'Their page on Amazon')}">${esc(x.seller)}</a>` : esc(x.seller)}${x.seller_rating != null ? `<div class="sub">${num(x.seller_rating)}% positive${x.seller_ratings ? ' · ' + compact(x.seller_ratings) + ' ratings' : ''}</div>` : ''}`
        : '<span class="muted small">not read yet</span>'}</td>
      <td>${x.ships ? `<span class="pill line">${shipTag(x.ships)}</span>` : '–'}</td><td class="r">${ageText(x.launched)}</td><td class="r">${num(x.reviews)}</td>
      <td class="r">${x.units ? fmtU(x.units.v) + `<div class="sub">${{badge: 'badge', rank: 'sales rank', h10: 'Helium 10'}[x.units.src] || ''}</div>` : '–'}</td></tr>`).join('');
    el.innerHTML = `
      <div class="head"><h3>Who sells the top ${n} listings</h3><div class="row"><span class="small" id="kWhoProg">${w.read_at ? 'Read ' + ago(w.read_at) : ''}</span>
        <button type="button" class="btn slim noprint" id="kWhoRead" title="Read their product and seller pages on Amazon again">${icon('refresh')}${w.read_at ? 'Read again' : 'Read now'}</button></div></div>
      ${w.takeaway ? `<p class="takeaway big">${esc(w.takeaway)}</p>` : '<p class="small">The sellers are read from the listings’ Amazon pages: about a minute the first time, then kept for two weeks.</p>'}
      ${w.reasons.length ? `<ul class="reasons">${w.reasons.map(r => `<li class="${r.level}">${esc(r.text)}</li>`).join('')}</ul>` : ''}
      ${known.seller ? `<div class="grid2"><div id="kWhoSellers"></div><div class="stackv"><div id="kWhoCountries"></div>
        <div><h3>How they ship</h3><div class="small" style="margin:2px 0 8px">FBA sellers keep stock in Amazon’s warehouse; FBM sellers send each order themselves.</div>${originBar(shipParts, n, 12)}${legend(shipParts)}</div></div></div>` : ''}
      <div class="tablewrap"><table><thead><tr><th class="r">#</th><th>Listing</th><th>Seller</th><th>Ships</th><th class="r">Launched</th><th class="r">Reviews</th><th class="r">Sales/mo</th></tr></thead><tbody>${rows}</tbody></table></div>
      <div class="small muted">Seller known for ${known.seller} of ${n} listings and their country for ${known.country} (from the business address Amazon shows). Sales per listing come from “bought in past month” badges, else the sales rank or Helium 10: estimates.</div>`;
    if (known.seller) {
      Charts.bars($('kWhoSellers'), {title: 'Biggest sellers', sub: sales ? 'Their share of the sales of the top listings (estimate)' : 'Listings in the top ' + n,
        columns: ['Seller', sales ? 'Share of sales' : 'Listings'],
        items: w.sellers.slice(0, 8).map(s => ({label: s.name || 'Unknown seller', flag: s.country, tag: s.ships.map(shipTag).filter(Boolean).join(' / '),
          value: s.share, text: sales ? Math.round(s.share * 100) + '%' + (s.listings > 1 ? ' · ' + s.listings + ' listings' : '') : s.listings + ' of ' + n}))});
      Charts.bars($('kWhoCountries'), {title: 'Where the sellers are based', sub: 'Listings in the top ' + n + ', Amazon itself not counted', columns: ['Country', 'Listings'],
        empty: 'Amazon has not shown a business address for these sellers yet.',
        items: w.countries.map(c => ({label: COUNTRY[c.code] || c.code, flag: c.code, value: c.listings,
          text: c.listings + ' of ' + n + (sales ? ' · ' + Math.round(c.share * 100) + '% of sales' : '')}))});
    }
    $('kWhoRead').addEventListener('click', () => follow(d, readTop(d, true)));
  }

  /* ---------- the PDF: pick the parts, the app saves it in Downloads ---------- */
  const PDF_PARTS = [['summary', 'Summary: score and key numbers'], ['sellers', 'Who sells it'], ['money', 'Money: profit and launch plan'],
                     ['demand', 'Demand'], ['competition', 'Competition'], ['reviews', 'Customer complaints'], ['markets', 'Other markets'],
                     ['data', 'Where the numbers come from']];
  function pdfDialog(d) {
    let on = ['summary', 'sellers', 'money', 'demand', 'competition', 'reviews'];
    try { const s = JSON.parse(localStorage.getItem('pcPdfParts')); if (Array.isArray(s) && s.length) on = s; } catch (e) { /* defaults */ }
    $('pdfFor').textContent = '“' + d.keyword + '” on Amazon ' + MK[d.market];
    $('pdfParts').innerHTML = PDF_PARTS.map(([k, label]) => `<label class="small"><input type="checkbox" value="${k}"${on.includes(k) ? ' checked' : ''}> ${esc(label)}</label>`).join('');
    $('pdfOut').innerHTML = '';
    $('pdfGo').disabled = false;
    $('pdfGo').onclick = async () => {
      const parts = [...$('pdfParts').querySelectorAll('input:checked')].map(i => i.value);
      if (!parts.length) { $('pdfOut').textContent = 'Tick at least one part.'; return; }
      try { localStorage.setItem('pcPdfParts', JSON.stringify(parts)); } catch (e) { /* not remembered */ }
      $('pdfGo').disabled = true;
      $('pdfOut').innerHTML = busy('Making the PDF…');
      try {
        const {job} = await api('/api/report/pdf', {market: d.market, kw: d.keyword, sections: parts});
        const r = await waitJob(job, line => { $('pdfOut').innerHTML = busy(line); });
        $('pdfOut').innerHTML = `<span class="pill good">${icon('ok')}Saved in ${esc(r.folder)}</span> <b>${esc(r.name)}</b>
          <div class="row" style="margin-top:6px"><button type="button" class="btn slim primary" data-pdf="${esc(r.id)}">Open the PDF</button><button type="button" class="btn slim" data-pdf="${esc(r.id)}" data-folder="1">Show in folder</button></div>`;
      } catch (e) { $('pdfOut').innerHTML = failPill(e.message); }
      $('pdfGo').disabled = false;
    };
    $('pdfOut').onclick = e => {
      const b = e.target.closest('[data-pdf]');
      if (b) api('/api/pdf/open', {id: b.dataset.pdf, folder: !!b.dataset.folder}).catch(err => toast(err.message));
    };
    $('pdfDlg').showModal();
  }

  /* ---------- opportunity, profit and launch plan ---------- */
  const LABEL_TONE = {Strong: 'good', 'Worth a look': 'accent', Weak: 'warn', Skip: 'bad'};
  const pfSaved = new Map();                               // market|keyword -> the profit form as you left it
  const sizeText = (dims, kg) => [dims ? dims.map(v => num(v, 1)).join(' × ') + ' cm' : '', kg != null ? num(kg, 2) + ' kg' : ''].filter(Boolean).join(' · ');
  /* Typical price in market t against the USA, both in US$: tells whether importing leaves room on price. */
  function priceGap(d, t) {
    const us = (d.markets.US || {}).price_med, there = (d.markets[t] || {}).price_med, thereUsd = fxConvert(there, MK_CUR[t], 'USD');
    if (us == null || thereUsd == null) return '';
    const diff = Math.round((thereUsd / us - 1) * 100);
    return `<div class="small">Typical price ${esc(priceFx(there, t))} · <b class="${diff >= 10 ? 'up' : diff <= -10 ? 'down' : ''}">${diff > 0 ? '+' : ''}${diff}%</b> against the USA (${esc(priceFx(us, 'US'))})</div>`;
  }
  async function loadMoney(d) {
    let m;
    try { m = await api('/api/report/money?market=' + d.market + '&kw=' + encodeURIComponent(d.keyword)); }
    catch (e) { return; }
    if (!current || current.kw_norm !== d.kw_norm || current.market !== d.market) return;
    const o = m.opportunity, cur = d.currency;
    const tone = p => !p.known ? 'weak' : p.v >= 0.66 ? 'good' : p.v >= 0.4 ? 'warn' : 'bad';
    const part = p => `<div class="part ${tone(p)}"><span class="small">${esc(p.name)}</span><div class="bar" style="height:8px"><span style="width:${Math.round((p.v || 0) * 100)}%"></span></div><span class="small tnum">${p.known ? Math.round((p.v || 0) * 100) : '?'}</span><div class="sub" style="grid-column:1/-1">${esc(p.why || '')}</div></div>`;
    $('kSentence').textContent = m.sentence || '';
    $('kScore').innerHTML = `
      <div class="head"><h3>Score</h3>${o ? `<span class="pill ${LABEL_TONE[o.label] || 'weak'}">${esc(o.label)} · ${o.score != null ? Math.round(o.score) : '?'} / 100</span>` : ''}</div>
      ${o ? `<div class="parts">${o.parts.map(part).join('')}</div>
        <div class="small">Green is good for you, red is hard. Crowding ${m.saturation && m.saturation.score != null ? Math.round(m.saturation.score) + ' / 100' : 'unknown'} · confidence ${esc(o.conf)}${o.risk && o.risk.n ? ' · risky: ' + esc(o.risk.flags.join(', ')) : ''}</div>`
        : '<div class="small">Search this keyword first (Refresh now).</div>'}`;
    const plan = m.plan || {};
    const dates = x => x ? `order by <b>${esc(x.order_by)}</b> · ship by ${esc(x.ship_by)} · in stock by ${esc(x.in_stock_by)}` : '–';
    // what you typed in the profit calculator survives a refresh or a currency change of this report
    const pfKey = d.market + '|' + d.kw_norm, saved = pfSaved.get(pfKey) || {};       // only the fields you changed yourself
    pfSaved.set(pfKey, saved);
    const pfVal = (k, dflt) => esc(saved[k] != null && saved[k] !== '' ? saved[k] : dflt);
    const sz = m.size || {}, amz = {l: (sz.dims_cm || [])[0], w: (sz.dims_cm || [])[1], h: (sz.dims_cm || [])[2], kg: sz.kg};
    const sizeVal = k => pfVal(k, amz[k] ?? {l: 30, w: 20, h: 10, kg: 0.5}[k]);
    const amzKnown = sz.dims_cm || sz.kg != null;
    let cogs = m.cogs_usd != null ? +fxConvert(m.cogs_usd, 'USD', costCur()).toFixed(2) : '';
    if (saved.cogs != null && saved.cogs !== '') {
      cogs = saved.cogs_currency && saved.cogs_currency !== costCur() ? +fxConvert(+saved.cogs, saved.cogs_currency, costCur()).toFixed(2) : saved.cogs;
    }
    $('kMoney').innerHTML = `
      <div class="grid2">
        ${Object.keys(m.arbitrage).length ? `<div class="panel stackv"><h3>Bring it from the USA?</h3>${Object.entries(m.arbitrage).map(([t, a]) => a ? `<div class="row" style="justify-content:space-between"><span><b>${mkFlag(t)}</b> <span class="small">${esc((a.why || []).slice(0, 2).join(' · '))}</span>${priceGap(d, t)}</span><span class="pill ${LABEL_TONE[a.label] || 'weak'}">${esc(a.label || '')} ${a.score != null ? Math.round(a.score) : '?'}</span></div>` : `<div class="small">${mkFlag(t)}: not checked yet. <a href="#/k/${t}/${encodeURIComponent(d.keyword)}">Open</a> and refresh.</div>`).join('')}</div>` : ''}
        <div class="panel stackv">
          <h3>Launch plan</h3>
          ${plan.status === 'unknown' ? `<div class="small">${esc(plan.note || 'Seasonality unknown.')}</div>` : `
            <div class="small">Peak in <b>${esc(Charts.MONTHS[(plan.peak_month || 1) - 1])}</b>, demand starts rising in ${esc(Charts.MONTHS[(plan.upswing_month || 1) - 1])}.</div>
            ${plan.status === 'too_late' ? `<div class="verdict some">${icon('warn')}<span>Too late by sea for this season (by ${plan.late_by_days} days). By air: ${dates(plan.air)}.</span></div>` : ''}
            ${plan.status === 'next_year' ? `<div class="verdict none">${icon('dot')}<span>This season is too close. Plan for next year: ${dates(plan.sea)}.</span></div>` : ''}
            ${plan.status === 'ok' ? `<div><b>By sea:</b> ${dates(plan.sea)}</div><div><b>By air:</b> ${dates(plan.air)}</div>` : ''}
            <div class="small muted">Production 30 days, transit by sea or air, Amazon check-in, then 45 days to rank before the season.</div>`}
        </div>
      </div>
      <div class="panel stackv" id="kProfit">
        <div class="head"><h3>Profit per sale</h3><span class="small">Fee tables as of ${esc('2026-10-09')}${d.market !== 'US' ? ' (estimates for ' + esc(MK[d.market]) + ': check on Amazon)' : ''}</span></div>
        <form class="row" id="pfForm" style="align-items:end">
          <div class="field"><label for="pfPrice">Price (${esc(cur)})</label><input type="number" step="0.01" id="pfPrice" name="price" value="${pfVal('price', m.price != null ? m.price : '')}"></div>
          <div class="field"><label for="pfCost">Product cost per piece</label><div class="row" style="gap:4px;flex-wrap:nowrap"><input type="number" step="0.01" min="0" id="pfCost" name="cogs" value="${esc(cogs)}" placeholder="from supplier">
            <select name="cogs_currency" aria-label="Currency of the product cost" title="The currency your supplier quotes in">${CURRENCIES.map(c => `<option value="${c}" ${c === costCur() ? 'selected' : ''}>${CUR_SYM[c]}</option>`).join('')}</select></div></div>
          <div class="field"><label>Size L × W × H (cm)</label><div class="row" style="gap:4px"><input type="number" step="0.1" name="l" style="width:64px" value="${sizeVal('l')}" aria-label="Length (cm)"><input type="number" step="0.1" name="w" style="width:64px" value="${sizeVal('w')}" aria-label="Width (cm)"><input type="number" step="0.1" name="h" style="width:64px" value="${sizeVal('h')}" aria-label="Height (cm)"></div></div>
          <div class="field"><label for="pfKg">Weight (kg)</label><input type="number" step="0.01" id="pfKg" name="kg" value="${sizeVal('kg')}"></div>
          <div class="field"><label for="pfFreight">Freight</label><select id="pfFreight" name="freight_mode"><option value="sea">Sea</option><option value="air" ${saved.freight_mode === 'air' ? 'selected' : ''}>Air</option></select></div>
          <div class="field"><label for="pfUnits">First order (units)</label><input type="number" min="0" step="50" id="pfUnits" name="units" value="${pfVal('units', 300)}"></div>
          ${d.market !== 'US' ? `<label class="small"><input type="checkbox" name="registered" ${(ST.settings.registered || {})[d.market] ? 'checked' : ''}> Registered for ${d.market === 'AU' ? 'GST' : 'VAT'}</label>` : ''}
          <label class="small noprint"><input type="checkbox" name="save_cogs"> Remember the cost</label>
        </form>
        <div class="row small sizebar" id="pfSize">
          <span>${amzKnown ? `Size from Amazon: <b>${esc(sizeText(sz.dims_cm, sz.kg))}</b> <span class="muted">(${sz.n ? 'median of ' + sz.n + ' top listing' + (sz.n > 1 ? 's' : '') : 'weight from Helium 10'})</span>`
            : sz.searched ? 'Size and weight above are a guess until Amazon\'s are read.' : 'Search this keyword (Refresh now) to read the size from Amazon.'}</span>
          ${amzKnown ? '<button type="button" class="btn slim" id="pfUseAmz" hidden>Use Amazon\'s size</button>' : ''}
          ${sz.searched && sz.n < 3 && (sz.missing || []).length ? `<button type="button" class="btn slim" id="pfReadAmz" title="Opens the product pages of the top listings in the background">${sz.n ? 'Read more listings' : 'Read size from Amazon'}</button>` : ''}
          <input type="text" id="pfPaste" placeholder="Or paste the supplier's size and weight, e.g. 30*20*10 cm, gross weight 0.5 kg" aria-label="Paste the supplier's size and weight">
          <span id="pfSizeOut"></span>
        </div>
        <div id="pfOut"></div>
      </div>`;
    const form = $('pfForm');
    const syncUse = () => {                                   // offer Amazon's size again once you have changed it
      if ($('pfUseAmz')) $('pfUseAmz').hidden = !['l', 'w', 'h', 'kg'].some(k => amz[k] != null && +form[k].value !== +amz[k]);
    };
    const keep = (...names) => { names.forEach(n => { if (form[n]) saved[n] = form[n].value; }); syncUse(); };
    const setSize = (dims, kg) => {
      const names = [];
      if (dims) ['l', 'w', 'h'].forEach((k, i) => { form[k].value = dims[i]; names.push(k); });
      if (kg != null) { form.kg.value = kg; names.push('kg'); }
      return names;
    };
    let last = null;
    const run = async () => {
      const f = Object.fromEntries(new FormData(form));
      f.registered = !!form.registered && form.registered.checked;
      f.save_cogs = form.save_cogs.checked;
      Object.assign(f, {market: d.market, kw: d.keyword});
      try { last = await api('/api/profit', f); renderProfit(last, m, d.market); } catch (e) { $('pfOut').innerHTML = failPill(e.message); }
    };
    form.addEventListener('input', e => {
      if (e.target.name === 'cogs') keep('cogs', 'cogs_currency'); else if (e.target.name) keep(e.target.name);
      if (e.target.name === 'units' && last) renderProfit(last, m, d.market);     // planner only: no new calculation needed
    });
    form.addEventListener('change', e => {
      if (e.target.name === 'units') return;
      if (e.target.name === 'cogs_currency') {
        const was = costCur(), cost = parseFloat(form.cogs.value);
        if (cost >= 0) form.cogs.value = +fxConvert(cost, was, e.target.value).toFixed(2);   // the same cost, in the other currency
        keep('cogs', 'cogs_currency');
        saveCurrency('cost_currency', e.target.value);
      } else if (e.target.name) keep(e.target.name);
      run();
    });
    let pasteTimer = 0;
    $('pfSize').addEventListener('input', e => {
      if (e.target.id !== 'pfPaste') return;
      clearTimeout(pasteTimer);
      pasteTimer = setTimeout(() => readPaste(e.target.value.trim()), 400);
    });
    const readPaste = async text => {
      const out = $('pfSizeOut');
      if (!text) { out.innerHTML = ''; return; }
      try {
        const r = await api('/api/parse_size', {text});
        if (!r.dims_cm && r.kg == null) { out.innerHTML = '<span class="pill weak">No size or weight found in that text</span>'; return; }
        keep(...setSize(r.dims_cm, r.kg));
        out.innerHTML = `<span class="pill good">${icon('ok')}Supplier: ${esc(sizeText(r.dims_cm, r.kg))}</span>`;
        run();
      } catch (err) { out.innerHTML = failPill(err.message); }
    };
    $('pfSize').addEventListener('click', async e => {
      if (e.target.closest('#pfUseAmz')) {
        setSize(sz.dims_cm, sz.kg).forEach(k => delete saved[k]);          // follow Amazon's size again
        syncUse();
        return run();
      }
      const b = e.target.closest('#pfReadAmz');
      if (!b) return;
      b.disabled = true;
      ['l', 'w', 'h', 'kg'].forEach(k => delete saved[k]);                // you asked for Amazon's size: use it
      $('pfSizeOut').innerHTML = busy('Opening product pages on Amazon…');
      if (!await follow(d, readTop(d)) && $('pfSizeOut')) {
        $('pfSizeOut').innerHTML = failPill('Could not read the listings: see Who sells it above.');
        b.disabled = false;
      }
    });
    syncUse();
    form.addEventListener('submit', e => { e.preventDefault(); run(); });
    if (form.price.value) await run();
  }

  function renderProfit(r, m, mk) {
    const e = r.economics, cur = CUR_SYM[MK_CUR[mk]], mny = v => (v < 0 ? '−' : '') + cur + ' ' + num(Math.abs(v), 2);
    const units = m.metrics && m.metrics.units_top10_median;
    $('pfOut').innerHTML = `
      <div class="kpis" style="margin:8px 0">
        <div class="kpi"><span class="eyebrow">Profit per sale</span><span class="v" style="color:var(--${e.Profit >= 0 ? 'good' : 'bad'}-text)">${mny(e.Profit)}</span><span class="s">${[e.Margin != null ? Math.round(e.Margin * 100) + '% margin' : '', approx(e.Profit, mk)].filter(Boolean).join(' · ')}</span></div>
        <div class="kpi"><span class="eyebrow">Return on stock</span><span class="v">${e.ROI != null ? Math.round(e.ROI * 100) + '%' : '–'}</span><span class="s">profit ÷ landed cost</span></div>
        <div class="kpi"><span class="eyebrow">Break-even ad spend</span><span class="v">${e.BreakEvenACoS != null ? Math.round(e.BreakEvenACoS * 100) + '%' : '–'}</span><span class="s">ACoS where profit hits zero</span></div>
        <div class="kpi"><span class="eyebrow">Highest product cost</span><span class="v">${r.max_cogs_20_cost != null ? curMoney(r.max_cogs_20_cost, r.cost_currency || 'USD') : '–'}</span><span class="s">for a 20% margin${r.max_cogs_20 != null && r.cost_currency !== 'USD' ? ' · US$ ' + num(r.max_cogs_20, 2) : ''}</span></div>
        ${units ? `<div class="kpi"><span class="eyebrow">Per month, selling like a median top-10 listing</span><span class="v">${mny(e.Profit * units)}</span><span class="s">${[approx(e.Profit * units, mk, {d: 0}), '~' + num(units) + ' sales a month (badge estimate, rough)'].filter(Boolean).join(' · ')}</span></div>` : ''}
      </div>
      ${orderPlan(e, mk, mny)}
      <div class="grid2">
        <div><table><tbody>${e.waterfall.map(([k, v]) => `<tr${k === 'Profit' ? ' class="fast"' : ''}><td>${esc({NetSales: e.registered ? 'Price without GST/VAT' : 'Price', Referral: 'Referral fee', FBA: 'FBA fee (' + (e.fee_source === 'table' ? (e.tier_label || 'table') : e.fee_source === 'h10' ? 'Helium 10' : 'yours') + ')', Storage: 'Storage', Placement: 'Inbound placement', FeeTax: 'GST/VAT on fees', Landed: 'Product + freight + duty', Ads: 'Ads (12% of sales)', Returns: 'Returns (2.5%)', Other: 'Other', Profit: 'Profit'}[k] || k)}</td><td class="r">${k === 'NetSales' || k === 'Profit' ? mny(v) : '− ' + num(v, 2)}</td></tr>`).join('')}</tbody></table>
          ${e.warnings.map(w => `<div class="small">${icon('warn')} ${esc(w)}</div>`).join('')}</div>
        <div><table><thead><tr><th class="r">Price</th><th class="r">Profit</th><th class="r">Margin</th><th></th></tr></thead><tbody>
          ${r.grid.filter((g, i) => i % 2 === 0 || g.cliff).map(g => `<tr><td class="r">${num(g.price, 2)}</td><td class="r ${g.profit < 0 ? 'down' : ''}">${num(g.profit, 2)}</td><td class="r">${g.margin != null ? Math.round(g.margin * 100) + '%' : ''}</td><td>${g.cliff ? '<span class="pill warn">fee step</span>' : ''}</td></tr>`).join('')}
        </tbody></table></div>
      </div>`;
  }

  /* The first order: money it ties up, how many sales bring it back, and how long it lasts at the page-1 average. */
  function orderPlan(e, mk, mny) {
    const n = Math.round(+(($('pfForm') || {}).units || {}).value || 0);
    if (!(n > 0)) return '';
    if (!(e.COGS > 0)) return '<div class="small">Type the product cost to plan the first order.</div>';
    const ps = current && current.now && current.now.per_seller, rate = ps && ps.per_seller ? ps.per_seller.v : null;
    const back = e.Profit + e.Landed, months = rate ? n / rate : null;          // each sale returns its landed cost + profit
    const tile = (label, v, s, tone) => `<div><div class="of">${label}</div><div class="v"${tone ? ` style="color:var(--${tone}-text)"` : ''}>${v}</div><div class="of">${s || ''}</div></div>`;
    return `<h3 style="margin-top:4px">First order of ${num(n)} units</h3><div class="perseller" style="margin:8px 0">
      ${tile('Money needed', mny(n * e.Landed), [approx(n * e.Landed, mk, {d: 0}), 'product, freight and duty'].filter(Boolean).join(' · '))}
      ${tile('Money back after', e.Profit >= 0 ? num(Math.ceil(n * e.Landed / back)) + ' sales' : 'never',
             e.Profit >= 0 ? Math.round(e.Landed / back * 100) + '% of the order' : 'each sale loses money', e.Profit >= 0 ? '' : 'bad')}
      ${tile('Profit on the whole order', mny(n * e.Profit), approx(n * e.Profit, mk, {d: 0}), e.Profit >= 0 ? 'good' : 'bad')}
      ${tile('Sold out in about', months == null ? '–' : months < 1 ? 'under a month' : num(months, 1) + ' months',
             rate ? 'at ≈ ' + compact(rate) + ' sales a month (page-1 average per seller)' : 'needs sales badges on page 1')}
    </div>${months > 4 ? `<div class="small">${icon('warn')} Slow to sell: storage after the first 2 months is not in the profit above, and a smaller first order ties up less money.</div>` : ''}`;
  }

  const median = v => { const s = [...v].sort((a, b) => a - b); return s.length ? s[Math.floor(s.length / 2)] : null; };
  const monthName = key => Charts.MONTHS[+key.slice(5) - 1] + ' ' + key.slice(0, 4);

  /* ---------- the charts, each with one plain line saying what it means ---------- */
  const change = (a, b) => { const p = Math.round((a / b - 1) * 100); return (p > 0 ? '+' : '') + p + '%'; };
  const quantile = (v, q) => { const s = [...v].sort((a, b) => a - b); return s.length ? s[Math.floor((s.length - 1) * q)] : null; };
  let unitsMore = false;

  function drawTrends(d) {
    const tr = d.trends || {}, el = $('kTrends'), yoy = (tr.features || {}).YoY, s = tr.seasonality;
    const pts = (tr.monthly || []).map(([m, v]) => ({t: Charts.monthT(m), y: v}));
    const empty = tr.status === 'low_volume' ? 'Not enough Google searches for “' + d.keyword + '” in ' + MK[d.market] + '. That is not the same as no demand: check Amazon below.'
      : tr.status === 'none' ? 'Google Trends not fetched yet. Press Refresh now.' : 'No Google Trends data.';
    const takeaway = tr.label && pts.length ? 'Searches: ' + tr.label.split(',')[0].toLowerCase() +
      (yoy != null && isFinite(yoy) ? ' (' + (yoy >= 0 ? '+' : '') + Math.round(yoy * 100) + '% on a year ago)' : '') +
      (s && s.reliable ? '; busiest in ' + Charts.MONTHS[s.peak_month - 1] : '') + '.' : '';
    Charts.timeseries(el, {title: 'Search interest, 5 years', sub: 'Google Trends in ' + MK[d.market] + ' · 100 = the busiest month', takeaway,
      x: 'month', series: pts.length ? [{label: 'Search interest', color: 'var(--s-demand)', basis: 'index', points: pts}] : [],
      yMin: 0, yMax: 100, empty, foot: ['Source: Google Trends (index, not search counts)', tr.as_of ? 'Fetched ' + ago(tr.as_of) : null,
        tr.q != null ? 'Agreement between samples ' + Math.round(tr.q * 100) + '%' : null].filter(Boolean)});
  }

  function drawSeason(d) {
    const s = (d.trends || {}).seasonality;
    const note = s && s.reliable ? `Peak month: ${Charts.MONTHS[s.peak_month - 1]}. Have stock live by ${Charts.MONTHS[(s.launch_month || 1) - 1]} to catch it.`
      : s ? 'The pattern is weak or the history is short, so treat it as a hint.' : null;
    let takeaway = '';
    if (s && s.reliable && s.si && s.si.length === 12) {
      const lo = s.si.indexOf(Math.min(...s.si));
      takeaway = `The busiest month (${Charts.MONTHS[s.peak_month - 1]}) sells about ${num(s.si[s.peak_month - 1], 1)}× an average month; the quietest is ${Charts.MONTHS[lo]} (${num(s.si[lo], 1)}×).`;
    }
    Charts.seasonStrip($('kSeason'), {title: 'Seasonality', sub: 'Each month compared with an average month (1.00)', takeaway,
      values: s ? s.si : null, peak: s && s.reliable ? s.peak_month : null, mark: s && s.reliable ? s.launch_month : null,
      note, foot: ['From the Google Trends shape'], empty: 'Needs about two years of Google Trends data.'});
  }

  function drawUnits(d) {
    const series = [], mm = d.monthly, a = mm[mm.length - 1], b = mm[mm.length - 2];
    const pts = basis => mm.filter(m => m.units.basis === basis).map(m => ({t: Charts.monthT(m.month), y: m.units.v, lo: m.units.lo, hi: m.units.hi,
      note: m.method === 'badge' ? (m.badged || 0) + ' listings with a sales badge' : m.listings ? m.listings + ' listings in the export' : ''}));
    if (unitsMore && d.backcast.length) series.push({label: 'Backcast from search trend', color: 'var(--s-demand)', basis: 'backcast',
      points: d.backcast.map(m => ({t: Charts.monthT(m.month), y: m.units.v, lo: m.units.lo, hi: m.units.hi}))});
    const est = pts('estimated');
    if (est.length) series.push({label: 'Badge estimate', color: 'var(--s-demand)', basis: 'estimated', points: est, maxGap: 70 * 864e5});
    const h10 = pts('reported');
    if (h10.length) series.push({label: 'Helium 10', color: 'var(--s-demand)', basis: 'reported', points: h10});
    if (unitsMore && d.forecast.length) series.push({label: 'Forecast', color: 'var(--s-demand)', basis: 'forecast',
      points: [mm[mm.length - 1], ...d.forecast].map(m => ({t: Charts.monthT(m.month), y: m.units.v, lo: m.units.lo, hi: m.units.hi}))});
    const takeaway = a && a.units.v != null ? `About ${fmtU(a.units.v)} sold in ${monthName(a.month)} on page 1` +
      (b && b.units.v ? `, ${change(a.units.v, b.units.v)} on ${monthName(b.month)}` : '') + '.' : '';
    Charts.timeseries($('kUnits'), {title: 'Units sold per month', sub: 'Page 1 of the Amazon search', takeaway,
      more: d.backcast.length || d.forecast.length ? {on: unitsMore, toggle: () => { unitsMore = !unitsMore; drawUnits(d); }} : null,
      x: 'month', series, yMin: 0, endLabel: false,
      empty: 'No monthly numbers yet. Run Check sellers for this keyword, or import a Helium 10 Xray export (Library → Helium 10 imports).',
      foot: ['Helium 10 = its own estimate (±25%)', 'Badge estimate = “bought in past month” ranges, listings with a badge only (an undercount)',
        unitsMore && d.backcast.length ? 'Backcast = recent units × the Google Trends shape' : null,
        unitsMore && d.forecast.length ? 'Forecast = seasonal pattern × yearly trend' : null].filter(Boolean)});
  }

  function drawRevenue(d) {
    const items = [...d.backcast, ...d.monthly].filter(m => m.revenue && m.revenue.v != null).map(m => ({label: monthName(m.month),
      short: Charts.monthLabel(Charts.monthT(m.month), m.month.endsWith('-01')),
      y: m.revenue.v, lo: m.revenue.lo, hi: m.revenue.hi, basis: m.revenue.basis}));
    const last = [...d.monthly].reverse().find(m => m.revenue && m.revenue.v != null);
    Charts.columns($('kRevenue'), {title: 'Revenue per month', sub: d.currency + ', page 1 listings', items, yTitle: 'Revenue (' + d.currency + ')',
      takeaway: last ? `About ${d.currency} ${compact(last.revenue.v)} of sales on page 1 in ${monthName(last.month)}${approx(last.revenue.v, d.market, {short: true}) ? ' (' + approx(last.revenue.v, d.market, {short: true}) + ')' : ''}.` : '',
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
      takeaway: amz.length ? `About ${fmtU(amz[amz.length - 1].y)} a month at the last check` + (amz.length > 1 ? `, ${fmtU(amz[0].y)} at the first.` : '.') : '',
      series: amz.length ? [{label: 'Units', color: 'var(--s-demand)', basis: 'estimated', points: amz}] : [], gaps, xDomain: dom, group,
      yMin: 0, empty: 'No checks yet. Press Refresh now.', foot: ['Source: Amazon search page (estimate from badge ranges)']});
    const sp = d.supply.points, lp = sp[sp.length - 1];
    Charts.timeseries($('kSupply'), {title: 'Sellers on page 1', sub: 'Local = arrives within ' + (d.now ? d.now.local_days : 9) + ' days',
      takeaway: lp ? `${lp.local} local and ${lp.overseas} overseas sellers at the last check` + (sp.length > 1 ? ` (${sp[0].local} and ${sp[0].overseas} at the first).` : '.') : '',
      series: sp.length ? [
        {label: 'Local', color: 'var(--s-local)', basis: 'measured', points: sp.map(p => ({t: tMs(p.t), y: p.local}))},
        {label: 'Overseas', color: 'var(--s-overseas)', basis: 'measured', points: sp.map(p => ({t: tMs(p.t), y: p.overseas}))},
      ] : [], gaps, xDomain: dom, group, yMin: 0, empty: 'No checks yet.', foot: ['Source: Amazon search page (measured)', gaps.length ? gaps.length + ' blocked or failed check' + (gaps.length > 1 ? 's' : '') + ' shown as grey marks' : null].filter(Boolean)});
    Charts.shares($('kMix'), {title: 'Where page 1 ships from', sub: 'Share of organic listings at each check',
      takeaway: lp && (lp.local + lp.overseas + (lp.unknown || 0)) ? `${Math.round(lp.overseas / (lp.local + lp.overseas + (lp.unknown || 0)) * 100)}% of page 1 shipped from overseas at the last check.` : '',
      parts: ORIGIN.map(o => ({key: o.key, label: o.label, color: o.color})),
      columns: sp.map(p => ({label: new Date(tMs(p.t)).toLocaleDateString(), short: new Date(tMs(p.t)).toLocaleDateString(undefined, {day: 'numeric', month: 'short'}),
        parts: [{key: 'local', n: p.local || 0}, {key: 'overseas', n: p.overseas || 0}, {key: 'unknown', n: p.unknown || 0}]})),
      empty: 'No checks yet.', foot: ['Source: Amazon search page (measured)']});
  }

  function drawCompetition(d) {
    const c = d.competition, ps = d.now && d.now.per_seller;
    $('kPer').hidden = !ps;
    if (ps) {
      $('kPer').innerHTML = `<h3>Demand vs sellers</h3>${perSellerTiles(ps) || '<div class="small muted">No listing shows a “bought in past month” badge, so sales per seller are unknown.</div>'}<div id="kPerBars"></div>`;
      if (ps.demand.v != null) Charts.sellerBars($('kPerBars'), {title: 'Sales per seller', sub: 'Estimated sales a month of each listing on page 1',
        takeaway: ps.per_seller ? `About ${compact(ps.per_seller.v)} sales a month per seller on average` + (ps.top3_share != null ? `; the 3 best listings take ${Math.round(ps.top3_share * 100)}% of them.` : '.') : '',
        listings: ps.listings, currency: d.currency, priceFmt: v => priceFx(v, d.market), foot: ['Source: “bought in past month” badges (estimate)', 'Latest check']});
    }
    const prices = c ? c.prices : [], rb = c ? c.review_barrier : null;
    Charts.histogram($('kPrice'), {title: 'Prices on page 1', sub: d.currency, values: prices, format: v => num(v, 0), xTitle: 'Price',
      takeaway: prices.length >= 3 ? `Most listings cost ${d.currency} ${num(quantile(prices, 0.25), 0)}–${num(quantile(prices, 0.75), 0)}; the middle one is ${d.currency} ${num(median(prices), 0)}.` : '',
      empty: 'No listings yet.', foot: ['Latest check']});
    Charts.histogram($('kRev'), {title: 'Reviews on page 1', sub: 'Fewer reviews = easier to compete', values: c ? c.reviews : [], log: true,
      takeaway: rb != null ? `Half of the top 10 have fewer than ${num(rb)} reviews: ${rb < 100 ? 'a new listing can catch up' : rb < 500 ? 'catching up takes a while' : 'hard to catch up'}.` : '',
      format: v => compact(v), xTitle: 'Reviews', color: 'var(--s-demand)', empty: 'No listings yet.',
      foot: [c && c.low_review_n != null ? c.low_review_n + ' listings under 100 reviews' : 'Latest check']});
    if (!c || !c.top.length) { $('kTop').innerHTML = ''; return; }
    const cur = d.currency;
    $('kTop').innerHTML = `<div class="head" style="margin-bottom:8px"><h3>Top listings now</h3>${c.top3_share != null ? `<span class="small">Top 3 badged listings take ${Math.round(c.top3_share * 100)}% of badge sales</span>` : ''}</div>
      <div class="tablewrap"><table><thead><tr><th class="r">#</th><th>Product</th><th>Ships from</th><th class="r">Price</th><th class="r">Reviews</th><th class="r">Bought/mo</th></tr></thead><tbody>
      ${c.top.map((r, i) => `<tr><td class="r">${i + 1}</td><td class="title"><a data-open="${esc(r.url)}">${esc(r.title)}</a><div class="sub">${esc(r.asin)} · <a href="#/p/${d.market}/${esc(r.asin)}">product report</a></div></td>
        <td>${r.origin === 'local' ? '<span class="pill line"><span class="swatch" style="background:var(--s-local)"></span>local</span>' : r.origin === 'overseas' ? '<span class="pill line"><span class="swatch" style="background:var(--s-overseas)"></span>overseas</span>' : '<span class="pill weak">unknown</span>'}<div class="sub">${esc(r.why || '')}</div></td>
        <td class="r">${money(r.price, '')}${fxSub(r.price, d.market)}</td><td class="r">${num(r.reviews)}</td><td class="r">${r.units ? fmtU(r.units.v) + '<div class="sub">' + rangeText(r.units) + '</div>' : ''}</td></tr>`).join('')}
      </tbody></table></div><p class="small" style="margin-top:6px">Prices in ${esc(cur)}. Bought/mo is the middle of the badge range.</p>`;
  }

  function drawMarkets(d) {
    const rows = Object.entries(d.markets);
    $('kMarkets').innerHTML = `<div class="tablewrap"><table><thead><tr><th>Market</th><th class="r">Local</th><th class="r">Overseas</th><th class="r">Fast</th><th>Sellers</th><th class="r">Units/mo (page 1)</th><th class="r">Typical price</th><th>Search trend</th><th>Last check</th><th></th></tr></thead><tbody>
      ${rows.map(([m, r]) => `<tr${m === d.market ? ' class="fast"' : ''}><td><b>${mkFlag(m)}</b></td><td class="r">${r.local ?? '–'}</td><td class="r">${r.overseas ?? '–'}</td><td class="r">${r.fast ?? '–'}</td>
        <td>${r.level ? `<span class="pill ${LEVEL[r.level] || 'weak'}"><i></i>${esc({opportunity: 'gap', some: 'some', crowded: 'crowded', none: 'none'}[r.level])}</span>` : '<span class="muted small">not checked</span>'}</td>
        <td class="r">${r.units ? fmtU(r.units.v) : '–'}</td><td class="r">${r.price_med != null ? curMoney(r.price_med, MK_CUR[m]) + fxSub(r.price_med, m) : '–'}</td><td>${r.trend && r.trend.label ? esc(r.trend.label) : r.trend && r.trend.status === 'low_volume' ? '<span class="muted">low volume</span>' : '–'}</td>
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
  window.addEventListener('pc:currency', e => { if (e.detail.key === 'price_currency' && currentView === 'k' && current) render(current); });
})();
