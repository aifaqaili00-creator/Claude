'use strict';
/* Home: what changed since you last looked. Alerts, gaps, refresh status, and the first-run checklist. */
(() => {
  view('home', {title: 'Dashboard', sub: 'Your markets, your products and what changed', onShow: () => load(true)});
  const SEV = {3: ['bad', 'warn', 'Urgent'], 2: ['warn', 'dot', 'Important'], 1: ['weak', 'dot', 'Info']};
  const RULE = {gap_open: 'Gap', gap_closing: 'Gap closing', demand_up: 'Demand up', demand_down: 'Demand down',
    competition_arriving: 'Competition', price_war: 'Price war', new_mover: 'New mover', persistent_riser: 'Riser',
    cluster_surge: 'Surge', trend_label_change: 'Trend', trends_breakout: 'Breakout', confirmed_trend: 'Confirmed trend',
    captcha_needed: 'Captcha', source_paused: 'Paused', stale_data: 'Stale data', layout_drift: 'Page changed',
    fees_stale: 'Fees', rerun_xray: 'Re-run Xray', order_by: 'Order by', board_stock_low: 'Low stock', board_reorder: 'Re-order',
    board_mail_failed: 'Email not sent', board_mail_missed: 'Email missed', board_inform_failed: 'Update not sent',
    lone_second_seller: 'New seller', amazon_stockout: 'Amazon out of stock'};
  let data = null;

  async function load(markSeen) {
    try { data = await api('/api/home'); } catch (e) { $('homeOut').innerHTML = failPill(e.message); return; }
    render();
    const unseen = data.alerts.filter(a => !a.seen_at).map(a => a.id);
    if (markSeen && unseen.length) setTimeout(() => api('/api/alerts/mark', {ids: unseen}).catch(() => {}), 1500);
  }

  function tile(label, value, sub, cls = '') {
    return `<div class="kpi ${cls}"><span class="eyebrow">${esc(label)}</span><span class="v">${value}</span>${sub ? `<span class="s">${sub}</span>` : ''}</div>`;
  }
  /* the dashboard: one clickable card per thing to act on, with what it means underneath */
  const STAGES = {idea: 'idea', precheck: 'pre-check', approval: 'approval', sourcing: 'sourcing', launch: 'launching', reorder: 're-order'};
  function card(href, label, value, sub, ic, tone = '') {
    const tag = href ? 'a' : 'div';
    return `<${tag} class="kpi ${tone}"${href ? ` href="${href}"` : ''}><span class="ic">${icon(ic)}</span><span class="eyebrow">${esc(label)}</span>
      <span class="v">${value}</span><span class="s">${sub}</span>${href ? '<span class="go">Open →</span>' : ''}</${tag}>`;
  }
  function dashTiles(d) {
    const g = d.dashboard || {}, f = g.found || {}, st = g.stock || {}, gp = g.gaps || {}, bd = g.board || {};
    const found = (f.AU || 0) + (f.AE || 0);
    const stages = Object.entries(bd.stages || {}).filter(([, n]) => n).map(([k, n]) => n + ' ' + (STAGES[k] || k)).join(' · ');
    return `<div class="dash-h"><h2>Your markets at a glance</h2><span class="small muted">Click a card to open it</span></div>
      <div class="kpis dash">
        ${card('#/mgaps/AU/products', 'Products to sell', num(found), found ? `Australia ${num(f.AU || 0)} · UAE ${num(f.AE || 0)}: proven demand, few sellers`
          : num(g.checked || 0) + ' niches checked so far: press Refresh in Market Gaps', 'box', found ? 'k-good' : '')}
        ${card('#/mgaps/AU/stock', 'Out of stock, few sellers', st.fits == null ? '–' : num(st.fits),
          num(st.watched || 0) + ' booming and brand-new products watched', 'warn', st.fits ? 'k-warn' : '')}
        ${card('#/gaps', 'Gap Finder: open gaps', num(gp.open || 0), num(gp.checked || 0) + ' proven products checked in 60 days', 'globe', gp.open ? 'k-good' : '')}
        ${card('#/board', 'Products on the board', num(bd.products || 0), (stages || 'nothing on the board yet') +
          (bd.late ? ` · <b>${num(bd.late)} late</b>` : ''), 'board', bd.late ? 'k-bad' : '')}
        ${card('', 'New alerts, 7 days', num(d.new_7d), num(d.market_alerts_7d) + ' about new trends: listed below', 'bell')}
        ${card('#/watch', 'Watching', num(d.watch_n), d.watch_n ? 'keywords and markets, checked every day' : 'add a keyword to watch', 'eye')}
      </div>`;
  }
  const inT = t => { const s = t - Date.now() / 1000; return s <= 60 ? 'now' : s < 3600 ? 'in ' + Math.round(s / 60) + ' min' : s < 86400 ? 'in ' + Math.round(s / 3600) + ' h' : 'in ' + Math.round(s / 86400) + ' d'; };

  function render() {
    const d = data, sc = d.scheduler || {}, c = d.checklist;
    const cooling = Object.entries(d.sources || {});
    const captcha = cooling.filter(([, v]) => /captcha/.test(v.reason || ''));
    const steps = [[c.checked, 'Check a product', '#/check'], [c.imported, 'Import your Helium 10 exports (Library → Helium 10 imports)', '#/library/imports'],
                   [c.watching, 'Watch a keyword, so it refreshes by itself', '#/watch'], [c.auto, 'Automatic refresh is on', '#/settings'],
                   [c.startup, 'Start with Windows (optional)', '#/settings']];
    const todo = steps.filter(s => !s[0]).length;
    const days = {};
    d.alerts.forEach(a => { const k = new Date(a.ts * 1000).toDateString(); (days[k] = days[k] || []).push(a); });
    $('homeOut').innerHTML = `
      ${captcha.map(([src, v]) => `<div class="verdict some">${icon('warn')}<span><b>${esc(src.replace('amazon:', 'Amazon '))} asked for a captcha</b> during the automatic refresh. It is paused until ${new Date(v.cooldown_until * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}.
        <button class="btn slim" data-solve="${esc(src)}">Solve now</button></span></div>`).join('')}
      ${dashTiles(d)}
      <div class="dash-h"><h2>Your watched keywords</h2></div>
      <div class="kpis">
        ${tile('Best AU / UAE gap', d.gaps.length ? esc(d.gaps[0].kw) : '–', d.gaps.length ? esc(MK[d.gaps[0].market]) + ' · ' + d.gaps[0].fast + ' fast local sellers' : 'none among watched keywords')}
        ${tile('Data', cooling.length ? cooling.length + ' paused' : 'All good', d.trends && d.trends.paused ? 'Google Trends paused' : num(d.counts.serp_snapshot) + ' searches stored', 'adv')}
        ${tile('Automatic refresh', sc.paused ? esc(sc.paused) : sc.running ? 'Running…' : 'On', sc.last_run ? 'last ' + ago(sc.last_run.ts) + (sc.next_due ? ' · next ' + inT(sc.next_due) : '') : sc.next_due ? 'next ' + inT(sc.next_due) : 'nothing scheduled yet', 'adv')}
      </div>
      <div class="row">
        <button class="btn primary" id="hRun">${icon('refresh')}Refresh everything now</button>
        ${sc.paused === 'paused by you' ? '<button class="btn" id="hResume">Resume</button>' : '<button class="btn adv" id="hPause">Pause 1 hour</button>'}
      </div>
      ${todo ? `<div class="panel stackv"><h3>Getting started</h3><ul class="reasons">${steps.map(s => `<li class="${s[0] ? 'good' : 'warn'}"><a href="${s[2]}">${esc(s[1])}</a></li>`).join('')}</ul></div>` : ''}
      ${(d.opportunities || []).length ? `<div class="panel"><h3 style="margin-bottom:8px">Best opportunities among your watched keywords</h3><div class="tablewrap" style="box-shadow:none"><table><tbody>
        ${d.opportunities.map(o => `<tr><td><a href="#/k/${o.market}/${encodeURIComponent(o.kw)}"><b>${esc(o.kw)}</b></a></td><td>${mkFlag(o.market, true)}</td>
          <td><span class="pill ${({Strong: 'good', 'Worth a look': 'accent', Weak: 'warn', Skip: 'bad'})[o.label] || 'weak'}">${esc(o.label)} · ${Math.round(o.score)}</span></td><td class="small adv">${esc(o.why.join(' · '))}</td></tr>`).join('')}
        </tbody></table></div></div>` : ''}
      ${d.gaps.length ? `<div class="panel"><h3 style="margin-bottom:8px">Gaps in Australia and the UAE (watched keywords)</h3><div class="row">${d.gaps.map(g => `<a class="pill good" href="#/k/${g.market}/${encodeURIComponent(g.kw)}">${icon('ok')}${esc(g.kw)} · ${MK_SHORT[g.market]} · ${g.fast} fast${g.units ? ' · ~' + compact(g.units) + '/mo' : ''}</a>`).join('')}</div></div>` : ''}
      <h2 class="section">Alerts</h2>
      ${Object.keys(days).length ? Object.entries(days).map(([day, list]) => `<div class="panel stackv"><h3>${esc(day === new Date().toDateString() ? 'Today' : day)}</h3>
        ${list.map(a => { const s = SEV[a.severity] || SEV[1]; const isKw = a.market && a.target && !['search', 'lists', 'db', 'board'].includes(a.target) && !/^B0[A-Z0-9]{8}$/.test(a.target);
          return `<div class="alert-row${a.seen_at ? '' : ' unseen'}"><span class="pill ${s[0]}">${icon(s[1])}${esc(RULE[a.rule] || a.rule)}</span>
            <div class="alert-body"><b>${esc(a.title)}</b><div class="small">${esc(a.body || '')}</div></div>
            <div class="row" style="gap:6px">${a.market ? `<span class="pill line">${esc(MK_SHORT[a.market] || a.market)}</span>` : ''}
              ${isKw ? `<a class="btn slim" href="#/k/${a.market}/${encodeURIComponent(a.target)}">Report</a>` : ''}
              ${a.target === 'board' ? `<a class="btn slim" href="#/board${a.data && a.data.card_id ? '/' + (+a.data.card_id) : ''}">${icon('board')}Board</a>` : ''}
              ${a.rule === 'lone_second_seller' ? `<a class="btn slim" href="#/gaps">${icon('globe')}Gap Finder</a>` : ''}
              ${a.data && a.data.asin ? `<button class="btn slim" data-open="${esc((ST.markets[a.market] || {}).site + '/dp/' + a.data.asin)}">Amazon</button>` : ''}
              <button class="btn slim ghost" data-dismiss="${a.id}" title="Dismiss">${icon('x')}</button></div></div>`; }).join('')}</div>`).join('')
        : '<div class="panel empty"><h2>No alerts yet</h2><p>Watch a few keywords and leave automatic refresh on. New trends, gaps and changes appear here.</p></div>'}`;
    $('hRun').addEventListener('click', () => api('/api/scheduler', {action: 'run_now'}).then(() => { toast('Refreshing everything in the background'); setTimeout(load, 1500); }).catch(e => toast(e.message)));
    if ($('hPause')) $('hPause').addEventListener('click', () => api('/api/scheduler', {action: 'pause', hours: 1}).then(load));
    if ($('hResume')) $('hResume').addEventListener('click', () => api('/api/scheduler', {action: 'resume'}).then(load));
  }

  $('homeOut').addEventListener('click', async e => {
    const d = e.target.closest('[data-dismiss]'), s = e.target.closest('[data-solve]');
    if (d) { await api('/api/alerts/mark', {ids: [+d.dataset.dismiss], dismiss: true}).catch(() => {}); load(); }
    if (s) {
      await api('/api/source/clear', {source: s.dataset.solve}).catch(() => {});
      await api('/api/browser', {action: 'show'}).catch(() => {});
      toast('Run any check for this marketplace: the captcha appears in the checker window, solve it there.', 9000);
      go('check');
    }
  });
  onState((s, fresh) => { if (currentView === 'home' && fresh.some(m => /new alert/.test(m.msg))) load(false); });
})();
