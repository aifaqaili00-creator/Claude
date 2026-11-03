'use strict';
/* Market Gaps: where Amazon Australia and the UAE leave room. A SmartScout export is the baseline (brands, sellers,
   who dominates each niche); Refresh reads Amazon itself to say whether each gap is still open. */
(() => {
  view('mgaps', {title: 'Market Gaps', sub: 'Where Amazon Australia and the UAE leave room for you · SmartScout data and the app’s own checks',
    onShow: p => {                                 // #/mgaps/<market>/<tab>[/<SmartScout view>]: Back returns to where you came from
      const m = ['AU', 'AE'].includes(p[0]) ? p[0] : mk, t = TABS.includes(p[1]) ? p[1] : null;
      if (t === 'smartscout') ssv = SSV.some(([k]) => k === p[2]) ? p[2] : 'summary';
      setSeg($('mgMk'), m);
      if (D && m === mk && D.market === m) { if (t) setTab(t); render(); return; }
      if (m !== mk) { limit = 100; open.clear(); }
      mk = m; if (t) setTab(t); load();
    }});
  const TABS = ['overview', 'smartscout', 'products', 'shipped', 'niches', 'contacts', 'stock', 'live', 'missing', 'sellers'];
  const SSV = [['summary', 'Summary'], ['niches', 'Niches'], ['brands', 'Brands'], ['sellers', 'Sellers'], ['products', 'Products']];
  let ssv = 'summary';                               // the SmartScout tab's view on show
  /* "What this means", in plain words, for the tab on show ({m} is the market's name, {n} the most sellers a product can have) */
  const EXPLAIN = {
    overview: `This page sums up Amazon {m} on one screen: <b>what to sell</b> (products that sell well and have few sellers), <b>who ships
      in from abroad</b> (their buyers wait weeks, so stock kept here wins the sale), the <b>niches</b> with the most room and the <b>brands</b>
      worth writing to. The numbers come from <b>SmartScout</b> (an Amazon data service) on the date shown at the top, and from the app's own
      checks of Amazon. Click any card or bar to see the details.`,
    smartscout: `SmartScout's own data for Amazon {m}, as it was exported on the date shown: every <b>niche</b> (SmartScout's
      subcategories), every <b>brand</b> and <b>seller</b> making 1,000+ a month, and the <b>products</b> exported (the ones with few
      sellers, the best sellers, the new ones). The money is SmartScout's estimate of sales a month. Click a bar or a dot to see it in
      the table; click a column's name to sort by it. The other tabs use this data with the app's own checks of Amazon.`,
    products: `Every product here sells well on Amazon {m} and has few sellers on its listing, so a new seller can win the sale.
      <b>Strong</b>: proven demand and at most {n} sellers. <b>Worth a look</b>: less proven (fewer sales, or up to 10 sellers).
      <b>Sold a month</b> is SmartScout's estimate, or what Amazon shows as “bought in past month”. <b>Pay ≤</b> is the most you can pay your
      supplier per piece and still keep 20% after Amazon's fees, shipping and duty (a small parcel is assumed: check the size).`,
    shipped: `These sellers send their products to {m} from another country: Amazon's own stores in the USA, the UK, Germany or Japan
      (“Amazon Global Store”) and import resellers. Their buyers wait one to three weeks. Stock the same product here and you deliver in days,
      so you win the sale: find a supplier (<b>Alibaba</b>) or write to the brand (<b>Contact</b>). Famous brands are hidden, because only their
      owner may sell them new.`,
    niches: `A niche is a group of similar products (SmartScout's subcategory). Its <b>score</b> (0–100) adds up demand, how open it is, the
      reviews you have to beat, the price and the growth. It is a <b>gap</b> when most of it is shipped in from abroad, when Amazon itself sells
      most of it and the next brand is far behind, when only a few brands sell it, or when it is much bigger in the USA. SmartScout's numbers
      are from the day of the export: <b>Check</b> reads Amazon now to say whether the gap is still open.`,
    contacts: `Brands that could sell through you on Amazon {m}: their products are shipped in from abroad, they are big in the USA but not here,
      or they make one of the products with few sellers. <b>Read</b> gets the business name and address from the brand's Amazon seller page,
      <b>Email</b> opens a ready email in your own Gmail or Outlook (you press Send), and the status keeps track of who answered.`,
    stock: `Products from Product ideas (and SmartScout's best sellers) that sell fast but ran out of stock while few sellers had them:
      buyers want them and few can sell them right now.`,
    live: `The app reads the Best Sellers of your categories on Amazon {m} itself: which brands lead and who holds the buy boxes. Always fresh,
      unlike the SmartScout snapshot.`,
    missing: `Brands selling at least US$200K a month in the USA that SmartScout does not see selling in {m}: proven demand that nobody brings
      in yet. Sell the brand itself, or a similar product under your own brand.`,
    sellers: `The biggest sellers on Amazon {m} in SmartScout's data, who they are, and how much of the money a few of them take.`,
  };
  const TONE = {global_store: 'good', amazon_then_gap: 'good', thin: 'good', us_proven: 'accent', brand_locked: 'warn', crowded: 'weak'};
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
  let mk = 'AU', tab = null, D = null, sortBy = 'score', sortDir = 1, limit = 100;
  const setTab = t => {
    tab = t;
    $('mgTabs').querySelectorAll('[data-tab]').forEach(x => x.setAttribute('aria-selected', String(x.dataset.tab === t)));
    const more = $('mgMore'), inMore = more.querySelector(`[data-tab="${t}"]`);   // the less used views sit under More
    more.classList.toggle('on', !!inMore);
    $('mgMoreName').textContent = inMore ? ': ' + inMore.firstChild.textContent : '';
    more.open = false;
  };
  const open = new Set();
  const cur = () => MK_CUR[mk];
  const site = () => (D && D.site) || (ST.markets[mk] || {}).site || '';
  const m0 = v => v == null ? '–' : curMoney(v, cur(), 0);
  const day = t => t ? new Date(t * 1000).toLocaleDateString(undefined, {day: 'numeric', month: 'short', year: 'numeric'}) : '';
  const share = s => s == null ? '<span class="muted small">–</span>' : `<span class="ishare"><i style="width:${Math.round(s * 100)}%"></i></span> ${Math.round(s * 100)}%`;
  const parts = (obj, defs) => defs.map(d => ({...d, n: (obj || {})[d.key] || 0}));
  const searchUrl = q => site() + '/s?k=' + encodeURIComponent(q || '').replace(/%20/g, '+');
  const maxSellers = () => (ST.settings || {}).mg_max_sellers || 6;
  const HUNT = {hit: ['good', 'Few sellers'], busy: ['bad', 'Too many sellers'], no_demand: ['weak', 'No proven demand'],
    none: ['weak', 'Nobody sells it yet'], unread: ['weak', 'Not read']};

  seg($('mgMk'), v => step('mgaps', v, tab));
  document.addEventListener('click', e => { const m = $('mgMore'); if (m.open && !m.contains(e.target)) m.open = false; });   // More closes like a menu
  ['mgType', 'mgLive', 'mgRisky', 'mgAll'].forEach(id => $(id).addEventListener('change', () => { limit = 100; render(); }));
  $('mgOut').addEventListener('change', async e => {               // a contact's status: saved as soon as you pick it
    const sf = e.target.closest('[data-ssf]');                      // a SmartScout filter: ask for the matching page
    if (sf) { ssf[ssv][sf.dataset.ssf] = sf.type === 'checkbox' ? (sf.checked ? 1 : '') : sf.value; render(); return; }
    const s = e.target.closest('[data-cstatus]'); if (!s) return;
    try { await api('/api/contacts/save', {brand: s.dataset.cstatus, fields: {status: s.value}}); toast('Saved'); await loadContacts(true); }
    catch (err) { toast(err.message); }
  });
  $('mgMin').addEventListener('input', () => { limit = 100; render(); });
  $('mgFind').addEventListener('input', () => { limit = 100; render(); });
  $('mgTabs').addEventListener('click', e => {
    const b = e.target.closest('[data-tab]'); if (!b) return;
    step('mgaps', mk, b.dataset.tab);
  });

  let seq = 0, stock = null, stockAll = false, stockMk = '';
  async function loadStock() {
    try { stock = {...await api('/api/stock'), at: Date.now()}; } catch (e) { stock = {error: e.message, at: Date.now()}; }
    if (tab === 'stock') render();
  }
  async function load() {
    const my = ++seq, want = mk;                     // an answer for the market you left never replaces the newer one
    $('mgOut').innerHTML = '<div class="panel">' + busy('Loading…') + '</div>';
    let got;
    try { got = await api('/api/mgaps?market=' + want); } catch (e) { if (my === seq) $('mgOut').innerHTML = failPill(e.message); return; }
    if (my !== seq) return;
    D = got;
    if (!stock || stock.at < Date.now() - 60000) loadStock();
    const sel = $('mgType'), was = sel.value;
    sel.innerHTML = '<option value="">Every gap</option>' + D.types.filter(t => t.type !== 'crowded')
      .map(t => `<option value="${t.type}">${esc(t.label)}</option>`).join('');
    sel.value = was;
    if (!tab) setTab('overview');
    render();
  }

  const ended = () => {
    const until = (D.ss || {}).until, t = new Date();
    return !!until && until < `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`;
  };
  const okProd = p => !p.risk_why && !p.flags.length;            // what Products to sell shows until "risky" is ticked
  const okNiche = r => !r.risky && r.gap.gap;                     // what Niches shows by default
  const mc = v => v == null ? '–' : CUR_SYM[cur()] + ' ' + compact(v);   // money, short: "A$ 44.7M"
  const inMk = () => mk === 'AE' ? 'the UAE' : MK[mk];   // "stocked in the UAE", "into Australia"
  function head() {
    const b = D.baseline, until = (D.ss || {}).until;
    $('mgWhen').textContent = (b.as_of ? (ended() ? 'SmartScout snapshot of ' : 'SmartScout data of ') + day(b.as_of) : 'No SmartScout data yet') +
      (until && !ended() ? ' (subscription until ' + day(Date.parse(until + 'T00:00') / 1000) + ')' : '') +
      (D.last_check ? ' · last live check ' + ago(D.last_check) : '');
    $('mgExplainText').innerHTML = (EXPLAIN[tab] || EXPLAIN.overview).replace(/\{m\}/g, esc(MK[mk])).replace(/\{n\}/g, num(maxSellers()));
    const counts = {products: (D.products || []).filter(okProd).length, shipped: ((D.shipped || {}).sources || []).length,
      niches: D.niches.filter(okNiche).length, contacts: CT && ctMk === mk && !CT.error ? CT.rows.length : null, missing: (D.missing || []).length};
    $('mgTabs').querySelectorAll('[data-tab] .n').forEach(n => { const v = counts[n.parentNode.dataset.tab]; n.textContent = v ? num(v) : ''; });
    return '';
  }
  /* a tab's opening line: an icon and one or two plain sentences */
  const intro = (ic, html, extra = '') => `<div class="panel stackv"><div class="mg-intro">${icon(ic, '')}<p>${html}</p></div>${extra}</div>`;

  function render() {
    if (!D) return;
    try { draw(); } catch (e) {                     // a drawing problem shows as a message, never as an empty page
      console.error(e);
      $('mgOut').innerHTML = head() + '<div class="panel">' + failPill('This view could not be drawn: ' + (e.message || e)) + '</div>';
    }
  }
  function draw() {
    $('mgFilters').hidden = !['niches', 'products', 'shipped'].includes(tab);
    ['mgType', 'mgLive', 'mgMin', 'mgAll'].forEach(id => { $(id).closest('label').hidden = tab === 'products' || tab === 'shipped'; });
    if (!D.niches.length && !['live', 'stock', 'contacts', 'shipped'].includes(tab)) { $('mgOut').innerHTML = head() + importPanel(); return; }
    const body = tab === 'overview' ? overview() : tab === 'smartscout' ? ssTab() : tab === 'stock' ? stockTab() : tab === 'products' ? productsTab()
      : tab === 'niches' ? niches() : tab === 'live' ? live() : tab === 'shipped' ? shippedTab() : tab === 'contacts' ? contactsTab()
      : tab === 'missing' ? missing() : sellers();
    $('mgOut').innerHTML = head() + body;
    if (tab === 'smartscout') ssMount();
    if (tab === 'shipped' && shipFocus) {                // a bar clicked on the Overview: go to that seller
      const el = [...$('mgOut').querySelectorAll('[data-shipsec]')].find(x => x.dataset.shipsec === shipFocus);
      shipFocus = null;
      if (el) requestAnimationFrame(() => el.scrollIntoView({block: 'start', behavior: 'smooth'}));
    }
  }

  /* ---------- Overview: the market on one page, in plain words ---------- */
  const tile = (tb, label, value, sub, ic, tone = '') => `<a class="kpi ${tone}" href="#/mgaps/${mk}/${tb}"><span class="ic">${icon(ic)}</span>
    <span class="eyebrow">${esc(label)}</span><span class="v">${value}</span><span class="s">${sub}</span><span class="go">Open →</span></a>`;
  const sellersChip = n => n == null ? '<span class="muted small">–</span>'
    : n === 0 ? `<span class="pill warn" title="Nobody offers it right now: out of stock">${icon('warn')}No seller now</span>`
    : `<span class="pill ${n <= 3 ? 'good' : n <= maxSellers() ? 'accent' : 'weak'}">${n <= 3 ? icon('ok') : ''}${num(n)} seller${n === 1 ? '' : 's'}</span>`;
  const demandOf = p => p.units ?? p.bought ?? null;
  function overview() {
    const S = D.shipped || {}, src = S.sources || [], k = D.kpis;
    const prods = (D.products || []).filter(okProd), strong = prods.filter(p => p.tier === 'strong').length;
    const gaps = D.niches.filter(okNiche).sort((a, b) => b.score - a.score);
    if (!CT || ctMk !== mk) loadContacts();
    const brandsN = CT && ctMk === mk && !CT.error ? CT.rows.length : null;
    const tiles = `<div class="kpis mgo">
      ${tile('products', 'Products to sell', num(prods.length), prods.length ? `sell well and have few sellers · <b>${num(strong)}</b> strong` : 'press Refresh to look inside the niches', 'box', prods.length ? 'k-good' : '')}
      ${tile('shipped', 'Shipped in from abroad', S.share != null ? pct(S.share) : '–', src.length ? `of sellers' sales here go to ${num(src.length)} seller${src.length === 1 ? '' : 's'} abroad` : 'import the sellers export', 'globe', src.length ? 'k-warn' : '')}
      ${tile('niches', 'Niches with a gap', num(gaps.length), `among the ${num(D.niches.length)} best niches here`, 'chart')}
      ${tile('contacts', 'Brands to contact', brandsN == null ? '…' : num(brandsN), 'could sell through you here', 'mail')}
    </div>`;
    const top = prods.slice(0, 6), dmax = Math.max(1, ...top.map(p => demandOf(p) || 0));
    const prodCard = `<div class="panel mgo-card"><div class="hd"><div><h3>Best products to sell</h3><p>Sell well on Amazon ${esc(MK[mk])} and few sellers have them</p></div>
        <a class="go" href="#/mgaps/${mk}/products">See all ${num(prods.length)} →</a></div>
      <div class="mgo-list">${top.map(p => {
        const d = demandOf(p), url = site() + '/dp/' + p.asin;
        return `<div class="mgo-item"><div class="mgo-pic">${p.image ? `<img src="${esc(p.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : ''}</div>
          <div style="min-width:0"><a class="t" data-open="${esc(url)}" title="${esc(p.title)}">${esc(p.title)}</a>
            <div class="m">${d ? `<span class="dem" title="Sold a month"><i style="width:${Math.round(d / dmax * 90)}px"></i><span>${num(d)}/mo</span></span>` : ''}${sellersChip(p.sellers)}</div></div>
          <div class="r">${p.price != null ? '<b>' + esc(curMoney(p.price, cur(), 2)) + '</b>' : ''}${p.max_cogs_20 ? 'pay ≤ ' + esc(costMoney(p.max_cogs_20)) : ''}</div></div>`;
      }).join('') || '<p class="small muted">None yet: press <b>Refresh AU &amp; UAE</b>.</p>'}</div></div>`;
    const nicheCard = `<div class="panel mgo-card"><div class="hd"><div><h3>Niches with the most room</h3><p>Score out of 100 · what makes it a gap · money spent a month</p></div>
        <a class="go" href="#/mgaps/${mk}/niches">See all ${num(gaps.length)} →</a></div>
      <div class="mgo-list">${gaps.slice(0, 6).map(r => `<div class="mgo-item nopic"><div style="min-width:0">
          <span class="t" title="${esc(r.name)}">${esc(r.name)}</span>
          <div class="m"><span class="pill ${TONE[r.gap.type] || 'weak'}" title="${esc(r.gap.means)}">${esc(r.gap.label)}</span><span>${esc(r.category || '')}</span></div></div>
          <div class="r" style="display:flex;gap:14px;align-items:center"><span><b>${mc(r.revenue)}</b>a month</span>
            <div class="score" title="Score ${r.score} of 100"><b>${r.score}</b><i><span style="width:${r.score}%"></span></i></div></div></div>`).join('')
        || '<p class="small muted">No niche with a gap.</p>'}</div></div>`;
    const brandCard = `<div class="panel mgo-card"><div class="hd"><div><h3>Brands to contact first</h3><p>Shipped in from abroad, missing here, or behind a product with few sellers</p></div>
        <a class="go" href="#/mgaps/${mk}/contacts">Open the list →</a></div>
      <div class="mgo-list">${brandsN == null ? busy('Loading…') : CT.rows.slice(0, 6).map(r => {
        const [tone, label] = cKind[r.kind] || cKind.saved;
        return `<div class="mgo-item nopic"><div style="min-width:0"><span class="t">${esc(r.brand)}</span>
          <div class="m"><span class="pill ${tone}">${esc(label)}</span><span title="${esc(r.reason || '')}" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:44ch">${esc((r.reason || '').replace(/^[^:]{1,40}:\s*/, ''))}</span></div></div>
          <div class="r">${r.revenue ? '<b>' + (r.kind === 'missing' ? esc(CUR_SYM.USD + ' ' + compact(r.revenue)) : mc(r.revenue)) + '</b>a month' + (r.kind === 'missing' ? ' in the USA' : '') : ''}</div></div>`;
      }).join('')}</div></div>`;
    return tiles + `<div class="mgo-grid">${src.length ? `<div class="panel mgo-card"><div class="hd"><div><h3>Who ships into ${esc(inMk())} from abroad</h3>
        <p>Share of all sellers' sales on Amazon ${esc(MK[mk])} · click a seller for its products</p></div><a class="go" href="#/mgaps/${mk}/shipped">Details →</a></div>
        ${shipChart()}</div>` : ''}${prodCard}${nicheCard}${brandCard}</div>${dataCard()}`;
  }
  /* who gets the sellers' money: Amazon itself, sellers abroad, everyone else (one stacked bar), then each seller abroad (bars) */
  function shipChart() {
    const S = D.shipped || {}, src = S.sources || [], sh = (D.sellers || {}).shares || {};
    const abroad = S.share || 0, amazon = sh.amazon || 0, other = Math.max(0, 1 - amazon - abroad);
    const money = src.reduce((a, e) => a + (e.revenue || 0), 0), max = Math.max(1e-9, ...src.map(e => e.share || 0));
    const segs = [{label: 'Amazon ' + MK[mk] + ' itself', n: amazon, color: 'var(--s-demand)'},
      {label: 'Shipped in from abroad', n: abroad, color: 'var(--s-overseas)'}, {label: 'All other sellers', n: other, color: 'var(--s-other)'}];
    return `<div class="mgo-hero"><b class="big">${pct(abroad)}</b><span>of what shoppers spend with sellers on Amazon ${esc(MK[mk])} goes to
        ${num(src.length)} seller${src.length === 1 ? '' : 's'} that ship from abroad: about <b>${mc(money)}</b> a month.</span></div>
      <div>${originBar(segs.map(p => ({...p, n: Math.round(p.n * 1000) / 10, label: p.label + ' (%)'})), 100, 12)}
        <div class="legend">${segs.map(p => `<span><i style="background:${p.color}"></i>${esc(p.label)} <b class="tnum">${pct(p.n)}</b></span>`).join('')}</div></div>
      <div class="vbars">${src.map(e => `<button type="button" class="vbar" data-shipgo="${esc(e.seller)}" title="${esc(e.seller)}: ${esc(pct(e.share, 1))} of sellers' sales, ${esc(m0(e.revenue))} a month. Click to see what it sells">
          <span class="lab">${e.country ? cflag(e.country, '') : ''}<span>${esc(e.seller)}</span></span>
          <span class="trk"><i style="width:${(e.share || 0) / max * 52}%;--vb:var(--s-overseas)"></i><em>${pct(e.share, 1)} · ${mc(e.revenue)}/mo</em></span></button>`).join('')}</div>
      <div class="mgo-take">Their buyers wait <b>one to three weeks</b>. The same product stocked in ${esc(inMk())} arrives in days, so it wins the sale:
        look at what they sell, then find a supplier or write to the brand.</div>`;
  }
  /* where the numbers come from, and how fresh they are */
  function dataCard() {
    const ss = D.ss || {}, c = ss.counts || {}, cv = ss.curve, b = D.baseline;
    const facts = [c.brands && num(c.brands) + ' brands', c.sellers && num(c.sellers) + ' sellers', c.subcats && num(c.subcats) + ' niches (subcategories)',
      c.products && num(c.products) + ' products', c.offers && num(c.offers) + ' products of sellers abroad', c.us_twins && num(c.us_twins) + ' of them also read in the USA'].filter(Boolean);
    return `<div class="panel"><div class="mgo-data">
      <div><span class="eyebrow">${icon('file')}SmartScout ${ended() ? 'snapshot' : 'data'}</span>
        <span>${b.as_of ? '<b>' + esc(day(b.as_of)) + '</b>: ' + esc(facts.join(' · ')) : 'Nothing imported yet: press <b>Import SmartScout files</b>.'}</span>
        <span class="small muted">${ss.until ? (ended() ? 'The subscription ended ' : 'Subscription until ') + esc(day(Date.parse(ss.until + 'T00:00') / 1000)) + '.' : 'Set the subscription’s last day in Settings → Gap Finder.'}</span></div>
      <div><span class="eyebrow">${icon('chart')}Sales estimates</span>
        <span>${cv && cv.status === 'calibrated' ? `<b>Tuned to SmartScout</b>: ${num(cv.ss || 0)} of its products taught the app how a sales rank turns into sales here. This stays after the subscription ends.`
          : 'Still using default values: import SmartScout product exports to tune them.'}</span></div>
      <div><span class="eyebrow">${icon('refresh')}Live checks of Amazon</span>
        <span>${D.last_check ? 'Last one <b>' + esc(ago(D.last_check)) + '</b>.' : 'None yet.'} ${D.auto ? 'A few niches are re-checked every day automatically.' : 'Automatic checks are off (<a href="#/settings">Settings</a>).'}</span>
        <span class="small muted">Press <b>Refresh AU &amp; UAE</b> to check now.</span></div></div></div>`;
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
    return intro('chart', `<b>${num(rows.length)} niche${rows.length === 1 ? '' : 's'}</b> shown, best first. The <b>score</b> (out of 100) is how much
      room a niche leaves you; the coloured label says <b>why</b> it is a gap; <b>Who sells most of it</b> shows who takes the money today
      (orange = shipped in from abroad). Press <b>Check</b> to read Amazon now and see if the gap is still open.`) +
      `<div class="panel"><div class="tablewrap"><table class="mtx mgtable"><thead><tr>${th('name', 'Niche', '', 'mtx-prod')}
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
    const ib = (attr, ic, tip) => `<button class="btn slim ico" ${attr} title="${esc(tip)}" aria-label="${esc(tip)}">${icon(ic)}</button>`;
    const acts = `<button class="btn slim" data-check="${esc(r.key)}" title="Read page 1, the top buy boxes and the products inside it on Amazon ${esc(MK[mk])} now">${icon('refresh')}Check</button>` +
      ib(`data-open="${esc(searchUrl(phrase))}"`, 'external', 'Search it on Amazon ' + MK[mk]) +
      (L && L.phrase ? `<a class="btn slim ico" href="#/k/${mk}/${encodeURIComponent(L.phrase)}" title="Keyword report for “${esc(L.phrase)}”" aria-label="Keyword report">${icon('chart')}</a>` : '') +
      ib(`data-watch="${esc(r.key)}"`, 'eye', 'Watch: check this phrase every day') +
      ib(`data-board="${esc(r.key)}"`, 'board', 'Add to your product board') +
      moreBtn(r.key, open.has(r.key));
    const also = (g.also || []).map(a => `<span class="pill line" title="${esc(a.why)}">${esc(a.label)}</span>`).join(' ');
    const sc = r.parts, tip = `Demand ${pct(sc.demand)} · openness ${pct(sc.open)} · reviews ${pct(sc.barrier)} · price ${pct(sc.price)} · growth ${pct(sc.momentum)}`;
    const who = parts(r.shares, WHO);
    const top = r.top[0];
    return `<tr>${prodCell({url: searchUrl(phrase), title: r.name, category: '', flags: r.flags}, pills, acts)}
      <td title="${esc(tip)}"><div class="score"><b>${r.score}</b><i><span style="width:${r.score}%"></span></i></div><span class="tsub">${r.us_ratio ? 'USA ' + num(r.us_ratio, 1) + '×' : ''}</span></td>
      <td class="gwhy"><span class="pill ${TONE[g.type] || 'weak'}" title="${esc(g.means)}">${icon(VICON[TONE[g.type]] || 'dot')}${esc(g.label)}</span>
        <div class="small">${esc(g.why)}</div>${also ? '<div style="margin-top:4px">' + also + '</div>' : ''}</td>
      <td><b>${m0(r.revenue)}</b><span class="tsub">${num(r.brands)} brand${r.brands === 1 ? '' : 's'} · ${r.avg_sellers != null ? num(r.avg_sellers, 1) + ' sellers each' : ''}</span>
        ${r.growth != null ? `<span class="tsub">${r.growth >= 0 ? '+' : ''}${pct(r.growth)} in 12 months</span>` : ''}
        ${r.ss ? `<span class="tsub" title="SmartScout's subcategory table">SmartScout: Amazon ${pct(r.ss.amazon_share)} · Chinese sellers ${pct(r.ss.chinese_share)}${r.ss.asins != null ? ' · ' + num(r.ss.asins) + ' listings' : ''}</span>` : ''}</td>
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
    const hits = (L.hunt || []).filter(h => h.hit).length;
    const hunt = (L.hunt || []).length ? `<div style="margin-top:4px"><span class="pill ${hits ? 'good' : 'weak'}">${hits ? icon('ok') : ''}${num(hits)} of ${num(L.hunt.length)} products with few sellers</span></div>` : '';
    return `<span class="pill ${t}" title="${esc((D.verdicts.find(v => v.verdict === L.verdict) || {}).means || '')}">${icon(VICON[t])}Niche: ${esc(L.label)}</span>${hunt}
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
      ${(L.hunt || []).length ? `<div style="grid-column:1/-1"><h4>Products inside this niche</h4><div class="tablewrap"><table class="itable"><thead><tr><th>What shoppers type</th><th>Verdict</th><th class="r">Sellers</th><th class="r">Bought last month</th><th>Why</th></tr></thead><tbody>
        ${L.hunt.map(h => `<tr><td><a data-open="${esc(searchUrl(h.phrase))}">${esc(h.phrase)}</a></td><td><span class="pill ${HUNT[h.verdict][0]}">${esc(HUNT[h.verdict][1])}</span></td>
          <td class="r">${h.sellers != null ? num(h.sellers) : '–'}</td><td class="r">${h.demand ? num(h.demand) + '+' : '–'}</td><td class="small">${esc(h.why)}</td></tr>`).join('')}</tbody></table></div></div>` : ''}
      ${tops ? `<div style="grid-column:1/-1"><h4>Top listings now</h4><div class="tablewrap"><table class="itable"><thead><tr><th>Listing</th><th>Ships</th><th>Buy box</th><th class="r">Bought</th><th class="r">Price</th></tr></thead><tbody>${tops}</tbody></table></div></div>` : ''}
    </div></td></tr>`;
  }

  /* ---------- Out of stock: booming and brand-new products of Product ideas that ran out with few sellers ---------- */
  const SMK = ['US', 'AU', 'AE'];
  const SSTATE = {out: ['warn', 'Out of stock'], in: ['line', 'In stock'], missing: ['accent', 'Not there yet'], unread: ['weak', 'Not read']};
  const short = t => t ? new Date(t * 1000).toLocaleDateString(undefined, {day: 'numeric', month: 'short'}) : '';
  function stockCell(r, m) {
    const s = r.markets[m], [tone, label] = SSTATE[s.state] || SSTATE.unread;
    const sellers = s.sellers != null && s.state !== 'missing' ? ` · ${num(s.sellers)} seller${s.sellers === 1 ? '' : 's'}` : '';
    const url = (ST.markets[m] || {}).site ? ST.markets[m].site + '/dp/' + r.asin : '';
    return `<td><span class="pill ${s.fits ? 'good' : tone}" title="${esc(s.why || '')}">${s.fits ? icon('ok') : ''}${esc(label)}${esc(sellers)}</span>
      ${s.state === 'out' ? `<span class="tsub">${s.since ? 'out since ' + short(s.since) : ''}${s.last_in ? ' · in stock ' + short(s.last_in) : ''}${s.rank ? ' · rank #' + num(s.rank) : ''}</span>` : ''}
      ${s.price != null && s.state !== 'missing' ? `<span class="tsub">${esc(curMoney(s.price, MK_CUR[m], 2))}</span>` : ''}
      ${url && s.state !== 'missing' && s.state !== 'unread' ? `<a class="tsub" data-open="${esc(url)}">open</a>` : ''}</td>`;
  }
  function stockTab() {
    if (!stock) { loadStock(); return '<div class="panel">' + busy('Loading…') + '</div>'; }
    if (stock.error) return '<div class="panel">' + failPill(stock.error) + '</div>';
    const q = $('mgFind').value.trim().toLowerCase();
    const rows = stock.rows.filter(r => (stockAll || r.fits) && (!stockMk || (stockAll ? r.markets[stockMk].state !== 'unread' : r.out.includes(stockMk)))
      && (!q || (r.title || r.asin).toLowerCase().includes(q)));
    const seg = ['', ...SMK].map(m => `<button type="button" data-smk="${m}" aria-pressed="${m === stockMk}">${m ? esc(MK[m]) : 'All markets'}</button>`).join('');
    const intro = `<div class="panel stackv"><div class="row" style="justify-content:space-between">
        <div class="row"><div class="seg" role="group" aria-label="Market">${seg}</div>
          <label class="small"><input type="checkbox" data-sall ${stockAll ? 'checked' : ''}> Show every watched product</label></div>
        <div class="row"><span class="small">${stock.last ? 'last read ' + ago(stock.last) : 'not read yet'}${stock.auto ? ' · every day automatically' : ''}</span>
          <button class="btn primary" data-srun>${icon('refresh')}Check stock now</button></div></div>
      <p class="small">Watching <b>${num(stock.booming)}</b> booming (sales rank up 300%+) and <b>${num(stock.new)}</b> brand-new (New Releases top 30) products
        from Product ideas of the last 2 weeks${stock.smartscout ? `, and <b>${num(stock.smartscout)}</b> of SmartScout's best sellers with 3 sellers or fewer (re-read here so they stay current)` : ''}, in the USA, Australia and the UAE. <b>${num(stock.fits)}</b> ran out of stock recently with
        ${num(stock.max_sellers)} sellers or fewer (change it in Settings → Gap Finder): buyers want it and few can sell it now.
        "Not there yet" means the product is not on that Amazon at all.</p></div>`;
    if (!stock.rows.length) return intro + `<div class="panel empty"><h2>No booming or brand-new products yet</h2><p>Run <b>Product ideas</b>
      (or let the automatic refresh read Movers &amp; Shakers and New Releases): its booming and brand-new products are watched here.</p></div>`;
    return intro + `<div class="panel"><div class="tablewrap"><table class="mtx"><thead><tr><th class="mtx-prod">Product</th><th>Why it is watched</th>
      ${SMK.map(m => `<th>${mkFlag(m, true)}</th>`).join('')}</tr></thead><tbody>
      ${rows.map(r => {
        const m0 = r.out[0] || r.idea.market, url = ((ST.markets[m0] || {}).site || '') + '/dp/' + r.asin;
        const words = (r.title || '').split(/\s+/).slice(0, 6).join(' ');
        const acts = `<button class="btn slim" data-open="${esc(url)}">${icon('external')}Amazon</button>` +
          (words ? `<button class="btn slim" data-open="${esc(alibabaUrl(words))}" title="Find suppliers">Alibaba</button>` : '') +
          shortlistBtn(`data-sboard="${esc(r.asin)}"`);
        return `<tr>${prodCell({image: r.image, url, title: r.title || r.asin, category: r.asin + (r.idea.category ? ' · ' + r.idea.category : ''), flags: []},
            r.fits ? `<span class="pill good">${icon('ok')}Out of stock, few sellers: ${r.out.map(m => esc(MK_SHORT[m] || m)).join(', ')}</span>` : '', acts)}
          <td><span class="pill ${r.idea.kind === 'booming' ? 'accent' : 'line'}">${esc(r.idea.why)}</span><span class="tsub">spotted in ${esc(MK[r.idea.market])} · ${ago(r.idea.last_seen)}</span></td>
          ${SMK.map(m => stockCell(r, m)).join('')}</tr>`;
      }).join('') || `<tr><td colspan="5" class="empty">${stock.read ? 'No product ran out recently with few sellers' + (stockMk ? ' in ' + esc(MK[stockMk]) : '') + '. Tick <b>Show every watched product</b> to see them all.' : 'Press <b>Check stock now</b>: the watched products have not been read yet.'}</td></tr>`}
      </tbody></table></div></div>`;
  }

  /* ---------- Products to sell ---------- */
  let pTier = '';
  const tierOf = p => p.source === 'stock' ? 'stock' : p.tier === 'strong' || p.tier === 'look' ? p.tier : 'other';
  function productsTab() {
    const all = D.products || [], risky = $('mgRisky').checked, q = $('mgFind').value.trim().toLowerCase();
    const base = all.filter(p => (risky || okProd(p)) && (!q || (p.title + ' ' + (p.niche || '') + ' ' + (p.brand || '')).toLowerCase().includes(q)));
    const rows = base.filter(p => !pTier || tierOf(p) === pTier);
    const hidden = risky ? 0 : all.filter(p => !okProd(p)).length;
    if (!all.length) return `<div class="panel empty"><h2>No products yet</h2><p>Press <b>Refresh AU &amp; UAE</b>, or <b>Check now</b> on a niche you like
      (Niches tab). Inside each niche the app searches the longer phrases shoppers type there, for example “leg rest pillow” inside Leg &amp; Foot
      Supports, and counts how many listings sell exactly that product and how many were bought last month. A product with proven demand and
      ${num(maxSellers())} sellers or fewer shows up here.</p></div>`;
    const n = t => base.filter(p => tierOf(p) === t).length;
    const segs = [['', 'All', base.length], ['strong', 'Strong', n('strong')], ['look', 'Worth a look', n('look')], ['stock', 'Out of stock now', n('stock')], ['other', 'Other', n('other')]]
      .filter(([v, , c]) => !v || c).map(([v, l, c]) => `<button type="button" data-ptier="${v}" aria-pressed="${pTier === v}">${esc(l)} <span class="muted">${num(c)}</span></button>`).join('');
    const ssDay = (all.find(p => p.source === 'smartscout') || {}).as_of;
    const dmax = Math.max(1, ...rows.slice(0, limit).map(p => demandOf(p) || 0));
    return intro('box', `<b>${num(base.length)} products</b> sell well on Amazon ${esc(MK[mk])} and have few sellers on their listing, so a new seller
        can win the sale. <b>Strong</b> = proven demand and at most ${num(maxSellers())} sellers; <b>Worth a look</b> = less proven.
        ${ssDay ? `Most come from SmartScout (${esc(day(ssDay))}); the rest from the app's own checks of Amazon.` : ''}
        <b>Pay ≤</b> is the most you can pay your supplier per piece and still keep 20%.
        ${hidden ? `<span class="muted">${num(hidden)} more have warnings (electrical, kids, chemicals, clothing…): tick <b>Show phones, books and other risky niches</b> to see them.</span>` : ''}`,
      `<div class="seg" role="group" aria-label="Show">${segs}</div>`) +
      `<div class="panel"><div class="tablewrap"><table class="mtx"><thead><tr><th class="mtx-prod">Product</th><th title="SmartScout's estimate, or what Amazon shows as bought in the past month">Sold a month</th>
        <th title="Sellers offering this listing now">Sellers</th><th>Price · pay ≤</th><th>Why it looks good</th><th>Who sells it now</th></tr></thead><tbody>
      ${rows.slice(0, limit).map(p => {
        const url = site() + '/dp/' + p.asin, d = demandOf(p);
        const tierPill = p.source === 'stock' ? `<span class="pill warn">${icon('warn')}Out of stock now</span>`
          : p.source === 'gapfinder' ? `<span class="pill good">${icon('globe')}Gap Finder</span>`
          : p.tier === 'strong' ? `<span class="pill good">${icon('ok')}Strong</span>` : p.tier === 'look' ? '<span class="pill accent">Worth a look</span>'
          : p.verdict ? `<span class="pill ${VTONE[p.verdict] || 'weak'}">${esc((D.verdicts.find(v => v.verdict === p.verdict) || {}).label || p.verdict)}</span>`
          : p.source === 'best' ? '<span class="pill accent">Best Seller</span>' : '';
        const from = p.source === 'smartscout' ? `<span class="small muted" title="SmartScout export of ${esc(day(p.as_of))}">SmartScout</span>`
          : p.source === 'hunt' && p.phrase ? `<span class="small muted" title="Found by searching Amazon for this phrase">found live: “${esc(p.phrase)}”</span>` : '';
        const pills = tierPill + from + (p.niche ? ` <span class="pill line">${esc(p.niche)}</span>` : '');
        const words = p.title.split(/\s+/).slice(0, 6).join(' ');
        const acts = `<button class="btn slim" data-open="${esc(url)}">${icon('external')}Amazon</button>` +
          `<button class="btn slim" data-open="${esc(alibabaUrl(words))}" title="Find suppliers">Alibaba</button>` +
          (p.phrase ? `<a class="btn slim" href="#/k/${mk}/${encodeURIComponent(p.phrase)}">Report</a>` : '') +
          shortlistBtn(`data-pboard="${esc(p.asin)}"`);
        return `<tr>${prodCell({image: p.image, url, title: p.title, category: p.asin, flags: p.flags.concat(p.risk_why ? [p.risk_why] : [])}, pills, acts)}
          <td>${d ? `<span class="dem"><i style="width:${Math.round(d / dmax * 70)}px"></i><span>${num(d)}</span></span>` : '<span class="muted small">–</span>'}</td>
          <td>${sellersChip(p.sellers)}</td>
          <td>${p.price != null ? '<b>' + curMoney(p.price, cur(), 2) + '</b>' + fxSub(p.price, mk) : '<span class="muted small">no price read</span>'}
            ${p.max_cogs_20 ? `<span class="tsub buy">pay ≤ ${esc(costMoney(p.max_cogs_20))}</span>` : p.price != null ? '<span class="tsub">no 20% margin at this price</span>' : ''}</td>
          <td><ul class="small" style="margin:0;padding-left:16px">${p.why.map(w => '<li>' + esc(w) + '</li>').join('')}</ul>
            ${p.score != null ? `<span class="tsub">score ${p.score} of 100</span>` : ''}
            ${p.rating ? `<span class="tsub">${num(p.rating, 1)}★${p.reviews != null ? ' · ' + num(p.reviews) + ' reviews' : ''}</span>` : p.reviews != null ? `<span class="tsub">${num(p.reviews)} reviews</span>` : ''}</td>
          <td>${p.source === 'hunt' ? (p.listings || []).map(l => `<div class="small"><a data-open="${esc(site() + '/dp/' + l.asin)}">${esc((l.title || l.asin).slice(0, 48))}</a>
              <span class="muted">${l.bought ? num(l.bought) + '+ · ' : ''}${l.origin === 'overseas' ? 'from abroad' : l.speed === 'fast' ? 'stock here' : 'local'}${l.days != null ? ' · ' + l.days + ' d' : ''}</span></div>`).join('')
            : p.source === 'smartscout' ? (p.brand ? `<b>${esc(p.brand)}</b><span class="tsub">the brand</span>` : '–') +
              (p.us && p.us.units ? `<span class="tsub" title="SmartScout, the same ASIN on Amazon USA">USA: ~${num(p.us.units)}/mo${p.us.price ? ' at ' + esc(curMoney(p.us.price, 'USD', 0)) : ''}${p.us.sellers != null ? ' · ' + num(p.us.sellers) + ' sellers' : ''}</span>` : '')
            : esc(p.seller || '–') + (p.days != null ? `<span class="tsub">${p.origin === 'overseas' ? 'from abroad' : 'local'} · ${p.days} days</span>` : '')}</td></tr>`;
      }).join('') || '<tr><td colspan="6" class="empty">Nothing to show with these filters.</td></tr>'}</tbody></table></div>
      ${rows.length > limit ? `<div class="row" style="justify-content:center;margin-top:8px"><button class="btn" data-more-rows>Show ${num(Math.min(100, rows.length - limit))} more of ${num(rows.length - limit)}</button></div>` : ''}</div>`;
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

  /* ---------- Shipped in: Amazon's Global Store and import resellers, the brands they dominate, their products ---------- */
  let shipFamous = false, shipFocus = null;
  const shipOpen = new Set();                            // sellers whose every product is on show
  const ROLE = {global_store: "Amazon's Global Store", reseller: 'Import reseller', seller: 'Seller', amazon: 'Amazon', unknown: ''};
  const OWN = {US: 'US', GB: 'UK', DE: 'German', JP: 'Japanese', FR: 'French', IT: 'Italian', ES: 'Spanish', IN: 'Indian', CA: 'Canadian'};
  function shippedTab() {
    const S = D.shipped || {}, src = S.sources || [];
    if (!src.length) return `<div class="panel empty"><h2>Nothing shipped in yet</h2><p>Import SmartScout's sellers and brands exports of ${esc(MK[mk])},
      and the Offers of Amazon US, UK, Germany and Japan (and the UAE's resellers): who ships in from abroad shows here.</p></div>`;
    const q = $('mgFind').value.trim().toLowerCase(), risky = $('mgRisky').checked;
    const hit = t => !q || (t || '').toLowerCase().includes(q);
    const max = Math.max(1e-9, ...src.map(e => e.share || 0));
    const top = `<div class="panel mgo-card"><div class="hd"><div><h3>Who ships into ${esc(inMk())} from abroad</h3>
        <p>SmartScout sellers export, ${esc(day(D.baseline.sellers_as_of))} · click a seller to jump to it</p></div>
        <label class="small"><input type="checkbox" data-shipfamous ${shipFamous ? 'checked' : ''}> Show famous brands</label></div>${shipChart()}</div>`;
    return top + src.map(e => {
      const brands = e.brands.filter(b => (shipFamous || !b.famous) && hit(b.brand + ' ' + (b.subcategory || '')));
      const famousN = e.brands.filter(b => b.famous).length;
      const prods = e.products.filter(p => (shipFamous || !p.famous) && (risky || !p.risk_why) && hit((p.title || '') + ' ' + (p.brand || '') + ' ' + (p.subcategory || '')));
      const shown = shipOpen.has(e.seller) ? prods : prods.slice(0, 12);
      const who = e.role === 'global_store' ? `Amazon's own ${OWN[e.country] || ''} store: it sells here and ships each order from abroad.`
        : e.role === 'reseller' ? `A reseller that brings products in from abroad and sells them on Amazon ${MK[mk]}.` : '';
      return `<div class="panel stackv" data-shipsec="${esc(e.seller)}">
        <div class="shiphead"><div><h3>${e.country ? cflag(e.country, '') : ''}${esc(e.seller)} <span class="pill line">${esc(ROLE[e.role] || e.role)}</span></h3>
            <div class="small muted" style="margin-top:4px">${esc(who)}${e.dominates ? ` The main seller of <b>${num(e.dominates)}</b> brands here.` : ''}${e.asins ? ` ${esc(compact(e.asins))} listings.` : ''}
              ${e.seller_id ? ` <a data-open="${esc(site() + '/sp?seller=' + e.seller_id)}">Its seller page</a>` : ''}</div></div>
          <div class="shipmeter"><div class="row" style="justify-content:space-between"><b>${mc(e.revenue)} a month</b><span>${pct(e.share, 1)} of sellers' sales</span></div>
            <span class="trk" role="img" aria-label="${esc(pct(e.share, 1))} of sellers' sales"><i style="width:${(e.share || 0) / max * 100}%"></i></span></div></div>
        <div><h4 style="margin-bottom:8px">Its best-selling products here</h4>
          ${shown.length ? `<div class="shipcards">${shown.map(p => {
            const url = site() + '/dp/' + p.asin;
            return `<div class="shipcard"><div class="pic">${p.image ? `<img src="${esc(p.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : ''}</div>
              <div class="body"><a class="t clamp2" data-open="${esc(url)}" title="${esc(p.title || p.asin)}">${esc(p.title || p.asin)}</a>
                <span class="sub">${esc([p.brand, p.subcategory].filter(Boolean).join(' · '))}</span>
                <div class="row" style="gap:4px 10px"><b>${mc(p.revenue)}</b><span class="small muted">a month</span>${p.price != null ? `<span class="small">${esc(curMoney(p.price, cur(), 0))}</span>` : ''}${p.sellers != null ? sellersChip(p.sellers) : ''}</div>
                ${p.us && p.us.units ? `<span class="sub" title="SmartScout, the same product on Amazon USA">USA: ~${num(p.us.units)} sold a month${p.us.sellers != null ? ' · ' + num(p.us.sellers) + ' sellers' : ''}</span>` : ''}
                ${p.risk_why ? `<span><span class="pill warn">${icon('warn')}${esc(p.risk_why)}</span></span>` : ''}</div>
              <div class="acts">${p.title ? `<button class="btn slim" data-open="${esc(alibabaUrl(p.title.split(/\s+/).slice(0, 6).join(' ')))}" title="Find suppliers">Alibaba</button>` : ''}
                ${p.brand ? `<button class="btn slim" data-cadd="${esc(p.brand)}" data-creason="${esc('Shipped in: most sales here go through ' + e.seller)}" title="Add ${esc(p.brand)} to Brands to contact">${icon('mail')}Contact</button>` : ''}
                ${shortlistBtn(`data-shipboard="${esc(p.asin)}"`)}</div></div>`;
          }).join('')}</div>
          ${prods.length > 12 ? `<div class="row" style="justify-content:center;margin-top:8px"><button class="btn" data-shipall="${esc(e.seller)}">${shipOpen.has(e.seller) ? 'Show fewer' : 'Show all ' + num(prods.length)}</button></div>` : ''}`
          : '<p class="small muted">No products to show: import its Offers export, or tick <b>Show famous brands</b>.</p>'}</div>
        ${brands.length ? `<details><summary class="small" style="cursor:pointer;font-weight:600">The ${num(brands.length)} brands it sells most of</summary>
          <div class="tablewrap" style="margin-top:8px"><table class="itable"><thead><tr><th>Brand</th><th class="r">a month</th><th></th></tr></thead><tbody>
          ${brands.map(b => `<tr><td><b>${esc(b.brand)}</b>${b.famous ? ' <span class="pill weak">famous</span>' : ''}<div class="sub">${esc(b.subcategory || '')}</div></td>
            <td class="r">${m0(b.revenue)}</td>
            <td><button class="btn slim ico" data-open="${esc(searchUrl(b.brand))}" title="Search ${esc(b.brand)} on Amazon ${esc(MK[mk])}" aria-label="Search on Amazon">${icon('external')}</button>
              <button class="btn slim" data-cadd="${esc(b.brand)}" data-creason="${esc('Shipped in: most sales here go through ' + e.seller)}">Contact</button></td></tr>`).join('')}
          </tbody></table></div></details>`
          : famousN ? `<p class="small muted">Its biggest brands here are famous names (${num(famousN)}), which only their owner may sell new: tick <b>Show famous brands</b> to list them.</p>` : ''}</div>`;
    }).join('');
  }

  /* ---------- Contacts: brands worth writing to, and what you know about each ---------- */
  let CT = null, ctMk = '', cEdit = null, cFilter = '';
  let ctBusy = '';
  async function loadContacts(force) {
    if (!force && CT && ctMk === mk && CT.at > Date.now() - 60000) return;
    if (!force && ctBusy === mk) return;                 // already asked (the Overview draws more than once while it loads)
    const want = ctBusy = mk;
    let got;
    try { got = {...await api('/api/contacts?market=' + want), at: Date.now()}; } catch (e) { got = {error: e.message, at: Date.now()}; }
    ctBusy = '';
    if (want !== mk) return;                             // the market changed meanwhile: its own load follows
    CT = got; ctMk = want;
    if (tab === 'contacts' || tab === 'overview') render();
  }
  const cKind = {shipped: ['warn', 'Shipped in'], missing: ['accent', 'Not here yet'], product: ['good', 'Product'], saved: ['line', 'Saved']};
  function contactsTab() {
    if (!CT || ctMk !== mk) { loadContacts(); return '<div class="panel">' + busy('Loading…') + '</div>'; }
    if (CT.error) return '<div class="panel">' + failPill(CT.error) + '</div>';
    const q = $('mgFind').value.trim().toLowerCase();
    const rows = CT.rows.filter(r => (!cFilter || (cFilter === 'saved' ? r.saved : (r.saved || {}).status === cFilter || (!r.saved && cFilter === 'to_contact')))
      && (!q || ((r.brand || '') + ' ' + (r.reason || '') + ' ' + JSON.stringify(r.saved || {})).toLowerCase().includes(q)));
    const counts = CT.counts || {};
    const segs = [['', 'All'], ['saved', 'Saved'], ...CT.statuses.map(s => [s.status, s.label + (counts[s.status] ? ' (' + counts[s.status] + ')' : '')])]
      .map(([v, l]) => `<button type="button" data-cfilter="${v}" aria-pressed="${cFilter === v}">${esc(l)}</button>`).join('');
    const unread = rows.filter(r => !(r.saved && (r.saved.address || r.saved.email || r.saved.phone)) && (r.asin || (r.saved || {}).seller_id)).slice(0, 10);
    const intro = `<div class="panel stackv"><div class="row" style="justify-content:space-between">
        <div class="seg" role="group" aria-label="Show">${segs}</div>
        <div class="row">${unread.length ? `<button class="btn" data-cread-all title="Read the brands' own sellers on Amazon: business name, address, and a phone or email when Amazon shows them">${icon('search')}Read details for ${num(unread.length)}</button>` : ''}
          <button class="btn" data-ctemplate>${icon('mail')}Email text</button></div></div>
      <div class="steps">
        <div><b>Pick a brand</b><span>Its products are shipped in from abroad, it is big in the USA but not here, or it makes a product with few sellers.</span></div>
        <div><b>Get its details</b><span><b>Read</b> takes the business name and address (and a phone or email when shown) from its Amazon seller page. <b>Edit</b> adds what you find.</span></div>
        <div><b>Write to it</b><span><b>Email</b> opens a ready email in your ${esc(((ST.settings || {}).mail_web || 'auto') === 'outlook' ? 'Outlook' : 'Gmail')}: you read it and press Send.</span></div>
        <div><b>Keep track</b><span>Set the status: contacted, replied or not interested.</span></div></div>
      ${cEdit === '#template' ? `<div class="stackv"><label class="field"><span class="lbl">Subject ({brand}, {market} and {me} are filled in)</span><input id="ctSubject" value="${esc(CT.template.subject)}" maxlength="200"></label>
        <label class="field"><span class="lbl">Text</span><textarea id="ctBody" rows="9">${esc(CT.template.body)}</textarea></label>
        <div class="row"><button class="btn primary" data-ctsave>Save the email text</button><button class="btn" data-cecancel>Cancel</button></div></div>` : ''}</div>`;
    const field = (r, k, label, type = 'text') => `<label class="field"><span class="lbl">${esc(label)}</span><input type="${type}" data-cf="${k}" value="${esc(((r.saved || {})[k]) || '')}" maxlength="${k === 'notes' ? 600 : 200}"></label>`;
    return intro + `<div class="panel"><div class="tablewrap"><table class="itable"><thead><tr><th>Brand</th><th>Why</th><th>Contact</th><th>Status</th><th></th></tr></thead><tbody>
      ${rows.slice(0, limit).map(r => {
        const c = r.saved || {}, k = r.key, [tone, label] = cKind[r.kind] || cKind.saved;
        const seller = c.seller_id ? `<a data-open="${esc(((ST.markets[c.seller_market || mk] || {}).site || site()) + '/sp?seller=' + c.seller_id)}">seller page</a>` : '';
        const what = [c.company && '<b>' + esc(c.company) + '</b>', c.email && `<a data-copy-email="${esc(c.email)}" title="Copy">${esc(c.email)}</a>`, c.phone && esc(c.phone),
          c.address && `<span class="sub">${esc(c.address)}</span>`, c.website && `<a data-open="${esc(c.website)}">website</a>`, seller,
          (c.storefront_url || r.storefront_url) && `<a data-open="${esc(c.storefront_url || r.storefront_url)}">Amazon store</a>`].filter(Boolean).join(' · ');
        const status = `<select data-cstatus="${esc(r.brand)}" aria-label="Status">${CT.statuses.map(s => `<option value="${s.status}"${(c.status || 'to_contact') === s.status ? ' selected' : ''}>${esc(s.label)}</option>`).join('')}</select>
          ${c.contacted_at ? `<span class="tsub">contacted ${ago(c.contacted_at)}</span>` : ''}`;
        const acts = `<button class="btn slim" data-cread="${esc(k)}" title="Read its seller's business details on Amazon">${icon('search')}Read</button>
          <button class="btn slim" data-cedit="${esc(k)}">Edit</button>
          <button class="btn slim" data-cmail="${esc(r.brand)}"${c.email ? '' : ' disabled title="Add an email address first (Edit)"'}>${icon('mail')}Email</button>
          <button class="btn slim ico" data-open="${esc('https://www.google.com/search?q=' + encodeURIComponent('"' + r.brand + '" official website contact wholesale'))}" title="Find its website (opens a search)" aria-label="Find website">${icon('globe')}</button>`;
        return `<tr><td><b>${esc(r.brand)}</b> <span class="pill ${tone}">${esc(label)}</span></td><td class="small">${esc(r.reason || '')}${r.revenue ? '<div class="sub">' + (r.kind === 'missing' ? curMoney(r.revenue, 'USD', 0) : m0(r.revenue)) + ' a month</div>' : ''}</td>
          <td class="small">${what || '<span class="muted">nothing yet</span>'}${c.notes ? `<div class="sub">${esc(c.notes)}</div>` : ''}</td><td>${status}</td><td>${acts}</td></tr>
          ${cEdit === k ? `<tr class="mgdetail"><td colspan="5"><div class="stackv" data-cform="${esc(r.brand)}" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px">
            ${field(r, 'company', 'Company')}${field(r, 'email', 'Email', 'email')}${field(r, 'phone', 'Phone')}${field(r, 'website', 'Website (https://…)')}
            ${field(r, 'address', 'Address')}${field(r, 'notes', 'Notes')}
            <div class="row"><button class="btn primary" data-csave="${esc(r.brand)}">Save</button><button class="btn" data-cecancel>Cancel</button>
              ${r.saved ? `<button class="btn ghost" data-cdelete="${esc(r.brand)}">Forget this contact</button>` : ''}</div></div></td></tr>` : ''}`;
      }).join('') || '<tr><td colspan="5" class="empty">No brands to show with this filter.</td></tr>'}</tbody></table></div>
      ${rows.length > limit ? `<div class="row" style="justify-content:center;margin-top:8px"><button class="btn" data-more-rows>Show ${num(Math.min(100, rows.length - limit))} more</button></div>` : ''}</div>`;
  }
  async function contactsAction(e) {
    const t = e.target;
    const f = t.closest('[data-cfilter]'); if (f) { cFilter = f.dataset.cfilter; render(); return true; }
    if (t.closest('[data-ctemplate]')) { cEdit = cEdit === '#template' ? null : '#template'; render(); return true; }
    if (t.closest('[data-cecancel]')) { cEdit = null; render(); return true; }
    const ed = t.closest('[data-cedit]'); if (ed) { cEdit = cEdit === ed.dataset.cedit ? null : ed.dataset.cedit; render(); return true; }
    const ce = t.closest('[data-copy-email]');
    if (ce) { try { await navigator.clipboard.writeText(ce.dataset.copyEmail); toast('Copied ' + ce.dataset.copyEmail); } catch (err) { toast(err.message); } return true; }
    if (t.closest('[data-ctsave]')) {
      try { await api('/api/contacts/template', {subject: $('ctSubject').value, body: $('ctBody').value}); cEdit = null; toast('Saved the email text'); await loadContacts(true); }
      catch (err) { toast(err.message); }
      return true;
    }
    const sv = t.closest('[data-csave]');
    if (sv) {
      const form = t.closest('[data-cform]'), fields = {};
      form.querySelectorAll('[data-cf]').forEach(i => { fields[i.dataset.cf] = i.value; });
      try { await api('/api/contacts/save', {brand: sv.dataset.csave, fields}); cEdit = null; toast('Saved'); await loadContacts(true); } catch (err) { toast(err.message); }
      return true;
    }
    const del = t.closest('[data-cdelete]');
    if (del) { try { await api('/api/contacts/delete', {brand: del.dataset.cdelete}); cEdit = null; await loadContacts(true); } catch (err) { toast(err.message); } return true; }
    const add = t.closest('[data-cadd]');
    if (add) {
      try { await api('/api/contacts/save', {brand: add.dataset.cadd, fields: {notes: add.dataset.creason || ''}}); toast(add.dataset.cadd + ' added to Brands to contact'); CT = null; }
      catch (err) { toast(err.message); }
      return true;
    }
    const mail = t.closest('[data-cmail]');
    if (mail) {
      try { const r = await api('/api/contacts/email', {brand: mail.dataset.cmail, market: mk}); toast(r.pasted ? 'The email opened; the whole text is on the clipboard to paste' : 'The email opened in your browser: read it and press Send', 6000); await loadContacts(true); }
      catch (err) { toast(err.message); }
      return true;
    }
    const one = t.closest('[data-cread]'), all = t.closest('[data-cread-all]');
    if (one || all) {
      const pick = one ? CT.rows.filter(r => r.key === one.dataset.cread)
        : CT.rows.filter(r => !(r.saved && (r.saved.address || r.saved.email || r.saved.phone)) && (r.asin || (r.saved || {}).seller_id)).slice(0, 10);
      const items = pick.map(r => ({brand: r.brand, asin: r.asin || '', seller_id: (r.saved || {}).seller_id || ''}));
      if (!items.length || (!items[0].asin && !items[0].seller_id)) { toast('No listing of this brand is known yet: use Find website, or Edit to add what you know.', 6000); return true; }
      $('mgProg').innerHTML = busy('Reading…');
      try {
        const {job} = await api('/api/contacts/read', {market: mk, items});
        const res = await waitJob(job, line => { $('mgProg').innerHTML = busy(line); });
        CT = {...res, at: Date.now()}; ctMk = mk; render();
        toast(`Read ${num(res.read)} page${res.read === 1 ? '' : 's'}: details found for ${num(res.found)}` + (res.notes.length ? ' · ' + res.notes.slice(0, 3).join(' · ') : ''), 9000);
      } catch (err) { toast(err.message); }
      $('mgProg').innerHTML = '';
      return true;
    }
    return false;
  }

  /* ---------- SmartScout: its own data for the market, straight from the exports, in charts and tables ----------
     Each view asks GET /api/smartscout for one page (filtered and sorted in the database); "Show more" adds the next. */
  const ssf = {niches: {}, brands: {}, sellers: {}, products: {}};    // each view's filters and sort
  let SS = null, ssKey = '', ssWant = '', ssTimer = 0;
  const ROLE_LABEL = {amazon: 'Amazon itself', global_store: 'Amazon abroad', reseller: 'Import reseller', seller: 'Seller', unknown: 'Not named'};
  const ROLE_TONE = {amazon: 'accent', global_store: 'warn', reseller: 'warn', seller: 'line', unknown: 'weak'};
  const SS_ROLES = [
    {key: 'amazon', label: 'Amazon itself', color: 'var(--s-demand)'}, {key: 'global_store', label: 'Amazon abroad (Global Store)', color: 'var(--s-overseas)'},
    {key: 'reseller', label: 'Import resellers', color: 'var(--s-5)'}, {key: 'seller', label: 'Other sellers', color: 'var(--s-local)'},
    {key: 'unknown', label: 'Not named', color: 'var(--s-other)'}];
  const ssQuery = (extra = {}) => {
    const f = {...(ssf[ssv] || {}), q: ssv === 'summary' ? '' : $('mgFind').value.trim(), ...extra};
    return '/api/smartscout?market=' + mk + '&view=' + ssv + Object.entries(f).filter(([, v]) => v !== '' && v != null && v !== false)
      .map(([k, v]) => '&' + encodeURIComponent(k) + '=' + encodeURIComponent(v === true ? 1 : v)).join('');
  };
  async function ssLoad(more) {
    const url = ssQuery(more ? {offset: SS.rows.length} : {}), key = more ? ssKey : url;
    ssWant = key;
    let got;
    try { got = await api(url); } catch (e) { got = {error: e.message}; }
    if (ssWant !== key) return;                       // a newer question was asked meanwhile
    ssWant = '';
    if (more && !got.error) SS = {...got, rows: SS.rows.concat(got.rows)}; else { SS = got; ssKey = key; }
    if (tab === 'smartscout') render();
  }
  function ssEnsure() {                               // ask for the page the filters describe (typing waits a moment)
    const url = ssQuery();
    if (url === ssKey || url === ssWant) return;
    clearTimeout(ssTimer);
    ssTimer = setTimeout(() => ssLoad(false), SS && ssKey.split('&view=')[1]?.split('&')[0] === ssv ? 300 : 0);
  }
  const ssMoney = v => v == null ? '–' : CUR_SYM[cur()] + ' ' + compact(v);
  const ssBar = (v, max) => v == null ? '<span class="muted small">–</span>'
    : `<span class="ss-money"><i style="width:${Math.max(2, Math.round(v / (max || 1) * 90))}px"></i><span>${ssMoney(v)}</span></span>`;
  const growth = g => g == null ? '<span class="muted small">–</span>' : `<span class="${g >= 0 ? 'up' : 'down'}">${g >= 0 ? '+' : ''}${pct(g)}</span>`;
  const ssTh = (key, label, tip = '', cls = '') => {
    const f = ssf[ssv] || {}, on = (SS && SS.sort) === key;
    return `<th scope="col" class="${cls}"${tip ? ` title="${esc(tip)}"` : ''} aria-sort="${on ? (SS.dir === 'asc' ? 'ascending' : 'descending') : 'none'}">` +
      `<button type="button" class="thsort" data-sssort="${key}">${esc(label)}${on ? (SS.dir === 'asc' ? ' ▲' : ' ▼') : ''}</button></th>`;
  };
  const ssSelect = (k, label, opts) => `<label class="small">${esc(label)} <select data-ssf="${k}">${opts.map(([v, l]) =>
    `<option value="${esc(v)}"${String((ssf[ssv] || {})[k] ?? '') === String(v) ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select></label>`;
  const ssCheck = (k, label) => `<label class="small"><input type="checkbox" data-ssf="${k}"${(ssf[ssv] || {})[k] ? ' checked' : ''}> ${esc(label)}</label>`;
  const ssMore = () => SS.rows.length < SS.total ? `<div class="row" style="justify-content:center;margin-top:8px"><button class="btn" data-ssmore>Show ${num(Math.min(50, SS.total - SS.rows.length))} more of ${num(SS.total - SS.rows.length)}</button></div>` : '';
  const catOpts = () => [['', 'Every category'], ...((SS && SS.categories) || []).map(c => [c.category, c.category + ' (' + num(c.n) + ')'])];

  function ssTab() {
    ssEnsure();
    const segs = SSV.map(([k, l]) => `<button type="button" data-ssv="${k}" aria-pressed="${ssv === k}">${esc(l)}</button>`).join('');
    const c = (D.ss || {}).counts || {};
    const head = `<div class="panel stackv"><div class="ss-head"><div><h2>SmartScout's data for Amazon ${esc(MK[mk])}</h2>
        <div class="small muted">Exported ${esc(day(D.baseline.as_of))} · ${esc([c.subcats ? num(c.subcats) + ' niches' : '', c.brands ? num(c.brands) + ' brands' : '',
          c.sellers ? num(c.sellers) + ' sellers' : '', c.products ? num(c.products) + ' products' : ''].filter(Boolean).join(' · '))}</div></div>
        <div class="seg" role="group" aria-label="SmartScout view">${segs}</div></div>${ssFilters()}</div>`;
    if (!SS || SS.view !== ssv || SS.market !== mk) return head + (SS && SS.error ? `<div class="panel">${failPill(SS.error)}</div>` : `<div class="panel">${busy('Loading SmartScout data…')}</div>`);
    if (SS.error) return head + `<div class="panel">${failPill(SS.error)}</div>`;
    const stale = ssWant || ssQuery() !== ssKey ? ' ss-loading' : '';
    return head + `<div class="stackv${stale}">` + (ssv === 'summary' ? ssSummary() : ssv === 'niches' ? ssNiches() : ssv === 'brands' ? ssBrands()
      : ssv === 'sellers' ? ssSellers() : ssProducts()) + '</div>';
  }
  function ssFilters() {
    if (ssv === 'niches') return `<div class="ss-filters">${ssSelect('cat', 'Category', catOpts())}
      ${ssSelect('min', 'Money a month at least', [['', 'any'], [10000, ssMoney(10000)], [50000, ssMoney(50000)], [100000, ssMoney(100000)], [500000, ssMoney(500000)], [1000000, ssMoney(1000000)]])}
      <span class="small muted">The search box above finds a niche or its biggest brand.</span></div>`;
    if (ssv === 'brands') return `<div class="ss-filters">${ssSelect('cat', 'Category', catOpts())}${ssCheck('abroad', 'Only brands whose main seller ships in from abroad')}
      ${ssCheck('generic', 'Include “Generic” (unbranded)')}<span class="small muted">The search box finds a brand or a niche.</span></div>`;
    if (ssv === 'sellers') return `<div class="ss-filters"><div class="seg" role="group" aria-label="Kind of seller">${[['', 'All'], ...SS_ROLES.map(r => [r.key, ROLE_LABEL[r.key]])]
      .map(([v, l]) => `<button type="button" data-ssrole="${v}" aria-pressed="${(ssf.sellers.role || '') === v}">${esc(l)}</button>`).join('')}</div></div>`;
    if (ssv === 'products') return `<div class="ss-filters">${ssSelect('cat', 'Category', catOpts())}
      ${ssSelect('sellers', 'Sellers at most', [['', 'any'], [1, '1'], [3, '3'], [6, '6']])}
      ${ssSelect('units', 'Sold a month at least', [['', 'any'], [30, '30'], [100, '100'], [300, '300'], [1000, '1,000']])}
      ${ssSelect('slice', 'Export', [['', 'all of them'], ...((SS && SS.slices) || []).map(s => [s.slice, s.slice.replace(/^products-/, '').replace(/-/g, ' ') + ' (' + num(s.rows) + ')'])])}
      ${ssCheck('new', 'Launched in the last 6 months')}</div>`;
    return '';
  }

  function ssSummary() {
    const S = SS, b = S.brands || {}, n = S.niches || {}, s = S.sellers || {}, c = (D.ss || {}).counts || {};
    const card = (v, label, value, sub, ic) => `<a class="kpi" href="#/mgaps/${mk}/smartscout/${v}"><span class="ic">${icon(ic)}</span><span class="eyebrow">${esc(label)}</span>
      <span class="v">${value}</span><span class="s">${sub}</span><span class="go">Open →</span></a>`;
    const parts = SS_ROLES.map(r => ({...r, n: (s.shares || {})[r.key] || 0}));
    return `<div class="kpis mgo">
        ${card('brands', 'Money a month', ssMoney(b.revenue), `spent with ${num(b.n)} brands making ${esc(curMoney(1000, cur(), 0))}+ a month`, 'chart')}
        ${card('niches', 'Niches', num(n.n), 'SmartScout subcategories, each with its money and competition', 'tags')}
        ${card('sellers', 'Sellers', num(s.sellers), s.half ? `half of the money goes to just <b>${num(s.half)}</b>` : '', 'users')}
        ${card('products', 'Products', num(c.products || 0), 'with their sales, price and sellers', 'box')}</div>
      <div class="ss-charts">
        <div class="panel" id="ssCats"></div>
        <div class="panel stackv"><div><h3 style="font:650 15px/1.3 var(--display)">Who takes the money</h3>
          <div class="small muted">Share of all sellers' sales · half goes to ${num(s.half)} sellers, the top 10 take ${pct(s.top10)}</div></div>
          <div>${originBar(parts.map(p => ({...p, n: Math.round(p.n * 1000) / 10, label: p.label + ' (%)'})), 100, 12)}
            <div class="legend">${parts.filter(p => p.n).map(p => `<span><i style="background:${p.color}"></i>${esc(p.label)} <b class="tnum">${pct(p.n)}</b></span>`).join('')}</div></div>
          <div id="ssTopSellers"></div></div>
        <div class="panel" id="ssTopNiches"></div>
        <div class="panel" id="ssGrowing"></div>
        <div class="panel" id="ssTopBrands"></div>
        <div class="panel stackv"><h3 style="font:650 15px/1.3 var(--display)">What was exported</h3>
          <div class="small">${esc(day(S.as_of))}, from SmartScout's Amazon ${esc(MK[mk])} tables. Everything a brand or seller makes is SmartScout's
            estimate of sales a month; brands and sellers making less than ${esc(curMoney(1000, cur(), 0))} a month were left out.</div>
          <div class="mgo-data" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr))">
            ${[['Niches', c.subcats], ['Brands', c.brands], ['Sellers', c.sellers], ['Products', c.products], ['Products of sellers abroad', c.offers], ['Also read in the USA', c.us_twins]]
              .filter(([, v]) => v).map(([l, v]) => `<div><span class="eyebrow">${esc(l)}</span><b style="font-size:18px">${num(v)}</b></div>`).join('')}</div>
          <div class="small muted">Products of sellers abroad are on <a href="#/mgaps/${mk}/shipped">Shipped in from abroad</a>.</div></div>
      </div>`;
  }
  function ssNiches() {
    const S = SS, max = Math.max(1, ...S.rows.map(r => r.revenue || 0));
    return `<div class="panel" id="ssMap"></div>
      <div class="panel"><div class="small" style="margin-bottom:8px"><b>${num(S.total)}</b> niche${S.total === 1 ? '' : 's'}${S.total < (((D.ss || {}).counts || {}).subcats || 0) ? ' match' : ''} · click a column to sort</div>
        <div class="tablewrap"><table class="itable ss-table"><thead><tr>${ssTh('name', 'Niche')}${ssTh('revenue', 'Money a month', 'SmartScout estimate of sales a month')}
          ${ssTh('units', 'Sold a month', '', 'r')}${ssTh('brands', 'Brands', 'Brands selling in it', 'r')}${ssTh('sellers', 'Sellers', 'SmartScout: average number of sellers', 'r')}
          ${ssTh('amazon', 'Amazon itself', 'Share sold by Amazon itself', 'r')}${ssTh('top', 'Biggest brand', 'and its share')}${ssTh('growth', '12 months', 'Change in sales over 12 months', 'r')}
          ${ssTh('price', 'Price', 'Average price', 'r')}${ssTh('reviews', 'Reviews', 'Average reviews a listing (the barrier)', 'r')}<th></th></tr></thead><tbody>
        ${S.rows.map(r => `<tr><td><b>${esc(r.name)}</b>${r.risk ? ` <span class="pill warn" title="Hard for a new seller">${esc(r.risk)}</span>` : ''}<div class="sub">${esc(r.category || '')}</div></td>
          <td>${ssBar(r.revenue, max)}</td><td class="r">${r.units != null ? num(r.units) : '–'}</td><td class="r">${r.brands != null ? num(r.brands) : '–'}</td>
          <td class="r">${r.avg_sellers != null ? num(r.avg_sellers, r.avg_sellers < 10 ? 1 : 0) : '–'}</td><td class="r">${r.amazon_share != null ? pct(r.amazon_share) : '–'}</td>
          <td>${r.top_brand ? esc(r.top_brand) + (r.top_brand_share != null ? ' <span class="muted">' + pct(r.top_brand_share) + '</span>' : '') : '–'}</td>
          <td class="r">${growth(r.growth_12m)}</td><td class="r">${r.price != null ? esc(curMoney(r.price, cur(), 0)) : '–'}</td>
          <td class="r">${r.reviews_avg != null ? num(r.reviews_avg) : '–'}</td>
          <td><button class="btn slim ico" data-open="${esc(searchUrl(r.name))}" title="Search “${esc(r.name)}” on Amazon ${esc(MK[mk])}" aria-label="Search on Amazon">${icon('external')}</button></td></tr>`).join('')
          || '<tr><td colspan="11" class="empty">No niche matches.</td></tr>'}</tbody></table></div>${ssMore()}</div>`;
  }
  function ssBrands() {
    const S = SS, max = Math.max(1, ...S.rows.map(r => r.revenue || 0));
    return `<div class="panel" id="ssBrandBars"></div>
      <div class="panel"><div class="small" style="margin-bottom:8px"><b>${num(S.total)}</b> brand${S.total === 1 ? '' : 's'} · click a column to sort</div>
        <div class="tablewrap"><table class="itable ss-table"><thead><tr>${ssTh('name', 'Brand')}${ssTh('revenue', 'Money a month')}${ssTh('growth', '12 months', '', 'r')}
          <th title="The seller that sells most of this brand here">Main seller</th>${ssTh('price', 'Price', 'Average price', 'r')}${ssTh('sellers', 'Sellers', 'Average sellers on its listings', 'r')}
          ${ssTh('reviews', 'Reviews', '', 'r')}${ssTh('products', 'Products', '', 'r')}<th></th></tr></thead><tbody>
        ${S.rows.map(r => `<tr><td><b>${esc(r.brand)}</b><div class="sub">${esc([r.subcategory, r.category].filter(Boolean).join(' · '))}</div></td>
          <td>${ssBar(r.revenue, max)}</td><td class="r">${growth(r.growth_12m)}</td>
          <td>${r.dominant_seller ? (r.store ? cflag(r.store, '') + ' ' : '') + esc(r.dominant_seller) + (r.role !== 'seller' ? ` <span class="pill ${ROLE_TONE[r.role] || 'weak'}">${esc(ROLE_LABEL[r.role] || r.role)}</span>` : '') : '<span class="muted small">–</span>'}</td>
          <td class="r">${r.avg_price != null ? esc(curMoney(r.avg_price, cur(), 0)) : '–'}</td><td class="r">${r.avg_sellers != null ? num(r.avg_sellers, 1) : '–'}</td>
          <td class="r">${r.reviews != null ? num(r.reviews) : '–'}${r.rating ? `<div class="sub">${num(r.rating, 1)}★</div>` : ''}</td><td class="r">${r.products != null ? num(r.products) : '–'}</td>
          <td style="white-space:nowrap"><button class="btn slim ico" data-open="${esc(r.storefront_url || searchUrl(r.brand))}" title="${r.storefront_url ? 'Its Amazon store' : 'Search it on Amazon ' + esc(MK[mk])}" aria-label="Open on Amazon">${icon('external')}</button>
            <button class="btn slim" data-cadd="${esc(r.brand)}" data-creason="${esc(r.role === 'global_store' || r.role === 'reseller' ? 'Shipped in: most sales here go through ' + r.dominant_seller : 'From SmartScout: ' + ssMoney(r.revenue) + ' a month here')}" title="Add it to Brands to contact">${icon('mail')}Contact</button></td></tr>`).join('')
          || '<tr><td colspan="9" class="empty">No brand matches.</td></tr>'}</tbody></table></div>${ssMore()}</div>`;
  }
  function ssSellers() {
    const S = SS, max = Math.max(1, ...S.rows.map(r => r.revenue || 0));
    const parts = SS_ROLES.map(r => ({...r, n: (S.shares || {})[r.key] || 0}));
    return `<div class="panel stackv"><div><h3 style="font:650 15px/1.3 var(--display)">Who takes the money</h3><div class="small muted">${num(S.sellers)} sellers make
        ${esc(ssMoney(S.money))} a month: half of it goes to ${num(S.half)} sellers, the top 10 take ${pct(S.top10)}</div></div>
        <div>${originBar(parts.map(p => ({...p, n: Math.round(p.n * 1000) / 10, label: p.label + ' (%)'})), 100, 12)}
        <div class="legend">${parts.filter(p => p.n).map(p => `<span><i style="background:${p.color}"></i>${esc(p.label)} <b class="tnum">${pct(p.n)}</b></span>`).join('')}</div></div></div>
      <div class="panel"><div class="small" style="margin-bottom:8px"><b>${num(S.total)}</b> seller${S.total === 1 ? '' : 's'} · click a column to sort</div>
        <div class="tablewrap"><table class="itable ss-table"><thead><tr>${ssTh('name', 'Seller')}<th>Who</th>${ssTh('revenue', 'Money a month')}<th class="r">Share</th>
          ${ssTh('brands', 'Brands', '', 'r')}${ssTh('asins', 'Listings', '', 'r')}${ssTh('price', 'Price', 'Average price', 'r')}${ssTh('fba', 'FBA', 'Share of its listings Amazon ships', 'r')}<th>Main niche</th></tr></thead><tbody>
        ${S.rows.map(r => `<tr><td>${r.store ? cflag(r.store, '') + ' ' : ''}<b>${esc(r.name || 'Not named')}</b>${r.seller_id ? `<div class="sub"><a data-open="${esc(r.url || site() + '/sp?seller=' + r.seller_id)}">${esc(r.seller_id)}</a></div>` : ''}</td>
          <td><span class="pill ${ROLE_TONE[r.role] || 'weak'}">${esc(ROLE_LABEL[r.role] || r.role)}</span></td><td>${ssBar(r.revenue, max)}</td>
          <td class="r">${pct(r.share, r.share < 0.01 ? 2 : 1)}</td><td class="r">${r.n_brands != null ? num(r.n_brands) : '–'}</td><td class="r">${r.n_asins != null ? compact(r.n_asins) : '–'}</td>
          <td class="r">${r.avg_price != null ? esc(curMoney(r.avg_price, cur(), 0)) : '–'}</td><td class="r">${r.pct_fba != null ? pct(r.pct_fba) : '–'}</td>
          <td class="small">${esc(r.subcategory || '–')}</td></tr>`).join('') || '<tr><td colspan="9" class="empty">No seller matches.</td></tr>'}</tbody></table></div>${ssMore()}</div>`;
  }
  function ssProducts() {
    const S = SS, max = Math.max(1, ...S.rows.map(r => r.units || 0));
    return `<div class="panel"><div class="small" style="margin-bottom:8px"><b>${num(S.total)}</b> product${S.total === 1 ? '' : 's'} · click a column to sort</div>
      <div class="tablewrap"><table class="mtx ss-table"><thead><tr><th class="mtx-prod">Product</th>${ssTh('units', 'Sold a month')}${ssTh('revenue', 'Money a month', '', 'r')}
        ${ssTh('price', 'Price', '', 'r')}${ssTh('sellers', 'Sellers', 'Sellers offering this listing')}${ssTh('rating', 'Rating', '', 'r')}${ssTh('reviews', 'Reviews', '', 'r')}
        ${ssTh('launch', 'Launched')}${ssTh('rank', 'Rank', 'Its rank in its niche', 'r')}<th title="The same product on Amazon USA (SmartScout connector)">USA</th></tr></thead><tbody>
      ${S.rows.map(p => {
        const url = site() + '/dp/' + p.asin, d = p.units;
        const pills = `<span class="pill line">${esc((p.preset || '').replace(/^products-/, '').replace(/-/g, ' ') || 'SmartScout')}</span>` +
          (p.risk ? `<span class="pill warn">${esc(p.risk)}</span>` : '');
        const acts = `<button class="btn slim" data-open="${esc(url)}">${icon('external')}Amazon</button>` +
          `<button class="btn slim" data-open="${esc(alibabaUrl((p.title || '').split(/\s+/).slice(0, 6).join(' ')))}" title="Find suppliers">Alibaba</button>` +
          shortlistBtn(`data-ssboard="${esc(p.asin)}"`);
        return `<tr>${prodCell({image: p.image, url, title: p.title || p.asin, category: [p.brand, p.subcategory].filter(Boolean).join(' · ') + ' · ' + p.asin, flags: []}, pills, acts)}
          <td>${d != null ? `<span class="dem"><i style="width:${Math.max(2, Math.round(d / max * 70))}px"></i><span>${num(d)}</span></span>` : '–'}</td>
          <td class="r">${p.revenue != null ? esc(ssMoney(p.revenue)) : '–'}</td><td class="r">${p.price != null ? esc(curMoney(p.price, cur(), 2)) : '–'}</td>
          <td>${sellersChip(p.sellers)}</td><td class="r">${p.rating ? num(p.rating, 1) + '★' : '–'}</td><td class="r">${p.reviews != null ? num(p.reviews) : '–'}</td>
          <td class="small">${p.launch && p.launch <= new Date().toISOString().slice(0, 10) ? esc(day(Date.parse(p.launch + 'T00:00') / 1000)) : '–'}</td>
          <td class="r">${p.sub_rank ? '#' + num(p.sub_rank) : '–'}</td>
          <td class="small">${p.us && p.us.units != null ? `~${num(p.us.units)}/mo${p.us.sellers != null ? '<div class="sub">' + num(p.us.sellers) + ' sellers</div>' : ''}` : '<span class="muted">–</span>'}</td></tr>`;
      }).join('') || '<tr><td colspan="10" class="empty">No product matches.</td></tr>'}</tbody></table></div>${ssMore()}</div>`;
  }
  /* the charts, drawn into the panels once the view is on the page */
  function ssMount() {
    if (!SS || SS.error || SS.view !== ssv) return;
    const S = SS, el = id => document.getElementById(id);
    const to = (v, f) => { Object.assign(ssf[v], f); step('mgaps', mk, 'smartscout', v); };
    const find = (v, text, f = {}) => { $('mgFind').value = text; to(v, f); };
    if (ssv === 'summary') {
      const cats = S.categories || [], total = cats.reduce((a, c) => a + (c.revenue || 0), 0) || 1;
      if (el('ssCats')) Charts.bars(el('ssCats'), {title: 'Money by category', sub: 'Sales a month of the brands in each category · click one to see its brands',
        columns: ['Category', 'Money a month'], pickTip: 'click to see its brands', onPick: it => { $('mgFind').value = ''; to('brands', {cat: it.key}); },
        items: cats.filter(c => c.category !== 'No category').slice(0, TOPN).map(c => ({key: c.category, label: c.category, value: c.revenue, text: ssMoney(c.revenue) + ' · ' + pct(c.revenue / total)}))});
      const top = ((S.sellers || {}).top || []).slice(0, 8);
      if (el('ssTopSellers')) Charts.bars(el('ssTopSellers'), {title: 'The biggest sellers', columns: ['Seller', 'Money a month'],
        items: top.map(t => ({label: t.name || 'Not named', flag: t.store, tag: t.role !== 'seller' ? ROLE_LABEL[t.role] : '', value: t.revenue, text: ssMoney(t.revenue) + ' · ' + pct(t.share, 1),
          color: (SS_ROLES.find(r => r.key === t.role) || {}).color})),
        legend: SS_ROLES.filter(r => top.some(t => t.role === r.key)).map(r => ({label: r.label, color: r.color}))});
      if (el('ssTopNiches')) Charts.bars(el('ssTopNiches'), {title: 'The biggest niches', sub: 'Sales a month · click one to see it with the others', columns: ['Niche', 'Money a month'],
        pickTip: 'click to open it', onPick: it => find('niches', it.label),
        items: (S.top_niches || []).map(r => ({label: r.name, value: r.revenue, text: ssMoney(r.revenue) + ' · ' + num(r.brands) + ' brands'}))});
      if (el('ssGrowing')) Charts.bars(el('ssGrowing'), {title: 'Growing fastest', sub: 'Change in sales over 12 months, niches making ' + ssMoney(((S.niches || {}).revenue || 0) * 0.002) + '+ a month',
        columns: ['Niche', '12 months'], pickTip: 'click to open it', onPick: it => find('niches', it.label), empty: 'SmartScout gave no 12-month growth for this market.',
        items: (S.growing || []).map(r => ({label: r.name, value: Math.max(0, r.growth_12m), text: '+' + pct(r.growth_12m) + ' · ' + ssMoney(r.revenue) + '/mo', color: 'var(--s-local)'}))});
      if (el('ssTopBrands')) Charts.bars(el('ssTopBrands'), {title: 'The biggest brands', sub: 'Sales a month · orange: most of it is shipped in from abroad', columns: ['Brand', 'Money a month'],
        pickTip: 'click to see it with the others', onPick: it => find('brands', it.label),
        items: (S.top_brands || []).map(r => ({label: r.brand, tag: r.role === 'global_store' || r.role === 'reseller' ? 'shipped in' : '', value: r.revenue, text: ssMoney(r.revenue),
          color: r.role === 'global_store' || r.role === 'reseller' ? 'var(--s-overseas)' : 'var(--s-demand)'})),
        legend: (S.top_brands || []).some(r => r.role === 'global_store' || r.role === 'reseller') ? [{label: 'Sold mostly from here', color: 'var(--s-demand)'}, {label: 'Shipped in from abroad', color: 'var(--s-overseas)'}] : []});
    }
    if (ssv === 'niches' && el('ssMap')) Charts.nicheMap(el('ssMap'), {title: 'Niche map', money: ssMoney,
      sub: `The ${num(S.map.length)} biggest niches${S.total > S.map.length ? ' of the ' + num(S.total) + ' that match' : ''}: higher = more money a month, further left = fewer brands sell in it; the dashed lines are the middle of these niches. Click a dot to find it in the table.`,
      points: S.map, onOpen: p => { $('mgFind').value = p.name; ssEnsure(); }});
    if (ssv === 'brands' && el('ssBrandBars')) Charts.bars(el('ssBrandBars'), {title: 'The biggest brands' + (ssf.brands.cat ? ' in ' + ssf.brands.cat : '') + (ssf.brands.abroad ? ', shipped in' : ''),
      sub: 'Sales a month · orange: most of it is shipped in from abroad', columns: ['Brand', 'Money a month'],
      items: (S.top || []).map(r => ({label: r.brand, flag: r.store, tag: r.role === 'global_store' || r.role === 'reseller' ? r.dominant_seller : '', value: r.revenue, text: ssMoney(r.revenue),
        color: r.role === 'global_store' || r.role === 'reseller' ? 'var(--s-overseas)' : 'var(--s-demand)'}))});
  }
  const TOPN = 12;

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
    const sm = e.target.closest('[data-smk]');
    if (sm) { stockMk = sm.dataset.smk; render(); return; }
    if (e.target.closest('[data-shipfamous]')) { shipFamous = e.target.closest('[data-shipfamous]').checked; render(); return; }
    const sg = e.target.closest('[data-shipgo]');
    if (sg) { shipFocus = sg.dataset.shipgo; if (tab === 'shipped') render(); else step('mgaps', mk, 'shipped'); return; }
    const sa = e.target.closest('[data-shipall]');
    if (sa) { shipOpen.has(sa.dataset.shipall) ? shipOpen.delete(sa.dataset.shipall) : shipOpen.add(sa.dataset.shipall); render(); return; }
    const pt = e.target.closest('[data-ptier]');
    if (pt) { pTier = pt.dataset.ptier; limit = 100; render(); return; }
    const sv = e.target.closest('[data-ssv]');                      // SmartScout: its views, sorting, kind of seller, more rows
    if (sv) { step('mgaps', mk, 'smartscout', sv.dataset.ssv); return; }
    const so = e.target.closest('[data-sssort]');
    if (so) {
      const f = ssf[ssv], k = so.dataset.sssort;
      f.dir = SS && SS.sort === k ? (SS.dir === 'asc' ? 'desc' : 'asc') : null;
      f.sort = k; render(); return;
    }
    const sr = e.target.closest('[data-ssrole]');
    if (sr) { ssf.sellers.role = sr.dataset.ssrole; render(); return; }
    if (e.target.closest('[data-ssmore]')) { e.target.closest('[data-ssmore]').disabled = true; ssLoad(true); return; }
    const ssb = e.target.closest('[data-ssboard]');
    if (ssb) {
      const p = ((SS || {}).rows || []).find(x => x.asin === ssb.dataset.ssboard);
      if (p) shortlist({title: (p.title || p.asin).slice(0, 120), market: mk, asin: p.asin, kw: '', image: p.image || '',
        source: 'SmartScout (' + (p.subcategory || p.category || 'products') + ')'}, ssb);
      return;
    }
    const shp = e.target.closest('[data-shipboard]');
    if (shp) {
      const p = ((D.shipped || {}).sources || []).flatMap(x => x.products).find(x => x.asin === shp.dataset.shipboard);
      if (p) shortlist({title: (p.title || p.asin).slice(0, 120), market: mk, asin: p.asin, kw: '', image: p.image || '',
        source: 'Shipped in (' + (((D.shipped || {}).sources || []).find(x => x.products.includes(p)) || {}).seller + ')'}, shp);
      return;
    }
    if ((tab === 'contacts' || e.target.closest('[data-cadd]')) && await contactsAction(e)) return;   // "Contact" also sits on Shipped in
    if (e.target.closest('[data-sall]')) { stockAll = e.target.closest('[data-sall]').checked; render(); return; }
    if (e.target.closest('[data-srun]')) {
      const btn = e.target.closest('[data-srun]'); btn.disabled = true;
      $('mgProg').innerHTML = busy('Starting…');
      try {
        const {job} = await api('/api/stock/run', {markets: stockMk ? [stockMk] : []});
        const res = await waitJob(job, line => { $('mgProg').innerHTML = busy(line); });
        stock = {...res, at: Date.now()}; render();
        toast((res.notes || []).join(' · ') || 'Done', 9000);
      } catch (err) { toast(err.message); }
      $('mgProg').innerHTML = '';
      return;
    }
    const sb = e.target.closest('[data-sboard]');
    if (sb) {
      const r = ((stock || {}).rows || []).find(x => x.asin === sb.dataset.sboard);
      if (r) shortlist({title: (r.title || r.asin).slice(0, 120), market: r.out[0] || r.idea.market, asin: r.asin, kw: '',
        image: r.image || '', source: 'Out of stock (' + r.idea.why + ')'}, sb);
      return;
    }
    const pb = e.target.closest('[data-pboard]');
    if (pb) {
      const p = (D.products || []).find(x => x.asin === pb.dataset.pboard);
      if (p) shortlist({title: p.title.slice(0, 120), market: mk, asin: p.asin, kw: p.phrase || '', image: p.image || '',
        source: 'Market Gaps' + (p.niche ? ' (' + p.niche + ')' : '')}, pb);
      return;
    }
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
