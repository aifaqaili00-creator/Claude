'use strict';
/* Library: every keyword ever checked (open its report), recent checks, and Helium 10 imports. */
(() => {
  let tab = 'keywords', lib = null;
  const SUB = 'Every keyword you checked, your recent checks and your Helium 10 history';
  view('library', {title: 'Library', sub: SUB, onShow: p => show(p[0] || tab)});
  view('history', {title: 'Library', sub: SUB, section: 'library', onShow: () => show('checks')});
  seg($('libTabs'), v => { history.replaceState(null, '', '#/library/' + v); show(v); });
  $('libFilter').addEventListener('input', () => renderKeywords());

  async function show(t) {
    tab = ['keywords', 'checks', 'imports'].includes(t) ? t : 'keywords';
    setSeg($('libTabs'), tab);
    $('libKeywords').hidden = tab !== 'keywords';
    $('libChecks').hidden = tab !== 'checks';
    $('libImports').hidden = tab !== 'imports';
    $('libFilter').hidden = tab !== 'keywords';
    if (tab === 'checks') return loadHistory();
    try { lib = await api('/api/library'); } catch (e) { toast(e.message); return; }
    if (tab === 'keywords') renderKeywords(); else renderImports();
  }

  function renderKeywords() {
    if (!lib) return;
    const f = $('libFilter').value.trim().toLowerCase();
    const rows = lib.keywords.filter(k => !f || k.kw_norm.includes(f) || (k.display || '').toLowerCase().includes(f));
    if (!lib.keywords.length) {
      $('libKeywords').innerHTML = '<div class="panel empty"><h2>No keywords yet</h2><p>Every search you run in Check sellers is kept here, with its history and charts.</p></div>';
      return;
    }
    $('libKeywords').innerHTML = `<div class="tablewrap"><table><thead><tr><th>Keyword</th><th>Markets</th><th class="r">Checks</th><th>Last check</th><th>Search trend</th><th></th></tr></thead><tbody>
      ${rows.map(k => `<tr><td><a class="title" style="cursor:pointer" data-rep="${esc(k.markets[0] || 'AU')}|${esc(k.display || k.kw_norm)}"><b>${esc(k.display || k.kw_norm)}</b></a></td>
        <td><div class="row" style="gap:4px">${k.markets.map(m => `<button class="pill line" data-rep="${esc(m)}|${esc(k.display || k.kw_norm)}" title="Open the ${esc(MK[m] || m)} report">${esc(MK_SHORT[m] || m)}</button>`).join('')}</div></td>
        <td class="r">${k.n}</td><td>${ago(k.last_ts)}</td>
        <td>${Object.entries(k.trends || {}).map(([g, t]) => `<span class="pill weak" title="${esc(MK[g] || g)}">${esc(MK_SHORT[g] || g)}: ${esc(t.label || '–')}</span>`).join(' ') || '<span class="muted small">–</span>'}</td>
        <td><button class="btn slim" data-rep="${esc(k.markets[0] || 'AU')}|${esc(k.display || k.kw_norm)}">${icon('chart')}Report</button></td></tr>`).join('')
        || '<tr><td colspan="6" class="empty">No keyword matches the filter.</td></tr>'}
      </tbody></table></div>`;
  }
  $('libKeywords').addEventListener('click', e => {
    const b = e.target.closest('[data-rep]'); if (!b) return;
    const [m, ...kw] = b.dataset.rep.split('|');
    go('k', m, kw.join('|'));
  });

  function renderImports() {
    if (!lib) return;
    const KIND = {xray: 'Xray', blackbox: 'Black Box', magnet: 'Magnet', cerebro: 'Cerebro'};
    $('impList').innerHTML = lib.imports.length ? `<div class="tablewrap"><table><thead><tr><th>Type</th><th>Market</th><th>Keyword</th><th>File</th><th>Month</th><th class="r">Rows</th><th>Imported</th></tr></thead><tbody>
      ${lib.imports.map(i => `<tr><td>${esc(KIND[i.kind] || i.kind)}</td><td>${esc(MK_SHORT[i.market] || i.market)}</td>
        <td>${i.kw_norm ? `<a style="cursor:pointer" data-rep="${esc(i.market)}|${esc(i.kw_norm)}">${esc(i.kw_norm)}</a>` : '<span class="muted">–</span>'}</td>
        <td class="small">${esc(i.file)}</td><td>${esc(i.month)}</td><td class="r">${num(i.n_rows)}</td><td>${ago(i.imported_at)}</td></tr>`).join('')}
      </tbody></table></div>` : '<div class="panel empty"><h2>No Helium 10 files yet</h2><p>Export Xray, Black Box, Magnet or Cerebro to CSV. Files in your Downloads folder are imported when the app starts, or press Scan Downloads now.</p></div>';
  }
  $('impList').addEventListener('click', e => {
    const b = e.target.closest('[data-rep]'); if (!b) return;
    const [m, ...kw] = b.dataset.rep.split('|');
    go('k', m, kw.join('|'));
  });

  $('btnScan').addEventListener('click', async () => {
    $('btnScan').disabled = true;
    $('impProg').innerHTML = busy('Looking for Helium 10 exports…');
    try {
      const {job} = await api('/api/import/scan', {});
      const res = await waitJob(job, line => { $('impProg').innerHTML = busy(line); });
      const n = res.filter(r => r.status === 'imported').length, bad = res.filter(r => r.status === 'error');
      $('impProg').innerHTML = `<span class="pill ${n ? 'good' : 'weak'}">${icon(n ? 'ok' : 'dot')}${n} new file${n === 1 ? '' : 's'} imported</span>` +
        (bad.length ? ` <span class="pill bad" title="${esc(bad.map(b => b.file + ': ' + b.error).join('\n'))}">${bad.length} could not be read</span>` : '');
      show('imports');
    } catch (e) { $('impProg').innerHTML = failPill(e.message); }
    $('btnScan').disabled = false;
  });
  $('impFile').addEventListener('change', async () => {
    const f = $('impFile').files[0]; if (!f) return;
    $('impProg').innerHTML = busy('Importing ' + f.name + '…');
    try {
      const r = await api('/api/import/upload?name=' + encodeURIComponent(f.name), f, true);
      $('impProg').innerHTML = `<span class="pill ${r.status === 'imported' ? 'good' : 'weak'}">${esc(f.name)}: ${esc(r.status)}</span>`;
      show('imports');
    } catch (e) { $('impProg').innerHTML = failPill(e.message); }
    $('impFile').value = '';
  });
})();
