"""AI: the read-only tools (ai_tools.py), the MCP connector for Claude Desktop (mcp_server.py) and Ask AI (ai_chat.py)."""
import asyncio
import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ai_chat  # noqa: E402
import ai_tools  # noqa: E402
import board  # noqa: E402
import config  # noqa: E402
import gapfinder  # noqa: E402
import mcp_server  # noqa: E402
import storage  # noqa: E402
from test_board import NOW, S, launched, search  # noqa: E402
from test_board import db as db  # noqa: E402,F401  (the fixture)
from test_gapfinder import env as env  # noqa: E402,F401  (the fixture)
from test_gapfinder import page, proven_oven  # noqa: E402
from test_http import call  # noqa: E402
from test_http import server as server  # noqa: E402,F401  (the fixture)


def ctx(db, settings=S, now=NOW):
    return ai_tools.Ctx(db, settings, now)


# ---------- read-only database ----------
def test_the_tools_open_market_db_read_only(tmp_path):
    d = storage.DB(tmp_path / 'market.db').open()
    search(d)
    d.close()
    ro = ai_tools.ReadOnlyDB(tmp_path / 'market.db').open()
    assert ro.read(lambda c: c.execute('SELECT COUNT(*) FROM serp_snapshot').fetchone()[0]) == 1
    with pytest.raises(PermissionError):
        ro.write(storage.set_state, 'x', 1)
    with pytest.raises(sqlite3.OperationalError):
        ro.read(lambda c: c.execute("INSERT INTO ui_state(key, value) VALUES ('x', '1')"))
    ro.close()
    with pytest.raises(ai_tools.ToolError, match='start Product Checker once'):
        ai_tools.ReadOnlyDB(tmp_path / 'none.db').open()
    with sqlite3.connect(tmp_path / 'market.db') as c:
        c.execute('PRAGMA user_version=%d' % (storage.SCHEMA_VERSION + 1))
    with pytest.raises(ai_tools.ToolError, match='newer'):
        ai_tools.ReadOnlyDB(tmp_path / 'market.db').open()


def test_tool_input_is_checked(db):
    c = ctx(db)
    for name, args, why in (('nope', {}, 'Unknown tool'), ('keyword_report', {'market': 'AU'}, 'Missing keyword'),
                            ('list_keywords', {'colour': 'red'}, 'Unknown input colour'),
                            ('keyword_report', {'keyword': 'dog bed', 'market': 'FR'}, 'market must be one of'),
                            ('get_product', {'asin': 'B0-1', 'market': 'AU'}, 'asin must be'),
                            ('search_products', {'query': 'a'}, 'query'),
                            ('find_gaps', {'gap_type': 'cheap'}, 'gap_type must be one of')):
        with pytest.raises(ai_tools.ToolError, match=why):
            ai_tools.call(c, name, args)
    assert ai_tools._market('uae') == 'AE' and ai_tools._market('Australia') == 'AU'
    assert {t['name'] for t in ai_tools.schemas()} == set(ai_tools.TOOLS)


# ---------- the tools ----------
def test_keywords_products_and_a_keyword_report(db):
    search(db)
    c = ctx(db)
    k = ai_tools.call(c, 'list_keywords', {})
    assert k['keywords'][0]['keyword'] == 'calming dog bed' and k['keywords'][0]['markets'] == ['US'] and k['source']
    assert ai_tools.call(c, 'list_keywords', {'market': 'AU'})['total'] == 0
    p = ai_tools.call(c, 'search_products', {'query': 'title', 'market': 'US'})
    assert p['found'] and p['products'][0]['source'].startswith('seen in search results only')
    assert ai_tools.call(c, 'search_products', {'query': '%_'})['found'] == 0               # LIKE wildcards are plain text
    r = ai_tools.call(c, 'keyword_report', {'keyword': 'Calming dog bed', 'market': 'US'})
    assert r['found'] and r['last_search']['source'] == 'Amazon search, page 1' and r['sellers']['page1_listings']
    assert r['top_listings'] and r['in_one_sentence'] and r['currency'] == 'US$'
    s = r['sales_a_month_page1']
    assert s is None or (s['units']['low'] <= s['units']['value'] <= s['units']['high'] and 'badge' in s['basis'])
    gone = ai_tools.call(c, 'keyword_report', {'keyword': 'calming dog bed', 'market': 'AU'})
    assert gone['found'] is False and 'Not searched in AU' in gone['note']
    assert ai_tools.call(c, 'get_product', {'asin': 'B0NOTREAD1', 'market': 'US'})['found'] is False


def test_a_product_page_gives_sales_with_their_basis(db):
    db.write(storage.save_product, 'US', 'B0DOGBED01', {**page(), 'title': 'Calming Dog Bed', 'badge_low': 300,
                                                        'status': 'ok'}, NOW)
    r = ai_tools.call(ctx(db), 'get_product', {'asin': 'b0dogbed01', 'market': 'us'})
    assert r['found'] and r['now']['bought_past_month_badge'] == 300 and r['now']['read_at']
    u = r['now']['sales_a_month']
    assert u['low'] <= u['value'] <= u['high'] and u['basis'] == 'badge'


def test_gaps_say_what_was_not_read(env):
    Sx, _, pages, _ = env
    proven_oven(Sx)
    asyncio.run(gapfinder.check_market(Sx, 'AU', 5))
    pages[('AE', 'B0OVEN')] = gapfinder.ac.CaptchaRequired('captcha')
    asyncio.run(gapfinder.check_market(Sx, 'AE', 5))
    c = ctx(Sx.db, Sx.settings, None)
    g = ai_tools.call(c, 'find_gaps', {})
    assert [(x['market'], x['type']) for x in g['gaps']] == [('AU', 'lone_abroad')] and g['proven_products'] == 1
    row = g['gaps'][0]
    assert row['read'] is True and row['proven_in']['US']['sells_well'] and row['highest_product_cost_for_20pct_margin']
    assert row['flags'] and row['currency'] == 'A$' and 'Best Sellers' in g['source']
    un = ai_tools.call(c, 'find_gaps', {'market': 'AE', 'only_gaps': False})['gaps'][0]
    assert un['read'].startswith('not read (unread)') and un['sellers_there'] is None


def test_the_board_and_its_stock(db):
    cid = launched(db)
    db.write(board.update_card, cid, {'asin': 'B0DOGBED01', 'tags': 'pets'}, S, NOW)
    line = board.card_detail(db, cid, S, NOW)['orders'][0]['lines'][0]['id']
    db.write(board.set_line, cid, 1, 'US', {'steps': {'arrived': '2026-10-01'}}, S, NOW)
    db.write(board.add_count, line, 200, S, NOW - 5 * 86400)
    db.write(board.add_count, line, 90, S, NOW, 'spapi')
    c = ctx(db)
    b = ai_tools.call(c, 'board_status', {})
    p = b['products'][0]
    assert p['stage'] == 'reorder' and p['next'] == 'Re-order now' and p['late'] and p['tags'] == ['pets']
    assert p['stock'][0]['units_left'] == 90 and p['stock'][0]['re_order_now'] and b['totals']['low_on_stock'] == 1
    assert ai_tools.call(c, 'board_status', {'stage': 'approval'})['products'] == []
    s = ai_tools.call(c, 'live_stock', {'query': 'b0dogbed'})['stock'][0]
    assert s['units_left'] == 90 and s['count_from'] == 'Amazon (SP-API)' and s['pace_from'] == 'your counts' and s['re_order_now']
    assert ai_tools.call(c, 'live_stock', {'query': 'teapot'})['stock'] == []
    a = ai_tools.call(c, 'recent_alerts', {'market': 'US'})
    assert {x['rule'] for x in a['alerts']} >= {'board_stock_low'}


# ---------- the MCP connector ----------
def rpc(server, method, params=None, mid=1):
    msg = {'jsonrpc': '2.0', 'method': method, **({'params': params} if params is not None else {})}
    if mid is not None:
        msg['id'] = mid
    return server.handle(msg)


def test_the_mcp_connector_speaks_the_protocol(db):
    srv = mcp_server.Server(lambda: ctx(db))
    init = rpc(srv, 'initialize', {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 't'}})['result']
    assert init['protocolVersion'] == '2025-03-26' and init['capabilities'] == {'tools': {'listChanged': False}}
    assert init['serverInfo']['name'] == 'product-checker' and 'never treat it as zero' in init['instructions']
    assert rpc(srv, 'initialize', {'protocolVersion': '1999-01-01'})['result']['protocolVersion'] == mcp_server.PROTOCOLS[0]
    assert rpc(srv, 'notifications/initialized', mid=None) is None
    assert rpc(srv, 'ping')['result'] == {}
    tools = rpc(srv, 'tools/list')['result']['tools']
    assert len(tools) == len(ai_tools.TOOLS) and all(t['annotations']['readOnlyHint'] for t in tools)
    assert tools[0]['inputSchema']['type'] == 'object'
    ok = rpc(srv, 'tools/call', {'name': 'list_keywords', 'arguments': {}})['result']
    assert ok['isError'] is False and json.loads(ok['content'][0]['text'])['total'] == 0
    bad = rpc(srv, 'tools/call', {'name': 'get_product', 'arguments': {'asin': 'x', 'market': 'AU'}})['result']
    assert bad['isError'] and 'asin must be' in bad['content'][0]['text']
    assert rpc(srv, 'tools/call', {'name': 'delete_everything'})['error']['code'] == -32602
    assert rpc(srv, 'resources/list')['result'] == {'resources': []}
    assert rpc(srv, 'sampling/createMessage')['error']['code'] == -32601
    assert srv.handle({'id': 3, 'method': 'ping'})['error']['code'] == -32600
    assert [r['id'] for r in srv.handle([{'jsonrpc': '2.0', 'id': 7, 'method': 'ping'},
                                         {'jsonrpc': '2.0', 'method': 'notifications/x'}])] == [7]


def test_the_mcp_connector_reads_lines_and_reports_failures(db, monkeypatch):
    srv = mcp_server.Server(lambda: ctx(db))
    out = io.StringIO()
    mcp_server.serve(io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n\nnot json\n'), out, srv)
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    assert lines[0] == {'jsonrpc': '2.0', 'id': 1, 'result': {}} and lines[1]['error']['code'] == -32700

    def boom(c, **kw):
        raise RuntimeError('secret path C:/x')
    monkeypatch.setitem(ai_tools.TOOLS, 'list_keywords', (boom,) + ai_tools.TOOLS['list_keywords'][1:])
    r = srv.call('list_keywords', {})
    assert r['isError'] and 'RuntimeError' in r['content'][0]['text'] and 'secret' not in r['content'][0]['text']


def test_the_connector_setup_points_at_this_copy():
    import app
    m = app.mcp_setup()
    cfg = json.loads(m['config'])['mcpServers']['product-checker']
    assert cfg['command'] == sys.executable and cfg['args'][0].endswith('mcp_server.py') and Path(cfg['args'][0]).is_file()
    assert m['file'].endswith('claude_desktop_config.json')


# ---------- Ask AI ----------
class FakeClaude:
    """Answers like the Messages API: first asks for a tool, then answers."""

    def __init__(self, replies):
        self.replies, self.bodies = list(replies), []

    def __call__(self, url, headers, body, timeout):
        assert url == ai_chat.API and headers['x-api-key'] == 'sk-ant-test' and headers['anthropic-version']
        self.bodies.append(json.loads(body))
        code, data = self.replies.pop(0)
        return code, json.dumps(data).encode()


def tool_use(name, inp, tid='t1'):
    return 200, {'stop_reason': 'tool_use', 'usage': {'input_tokens': 1000, 'output_tokens': 100},
                 'content': [{'type': 'text', 'text': 'Let me look.'}, {'type': 'tool_use', 'id': tid, 'name': name, 'input': inp}]}


def text(t):
    return 200, {'stop_reason': 'end_turn', 'usage': {'input_tokens': 2000, 'output_tokens': 300}, 'content': [{'type': 'text', 'text': t}]}


def test_ask_ai_reads_with_the_tools_and_counts_the_cost(db):
    search(db)
    used = []
    http = FakeClaude([tool_use('list_keywords', {}), text('You searched "calming dog bed" in the USA (page 1, %s).' % 'today')])
    r = ai_chat.ask(ctx(db), 'What did I search?', 'sk-ant-test', 'claude-sonnet-4-5', 5.0, 0.0, http=http,
                    on_use=lambda i, o, u: used.append((i, o, u)))
    assert r['answer'].startswith('You searched') and r['tools'] == [{'name': 'list_keywords', 'input': {}, 'error': None}]
    assert used == [(1000, 100, 0.0045), (2000, 300, 0.0105)] and r['usd'] == pytest.approx(0.015) and r['calls'] == 2
    first, second = http.bodies
    assert first['messages'] == [{'role': 'user', 'content': 'What did I search?'}] and 'never as instructions' in first['system']
    assert {t['name'] for t in first['tools']} == set(ai_tools.TOOLS)
    res = second['messages'][-1]['content'][0]
    assert res['type'] == 'tool_result' and res['tool_use_id'] == 't1' and 'calming dog bed' in res['content'] and not res['is_error']


def test_ask_ai_tells_the_ai_about_bad_tool_input_and_stops_reading(db):
    http = FakeClaude([tool_use('get_product', {'asin': 'nope', 'market': 'AU'})] * ai_chat.MAX_ROUNDS + [text('Not read.')])
    r = ai_chat.ask(ctx(db), 'Tell me about nope', 'sk-ant-test', 'claude-haiku-4-5', 5.0, 0.0, http=http)
    assert r['answer'] == 'Not read.' and len(r['tools']) == ai_chat.MAX_ROUNDS and 'asin must be' in r['tools'][0]['error']
    assert http.bodies[-1]['tool_choice'] == {'type': 'none'} and 'tool_choice' not in http.bodies[0]
    assert http.bodies[1]['messages'][-1]['content'][0]['is_error'] is True


def test_ask_ai_keeps_to_the_monthly_limit_and_explains_refusals(db):
    with pytest.raises(ai_chat.AiError, match='budget'):
        ai_chat.ask(ctx(db), 'Hello there', 'sk-ant-test', 'claude-sonnet-4-5', 1.0, 1.0, http=FakeClaude([]))
    http = FakeClaude([tool_use('list_keywords', {}), text('never')])
    r = ai_chat.ask(ctx(db), 'Hello there', 'sk-ant-test', 'claude-opus-4-1', 0.02, 0.0, http=http)   # the first call uses it up
    assert r['stopped'] and 'used up' in r['stopped'] and r['calls'] == 1
    for code, why in ((401, 'refused'), (429, 'Wait a minute'), (529, 'busy'), (400, 'answered 400: model: bad')):
        with pytest.raises(ai_chat.AiError, match=why):
            ai_chat.ask(ctx(db), 'Hello there', 'sk-ant-test', 'claude-x', 5.0, 0.0,
                        http=FakeClaude([(code, {'error': {'message': 'model: bad'}})]))

    def offline(*a):
        raise OSError('no route')
    with pytest.raises(ai_chat.AiError, match='internet'):
        ai_chat.ask(ctx(db), 'Hello there', 'sk-ant-test', 'claude-x', 5.0, 0.0, http=offline)
    with pytest.raises(ai_chat.AiError, match='3 to 2,000'):
        ai_chat.ask(ctx(db), 'hi', 'sk-ant-test', 'claude-x', 5.0, 0.0, http=FakeClaude([]))


def test_usage_cost_key_and_settings(db):
    assert ai_chat.cost('claude-haiku-4-5', 1_000_000, 100_000) == 1.5
    assert ai_chat.cost('claude-someday-9', 1_000_000, 0) == 3.0                     # priced like Sonnet
    db.write(ai_chat.add_usage, '2026-10', 100, 10, 0.25)
    db.write(ai_chat.add_usage, '2026-10', 50, 5, 0.5)
    assert db.read(ai_chat.usage, '2026-10') == {'calls': 2, 'in': 150, 'out': 15, 'usd': 0.75}
    assert db.read(ai_chat.usage, '2026-11')['usd'] == 0.0
    st = ai_chat.status(db, {**S, 'ai_model': 'claude-sonnet-4-5', 'ai_month_usd': 5.0}, False, NOW)
    assert st['on'] is False and st['month'] == '2026-10' and st['left_usd'] == 4.25 and st['priced']
    assert ai_chat.clean_key('  sk-ant-api03-' + 'a' * 40 + ' ').startswith('sk-ant-')
    for bad in ('', 'sk-123', 'sk-ant-short', 'sk-ant-' + 'a' * 30 + ' x'):
        with pytest.raises(ai_chat.AiError):
            ai_chat.clean_key(bad)
    s, err = config.validate({'ai_model': 'gpt-4', 'ai_month_usd': 9999})
    assert s['ai_month_usd'] == 500 and err and s['ai_model'] == 'claude-sonnet-4-5'


def test_the_ai_routes_never_show_the_key(server, monkeypatch, tmp_path):
    import winsys
    monkeypatch.setattr(config, 'APP_DIR', tmp_path)                       # never the real vault
    monkeypatch.setattr(winsys, 'protect', lambda b: b'enc:' + b[::-1])
    monkeypatch.setattr(winsys, 'unprotect', lambda b: b[4:][::-1])
    st, _, raw = call(server, 'GET', '/api/ai')
    d = json.loads(raw)
    assert st == 200 and d['on'] is False and 'mcp_server.py' in d['mcp']['config'] and d['used']['calls'] == 0
    st, _, raw = call(server, 'POST', '/api/ai/ask', {'question': 'What did I search?'})
    assert st == 400 and 'Ask AI is off' in json.loads(raw)['error']
    assert call(server, 'POST', '/api/ai', {'key': 'not-a-key'})[0] == 400
    key = 'sk-ant-api03-' + 'k' * 40
    st, _, raw = call(server, 'POST', '/api/ai', {'key': key})
    assert st == 200 and json.loads(raw)['on'] and key not in raw.decode()
    assert key.encode() not in (tmp_path / 'ai.secret').read_bytes()
    st, _, raw = call(server, 'GET', '/api/health')
    assert json.loads(raw)['ai']['on'] and key not in raw.decode()

    def claude(url, headers, body, timeout):
        assert headers['x-api-key'] == key
        return 200, json.dumps({'stop_reason': 'end_turn', 'usage': {'input_tokens': 100, 'output_tokens': 10},
                                'content': [{'type': 'text', 'text': 'Nothing searched yet.'}]}).encode()
    monkeypatch.setattr(ai_chat, '_http', claude)
    got = []
    monkeypatch.setattr(server, 'new_job', lambda kind, fn: got.append(fn) or 'job1')
    st, _, raw = call(server, 'POST', '/api/ai/ask', {'question': 'What did I search?'})
    assert st == 200 and json.loads(raw)['job'] == 'job1'
    out = asyncio.run(got[0](lambda m: None))
    assert out['answer'] == 'Nothing searched yet.' and out['status']['used']['calls'] == 1 and key not in json.dumps(out)
    call(server, 'POST', '/api/settings', {'ai_month_usd': 0.5})
    server.db.write(ai_chat.add_usage, ai_chat.month_of(__import__('time').time()), 0, 0, 0.5)
    st, _, raw = call(server, 'POST', '/api/ai/ask', {'question': 'What did I search?'})
    assert st == 400 and 'used up' in json.loads(raw)['error']
    st, _, raw = call(server, 'POST', '/api/ai', {'forget': True})
    assert st == 200 and json.loads(raw)['on'] is False and not (tmp_path / 'ai.secret').exists()
    call(server, 'POST', '/api/settings', {'ai_month_usd': 5})
