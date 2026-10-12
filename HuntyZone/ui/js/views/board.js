'use strict';
/* Product board (#/board, #/board/<id>): your products from Sourcing & PO to Launch & strategy to Re-order, with
   trophies, deadlines and the reminder emails they send. Also binds Settings, Email for reminders. */
(() => {
  const STAGES = [['sourcing', 'Sourcing & PO'], ['launch', 'Launch & strategy'], ['reorder', 'Re-order']];
  const STAGE = Object.fromEntries(STAGES);
  const COLORS = ['blue', 'violet', 'teal', 'green', 'amber', 'orange', 'red', 'pink', 'gray'];
  const STATUS = [['todo', 'To check'], ['done', 'Done'], ['adjusted', 'Changed'], ['skip', 'Not relevant']];
  const KIND = {start: 'New task email', reminder: 'Reminder', meeting: 'Meeting invite'};
  const CAT = {price: 'Price', money: 'Profit', reviews: 'Reviews', competition: 'Competition', product: 'Product',
    listing: 'Listing', timing: 'Timing', sales: 'Sales target', trend: 'Search trend', search: 'Research', own: 'Your own'};
  const STEP = [['deposit', 'Deposit paid'], ['produced', 'Produced'], ['shipped', 'Shipped'], ['arrived', 'Arrived']];
  const EMPTY = {sourcing: 'Use Add product, or Add to board on the Trend radar or in a report.',
    launch: 'Products move here when their order is complete.', reorder: 'Products move here once they are live on Amazon.'};
  const LOCKED = {launch: 'Locked: finish Sourcing & PO first', reorder: 'Locked: launch it first'};
  const EV = {trophy: 'trophy', mail: 'mail', task: 'clock', stock: 'box', arrived: 'box', moved: 'board', created: 'plus',
    note: 'file', reorder: 'refresh', live: 'ok', hint: 'ok', po: 'file', edit: 'file', archived: 'trash', restored: 'plus'};
  const CONF = ['#2f6fd6', '#7a5af8', '#0e9384', '#f5b700', '#f04438', '#17b26a'];
  const MKS = ['US', 'AU', 'AE'];
  const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  let B = null, D = null, openId = null, tab = null, orderN = null, lastRev = null, drag = null, dlOpen = null, binOpen = false;
  const filt = {mk: '', q: ''};

  view('board', {title: 'Board', sub: 'Your products from sourcing to re-order', onShow: p => {
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
      : inf.sent_at ? `${icon('ok')}<span>Last product update emailed ${esc(niceTime(inf.sent_at))} to ${esc(names(inf.to))}.</span>` : '';
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
            <input type="search" id="bFind" placeholder="Find a product" value="${esc(filt.q)}" aria-label="Find a product">
            <div class="seg slim" id="bMk" role="group" aria-label="Market">${['', ...MKS].map(m => `<button type="button" data-v="${m}" aria-pressed="${filt.mk === m}">${m ? mkFlag(m, true) : 'All'}</button>`).join('')}</div>
            <button type="button" class="btn" id="bInform" title="Email everyone on your Notifier list where each product stands">${icon('mail')}Inform attendees</button>
            <button type="button" class="btn" id="bSum">${icon('copy')}Weekly summary</button>
            <button type="button" class="btn" id="bPeople">${icon('users')}Notifiers (${B.notifiers.length})</button>
            <button type="button" class="btn primary" id="bNew">${icon('plus')}Add product</button>
          </div>
        </div>
        <div class="bprog" role="img" aria-label="${share}% of the milestones reached" title="${got} of ${cards.length * 2} milestones reached (Order ready, Launched)"><span style="width:${share}%"></span></div>
        ${status ? `<div class="small binfo" role="status">${status}<button type="button" class="btn slim ghost" data-inform>See the email</button></div>` : ''}
      </div>
      ${B.mail.ready ? '' : `<div class="note small">To email deadlines and reminders to your team, set up your Gmail in <a href="#/settings">Settings, Email for reminders</a>.</div>`}
      <div class="bcols" id="bCols"></div>
      ${B.recent.length ? `<div class="panel brecent"><h3>Latest</h3><ul>${B.recent.map(e => `<li>${evIcon(e.kind)}<span><a href="#/board/${e.card_id}" data-bopen="${e.card_id}">${esc(e.title)}</a>: ${esc(e.text)} <span class="muted">${ago(e.ts)}</span></span></li>`).join('')}</ul></div>` : ''}
      ${B.archived.length ? `<details class="small barch"${binOpen ? ' open' : ''}><summary>${icon('trash')}Removed (${B.archived.length})</summary><ul>${B.archived.map(a => `<li><span>${esc(a.title)} · ${mkFlag(a.market, true)} <span class="muted">removed ${ago(a.archived_at)}</span></span><span class="row"><button type="button" class="btn slim" data-restore="${a.id}">Put back</button><button type="button" class="btn slim ghost" data-purge="${a.id}" data-title="${esc(a.title)}">Delete for good</button></span></li>`).join('')}</ul></details>` : ''}`;
    cols();
  }

  function cols() {
    const q = filt.q.trim().toLowerCase();
    const cards = B.cards.filter(c => (!filt.mk || c.markets.some(m => m.market === filt.mk)) &&
      (!q || (c.title + ' ' + c.kw + ' ' + c.key).toLowerCase().includes(q)));
    $('bCols').innerHTML = STAGES.map(([k, name]) => {
      const list = cards.filter(c => c.stage === k).sort((a, b) => a.pos - b.pos || a.id - b.id);
      const sum = list.reduce((a, c) => a + (c.invested_usd || 0), 0);
      return `<section class="bcol st-${k}" aria-label="${esc(name)}">
        <header class="bcol-h"><i class="bdot" aria-hidden="true"></i><h3>${esc(name)}</h3><span class="count">${list.length}</span>${sum ? `<span class="small muted">${mine(sum)}</span>` : ''}</header>
        <div class="bcards" data-drop="${k}" data-why="${esc(LOCKED[k] || '')}">${list.map(cardHtml).join('') || `<div class="bempty small">${EMPTY[k]}</div>`}</div></section>`;
    }).join('');
  }

  function cardHtml(c) {
    const ms = c.milestones, t = c.task, won = (ms.order_ready ? 1 : 0) + (ms.launched ? 1 : 0) + (ms.reorders || 0);
    const chips = c.markets.map(m => {
      const s = m.stock, lv = s && s.level !== 'unknown' ? ' lv-' + s.level : '';
      const tip = MK[m.market] + (m.units ? ': ' + num(m.units) + ' units in the first order' : '') + (s && s.pct != null ? ', ' + Math.round(s.pct) + '% left' : '');
      return `<span class="bchip${lv}" title="${esc(tip)}">${cflag(m.market, '')}${s && s.pct != null ? Math.round(s.pct) + '% left' : m.units ? num(m.units) : '–'}</span>` +
        (s && s.order_now ? `<span class="bchip lv-red" title="Re-order by ${esc(niceDay(s.reorder_by))}">${icon('warn')}Order now</span>` : '');
    });
    if (t && t.due) chips.push(`<span class="bchip due-${t.due}" title="${esc(STAGE[c.stage])} due ${esc(niceDay(t.due_day))}">${icon('clock')}${t.due === 'late' ? 'Late' : 'Due'} ${esc(niceDay(t.due_day))}</span>`);
    if (t && t.next_mail) chips.push(`<span class="bchip" title="${esc(KIND[t.next_mail.kind])}: ${esc(niceTime(t.next_mail.send_at))}">${icon('mail')}${esc(niceDay(isoOf(t.next_mail.send_at)))}</span>`);
    if (c.stage === 'launch' && c.hints.total) chips.push(`<span class="bchip">${icon('ok')}${c.hints.done}/${c.hints.total}</span>`);
    if (c.hints.moved) chips.push(`<span class="bchip lv-amber" title="The research changed since these hints were checked">${icon('warn')}${c.hints.moved} moved</span>`);
    return `<article class="bcard c-${esc(c.color)}" draggable="true" data-card="${c.id}" data-pos="${c.pos}" tabindex="0" aria-label="${esc(c.title)}, ${esc(STAGE[c.stage])}">
      <div class="bc-top">${c.image ? `<img src="${esc(c.image)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : `<div class="bc-noimg" aria-hidden="true">${icon('box')}</div>`}
        <div class="bc-txt"><div class="bc-key">${esc(c.key)} · ${mkFlag(c.market, true)}</div><div class="bc-title">${esc(c.title)}</div></div>
        ${won ? `<span class="bc-trophy" title="${won} ${won === 1 ? 'trophy' : 'trophies'}">${icon('trophy')}${won > 1 ? won : ''}</span>` : ''}</div>
      <div class="bseg3" aria-hidden="true">${STAGES.map(([k]) => `<span class="st-${k}"><i style="width:${Math.round((c.progress[k] || 0) * 100)}%"></i></span>`).join('')}</div>
      ${chips.length ? `<div class="bc-chips">${chips.join('')}</div>` : ''}
    </article>`;
  }

  $('boardOut').addEventListener('input', e => { if (e.target.id === 'bFind') { filt.q = e.target.value; cols(); } });
  $('boardOut').addEventListener('click', async e => {
    const mk = e.target.closest('#bMk button');
    if (mk) { filt.mk = mk.dataset.v; setSeg($('bMk'), filt.mk); cols(); return; }
    if (e.target.closest('#bNew')) return addDialog();
    if (e.target.closest('#bPeople')) return peopleDialog();
    if (e.target.closest('#bSum')) return summaryDialog();
    if (e.target.closest('#bInform, [data-inform]')) return informDialog();
    const rs = e.target.closest('[data-restore]');
    if (rs) { await change('/api/board/archive', {id: +rs.dataset.restore, archived: false}); return; }
    const pg = e.target.closest('[data-purge]');
    if (pg) {
      if (!confirm(`Delete "${pg.dataset.title}" for good? Its orders, stock counts, notes and history go too. This cannot be undone.`)) return;
      if (await change('/api/board/delete', {id: +pg.dataset.purge})) toast('Deleted', 2500);
      return;
    }
    const bin = e.target.closest('.barch summary');
    if (bin) binOpen = !bin.parentElement.open;
    const op = e.target.closest('[data-bopen]');
    if (op) { e.preventDefault(); openCard(+op.dataset.bopen); return; }
    const card = e.target.closest('[data-card]');
    if (card) openCard(+card.dataset.card);
  });
  $('boardOut').addEventListener('keydown', e => {
    const card = e.target.closest('[data-card]');
    if (card && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); openCard(+card.dataset.card); }
  });

  /* drag a card to another column (only into stages it has unlocked) or to another place in its column */
  $('boardOut').addEventListener('dragstart', e => {
    const el = e.target.closest('[data-card]'); if (!el) return;
    drag = B.cards.find(c => c.id === +el.dataset.card);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', el.dataset.card);
    el.classList.add('dragging');
  });
  $('boardOut').addEventListener('dragend', () => {
    drag = null;
    document.querySelectorAll('.bcard.dragging, .bcards.over, .bcards.nope').forEach(x => x.classList.remove('dragging', 'over', 'nope'));
  });
  $('boardOut').addEventListener('dragover', e => {
    const zone = e.target.closest('[data-drop]'); if (!zone || !drag) return;
    const ok = drag.unlocked.includes(zone.dataset.drop);
    zone.classList.toggle('over', ok); zone.classList.toggle('nope', !ok);
    if (ok) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; }
  });
  $('boardOut').addEventListener('dragleave', e => {
    const zone = e.target.closest('[data-drop]');
    if (zone && !zone.contains(e.relatedTarget)) zone.classList.remove('over', 'nope');
  });
  $('boardOut').addEventListener('drop', async e => {
    const zone = e.target.closest('[data-drop]'); if (!zone || !drag) return;
    e.preventDefault();
    const id = drag.id, els = [...zone.querySelectorAll('[data-card]')].filter(x => +x.dataset.card !== id);
    const at = els.findIndex(x => { const r = x.getBoundingClientRect(); return e.clientY < r.top + r.height / 2; });
    const p = x => parseFloat(x.dataset.pos);
    const pos = !els.length ? 1 : at === 0 ? p(els[0]) - 1 : at === -1 ? p(els[els.length - 1]) + 1 : (p(els[at - 1]) + p(els[at])) / 2;
    zone.classList.remove('over');
    await change('/api/board/move', {id, stage: zone.dataset.drop, pos});
  });

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
  async function openCard(id) {
    if (openId !== id) { tab = null; orderN = null; dlOpen = null; }
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
    if (!tab) tab = D.card.stage;
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
      <div class="bd-colors" role="group" aria-label="Colour">${COLORS.map(k => `<button type="button" class="c-${k}" data-color="${k}" aria-pressed="${c.color === k}" title="${k}" aria-label="${k}"></button>`).join('')}</div>
      <ol class="bstep">${STAGES.map(([k, name], i) => {
        const done = k === 'sourcing' ? ms.order_ready : k === 'launch' ? ms.launched : (ms.reorders || 0);
        const locked = !un.includes(k), now = c.stage === k;
        const sub = done ? (k === 'reorder' ? `${done} re-order${done > 1 ? 's' : ''}` : (k === 'sourcing' ? 'Order ready ' : 'Launched ') + niceDay(isoOf(done)))
          : locked ? 'locked' : now ? 'in progress' : '';
        return `<li class="st-${k}${done ? ' done' : ''}${locked ? ' locked' : ''}${now ? ' now' : ''}"><span class="bs-ic">${done ? icon('trophy') : locked ? icon('lock') : i + 1}</span><b>${esc(name)}</b><span class="small">${esc(sub)}</span></li>`;
      }).join('')}</ol>
      <div class="bd-tabs" role="tablist">${[...STAGES, ['notes', 'Notes & history']].map(([k, name]) => `<button type="button" role="tab" class="st-${k}" data-tab="${k}" aria-selected="${tab === k}">${k !== 'notes' && !un.includes(k) ? icon('lock') : ''}${esc(name)}</button>`).join('')}</div>
      <div class="bd-body">${tab === 'sourcing' ? sourcingTab() : tab === 'launch' ? launchTab() : tab === 'reorder' ? reorderTab() : notesTab()}</div>`;
  }

  /* Deadline & reminders: a short summary (due day, who gets the emails, when each goes out) and, to set or change
     it, a form with days to complete, the people, a note and the update meeting. */
  function deadline(stage) {
    const t = D.tasks[stage], active = !!(t && !t.done_at), ppl = D.notifiers, ms = D.card.milestones, set = ST.settings || {};
    const head = `<div class="bdead-h">${icon('clock')}<b>Deadline & reminders</b>`;
    if (dlOpen !== stage) {
      if (t && t.done_at) return `<div class="bdead bdead-min">${head}<span class="pill good">${icon('ok')}Done ${esc(niceDay(isoOf(t.done_at)))}</span><span class="small muted">it was due ${esc(niceDay(t.due_day))}</span></div></div>`;
      if (!active) {
        if (stage === 'sourcing' ? ms.order_ready : stage === 'launch' ? ms.launched : false) return '';
        return `<div class="bdead bdead-min">${head}<span class="small muted">No deadline yet.</span><button type="button" class="btn slim" data-dlopen="${stage}">${icon('plus')}Set a deadline</button></div></div>`;
      }
      const now = new Date(); now.setHours(0, 0, 0, 0);
      const left = Math.round((dayOf(t.due_day) - now) / 864e5), tone = left < 0 ? 'bad' : left <= 3 ? 'warn' : 'line';
      const who = ppl.filter(p => t.notify.includes(p.id)).map(p => p.name);
      return `<div class="bdead">${head}<span class="pill ${tone}">${left < 0 ? 'Late, was due' : 'Due'} ${esc(niceDay(t.due_day))}</span>
          <span class="small muted">${t.days} day${t.days === 1 ? '' : 's'} from ${esc(niceDay(t.start_day))}${who.length ? ' · emails to ' + esc(who.join(', ')) : ' · no emails'}</span></div>
        ${t.note ? `<div class="small">Note: ${esc(t.note)}</div>` : ''}
        ${t.mails.length ? `<ol class="btimeline">${t.mails.map(m => `<li class="m-${esc(m.status)}">${icon(m.status === 'sent' ? 'ok' : m.status === 'scheduled' ? 'clock' : 'x')}<b>${KIND[m.kind]}</b><span>${m.status === 'sent' ? 'sent ' + esc(niceTime(m.sent_at)) : m.status === 'scheduled' ? esc(niceTime(m.send_at)) + (m.attempts ? ', trying again' : '') : esc(m.status)}</span>${m.error && m.status !== 'sent' ? `<span class="small muted">${esc(m.error)}</span>` : ''}</li>`).join('')}</ol>` : ''}
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

  const lockNote = stage => `<div class="verdict some">${icon('lock')}<span>${stage === 'launch'
    ? 'Opens when Sourcing & PO is complete' + (D.card.missing.length ? ': still needs ' + esc(D.card.missing.join(', ')) : '') + '.'
    : 'Opens once the product is live on Amazon and every launch hint is checked.'} You can set its deadline already.</span></div>`;
  const sumOf = (st, k) => { const v = Object.values(st.markets).map(m => m[k]).filter(x => x != null); return v.length ? v.reduce((a, b) => a + b, 0) : null; };

  function sourcingTab() {
    const o = D.orders.find(x => x.n === orderN) || D.orders[0];
    if (!o) return '<div class="empty">No order yet.</div>';
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
        ${st.complete ? `<div class="verdict opportunity">${icon('trophy')}<span><b>Order ready.</b> ${o.n === 1 ? 'Launch & strategy is open.' : 'Re-order complete.'}</span></div>`
          : `<div class="verdict some">${icon('lock')}<span>Still needed: ${esc(st.missing.join(', '))}.${o.n === 1 ? ' Then Launch & strategy opens.' : ''}</span></div>`}
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

  /* What the product update email says about this product, with its Amazon ASIN and Alibaba link. */
  function teamBox() {
    const t = D.team, c = D.card, ali = (D.data.links || {}).alibaba || '';
    return `<section class="bsec bteam">
      <div class="bsec-h"><h3>${icon('mail')}In the team email</h3><button type="button" class="btn slim" data-inform>${icon('mail')}Inform attendees</button></div>
      <dl class="bfacts"><dt>Product name</dt><dd>${esc(c.title)}</dd>${t.rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>
      <div class="bl-grid2">
        <div class="field"><div class="lblrow"><label for="bAsin">Amazon ASIN</label><a data-open="${esc(t.amazon)}" title="${esc(t.amazon)}">${esc(t.amazon_label)}${icon('external')}</a></div>
          <input type="text" id="bAsin" data-f="asin" data-fk="asin" maxlength="10" value="${esc(c.asin || '')}" placeholder="B0…" spellcheck="false"></div>
        <div class="field"><div class="lblrow"><label for="bAli">Alibaba link</label><a data-open="${esc(t.alibaba)}" title="${esc(t.alibaba)}">${esc(t.alibaba_label)}${icon('external')}</a></div>
          <input type="text" id="bAli" data-f="alibaba" data-fk="alibaba" maxlength="500" value="${esc(ali)}" placeholder="https://www.alibaba.com/product-detail/…" spellcheck="false"></div>
      </div>
      <div class="small muted">Sourcing ETA is the Sourcing & PO deadline above. Without an ASIN or an Alibaba link the email links to a search for “${esc(c.kw)}”.</div>
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

  function launchTab() {
    const by = {}, ls = D.launch;
    D.hints.forEach(h => { (by[h.market] = by[h.market] || []).push(h); });
    const suggest = mk => { const h = D.hints.find(x => x.market === mk && x.key === 'target'); return h && h.data.v ? h.data.v : ''; };
    return `${D.card.unlocked.includes('launch') ? '' : lockNote('launch')}${deadline('launch')}
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
    const low = (ST.settings || {}).board_low_pct || 30;
    return `${deadline('reorder')}
      ${D.stock.map(s => {
        const p = s.pct;
        return `<div class="bstock lv-${esc(s.level || 'unknown')}">
          <div class="row" style="justify-content:space-between"><b>${mkFlag(s.market)}</b><span class="small muted">${s.n === 1 ? 'First order' : 'Re-order #' + s.n}: ${num(s.sent)} units${s.arrived ? ', arrived ' + esc(niceDay(s.arrived)) : ', not arrived yet'}</span></div>
          <div class="bbar" role="img" aria-label="${p == null ? 'no count yet' : Math.round(p) + '% left'}"><span style="width:${p == null ? 0 : Math.min(100, p)}%"></span><i style="left:${low}%" title="Red at ${low}%"></i></div>
          <div class="row wrap"><span class="bst-big">${p == null ? 'No count yet' : `${num(s.left)} left · ${Math.round(p)}%`}</span>
            ${s.order_now ? `<span class="pill bad">${icon('warn')}Re-order now</span>` : s.level === 'red' ? `<span class="pill bad">${icon('warn')}Low stock</span>` : ''}</div>
          <div class="row wrap"><input type="number" id="bLeft${s.line_id}" data-fk="left-${s.line_id}" min="0" step="1" placeholder="Units left now" aria-label="Units left now">
            <button type="button" class="btn" data-count="${s.line_id}">Save count</button>
            <button type="button" class="btn" data-reorder="${s.market}">${icon('refresh')}Start re-order</button></div>
          <ul class="reasons small">
            ${s.rate != null ? `<li class="${s.target_progress && s.target_progress.pct < 60 ? 'warn' : 'good'}"><span>Selling about ${num(s.rate * 30.4)} a month${s.target ? ` (target ${num(s.target)}: ${s.target_progress.pct}%)` : ''}.</span></li>` : '<li class="unk"><span>Two counts a few days apart show how fast it sells.</span></li>'}
            ${s.sellout ? `<li class="${s.order_now ? 'bad' : 'good'}"><span>Runs out about ${esc(niceDay(s.sellout))}. A new shipment takes about ${s.lead_days} days, so ${s.order_now ? `re-order now (the last good day was ${esc(niceDay(s.reorder_by))})` : `re-order by <b>${esc(niceDay(s.reorder_by))}</b>`}.</span></li>` : ''}
          </ul>
          ${s.counts.length ? `<div class="small muted bcounts">Counts: ${s.counts.slice(-6).map(([t, v, cid]) => `<span class="bcount">${num(v)} on ${esc(niceDay(isoOf(t)))}<button type="button" data-crm="${cid}" title="Delete this count" aria-label="Delete the count of ${num(v)} on ${esc(niceDay(isoOf(t)))}">${icon('x')}</button></span>`).join('')}</div>` : ''}
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
    if (f === 'title') return change('/api/board/card', {id, set: {title: v}});
    if (f === 'asin' || f === 'alibaba') return change('/api/board/card', {id, set: {[f]: v.trim()}});
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

  $('bDlg').addEventListener('click', async e => {
    const t = e.target, dlg = $('bDlg'), id = openId;
    if (t === dlg) { const r = dlg.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right) dlg.close(); return; }
    if (t.closest('[data-close]')) { dlg.close(); return; }
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
    if (t.closest('[data-archive]')) {
      if (!confirm(`Remove "${D.card.title}" from the board? Its scheduled emails are cancelled. You can put it back, or delete it for good, under Removed at the bottom of the board.`)) return;
      dlg.close();
      openId = null;
      if (await change('/api/board/archive', {id, archived: true})) toast('Removed from the board', 2500);
      return;
    }
    const nrm = t.closest('[data-nrm]');
    if (nrm) { if (confirm('Delete this note?')) await change('/api/board/note', {remove: +nrm.dataset.nrm}); return; }
    const crm = t.closest('[data-crm]');
    if (crm) { if (confirm('Delete this count? The selling pace is worked out again without it.')) await change('/api/board/stock', {remove: +crm.dataset.crm}); return; }
    if (t.closest('[data-people]')) return peopleDialog();
    const sv = t.closest('[data-dlsave]');
    if (sv) return saveDeadline(sv.dataset.dlsave, sv.closest('[data-dl]'));
    const sn = t.closest('[data-dlsend]');
    if (sn) { if (await change('/api/board/task', {send: +sn.dataset.dlsend})) toast('The reminder goes out in a moment', 3000); return; }
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
      <div class="field"><label for="baAsin">ASIN (optional)</label><input type="text" id="baAsin" maxlength="10" placeholder="B0…" style="width:140px"></div>
      <div class="small muted"><b>Add to board</b> on the Trend radar, Product ideas, a keyword report or a product report brings the picture, ASIN and search words along.</div>
      <div class="row"><button type="submit" class="btn primary">${icon('plus')}Add to the board</button></div></form>`, d => {
      $('baForm').addEventListener('submit', async e => {
        e.preventDefault();
        const r = await addToBoard({title: $('baTitle').value, market: $('baMk').value, kw: $('baKw').value, asin: $('baAsin').value});
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

  /* Inform attendees: one email with every product on the board, as the board is now, to the people you tick. */
  function informDialog() {
    const sel = new Set(B.notifiers.map(p => p.id));
    let msg = '', timer = null;
    const view = async () => {
      const r = await api(`/api/board/inform?to=${sel.size ? [...sel].join(',') : '0'}&message=${encodeURIComponent(msg)}`);
      $('biSubj').textContent = r.subject;
      $('biFrame').srcdoc = r.html;
      $('biState').innerHTML = informState(r);
      const b = $('biSend');
      b.disabled = !sel.size || !r.mail.ready || !r.count;
      b.innerHTML = `${icon('mail')}Send to ${sel.size} ${sel.size === 1 ? 'person' : 'people'}`;
    };
    const show = () => view().catch(e => toast(e.message));
    mini(`<div class="stackv binf" style="gap:12px">
      <div class="head"><h3>Inform attendees</h3>${closeBtn}</div>
      <div class="small">One email with every product on the board: units needed, target market, sourcing ETA, and links to Amazon and Alibaba, as the board is now.</div>
      ${B.notifiers.length ? `<div class="field"><span class="lbl">To</span><div class="bpeople">${B.notifiers.map(p => `<label class="small"><input type="checkbox" data-bip="${p.id}" checked> ${esc(p.name)} <span class="muted">${esc(p.email)}</span></label>`).join('')}</div></div>`
        : `<div class="note small">Nobody is on your Notifier list yet. <button type="button" class="btn slim" data-people>${icon('users')}Add people</button></div>`}
      <div class="field"><label for="biMsg">Message (optional)</label><textarea id="biMsg" rows="2" maxlength="1000" placeholder="For example: please send me supplier quotes by Friday."></textarea></div>
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
          const r = await api('/api/board/inform', {to: [...sel], message: msg});
          d.close();
          toast(`Product update sent to ${names(r.to)}`, 4000);
          load();
        } catch (e) { toast(e.message); show(); }
      });
      show();
    }, true);
  }

  function informState(r) {
    const last = r.last.sent_at ? `Last update emailed ${esc(niceTime(r.last.sent_at))} to ${esc(names(r.last.to))}${r.last.auto ? ' (automatic)' : ''}.` : 'No product update emailed yet.';
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
      ${r.mails.map(m => `<div class="bmail"><div class="small muted">${KIND[m.kind]} · ${m.status === 'scheduled' ? esc(niceTime(m.send_at)) : esc(m.status)}</div><b>${esc(m.subject || '')}</b><pre>${esc(m.text || '')}</pre></div>`).join('') || '<div class="small muted">No emails: nobody is picked.</div>'}</div>`);
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
  async function mailState() {
    try {
      const m = await api('/api/mail');
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
    try { const r = await api('/api/mail/test', {}); $('mailTestOut').innerHTML = `<span class="pill good">${icon('ok')}Sent to ${esc(r.to)}: check your inbox</span>`; }
    catch (e) { $('mailTestOut').innerHTML = failPill(e.message); }
    $('mailTest').disabled = false;
  });
  window.addEventListener('hashchange', () => { if (location.hash.startsWith('#/settings')) mailState(); });
  mailState();
})();
