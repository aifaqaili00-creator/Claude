'use strict';
/* Ask AI (optional): questions about your saved research and board, answered by Claude with read-only tools.
   Also binds the Settings AI panel (the key, the model, the monthly limit) and shows the MCP connector setup. */
(() => {
  const EXAMPLES = ['Which gaps in Australia have the fewest sellers and the best proof?', 'What is the price band and the reviews to beat for my newest keyword?',
    'Which products on my board need a re-order soon?', 'Who sells the most in the UAE among the sellers read?'];
  const NAMES = {list_keywords: 'Keywords searched', search_products: 'Products read', get_product: 'One product', keyword_report: 'Keyword report',
    find_gaps: 'Gap Finder', lookup_seller: 'Sellers', lookup_brand: 'Brands', board_status: 'Product board', live_stock: 'Stock and sales', recent_alerts: 'Home alerts'};
  let A = null, last = null, busyNow = false;
  view('ai', {title: 'Ask AI', sub: 'Questions about your research and your board, answered from your saved data', onShow: () => load()});

  const money = v => 'US$' + (v < 0.1 && v > 0 ? v.toFixed(3) : v.toFixed(2));
  const useLine = s => `This month: ${money(s.used.usd)} of your ${money(s.cap_usd)} limit \u00b7 ${num(s.used.calls)} call${s.used.calls === 1 ? '' : 's'}` +
    (s.priced ? '' : ' (this model is priced like Sonnet: an estimate)');
  async function load() {
    try { A = await api('/api/ai'); } catch (e) { $('aiOut').innerHTML = failPill(e.message); return; }
    draw();
  }
  function draw() {
    if (!A.on) {
      $('aiOut').innerHTML = `<div class="panel empty"><h2>Ask AI is off</h2>
        <p>Save an Anthropic API key in <a href="#/settings">Settings, AI</a> to ask questions here. Or use your own Claude Desktop with the
        <b>MCP connector</b> (no key needed in this app): the setup is in the same Settings panel.</p></div>`;
      return;
    }
    $('aiOut').innerHTML = `<div class="panel stackv">
        <label class="lbl" for="aiQ">Your question</label>
        <textarea id="aiQ" rows="3" maxlength="2000" placeholder="For example: ${esc(EXAMPLES[0])}"></textarea>
        <div class="row wrap aiex">${EXAMPLES.map((x, i) => `<button type="button" class="btn slim ghost" data-ex="${i}">${esc(x)}</button>`).join('')}</div>
        <div class="row"><button type="button" class="btn primary" id="aiAsk">${icon('spark')}Ask</button><span class="small muted" id="aiProg" aria-live="polite"></span>
          <span class="small muted" style="margin-left:auto">${esc(useLine(A))} \u00b7 model ${esc(A.model)}</span></div>
        ${explain('Claude reads only what this app saved (it cannot open Amazon, click, email or buy), says where each number comes from, and calls something not read a gap, never zero. Check important numbers in the report it names.')}
      </div>
      <div id="aiAns"></div>`;
    if (last) answer(last);
  }
  function answer(r) {
    $('aiAns').innerHTML = `<div class="panel stackv aians">
        <div class="aitext"></div>
        ${r.stopped ? `<div class="note small">${esc(r.stopped)}</div>` : ''}
        <div class="small muted">Read: ${r.tools.length ? r.tools.map(t => `<span class="pill line" title="${esc(JSON.stringify(t.input))}">${esc(NAMES[t.name] || t.name)}${t.error ? ' (' + esc(t.error) + ')' : ''}</span>`).join(' ') : 'nothing (answered without your data)'}
          \u00b7 this question cost about ${money(r.usd)}</div></div>`;
    $('aiAns').querySelector('.aitext').textContent = r.answer;
  }
  async function ask() {
    const q = $('aiQ').value.trim();
    if (q.length < 3) { $('aiQ').focus(); return; }
    if (busyNow) return;
    busyNow = true; $('aiAsk').disabled = true; $('aiProg').innerHTML = busy('Thinking\u2026');
    try {
      const {job} = await api('/api/ai/ask', {question: q});
      const r = await waitJob(job, line => { if ($('aiProg')) $('aiProg').innerHTML = busy(line); }, {timeoutMs: 5 * 60 * 1000});
      last = r; A = {...A, ...r.status};
      draw(); $('aiQ').value = q;
    } catch (e) { toast(e.message, 8000); }
    busyNow = false;
    if ($('aiAsk')) $('aiAsk').disabled = false;
    if ($('aiProg')) $('aiProg').innerHTML = '';
  }
  $('aiOut').addEventListener('click', e => {
    const ex = e.target.closest('[data-ex]');
    if (ex) { $('aiQ').value = EXAMPLES[+ex.dataset.ex]; $('aiQ').focus(); return; }
    if (e.target.closest('#aiAsk')) ask();
  });
  $('aiOut').addEventListener('keydown', e => { if (e.target.id === 'aiQ' && e.key === 'Enter' && (e.ctrlKey || e.metaKey)) ask(); });

  /* Settings: AI panel. The key is never sent back to the page. */
  let filled = false;
  onState(() => {
    const s = ST.settings || {};
    if (filled || s.ai_model === undefined) return;
    filled = true;
    document.querySelectorAll('#aiPanel [data-set]').forEach(el => { const v = s[el.dataset.set]; el.value = v == null ? '' : v; });
  });
  $('aiPanel').addEventListener('change', async e => {
    const el = e.target.closest('[data-set]'); if (!el) return;
    try { const r = await api('/api/settings', {[el.dataset.set]: el.value}); ST.settings = r.settings; toast('Saved', 1500); panelState(); }
    catch (err) { toast(err.message); filled = false; }
  });
  async function panelState() {
    try {
      const s = await api('/api/ai');
      $('aiState').innerHTML = s.on ? `<span class="pill good">${icon('lock')}Key saved</span> ${esc(useLine(s))}` : '<span class="pill weak">Off: no key saved</span>';
      $('aiForget').disabled = !s.on;
      $('mcpFile').textContent = s.mcp.file;
      $('mcpCfg').textContent = s.mcp.config;
    } catch (e) { $('aiState').innerHTML = failPill(e.message); }
  }
  window.addEventListener('hashchange', () => { if (/^#\/settings/.test(location.hash)) panelState(); });
  if (/^#\/settings/.test(location.hash)) panelState();
  $('aiSave').addEventListener('click', async () => {
    const key = $('aiKey').value.trim();
    if (!key) { $('aiKey').focus(); return; }
    try { await api('/api/ai', {key}); $('aiKey').value = ''; toast('Key saved, encrypted for your Windows user', 3000); panelState(); }
    catch (err) { toast(err.message); }
  });
  $('aiForget').addEventListener('click', async () => {
    if (!confirm('Forget the AI key? Ask AI turns off.')) return;
    try { await api('/api/ai', {forget: true}); toast('Key forgotten', 2000); panelState(); } catch (err) { toast(err.message); }
  });
  $('mcpCopy').addEventListener('click', () => navigator.clipboard.writeText($('mcpCfg').textContent)
    .then(() => toast('Copied', 1500), () => toast('Select the text and copy it')));
})();
