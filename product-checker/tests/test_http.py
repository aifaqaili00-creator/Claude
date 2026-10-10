"""The local server: static files, MIME types, token / Host / Origin checks, settings validation, jobs."""
import http.client
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('appdir')
    saved = (app.SETTINGS_FILE, app.HISTORY_FILE, app.PORT_FILE, app.DB_FILE)
    app.SETTINGS_FILE, app.HISTORY_FILE, app.PORT_FILE = tmp / 'settings.json', tmp / 'history.json', tmp / 'port.txt'
    app.DB_FILE = tmp / 'market.db'
    (tmp / 'settings.json').write_text(json.dumps({'pages': 9, 'cache_hours': 'abc', 'theme': 'dark', 'old_key': 1}))
    app.S = app.State(start_loop=False)
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
    app.S.port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield app.S
    httpd.shutdown()
    app.S.db.close()
    app.SETTINGS_FILE, app.HISTORY_FILE, app.PORT_FILE, app.DB_FILE = saved


def call(S, method, path, body=None, token=True, host=None, origin=None, headers=None):
    c = http.client.HTTPConnection('127.0.0.1', S.port, timeout=5)
    h = {'Host': host or '127.0.0.1:%d' % S.port}
    if token:
        h['X-PC-Token'] = S.token
    if origin:
        h['Origin'] = origin
    if body is not None:
        body = json.dumps(body).encode()
        h['Content-Type'] = 'application/json'
    h.update(headers or {})
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, r.getheader('Content-Type') or '', data


def test_old_settings_are_migrated_and_clamped(server):
    assert server.settings['pages'] == 3
    assert server.settings['cache_hours'] == 6
    assert server.settings['theme'] == 'dark'
    assert 'old_key' not in server.settings
    assert server.settings['locations']['AU'] == '2000'


def test_index_has_the_token_and_assets_have_fixed_types(server):
    st, ct, body = call(server, 'GET', '/', token=False)
    assert st == 200 and ct.startswith('text/html')
    assert server.token.encode() in body and b'%%PC_TOKEN%%' not in body
    for path, kind in (('/ui/js/app.js', 'text/javascript'), ('/ui/js/legacy.js', 'text/javascript'),
                       ('/ui/css/tokens.css', 'text/css'), ('/ui/css/app.css', 'text/css'), ('/favicon.svg', 'image/svg+xml')):
        st, ct, _ = call(server, 'GET', path, token=False)
        assert st == 200 and ct.startswith(kind), path


def test_every_script_and_stylesheet_in_index_exists():
    import re
    html = (app.UI_DIR / 'index.html').read_text(encoding='utf-8')
    refs = re.findall(r'(?:src|href)="/ui/([^"]+)"', html)
    assert refs
    for ref in refs:
        assert (app.UI_DIR / ref).is_file(), ref


def test_static_files_cannot_escape_the_ui_folder(server):
    for path in ('/ui/../app.py', '/ui/%2e%2e/app.py', '/ui/..%2fapp.py', '/ui/js/../../config.py', '/app.py', '/ui/'):
        st, _, body = call(server, 'GET', path, token=False)
        assert st == 404, path
        assert b'import' not in body


def test_api_needs_the_token(server):
    assert call(server, 'GET', '/api/state', token=False)[0] == 403
    st, _, body = call(server, 'GET', '/api/state')
    assert st == 200 and json.loads(body)['version'] == app.VERSION
    assert call(server, 'GET', '/api/state', headers={'X-PC-Token': 'wrong'}, token=False)[0] == 403
    assert call(server, 'POST', '/api/quit', body={}, token=False)[0] == 403


def test_other_hosts_and_origins_are_refused(server):
    assert call(server, 'GET', '/', host='evil.example:%d' % server.port, token=False)[0] == 403
    assert call(server, 'GET', '/api/state', host='evil.example')[0] == 403
    assert call(server, 'POST', '/api/settings', body={'pages': 2}, origin='https://evil.example')[0] == 403
    assert call(server, 'POST', '/api/settings', body={'pages': 2}, origin='http://127.0.0.1:%d' % server.port)[0] == 200
    assert call(server, 'GET', '/api/state', host='localhost:%d' % server.port)[0] == 200


def test_ping_works_without_the_token(server):
    st, _, body = call(server, 'GET', '/api/ping', token=False)
    assert st == 200 and json.loads(body)['ok'] is True


def test_settings_are_validated(server):
    st, _, body = call(server, 'POST', '/api/settings', body={'theme': 'pink'})
    assert st == 400 and b'theme' in body
    st, _, body = call(server, 'POST', '/api/settings', body={'fast_days': 99, 'theme': 'system', 'locations': {'AU': ' 3000 '}})
    s = json.loads(body)['settings']
    assert st == 200 and s['fast_days'] == 10 and s['theme'] == 'system' and s['locations']['AU'] == '3000'
    assert json.loads(app.SETTINGS_FILE.read_text())['theme'] == 'system'
    st, _, body = call(server, 'POST', '/api/settings', body={'fast_days': 'soon'})
    assert st == 400
    call(server, 'POST', '/api/settings', body={'locations': {'AU': '2000'}, 'fast_days': 3})


def test_unknown_routes_jobs_and_actions(server):
    assert call(server, 'GET', '/api/nope')[0] == 404
    assert call(server, 'GET', '/api/job?id=missing')[0] == 410
    assert call(server, 'POST', '/api/browser', body={'action': 'explode'})[0] == 400
    assert call(server, 'POST', '/api/export', body={'kind': 'check', 'job': 'missing'})[0] == 410
    assert call(server, 'POST', '/api/open', body={'url': 'http://example.com'})[0] == 400
    assert call(server, 'POST', '/api/check', body={'keyword': '   '})[0] == 400


def test_job_manager_prunes_old_and_extra_jobs():
    import concurrent.futures as cf
    import jobs

    def run(coro):
        coro.close()
        f = cf.Future()
        f.set_result({'ok': 1})
        return f

    async def work(progress):
        return 1
    jm = jobs.JobManager(run)
    ids = [jm.start('t', lambda p: work(p)) for _ in range(jobs.MAX_JOBS + 5)]
    assert jm.get(ids[-1])['status'] == 'done'
    jm.prune()
    assert len(jm.jobs) <= jobs.MAX_JOBS and jm.get(ids[-1])
    jm.prune(now=jm.get(ids[-1])['finished'] + jobs.KEEP_SECONDS + 1)
    assert not jm.jobs


def test_failed_job_reports_its_error():
    import concurrent.futures as cf
    import jobs
    said = []

    def run(coro):
        coro.close()
        f = cf.Future()
        f.set_exception(RuntimeError('Amazon asked for a captcha\nmore'))
        return f

    async def work(progress):
        return 1
    jm = jobs.JobManager(run, say=said.append)
    j = jm.get(jm.start('check', lambda p: work(p)))
    assert j['status'] == 'error' and 'captcha' in j['error'] and said and 'Check failed' in said[0]
