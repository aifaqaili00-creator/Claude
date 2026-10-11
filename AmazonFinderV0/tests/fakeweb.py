"""A small fake of the outside world for tests: Google Trends API and Amazon pages.

POST /__mode with {"trends": "...", "amazon": "..."} switches scenarios:
  trends: ok | lowvol | 429 | sorry | empty | scraper
  amazon: ok | captcha | dog | robot | empty | noresults | geo
"""
import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

START = 1_633_219_200          # Sunday 3 Oct 2021, 00:00 UTC
WEEK = 7 * 86400
MODE = {'trends': 'ok', 'amazon': 'ok'}
HITS = []


def weekly(kind='growing', weeks=261):
    out = []
    for i in range(weeks):
        if kind == 'seasonal':
            v = 50 + 40 * math.cos(2 * math.pi * (i / 52.18 - 0.78))          # peaks mid-July
        elif kind == 'flat':
            v = 60
        else:
            v = 20 + 75 * i / weeks
        out.append({'time': str(START + i * WEEK), 'formattedTime': '', 'value': [round(v)], 'hasData': [True],
                    'formattedValue': [str(round(v))], **({'isPartial': True} if i == weeks - 1 else {})})
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype='application/json', headers=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        MODE.update(json.loads(self.rfile.read(n) or b'{}'))
        self._send(200, '{"ok": true}')

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        HITS.append(u.path)
        if u.path.startswith('/trends'):
            return self.trends(u.path, q)
        return self._send(404, 'not found', 'text/plain')

    def trends(self, path, q):
        mode = MODE['trends']
        if path == '/trends/explore':
            return self._send(200, '<html><body>Trends</body></html>', 'text/html')
        if path.startswith('/sorry'):
            return self._send(200, '<html>unusual traffic from your computer network</html>', 'text/html')
        if mode == '429':
            return self._send(429, 'Too many requests', 'text/plain')
        if mode == 'sorry':
            return self._send(302, '', 'text/plain', {'Location': '/sorry/index?continue=x'})
        if path == '/trends/api/explore':
            req = json.loads(q.get('req') or '{}')
            item = (req.get('comparisonItem') or [{}])[0]
            user = 'USER_TYPE_SCRAPER' if mode == 'scraper' else 'USER_TYPE_LEGIT_USER'
            widgets = [{'id': 'TIMESERIES', 'token': 'T1',
                        'request': {'time': item.get('time'), 'keyword': item.get('keyword'),
                                    'geo': item.get('geo'), 'userConfig': {'userType': user}}},
                       {'id': 'RELATED_QUERIES', 'token': 'T2', 'request': {'keyword': item.get('keyword')}}]
            return self._send(200, ")]}'\n" + json.dumps({'widgets': widgets}))
        if path == '/trends/api/widgetdata/multiline':
            req = json.loads(q.get('req') or '{}')
            kw = req.get('keyword') or ''
            if mode == 'empty':
                return self._send(200, ")]}',\n" + json.dumps({'default': {'timelineData': []}}))
            if mode == 'lowvol':
                tl = [{**p, 'value': [0], 'hasData': [False], 'formattedValue': ['0']} for p in weekly('flat')]
            else:
                tl = weekly('seasonal' if 'fan' in kw else 'flat' if 'flat' in kw else 'growing')
            return self._send(200, ")]}',\n" + json.dumps({'default': {'timelineData': tl}}))
        if path == '/trends/api/widgetdata/relatedsearches':
            data = {'default': {'rankedList': [
                {'rankedKeyword': [{'query': 'best tower fan', 'value': 100, 'formattedValue': '100'}]},
                {'rankedKeyword': [{'query': 'bladeless tower fan', 'value': 4500, 'formattedValue': 'Breakout'},
                                   {'query': 'quiet tower fan', 'value': 140, 'formattedValue': '+140%'}]}]}}
            return self._send(200, ")]}',\n" + json.dumps(data))
        return self._send(404, 'not found', 'text/plain')


def start(port=0):
    """Start in a background thread. Returns (server, base_url)."""
    srv = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, 'http://127.0.0.1:%d' % srv.server_address[1]
