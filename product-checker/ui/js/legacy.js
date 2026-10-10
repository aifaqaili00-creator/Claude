'use strict';
/* The original tools (Check sellers, Product ideas, Niche keywords, Xray, Rank a file, History, Settings),
   ported into the new shell. Later phases move these into ui/js/views/ one by one. */

view('check', {title: 'Check sellers', sub: 'How many listings deliver fast in each country, and how many ship from overseas',
               onShow: () => $('kw').focus()});
view('ideas', {title: 'Product ideas', sub: 'Booming and brand-new products, and whether they have reached Australia and the UAE'});
view('niche', {title: 'Niche keywords', sub: 'What shoppers type into Amazon, with demand and local sellers per country',
               onShow: () => $('nSeed').focus()});
view('xray', {title: 'Xray analysis', sub: 'Run Helium 10 Xray in your Chrome; the export is picked up and analysed here'});
view('rank', {title: 'Rank a file', sub: 'Top products from a Black Box, Xray or any export, with local seller checks'});
view('settings', {title: 'Settings', sub: 'Theme, Chrome profile, delivery locations and checks'});

/* Search phrases the user edited, kept across re-renders (key: "rank:<asin>" or "idea:<asin>"). */
const editedSearch = new Map();
document.addEventListener('input', e => {
  const t = e.target;
  if (t.dataset && t.dataset.search) editedSearch.set('rank:' + t.dataset.search, t.value);
  if (t.dataset && t.dataset.kw) editedSearch.set('idea:' + t.dataset.kw, t.value);
});
const rankSearch = r => editedSearch.has('rank:' + r.asin) ? editedSearch.get('rank:' + r.asin) : r.search;
const ideaSearch = i => editedSearch.has('idea:' + i.asin) ? editedSearch.get('idea:' + i.asin) : i.search;

document.addEventListener('click', e => {
  const a = e.target.closest('[data-open]');
  if (a) { e.preventDefault(); openUrl(a.dataset.open); }
});

/* ---------- Check sellers ---------- */
$('mkSeg').dataset.multi = '1';
seg($('mkSeg'));
let currentJob = null, checkData = null;
$('checkForm').addEventListener('submit', e => {
  e.preventDefault();
  const keyword = $('kw').value.trim();
  const markets = pressed($('mkSeg'));
  if (!keyword) return $('kw').focus();
  if (!markets.length) return toast('Pick at least one country');
  runCheck(keyword, markets, $('refresh').checked);
});
async function runCheck(keyword, markets, refresh) {
  $('btnCheck').disabled = true;
  $('progress').innerHTML = busy('Starting…');
  try {
    const {job} = await api('/api/check', {keyword, markets, refresh});
    currentJob = job;
    const res = await waitJob(job, line => { $('progress').innerHTML = busy(line); });
    $('progress').textContent = '';
    $('refresh').checked = false;
    renderCheck(res);
  } catch (e) { $('progress').innerHTML = failPill(e.message); }
  $('btnCheck').disabled = false;
}
function renderCheck(res) {
  checkData = res;
  $('checkEmpty').hidden = true; $('checkOut').hidden = false; $('listOut').hidden = false;
  $('checkTitle').textContent = '“' + res.keyword + '”';
  const times = res.results.filter(r => r.checked_at).map(r => r.checked_at);
  $('checkWhen').textContent = times.length ? 'Checked ' + ago(Math.min(...times)) + (res.results.some(r => r.cached) ? ' (saved result)' : '') : '';
  $('cards').innerHTML = res.results.map(card).join('');
  $('lmSeg').innerHTML = '<button type="button" data-v="all" aria-pressed="true">All</button>' +
    res.results.filter(r => !r.error).map(r => `<button type="button" data-v="${r.market}" aria-pressed="false">${MK_SHORT[r.market]}</button>`).join('');
  renderList();
}
function originParts(r) {
  const local = r.local ?? r.fast, abroad = r.overseas ?? r.slow;
  const localFast = r.local_other != null ? local - r.local_other : r.fast;
  return [
    {label: 'Local', n: local, color: 'var(--s-local)', note: localFast ? '(' + localFast + ' within ' + r.fast_days + ' days)' : ''},
    {label: 'Overseas', n: abroad, color: 'var(--s-overseas)', note: r.overseas_intl ? '(' + r.overseas_intl + ' say “international”)' : ''},
    {label: 'No date shown', n: r.origin_unknown ?? r.unknown ?? 0, color: 'var(--s-other)'}];
}
function card(r) {
  if (r.error) return `<div class="panel mcard"><div class="mtop"><div><div class="eyebrow">Amazon ${esc(MK_SHORT[r.market] || r.market)}</div><h2>${esc(r.name)}</h2></div></div>
    <div class="verdict error">${icon('warn')}<span>Could not check: ${esc(r.error)}</span></div></div>`;
  const cur = r.currency, parts = originParts(r);
  const loc = r.location_ok ? `<span class="pill good" title="Amazon delivers to ${esc(r.location)}"><i></i>${esc(r.location)}</span>`
    : `<span class="pill warn" title="Wanted: ${esc(r.location_wanted)}"><i></i>${esc(r.location || 'location?')}</span>`;
  const share = n => r.total ? Math.round(n / r.total * 100) : 0;
  return `<div class="panel mcard ${r.level}">
    <div class="mtop"><div><div class="eyebrow">Amazon ${esc(MK_SHORT[r.market])}</div><h2>${esc(r.name)}</h2></div>${loc}</div>
    <div class="split">
      <div><span class="swatch" style="background:var(--s-local)"></span><span class="v">${parts[0].n}</span><span class="of">local sellers<br><span class="tnum">${share(parts[0].n)}%</span> of ${r.total}</span></div>
      <div><span class="swatch" style="background:var(--s-overseas)"></span><span class="v">${parts[1].n}</span><span class="of">not local<br><span class="tnum">${share(parts[1].n)}%</span> ship from overseas</span></div>
    </div>
    <div>${originBar(parts, r.total, 12)}${legend(parts)}</div>
    ${r.status === 'empty_suspect' ? `<div class="verdict error">${icon('warn')}<span>Amazon returned an empty page without saying “no results”. It may be limiting requests. This is not counted as zero sellers; try again in a few minutes.</span></div>`
      : `<div class="verdict ${r.level}">${icon(LEVEL_ICON[r.level] || 'dot')}<span>${esc(r.verdict)}</span></div>`}
    ${r.location_ok === false ? `<div class="small">${icon('warn')} Amazon showed “${esc(r.location || '?')}” instead of ${esc(r.location_wanted)}, so delivery times may be off. This search is not used in charts.</div>` : ''}
    ${r.layout_warning ? `<div class="small">${icon('warn')} The page looked different from usual; some numbers may be missing.</div>` : ''}
    <dl class="facts">
      <div><dt>Listings</dt><dd class="tnum">${r.total} organic + ${r.sponsored} ads${r.results_total ? ` · Amazon says ${r.results_over ? 'over ' : ''}${num(r.results_total)} results` : ''}</dd></div>
      <div><dt>Fast sellers</dt><dd>${r.fast ? r.fast + ' · median ' + num(r.fast_reviews_median) + ' reviews' + (r.fast_price_min != null ? ' · ' + money(r.fast_price_min, cur) + '–' + num(r.fast_price_max, 2) : '') : '<span class="muted">none</span>'}</dd></div>
      <div><dt>Demand</dt><dd>${r.bought_listings ? r.bought_listings + ' listings show “bought in past month”, top ' + num(r.bought_top) + '+' : '<span class="muted">Amazon shows no sales badges</span>'}</dd></div>
    </dl>
    <div class="row"><button class="btn slim" data-open="${esc(r.search_url)}">Open search in Chrome${icon('external')}</button>${r.cached ? '<span class="small">saved ' + ago(r.checked_at) + '</span>' : ''}</div>
  </div>`;
}
let listSort = {k: 'speed', dir: 1};
function renderList() {
  if (!checkData) return;
  const spd = segVal($('spdSeg')), lm = segVal($('lmSeg')), hide = $('hideAds').checked;
  let rows = checkData.results.flatMap(r => (r.rows || []).map(x => ({...x, currency: r.currency})));
  rows = rows.filter(x => (spd === 'all' || x.speed === spd || x.origin === spd) && (lm === 'all' || x.market === lm) && !(hide && x.sponsored));
  const order = {fast: 0, slow: 1, unknown: 2}, oorder = {local: 0, unknown: 1, overseas: 2};
  const key = {speed: x => order[x.speed] * 100 + (x.days ?? 99), origin: x => oorder[x.origin] * 100 + (x.days ?? 99), market: x => x.market,
               days: x => x.days ?? 999, price: x => x.price ?? -1, reviews: x => x.reviews ?? -1, rating: x => x.rating ?? -1,
               bought: x => x.bought ?? -1, title: x => x.title}[listSort.k];
  rows.sort((a, b) => { const A = key(a), B = key(b); return (A > B ? 1 : A < B ? -1 : 0) * listSort.dir; });
  const cols = [['market', 'Market'], ['origin', 'Ships from'], ['speed', 'Delivery'], ['days', 'Days', 'r'], ['price', 'Price', 'r'],
                ['reviews', 'Reviews', 'r'], ['rating', 'Rating', 'r'], ['bought', 'Bought/mo', 'r'], ['title', 'Product']];
  const sp = {fast: '<span class="pill good"><i></i>fast</span>', slow: '<span class="pill warn"><i></i>slow</span>', unknown: '<span class="pill weak">no date</span>'};
  const og = x => x.origin === 'local' ? '<span class="pill line"><span class="swatch" style="background:var(--s-local)"></span>local</span>'
    : x.origin === 'overseas' ? `<span class="pill line"><span class="swatch" style="background:var(--s-overseas)"></span>${x.intl ? 'international' : 'abroad'}</span>`
    : '<span class="pill weak">unknown</span>';
  $('listTable').innerHTML = '<thead><tr>' + cols.map(([k, h, c]) => `<th class="${c || ''}" data-k="${k}">${h}${listSort.k === k ? (listSort.dir > 0 ? ' ↑' : ' ↓') : ''}</th>`).join('') + '</tr></thead><tbody>' +
    (rows.length ? rows.map(x => `<tr class="${x.speed}">
      <td>${MK_SHORT[x.market] || x.market}</td><td title="${esc(x.why || '')}">${og(x)}<div class="sub">${esc(x.why || '')}</div></td>
      <td>${sp[x.speed] || ''}${x.prime ? ' <span class="pill accent">prime</span>' : ''}${x.sponsored ? ' <span class="pill weak">ad</span>' : ''}</td>
      <td class="r">${x.days ?? ''}</td><td class="r">${money(x.price, '')}</td><td class="r">${num(x.reviews)}</td><td class="r">${x.rating ?? ''}</td><td class="r">${x.bought ? num(x.bought) + '+' : ''}</td>
      <td class="title"><a data-open="${esc(x.url)}">${esc(x.title)}</a><div class="sub">${esc(x.delivery)}</div></td></tr>`).join('')
      : '<tr><td colspan="9" class="empty">No listings match these filters.</td></tr>') + '</tbody>';
}
$('listTable').addEventListener('click', e => {
  const th = e.target.closest('th[data-k]'); if (!th) return;
  listSort = {k: th.dataset.k, dir: listSort.k === th.dataset.k ? -listSort.dir : 1}; renderList();
});
seg($('spdSeg'), renderList); seg($('lmSeg'), renderList);
$('hideAds').addEventListener('change', renderList);
$('btnExportCheck').addEventListener('click', () => currentJob ? download({kind: 'check', job: currentJob}) : toast('Run a new check to save it'));

/* ---------- Rank a file ---------- */
let rankData = null, rankFile = null, local = {};
seg($('rkMk'), () => rankFile && rankUpload(rankFile));
seg($('rkShow'), () => renderRank());
$('drop').addEventListener('click', () => $('file').click());
$('drop').addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('file').click(); } });
$('file').addEventListener('change', () => { if ($('file').files[0]) rankUpload($('file').files[0]); $('file').value = ''; });
['dragover', 'dragenter'].forEach(t => $('drop').addEventListener(t, e => { e.preventDefault(); $('drop').classList.add('over'); }));
['dragleave', 'drop'].forEach(t => $('drop').addEventListener(t, e => { e.preventDefault(); $('drop').classList.remove('over'); }));
$('drop').addEventListener('drop', e => { const f = e.dataTransfer.files[0]; if (f) rankUpload(f); });
async function rankUpload(f) {
  rankFile = f;
  $('drop').querySelector('b').textContent = 'Reading ' + f.name + '…';
  try {
    rankData = await api('/api/rank?name=' + encodeURIComponent(f.name) + '&market=' + segVal($('rkMk')) + '&top=100', f, true);
    local = {};
    $('drop').querySelector('b').textContent = f.name + ' — drop another file to replace it';
    renderRank();
  } catch (e) { $('drop').querySelector('b').textContent = 'Drop a Helium 10 export here'; toast(e.message, 8000); }
}
function shownRows() {
  const v = segVal($('rkShow'));
  return v === 'good' ? rankData.rows.filter(r => r.verdict === 'Good pick') : rankData.rows.slice(0, +v);
}
function renderRank() {
  if (!rankData) return;
  const d = rankData, cur = d.target.currency, c = d.counts;
  $('rankOut').hidden = false;
  $('rankTitle').textContent = MK[d.market] + ' · ' + d.file;
  $('rankTarget').textContent = `Targets: ${cur} ${d.target.price[0]}–${d.target.price[1]}, at least ${d.target.sales} sales/month, at most ${d.target.reviews} reviews`;
  const k = (v, l, s) => `<div class="kpi"><span class="eyebrow">${l}</span><span class="v">${v}</span>${s ? '<span class="s">' + s + '</span>' : ''}</div>`;
  $('rankKpis').innerHTML = k(num(d.total), 'Products') + k(c['Good pick'] || 0, 'Good picks', 'targets met, no flags') +
    k(c['Check first'] || 0, 'Check first', 'targets met, read the flags') + k(c['Close'] || 0, 'Close', 'almost') + k(c['Skip'] || 0, 'Skip');
  const vc = {'Good pick': ['good', 'ok'], 'Check first': ['warn', 'warn'], 'Close': ['weak', 'dot'], 'Skip': ['bad', 'x']};
  $('rankTable').innerHTML = `<thead><tr><th class="r">#</th><th>Verdict</th><th>Product</th><th class="r">Price</th><th class="r">Sales/mo</th><th class="r">Revenue/mo</th><th class="r">Reviews</th><th class="r">Rating</th><th class="r">Age</th><th class="r">90-day trend</th><th>Local sellers</th><th>Search words</th></tr></thead><tbody>` +
    shownRows().map(r => {
      const L = local[r.asin], v = vc[r.verdict] || ['weak', 'dot'];
      const lc = !L ? `<button class="btn slim" data-check="${esc(r.asin)}">Check</button>` : L === 'busy' ? '<span class="spin"></span>' : L.error ? `<span class="pill bad" title="${esc(L.error)}">failed</span>` :
        `<span class="pill ${LEVEL[L.level] || 'weak'}" title="${esc(L.verdict)} Searched: ${esc(L.keyword)}"><i></i>${L.fast} fast / ${L.total}</span><div class="sub">${L.local ?? '?'} local · ${L.overseas ?? '?'} abroad</div>`;
      const tr = r.trend == null ? '' : `<span class="${r.trend >= 0 ? 'up' : 'down'}">${r.trend > 0 ? '+' : ''}${num(r.trend)}%</span>`;
      return `<tr><td class="r">${r.rank}</td><td><span class="pill ${v[0]}">${icon(v[1])}${esc(r.verdict)}</span></td>
        <td class="title"><a data-open="${esc(r.url || '')}">${esc(r.title)}</a><div class="sub">${esc(r.brand)}${r.asin ? ' · ' + esc(r.asin) : ''}</div>
        ${r.flags.length ? '<div class="flags">' + r.flags.map(f => '<span class="flag">' + esc(f) + '</span>').join('') + '</div>' : ''}</td>
        <td class="r">${money(r.price, '')}</td><td class="r">${num(r.sales)}</td><td class="r">${money(r.revenue, '', 0)}</td><td class="r">${num(r.reviews)}</td>
        <td class="r">${r.rating && r.reviews ? r.rating : ''}</td><td class="r">${r.age != null ? num(r.age) + ' mo' : ''}</td><td class="r">${tr}</td>
        <td>${lc}</td><td><input type="text" class="cellin" value="${esc(rankSearch(r))}" data-search="${esc(r.asin)}" title="Words used to search Amazon" aria-label="Search words for ${esc(r.asin)}"></td></tr>`;
    }).join('') + '</tbody>';
}
$('rankTable').addEventListener('click', e => { const b = e.target.closest('[data-check]'); if (b) batch([b.dataset.check]); });
$('btnBatch').addEventListener('click', () => batch(shownRows().map(r => r.asin).filter(a => !local[a] || local[a].error)));
async function batch(asins) {
  if (!asins.length) return toast('These are already checked');
  const items = asins.map(a => {
    const r = rankData.rows.find(x => x.asin === a);
    return {key: a, market: rankData.market, search: (r ? rankSearch(r) : '').trim()};
  }).filter(i => i.search);
  if (!items.length) return toast('Type the search words first');
  items.forEach(i => { local[i.key] = 'busy'; }); renderRank();
  $('batchProgress').innerHTML = busy(`Checking ${items.length} product${items.length > 1 ? 's' : ''} on ${MK[rankData.market]}, 3 at a time…`);
  try {
    const {job} = await api('/api/check_batch', {items});
    Object.assign(local, await waitJob(job, line => { $('batchProgress').innerHTML = busy(line); }));
    $('batchProgress').textContent = '';
  } catch (e) { items.forEach(i => { local[i.key] = {error: e.message}; }); $('batchProgress').innerHTML = failPill(e.message); }
  items.forEach(i => { if (local[i.key] === 'busy') local[i.key] = {error: 'no result'}; });
  renderRank();
}
$('btnExportRank').addEventListener('click', () => rankData && download({kind: 'rank', id: rankData.id,
  fast: Object.fromEntries(Object.entries(local).filter(([, v]) => v && v.fast != null))}));

/* ---------- Product ideas ---------- */
let iData = null, iLocal = {};
$('iKinds').dataset.multi = '1';
seg($('iMk'), () => { $('iCatsWrap').hidden = true; $('iCats').innerHTML = ''; });
seg($('iKinds'));
const trends = (kw, mk) => 'https://trends.google.com/trends/explore?date=today%205-y&geo=' + (mk || 'US') + '&q=' + encodeURIComponent(kw);
function localPill(L, mk) {
  if (!L) return '';
  const name = MK_SHORT[mk] || mk;
  if (L === 'busy') return `<span class="pill weak"><span class="spin" style="width:11px;height:11px"></span>${name}</span>`;
  if (L.error) return `<span class="pill bad" title="${esc(L.error)}">${icon('x')}${name}: failed</span>`;
  const gap = L.total && L.local <= 4;
  return `<span class="pill ${gap ? 'good' : LEVEL[L.level] || 'weak'}" title="${esc(L.verdict)} Searched: ${esc(L.keyword)}">${gap ? icon('ok') : '<i></i>'}${name}: ${L.local} local · ${L.overseas} abroad${gap ? ' · gap' : ''}</span>`;
}
$('btnIdeas').addEventListener('click', () => runIdeas(false));
$('iCats').addEventListener('click', e => { const b = e.target.closest('.chip'); if (b) b.setAttribute('aria-pressed', b.getAttribute('aria-pressed') === 'true' ? 'false' : 'true'); });
$('iHide').addEventListener('change', () => { if (iData) runIdeas(false); });
async function runIdeas(refresh) {
  const kinds = pressed($('iKinds'));
  if (!kinds.length) return toast('Pick at least one list');
  const slugs = $('iCatsWrap').hidden ? [] : [...$('iCats').querySelectorAll('[aria-pressed="true"]')].map(b => b.dataset.slug);
  $('btnIdeas').disabled = true;
  $('iProg').innerHTML = busy('Starting…');
  try {
    const {job} = await api('/api/ideas', {market: segVal($('iMk')), kinds, slugs, hide_risky: $('iHide').checked, refresh});
    const prevMarket = iData && iData.market;
    iData = await waitJob(job, line => { $('iProg').innerHTML = busy(line); });
    if (iData.market !== prevMarket) iLocal = {};
    $('iProg').textContent = '';
    renderIdeas();
  } catch (e) { $('iProg').innerHTML = failPill(e.message); }
  $('btnIdeas').disabled = false;
}
function sortedIdeas() {
  const s = $('iSort').value, list = [...(iData.ideas || [])];
  const k = {score: x => -x.score, pct: x => -(x.pct || 0), new: x => x.new_rank || 999, reviews: x => x.reviews ?? 1e9}[s];
  return list.sort((a, b) => k(a) - k(b));
}
const ideaChecks = mk => mk === 'US' ? ['AU', 'AE'] : [mk === 'AU' ? 'AE' : 'AU'];
function renderIdeas() {
  if (!iData) return;
  const d = iData, cur = (ST.markets[d.market] || {}).currency || '';
  $('iEmpty').hidden = true;
  $('iCatsWrap').hidden = false;
  $('iCats').innerHTML = d.all_categories.map(c => `<button type="button" class="chip${c.risky ? ' risky' : ''}" data-slug="${esc(c.slug)}" aria-pressed="${c.on}" title="${c.risky ? 'Category with extra rules or big brands' : ''}">${esc(c.name)}</button>`).join('');
  $('iOut').hidden = false;
  $('iTitle').textContent = d.ideas.length + ' product ideas on Amazon ' + (MK[d.market] || d.market);
  const boom = d.ideas.filter(i => i.label === 'Booming').length, nw = d.ideas.filter(i => i.lists.includes('new')).length;
  $('iSub').textContent = `${d.scanned} products read from ${d.lists} lists · ${boom} booming · ${nw} new releases · scanned ${ago(d.at)}`;
  $('btnIdeasCheck').hidden = d.market !== 'US';
  const lab = {Booming: 'warn', Rising: 'accent', New: 'good', Moving: 'weak'};
  const checks = ideaChecks(d.market);
  $('iGrid').innerHTML = sortedIdeas().map(i => `<div class="panel icard">
      <div class="pic">${i.image ? `<img src="${esc(i.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : ''}</div>
      <div class="body">
        <div class="row" style="gap:6px"><span class="pill ${lab[i.label] || 'weak'}">${esc(i.label)}${i.pct ? ' +' + num(i.pct) + '%' : ''}</span>
          ${i.lists.includes('new') ? `<span class="pill good">New #${i.new_rank}</span>` : ''}
          ${i.similar ? `<span class="pill accent" title="Other products like this are moving too">+${i.similar} similar</span>` : ''}
          <span class="small muted" style="margin-left:auto">${esc(i.category)}</span></div>
        <a class="clamp2" data-open="${esc(i.url)}" style="cursor:pointer;color:var(--ink);font-weight:550;text-decoration:none">${esc(i.title)}</a>
        <div class="small">${i.price != null ? money(i.price, cur) : 'no price'} · ${i.rating ? '★ ' + i.rating : 'no rating'} · ${i.reviews != null ? num(i.reviews) + ' reviews' : 'no reviews yet'}${i.rank_before ? ` · rank ${num(i.rank_before)} → ${num(i.rank_now)}` : ''}</div>
        ${i.flags.length ? '<div class="flags">' + i.flags.map(f => '<span class="flag">' + esc(f) + '</span>').join('') + '</div>' : ''}
      </div>
      <div class="acts">
        <input type="text" class="kwin" value="${esc(ideaSearch(i))}" data-kw="${esc(i.asin)}" title="Words used to search the other Amazon sites" aria-label="Search words">
        ${checks.map(mk => localPill(iLocal[i.asin + ':' + mk], mk)).join('')}
        <button class="btn slim" data-icheck="${esc(i.asin)}">Check ${checks.map(m => MK_SHORT[m]).join(' & ')}</button>
        <button class="btn slim" data-trend="${esc(i.asin)}" title="Google Trends, last 5 years">Trends${icon('external')}</button>
        <button class="btn slim" data-ixray="${esc(i.asin)}">Xray</button>
      </div></div>`).join('') || '<div class="panel empty">No ideas left after hiding risky products. Untick “Hide risky products” or choose other categories.</div>';
}
$('iSort').addEventListener('change', renderIdeas);
const ideaKw = asin => {
  const i = (iData.ideas || []).find(x => x.asin === asin);
  return (i ? ideaSearch(i) : '').trim();
};
$('iGrid').addEventListener('click', e => {
  const c = e.target.closest('[data-icheck]'), t = e.target.closest('[data-trend]'), x = e.target.closest('[data-ixray]');
  if (c) ideaCheck([c.dataset.icheck]);
  if (t) openUrl(trends(ideaKw(t.dataset.trend), iData.market));
  if (x) { $('xkw').value = ideaKw(x.dataset.ixray); setSeg($('xMk'), iData.market); go('xray'); }
});
$('btnIdeasCheck').addEventListener('click', () => ideaCheck(sortedIdeas().slice(0, 12).map(i => i.asin)));
async function ideaCheck(asins) {
  const checks = ideaChecks(iData.market);
  const items = asins.flatMap(a => checks.map(mk => ({key: a + ':' + mk, market: mk, search: ideaKw(a)})))
    .filter(i => i.search && !(iLocal[i.key] && iLocal[i.key].total != null));
  if (!items.length) return toast('Already checked');
  items.forEach(i => { iLocal[i.key] = 'busy'; }); renderIdeas();
  $('iCheckProg').innerHTML = busy(`Searching ${items.length} times on ${checks.map(m => MK[m]).join(' and ')}, 3 at a time…`);
  try {
    const {job} = await api('/api/check_batch', {items});
    Object.assign(iLocal, await waitJob(job, line => { $('iCheckProg').innerHTML = busy(line); }));
    $('iCheckProg').innerHTML = '<span class="small">A green <b>gap</b> means 4 or fewer local sellers there.</span>';
  } catch (e) { $('iCheckProg').innerHTML = failPill(e.message); }
  items.forEach(i => { if (iLocal[i.key] === 'busy') iLocal[i.key] = {error: 'no result'}; });
  renderIdeas();
}

/* ---------- Niche keywords ---------- */
let nData = null, nLocal = {};
$('nCheckMk').dataset.multi = '1';
seg($('nMk')); seg($('nCheckMk'), () => renderNiche());
$('nForm').addEventListener('submit', async e => {
  e.preventDefault();
  const seed = $('nSeed').value.trim(); if (!seed) return $('nSeed').focus();
  $('btnNicheFind').disabled = true;
  $('nProg').innerHTML = busy('Collecting what shoppers type…');
  try {
    const {job} = await api('/api/niche', {market: segVal($('nMk')), seed});
    nData = await waitJob(job, line => { $('nProg').innerHTML = busy(line); }); nLocal = {};
    nData.keywords.forEach((k, i) => { k.sel = i < 8; });
    $('nProg').textContent = ''; renderNiche();
  } catch (e) { $('nProg').innerHTML = failPill(e.message); }
  $('btnNicheFind').disabled = false;
});
function renderNiche() {
  if (!nData) return;
  $('nOut').hidden = false; $('nEmpty').hidden = true;
  const mks = pressed($('nCheckMk'));
  if (!nData.keywords.length) { $('nTable').innerHTML = '<tbody><tr><td class="empty">Amazon returned no suggestions. Try a shorter niche, e.g. one or two words.</td></tr></tbody>'; return; }
  const maxHits = Math.max(...nData.keywords.map(k => k.hits));
  $('nTable').innerHTML = `<thead><tr><th></th><th>Keyword</th><th>Popularity</th>${mks.map(m => `<th>${MK[m]}</th>`).join('')}<th></th></tr></thead><tbody>` +
    nData.keywords.map((k, i) => `<tr><td><input type="checkbox" data-nsel="${i}" ${k.sel ? 'checked' : ''} aria-label="Select ${esc(k.keyword)}"></td>
      <td><b>${esc(k.keyword)}</b></td>
      <td><div class="bar" style="width:90px" title="${k.hits} suggestion lists"><span style="width:${k.hits / maxHits * 100}%;background:var(--s-demand)"></span></div></td>
      ${mks.map(m => { const L = nLocal[k.keyword + '|' + m]; return `<td>${L ? localPill(L, m) + (L.total != null ? `<div class="sub">${L.bought_top ? 'demand ' + num(L.bought_top) + '+/mo' : 'no sales badges'} · ${L.fast} fast</div>` : '') : '<span class="muted small">–</span>'}</td>`; }).join('')}
      <td style="white-space:nowrap"><button class="btn slim" data-ntrend="${i}">Trends${icon('external')}</button> <button class="btn slim" data-nopen="${i}">Open${icon('external')}</button></td></tr>`).join('') + '</tbody>';
}
$('nTable').addEventListener('change', e => { const c = e.target.closest('[data-nsel]'); if (c) nData.keywords[+c.dataset.nsel].sel = c.checked; });
$('nTable').addEventListener('click', e => {
  const t = e.target.closest('[data-ntrend]'), o = e.target.closest('[data-nopen]');
  if (t) openUrl(trends(nData.keywords[+t.dataset.ntrend].keyword, nData.market));
  if (o) openUrl(((ST.markets[nData.market] || {}).site || 'https://www.amazon.com') + '/s?k=' + encodeURIComponent(nData.keywords[+o.dataset.nopen].keyword).replace(/%20/g, '+'));
});
$('nTop').addEventListener('click', () => { nData.keywords.forEach((k, i) => { k.sel = i < 8; }); renderNiche(); });
$('btnNiche').addEventListener('click', async () => {
  const mks = pressed($('nCheckMk')), kws = nData.keywords.filter(k => k.sel).slice(0, 15);
  if (!kws.length || !mks.length) return toast('Tick some keywords and at least one country');
  const items = kws.flatMap(k => mks.map(m => ({key: k.keyword + '|' + m, market: m, search: k.keyword})));
  items.forEach(i => { nLocal[i.key] = 'busy'; }); renderNiche();
  $('nProg').innerHTML = busy(`${items.length} searches, 3 at a time…`);
  try {
    const {job} = await api('/api/check_batch', {items});
    Object.assign(nLocal, await waitJob(job, line => { $('nProg').innerHTML = busy(line); }));
    $('nProg').textContent = '';
  } catch (e) { $('nProg').innerHTML = failPill(e.message); }
  items.forEach(i => { if (nLocal[i.key] === 'busy') nLocal[i.key] = {error: 'no result'}; });
  renderNiche();
});

/* ---------- Xray ---------- */
let xData = null, xWatch = null;
seg($('xMk'));
seg($('xShow'), () => renderXTable());
$('btnXOpen').addEventListener('click', () => {
  const kw = $('xkw').value.trim(); if (!kw) return $('xkw').focus();
  const site = (ST.markets[segVal($('xMk'))] || {}).site || 'https://www.amazon.com.au';
  openUrl(site + '/s?k=' + encodeURIComponent(kw).replace(/%20/g, '+'));
  startWatch();
});
$('xFile').addEventListener('change', async () => {
  const f = $('xFile').files[0]; if (!f) return;
  stopWatch();
  $('xWatch').innerHTML = busy('Analysing ' + f.name + '…');
  try { renderXray(await api('/api/xray?name=' + encodeURIComponent(f.name) + '&market=' + segVal($('xMk')) + '&keyword=' + encodeURIComponent($('xkw').value.trim()), f, true)); }
  catch (e) { $('xWatch').innerHTML = failPill(e.message); }
  $('xFile').value = '';
});
function startWatch() {
  stopWatch();
  const since = Date.now() / 1000 - 3, until = Date.now() + 20 * 60 * 1000;
  $('xWatch').innerHTML = busy('Waiting for your Xray export in Downloads… (run Xray, then Export to CSV)') +
    ' <button class="btn slim" id="xStop">Stop waiting</button>';
  $('xStop').addEventListener('click', () => { stopWatch(); $('xWatch').textContent = ''; });
  xWatch = setInterval(async () => {
    if (Date.now() > until) { stopWatch(); $('xWatch').textContent = 'Stopped waiting after 20 minutes. Choose the file instead.'; return; }
    try {
      const w = await api('/api/xray/watch?since=' + since);
      if (!w.found) return;
      stopWatch();
      $('xWatch').innerHTML = busy('Found ' + w.found.name + ', analysing…');
      renderXray(await api('/api/xray/load', {path: w.found.path, market: segVal($('xMk')), keyword: $('xkw').value.trim()}));
    } catch (e) { stopWatch(); $('xWatch').innerHTML = failPill(e.message); }
  }, 2000);
}
function stopWatch() { if (xWatch) clearInterval(xWatch); xWatch = null; }
function renderXray(a) {
  xData = a;
  $('xWatch').innerHTML = '<span class="pill good">' + icon('ok') + 'Analysed ' + esc(a.file) + '</span>';
  $('xOut').hidden = false;
  $('xMeta').textContent = a.name + ' · ' + a.metrics.listings + ' listings (' + a.metrics.sponsored + ' sponsored) · ' + a.file;
  $('xTitle').textContent = '“' + (a.keyword || 'this market') + '”';
  if (a.keyword && !$('xkw').value.trim()) $('xkw').value = a.keyword;
  const pts = Math.max(0, Math.min(10, a.points));
  const tone = a.level === 'good' ? 'good' : a.level === 'warn' ? 'warn' : 'bad';
  $('xVerdict').className = 'panel xverdict ' + a.level;
  $('xVerdict').innerHTML = `<span class="eyebrow">Verdict</span><span class="v">${icon({good: 'ok', warn: 'warn', bad: 'x'}[tone], '')} ${esc(a.verdict)}</span>
    <div class="meter" style="color:var(--${tone})" role="img" aria-label="${a.points} of 10 points">${Array.from({length: 10}, (_, i) => `<i class="${i < pts ? 'on' : ''}"></i>`).join('')}</div>
    <span class="small">${a.points} of 10 points: demand, review barrier, competition spread, newcomers, Amazon, margin</span>
    <div id="xLocal" class="small"></div>`;
  $('xReasons').innerHTML = a.reasons.map(r => `<li class="${r.level}">${esc(r.text)}</li>`).join('');
  const m = a.metrics, cur = a.currency;
  const k = (v, l, s) => `<div class="kpi"><span class="eyebrow">${l}</span><span class="v">${v}</span>${s ? '<span class="s">' + s + '</span>' : ''}</div>`;
  $('xKpis').innerHTML = k(money(m.total_revenue, cur, 0), 'Revenue / month', m.organic + ' organic listings') +
    k(num(m.top10_sales_median), 'Top 10 sales / month', 'median, target ' + a.target.sales) +
    k(m.price_p25 != null ? num(m.price_p25, 0) + '–' + num(m.price_p75, 0) : '–', 'Price (' + cur + ')', 'middle half of listings') +
    k(num(m.review_barrier), 'Review barrier', 'median reviews, top 10') +
    k(pct(m.top3_share), 'Top 3 share', m.top_brand ? 'biggest brand ' + esc(m.top_brand) + ' ' + pct(m.top_brand_share) : '') +
    k(m.new_winners, 'New winners', 'under 1 year, selling ' + a.target.sales + '+') +
    k(m.beatable, 'Beatable', 'enough sales, few reviews') +
    k(m.left_median != null ? money(m.left_median, cur) : '–', 'Left per sale', m.left_pct != null ? pct(m.left_pct) + ' of price, after fees' : 'no FBA fee column');
  const stack = (obj, names) => {
    const parts = catParts(obj, names), tot = parts.reduce((s, p) => s + p.n, 0);
    return tot ? `<div class="stack">${parts.map(p => `<span style="width:${p.n / tot * 100}%;background:${p.color}" title="${esc(p.label)}: ${p.n}"></span>`).join('')}</div>` + legend(parts)
      : '<span class="small muted">Not in this export</span>';
  };
  $('xFul').innerHTML = stack(m.fulfillment, {FBA: 'FBA (Amazon warehouse)', FBM: 'FBM (seller ships)', AMZ: 'Amazon itself'});
  $('xCountry').innerHTML = stack(m.countries, {CN: 'China', US: 'USA', AU: 'Australia', AE: 'UAE', GB: 'UK', HK: 'Hong Kong'});
  renderXTable();
  go('xray');
}
function renderXTable() {
  if (!xData) return;
  const v = segVal($('xShow'));
  const rows = xData.rows.filter(r => v === 'all' || (v === 'beat' && r.beatable) || (v === 'new' && r.new));
  $('xTable').innerHTML = `<thead><tr><th class="r">#</th><th>Product</th><th class="r">Price</th><th class="r">Sales/mo</th><th class="r">Revenue/mo</th><th class="r">Reviews</th><th class="r">Rating</th><th class="r">Age</th><th>Ships</th><th class="r">Left</th><th></th></tr></thead><tbody>` +
    (rows.length ? rows.map(r => `<tr class="${r.beatable ? 'fast' : ''}"><td class="r">${r.pos}</td>
      <td class="title"><a data-open="${esc(r.url || '')}">${esc(r.title)}</a><div class="sub">${esc(r.brand)}${r.country ? ' · ' + esc(r.country) : ''}${r.size_tier ? ' · ' + esc(r.size_tier) : ''}</div>
      ${r.flags.length ? '<div class="flags">' + r.flags.map(f => '<span class="flag">' + esc(f) + '</span>').join('') + '</div>' : ''}</td>
      <td class="r">${money(r.price, '')}</td><td class="r">${num(r.sales)}</td><td class="r">${money(r.revenue, '', 0)}</td><td class="r">${num(r.reviews)}</td>
      <td class="r">${r.rating && r.reviews ? r.rating : ''}</td><td class="r">${r.age != null ? num(r.age) + ' mo' : ''}</td>
      <td>${esc(r.fulfillment)}${r.amazon ? ' <span class="pill bad">Amazon</span>' : ''}</td><td class="r">${r.left != null ? money(r.left, '') : ''}</td>
      <td>${r.beatable ? '<span class="pill good">' + icon('ok') + 'beatable</span>' : ''}${r.new ? ' <span class="pill accent">new</span>' : ''}</td></tr>`).join('')
      : '<tr><td colspan="11" class="empty">No listings match.</td></tr>') + '</tbody>';
}
$('btnXExport').addEventListener('click', () => xData && download({kind: 'xray', id: xData.id}));
$('btnXLocal').addEventListener('click', async () => {
  if (!xData) return;
  const kw = $('xkw').value.trim() || xData.keyword;
  if (!kw) return toast('Type the keyword first');
  $('xLocal').innerHTML = busy('Checking how many sellers deliver fast…');
  try {
    const {job} = await api('/api/check', {keyword: kw, markets: [xData.market]});
    const r = (await waitJob(job)).results[0];
    $('xLocal').innerHTML = r.error ? failPill(r.error) :
      `<span class="pill ${LEVEL[r.level] || 'weak'}"><i></i>${r.local} local · ${r.overseas} not local · ${r.fast} fast (of ${r.total})</span> ${esc(r.verdict)}`;
  } catch (e) { $('xLocal').innerHTML = failPill(e.message); }
});
$('btnReport').addEventListener('click', () => {
  if (!checkData) return;
  const first = checkData.results.find(r => !r.error) || checkData.results[0];
  go('k', first.market, checkData.keyword);
});
$('btnToXray').addEventListener('click', () => {
  if (!checkData) return;
  $('xkw').value = checkData.keyword; go('xray'); $('btnXOpen').focus();
});

/* ---------- History ---------- */
async function loadHistory() {
  try {
    const h = await api('/api/history');
    $('hist').innerHTML = h.length ? h.map(x => `<button class="panel hitem" data-hist="${esc(x.id)}">
        <div class="head"><h3>${esc(x.keyword)}</h3><span class="small">${ago(x.at)}</span></div>
        <div class="row" style="gap:6px">${x.markets.map(m => `<span class="pill ${LEVEL[m.level] || 'weak'}" title="${m.fast} deliver fast"><i></i>${MK_SHORT[m.market]} · ${m.local} local · ${m.overseas} abroad / ${m.total}</span>`).join('')}</div></button>`).join('')
      : '<div class="panel empty"><h2>No checks yet</h2><p>Your checks appear here, so you can open them again without a new search.</p></div>';
  } catch (e) { $('hist').innerHTML = failPill(e.message); }
}
$('hist').addEventListener('click', async e => {
  const b = e.target.closest('[data-hist]'); if (!b) return;
  try {
    const item = await api('/api/history/item?id=' + encodeURIComponent(b.dataset.hist));
    currentJob = null; $('kw').value = item.keyword;
    renderCheck({keyword: item.keyword, results: item.results.map(r => ({...r, cached: true}))});
    go('check');
  } catch (err) { toast(err.message); }
});

/* ---------- Settings ---------- */
let settingsFilled = false;
function fillSettings() {
  if (!ST.settings || !ST.settings.locations) return;
  settingsFilled = true;
  const s = ST.settings;
  $('profile').innerHTML = (ST.profiles.length ? ST.profiles : [{id: '', label: 'Default browser (no Chrome profiles found)'}])
    .map(p => `<option value="${esc(p.id)}" ${p.id === s.profile ? 'selected' : ''}>${esc(p.label)}</option>`).join('');
  $('locAU').value = s.locations.AU; $('locAE').value = s.locations.AE; $('locUS').value = s.locations.US;
  $('fastDays').value = s.fast_days; $('pages').value = s.pages; $('cacheH').value = s.cache_hours; $('localDays').value = s.local_days;
}
async function saveSettings() {
  try {
    const r = await api('/api/settings', {profile: $('profile').value, fast_days: $('fastDays').value, pages: $('pages').value,
      cache_hours: $('cacheH').value, local_days: $('localDays').value,
      locations: {AU: $('locAU').value, AE: $('locAE').value, US: $('locUS').value}});
    ST.settings = r.settings; settingsFilled = false; fillSettings(); renderStatus(); toast('Settings saved', 1800);
  } catch (e) { toast(e.message); }
}
['profile', 'locAU', 'locAE', 'locUS', 'fastDays', 'localDays', 'pages', 'cacheH'].forEach(id => $(id).addEventListener('change', saveSettings));
$('btnProfiles').addEventListener('click', async () => {
  try { ST.profiles = await api('/api/profiles', {}); settingsFilled = false; fillSettings(); toast(ST.profiles.length + ' profiles found'); }
  catch (e) { toast(e.message); }
});
$('btnTestProfile').addEventListener('click', () => openUrl('https://members.helium10.com/'));
$('btnLoc').addEventListener('click', () => {
  api('/api/browser', {action: 'locations'}).catch(e => toast(e.message));
  $('locNote').textContent = 'Setting… watch the pills at the top.';
});
const browser = a => api('/api/browser', {action: a}).catch(e => toast(e.message));
$('btnShow2').addEventListener('click', () => browser('show'));
$('btnHide').addEventListener('click', () => browser('hide'));
$('btnRestart').addEventListener('click', () => browser('restart'));

function logLine(m) {
  const el = $('log'), d = new Date(m.t * 1000);
  el.textContent += d.toLocaleTimeString() + '  ' + m.msg + '\n';
  if (el.textContent.length > 20000) el.textContent = el.textContent.slice(-15000);
  el.scrollTop = el.scrollHeight;
}
onState((s, fresh) => {
  for (const m of fresh) { logLine(m); if (/captcha|could not|failed/i.test(m.msg)) toast(m.msg, 9000); }
  const b = s.browser || {}, bs = BROWSER_STATE[b.state] || ['weak', b.state];
  $('bState').className = 'pill ' + bs[0];
  $('bState').innerHTML = '<i></i>' + esc(bs[1]) + (b.visible ? ' (visible)' : ' (minimised)');
  const prof = (s.profiles || []).find(p => p.id === s.settings.profile);
  $('xProf').textContent = prof ? prof.label : 'your default browser';
  if (!settingsFilled) fillSettings();
});
