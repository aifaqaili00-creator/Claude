'use strict';
/* Health: every source with its budget and cooldown, the scheduler, recent runs and the database.
   Also binds the Automatic refresh settings panel. */
(() => {
  view('health', {title: 'Health', sub: 'Sources, budgets, cooldowns and recent refreshes', onShow: () => load()});
  const NAME = s => s.replace('amazon:', 'Amazon ').replace('complete:', 'Amazon suggestions ').replace('google', 'Google Trends');

  async function load() {
    let h;
    try { h = await api('/api/health'); } catch (e) { $('healthOut').innerHTML = failPill(e.message); return; }
    const sc = h.scheduler || {}, tr = h.trends || {};
    $('healthOut').innerHTML = `
      <div class="kpis">
        <div class="kpi"><span class="eyebrow">Automatic refresh</span><span class="v">${sc.paused ? esc(sc.paused) : sc.running ? 'Running' : 'On'}</span><span class="s">${sc.tasks_total} tasks · ${sc.due_now} due now</span></div>
        <div class="kpi"><span class="eyebrow">Google Trends</span><span class="v">${tr.paused ? 'Paused' : 'OK'}</span><span class="s">${tr.paused ? esc(tr.reason) + ' · until ' + new Date(tr.until * 1000).toLocaleTimeString() : 'one request every 6–10 s'}</span></div>
        <div class="kpi"><span class="eyebrow">History database</span><span class="v">${h.db_mb} MB</span><span class="s">${num(h.counts.serp_snapshot)} searches · ${num(h.counts.trends_sample)} Trends samples</span></div>
        <div class="kpi"><span class="eyebrow">Checker browser</span><span class="v">${esc((h.browser || {}).state || '–')}</span><span class="s">${esc((h.browser || {}).error || '')}</span></div>
      </div>
      <div class="panel"><h3 style="margin-bottom:8px">Sources today</h3><div class="tablewrap"><table><thead><tr><th>Source</th><th class="r">Used today</th><th>Background budgets (used / limit, skipped)</th><th>Status</th><th></th></tr></thead><tbody>
        ${Object.entries(h.sources).map(([s, v]) => `<tr><td><b>${esc(NAME(s))}</b></td><td class="r">${v.used_today} / ${v.daily}</td>
          <td class="small">${Object.entries(v.tasks).map(([t, x]) => `${esc(t)} ${x.used}${x.quota ? '/' + x.quota : ''}${x.skipped ? ' (' + x.skipped + ' skipped)' : ''}`).join(' · ')}</td>
          <td>${v.cooldown_until ? `<span class="pill warn">${icon('warn')}paused until ${new Date(v.cooldown_until * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}</span><div class="sub">${esc(v.reason || '')}</div>` : '<span class="pill good"><i></i>ok</span>'}</td>
          <td>${v.cooldown_until ? `<button class="btn slim" data-clear="${esc(s)}">Try again now</button>` : ''}</td></tr>`).join('')}
      </tbody></table></div></div>
      <div class="panel"><h3 style="margin-bottom:8px">Scheduled work</h3><div class="tablewrap"><table><thead><tr><th>Task</th><th>Market</th><th>Target</th><th>Last result</th><th>Last run</th><th>Next run</th></tr></thead><tbody>
        ${h.tasks.map(t => `<tr><td>${esc(t.kind)}</td><td>${esc(t.market || '')}</td><td>${esc(t.target)}</td>
          <td>${t.last_status ? `<span class="pill ${['ok', 'empty', 'partial'].includes(t.last_status) ? 'good' : 'warn'}"><i></i>${esc(t.last_status)}</span>` : '<span class="muted small">not yet</span>'}</td>
          <td>${t.last_run_at ? ago(t.last_run_at) : '–'}</td><td>${new Date(t.next_run_at * 1000).toLocaleString()}</td></tr>`).join('') || '<tr><td colspan="6" class="empty">Nothing scheduled. Turn on automatic refresh in Settings.</td></tr>'}
      </tbody></table></div></div>
      <div class="panel"><h3 style="margin-bottom:8px">Recent refreshes</h3><div class="tablewrap"><table><thead><tr><th>When</th><th>What</th><th>Trigger</th><th>Result</th><th class="r">OK</th><th class="r">Failed</th><th class="r">Blocked</th></tr></thead><tbody>
        ${h.runs.map(r => `<tr><td>${ago(r.started_at)}</td><td>${esc(r.kind)}</td><td>${esc(r.trigger)}</td><td>${esc(r.status)}</td><td class="r">${r.n_ok || 0}</td><td class="r">${r.n_fail || 0}</td><td class="r">${r.n_blocked || 0}</td></tr>`).join('')}
      </tbody></table></div></div>
      <div class="panel" id="calibOut"><h3>Sales curves</h3><div class="small">Loading…</div></div>
      <p class="small">Database: ${esc(h.db_path)} (backups in its backup folder).</p>`;
    calibration();
  }
  async function calibration() {
    let c;
    try { c = await api('/api/calibration'); } catch (e) { return; }
    const box = $('calibOut'); if (!box) return;
    box.innerHTML = `<h3 style="margin-bottom:8px">Sales curves (sales rank → units per month)</h3><div class="tablewrap"><table><thead><tr><th>Market</th><th>Status</th><th class="r">Readings</th><th class="r">Slope</th><th>How good</th><th>Fitted</th></tr></thead><tbody>
      ${Object.entries(c).map(([m, x]) => { const cv = x.curve, dg = cv && cv.diag || {}; const n = Object.values(x.points || {}).reduce((a, b) => a + b, 0);
        return `<tr><td><b>${esc(MK[m])}</b></td><td>${cv ? (cv.status === 'calibrated' ? '<span class="pill good">' + icon('ok') + 'calibrated</span>' : '<span class="pill warn">' + icon('warn') + 'default values</span>') : '<span class="muted small">no readings yet</span>'}</td>
          <td class="r">${n}</td><td class="r">${cv && cv.beta != null ? cv.beta.toFixed(2) : '–'}</td>
          <td class="small">${dg.hit != null ? `Right badge bucket ${Math.round(dg.hit * 100)}% of the time, within one bucket ${Math.round((dg.within1 || 0) * 100)}%, 80% range holds ${Math.round((dg.cov80 || 0) * 100)}%` : 'needs more readings (product pages with a badge, or Helium 10 exports)'}</td>
          <td>${cv && cv.fitted_at ? ago(cv.fitted_at) : '–'}</td></tr>`; }).join('')}
      </tbody></table></div>`;
  }
  $('healthOut').addEventListener('click', async e => {
    const b = e.target.closest('[data-clear]'); if (!b) return;
    await api('/api/source/clear', {source: b.dataset.clear}).catch(err => toast(err.message));
    load();
  });

  /* Settings: Automatic refresh panel */
  let filled = false;
  function fill() {
    const s = ST.settings || {};
    if (filled || s.auto_refresh === undefined) return;
    filled = true;
    $('regAU').checked = !!(s.registered || {}).AU; $('regAE').checked = !!(s.registered || {}).AE;
    document.querySelectorAll('#autoPanel [data-set]').forEach(el => {
      const v = s[el.dataset.set];
      if (el.type === 'checkbox') el.checked = !!v; else el.value = v == null ? '' : v;
    });
  }
  onState(() => fill());
  $('autoPanel').addEventListener('change', async e => {
    if (e.target.id === 'regAU' || e.target.id === 'regAE') {
      try { const r = await api('/api/settings', {registered: {AU: $('regAU').checked, AE: $('regAE').checked}}); ST.settings = r.settings; toast('Saved', 1500); }
      catch (err) { toast(err.message); }
      return;
    }
    const el = e.target.closest('[data-set]'); if (!el) return;
    const v = el.type === 'checkbox' ? el.checked : el.value;
    try { const r = await api('/api/settings', {[el.dataset.set]: v}); ST.settings = r.settings; toast('Saved', 1500); }
    catch (err) { toast(err.message); filled = false; fill(); }
  });
  $('btnQuit').addEventListener('click', async () => {
    if (!confirm('Stop Product Checker completely, including the background refresh?')) return;
    await api('/api/quit', {}).catch(() => {});
    document.body.innerHTML = '<div class="empty"><h2>Product Checker has stopped.</h2><p>Start it again with start.bat or the desktop shortcut.</p></div>';
  });
})();
