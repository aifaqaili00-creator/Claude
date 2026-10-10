'use strict';
/* Home: what changed since you last looked. Alerts, gaps, refresh status, and the first-run checklist. */
(() => {
  view('home', {title: 'Home', sub: 'What changed in your markets', onShow: () => load(true)});
  const SEV = {3: ['bad', 'warn', 'Urgent'], 2: ['warn', 'dot', 'Important'], 1: ['weak', 'dot', 'Info']};
  const RULE = {gap_open: 'Gap', gap_closing: 'Gap closing', demand_up: 'Demand up', demand_down: 'Demand down',
    competition_arriving: 'Competition', price_war: 'Price war', new_mover: 'New mover', persistent_riser: 'Riser',
    cluster_surge: 'Surge', trend_label_change: 'Trend', trends_breakout: 'Breakout', confirmed_trend: 'Confirmed trend',
    captcha_needed: 'Captcha', source_paused: 'Paused', stale_data: 'Stale data', layout_drift: 'Page changed',
    fees_stale: 'Fees', rerun_xray: 'Re-run Xray', order_by: 'Order by'};
  let data = null;

  async function load(markSeen) {
    try { data = await api('/api/home'); } catch (e) { $('homeOut').innerHTML = failPill(e.message); return; }
    render();
    const unseen = data.alerts.filter(a => !a.seen_at).map(a => a.id);
    if (markSeen && unseen.length) setTimeout(() => api('/api/alerts/mark', {ids: unseen}).catch(() => {}), 1500);
  }

  function tile(label, value, sub) {
    return `<div class="kpi"><span class="eyebrow">${esc(label)}</span><span class="v">${value}</span>${sub ? `<span class="s">${sub}</span>` : ''}</div>`;
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
      <div class="kpis">
        ${tile('New alerts, 7 days', d.new_7d, d.market_alerts_7d + ' about new trends')}
        ${tile('Watching', d.watch_n, d.watch_n ? 'keywords and markets' : '<a href="#/watch">add a keyword</a>')}
        ${tile('Best AU / UAE gap', d.gaps.length ? esc(d.gaps[0].kw) : '–', d.gaps.length ? esc(MK[d.gaps[0].market]) + ' · ' + d.gaps[0].fast + ' fast local sellers' : 'none among watched keywords')}
        ${tile('Data', cooling.length ? cooling.length + ' paused' : 'All good', d.trends && d.trends.paused ? 'Google Trends paused' : num(d.counts.serp_snapshot) + ' searches stored')}
        ${tile('Automatic refresh', sc.paused ? esc(sc.paused) : sc.running ? 'Running…' : 'On', sc.last_run ? 'last ' + ago(sc.last_run.ts) + (sc.next_due ? ' · next ' + inT(sc.next_due) : '') : sc.next_due ? 'next ' + inT(sc.next_due) : 'nothing scheduled yet')}
      </div>
      <div class="row">
        <button class="btn primary" id="hRun">${icon('refresh')}Refresh everything now</button>
        ${sc.paused === 'paused by you' ? '<button class="btn" id="hResume">Resume</button>' : '<button class="btn" id="hPause">Pause 1 hour</button>'}
      </div>
      ${todo ? `<div class="panel stackv"><h3>Getting started</h3><ul class="reasons">${steps.map(s => `<li class="${s[0] ? 'good' : 'warn'}"><a href="${s[2]}">${esc(s[1])}</a></li>`).join('')}</ul></div>` : ''}
      ${d.gaps.length ? `<div class="panel"><h3 style="margin-bottom:8px">Gaps in Australia and the UAE (watched keywords)</h3><div class="row">${d.gaps.map(g => `<a class="pill good" href="#/k/${g.market}/${encodeURIComponent(g.kw)}">${icon('ok')}${esc(g.kw)} · ${MK_SHORT[g.market]} · ${g.fast} fast${g.units ? ' · ~' + compact(g.units) + '/mo' : ''}</a>`).join('')}</div></div>` : ''}
      <h2 class="section">Alerts</h2>
      ${Object.keys(days).length ? Object.entries(days).map(([day, list]) => `<div class="panel stackv"><h3>${esc(day === new Date().toDateString() ? 'Today' : day)}</h3>
        ${list.map(a => { const s = SEV[a.severity] || SEV[1]; const isKw = a.market && a.target && !['search', 'lists', 'db'].includes(a.target) && !/^B0[A-Z0-9]{8}$/.test(a.target);
          return `<div class="alert-row${a.seen_at ? '' : ' unseen'}"><span class="pill ${s[0]}">${icon(s[1])}${esc(RULE[a.rule] || a.rule)}</span>
            <div class="alert-body"><b>${esc(a.title)}</b><div class="small">${esc(a.body || '')}</div></div>
            <div class="row" style="gap:6px">${a.market ? `<span class="pill line">${esc(MK_SHORT[a.market] || a.market)}</span>` : ''}
              ${isKw ? `<a class="btn slim" href="#/k/${a.market}/${encodeURIComponent(a.target)}">Report</a>` : ''}
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
