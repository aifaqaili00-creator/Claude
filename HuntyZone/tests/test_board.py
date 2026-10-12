"""The product board: cards, orders, trophies, hints from research, stock alerts, deadlines and their emails."""
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import amazon_check as ac  # noqa: E402
import board  # noqa: E402
import config  # noqa: E402
import fxrates  # noqa: E402
import storage  # noqa: E402
from engine import remind  # noqa: E402
from test_discovery import suggest  # noqa: E402
from test_storage import ROWS  # noqa: E402

TZ = ZoneInfo('Asia/Dubai')
DAY = 86400
NOW = remind.at('2026-10-12', (10, 0), TZ)
S = {**config.defaults(), 'mail_address': 'ali@gmail.com', 'mail_from_name': 'Ali Afa'}


@pytest.fixture
def db(tmp_path):
    d = storage.DB(tmp_path / 'm.db').open()
    yield d
    d.close()


def at(day, h, m=0):
    return remind.at(day, (h, m), TZ)


def search(db, rows=ROWS, at_=NOW - DAY, kw='calming dog bed', market='US'):
    s = ac.summarize(market, kw, ac.MARKETS[market]['expect'][0], [dict(r) for r in rows], 3, 9, at_)
    db.write(storage.save_serp, s, 'qualify', None, 1, None)


def approve(db, cid, now=NOW):
    """Tick every approval check, so the product moves on to Sourcing & PO."""
    out = {}
    for item in detail(db, cid)['card']['approval']['items']:
        out = db.write(board.set_check, cid, item['id'], True, S, now)
    return out


def add(db, approved=True, **kw):
    cid = db.write(board.add_card, {'title': 'Calming Dog Bed', 'market': 'US', 'kw': 'calming dog bed', **kw}, S, NOW)['id']
    if approved:
        approve(db, cid)
    return cid


def order(db, cid, markets=('US',)):
    db.write(board.set_order, cid, 1, {'unit_cost': 6}, S, NOW)
    out = None
    for mk in markets:
        out = db.write(board.set_line, cid, 1, mk, {'units': 300, 'ship_cost': 450, 'ship_mode': 'sea'}, S, NOW)
    return out


def detail(db, cid, now=NOW):
    d = board.card_detail(db, cid, S, now)
    json.dumps(d, allow_nan=False)
    return d


def people(db, *pairs):
    out = []
    for name, email in pairs:
        out = db.write(board.add_notifier, name, email, NOW)
    return [p['id'] for p in out]


def test_a_product_goes_on_the_board_once_and_is_watched(db):
    db.write(storage.set_pref, 'US', 'B0DOGBED01', cogs_usd=5.0)
    cid = db.write(board.add_card, {'title': 'Calming Dog Bed', 'market': 'US', 'asin': 'b0dogbed01', 'kw': 'Calming Dog Bed!',
                                    'image': 'https://m.media-amazon.com/images/I/x.jpg'}, {**S, 'cost_currency': 'AUD'}, NOW)['id']
    assert db.write(board.add_card, {'title': 'Other name', 'market': 'US', 'asin': 'B0DOGBED01'}, S, NOW) == \
        {'id': cid, 'exists': True}
    d = detail(db, cid)
    c, o = d['card'], d['orders'][0]
    assert c['stage'] == 'approval' and c['key'] == 'PB-%d' % cid and c['kw'] == 'calming dog bed' and c['asin'] == 'B0DOGBED01'
    assert c['image'] == 'https://m.media-amazon.com/images/I/x.jpg' and c['unlocked'] == ['approval']
    assert o['cost_cur'] == 'AUD' and o['unit_cost'] == round(fxrates.convert(5.0, 'USD', 'AUD'), 2)
    assert [ln['market'] for ln in o['lines']] == ['US'] and o['status']['missing'][0] == 'units for USA'
    w = db.read(storage.watches)
    assert [(x['kind'], x['market'], x['target'], x['stage']) for x in w] == [('keyword', 'US', 'calming dog bed', 'researching')]
    assert approve(db, cid)['trophies'] == ['Approved']
    c = detail(db, cid)['card']
    assert c['stage'] == 'sourcing' and c['unlocked'] == ['approval', 'sourcing']
    assert [x['stage'] for x in db.read(storage.watches)] == ['sourcing']
    other = db.write(board.add_card, {'title': 'Lick Mat', 'market': 'AU', 'image': 'javascript:alert(1)'}, S, NOW)
    assert db.read(board._card, other['id'])['image'] is None and db.read(board._card, other['id'])['kw'] == 'lick mat'
    for bad in ({'title': 'x', 'market': 'XX'}, {'title': '  ', 'market': 'US'}):
        with pytest.raises(board.BoardError):
            db.write(board.add_card, bad, S, NOW)


def test_a_complete_order_wins_a_trophy_and_opens_launch(db):
    cid = add(db)
    ids = people(db, ('Sara Khan', 'sara@example.com'))
    assert db.write(board.set_task, cid, 'sourcing', {'days': 7, 'notify': ids}, S, NOW, TZ)['mails'] == 3
    with pytest.raises(board.BoardError, match='Finish Sourcing'):
        db.write(board.move_card, cid, 'launch', None, NOW)
    db.write(board.set_order, cid, 1, {'unit_cost': 6}, S, NOW)
    assert db.write(board.set_line, cid, 1, 'AU', {'units': 200}, S, NOW)['watched'] is True
    db.write(board.set_line, cid, 1, 'US', {'units': 300, 'ship_cost': 450, 'ship_mode': 'sea'}, S, NOW)
    out = db.write(board.set_line, cid, 1, 'AU', {'ship_cost': 300, 'ship_cur': 'USD', 'ship_mode': 'air'}, S, NOW)
    assert out['trophies'] == ['Order ready']
    d = detail(db, cid)
    assert d['card']['stage'] == 'launch' and d['card']['unlocked'] == ['approval', 'sourcing', 'launch']
    assert d['card']['invested_usd'] == 300 * 6 + 450 + 200 * 6 + 300 and d['card']['milestones']['order_ready'] == NOW
    assert d['tasks']['sourcing']['done_at'] == NOW and {m['status'] for m in d['tasks']['sourcing']['mails']} == {'cancelled'}
    assert d['events'][0]['kind'] == 'trophy' and d['events'][0]['text'] == 'Order ready'
    assert {(w['market'], w['stage']) for w in db.read(storage.watches)} == {('US', 'ordered'), ('AU', 'ordered')}
    assert d['orders'][0]['lines'][1]['cost']['landed_unit_usd'] == 7.5
    db.write(board.set_line, cid, 1, 'US', {'units': None}, S, NOW + 60)            # later edits never lock it again
    assert detail(db, cid)['card']['stage'] == 'launch'
    db.write(board.move_card, cid, 'sourcing', 5, NOW)
    with pytest.raises(board.BoardError, match='Finish Launch'):
        db.write(board.move_card, cid, 'reorder', None, NOW)
    for bad in ({'units': 0}, {'ship_mode': 'boat'}, {'ship_cur': 'GBP'}, {'steps': {'arrived': 'soon'}}, {'steps': {'x': None}}):
        with pytest.raises(board.BoardError):
            db.write(board.set_line, cid, 1, 'US', bad, S, NOW)


def test_hints_come_from_research_and_go_back_when_the_market_moves(db):
    search(db)
    suggest(db, 'US', 'calming dog bed', ['calming dog bed', 'calming dog bed for large dogs'], NOW - DAY)
    cid = add(db)
    order(db, cid)
    board.refresh_hints(db, cid, S, NOW)
    hs = {h['key']: h for h in detail(db, cid)['hints']}
    assert {'price', 'profit', 'reviews', 'competition', 'phrases', 'target'} <= set(hs)
    assert hs['price']['data']['price'] == 20 and hs['profit']['data']['profit'] < 0        # US$6 + US$1.50 shipping loses
    assert 'large dogs' in hs['phrases']['text'] and hs['target']['data']['v'] > 0
    for k in ('price', 'reviews'):
        db.write(board.set_hint, hs[k]['id'], 'done', 'ok', S, NOW)
    search(db, [dict(r, price=r['price'] * 1.5) for r in ROWS], NOW)                         # page 1 got dearer
    board.refresh_hints(db, cid, S, NOW + DAY)
    d = detail(db, cid, NOW + DAY)
    hs = {h['key']: h for h in d['hints']}
    assert hs['price']['status'] == 'todo' and hs['price']['moved']['from'] == 20 and hs['price']['moved']['to'] == 30
    assert hs['reviews']['status'] == 'done' and hs['reviews']['note'] == 'ok' and hs['reviews']['moved'] is None
    assert d['card']['hints']['moved'] == 1 and d['money']['US']['econ']['Profit'] > 0
    late = detail(db, cid, NOW + 31 * DAY)
    assert {h['key'] for h in late['hints'] if h['stale']} == {'reviews'}                  # checked 31 days ago, every 30


def test_launch_needs_every_hint_handled_and_a_live_day(db):
    search(db)
    cid = add(db)
    order(db, cid)
    board.refresh_hints(db, cid, S, NOW)
    hs = detail(db, cid)['hints']
    with pytest.raises(board.BoardError, match='differently'):
        db.write(board.set_hint, hs[0]['id'], 'adjusted', '', S, NOW)
    for h in hs:
        assert db.write(board.set_hint, h['id'], 'skip', None, S, NOW)['trophies'] == []
    db.write(board.add_hint, cid, 'US', 'Order 50 inserts', NOW)
    own = next(h for h in detail(db, cid)['hints'] if h['custom'])
    assert own['status'] == 'todo' and own['text'] == 'Order 50 inserts'
    assert db.write(board.update_card, cid, {'live': {'US': '2026-10-10'}}, S, NOW)['trophies'] == []
    assert db.write(board.set_hint, own['id'], 'done', 'ordered', S, NOW)['trophies'] == ['Launched']
    d = detail(db, cid)
    assert d['card']['stage'] == 'reorder' and d['card']['unlocked'] == ['approval', 'sourcing', 'launch', 'reorder']
    assert d['data']['live'] == {'US': '2026-10-10'} and db.read(storage.watches)[0]['stage'] == 'launched'
    with pytest.raises(board.BoardError, match='your own'):
        db.write(board.remove_hint, hs[0]['id'])
    db.write(board.remove_hint, own['id'])
    with pytest.raises(board.BoardError):
        db.write(board.update_card, cid, {'targets': {'US': -1}}, S, NOW)
    db.write(board.update_card, cid, {'targets': {'US': 250}, 'color': 'teal', 'title': 'Donut Dog Bed'}, S, NOW)
    c = detail(db, cid)
    assert c['data']['targets'] == {'US': 250} and c['card']['color'] == 'teal' and c['card']['title'] == 'Donut Dog Bed'


def launched(db):
    cid = add(db)
    order(db, cid)
    assert db.write(board.update_card, cid, {'live': {'US': '2026-10-01'}}, S, NOW)['trophies'] == ['Launched']
    return cid


def test_stock_turns_red_once_and_says_when_to_reorder(db):
    cid = launched(db)
    line = detail(db, cid)['orders'][0]['lines'][0]['id']
    db.write(board.set_line, cid, 1, 'US', {'steps': {'arrived': '2026-10-01'}}, S, NOW)
    first = db.write(board.add_count, line, 200, S, NOW - 5 * DAY)['alerts']          # 16 a day since 1 Oct: 12 days left
    assert [a['rule'] for a in first] == ['board_reorder']                               # but a new shipment takes 87
    found = db.write(board.add_count, line, 90, S, NOW)['alerts']                     # 30% of 300, selling 22 a day
    assert [a['rule'] for a in found] == ['board_stock_low'] and 'Re-order by' in found[0]['body']
    assert db.write(board.add_count, line, 80, S, NOW + 60)['alerts'] == []           # each alert only once
    s = detail(db, cid)['stock'][0]
    assert s['level'] == 'red' and s['order_now'] and s['rate'] == pytest.approx(23, abs=0.5) and s['left'] == 80
    assert s['lead_days'] == 87 and s['sellout'] and s['reorder_by'] < s['sellout']
    st = board.state(db, S, NOW)
    assert st['low'] == 1 and st['cards'][0]['markets'][0]['stock']['level'] == 'red'
    assert 'USA 27% left (re-order now)' in board.summary_text(db, S, NOW)
    assert {a['rule'] for a in db.read(storage.list_alerts)} >= {'board_stock_low', 'board_reorder'}
    with pytest.raises(board.BoardError):
        db.write(board.add_count, line, -1, S, NOW)
    with pytest.raises(board.BoardError):
        db.write(board.add_count, 999, 5, S, NOW)


def test_reorders_start_from_the_last_order_and_win_trophies(db):
    cid = add(db)
    order(db, cid)
    with pytest.raises(board.BoardError, match='launched'):
        db.write(board.start_reorder, cid, 'US', S, NOW)
    db.write(board.update_card, cid, {'live': {'US': '2026-10-01'}}, S, NOW)
    ids = people(db, ('Sara', 'sara@example.com'))
    db.write(board.set_task, cid, 'reorder', {'days': 5, 'notify': ids}, S, NOW, TZ)
    out = db.write(board.start_reorder, cid, 'US', S, NOW)
    assert out['n'] == 2 and out['trophies'] == []
    o2 = detail(db, cid)['orders'][1]
    assert o2['unit_cost'] == 6 and o2['lines'][0]['ship_mode'] == 'sea' and o2['lines'][0]['units'] is None
    out = db.write(board.set_line, cid, 2, 'US', {'units': 400, 'ship_cost': 500}, S, NOW)
    assert out['trophies'] == ['Re-order #2 ready']
    d = detail(db, cid)
    assert d['card']['milestones']['reorders'] == 1 and d['tasks']['reorder']['done_at'] == NOW
    assert d['card']['invested_usd'] == 300 * 6 + 450 + 400 * 6 + 500


def test_deadlines_schedule_three_emails_and_replace_old_ones(db):
    cid = add(db)
    ids = people(db, ('Sara Khan', 'sara@example.com'), ('Ahmed', 'ahmed@example.com'))
    t = db.write(board.set_task, cid, 'sourcing', {'days': 7, 'notify': ids + [999], 'note': 'Bring samples'}, S, NOW, TZ)
    assert t['due_day'] == '2026-10-19' and t['mails'] == 3
    task = detail(db, cid)['tasks']['sourcing']
    assert task['notify'] == ids and task['note'] == 'Bring samples' and task['meet_start'] == at('2026-10-19', 15)
    assert [(m['kind'], m['send_at']) for m in task['mails']] == [('start', NOW), ('reminder', at('2026-10-16', 9)),
                                                                  ('meeting', at('2026-10-19', 9))]
    db.write(board.set_task, cid, 'sourcing', {'days': 3, 'notify': ids[:1]}, S, NOW + 60, TZ)
    assert db.read(lambda c: [r[0] for r in c.execute("SELECT status FROM board_mail WHERE task_id=? ORDER BY id",
                                                      (t['task_id'],))]) == ['cancelled'] * 3
    assert detail(db, cid)['tasks']['sourcing']['due_day'] == '2026-10-15'
    quiet = db.write(board.set_task, cid, 'launch', {'due_day': '2026-10-20'}, S, NOW, TZ)
    assert quiet['mails'] == 0 and detail(db, cid)['tasks']['launch']['days'] == 8
    st = board.state(db, S, NOW)['cards'][0]['task']
    assert st['due_day'] == '2026-10-15' and st['next_mail']['kind'] == 'start'
    for bad in ({'days': 0}, {}, {'due_day': '2026-10-12'}, {'days': 7, 'meet_time': '25:00'}):
        with pytest.raises(board.BoardError):
            db.write(board.set_task, cid, 'sourcing', bad, S, NOW, TZ)
    with pytest.raises(board.BoardError):
        db.write(board.set_task, cid, 'shipping', {'days': 3}, S, NOW, TZ)


def test_the_sender_sends_retries_and_reports(db):
    cid = add(db)
    ids = people(db, ('Sara Khan', 'sara@example.com'))
    db.write(board.set_task, cid, 'sourcing', {'days': 7, 'notify': ids}, S, NOW, TZ)
    sent = []
    keep = lambda *a: sent.append(a)                                                          # noqa: E731
    out = board.tick(db, S, keep, NOW + 1, TZ)
    assert out['sent'] == 1 and sent[0][0] == [('Sara Khan', 'sara@example.com')]
    assert sent[0][1] == 'New task: Sourcing & PO for Calming Dog Bed, due Mon 19 Oct'
    assert '- the price per unit' in sent[0][2] and 'Ali Afa has started' in sent[0][2] and sent[0][4] is None
    assert detail(db, cid)['events'][0]['text'] == 'New task email emailed to Sara'
    assert board.tick(db, S, keep, NOW + 2, TZ)['sent'] == 0                                  # nothing sent twice

    def down(*a):
        raise RuntimeError('server down')
    t = at('2026-10-16', 9)
    assert board.tick(db, S, down, t, TZ) == {'sent': 0, 'failed': 1, 'missed': 0, 'alerts': [], 'next': None}
    rem = next(m for m in detail(db, cid)['tasks']['sourcing']['mails'] if m['kind'] == 'reminder')
    assert rem['attempts'] == 1 and rem['send_at'] == t + 900 and rem['error'] == 'server down'
    board.tick(db, S, down, t + 900, TZ)
    out = board.tick(db, S, down, t + 900 + 3600, TZ)
    assert [a['rule'] for a in out['alerts']] == ['board_mail_failed']
    assert next(m for m in detail(db, cid)['tasks']['sourcing']['mails'] if m['kind'] == 'reminder')['status'] == 'failed'
    out = board.tick(db, S, keep, at('2026-10-19', 9), TZ)
    assert out['sent'] == 1 and sent[-1][1] == 'Update meeting: Sourcing & PO for Calming Dog Bed, Mon 19 Oct at 15:00'
    assert 'METHOD:REQUEST' in sent[-1][4] and 'ATTENDEE;CN="Sara Khan"' in sent[-1][4].replace('\r\n ', '')


def test_late_emails_are_missed_and_finished_or_removed_products_send_none(db):
    ids = people(db, ('Sara', 'sara@example.com'))
    late = add(db)
    db.write(board.set_task, late, 'sourcing', {'days': 7, 'notify': ids}, S, NOW, TZ)
    done = add(db, title='Lick Mat', kw='lick mat')
    db.write(board.set_task, done, 'sourcing', {'days': 7, 'notify': ids}, S, NOW, TZ)
    order(db, done)
    gone = add(db, title='Pet Stairs', kw='pet stairs')
    db.write(board.set_task, gone, 'sourcing', {'days': 7, 'notify': ids}, S, NOW, TZ)
    db.write(board.archive_card, gone, True, NOW)
    sent = []
    out = board.tick(db, S, lambda *a: sent.append(a), at('2026-10-20', 0, 1), TZ)
    assert sent == [] and out['missed'] == 3 and [a['rule'] for a in out['alerts']] == ['board_mail_missed'] * 3
    assert board.state(db, S, NOW)['archived'][0]['id'] == gone
    assert board.pending_mail(db) == 0


def test_the_board_state_summary_and_refresh_after_runs(db):
    search(db)
    cid = add(db)
    order(db, cid)
    st = board.state(db, S, NOW)
    json.dumps(st, allow_nan=False)
    assert st['cards'][0]['stage'] == 'launch' and st['trophies'] == 2 and st['invested_usd'] == 2250
    assert st['recent'][0]['kind'] == 'trophy' and st['rev'] == board.rev()
    text = board.summary_text(db, S, NOW)
    assert 'Launch & strategy (1)' in text and 'Trophies this week: Approved (Calming Dog Bed); Order ready (Calming Dog Bed)' in text
    assert 'Invested so far: US$ 2,250' in text and 'Sourcing & PO (0)' in text
    assert board.refresh_for(db, S, [{'kind': 'watch_kw', 'market': 'US', 'target': 'Calming Dog Bed'}], NOW) == 1
    assert board.refresh_for(db, S, [{'kind': 'lists', 'market': 'US', 'target': 'all'}], NOW) == 0
    assert detail(db, cid)['hints']
    db.write(board.archive_card, cid, True, NOW)
    assert board.state(db, S, NOW)['cards'] == []


def test_mistakes_can_be_removed_and_products_deleted_for_good(db):
    cid = launched(db)
    other = add(db, title='Lick Mat', kw='lick mat')
    line = detail(db, cid)['orders'][0]['lines'][0]['id']
    db.write(board.set_line, cid, 1, 'US', {'steps': {'arrived': '2026-10-01'}}, S, NOW)
    db.write(board.add_count, line, 200, S, NOW - 5 * DAY)
    db.write(board.add_count, line, 2000, S, NOW)                                           # a typo
    typo = detail(db, cid)['stock'][0]['counts'][-1]
    assert typo[1] == 2000
    db.write(board.remove_count, typo[2], NOW + 60)
    s = detail(db, cid)['stock'][0]
    assert [c[1] for c in s['counts']] == [200] and s['left'] == 200
    assert 'the count of 2,000 on' in detail(db, cid)['events'][0]['text']
    db.write(board.add_note, cid, 'Supplier is late', NOW)
    events = detail(db, cid)['events']
    db.write(board.remove_note, next(e['id'] for e in events if e['kind'] == 'note'))
    assert all(e['kind'] != 'note' for e in detail(db, cid)['events'])
    for bad in ((board.remove_note, events[-1]['id']), (board.remove_note, 99999), (board.remove_count, 99999)):
        with pytest.raises(board.BoardError):                                               # history is not a note
            db.write(*bad)
    ids = people(db, ('Sara', 'sara@example.com'))
    db.write(board.set_task, cid, 'reorder', {'days': 7, 'notify': ids}, S, NOW, TZ)
    db.write(board.archive_card, cid, True, NOW)
    st = board.state(db, S, NOW)
    assert [c['id'] for c in st['cards']] == [other] and st['archived'][0]['id'] == cid and board.pending_mail(db) == 0
    assert 'Removed from the board' in [e['text'] for e in detail(db, cid)['events']]
    assert any(a['data'].get('card_id') == cid for a in db.read(storage.list_alerts))
    db.write(board.delete_card, cid)
    left = db.read(lambda c: [c.execute(q, a).fetchone()[0] for q, a in (
        ('SELECT COUNT(*) FROM board_card WHERE id=?', (cid,)),
        ('SELECT COUNT(*) FROM board_order WHERE card_id=?', (cid,)),
        ('SELECT COUNT(*) FROM board_line WHERE order_id NOT IN (SELECT id FROM board_order)', ()),
        ('SELECT COUNT(*) FROM board_stock WHERE line_id NOT IN (SELECT id FROM board_line)', ()),
        ('SELECT COUNT(*) FROM board_hint WHERE card_id=?', (cid,)),
        ('SELECT COUNT(*) FROM board_event WHERE card_id=?', (cid,)),
        ('SELECT COUNT(*) FROM board_task WHERE card_id=?', (cid,)),
        ('SELECT COUNT(*) FROM board_mail WHERE task_id NOT IN (SELECT id FROM board_task)', ()),
        ("SELECT COUNT(*) FROM alert WHERE target='board'", ()))])
    assert left == [0] * 9
    assert board.state(db, S, NOW)['archived'] == [] and detail(db, other)['card']['title'] == 'Lick Mat'
    with pytest.raises(board.BoardError):
        db.write(board.delete_card, cid)


def test_the_product_update_lists_every_product_and_goes_to_everyone(db):
    bed = add(db)
    db.write(board.set_line, bed, 1, 'US', {'units': 300}, S, NOW)
    db.write(board.set_task, bed, 'sourcing', {'days': 7}, S, NOW, TZ)
    mat = add(db, title='Lick Mat', kw='lick mat')
    order(db, mat)
    for bad in ({'asin': 'B0SHORT'}, {'alibaba': 'http://www.alibaba.com/x'}, {'alibaba': 'javascript:alert(1)'}):
        with pytest.raises(board.BoardError):
            db.write(board.update_card, bed, bad, S, NOW)
    db.write(board.update_card, bed, {'asin': 'b0dogbed01', 'alibaba': 'https://www.alibaba.com/product-detail/bed.html'}, S, NOW)
    team = detail(db, bed)['team']
    assert dict(team['rows']) == {'Units needed': 'USA 300', 'Target market': 'USA', 'Sourcing ETA': 'Mon 19 Oct',
                                  'Stage': 'Sourcing & PO \u00b7 First order'}
    assert team['amazon'] == 'https://www.amazon.com/dp/B0DOGBED01' and team['alibaba_label'] == 'Alibaba page'
    with pytest.raises(board.BoardError, match='Notifier list'):
        board.inform_send(db, S, lambda *a: None, None, '', NOW)
    people(db, ('Sara Khan', 'sara@example.com'), ('Ahmed Ali', 'ahmed@example.com'))
    view = board.inform_view(db, S, None, 'Quotes by Friday', NOW)
    assert view['count'] == 2 and view['to'] == ['Ahmed Ali', 'Sara Khan'] and view['changes'] == []
    assert view['subject'] == 'Product update, Mon 12 Oct: 2 products' and view['last']['sent_at'] is None
    assert 'Hi Ahmed and Sara,' in view['text'] and 'Quotes by Friday' in view['text'] and 'Sourcing ETA: Done, Mon 12 Oct' in view['text']
    assert view['text'].index('1. Calming Dog Bed') < view['text'].index('2. Lick Mat')         # Sourcing first
    with pytest.raises(board.BoardError, match='Pick who'):
        board.inform_send(db, S, lambda *a: None, [], '', NOW)
    sent = []
    out = board.inform_send(db, S, lambda *a: sent.append(a), [view['notifiers'][1]['id']], 'Hi', NOW + 60)
    assert out['to'] == ['Sara Khan'] and sent[0][0] == [('Sara Khan', 'sara@example.com')] and sent[0][4] is None
    assert 'View on Amazon: https://www.amazon.com/dp/B0DOGBED01' in sent[0][2] and '<html>' in sent[0][3]
    db.write(board.set_line, bed, 1, 'US', {'units': 500}, S, NOW + 120)
    view = board.inform_view(db, S, None, '', NOW + 180)
    assert [c['text'] for c in view['changes']] == ['Calming Dog Bed: USA units 300 \u2192 500']
    assert view['last']['sent_at'] == NOW + 60 and view['last']['to'] == ['Sara Khan']
    board.inform_send(db, S, lambda *a: sent.append(a), None, '', NOW + 240)
    assert sent[1][1] == 'Product update, Mon 12 Oct: Calming Dog Bed changed' and len(sent[1][0]) == 2
    assert detail(db, bed)['events'][0]['text'] == 'Product update emailed to Ahmed and Sara'


def test_the_product_update_goes_out_by_itself_once_editing_stops(db):
    people(db, ('Sara', 'sara@example.com'))
    bed = add(db)
    sent = []
    keep = lambda *a: sent.append(a)                                                           # noqa: E731
    assert board.tick(db, S, keep, NOW, TZ)['next'] is None and sent == []                    # the board as it is: the start
    db.write(board.set_line, bed, 1, 'US', {'units': 300}, S, NOW + 60)
    assert board.tick(db, S, keep, NOW + 120, TZ)['next'] == NOW + 120 + 900 and sent == []
    assert board.state(db, S, NOW + 130)['inform']['next_at'] == NOW + 120 + 900 and board.inform_waiting(db, S)
    db.write(board.set_line, bed, 1, 'US', {'units': 400}, S, NOW + 600)                       # still editing: wait again
    t = NOW + 660
    assert board.tick(db, S, keep, t, TZ)['next'] == t + 900
    assert board.tick(db, S, keep, t + 899, TZ)['next'] == t + 900 and sent == []
    assert board.tick(db, S, keep, t + 900, TZ, inform_ok=False)['next'] is None and sent == []  # no Gmail yet
    assert board.tick(db, {**S, 'board_inform_auto': False}, keep, t + 900, TZ)['next'] is None and sent == []
    board.tick(db, S, keep, t + 900, TZ)
    assert len(sent) == 1 and sent[0][0] == [('Sara', 'sara@example.com')]
    assert sent[0][1] == 'Product update, Mon 12 Oct: Calming Dog Bed changed'
    assert 'The product board changed' in sent[0][2] and 'Calming Dog Bed: USA units not set \u2192 400' in sent[0][2]
    assert not board.inform_waiting(db, S) and board.state(db, S, NOW)['inform']['sent_at'] == t + 900
    assert board.tick(db, S, keep, t + 3000, TZ)['next'] is None and len(sent) == 1           # nothing new
    db.write(board.set_line, bed, 1, 'US', {'units': 500}, S, t + 4000)
    board.tick(db, S, keep, t + 4060, TZ)
    db.write(board.set_line, bed, 1, 'US', {'units': 400}, S, t + 4100)
    assert board.tick(db, S, keep, t + 4160, TZ)['next'] is None and not board.inform_waiting(db, S)  # changed back

    def down(*a):
        raise RuntimeError('server down')
    db.write(board.update_card, bed, {'title': 'Donut Dog Bed'}, S, t + 5000)
    t2 = t + 5060
    assert board.tick(db, S, down, t2, TZ)['next'] == t2 + 900
    assert board.tick(db, S, down, t2 + 900, TZ)['next'] == t2 + 1800
    assert board.tick(db, S, down, t2 + 1800, TZ)['next'] == t2 + 1800 + 3600
    out = board.tick(db, S, down, t2 + 5400, TZ)
    assert [a['rule'] for a in out['alerts']] == ['board_inform_failed'] and out['next'] is None
    assert not board.inform_waiting(db, S) and board.inform_view(db, S, None, '', t2 + 5500)['error'] == 'server down'
    assert board.tick(db, S, keep, t2 + 9000, TZ)['next'] is None and len(sent) == 1           # it gave up on these changes
    db.write(board.add_card, {'title': 'Sauna Hat', 'market': 'US', 'kw': 'sauna hat'}, S, t2 + 9100)
    assert board.tick(db, S, keep, t2 + 9160, TZ)['next'] == t2 + 9160 + 900                   # new changes: a new try


def test_notifiers(db):
    out = db.write(board.add_notifier, 'Sara <b>', 'sara@example.com', NOW)
    assert [(p['name'], p['email']) for p in out] == [('Sara b', 'sara@example.com')]
    out = db.write(board.add_notifier, 'Sara K', 'SARA@example.com', NOW)
    assert [(p['name'], p['email']) for p in out] == [('Sara K', 'sara@example.com')]
    for bad in ('not-an-email', 'a@b.c\r\nBcc: z@z.zz', 'x' * 250 + '@example.com'):
        with pytest.raises(board.BoardError):
            db.write(board.add_notifier, 'x', bad, NOW)
    assert db.write(board.remove_notifier, out[0]['id'], NOW) == []
    assert db.write(board.add_notifier, '', 'sara@example.com', NOW)[0]['name'] == 'sara'


def test_approval_needs_every_check_ticked(db):
    cid = add(db, approved=False)
    c = detail(db, cid)['card']
    items = c['approval']['items']
    assert c['stage'] == 'approval' and len(items) == 6 and c['approval']['done'] == 0 and c['progress']['approval'] == 0
    with pytest.raises(board.BoardError, match='Approval'):
        db.write(board.move_card, cid, 'sourcing', None, NOW)               # locked until approved
    for it in items[:-1]:
        assert db.write(board.set_check, cid, it['id'], True, S, NOW)['trophies'] == []
    db.write(board.set_check, cid, items[0]['id'], False, S, NOW)            # untick one again
    db.write(board.add_check, cid, 'Sample arrived and looks good', NOW)
    c = detail(db, cid)['card']
    assert c['approval']['total'] == 7 and c['approval']['done'] == 4 and c['stage'] == 'approval'
    assert 'Sample arrived and looks good' in c['approval']['missing']
    db.write(board.remove_check, cid, items[0]['id'], NOW)                   # this check does not apply
    last = [i for i in detail(db, cid)['card']['approval']['items'] if not i['done']]
    out = {}
    for it in last:
        out = db.write(board.set_check, cid, it['id'], True, S, NOW + 60)
    c = detail(db, cid)['card']
    assert out['trophies'] == ['Approved'] and c['stage'] == 'sourcing' and c['milestones']['approved'] == NOW + 60
    assert c['progress']['approval'] == 1.0
    with pytest.raises(board.BoardError, match='already approved'):
        db.write(board.set_check, cid, items[1]['id'], False, S, NOW)
    for bad in ('', '   '):
        with pytest.raises(board.BoardError):
            db.write(board.add_check, add(db, approved=False, title='Lick Mat', kw='lick mat'), bad, NOW)


def test_products_already_on_the_board_count_as_approved(db):
    cid = add(db, approved=False)
    db.write(lambda c: c.execute("UPDATE board_card SET stage='sourcing', data_json='{}' WHERE id=?", (cid,)))
    c = detail(db, cid)['card']
    assert c['stage'] == 'sourcing' and 'sourcing' in c['unlocked'] and c['milestones']['approved']
