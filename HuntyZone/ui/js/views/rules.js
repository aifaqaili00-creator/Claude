'use strict';
/* Settings: Go or no-go rules. The limits of each deal-breaker and warning per market, on or off, saved as you type. */
(() => {
  const PCT = ['margin_floor', 'amazon_sells', 'one_season'];
  const LISTS = {restricted: null, searches_falling: ['Declining', 'Fad', 'Spike (watch)', 'Seasonal', 'Mixed']};
  let R = null;

  async function load() {
    try { R = await api('/api/decide/rules'); } catch (e) { $('rulesOut').innerHTML = failPill(e.message); return; }
    LISTS.restricted = R.restricted;
    draw();
  }
  function cell(key, mk, v) {
    if (key === 'price_band') return `<span class="pair"><input type="number" min="0" step="1" data-k="${key}" data-mk="${mk}" data-i="0" value="${v[0]}" aria-label="${esc(MK[mk])} lowest price">\u2013<input type="number" min="0" step="1" data-k="${key}" data-mk="${mk}" data-i="1" value="${v[1]}" aria-label="${esc(MK[mk])} highest price"></span>`;
    if (PCT.includes(key)) return `<span class="pair"><input type="number" min="0" max="100" step="1" data-k="${key}" data-mk="${mk}" value="${Math.round(v * 100)}" aria-label="${esc(MK[mk])} limit in percent">%</span>`;
    return `<input type="number" min="0" step="1" data-k="${key}" data-mk="${mk}" value="${v}" aria-label="${esc(MK[mk])} limit">`;
  }
  function draw() {
    const mks = R.markets;
    $('rulesOut').innerHTML = Object.entries(R.rules).map(([k, r]) => `<div class="rrule${r.on ? '' : ' off'}">
        <label class="small"><input type="checkbox" data-on="${k}" ${r.on ? 'checked' : ''}> <b>${esc(r.name)}</b></label>
        <span class="small muted">${r.kind === 'hard' ? 'deal-breaker' : 'warning'}</span>
        ${LISTS[k] !== undefined ? `<div class="checks">${(LISTS[k] || []).map(o => `<label class="small"><input type="checkbox" data-list="${k}" value="${esc(o)}" ${r.limit[mks[0]].includes(o) ? 'checked' : ''}> ${esc(o)}</label>`).join('')}</div>
          <div class="sub">${k === 'restricted' ? 'Ticked words in the top titles stop a product; the others are only mentioned.' : 'Ticked Google Trends labels count as falling searches.'}</div>`
          : `<div class="rlims">${mks.map(m => `<span class="rlim">${mkFlag(m, true)} ${cell(k, m, r.limit[m])}</span>`).join('')}</div>`}
        <div class="sub">${esc(r.means)}</div></div>`).join('');
  }
  async function save(rules) {
    try { R.rules = (await api('/api/decide/rules', {rules})).rules; toast('Saved', 1500); }
    catch (e) { toast(e.message); }
    draw();
  }
  $('rulesOut').addEventListener('change', e => {
    const el = e.target;
    if (el.dataset.on) return save({[el.dataset.on]: {on: el.checked}});
    if (el.dataset.list) {
      const k = el.dataset.list, picked = [...$('rulesOut').querySelectorAll(`[data-list="${k}"]`)].filter(x => x.checked).map(x => x.value);
      return save({[k]: {limit: Object.fromEntries(R.markets.map(m => [m, picked]))}});
    }
    const k = el.dataset.k, mk = el.dataset.mk;
    if (!k || el.value === '') return;
    let v = +el.value;
    if (k === 'price_band') { v = [...R.rules[k].limit[mk]]; v[+el.dataset.i] = +el.value; }
    else if (PCT.includes(k)) v = v / 100;
    return save({[k]: {limit: {[mk]: v}}});
  });
  $('rulesReset').addEventListener('click', async () => {
    if (!confirm('Use the built-in rules again? Your limits are forgotten.')) return;
    try { R.rules = (await api('/api/decide/rules', {reset: true})).rules; toast('Built-in rules again', 2000); draw(); }
    catch (e) { toast(e.message); }
  });
  window.addEventListener('hashchange', () => { if (/^#\/settings/.test(location.hash)) load(); });
  if (/^#\/settings/.test(location.hash)) load();
})();
