'use strict';
/* Watchlist: keywords refreshed automatically, with their stage in your pipeline. */
(() => {
  const STAGES = ['idea', 'researching', 'sourcing', 'ordered', 'launched', 'rejected'];
  let rows = [];
  view('watch', {title: 'Watchlist', sub: 'Keywords the app re-checks by itself, with demand and sellers over time', onShow: () => load()});
  $('wMk').dataset.multi = '1';
  seg($('wMk'));
  $('wForm').addEventListener('submit', async e => {
    e.preventDefault();
    const kw = $('wKw').value.trim(), markets = pressed($('wMk'));
    if (!kw) return $('wKw').focus();
    if (!markets.length) return toast('Pick at least one country');
    try { await api('/api/watch', {kind: 'keyword', target: kw, markets}); $('wKw').value = ''; toast('Watching “' + kw + '”'); load(); }
    catch (err) { toast(err.message); }
  });
  $('wRun').addEventListener('click', () => api('/api/scheduler', {action: 'run_now'}).then(() => toast('Refreshing in the background')).catch(e => toast(e.message)));

  async function load() {
    try { rows = await api('/api/watchlist'); } catch (e) { $('watchOut').innerHTML = failPill(e.message); return; }
    const sc = ST.scheduler || {};
    $('wSched').textContent = sc.paused ? 'Automatic refresh: ' + sc.paused : sc.last_run ? 'Last refresh ' + ago(sc.last_run.ts) : '';
    const kws = rows.filter(r => r.kind === 'keyword');
    if (!kws.length) {
      $('watchOut').innerHTML = '<div class="panel empty"><h2>Nothing watched yet</h2><p>Type a keyword above, or press <b>Watch</b> on a keyword report or a radar card.<br>Watched keywords are searched again every day, and Google Trends weekly, so the charts fill in by themselves.</p></div>';
      return;
    }
    $('watchOut').innerHTML = `<div class="tablewrap"><table><thead><tr><th>Keyword</th><th>Market</th><th>Stage</th><th>Bought / month (page 1)</th><th class="r">Fast local</th><th class="r">Overseas</th><th class="r">Price</th><th class="r">Review barrier</th><th>Search trend</th><th>Checked</th><th></th></tr></thead><tbody>
      ${kws.map(r => `<tr><td><a style="cursor:pointer" href="#/k/${r.market}/${encodeURIComponent(r.display || r.target)}"><b>${esc(r.display || r.target)}</b></a></td>
        <td>${esc(MK_SHORT[r.market])}</td>
        <td><select data-stage="${r.id}" aria-label="Stage">${STAGES.map(s => `<option value="${s}" ${s === r.stage ? 'selected' : ''}>${s[0].toUpperCase() + s.slice(1)}</option>`).join('')}</select></td>
        <td>${r.units ? `<b>${compact(r.units.v)}</b> <span class="sub">${compact(r.units.lo)}–${compact(r.units.hi)}</span> ` : '<span class="muted small">–</span> '}${Charts.spark(r.spark, {label: 'bought per month'})}</td>
        <td class="r">${r.fast ?? '–'}${r.fast_change ? ` <span class="${r.fast_change > 0 ? 'down' : 'up'}">${r.fast_change > 0 ? '+' : ''}${r.fast_change}</span>` : ''}</td>
        <td class="r">${r.overseas_share != null ? Math.round(r.overseas_share * 100) + '%' : '–'}</td>
        <td class="r">${r.price_med != null ? num(r.price_med, 0) : '–'}</td><td class="r">${r.review_barrier != null ? num(r.review_barrier) : '–'}</td>
        <td>${r.trend && r.trend.label ? esc(r.trend.label) : '<span class="muted small">–</span>'}</td>
        <td>${r.checked_at ? ago(r.checked_at) : '<span class="muted small">waiting</span>'}${r.last_status && !['ok', 'empty', 'partial'].includes(r.last_status) ? ` <span class="pill warn" title="Last attempt">${esc(r.last_status)}</span>` : ''}</td>
        <td><button class="btn slim ghost" data-unwatch="${r.id}" title="Stop watching">${icon('x')}</button></td></tr>`).join('')}
      </tbody></table></div><p class="small" style="margin-top:8px">Fast local = arrives within ${ST.settings.fast_days || 3} days (green change = fewer fast sellers than a week ago, so the gap is opening).</p>`;
  }
  $('watchOut').addEventListener('change', e => {
    const s = e.target.closest('[data-stage]'); if (!s) return;
    api('/api/watch/update', {id: +s.dataset.stage, stage: s.value}).then(() => toast('Stage saved', 1500)).catch(err => toast(err.message));
  });
  $('watchOut').addEventListener('click', async e => {
    const b = e.target.closest('[data-unwatch]'); if (!b) return;
    await api('/api/watch/remove', {id: +b.dataset.unwatch}).catch(err => toast(err.message));
    load();
  });
})();
