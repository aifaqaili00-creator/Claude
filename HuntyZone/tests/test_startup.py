"""Starting the app while a copy is already running: it is found, and an older version is asked to quit."""
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402


def test_a_running_copy_is_found_when_its_reply_comes_in_pieces(tmp_path, monkeypatch):
    srv = socket.socket()
    srv.bind(('127.0.0.1', 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def answer():
        conn, _ = srv.accept()
        with conn:
            conn.recv(4096)
            conn.sendall(b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n')
            time.sleep(0.2)                                     # the body arrives after the headers
            conn.sendall(b'{"ok": true, "version": "6.4"}')

    t = threading.Thread(target=answer, daemon=True)
    t.start()
    (tmp_path / 'port.txt').write_text(str(port))
    monkeypatch.setattr(app, 'PORT_FILE', tmp_path / 'port.txt')
    try:
        assert app.already_running() == (port, '6.4')
    finally:
        t.join(5)
        srv.close()


def test_nothing_running(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'PORT_FILE', tmp_path / 'port.txt')
    assert app.already_running() == (None, None)                # never started
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    (tmp_path / 'port.txt').write_text(str(s.getsockname()[1]))
    s.close()
    assert app.already_running() == (None, None)                # started once, now closed


def test_versions_compare_as_numbers():
    key = app._version_key
    assert key('7.0') > key('6.4')
    assert key('10.0') > key('9.9')
    assert key('6.4') < key('6.10')
    assert key(None) < key('6.4') and key('0') < key('6.4')
    assert not key(app.VERSION) < key(app.VERSION)              # this copy never replaces itself
    assert not key(app.VERSION + '.1') < key(app.VERSION)       # ... or a newer one


def test_an_older_copy_is_asked_to_quit_with_its_own_token(tmp_path, monkeypatch):
    seen = {}

    class Old(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, body, kind='application/json'):
            data = body.encode()
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == '/api/ping':
                self.reply(json.dumps({'ok': True, 'version': '6.4'}))
            else:
                self.reply('<meta name="pc-token" content="tok-123">', 'text/html')

        def do_POST(self):
            seen['path'], seen['token'] = self.path, self.headers.get('X-PC-Token')
            self.reply('{"ok": true}')
            threading.Thread(target=lambda: (httpd.shutdown(), httpd.server_close()), daemon=True).start()

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Old)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    (tmp_path / 'port.txt').write_text(str(port))
    monkeypatch.setattr(app, 'PORT_FILE', tmp_path / 'port.txt')
    assert app.already_running() == (port, '6.4')
    assert app.replace_older(port, timeout=15) is True
    assert seen == {'path': '/api/quit', 'token': 'tok-123'}
    assert app.already_running() == (None, None)
