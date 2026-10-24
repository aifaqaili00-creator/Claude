'use strict';
/* Product board (#/board, #/board/<id>): your products from Idea to Approval, Sourcing & PO, Launching strategy and
   Re-order, with trophies, deadlines and the reminder emails they send. Also binds Settings, Email for reminders. */
(() => {
  const STAGES = [['idea', 'Idea'], ['precheck', 'Pre-check'], ['approval', 'Approval'], ['sourcing', 'Sourcing & PO'], ['launch', 'Launching strategy'], ['reorder', 'Re-order']];
  const STAGE = Object.fromEntries(STAGES);
  const STEPS = STAGES.filter(([k]) => k !== 'precheck');   // Pre-check is a holding column: no checks, tab or progress of its own
  const COLORS = ['blue', 'violet', 'teal', 'green', 'amber', 'orange', 'red', 'pink', 'gray'];
  const STATUS = [['todo', 'To check'], ['done', 'Done'], ['adjusted', 'Changed'], ['skip', 'Not relevant']];
  const KIND = {start: 'New task email', reminder: 'Reminder', meeting: 'Meeting invite'};
  const CAT = {price: 'Price', money: 'Profit', reviews: 'Reviews', competition: 'Competition', product: 'Product',
    listing: 'Listing', timing: 'Timing', sales: 'Sales target', trend: 'Search trend', search: 'Research', own: 'Your own'};
  const STEP = [['deposit', 'Deposit paid'], ['produced', 'Produced'], ['shipped', 'Shipped'], ['arrived', 'Arrived']];
  const EMPTY = {idea: 'New products start here. Use Add product, or Add to board on the Trend radar or in a report.',
    precheck: 'Products move here once they have an Amazon ASIN and an Alibaba link. Drag them on to Approval when the pre-check is done.',
    approval: 'Drag products here from Pre-check (they need the Amazon ASIN and the Alibaba link).',
    sourcing: 'Products move here once every approval check is ticked.',
    launch: 'Products move here when their order is complete.', reorder: 'Products move here once they are live on Amazon.'};
  const LOCKED = {approval: 'Locked: add the Amazon ASIN and the Alibaba link first', sourcing: 'Locked: tick every approval check first',
    launch: 'Locked: finish Sourcing & PO first', reorder: 'Locked: launch it first'};
  const EV = {trophy: 'trophy', mail: 'mail', task: 'clock', stock: 'box', arrived: 'box', moved: 'board', created: 'plus',
    note: 'file', strategy: 'file', reorder: 'refresh', live: 'ok', hint: 'ok', po: 'file', edit: 'file', archived: 'trash', restored: 'plus'};
  const CONF = ['#2f6fd6', '#7a5af8', '#0e9384', '#f5b700', '#f04438', '#17b26a'];
  const MKS = ['US', 'AU', 'AE'];
  const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  let B = null, D = null, openId = null, tab = null, orderN = null, lastRev = null, drag = null, dlOpen = null, binOpen = false, stratEdit = null;
  const filt = {mk: '', q: '', stage: '', overdue: false, tag: ''};
  let mode = 'board', editId = null, month = null, TL = null;
  const purging = new Set();

  view('board', {title: 'Board', sub: 'Your products from idea to re-order', onShow: p => {
    load();
    if (p[0]) openCard(+p[0]); else if ($('bDlg').open) $('bDlg').close();
  }});

  /* ---------- formatting ---------- */
  const dayOf = iso => { const [y, m, d] = String(iso).split('-').map(Number); return new Date(y, m - 1, d); };
  const niceDay = iso => { if (!iso) return ''; const d = dayOf(iso); return `${DAYS[d.getDay()]} ${d.getDate()} ${MON[d.getMonth()]}`; };
  const isoOf = ts => { const d = new Date(ts * 1000); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
  const niceTime = ts => { const d = new Date(ts * 1000); return `${niceDay(isoOf(ts))}, ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; };
  const mine = (usd, d = 0) => { if (usd == null) return '–'; const v = fxConvert(usd, 'USD', myCur()); return v == null ? curMoney(usd, 'USD', d) : curMoney(v, myCur(), d); };
  const local = (v, mk) => v == null ? '–' : curMoney(v, MK_CUR[mk], 2);
  const evIcon = k => icon(EV[k] || 'dot');
  const kpi = (v, label, tone = '') => `<div class="bh-kpi ${tone}"><b>${v}</b><span>${esc(label)}</span></div>`;
  const curSel = (field, cur, attrs) => `<select data-f="${field}" ${attrs} aria-label="Currency">${CURRENCIES.map(c => `<option value="${c}"${c === cur ? ' selected' : ''}>${CUR_SYM[c]}</option>`).join('')}</select>`;
  const typing = () => { const a = document.activeElement; return !!(a && $('bDlg').contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)); };
  const names = list => { const f = (list || []).map(n => String(n).split(' ')[0]).filter(Boolean); return f.length < 2 ? f.join('') : f.slice(0, -1).join(', ') + ' and ' + f[f.length - 1]; };
  const lastUpdate = l => l.opened ? `Last product update opened in your browser ${esc(niceTime(l.sent_at))}, for ${esc(names(l.to))}`
    : `Last product update emailed ${esc(niceTime(l.sent_at))} to ${esc(names(l.to))}`;
  const MAIL_DONE = {sent: 'sent', opened: 'opened in your browser'};

  /* ---------- the board ---------- */
  async function load() {
    try { B = await api('/api/board'); } catch (e) { $('boardOut').innerHTML = failPill(e.message); return; }
    lastRev = B.rev;
    render();
  }
  onState(s => {
    if (currentView !== 'board' || s.board_rev == null || s.board_rev === lastRev) return;
    lastRev = s.board_rev;
    load();
    if (openId && !typing()) loadCard();
  });
  window.addEventListener('pc:currency', () => { if (currentView === 'board' && B) { render(); if (D) drawCard(); } });

  function render() {
    const cards = B.cards, got = cards.reduce((a, c) => a + (c.milestones.order_ready ? 1 : 0) + (c.milestones.launched ? 1 : 0), 0);
    const share = cards.length ? Math.round(got / (cards.length * 2) * 100) : 0;
    const inf = B.inform || {};
    const status = inf.next_at ? `${icon('clock')}<span>Products changed: the update email goes to everyone about <b>${esc(niceTime(inf.next_at))}</b>, unless you change more.</span>`
      : inf.sent_at ? `${icon('ok')}<span>${lastUpdate(inf)}.</span>` : '';
    $('boardOut').innerHTML = `
      <div class="panel bhead">
        <div class="bh-top">
          <div class="bh-kpis">
            ${kpi(num(cards.length), cards.length === 1 ? 'product' : 'products')}
            ${kpi(mine(B.invested_usd), 'invested in orders')}
            ${kpi(icon('trophy') + num(B.trophies), B.trophies === 1 ? 'trophy' : 'trophies', 'gold')}
            ${kpi(num(B.low), 'low on stock', B.low ? 'bad' : '')}
            ${kpi(num(B.late), 'late', B.late ? 'bad' : '')}
          </div>
          <div class="row bh-tools">
            <div class="seg slim" id="bMode" role="group" aria-label="View"><button type="button" data-v="board" aria-pressed="${mode === 'board'}">${icon('board')}Board</button><button type="button" data-v="timeline" aria-pressed="${mode === 'timeline'}">${icon('clock')}Timeline</button></div>
            ${B.live && B.live.length ? `<button type="button" class="btn" id="bLive" title="Units left and daily sales from Amazon (SP-API): ${esc(B.live.map(m => MK[m]).join(', '))}">${icon('refresh')}Update from Amazon</button>` : ''}
            <button type="button" class="btn" id="bInform" title="Email everyone on your Notifier list where each product stands">${icon('mail')}Inform attendees</button>
            <button type="button" class="btn" id="bDeadlines" title="Email everyone on your Notifier list just the deadlines: each product and its deadline">${icon('clock')}Inform deadlines</button>
            <button type="button" class="btn adv" id="bSum">${icon('copy')}Weekly summary</button>
            <button type="button" class="btn" id="bPeople">${icon('users')}Notifiers (${B.notifiers.length})</button>
            <button type="button" class="btn primary" id="bNew" title="Add a product (N)">${icon('plus')}Add product</button>
          </div>
        </div>
        <div class="row bfilter" role="search">
          <input type="search" id="bFind" placeholder="Find a product (/)" value="${esc(filt.q)}" aria-label="Find a product">
          <div class="seg slim" id="bMk" role="group" aria-label="Market">${['', ...MKS].map(m => `<button type="button" data-v="${m}" aria-pressed="${filt.mk === m}">${m ? mkFlag(m, true) : 'All'}</button>`).join('')}</div>
          <select id="bStage" aria-label="Stage"><option value="">Every stage</option>${STAGES.map(([k, n]) => `<option value="${k}"${filt.stage === k ? ' selected' : ''}>${esc(n)}</option>`).join('')}</select>
          <select id="bTag" class="adv" aria-label="Tag"><option value="">Every tag</option>${allTags().map(t => `<option${filt.tag === t ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select>
          <label class="small adv"><input type="checkbox" id="bLate"${filt.overdue ? ' checked' : ''}> Overdue only</label>
          <select id="bSaved" class="adv" aria-label="Saved filters"><option value="">Saved filters…</option>${(B.filters || []).map(f => `<option value="${esc(f.name)}">${esc(f.name)}</option>`).join('')}</select>
          <button type="button" class="btn slim adv" id="bSaveF" title="Keep this filter">${icon('plus')}Save filter</button>
          ${(B.filters || []).length ? `<button type="button" class="btn slim ghost adv" id="bDelF" title="Delete the saved filter picked on the left">${icon('trash')}</button>` : ''}
          <span class="small muted bkeys adv" title="Keyboard">N new · / search · arrows move · Shift+← → change stage · Enter open</span>
        </div>
        <div class="bprog adv" role="img" aria-label="${share}% of the milestones reached" title="${got} of ${cards.length * 2} milestones reached (Order ready, Launched)"><span style="width:${share}%"></span></div>
        ${status ? `<div class="small binfo" role="status">${status}<button type="button" class="btn slim ghost" data-inform>See the email</button></div>` : ''}
      </div>
      ${B.mail.ready ? '' : `<div class="note small">To email deadlines and reminders to your team, set up your Gmail in <a href="#/settings">Settings, Email for reminders</a>.</div>`}
      <div class="bcols" id="bCols"${mode === 'timeline' ? ' hidden' : ''}></div>
      <div id="bTimeline"${mode === 'timeline' ? '' : ' hidden'}></div>
      ${B.recent.length ? `<div class="panel brecent adv"><h3>Latest</h3><ul>${B.recent.map(e => `<li>${evIcon(e.kind)}<span><a href="#/board/${e.card_id}" data-bopen="${e.card_id}">${esc(e.title)}</a>: ${esc(e.text)} <span class="muted">${ago(e.ts)}</span></span></li>`).join('')}</ul></div>` : ''}
      ${B.archived.length ? `<details class="small barch"${binOpen ? ' open' : ''}><summary>${icon('trash')}Removed (${B.archived.length})</summary><ul>${B.archived.filter(a => !purging.has(a.id)).map(a => `<li><span>${esc(a.title)} · ${mkFlag(a.market, true)} <span class="muted">removed ${ago(a.archived_at)}</span></span><span class="row"><button type="button" class="btn slim" data-restore="${a.id}">Put back</button><button type="button" class="btn slim ghost" data-purge="${a.id}" data-title="${esc(a.title)}">Delete for good</button></span></li>`).join('')}</ul></details>` : ''}`;
    if (mode === 'timeline') timeline(); else cols();
  }

  const allTags = () => [...new Set(B.cards.flatMap(c => c.tags || []))].sort((a, b) => a.localeCompare(b));
  function shown() {
    const q = filt.q.trim().toLowerCase();
    return B.cards.filter(c => (!filt.mk || c.markets.some(m => m.market === filt.mk)) && (!filt.stage || c.stage === filt.stage) &&
      (!filt.overdue || c.late) && (!filt.tag || (c.tags || []).includes(filt.tag)) &&
      (!q || (c.title + ' ' + c.kw + ' ' + c.key + ' ' + (c.asin || '') + ' ' + (c.tags || []).join(' ')).toLowerCase().includes(q)));
  }

  function cols() {
    const a = document.activeElement, f = a && a.closest && a.closest('[data-qform]');
    const back = f ? `[data-qform="${f.dataset.qform}"] [data-q="${a.dataset.q}"]${a.dataset.mk ? `[data-mk="${a.dataset.mk}"]` : ''}`
      : a && a.dataset && a.dataset.card ? `[data-card="${a.dataset.card}"]` : null;
    const cards = shown();
    $('bCols').innerHTML = STAGES.map(([k, name]) => {
      const list = cards.filter(c => c.stage === k).sort((a, b) => a.pos - b.pos || a.id - b.id);
      const sum = list.reduce((a, c) => a + (c.invested_usd || 0), 0);
      return `<section class="bcol st-${k}" aria-label="${esc(name)}">
        <header class="bcol-h"><i class="bdot" aria-hidden="true"></i><h3>${esc(name)}</h3><span class="count">${list.length}</span>${sum ? `<span class="small muted">${mine(sum)}</span>` : ''}</header>
        <div class="bcards" data-drop="${k}" data-why="${esc(LOCKED[k] || '')}">${list.map(cardHtml).join('') || `<div class="bempty small">${EMPTY[k]}</div>`}</div></section>`;
    }).join('');
    if (back) { const el = $('bCols').querySelector(back); if (el) el.focus(); }
  }

  /* The card face: progress ring, countdown, tags, small charts, quick actions on hover and an inline quick edit. */
  function ringSvg(r) {
    const C = 2 * Math.PI * 11;
    return `<svg class="bc-ring t-${esc(r.tone)}" viewBox="0 0 28 28" role="img" aria-label="${r.pct}% done"><title>${r.pct}% of the way from idea to re-order${r.tone === 'bad' ? ' (something is late)' : ''}</title>
      <circle cx="14" cy="14" r="11" class="rg-bg"/><circle cx="14" cy="14" r="11" class="rg-fg" stroke-dasharray="${C.toFixed(1)}" stroke-dashoffset="${(C * (1 - r.pct / 100)).toFixed(1)}" transform="rotate(-90 14 14)"/><text x="14" y="17.5" text-anchor="middle">${r.pct}</text></svg>`;
  }
  const pt = (x, y) => x.toFixed(1) + ',' + y.toFixed(1);
  function burnSvg(m, W = 112, H = 30) {
    const pts = (m.counts || []).filter(p => p[1] != null);
    if (!pts.length || !m.sent) return '';
    const t0 = pts[0][0], span = Math.max(1, pts[pts.length - 1][0] - t0), y = v => H - 2 - Math.max(0, Math.min(1, v / m.sent)) * (H - 4);
    const xy = pts.map(p => [pts.length === 1 ? W - 3 : 3 + (p[0] - t0) / span * (W - 6), y(p[1])]), low = y(m.sent * m.low_pct / 100);
    const last = pts[pts.length - 1][1];
    return `<svg class="bc-spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Units left: ${num(last)} of ${num(m.sent)}"><title>Units left in ${esc(MK[m.market])}: ${num(last)} of ${num(m.sent)}. Red line: ${m.low_pct}%, where the low-stock alert fires.</title>
      <line x1="0" x2="${W}" y1="${low.toFixed(1)}" y2="${low.toFixed(1)}" class="sp-low"/><polyline points="${xy.map(p => pt(p[0], p[1])).join(' ')}" class="sp-line"/>${xy.map(p => `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="1.8" class="sp-dot"/>`).join('')}</svg>`;
  }
  function salesSvg(m, W = 112, H = 30) {
    const s = (m.sales || []).slice(-21);
    if (!s.length) return '';
    const per = m.target ? m.target / 30.4 : null, top = Math.max(1, per || 0, ...s.map(x => x[1])) * 1.15, bw = W / s.length;
    const avg = s.reduce((a, x) => a + x[1], 0) / s.length, yT = per ? H - per / top * H : null;
    return `<svg class="bc-spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Sold a day"><title>Sold a day on Amazon ${esc(MK[m.market])} (last ${s.length} days, from Amazon's order report): about ${avg.toFixed(1)}${per ? `. Dashed line: your target, ${per.toFixed(1)} a day (${num(m.target)} a month)` : ''}.</title>
      ${s.map((x, i) => { const h = Math.max(x[1] ? 1 : 0, x[1] / top * H); return `<rect x="${(i * bw + 0.5).toFixed(1)}" y="${(H - h).toFixed(1)}" width="${Math.max(1, bw - 1).toFixed(1)}" height="${h.toFixed(1)}" class="sp-bar${per && x[1] >= per ? ' hit' : ''}"/>`; }).join('')}
      ${yT != null ? `<line x1="0" x2="${W}" y1="${yT.toFixed(1)}" y2="${yT.toFixed(1)}" class="sp-tgt"/>` : ''}</svg>`;
  }
  const amazonUrl = c => { const site = (ST.markets[c.market] || {}).site || 'https://www.amazon.com'; return c.asin ? site + '/dp/' + c.asin : site + '/s?k=' + encodeURIComponent(c.kw); };
  function quickActs(c) {
    const ali = (c.links || {}).alibaba;
    return `<div class="bc-acts" role="toolbar" aria-label="Quick actions for ${esc(c.title)}">
      <button type="button" class="bq" data-open="${esc(amazonUrl(c))}" title="${c.asin ? 'Open the listing on Amazon' : 'Search Amazon for ' + esc(c.kw)}">${icon('external')}Amazon</button>
      <button type="button" class="bq" data-open="${esc(ali || alibabaUrl(c.kw))}" title="${ali ? 'Your supplier on Alibaba' : 'Search Alibaba for ' + esc(c.kw)}">${icon('search')}Alibaba</button>
      <button type="button" class="bq" data-qmail title="Email the team where every product stands" aria-label="Email the team">${icon('mail')}</button>
      <button type="button" class="bq" data-qnote="${c.id}" title="Add a note" aria-label="Add a note">${icon('file')}</button>
      <button type="button" class="bq" data-qedit="${c.id}" title="Quick edit" aria-label="Quick edit" aria-pressed="${editId === c.id}">${icon('sliders')}</button></div>`;
  }
  function quickEdit(c) {
    const e = c.edit || {}, mks = c.markets.map(m => m.market), ordered = c.unlocked.includes('sourcing');
    return `<form class="bc-edit" data-qform="${c.id}" aria-label="Quick edit">
      <label>Stage<select data-q="stage">${STAGES.filter(([k]) => c.unlocked.includes(k)).map(([k, n]) => `<option value="${k}"${c.stage === k ? ' selected' : ''}>${esc(n)}</option>`).join('')}</select></label>
      ${ordered ? `<label>Price a unit (${esc(e.cost_cur || 'USD')})<input type="number" data-q="unit_cost" min="0" step="0.01" value="${e.unit_cost ?? ''}"></label>
        ${mks.map(mk => `<label>Units for ${esc(MK_SHORT[mk] || mk)}<input type="number" data-q="units" data-mk="${mk}" min="0" step="1" value="${(e.units || {})[mk] ?? ''}"></label>`).join('')}` : ''}
      ${e.task_id ? `<label>Due day<input type="date" data-q="due" value="${esc(e.due_day || '')}"></label>` : ''}
      ${c.milestones.order_ready ? mks.map(mk => `<label>Live on ${esc(MK_SHORT[mk] || mk)}<input type="date" data-q="live" data-mk="${mk}" value="${esc((e.live || {})[mk] || '')}"></label>`).join('') : ''}
      <div class="row"><button type="button" class="btn slim" data-qdone>Done</button><span class="small muted">Each change saves by itself.</span></div></form>`;
  }

  function cardHtml(c) {
    const ms = c.milestones, t = c.task, cd = c.countdown, won = (ms.order_ready ? 1 : 0) + (ms.launched ? 1 : 0) + (ms.reorders || 0);
    const chips = [];
    if (cd && !(cd.kind === 'reorder' && c.next && c.next.tone === 'bad')) chips.push(`<span class="bchip cd-${esc(cd.tone)}" title="${esc(niceDay(cd.day))}${cd.market ? ' · ' + esc(MK[cd.market]) : ''}">${icon(cd.kind === 'eta' ? 'box' : 'clock')}${esc(cd.text)}</span>`);
    if (c.stage === 'idea' && c.idea) chips.push(`<span class="bchip adv" title="${esc(c.idea.missing.length ? 'Still needs ' + c.idea.missing.join(' and ') : 'Amazon ASIN and Alibaba link added')}">${icon('ok')}${c.idea.done}/${c.idea.total} links</span>`);
    if (c.stage === 'approval' && c.approval && c.approval.total) chips.push(`<span class="bchip adv" title="Approval checks ticked">${icon('ok')}${c.approval.done}/${c.approval.total} checks</span>`);
    c.markets.forEach(m => {
      const s = m.stock, lv = s && s.level !== 'unknown' ? ' lv-' + s.level : '';
      const tip = MK[m.market] + (m.units ? ': ' + num(m.units) + ' units in the first order' : '') + (s && s.pct != null ? ', ' + Math.round(s.pct) + '% left' : '');
      chips.push(`<span class="bchip${lv}" title="${esc(tip)}">${cflag(m.market, '')}${s && s.pct != null ? Math.round(s.pct) + '% left' : m.units ? num(m.units) : '–'}</span>`);
      if (s && s.order_now && !(cd && cd.kind === 'reorder' && cd.market === m.market)) chips.push(`<span class="bchip lv-red" title="Re-order by ${esc(niceDay(s.reorder_by))}">${icon('warn')}Order now</span>`);
    });
    if (t && t.due && !(cd && cd.kind === 'due')) chips.push(`<span class="bchip due-${t.due}" title="${esc(STAGE[c.stage])} due ${esc(niceDay(t.due_day))}">${icon('clock')}${t.due === 'late' ? 'Late' : 'Due'} ${esc(niceDay(t.due_day))}</span>`);
    if (t && t.next_mail) chips.push(`<span class="bchip adv" title="${esc(KIND[t.next_mail.kind])}: ${esc(niceTime(t.next_mail.send_at))}">${icon('mail')}${esc(niceDay(isoOf(t.next_mail.send_at)))}</span>`);
    if (c.stage === 'launch' && c.hints.total) chips.push(`<span class="bchip adv" title="Launch hints checked">${icon('ok')}${c.hints.done}/${c.hints.total}</span>`);
    if (c.hints.moved) chips.push(`<span class="bchip lv-amber adv" title="The research changed since these hints were checked">${icon('warn')}${c.hints.moved} moved</span>`);
    const m = c.mini, charts = m ? burnSvg(m) + salesSvg(m) : '', editing = editId === c.id;
    return `<article class="bcard c-${esc(c.color)}${c.late ? ' is-late' : ''}${editing ? ' editing' : ''}" draggable="${!editing}" data-card="${c.id}" data-pos="${c.pos}" tabindex="0" aria-label="${esc(c.title)}, ${esc(STAGE[c.stage])}${cd ? ', ' + esc(cd.text) : ''}">
      ${quickActs(c)}
      <div class="bc-top">${c.image ? `<img src="${esc(c.image)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : `<div class="bc-noimg" aria-hidden="true">${icon('box')}</div>`}
        <div class="bc-txt"><div class="bc-key">${esc(c.key)} · ${(c.markets || []).length > 1 ? `<span class="bc-mks" title="${esc(c.markets.map(m => MK[m.market] || m.market).join(' · '))}">${c.markets.map(m => cflag(m.market, '')).join('')}</span>` : mkFlag(c.market, true)}${won ? ` <span class="bc-trophy" title="${won} ${won === 1 ? 'trophy' : 'trophies'}">${icon('trophy')}${won > 1 ? won : ''}</span>` : ''}</div><div class="bc-title">${esc(c.title)}</div></div>
        ${c.ring ? ringSvg(c.ring) : ''}</div>
      <div class="bseg3 adv" aria-hidden="true">${STEPS.map(([k]) => `<span class="st-${k}"><i style="width:${Math.round((c.progress[k] || 0) * 100)}%"></i></span>`).join('')}</div>
      ${c.next ? `<button type="button" class="bc-next nx-${esc(c.next.tone)}" data-ntab="${esc(c.next.tab)}"${c.next.order ? ` data-norder="${c.next.order}"` : ''} title="Open ${esc(STAGE[c.next.tab])}">${icon('chev')}<span><b>Next:</b> ${esc(c.next.text)}</span></button>` : ''}
      ${chips.length ? `<div class="bc-chips">${chips.join('')}</div>` : ''}
      ${(c.tags || []).length ? `<div class="bc-tags">${c.tags.map(x => `<button type="button" class="btag" data-tagf="${esc(x)}" title="Show only ${esc(x)}">#${esc(x)}</button>`).join('')}</div>` : ''}
      ${charts ? `<div class="bc-mini adv" title="${m.live_at ? 'From Amazon, ' + esc(ago(m.live_at)) : 'From your counts'}">${charts}</div>` : ''}
      ${editing ? quickEdit(c) : ''}
    </article>`;
  }

  const redraw = () => { if (mode === 'timeline') timeline(); else cols(); };
  $('boardOut').addEventListener('input', e => { if (e.target.id === 'bFind') { filt.q = e.target.value; redraw(); } });
  $('boardOut').addEventListener('change', async e => {
    const el = e.target;
    if (el.id === 'bStage') { filt.stage = el.value; return redraw(); }
    if (el.id === 'bTag') { filt.tag = el.value; return redraw(); }
    if (el.id === 'bLate') { filt.overdue = el.checked; return redraw(); }
    if (el.id === 'bSaved') {
      const f = (B.filters || []).find(x => x.name === el.value);
      if (f) { Object.assign(filt, {q: f.q || '', mk: f.mk || '', stage: f.stage || '', overdue: !!f.overdue, tag: f.tag || ''}); render(); $('bSaved').value = f.name; }
      return;
    }
    const form = el.closest('[data-qform]');
    if (!form) return;
    const c = B.cards.find(x => x.id === +form.dataset.qform), id = c && c.id, v = el.value, q = el.dataset.q, ed = (c && c.edit) || {};
    if (!c) return;
    if (q === 'stage') return moveWithUndo(c, v, null);
    if (q === 'unit_cost') return change('/api/board/order', {id, n: ed.n || 1, set: {unit_cost: v}});
    if (q === 'units') return change('/api/board/line', {id, n: ed.n || 1, market: el.dataset.mk, set: {units: v}});
    if (q === 'due' && v) return change('/api/board/task', {move: ed.task_id, due_day: v});
    if (q === 'live') return change('/api/board/card', {id, set: {live: {[el.dataset.mk]: v || null}}});
  });
  $('boardOut').addEventListener('submit', e => e.preventDefault());
  $('boardOut').addEventListener('click', async e => {
    if (e.target.closest('[data-open]')) return;                    // opened in your browser by the page-wide handler
    const mk = e.target.closest('#bMk button');
    if (mk) { filt.mk = mk.dataset.v; setSeg($('bMk'), filt.mk); redraw(); return; }
    const md = e.target.closest('#bMode button');
    if (md) { mode = md.dataset.v; editId = null; render(); return; }
    if (e.target.closest('#bNew')) return addDialog();
    if (e.target.closest('#bPeople')) return peopleDialog();
    if (e.target.closest('#bSum')) return summaryDialog();
    if (e.target.closest('#bInform, [data-inform], [data-qmail]')) return informDialog();
    if (e.target.closest('#bDeadlines')) return mailDialog('deadlines');
    if (e.target.closest('#bLive')) return liveNow(null, e.target.closest('#bLive'));
    if (e.target.closest('#bSaveF')) return saveFilter();
    if (e.target.closest('#bDelF')) {
      const name = $('bSaved').value;
      if (!name) { toast('Pick the saved filter to delete first.'); $('bSaved').focus(); return; }
      try { B.filters = (await api('/api/board/filters', {remove: name})).filters; render(); toast('Filter deleted', 2500); } catch (err) { toast(err.message); }
      return;
    }
    const tg = e.target.closest('[data-tagf]');
    if (tg) { filt.tag = filt.tag === tg.dataset.tagf ? '' : tg.dataset.tagf; render(); return; }
    const qe = e.target.closest('[data-qedit]');
    if (qe) { editId = editId === +qe.dataset.qedit ? null : +qe.dataset.qedit; cols(); const f = $('bCols').querySelector(`[data-qform="${editId}"] [data-q]`); if (f) f.focus(); return; }
    if (e.target.closest('[data-qdone]')) { editId = null; cols(); return; }
    if (e.target.closest('.bc-edit')) return;
    const qn = e.target.closest('[data-qnote]');
    if (qn) return noteDialog(+qn.dataset.qnote);
    const tl = e.target.closest('[data-tlnav]');
    if (tl) { month = tl.dataset.tlnav === 'today' ? null : shiftMonth(month || thisMonth(), +tl.dataset.tlnav); timeline(); return; }
    const rs = e.target.closest('[data-restore]');
    if (rs) { await change('/api/board/archive', {id: +rs.dataset.restore, archived: false}); return; }
    const pg = e.target.closest('[data-purge]');
    if (pg) {
      const id = +pg.dataset.purge;
      purging.add(id);
      render();
      undoToast(`"${pg.dataset.title}" is deleted for good in 10 seconds, with its orders, counts, notes and history`,
        () => { purging.delete(id); render(); },
        async undone => { if (undone) return; purging.delete(id); if (await change('/api/board/delete', {id})) toast('Deleted', 2500); });
      return;
    }
    const bin = e.target.closest('.barch summary');
    if (bin) binOpen = !bin.parentElement.open;
    const op = e.target.closest('[data-bopen]');
    if (op) { e.preventDefault(); openCard(+op.dataset.bopen); return; }
    const nx = e.target.closest('[data-ntab]');
    if (nx) { openCard(+nx.closest('[data-card]').dataset.card, nx.dataset.ntab, +nx.dataset.norder || null); return; }
    const card = e.target.closest('[data-card]');
    if (card) openCard(+card.dataset.card);
  });
  $('boardOut').addEventListener('keydown', e => {
    const card = e.target.closest('[data-card]');
    if (!card || e.target !== card) return;                        // typing in the quick edit is not a shortcut
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openCard(+card.dataset.card); return; }
    if (!e.key.startsWith('Arrow')) return;
    e.preventDefault();
    const c = B.cards.find(x => x.id === +card.dataset.card);
    if (e.shiftKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
      const i = STAGES.findIndex(([k]) => k === c.stage), to = (STAGES[i + (e.key === 'ArrowRight' ? 1 : -1)] || [])[0];
      if (!to) return;
      if (!c.unlocked.includes(to)) { toast(LOCKED[to]); return; }
      moveWithUndo(c, to, null).then(() => { const el = $('bCols').querySelector(`[data-card="${c.id}"]`); if (el) el.focus(); });
      return;
    }
    const colEls = [...$('bCols').querySelectorAll('.bcards')], ci = colEls.findIndex(x => x.contains(card));
    const inCol = x => [...x.querySelectorAll('[data-card]')], here = inCol(colEls[ci]), ri = here.indexOf(card);
    let next = null;
    if (e.key === 'ArrowUp') next = here[ri - 1];
    else if (e.key === 'ArrowDown') next = here[ri + 1];
    else for (let j = ci + (e.key === 'ArrowRight' ? 1 : -1); j >= 0 && j < colEls.length && !next; j += e.key === 'ArrowRight' ? 1 : -1) {
      const list = inCol(colEls[j]);
      next = list[Math.min(ri, list.length - 1)];
    }
    if (next) next.focus();
  });

  /* Shortcuts while the board is open: N new product, / find, arrows to reach the first card, Ctrl+Z undo. */
  let undoLast = null;
  document.addEventListener('keydown', e => {
    if (currentView !== 'board' || !B || $('bDlg').open || $('bMini').open || e.altKey || e.metaKey) return;
    const a = document.activeElement;
    if (a && (/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) || a.isContentEditable)) return;
    if (e.ctrlKey) { if (e.key.toLowerCase() === 'z' && undoLast) { e.preventDefault(); undoLast(); } return; }
    if (e.key === 'n' || e.key === 'N') { e.preventDefault(); addDialog(); return; }
    if (e.key === '/') { e.preventDefault(); $('bFind').focus(); $('bFind').select(); return; }
    if (e.key.startsWith('Arrow') && mode === 'board' && !(a && a.closest && a.closest('[data-card]'))) {
      const first = $('bCols').querySelector('[data-card]');
      if (first) { e.preventDefault(); first.focus(); }
    }
  });

  /* "Undo" for 10 seconds after moving, removing or deleting a product. */
  const UNDO_MS = 10000;
  function undoToast(msg, undo, onEnd) {
    const t = document.createElement('div');
    t.className = 'toast bundo';
    t.setAttribute('role', 'status');
    t.innerHTML = `<span>${esc(msg)}</span><button type="button" class="btn slim">Undo</button><i style="animation-duration:${UNDO_MS}ms" aria-hidden="true"></i>`;
    let over = false;
    const end = undone => {
      if (over) return;
      over = true; clearTimeout(timer); t.remove(); raiseToasts();
      if (undoLast === run) undoLast = null;
      if (undone) undo();
      if (onEnd) onEnd(undone);
    };
    const run = () => end(true), timer = setTimeout(() => end(false), UNDO_MS);
    t.querySelector('button').addEventListener('click', run);
    undoLast = run;
    $('toasts').appendChild(t);
    raiseToasts();
  }
  async function moveWithUndo(c, stage, pos) {
    const was = {stage: c.stage, pos: c.pos};
    const r = await change('/api/board/move', {id: c.id, stage, pos});
    if (r && stage !== was.stage) undoToast(`Moved "${c.title}" to ${STAGE[stage]}`, () => change('/api/board/move', {id: c.id, ...was}));
    return r;
  }
  async function saveFilter() {
    const name = (prompt('Name this filter (for example "Late in Australia"):', '') || '').trim();
    if (!name) return;
    try {
      B.filters = (await api('/api/board/filters', {save: {name, ...filt}})).filters;
      render(); $('bSaved').value = name; toast('Filter saved', 2500);
    } catch (err) { toast(err.message); }
  }
  function noteDialog(id) {
    const c = B.cards.find(x => x.id === id);
    mini(`<form class="stackv" style="gap:12px" id="bnForm">
      <div class="head"><h3>Note on ${esc(c ? c.title : 'this product')}</h3>${closeBtn}</div>
      <textarea id="bnText" rows="4" maxlength="2000" placeholder="Feedback from the team, a supplier update…" required></textarea>
      <div class="row"><button type="submit" class="btn primary">${icon('plus')}Add note</button></div></form>`, d => {
      $('bnText').focus();
      $('bnForm').addEventListener('submit', async ev => {
        ev.preventDefault();
        const text = $('bnText').value.trim();
        if (!text) return;
        if (await change('/api/board/note', {id, text})) { d.close(); toast('Note added', 2500); }
      });
    });
  }

  /* Units left and daily sales from Amazon (SP-API keys in Settings), for one product or every one. */
  async function liveNow(id, btn) {
    if (btn) btn.disabled = true;
    try {
      const {job} = await api('/api/board/live', id ? {id} : {});
      const r = await waitJob(job, line => { if (btn) btn.title = line; });
      toast(r.errors && r.errors.length ? 'Amazon answered with problems: ' + r.errors.join(' · ')
        : `Updated from Amazon: ${r.cards} product${r.cards === 1 ? '' : 's'}, ${r.counts} new stock count${r.counts === 1 ? '' : 's'}, ${r.sales_days} days of sales`, 7000);
      await load();
      if (openId) await loadCard();
    } catch (err) { toast(err.message); }
    if (btn && btn.isConnected) btn.disabled = false;
  }

  /* drag a card to another column (only into stages it has unlocked) or to another place in its column;
     on the timeline, drag a date chip to another day */
  let tdrag = null;
  $('boardOut').addEventListener('dragstart', e => {
    const tev = e.target.closest('[data-tev]');
    if (tev) {
      tdrag = TL && TL.events[+tev.dataset.tev];
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', tev.dataset.tev);
      tev.classList.add('dragging');
      return;
    }
    const el = e.target.closest('[data-card]'); if (!el) return;
    drag = B.cards.find(c => c.id === +el.dataset.card);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', el.dataset.card);
    el.classList.add('dragging');
  });
  $('boardOut').addEventListener('dragend', () => {
    drag = null; tdrag = null;
    document.querySelectorAll('.bcard.dragging, .bcards.over, .bcards.nope, .tl-ev.dragging, .tl-day.over').forEach(x => x.classList.remove('dragging', 'over', 'nope'));
  });
  /* The column under the pointer: its cards, its header or its empty space, or the column whose strip of the board the
     pointer is in (between cards, below a short column), so a card can be dropped from anywhere. */
  function dropZone(e) {
    const z = e.target.closest('[data-drop]');
    if (z) return z;
    const col = e.target.closest('.bcol');
    if (col) return col.querySelector('[data-drop]');
    const cols = $('bCols'); if (!cols || !e.target.closest('#boardOut')) return null;
    const hit = [...cols.querySelectorAll('.bcol')].find(c => { const r = c.getBoundingClientRect(); return e.clientX >= r.left - 6 && e.clientX <= r.right + 6; });
    return hit ? hit.querySelector('[data-drop]') : null;
  }
  $('boardOut').addEventListener('dragover', e => {
    const day = e.target.closest('[data-day]');
    if (day && tdrag) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; day.classList.add('over'); return; }
    const zone = drag && dropZone(e); if (!zone) return;
    const ok = drag.unlocked.includes(zone.dataset.drop);
    $('bCols').querySelectorAll('.bcards.over, .bcards.nope').forEach(x => { if (x !== zone) x.classList.remove('over', 'nope'); });
    zone.classList.toggle('over', ok); zone.classList.toggle('nope', !ok);
    if (ok) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; }
  });
  $('boardOut').addEventListener('dragleave', e => {
    const zone = e.target.closest('[data-day]');                   // board columns are cleared by dragover and dragend
    if (zone && !zone.contains(e.relatedTarget)) zone.classList.remove('over', 'nope');
  });
  $('boardOut').addEventListener('drop', async e => {
    const day = e.target.closest('[data-day]');
    if (day && tdrag) { e.preventDefault(); day.classList.remove('over'); const ev = tdrag; tdrag = null; return moveDate(ev, day.dataset.day); }
    const zone = drag && dropZone(e); if (!zone) return;
    e.preventDefault();
    if (!drag.unlocked.includes(zone.dataset.drop)) { zone.classList.remove('nope'); toast(LOCKED[zone.dataset.drop]); return; }
    const c = drag, id = c.id, els = [...zone.querySelectorAll('[data-card]')].filter(x => +x.dataset.card !== id);
    const at = els.findIndex(x => { const r = x.getBoundingClientRect(); return e.clientY < r.top + r.height / 2; });
    const p = x => parseFloat(x.dataset.pos);
    const pos = !els.length ? 1 : at === 0 ? p(els[0]) - 1 : at === -1 ? p(els[els.length - 1]) + 1 : (p(els[at - 1]) + p(els[at])) / 2;
    zone.classList.remove('over');
    await moveWithUndo(c, zone.dataset.drop, pos);
  });

  /* ---------- the timeline: every date on the board in a month grid ---------- */
  const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  const TL_ICON = {due: 'clock', step: 'box', eta: 'box', live: 'ok', reorder: 'refresh', order_by: 'warn'};
  const isoDay = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const thisMonth = () => (TL ? TL.today : isoDay(new Date())).slice(0, 7);
  const shiftMonth = (ym, n) => { const [y, m] = ym.split('-').map(Number); return isoDay(new Date(y, m - 1 + n, 1)).slice(0, 7); };
  let tlSeq = 0;
  async function timeline() {
    const host = $('bTimeline'), seq = ++tlSeq;
    if (!host) return;
    if (!TL || TL.rev !== B.rev) {
      if (!TL) host.innerHTML = busy('Reading the dates…');
      try { TL = {...await api('/api/board/timeline'), rev: B.rev}; } catch (e) { host.innerHTML = failPill(e.message); return; }
      if (seq !== tlSeq || !$('bTimeline')) return;
    }
    const ym = month || thisMonth(), [y, m] = ym.split('-').map(Number);
    const start = new Date(y, m - 1, 1 - ((new Date(y, m - 1, 1).getDay() + 6) % 7));
    const ids = new Set(shown().map(c => c.id)), byDay = {};
    const evs = TL.events.map((e, i) => ({...e, i})).filter(e => ids.has(e.card_id) && (!filt.mk || !e.market || e.market === filt.mk));
    evs.forEach(e => (byDay[e.day] = byDay[e.day] || []).push(e));
    const cells = Array.from({length: 42}, (_, i) => {
      const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i), iso = isoDay(d), list = byDay[iso] || [];
      return `<div class="tl-day${d.getMonth() !== m - 1 ? ' out' : ''}${iso === TL.today ? ' today' : ''}${iso < TL.today ? ' past' : ''}" data-day="${iso}" role="gridcell" aria-label="${esc(niceDay(iso))}${list.length ? `, ${list.length} date${list.length === 1 ? '' : 's'}` : ''}">
        <span class="tl-n"><b>${d.getDate()}</b><i>${esc(niceDay(iso))}</i></span>${list.map(tlChip).join('')}</div>`;
    });
    const n = evs.filter(e => e.day.startsWith(ym)).length;
    $('bTimeline').innerHTML = `<div class="panel tl">
      <div class="row tl-head"><button type="button" class="btn slim" data-tlnav="-1" aria-label="Previous month">‹</button><h3>${MONTHS[m - 1]} ${y}</h3><button type="button" class="btn slim" data-tlnav="1" aria-label="Next month">›</button>
        <button type="button" class="btn slim ghost" data-tlnav="today">Today</button><span class="small muted">${n} date${n === 1 ? '' : 's'} this month</span></div>
      <div class="tl-grid" role="grid">${['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map(x => `<div class="tl-wd" role="columnheader">${x}</div>`).join('')}${cells.join('')}</div>
      ${explain('Each chip is a date from a product\'s card. Drag a deadline, a shipment step or a go-live day to another day to change it (deadline emails move with it). Dashed chips are worked out by the app, so they move when their inputs change: expected at Amazon (shipping times), re-order by (the selling pace) and the last day to order (for a product not ordered yet, to be in stock for its next seasonal peak, from Google Trends). Click a chip to open the product.')}</div>`;
  }
  const tlChip = e => `<button type="button" class="tl-ev c-${esc(e.color)} k-${e.kind}${e.edit ? '' : ' auto'}${e.late ? ' late' : ''}" data-tev="${e.i}" data-bopen="${e.card_id}"${e.edit ? ' draggable="true"' : ''} title="${esc(e.title)}: ${esc(e.label)}${e.market ? ' (' + esc(MK[e.market]) + ')' : ''}${e.edit ? '. Drag to another day to change it.' : ''}">${icon(TL_ICON[e.kind] || 'dot')}<span>${esc(e.title)}</span><em>${esc(e.label)}${e.market ? ' · ' + esc(MK_SHORT[e.market] || e.market) : ''}</em></button>`;
  async function moveDate(ev, day) {
    const ed = ev.edit, id = ev.card_id;
    if (!ed || day === ev.day) return;
    const r = ed.type === 'task' ? await change('/api/board/task', {move: ed.task_id, due_day: day})
      : ed.type === 'step' ? await change('/api/board/line', {id, n: ed.n, market: ed.market, set: {steps: {[ed.step]: day}}})
        : await change('/api/board/card', {id, set: {live: {[ed.market]: day}}});
    if (r) toast(`${ev.title}: ${ev.label} now ${niceDay(day)}`, 3500);
  }

  /* Every change: save, celebrate a new trophy, then redraw the board and the open card. */
  async function change(path, body) {
    try {
      const r = await api(path, body);
      if (r && r.trophies && r.trophies.length) celebrate(r.trophies);
      await load();
      if (openId) await loadCard();
      return r;
    } catch (e) { toast(e.message); if (openId) loadCard(); return null; }
  }

  function celebrate(names) {
    const host = $('bDlg').open ? $('bDlg') : document.body, el = document.createElement('div');
    el.className = 'bparty';
    el.setAttribute('role', 'status');
    const bits = Array.from({length: 42}, (_, i) => {
      const a = Math.random() * Math.PI * 2, r = 120 + Math.random() * 280;
      return `<i style="--x:${Math.round(Math.cos(a) * r)}px;--y:${Math.round(Math.sin(a) * r + 140)}px;--r:${Math.round(Math.random() * 720 - 360)}deg;--c:${CONF[i % CONF.length]};animation-delay:${Math.round(Math.random() * 160)}ms"></i>`;
    }).join('');
    el.innerHTML = `<div class="bp-card">${icon('trophy', 'bp-ic')}<b>${esc(names.join(' · '))}</b><span class="small muted">Trophy won</span></div>${bits}`;
    host.appendChild(el);
    setTimeout(() => el.remove(), 2800);
  }

  /* ---------- one card ---------- */
  async function openCard(id, toTab, toOrder) {
    if (openId !== id) { tab = null; orderN = null; dlOpen = null; stratEdit = null; }
    if (toTab) { tab = toTab; dlOpen = null; }
    if (toOrder) orderN = toOrder;
    openId = id;
    await loadCard();
    if (!D) return;
    if (!$('bDlg').open) $('bDlg').showModal();
    if (location.hash !== '#/board/' + id) history.replaceState(null, '', '#/board/' + id);
  }
  async function loadCard() {
    if (!openId) return;
    try { D = await api('/api/board/card?id=' + openId); }
    catch (e) { toast(e.message); D = null; openId = null; if ($('bDlg').open) $('bDlg').close(); return; }
    if (!tab) tab = D.card.stage === 'precheck' ? (D.card.unlocked.includes('approval') ? 'approval' : 'idea') : D.card.stage;
    if (!orderN || !D.orders.some(o => o.n === orderN)) orderN = D.orders.length ? D.orders[0].n : 1;
    drawCard();
  }
  $('bDlg').addEventListener('close', () => {
    openId = null; D = null;
    if (location.hash.startsWith('#/board/')) history.replaceState(null, '', '#/board');
  });

  function drawCard() {
    const dlg = $('bDlg'), a = document.activeElement, key = a && dlg.contains(a) && a.dataset ? a.dataset.fk : null;
    let wrap = dlg.querySelector(':scope > .bd-wrap');
    if (!wrap) { dlg.innerHTML = '<div class="bd-wrap"></div>'; wrap = dlg.firstChild; }    // a trophy overlay stays next to it
    const top = dlg.scrollTop;
    wrap.innerHTML = cardPanel();
    dlg.scrollTop = top;
    if (key) { const el = dlg.querySelector(`[data-fk="${CSS.escape(key)}"]`); if (el) el.focus(); }
  }

  function cardPanel() {
    const c = D.card, ms = c.milestones, un = c.unlocked;
    const site = (ST.markets[c.market] || {}).site || '';
    return `<div class="bd-head c-${esc(c.color)}">
        ${c.image ? `<img src="${esc(c.image)}" alt="" referrerpolicy="no-referrer">` : `<div class="bc-noimg" aria-hidden="true">${icon('box')}</div>`}
        <div class="bd-ttl">
          <div class="small muted">${esc(c.key)} · ${mkFlag(c.market)} · sells as “${esc(c.kw)}” · <a href="#/k/${esc(c.market)}/${encodeURIComponent(c.kw)}">keyword report</a>${c.asin ? ` · <a data-open="${esc(site + '/dp/' + c.asin)}">${esc(c.asin)}</a>` : ''}</div>
          <input type="text" class="bd-title" data-f="title" data-fk="title" value="${esc(c.title)}" maxlength="160" aria-label="Product name">
        </div>
        <div class="bd-acts"><button type="button" class="btn slim ghost" data-archive title="Remove this product from the board" aria-label="Remove from the board">${icon('trash')}<span>Remove</span></button>
          <button type="button" class="btn ghost" data-close title="Close (Esc)" aria-label="Close">${icon('x')}</button></div>
      </div>
      <div class="bd-meta"><div class="bd-colors" role="group" aria-label="Colour">${COLORS.map(k => `<button type="button" class="c-${k}" data-color="${k}" aria-pressed="${c.color === k}" title="${k}" aria-label="${k}"></button>`).join('')}</div>
        <label class="bd-tags small">${icon('tags')}<input type="text" data-f="tags" data-fk="tags" maxlength="240" value="${esc((c.tags || []).join(', '))}" placeholder="Tags, with commas: summer, gift" aria-label="Tags, with commas between them"></label></div>
      <ol class="bstep">${STEPS.map(([k, name], i) => {
        const done = k === 'idea' ? ms.idea_done : k === 'approval' ? ms.approved : k === 'sourcing' ? ms.order_ready : k === 'launch' ? ms.launched : (ms.reorders || 0);
        const locked = !un.includes(k), now = c.stage === k;
        const sub = done ? (k === 'reorder' ? `${done} re-order${done > 1 ? 's' : ''}` : ({idea: 'Ready ', approval: 'Approved ', sourcing: 'Order ready '}[k] || 'Launched ') + niceDay(isoOf(done)))
          : locked ? 'locked' : now ? 'in progress' : '';
        return `<li class="st-${k}${done ? ' done' : ''}${locked ? ' locked' : ''}${now ? ' now' : ''}"><span class="bs-ic">${done ? icon(k === 'idea' ? 'ok' : 'trophy') : locked ? icon('lock') : i + 1}</span><b>${esc(name)}</b><span class="small">${esc(sub)}</span></li>`;
      }).join('')}</ol>
      ${c.next ? `<div class="bnext nx-${esc(c.next.tone)}">${icon('chev')}<span><b>Next step:</b> ${esc(c.next.text)}</span>${tab !== c.next.tab || (c.next.order && orderN !== c.next.order) ? `<button type="button" class="btn slim" data-ngo>Open ${esc(STAGE[c.next.tab])}</button>` : ''}</div>` : ''}
      <div class="bd-tabs" role="tablist">${[...STEPS, ['notes', 'Notes & history']].map(([k, name]) => `<button type="button" role="tab" class="st-${k}" data-tab="${k}" aria-selected="${tab === k}">${k !== 'notes' && !un.includes(k) ? icon('lock') : ''}${esc(name)}</button>`).join('')}</div>
      <div class="bd-body">${tab === 'idea' ? ideaTab() : tab === 'approval' ? approvalTab() : tab === 'sourcing' ? sourcingTab() : tab === 'launch' ? launchTab() : tab === 'reorder' ? reorderTab() : notesTab()}</div>`;
  }

  /* Deadline & reminders: a short summary (due day, who gets the emails, when each goes out) and, to set or change
     it, a form with days to complete, the people, a note and the update meeting. */
  function deadline(stage) {
    const t = D.tasks[stage], active = !!(t && !t.done_at), ppl = D.notifiers, ms = D.card.milestones, set = ST.settings || {};
    const head = `<div class="bdead-h">${icon('clock')}<b>Deadline & reminders</b>`;
    if (dlOpen !== stage) {
      if (t && t.done_at) return `<div class="bdead bdead-min">${head}<span class="pill good">${icon('ok')}Done ${esc(niceDay(isoOf(t.done_at)))}</span><span class="small muted">it was due ${esc(niceDay(t.due_day))}</span></div></div>`;
      if (!active) {
        if (stage === 'idea' ? ms.idea_done : stage === 'approval' ? ms.approved : stage === 'sourcing' ? ms.order_ready : stage === 'launch' ? ms.launched : false) return '';
        return `<div class="bdead bdead-min">${head}<span class="small muted">No deadline yet.</span><button type="button" class="btn slim" data-dlopen="${stage}">${icon('plus')}Set a deadline</button></div></div>`;
      }
      const now = new Date(); now.setHours(0, 0, 0, 0);
      const left = Math.round((dayOf(t.due_day) - now) / 864e5), tone = left < 0 ? 'bad' : left <= 3 ? 'warn' : 'line';
      const who = ppl.filter(p => t.notify.includes(p.id)).map(p => p.name);
      return `<div class="bdead">${head}<span class="pill ${tone}">${left < 0 ? 'Late, was due' : 'Due'} ${esc(niceDay(t.due_day))}</span>
          <span class="small muted">${t.days} day${t.days === 1 ? '' : 's'} from ${esc(niceDay(t.start_day))}${who.length ? ' · emails to ' + esc(who.join(', ')) : ' · no emails'}</span></div>
        ${t.note ? `<div class="small">Note: ${esc(t.note)}</div>` : ''}
        ${t.mails.length ? `<ol class="btimeline">${t.mails.map(m => `<li class="m-${esc(m.status)}">${icon(MAIL_DONE[m.status] ? 'ok' : m.status === 'scheduled' ? 'clock' : 'x')}<b>${KIND[m.kind]}</b><span>${MAIL_DONE[m.status] ? MAIL_DONE[m.status] + ' ' + esc(niceTime(m.sent_at)) : m.status === 'scheduled' ? esc(niceTime(m.send_at)) + (m.attempts ? ', trying again' : '') : esc(m.status)}</span>${m.error && !MAIL_DONE[m.status] ? `<span class="small muted">${esc(m.error)}</span>` : ''}</li>`).join('')}</ol>` : ''}
        <div class="row wrap">${who.length ? `<button type="button" class="btn slim" data-dlsend="${t.id}">${icon('mail')}Send reminder now</button><button type="button" class="btn slim" data-dlprev="${t.id}">Preview emails</button>` : ''}
          <button type="button" class="btn slim" data-dlopen="${stage}">Change</button><button type="button" class="btn slim ghost" data-dlcancel="${t.id}">Remove</button></div>
        ${who.length && !B.mail.ready ? '<div class="small muted">Emails need your Gmail: <a href="#/settings">Settings, Email for reminders</a>.</div>' : ''}
      </div>`;
    }
    const sel = new Set(active ? t.notify : ppl.map(p => p.id));
    return `<div class="bdead" data-dl="${stage}">${head}</div>
      <div class="row wrap">
        <div class="field"><label>Days to complete</label><input type="number" data-dlf="days" data-fk="dl-days-${stage}" min="1" max="365" step="1" value="${active ? t.days : ''}" placeholder="7"></div>
        <div class="field"><label>Update meeting at</label><input type="time" data-dlf="meet_time" data-fk="dl-time-${stage}" value="${esc(active ? t.meet_time : set.mail_meet_time || '15:00')}"></div>
        <div class="field"><label>Minutes</label><input type="number" data-dlf="meet_minutes" data-fk="dl-min-${stage}" min="15" max="180" step="5" value="${active ? t.meet_minutes : set.mail_meet_minutes || 30}"></div>
      </div>
      <div class="field"><span class="lbl">Email</span><div class="bpeople">${ppl.map(p => `<label class="small"><input type="checkbox" data-dlp="${p.id}"${sel.has(p.id) ? ' checked' : ''}> ${esc(p.name)} <span class="muted">${esc(p.email)}</span></label>`).join('') || '<span class="small muted">Nobody yet.</span>'}
        <button type="button" class="btn slim ghost" data-people>${icon('mail')}Notifiers</button></div></div>
      <div class="field"><label>Note for the emails (optional)</label><textarea data-dlf="note" data-fk="dl-note-${stage}" rows="2" maxlength="1000">${esc(active ? t.note || '' : '')}</textarea></div>
      <div class="small muted">The people get an email now${set.mail_start_email === false ? ' (off in Settings)' : ''}, a reminder ${set.mail_remind_days || 3} days before the due day and, on the due day, an invitation to the update meeting.</div>
      <div class="row wrap"><button type="button" class="btn primary" data-dlsave="${stage}">${active ? 'Save the new deadline' : 'Set deadline'}</button><button type="button" class="btn ghost" data-dlclose>Cancel</button></div>
    </div>`;
  }

  const lockNote = stage => `<div class="verdict some">${icon('lock')}<span>${stage === 'approval'
    ? 'Opens when the idea has its Amazon ASIN and Alibaba link' + (D.card.idea.missing.length ? ': still needs ' + esc(D.card.idea.missing.join(' and ')) : '') + '. You can tick the checks already.'
    : stage === 'sourcing'
    ? 'Opens when every approval check is ticked' + (D.card.approval.missing.length ? ': still to check ' + esc(D.card.approval.missing.slice(0, 3).join('; ')) + (D.card.approval.missing.length > 3 ? ' and more' : '') : '') + '. You can fill in the order already.'
    : stage === 'launch'
    ? 'Opens when Sourcing & PO is complete' + (D.card.missing.length ? ': still needs ' + esc(D.card.missing.join(', ')) : '') + '.'
    : 'Opens once the product is live on Amazon and every launch hint is checked.'} You can set its deadline already.</span></div>`;
  const sumOf = (st, k) => { const v = Object.values(st.markets).map(m => m[k]).filter(x => x != null); return v.length ? v.reduce((a, b) => a + b, 0) : null; };

  /* Idea: the product you found on Amazon and a supplier on Alibaba. With both, it moves on to Approval by itself. */
  function ideaTab() {
    const c = D.card, t = D.team, st = c.idea, done = c.milestones.idea_done, ali = (D.data.links || {}).alibaba || '';
    return `${deadline('idea')}
      <section class="bsec bidea">
        <div class="bsec-h"><h3>${icon('bulb')}The idea</h3>${done ? `<span class="pill good">${icon('ok')}Ready ${esc(niceDay(isoOf(done)))}</span>` : `<span class="small muted">${st.done} of ${st.total} added</span>`}</div>
        <div class="bl-grid2">
          <div class="field"><div class="lblrow"><label for="bAsin">Amazon ASIN</label><a data-open="${esc(t.amazon)}" title="${esc(t.amazon)}">${esc(t.amazon_label)}${icon('external')}</a></div>
            <input type="text" id="bAsin" data-f="asin" data-fk="asin" maxlength="10" value="${esc(c.asin || '')}" placeholder="B0…" spellcheck="false"></div>
          <div class="field"><div class="lblrow"><label for="bAli">Alibaba link</label><a data-open="${esc(t.alibaba)}" title="${esc(t.alibaba)}">${esc(t.alibaba_label)}${icon('external')}</a></div>
            <input type="text" id="bAli" data-f="alibaba" data-fk="alibaba" maxlength="500" value="${esc(ali)}" placeholder="https://www.alibaba.com/product-detail/…" spellcheck="false"></div>
        </div>
        ${done ? '' : `<div class="verdict some">${icon('lock')}<span>Add ${esc(st.missing.join(' and '))}, then it moves to Approval by itself.</span></div>`}
        <div class="small muted">The ASIN can be a listing you found. Once your own listing is live, put your ASIN here so stock and sales come from Amazon.</div>
      </section>`;
  }

  /* Approval: the checklist a product must pass before money goes into it. Every check ticked = approved, and the
     product moves on to Sourcing & PO by itself. */
  function approvalTab() {
    const a = D.card.approval, ok = D.card.milestones.approved, pct = a.total ? Math.round(a.done / a.total * 100) : 100;
    const when = ok ? niceDay(isoOf(ok)) : '', res = D.research || {};
    const sure = ok ? [] : a.items.filter(i => !i.done && (res[i.id] || {}).state === 'pass');
    const said = i => { const r = res[i.id]; return r && !ok ? `<div class="bres rs-${esc(r.state)} small">${icon(r.state === 'pass' ? 'ok' : r.state === 'fail' ? 'warn' : 'info')}<span>${esc(r.why)}</span></div>` : ''; };
    return `${D.card.unlocked.includes('approval') ? '' : lockNote('approval')}${deadline('approval')}
      <div class="bappr">
        <div class="head"><h3>Approval checklist</h3><span class="small muted">${a.done} of ${a.total} ticked</span></div>
        <div class="bappr-bar" role="img" aria-label="${pct}% checked"><span style="width:${pct}%"></span></div>
        ${ok ? `<div class="verdict opportunity">${icon('trophy')}<span><b>Approved${when ? ' ' + esc(when) : ''}.</b> Sourcing & PO is open.</span></div>`
          : `<div class="small muted">Tick each check once you have looked at it.${Object.keys(res).length ? ' Under each one is what your research found.' : ''} When every check is ticked the product is approved and moves to Sourcing & PO.</div>`}
        ${sure.length ? `<div class="row"><button type="button" class="btn slim" data-chkres>${icon('ok')}Tick the ${sure.length} check${sure.length === 1 ? '' : 's'} the research confirms</button></div>` : ''}
        ${a.items.length ? `<ul class="bchecks">${a.items.map(i => `<li class="${i.done ? 'done' : ''}">
          <div class="bchk-body"><label><input type="checkbox" data-chk="${i.id}"${i.done ? ' checked' : ''}${ok ? ' disabled' : ''}><span>${esc(i.text)}</span></label>${said(i)}</div>
          ${i.done ? `<span class="small muted">${esc(niceDay(isoOf(i.done)))}</span>` : ''}
          ${ok ? '' : `<button type="button" class="btn slim ghost" data-chkrm="${i.id}" title="This check does not apply to this product" aria-label="Remove this check">${icon('x')}</button>`}</li>`).join('')}</ul>`
          : '<div class="small muted">This product was on the board before the approval checklist, so it counts as approved.</div>'}
        ${ok ? '' : `<div class="row"><input type="text" id="bChkText" data-fk="chk-new" maxlength="160" placeholder="Add your own check, e.g. Sample arrived and looks good" style="flex:1;min-width:0">
          <button type="button" class="btn slim" data-chkadd>${icon('plus')}Add check</button></div>`}
      </div>`;
  }

  function sourcingTab() {
    const o = D.orders.find(x => x.n === orderN) || D.orders[0];
    if (!o) return '<div class="empty">No order yet.</div>';
    if (!D.card.unlocked.includes('sourcing')) return lockNote('sourcing') + sourcingForm(o);
    return sourcingForm(o);
  }

  function sourcingForm(o) {
    const st = o.status, on = new Set(o.lines.map(l => l.market));
    return `${deadline('sourcing')}
      ${D.orders.length > 1 ? `<div class="seg slim" role="group" aria-label="Order">${D.orders.map(x => `<button type="button" data-order="${x.n}" aria-pressed="${x.n === o.n}">${x.n === 1 ? 'First order' : 'Re-order #' + x.n}${x.ready_at ? ' ✓' : ''}</button>`).join('')}</div>` : ''}
      <section class="bsec">
        <div class="bsec-h"><h3>${icon('file')}${o.n === 1 ? 'Purchase order' : 'Re-order #' + o.n}</h3>
          ${st.complete ? `<span class="pill good">${icon('trophy')}Order ready</span>` : `<span class="small muted">${Math.round(st.progress * 100)}% filled in</span>`}</div>
        <div class="bpo-top">
          <div class="field"><label for="bUnit">Price per unit</label>
            <div class="row bmoney"><input type="number" id="bUnit" data-f="unit_cost" data-fk="unit-${o.n}" data-n="${o.n}" min="0" step="0.01" value="${o.unit_cost ?? ''}" placeholder="0.00">${curSel('cost_cur', o.cost_cur, `data-n="${o.n}" data-fk="ccur-${o.n}"`)}</div></div>
          <div class="field"><span class="lbl">Markets in this order</span>
            <div class="bmk-pick">${MKS.map(m => `<label class="bmk-chip${on.has(m) ? ' on' : ''}"><input type="checkbox" data-mkon="${m}" data-n="${o.n}"${on.has(m) ? ' checked' : ''}>${mkFlag(m)}</label>`).join('')}</div></div>
        </div>
        ${o.lines.map(ln => lineHtml(o, ln)).join('') || '<div class="small muted">Pick at least one market.</div>'}
        <div class="btotal">
          <div><span class="eyebrow">Product</span><b>${mine(sumOf(st, 'goods_usd'))}</b></div>
          <div><span class="eyebrow">Shipping</span><b>${mine(sumOf(st, 'ship_usd'))}</b></div>
          <div><span class="eyebrow">Units</span><b>${num(st.units)}</b></div>
          <div class="tot"><span class="eyebrow">Total to invest</span><b>${st.total_usd != null ? mine(st.total_usd) : mine(st.known_usd) + ' so far'}</b></div>
        </div>
        ${st.complete ? `<div class="verdict opportunity">${icon('trophy')}<span><b>Order ready.</b> ${o.n === 1 ? 'Launching strategy is open.' : 'Re-order complete.'}</span></div>`
          : `<div class="verdict some">${icon('lock')}<span>Still needed: ${esc(st.missing.join(', '))}.${o.n === 1 ? ' Then Launching strategy opens.' : ''}</span></div>`}
      </section>
      ${teamBox()}
      ${o.n === 1 ? moneyPreview() : ''}`;
  }

  function lineHtml(o, ln) {
    const c = ln.cost || {}, k = `${o.n}-${ln.market}`, at = `data-n="${o.n}" data-mk="${ln.market}"`;
    const done = STEP.filter(([s]) => ln.steps[s]).length;
    const sum = [ln.units ? num(ln.units) + ' units' : '', ln.ship_mode ? 'by ' + ln.ship_mode : '',
      c.landed_unit_usd != null ? 'landed ' + mine(c.landed_unit_usd, 2) + ' a unit' : ''].filter(Boolean).join(' · ');
    return `<div class="bline">
      <div class="bl-head"><b class="bl-mk">${mkFlag(ln.market)}</b><span class="small muted">${sum}</span><b class="bl-tot">${c.total_usd != null ? mine(c.total_usd) : ''}</b></div>
      <div class="bl-grid">
        <div class="field"><label for="bU${k}">Units</label><input type="number" id="bU${k}" data-f="units" data-fk="units-${k}" ${at} min="1" step="1" value="${ln.units ?? ''}" placeholder="0"></div>
        <div class="field"><span class="lbl">Shipment</span><div class="seg bship" role="group" aria-label="Shipment to ${esc(MK[ln.market])}">${[['sea', 'Sea'], ['air', 'Air']].map(([m, name]) => `<button type="button" data-ship="${m}" ${at} aria-pressed="${ln.ship_mode === m}">${name}</button>`).join('')}</div></div>
        <div class="field"><label for="bS${k}">Shipping cost (total)</label><div class="row bmoney"><input type="number" id="bS${k}" data-f="ship_cost" data-fk="ship-${k}" ${at} min="0" step="0.01" value="${ln.ship_cost ?? ''}" placeholder="0.00">${curSel('ship_cur', ln.ship_cur || 'USD', `${at} data-fk="scur-${k}"`)}</div></div>
      </div>
      <div class="btrack">
        <div class="btrack-h"><span class="lbl">Shipment tracker</span><span class="small muted">${done} of ${STEP.length} done</span></div>
        <ol class="btrack-steps">${STEP.map(([s, name]) => `<li class="${ln.steps[s] ? 'done' : ''}"><div class="bt-rail"><span class="bt-dot">${ln.steps[s] ? icon('ok') : ''}</span></div>
          <label for="bT${s}${k}">${name}</label><input type="date" id="bT${s}${k}" data-f="step" data-step="${s}" data-fk="step-${s}-${k}" ${at} value="${esc(ln.steps[s] || '')}"></li>`).join('')}</ol>
      </div>
      ${ln.steps.arrived ? `<div class="field bl-arr"><label for="bA${k}">Units that arrived</label><input type="number" id="bA${k}" data-f="arrived_units" data-fk="arr-${k}" ${at} min="1" value="${ln.arrived_units ?? ''}" placeholder="${ln.units ?? ''}"></div>`
        : ln.eta ? `<div class="small bl-eta">${icon('clock')}On sale at Amazon about <b>${esc(niceDay(ln.eta))}</b> (from the usual production and shipping times).</div>` : ''}
    </div>`;
  }

  /* What the product update email says about this product. Its Amazon ASIN and Alibaba link are set in the Idea tab. */
  function teamBox() {
    const t = D.team, c = D.card;
    return `<section class="bsec bteam">
      <div class="bsec-h"><h3>${icon('mail')}In the team email</h3><button type="button" class="btn slim" data-inform>${icon('mail')}Inform attendees</button></div>
      <dl class="bfacts"><dt>Product name</dt><dd>${esc(c.title)}</dd>${t.rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}
        <dt>Links</dt><dd><a data-open="${esc(t.amazon)}" title="${esc(t.amazon)}">${esc(t.amazon_label)}${icon('external')}</a> · <a data-open="${esc(t.alibaba)}" title="${esc(t.alibaba)}">${esc(t.alibaba_label)}${icon('external')}</a></dd></dl>
      <div class="row wrap small muted">Sourcing ETA is the Sourcing & PO deadline above. The ASIN and the Alibaba link are set in the Idea tab; without them the email links to a search for “${esc(c.kw)}”.
        <button type="button" class="btn slim ghost" data-tab="idea">${icon('bulb')}Open Idea</button></div>
    </section>`;
  }

  function moneyPreview() {
    const rows = D.markets.map(mk => {
      const m = D.money[mk] || {};
      if (!m.searched) return `<li class="unk"><span>${mkFlag(mk)}: not searched there yet. <a href="#/k/${mk}/${encodeURIComponent(D.card.kw)}">Open the report</a> to search it.</span></li>`;
      if (!m.econ) return `<li class="unk"><span>${mkFlag(mk)}: page 1 sells at about ${local(m.price, mk)}. Type the price per unit to see your profit.</span></li>`;
      const e = m.econ, s = m.sim || {};
      if (e.Profit <= 0) return `<li class="bad"><span>${mkFlag(mk)}: at ${local(m.price, mk)} each sale <b>loses ${local(-e.Profit, mk)}</b> after Amazon fees, your shipping and duty: raise the price or lower the costs.</span></li>`;
      return `<li class="good"><span>${mkFlag(mk)}: at ${local(m.price, mk)} you keep <b>${local(e.Profit, mk)}</b> a sale (${pct(e.Margin)}), ROI ${pct(e.ROI)}, ads can take up to ${pct(e.BreakEvenACoS)} of sales.${s.months ? ` This order sells in about ${num(s.months, 1)} months` + (s.profit_total != null ? ` and makes about ${local(s.profit_total, mk)}.` : '.') : ''}</span></li>`;
    }).join('');
    return `<div class="panel flat"><h3 style="margin-bottom:8px">What this order could make</h3><ul class="reasons">${rows}</ul>
      <div class="small muted" style="margin-top:6px">From the newest search: the middle page-1 price, Amazon fees, your shipping per unit, duty, 12% ads and 2.5% returns. A new seller's sales are a rough guess.</div></div>`;
  }

  /* Launching strategy: the launch plans people gave you (who and what), then the checks from research. */
  function strategyBox() {
    const list = D.strategy || [], who = [...new Set(D.notifiers.map(p => p.name))];
    const item = s => stratEdit === s.id ? `<li class="editing">
        <input type="text" data-sby="${s.id}" data-fk="sby-${s.id}" maxlength="80" list="bStratWho" value="${esc(s.by)}" placeholder="From (who gave it)" aria-label="Who gave this strategy">
        <textarea data-stext="${s.id}" data-fk="stext-${s.id}" rows="4" maxlength="4000" aria-label="The strategy">${esc(s.text)}</textarea>
        <div class="row"><button type="button" class="btn slim primary" data-stratsave="${s.id}">Save</button><button type="button" class="btn slim ghost" data-stratcancel>Cancel</button></div></li>`
      : `<li><div class="bstrat-h"><b>${s.by ? 'From ' + esc(s.by) : 'Strategy'}</b><span class="small muted">${esc(niceDay(isoOf(s.at)))}${s.edited_at ? ', changed ' + esc(niceDay(isoOf(s.edited_at))) : ''}</span>
          <span class="bstrat-acts"><button type="button" class="btn slim ghost" data-stratedit="${s.id}">Edit</button><button type="button" class="btn slim ghost" data-stratrm="${s.id}" title="Delete this strategy" aria-label="Delete this strategy">${icon('x')}</button></span></div>
        <div class="bstrat-text">${esc(s.text)}</div></li>`;
    return `<section class="bsec bstrat">
      <div class="bsec-h"><h3>${icon('file')}Launching strategy</h3><span class="small muted">${list.length ? `${list.length} ${list.length === 1 ? 'strategy' : 'strategies'}` : 'Write down the launch plans people give you'}</span></div>
      <datalist id="bStratWho">${who.map(n => `<option value="${esc(n)}">`).join('')}</datalist>
      <div class="bstrat-add">
        <input type="text" id="bStratBy" data-fk="strat-by" maxlength="80" list="bStratWho" placeholder="From (who gave it)" aria-label="Who gave this strategy">
        <textarea id="bStratText" data-fk="strat-text" rows="3" maxlength="4000" placeholder="The strategy: launch price, coupons, PPC bids, Vine, giveaways…" aria-label="The strategy"></textarea>
        <div class="row"><button type="button" class="btn primary" data-stratadd>${icon('plus')}Add strategy</button></div>
      </div>
      ${list.length ? `<ul class="bstrats">${list.map(item).join('')}</ul>` : ''}
    </section>`;
  }

  function launchTab() {
    const by = {}, ls = D.launch;
    D.hints.forEach(h => { (by[h.market] = by[h.market] || []).push(h); });
    const suggest = mk => { const h = D.hints.find(x => x.market === mk && x.key === 'target'); return h && h.data.v ? h.data.v : ''; };
    return `${D.card.unlocked.includes('launch') ? '' : lockNote('launch')}${deadline('launch')}
      ${strategyBox()}
      <div class="row" style="justify-content:space-between"><span class="small">${ls.done} of ${ls.total} hints checked${ls.missing.length ? ' · still: ' + esc(ls.missing.join(', ')) : ''}</span>
        <button type="button" class="btn slim" data-refresh>${icon('refresh')}Refresh from research</button></div>
      ${D.markets.map(mk => `<div class="bmk"><h3>${mkFlag(mk)}</h3>
        <div class="bhints">${(by[mk] || []).map(hintHtml).join('') || '<div class="small muted">No hints for this market yet.</div>'}</div>
        <div class="row wrap">
          <div class="field"><label>Sales target (units a month)</label><input type="number" data-f="target" data-mk="${mk}" data-fk="tgt-${mk}" min="0" step="1" value="${D.data.targets[mk] ?? ''}" placeholder="${suggest(mk)}"></div>
          <div class="field"><label>Live on Amazon since</label><input type="date" data-f="live" data-mk="${mk}" data-fk="live-${mk}" value="${esc(D.data.live[mk] || '')}"></div>
        </div></div>`).join('')}
      <div class="panel flat row bown">
        <select id="bOwnMk" data-fk="own-mk" aria-label="Market">${D.markets.map(m => `<option value="${m}">${MK_SHORT[m]}</option>`).join('')}</select>
        <input type="text" id="bOwnText" data-fk="own-text" maxlength="400" placeholder="Add your own item to check" aria-label="Your own item">
        <button type="button" class="btn" data-ownadd>${icon('plus')}Add</button>
      </div>`;
  }

  function hintHtml(h) {
    const m = h.moved || {}, f = v => typeof v === 'number' ? (Math.abs(v) < 1 && v !== 0 ? pct(v) : num(v, v % 1 ? 2 : 0)) : esc(String(v));
    return `<div class="bhint s-${esc(h.status)}${h.moved ? ' moved' : ''}${h.stale ? ' stale' : ''}">
      <div class="bh-top"><span class="bh-cat">${esc(CAT[h.cat] || h.cat)}</span>
        ${h.moved ? `<span class="pill warn">${icon('warn')}Market moved${m.from != null && m.to != null ? ': ' + f(m.from) + ' → ' + f(m.to) : ''}</span>` : ''}
        ${h.stale ? `<span class="pill warn">${icon('clock')}Check again</span>` : ''}
        ${h.checked_at && h.status !== 'todo' ? `<span class="small muted">checked ${ago(h.checked_at)}</span>` : ''}
        ${h.custom ? `<button type="button" class="btn slim ghost" data-hrm="${h.id}" title="Remove" aria-label="Remove">${icon('x')}</button>` : ''}</div>
      <div class="bh-text">${esc(h.text)}</div>
      <div class="row wrap bh-act"><div class="seg slim" role="group" aria-label="Status">${STATUS.map(([k, name]) => `<button type="button" data-hst="${k}" data-hid="${h.id}" aria-pressed="${h.status === k}">${name}</button>`).join('')}</div>
        <input type="text" data-f="hnote" data-hid="${h.id}" data-fk="hn-${h.id}" maxlength="400" value="${esc(h.note || '')}" placeholder="${h.status === 'adjusted' ? 'What you do instead' : 'Note (needed for Changed)'}" aria-label="Note"></div>
    </div>`;
  }

  function reorderTab() {
    if (!D.card.unlocked.includes('reorder')) return lockNote('reorder') + deadline('reorder');
    const low = (ST.settings || {}).board_low_pct || 30, live = (B && B.live) || [], anyLive = D.stock.some(s => live.includes(s.market));
    return `${deadline('reorder')}
      ${D.stock.length ? `<div class="row wrap blive small">${icon('refresh')}${anyLive
        ? `<span>Units left and daily sales come from Amazon (SP-API) every 4 hours while the app runs${(ST.settings || {}).board_live === false ? ', but this is switched off in Settings' : ''}.</span><button type="button" class="btn slim" data-live>Update from Amazon now</button>`
        : '<span class="muted">Type the units left below. With SP-API keys saved (Settings, Gap Finder), Amazon fills them in by itself.</span>'}</div>` : ''}
      ${D.stock.map(s => {
        const p = s.pct, m = {...s, low_pct: low};
        return `<div class="bstock lv-${esc(s.level || 'unknown')}">
          <div class="row" style="justify-content:space-between"><b>${mkFlag(s.market)}</b><span class="small muted">${s.n === 1 ? 'First order' : 'Re-order #' + s.n}: ${num(s.sent)} units${s.arrived ? ', arrived ' + esc(niceDay(s.arrived)) : ', not arrived yet'}</span></div>
          ${s.live_at ? `<div class="small blive-at">${icon('ok')}Live from Amazon · updated ${esc(ago(s.live_at))}</div>` : ''}
          <div class="bbar" role="img" aria-label="${p == null ? 'no count yet' : Math.round(p) + '% left'}"><span style="width:${p == null ? 0 : Math.min(100, p)}%"></span><i style="left:${low}%" title="Red at ${low}%"></i></div>
          <div class="row wrap"><span class="bst-big">${p == null ? 'No count yet' : `${num(s.left)} left · ${Math.round(p)}%`}</span>
            ${s.order_now ? `<span class="pill bad">${icon('warn')}Re-order now</span>` : s.level === 'red' ? `<span class="pill bad">${icon('warn')}Low stock</span>` : ''}</div>
          ${s.counts.length > 1 || s.sales.length ? `<div class="bst-charts">${s.counts.length > 1 ? `<figure>${burnSvg(m, 300, 54)}<figcaption class="small muted">Units left over time; red line ${low}%</figcaption></figure>` : ''}${s.sales.length ? `<figure>${salesSvg(m, 300, 54)}<figcaption class="small muted">Sold a day on Amazon${s.target ? '; dashed line is your target' : ''}</figcaption></figure>` : ''}</div>` : ''}
          <div class="row wrap"><input type="number" id="bLeft${s.line_id}" data-fk="left-${s.line_id}" min="0" step="1" placeholder="Units left now" aria-label="Units left now">
            <button type="button" class="btn" data-count="${s.line_id}">Save count</button>
            <button type="button" class="btn" data-reorder="${s.market}">${icon('refresh')}Start re-order</button></div>
          <ul class="reasons small">
            ${s.rate != null ? `<li class="${s.target_progress && s.target_progress.pct < 60 ? 'warn' : 'good'}"><span>Selling about ${num(s.rate * 30.4)} a month${s.target ? ` (target ${num(s.target)}: ${s.target_progress.pct}%)` : ''}, from ${esc(s.rate_src || 'your counts')}${s.rate_src === 'Amazon orders' ? ' (the last 4 weeks)' : ''}.</span></li>` : '<li class="unk"><span>Two counts a few days apart (or a week of Amazon sales) show how fast it sells.</span></li>'}
            ${s.sellout ? `<li class="${s.order_now ? 'bad' : 'good'}"><span>Runs out about ${esc(niceDay(s.sellout))}. A new shipment takes about ${s.lead_days} days, so ${s.order_now ? `re-order now (the last good day was ${esc(niceDay(s.reorder_by))})` : `re-order by <b>${esc(niceDay(s.reorder_by))}</b>`}.</span></li>` : ''}
          </ul>
          ${s.counts.length ? `<div class="small muted bcounts adv">Counts: ${s.counts.slice(-6).map(([t, v, cid, src]) => `<span class="bcount">${num(v)} on ${esc(niceDay(isoOf(t)))}${src === 'spapi' ? ' (Amazon)' : ''}<button type="button" data-crm="${cid}" title="Delete this count" aria-label="Delete the count of ${num(v)} on ${esc(niceDay(isoOf(t)))}">${icon('x')}</button></span>`).join('')}</div>` : ''}
        </div>`;
      }).join('') || '<div class="small muted">No shipments yet.</div>'}`;
  }

  function notesTab() {
    return `<div class="panel flat stackv"><label class="lbl" for="bNote">Add a note</label>
        <textarea id="bNote" data-fk="note" rows="3" maxlength="2000" placeholder="Feedback from the team, a supplier update…"></textarea>
        <div class="row"><button type="button" class="btn primary" data-note>${icon('plus')}Add note</button></div></div>
      <ul class="bevents">${D.events.map(e => `<li class="ev-${esc(e.kind)}">${evIcon(e.kind)}<div><div>${esc(e.text)}</div><div class="small muted">${esc(niceTime(e.ts))}</div></div>${e.kind === 'note' ? `<button type="button" class="btn slim ghost" data-nrm="${e.id}" title="Delete this note" aria-label="Delete this note">${icon('x')}</button>` : ''}</li>`).join('')}</ul>`;
  }

  $('bDlg').addEventListener('change', async e => {
    const el = e.target, id = openId;
    if (!D || !id) return;
    const n = +el.dataset.n || 1, mk = el.dataset.mk, v = el.value, f = el.dataset.f;
    if (el.dataset.mkon) {
      const m = el.dataset.mkon, o = D.orders.find(x => x.n === n), ln = o && o.lines.find(x => x.market === m);
      if (!el.checked && ln && (ln.units || ln.ship_cost != null) && !confirm(`Take ${MK[m]} out of this order?`)) { el.checked = true; return; }
      return change('/api/board/line', el.checked ? {id, n, market: m, set: {}} : {id, n, market: m, remove: true});
    }
    if (el.dataset.chk) {
      const r = await change('/api/board/check', {id, item: +el.dataset.chk, done: el.checked});
      if (r && r.trophies && r.trophies.includes('Approved')) { tab = 'sourcing'; drawCard(); }
      return;
    }
    if (f === 'title') return change('/api/board/card', {id, set: {title: v}});
    if (f === 'tags') return change('/api/board/card', {id, set: {tags: v}});
    if (f === 'asin' || f === 'alibaba') {
      const r = await change('/api/board/card', {id, set: {[f]: v.trim()}});
      if (r && r.moved) { tab = r.moved === 'precheck' ? 'approval' : r.moved; drawCard(); toast(`Both links are in: moved to ${STAGE[r.moved]}`, 3500); }
      return;
    }
    if (f === 'unit_cost' || f === 'cost_cur') return change('/api/board/order', {id, n, set: {[f]: v}});
    if (['units', 'ship_mode', 'ship_cost', 'ship_cur', 'arrived_units'].includes(f)) return change('/api/board/line', {id, n, market: mk, set: {[f]: v}});
    if (f === 'step') return change('/api/board/line', {id, n, market: mk, set: {steps: {[el.dataset.step]: v}}});
    if (f === 'target') return change('/api/board/card', {id, set: {targets: {[mk]: v}}});
    if (f === 'live') return change('/api/board/card', {id, set: {live: {[mk]: v}}});
    if (f === 'hnote') {
      const h = D.hints.find(x => x.id === +el.dataset.hid);
      if (h && (v.trim() || h.status !== 'adjusted')) return change('/api/board/hint', {hint_id: h.id, status: h.status, note: v});
    }
  });

  $('bDlg').addEventListener('keydown', e => {
    if (e.key === 'Enter' && e.target.id === 'bChkText') { e.preventDefault(); $('bDlg').querySelector('[data-chkadd]').click(); }
  });
  $('bDlg').addEventListener('click', async e => {
    const t = e.target, dlg = $('bDlg'), id = openId;
    if (t === dlg) { const r = dlg.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right) dlg.close(); return; }
    if (t.closest('[data-close]')) { dlg.close(); return; }
    if (t.closest('[data-ngo]')) { tab = D.card.next.tab; if (D.card.next.order) orderN = D.card.next.order; dlOpen = null; drawCard(); return; }
    const tb = t.closest('[data-tab]');
    if (tb) { tab = tb.dataset.tab; dlOpen = null; drawCard(); return; }
    const op = t.closest('[data-dlopen]');
    if (op) { dlOpen = op.dataset.dlopen; drawCard(); const f = dlg.querySelector('[data-dlf="days"]'); if (f) f.focus(); return; }
    if (t.closest('[data-dlclose]')) { dlOpen = null; drawCard(); return; }
    const col = t.closest('[data-color]');
    if (col) return change('/api/board/card', {id, set: {color: col.dataset.color}});
    const sh = t.closest('[data-ship]');
    if (sh) return change('/api/board/line', {id, n: +sh.dataset.n, market: sh.dataset.mk, set: {ship_mode: sh.dataset.ship}});
    if (t.closest('[data-inform]')) return informDialog();
    const ord = t.closest('[data-order]');
    if (ord) { orderN = +ord.dataset.order; drawCard(); return; }
    const hs = t.closest('[data-hst]');
    if (hs) {
      const note = dlg.querySelector(`[data-f="hnote"][data-hid="${hs.dataset.hid}"]`);
      if (hs.dataset.hst === 'adjusted' && !note.value.trim()) { note.focus(); toast('Say what you do differently, then press Changed.'); return; }
      return change('/api/board/hint', {hint_id: +hs.dataset.hid, status: hs.dataset.hst, note: note.value});
    }
    const cr = t.closest('[data-chkrm]');
    if (cr) return change('/api/board/check', {id, remove: +cr.dataset.chkrm});
    const cs = t.closest('[data-chkres]');
    if (cs) {
      const res = D.research || {}, todo = D.card.approval.items.filter(i => !i.done && (res[i.id] || {}).state === 'pass');
      cs.disabled = true;
      cs.innerHTML = busy('Ticking…');
      let r = null;
      for (const i of todo) {
        r = await api('/api/board/check', {id, item: i.id, done: true}).catch(err => { toast(err.message); return null; });
        if (!r) break;
      }
      if (r && r.trophies && r.trophies.length) celebrate(r.trophies);
      await load();
      if (r && r.trophies && r.trophies.includes('Approved')) tab = 'sourcing';
      if (openId) await loadCard();
      return;
    }
    if (t.closest('[data-chkadd]')) {
      const text = $('bChkText').value.trim();
      if (!text) { $('bChkText').focus(); return; }
      return change('/api/board/check', {id, add: text});
    }
    const hr = t.closest('[data-hrm]');
    if (hr) return change('/api/board/hint', {remove: +hr.dataset.hrm});
    if (t.closest('[data-ownadd]')) {
      const text = $('bOwnText').value.trim();
      if (!text) { $('bOwnText').focus(); return; }
      return change('/api/board/hint', {add: {id, market: $('bOwnMk').value, text}});
    }
    if (t.closest('[data-refresh]')) return change('/api/board/hints/refresh', {id});
    const cnt = t.closest('[data-count]');
    if (cnt) {
      const inp = $('bLeft' + cnt.dataset.count);
      if (inp.value === '') { inp.focus(); return; }
      return change('/api/board/stock', {line_id: +cnt.dataset.count, left: inp.value});
    }
    const ro = t.closest('[data-reorder]');
    if (ro) {
      if (!confirm(`Start a re-order for ${MK[ro.dataset.reorder]}? The price per unit and shipment type come from the last order; you add how many and the shipping cost.`)) return;
      const r = await change('/api/board/reorder', {id, market: ro.dataset.reorder});
      if (r) { tab = 'sourcing'; orderN = r.n; drawCard(); }
      return;
    }
    if (t.closest('[data-note]')) {
      const text = $('bNote').value.trim();
      if (!text) { $('bNote').focus(); return; }
      return change('/api/board/note', {id, text});
    }
    if (t.closest('[data-stratadd]')) {
      const text = $('bStratText').value.trim();
      if (!text) { $('bStratText').focus(); toast('Write the strategy first.'); return; }
      return change('/api/board/strategy', {id, add: {by: $('bStratBy').value, text}});
    }
    const se = t.closest('[data-stratedit]');
    if (se) { stratEdit = +se.dataset.stratedit; drawCard(); const x = dlg.querySelector(`[data-stext="${stratEdit}"]`); if (x) x.focus(); return; }
    if (t.closest('[data-stratcancel]')) { stratEdit = null; drawCard(); return; }
    const ss = t.closest('[data-stratsave]');
    if (ss) {
      const sid = +ss.dataset.stratsave, box = dlg.querySelector(`[data-stext="${sid}"]`);
      if (!box.value.trim()) { box.focus(); return; }
      const r = await change('/api/board/strategy', {id, edit: {sid, by: dlg.querySelector(`[data-sby="${sid}"]`).value, text: box.value}});
      if (r) { stratEdit = null; drawCard(); }
      return;
    }
    const sr = t.closest('[data-stratrm]');
    if (sr) { if (confirm('Delete this strategy?')) await change('/api/board/strategy', {id, remove: +sr.dataset.stratrm}); return; }
    if (t.closest('[data-archive]')) {
      const title = D.card.title;
      dlg.close();
      openId = null;
      if (await change('/api/board/archive', {id, archived: true})) {
        undoToast(`Removed "${title}" from the board. Its future emails are paused; Undo (or Put back) restores them`, () => change('/api/board/archive', {id, archived: false}));
      }
      return;
    }
    const lv = t.closest('[data-live]');
    if (lv) return liveNow(id, lv);
    const nrm = t.closest('[data-nrm]');
    if (nrm) { if (confirm('Delete this note?')) await change('/api/board/note', {remove: +nrm.dataset.nrm}); return; }
    const crm = t.closest('[data-crm]');
    if (crm) { if (confirm('Delete this count? The selling pace is worked out again without it.')) await change('/api/board/stock', {remove: +crm.dataset.crm}); return; }
    if (t.closest('[data-people]')) return peopleDialog();
    const sv = t.closest('[data-dlsave]');
    if (sv) return saveDeadline(sv.dataset.dlsave, sv.closest('[data-dl]'));
    const sn = t.closest('[data-dlsend]');
    if (sn) { if (await change('/api/board/task', {send: +sn.dataset.dlsend})) toast((ST.settings || {}).mail_method === 'smtp' ? 'The reminder goes out in a moment' : 'The reminder opens in your browser in a moment: press Send there', 3000); return; }
    const pv = t.closest('[data-dlprev]');
    if (pv) return previewDialog(+pv.dataset.dlprev);
    const cc = t.closest('[data-dlcancel]');
    if (cc && confirm('Remove this deadline and its unsent emails?')) return change('/api/board/task', {cancel: +cc.dataset.dlcancel});
  });

  async function saveDeadline(stage, box) {
    const v = k => box.querySelector(`[data-dlf="${k}"]`).value;
    const notify = [...box.querySelectorAll('[data-dlp]')].filter(x => x.checked).map(x => +x.dataset.dlp);
    if (!v('days')) { box.querySelector('[data-dlf="days"]').focus(); toast('How many days to complete it?'); return; }
    const r = await change('/api/board/task', {id: openId, stage, days: v('days'), meet_time: v('meet_time'), meet_minutes: v('meet_minutes'), note: v('note'), notify});
    if (r) { dlOpen = null; drawCard(); toast(`Due ${niceDay(r.due_day)}` + (r.mails ? `: ${r.mails} email${r.mails === 1 ? '' : 's'} scheduled` : ''), 4000); }
  }

  /* ---------- small dialogs: add a product, notifiers, weekly summary, email preview ---------- */
  function mini(html, wire, wide) {
    const d = $('bMini');
    d.classList.toggle('wide', !!wide);
    d.innerHTML = html;
    if (!d.open) d.showModal();
    if (wire) wire(d);
  }
  const closeBtn = `<button type="button" class="btn slim ghost" data-mclose aria-label="Close">${icon('x')}</button>`;

  function addDialog() {
    mini(`<form class="stackv" style="gap:12px" id="baForm">
      <div class="head"><h3>Add a product</h3>${closeBtn}</div>
      <div class="field"><label for="baTitle">Name</label><input type="text" id="baTitle" maxlength="160" required></div>
      <div class="row wrap"><div class="field"><label for="baMk">Market</label><select id="baMk">${MKS.map(m => `<option value="${m}">${MK[m]}</option>`).join('')}</select></div>
        <div class="field" style="flex:1"><label for="baKw">Search words on Amazon</label><input type="text" id="baKw" maxlength="120" placeholder="calming dog bed"></div></div>
      <div class="row wrap"><div class="field"><label for="baAsin">Amazon ASIN (optional)</label><input type="text" id="baAsin" maxlength="10" placeholder="B0…" style="width:140px"></div>
        <div class="field" style="flex:1"><label for="baAli">Alibaba link (optional)</label><input type="text" id="baAli" maxlength="500" placeholder="https://www.alibaba.com/product-detail/…"></div></div>
      <div class="small muted">It starts as an <b>Idea</b>. With the ASIN and the Alibaba link it moves to Approval by itself. <b>Add to board</b> on the Trend radar, Product ideas, a keyword report or a product report brings the picture, ASIN and search words along.</div>
      <div class="row"><button type="submit" class="btn primary">${icon('plus')}Add to the board</button></div></form>`, d => {
      $('baForm').addEventListener('submit', async e => {
        e.preventDefault();
        const r = await addToBoard({title: $('baTitle').value, market: $('baMk').value, kw: $('baKw').value, asin: $('baAsin').value, alibaba: $('baAli').value});
        if (r) { d.close(); await load(); openCard(r.id); }
      });
      $('baTitle').focus();
    });
  }

  function peopleHtml(list) {
    return `<div class="stackv" style="gap:12px">
      <div class="head"><h3>Notifier by email</h3>${closeBtn}</div>
      <div class="small">People who can get deadline emails. On each card you tick who gets them.</div>
      ${list.length ? `<ul class="bplist">${list.map(p => `<li><span><b>${esc(p.name)}</b> <span class="muted">${esc(p.email)}</span></span><button type="button" class="btn slim ghost" data-prm="${p.id}" title="Remove" aria-label="Remove ${esc(p.name)}">${icon('x')}</button></li>`).join('')}</ul>` : '<div class="small muted">Nobody yet.</div>'}
      <form class="row wrap" id="bpForm"><input type="text" id="bpName" placeholder="Name" maxlength="80" aria-label="Name">
        <input type="email" id="bpEmail" placeholder="name@example.com" maxlength="254" required aria-label="Email address">
        <button class="btn primary" type="submit">${icon('plus')}Add</button></form>
      ${B && B.mail.ready ? '' : '<div class="note small">The emails need your Gmail: <a href="#/settings" data-mclose>Settings, Email for reminders</a>.</div>'}</div>`;
  }
  function peopleDialog() {
    const wire = d => d.querySelector('#bpForm').addEventListener('submit', async e => {
      e.preventDefault();
      try {
        const r = await api('/api/board/notifiers', {add: {name: $('bpName').value, email: $('bpEmail').value}});
        B.notifiers = r.notifiers;
        mini(peopleHtml(r.notifiers), wire);
        $('bpName').focus();
        render();
        if (openId) loadCard();
      } catch (err) { toast(err.message); }
    });
    mini(peopleHtml(B.notifiers), wire);
  }

  async function summaryDialog() {
    let r;
    try { r = await api('/api/board/summary'); } catch (e) { toast(e.message); return; }
    mini(`<div class="stackv" style="gap:12px"><div class="head"><h3>Weekly summary</h3>${closeBtn}</div>
      <textarea id="bsText" rows="14" readonly aria-label="Weekly summary">${esc(r.text)}</textarea>
      <div class="row"><button type="button" class="btn primary" id="bsCopy">${icon('copy')}Copy</button>
        <a class="btn" href="mailto:?subject=${encodeURIComponent('Product board summary')}&amp;body=${encodeURIComponent(r.text)}">${icon('mail')}Open in your email</a></div></div>`, () => {
      $('bsCopy').addEventListener('click', () => navigator.clipboard.writeText(r.text)
        .then(() => toast('Copied: paste it into WhatsApp or an email', 2500), () => toast('Could not copy')));
    });
  }

  /* The two team emails, each to the people you tick: Inform attendees (every product, as the board is now) and
     Inform deadlines (just each product and its deadline, the late ones first). */
  const MAILS = {
    inform: {title: 'Inform attendees', path: '/api/board/inform', sent: 'Product update', state: r => informState(r),
      intro: 'One email with every product on the board: units needed, target market, sourcing ETA, and links to Amazon and Alibaba, as the board is now.',
      hint: 'For example: please send me supplier quotes by Friday.'},
    deadlines: {title: 'Inform deadlines', path: '/api/board/deadlines', sent: 'Deadlines email', state: r => deadlinesState(r),
      intro: 'One email with just the deadlines: each product, its stage and its deadline, the late ones first. The product update and the reminder emails stay as they are.',
      hint: 'For example: tell me today if a date will slip.'},
  };

  function informDialog() { return mailDialog('inform'); }

  function mailDialog(kind) {
    const k = MAILS[kind], sel = new Set(B.notifiers.map(p => p.id));
    let msg = '', timer = null;
    const view = async () => {
      const r = await api(`${k.path}?to=${sel.size ? [...sel].join(',') : '0'}&message=${encodeURIComponent(msg)}`);
      $('biSubj').textContent = r.subject;
      $('biFrame').srcdoc = r.html;
      $('biState').innerHTML = k.state(r);
      const b = $('biSend');
      b.disabled = !sel.size || !r.mail.ready || !r.count;
      b.innerHTML = `${icon('mail')}Send to ${sel.size} ${sel.size === 1 ? 'person' : 'people'}`;
    };
    const show = () => view().catch(e => toast(e.message));
    mini(`<div class="stackv binf" style="gap:12px">
      <div class="head"><h3>${k.title}</h3>${closeBtn}</div>
      <div class="small">${k.intro}</div>
      ${B.notifiers.length ? `<div class="field"><span class="lbl">To</span><div class="bpeople">${B.notifiers.map(p => `<label class="small"><input type="checkbox" data-bip="${p.id}" checked> ${esc(p.name)} <span class="muted">${esc(p.email)}</span></label>`).join('')}</div></div>`
        : `<div class="note small">Nobody is on your Notifier list yet. <button type="button" class="btn slim" data-people>${icon('users')}Add people</button></div>`}
      <div class="field"><label for="biMsg">Message (optional)</label><textarea id="biMsg" rows="2" maxlength="1000" placeholder="${k.hint}"></textarea></div>
      <div class="biprev"><div class="small">Subject: <b id="biSubj">…</b></div><iframe id="biFrame" title="Preview of the email" sandbox></iframe></div>
      <div class="small" id="biState"></div>
      <div class="row"><button type="button" class="btn primary" id="biSend" disabled>${icon('mail')}Send</button><button type="button" class="btn ghost" data-mclose>Cancel</button></div>
    </div>`, d => {
      d.querySelectorAll('[data-bip]').forEach(x => x.addEventListener('change', () => {
        if (x.checked) sel.add(+x.dataset.bip); else sel.delete(+x.dataset.bip);
        show();
      }));
      $('biMsg').addEventListener('input', () => { msg = $('biMsg').value; clearTimeout(timer); timer = setTimeout(show, 400); });
      $('biSend').addEventListener('click', async () => {
        const b = $('biSend');
        b.disabled = true;
        b.innerHTML = busy('Sending…');
        try {
          const r = await api(k.path, {to: [...sel], message: msg});
          d.close();
          toast(r.pasted ? `${k.sent} opened in your browser. It was too long for one email link, so the whole email is on your clipboard: in Gmail click in the message, press Ctrl+A, then Ctrl+V, then Send.`
            : r.opened ? `${k.sent} opened in your browser: press Send there` : `${k.sent} sent to ${names(r.to)}`, r.pasted ? 15000 : 4000);
          load();
        } catch (e) { toast(e.message); show(); }
      });
      show();
    }, true);
  }

  function deadlinesState(r) {
    const last = !r.last.sent_at ? 'No deadlines email sent yet.'
      : r.last.opened ? `Last deadlines email opened in your browser ${esc(niceTime(r.last.sent_at))}, for ${esc(names(r.last.to))}.`
      : `Last deadlines email sent ${esc(niceTime(r.last.sent_at))} to ${esc(names(r.last.to))}.`;
    return `${r.count ? `<b>${r.count} deadline${r.count === 1 ? '' : 's'}${r.late ? `, ${r.late} late` : ''}.</b> ` : ''}${last}` +
      (r.count && r.none.length ? ` No deadline yet: ${esc(r.none.join(', '))}.` : '') +
      (r.count ? '' : '<div class="note small">No product has a deadline yet: open a product and press Set a deadline.</div>') +
      (r.mail.ready ? '' : `<div class="note small">The emails need your Gmail: <a href="#/settings" data-mclose>Settings, Email for reminders</a>.</div>`);
  }

  function informState(r) {
    const last = r.last.sent_at ? `${lastUpdate(r.last)}${r.last.auto ? ' (automatic)' : ''}.` : 'No product update emailed yet.';
    const auto = !r.auto ? 'Automatic updates are off (Settings, Product board).'
      : r.next_at ? `Changes are waiting: they go out on their own about <b>${esc(niceTime(r.next_at))}</b> unless you change more.`
      : `When you add or change a product, everyone gets an update ${r.wait_min} minutes after you stop editing.`;
    return `${r.changes.length ? `<b>${r.changes.length} change${r.changes.length === 1 ? '' : 's'} since the last update.</b> ` : ''}${last} ${auto}` +
      (r.error ? `<div class="bierr">The last automatic try failed: ${esc(r.error)}</div>` : '') +
      (r.mail.ready ? '' : `<div class="note small">The emails need your Gmail: <a href="#/settings" data-mclose>Settings, Email for reminders</a>.</div>`);
  }

  async function previewDialog(taskId) {
    let r;
    try { r = await api('/api/board/preview?task=' + taskId); } catch (e) { toast(e.message); return; }
    mini(`<div class="stackv" style="gap:12px"><div class="head"><h3>The emails of this deadline</h3>${closeBtn}</div>
      ${r.mails.map(m => `<div class="bmail"><div class="small muted">${KIND[m.kind]} · ${m.status === 'scheduled' ? esc(niceTime(m.send_at)) : esc(MAIL_DONE[m.status] || m.status)}</div><b>${esc(m.subject || '')}</b><pre>${esc(m.text || '')}</pre></div>`).join('') || '<div class="small muted">No emails: nobody is picked.</div>'}</div>`);
  }

  $('bMini').addEventListener('click', async e => {
    if (e.target === $('bMini') || e.target.closest('[data-mclose]')) { $('bMini').close(); return; }
    if (e.target.closest('[data-people]')) { peopleDialog(); return; }
    const rm = e.target.closest('[data-prm]');
    if (rm) {
      try {
        const r = await api('/api/board/notifiers', {remove: +rm.dataset.prm});
        B.notifiers = r.notifiers;
        peopleDialog();
        render();
        if (openId) loadCard();
      } catch (err) { toast(err.message); }
    }
  });

  /* ---------- Settings: Email for reminders ---------- */
  function mailProfiles() {
    const s = ST.settings || {}, want = s.mail_profile || '';
    const auto = s.mail_address ? `Automatic: the profile signed in as ${s.mail_address}, else the one for links` : 'Automatic: the profile signed in with your email address, else the one for links';
    const opts = [{id: '', label: auto}].concat(ST.profiles || []);
    $('mailProfile').innerHTML = opts.map(p => `<option value="${esc(p.id)}" ${p.id === want ? 'selected' : ''}>${esc(p.label)}</option>`).join('');
  }
  async function mailState() {
    try {
      const m = await api('/api/mail');
      if (!$('mailPanel').contains(document.activeElement)) mailProfiles();
      $('mailMethod').value = m.method;
      $('mailBrowser').hidden = m.method !== 'browser';
      $('mailSmtp').hidden = m.method === 'browser';
      $('mailWhere').innerHTML = `Emails will open in <b>${esc(m.where)}</b>.`;
      $('mailTest').textContent = m.method === 'browser' ? 'Open a test email' : 'Send me a test email';
      $('mailState').innerHTML = (m.has_password ? `<span class="pill good">${icon('ok')}App password saved</span>` : '<span class="pill weak">No app password yet</span>') +
        (m.pending ? ` <span class="small muted">${m.pending} email${m.pending === 1 ? '' : 's'} waiting to go out</span>` : '');
      $('mailPassForget').hidden = !m.has_password;
    } catch (e) { $('mailState').textContent = e.message; }
  }
  $('mailPassSave').addEventListener('click', async () => {
    const p = $('mailPass').value;
    if (!p.trim()) { $('mailPass').focus(); return; }
    try { await api('/api/mail', {password: p}); $('mailPass').value = ''; toast('App password saved', 2000); mailState(); }
    catch (e) { toast(e.message); }
  });
  $('mailPassForget').addEventListener('click', async () => {
    if (!confirm('Forget the saved app password? Reminder emails stop until you save one again.')) return;
    await api('/api/mail', {forget: true}).catch(e => toast(e.message));
    mailState();
  });
  $('mailTest').addEventListener('click', async () => {
    $('mailTest').disabled = true;
    $('mailTestOut').innerHTML = busy('Sending…');
    try {
      const r = await api('/api/mail/test', {});
      $('mailTestOut').innerHTML = r.opened ? `<span class="pill good">${icon('ok')}Opened in ${esc(r.where)}: press Send there</span>`
        : `<span class="pill good">${icon('ok')}Sent to ${esc(r.to)}: check your inbox</span>`;
    }
    catch (e) { $('mailTestOut').innerHTML = failPill(e.message); }
    $('mailTest').disabled = false;
  });
  $('mailPanel').addEventListener('change', e => {         // the setting is saved first (Settings), then shown here
    if (/^mail_(method|profile|web|address)$/.test(e.target.dataset.set || '')) setTimeout(mailState, 600);
  });
  window.addEventListener('hashchange', () => { if (location.hash.startsWith('#/settings')) mailState(); });
  let mailSeen = false;
  onState(s => { if (!mailSeen && s.settings && s.settings.locations) { mailSeen = true; mailState(); } });   // labels need your address
  mailState();
})();
