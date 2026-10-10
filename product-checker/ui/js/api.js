'use strict';
/* The one way to talk to the local server. Every request carries this run's token, downloads included. */
const PC = window.PC = window.PC || {};
PC.token = (document.querySelector('meta[name="pc-token"]') || {}).content || '';

class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

PC.request = async function (path, {method = 'GET', body, raw = false, timeout = 0} = {}) {
  const headers = {'X-PC-Token': PC.token};
  let payload;
  if (body !== undefined) {
    if (raw) payload = body;
    else { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
  }
  const ctl = timeout ? new AbortController() : null;
  const timer = ctl && setTimeout(() => ctl.abort(), timeout);
  let r;
  try {
    r = await fetch(path, {method, headers, body: payload, cache: 'no-store', signal: ctl ? ctl.signal : undefined});
  } catch (e) {
    throw new ApiError(e.name === 'AbortError' ? 'The app did not answer in time.' : 'The app is not running. Start it again with start.bat.', 0);
  } finally { if (timer) clearTimeout(timer); }
  if (r.status === 403) PC.staleToken();
  return r;
};

/* The app was restarted, so this window holds an old token: reload once to pick up the new one. */
PC.staleToken = function () {
  try {
    const last = +sessionStorage.getItem('pcReload') || 0;
    if (Date.now() - last < 15000) return;
    sessionStorage.setItem('pcReload', String(Date.now()));
  } catch (e) { return; }
  location.reload();
};

/* GET when body is undefined, otherwise POST (JSON, or raw bytes for file uploads). */
async function api(path, body, raw) {
  const r = await PC.request(path, body === undefined ? {} : {method: 'POST', body, raw});
  const ct = r.headers.get('Content-Type') || '';
  const data = ct.includes('json') ? await r.json().catch(() => ({})) : await r.blob();
  if (!r.ok) throw new ApiError((data && data.error) || ('Error ' + r.status), r.status);
  return data;
}

/* Poll a background job until it finishes. A job that expired (410) or a run that takes too long ends with a clear message. */
async function waitJob(id, onLine, {timeoutMs = 30 * 60 * 1000} = {}) {
  const until = Date.now() + timeoutMs;
  let misses = 0;
  for (;;) {
    await new Promise(r => setTimeout(r, 600));
    if (Date.now() > until) throw new ApiError('This is taking very long. It may still finish; try again in a few minutes.', 0);
    let j;
    try { j = await api('/api/job?id=' + encodeURIComponent(id)); misses = 0; }
    catch (e) {
      if (e.status === 410 || ++misses > 8) throw e;
      continue;
    }
    if (j.log.length && onLine) onLine(j.log[j.log.length - 1]);
    if (j.status === 'done') return j.result;
    if (j.status === 'error') throw new ApiError(j.error || 'Failed', 500);
  }
}

/* Ask the server for an Excel file and save it. */
async function download(body) {
  try {
    const r = await PC.request('/api/export', {method: 'POST', body});
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.error || 'Could not save the file');
    }
    const name = ((r.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/) || [])[1] || 'export.xlsx';
    const a = document.createElement('a');
    a.href = URL.createObjectURL(await r.blob());
    a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 30000);
    toast('Saved ' + name + ' to your Downloads folder');
  } catch (e) { toast(e.message); }
}

function openUrl(url) { api('/api/open', {url}).catch(e => toast(e.message)); }
