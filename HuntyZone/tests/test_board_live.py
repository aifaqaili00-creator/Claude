"""The interactive Product board: live stock and sales from Amazon (SP-API), the card face (progress ring, countdown,
mini charts, quick-edit values, tags), moving a due day, the timeline, saved filters, and putting a card back."""
import datetime as dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import board  # noqa: E402
import spapi  # noqa: E402
import storage  # noqa: E402
from engine import board as rules  # noqa: E402
from test_board import DAY, NOW, S, add, at, db, detail, launched, order, people  # noqa: E402,F401

TODAY = dt.date(2026, 10, 12)


# ---------- engine rules ----------
def test_sales_rate_needs_a_week_of_amazons_counts():
    days = [('2026-10-%02d' % d, 4) for d in range(1, 12)] + [('2026-10-12', 99)]      # today is not complete yet
    assert rules.sales_rate(days, TODAY) == 4.0
    assert rules.sales_rate(days[:6], TODAY) is None
    assert rules.sales_rate([('2026-08-01', 50)] * 10, TODAY) is None                  # too old
    assert rules.sales_rate([], TODAY) is None


def test_progress_ring():
    assert rules.ring({'idea': 1, 'approval': 1, 'sourcing': 0.5}) == {'pct': 50, 'tone': 'accent'}
    assert rules.ring({k: 1 for k in rules.STAGES}) == {'pct': 100, 'tone': 'good'}
    assert rules.ring({}, late=True)['tone'] == 'bad'


def test_countdown_chip_picks_what_matters_first():
    assert rules.countdown([{'kind': 'reorder', 'day': '2026-10-17', 'market': 'AU'}, {'kind': 'due', 'day': '2026-10-30'}],
                           TODAY) == {'kind': 'reorder', 'day': '2026-10-17', 'days': 5, 'text': 'Re-order in 5 days',
                                      'tone': 'warn', 'market': 'AU'}
    late = rules.countdown([{'kind': 'due', 'day': '2026-10-10'}, {'kind': 'eta', 'day': '2026-10-13'}], TODAY)
    assert late['text'] == '2 days late' and late['tone'] == 'bad'                      # overdue first
    assert rules.countdown([{'kind': 'reorder', 'day': '2026-10-12'}], TODAY)['text'] == 'Re-order now'
    assert rules.countdown([{'kind': 'eta', 'day': '2026-11-01'}], TODAY)['text'] == 'At Amazon in 20 days'
    assert rules.countdown([{'kind': 'due', 'day': '2026-10-13'}], TODAY)['text'] == 'Due in 1 day'
    assert rules.countdown([{'kind': 'other', 'day': '2026-10-13'}, {'kind': 'due', 'day': None}], TODAY) is None


def test_timeline_events_and_how_each_moves():
    ev = rules.timeline_events([{'id': 1, 'title': 'Bed', 'color': 'teal',
                                 'tasks': [{'id': 7, 'stage': 'sourcing', 'due_day': '2026-10-10', 'start_day': '2026-10-01', 'done': False},
                                           {'id': 8, 'stage': 'approval', 'due_day': '2026-10-02', 'done': True}],
                                 'lines': [{'n': 1, 'market': 'AU', 'steps': {'deposit': '2026-10-03'}, 'eta': '2026-12-20'}],
                                 'live': {'US': '2026-11-01'}, 'reorder': [{'market': 'US', 'day': '2026-12-01'}]}], TODAY)
    assert [(e['kind'], e['day']) for e in ev] == [('step', '2026-10-03'), ('due', '2026-10-10'), ('live', '2026-11-01'),
                                                   ('reorder', '2026-12-01'), ('eta', '2026-12-20')]
    due = ev[1]
    assert due['late'] and due['label'] == 'Sourcing & PO due' and due['edit'] == {'type': 'task', 'task_id': 7, 'start_day': '2026-10-01'}
    assert ev[0]['edit'] == {'type': 'step', 'n': 1, 'market': 'AU', 'step': 'deposit'} and ev[0]['label'] == 'Deposit paid'
    assert ev[3]['edit'] is None and ev[4]['edit'] is None and ev[2]['edit'] == {'type': 'live', 'market': 'US'}


def test_tags_are_short_and_unique():
    assert rules.clean_tags([' Summer ', 'summer', 'x' * 40, '', 'Gift', 1]) == ['Summer', 'x' * 24, 'Gift', '1']
    assert len(rules.clean_tags([str(i) for i in range(20)])) == 8


# ---------- SP-API readers ----------
def test_inventory_and_daily_sales_from_the_sp_api():
    inv = spapi.parse_inventory([
        {'asin': 'b0bed00001', 'sellerSku': 'BED-1', 'totalQuantity': 120,
         'inventoryDetails': {'fulfillableQuantity': 100, 'inboundShippedQuantity': 15, 'reservedQuantity': {'totalReservedQuantity': 5}}},
        {'asin': 'B0BED00001', 'sellerSku': 'BED-2', 'totalQuantity': 10, 'inventoryDetails': {'fulfillableQuantity': 10}},
        {'sellerSku': 'NO-ASIN'}])
    assert inv == {'B0BED00001': {'fulfillable': 110, 'inbound': 15, 'reserved': 5, 'total': 130, 'skus': ['BED-1', 'BED-2']}}
    assert spapi.parse_order_metrics([{'interval': '2026-10-01T00:00+10:00--2026-10-02T00:00+10:00', 'unitCount': 3},
                                      {'interval': 'bad'}, {'interval': '2026-10-02T00:00+10:00--x', 'unitCount': 0}]) == \
        [('2026-10-01', 3), ('2026-10-02', 0)]
    assert spapi.day_interval(dt.date(2026, 9, 7), dt.date(2026, 10, 12), 'Australia/Sydney') == \
        '2026-09-07T00:00:00+10:00--2026-10-12T00:00:00+11:00'


def test_inventory_reads_every_page():
    pages = [{'payload': {'inventorySummaries': [{'asin': 'B1', 'inventoryDetails': {'fulfillableQuantity': 1}}]},
              'pagination': {'nextToken': 'n2'}},
             {'payload': {'inventorySummaries': [{'asin': 'B2', 'inventoryDetails': {'fulfillableQuantity': 2}}]}}]
    asked = []

    def http(method, url, headers, body=None, timeout=None):
        if url == spapi.TOKEN_URL:
            return 200, {}, json.dumps({'access_token': 'Atza|t', 'expires_in': 3600}).encode()
        asked.append(url)
        return 200, {}, json.dumps(pages[len(asked) - 1]).encode()
    c = spapi.SpApi({'client_id': 'x', 'client_secret': 'y', 'refresh': {'FE': 'Atzr|r'}}, http=http, sleep=lambda s: None)
    got = c.inventory_by_asin('AU')
    assert set(got) == {'B1', 'B2'} and 'nextToken=n2' in asked[1]


# ---------- the board ----------
class FakeApi:
    def __init__(self, stock, sales, markets=('US',)):
        self.stock, self.sales, self.mk = stock, sales, markets

    def has(self, m):
        return m in self.mk

    def inventory_by_asin(self, m):
        if isinstance(self.stock, Exception):
            raise self.stock
        return self.stock

    def order_metrics(self, m, interval, tz, asin=None, sku=None):
        return [{'interval': '%sT00:00:00-05:00--x' % d, 'unitCount': u} for d, u in self.sales]


def test_live_stock_and_sales_fill_the_reorder_stage(db):
    cid = launched(db)
    db.write(board.update_card, cid, {'asin': 'B0DOGBED01', 'targets': {'US': 150}}, S, NOW)
    db.write(board.set_line, cid, 1, 'US', {'steps': {'arrived': '2026-09-20'}}, S, NOW)
    sales = [('2026-10-%02d' % d, 5) for d in range(1, 12)]
    out = board.sync_live(db, FakeApi({'B0DOGBED01': {'fulfillable': 180}}, sales), S, NOW)
    assert out['cards'] == 1 and out['counts'] == 1 and out['sales_days'] == 11 and not out['errors']
    s = detail(db, cid)['stock'][0]
    assert s['left'] == 180 and s['rate'] == 5.0 and s['rate_src'] == 'Amazon orders' and s['live_at'] == NOW
    assert s['counts'][-1][3] == 'spapi' and len(s['sales']) == 11
    assert board.sync_live(db, FakeApi({'B0DOGBED01': {'fulfillable': 180}}, sales), S, NOW + 3600)['counts'] == 0   # same count
    db.write(board.add_count, s['line_id'], 170, S, NOW + 7200)                         # you can still type a count
    assert detail(db, cid)['stock'][0]['counts'][-1][3] is None
    bad = board.sync_live(db, FakeApi(spapi.SpApiError('refused'), sales), S, NOW + 9000)
    assert bad['errors'] == ['USA stock: refused'] and bad['sales_days'] == 11
    assert board.sync_live(db, None, S, NOW)['cards'] == 0
    assert board.sync_live(db, FakeApi({}, [], markets=('AU',)), S, NOW)['cards'] == 0   # keys for another region only


def test_the_card_face(db):
    cid = launched(db)
    db.write(board.update_card, cid, {'tags': 'pets, Summer, pets'}, S, NOW)
    line = detail(db, cid)['orders'][0]['lines'][0]['id']
    db.write(board.set_line, cid, 1, 'US', {'steps': {'arrived': '2026-10-01'}}, S, NOW)
    db.write(board.add_count, line, 200, S, NOW - 5 * DAY)
    db.write(board.add_count, line, 90, S, NOW)
    c = board.state(db, S, NOW)['cards'][0]
    assert c['tags'] == ['pets', 'Summer'] and c['late'] and c['ring']['tone'] == 'bad'
    assert c['countdown']['kind'] == 'reorder' and c['countdown']['text'] == 'Re-order now'
    assert c['mini']['market'] == 'US' and c['mini']['sent'] == 300 and len(c['mini']['counts']) == 2 and c['mini']['low_pct'] == 30
    assert c['edit']['unit_cost'] == 6 and c['edit']['units'] == {'US': 300} and c['edit']['live'] == {'US': '2026-10-01'}
    json.dumps(c, allow_nan=False)


def test_a_due_day_moves_with_its_emails(db):
    cid = add(db)
    ids = people(db, ('Sara', 'sara@example.com'))
    t = db.write(board.set_task, cid, 'sourcing', {'days': 10, 'notify': ids}, S, NOW, board_tz())
    old = mails(db, t['task_id'])
    out = db.write(board.move_task, t['task_id'], '2026-10-30', S, NOW + 60, board_tz())
    assert out['due_day'] == '2026-10-30'
    now = mails(db, t['task_id'])
    assert [m['kind'] for m in now if m['status'] == 'scheduled'].count('start') == [m['kind'] for m in old
                                                                                     if m['status'] == 'scheduled'].count('start')
    assert any(m['kind'] == 'meeting' and m['status'] == 'scheduled' and m['send_at'] > at('2026-10-29', 0) for m in now)
    assert 'now due Fri 30 Oct' in detail(db, cid)['events'][0]['text']
    with pytest.raises(board.BoardError, match='after today'):
        db.write(board.move_task, t['task_id'], '2026-10-01', S, NOW, board_tz())
    db.write(board.cancel_task, t['task_id'], NOW)
    with pytest.raises(board.BoardError, match='done or gone'):
        db.write(board.move_task, t['task_id'], '2026-11-30', S, NOW, board_tz())


def board_tz():
    from test_board import TZ
    return TZ


def mails(db, task_id):
    return db.read(lambda c: [dict(r) for r in c.execute('SELECT * FROM board_mail WHERE task_id=? ORDER BY send_at', (task_id,))])


def test_putting_a_card_back_restores_its_deadline(db):
    cid = add(db)
    ids = people(db, ('Sara', 'sara@example.com'))
    t = db.write(board.set_task, cid, 'sourcing', {'days': 10, 'notify': ids}, S, NOW, board_tz())
    db.write(board.archive_card, cid, True, NOW + 60)
    assert all(m['status'] != 'scheduled' for m in mails(db, t['task_id']))
    db.write(board.archive_card, cid, False, NOW + 120)
    assert any(m['status'] == 'scheduled' for m in mails(db, t['task_id']))
    assert detail(db, cid, NOW + 120)['card']['task']['id'] == t['task_id']


def test_the_timeline_lists_board_dates(db):
    cid = add(db)
    order(db, cid)
    db.write(board.set_line, cid, 1, 'US', {'steps': {'deposit': '2026-10-05'}}, S, NOW)
    db.write(board.set_task, cid, 'launch', {'days': 20}, S, NOW, board_tz())
    tl = board.timeline(db, S, NOW)
    kinds = {e['kind'] for e in tl['events']}
    assert {'step', 'eta', 'due'} <= kinds and tl['today'] == '2026-10-12'


def test_saved_filters(db):
    out = db.write(board.save_filter, {'name': 'Late AU', 'mk': 'AU', 'overdue': True, 'stage': 'nope', 'tag': 'x'})
    assert out == [{'name': 'Late AU', 'q': '', 'mk': 'AU', 'stage': '', 'overdue': True, 'tag': 'x'}]
    db.write(board.save_filter, {'name': 'Late AU', 'q': 'bed'})
    assert [f['q'] for f in db.read(board.saved_filters)] == ['bed']
    assert db.write(board.save_filter, None, 'Late AU') == []
    with pytest.raises(board.BoardError):
        db.write(board.save_filter, {'name': ' '})


def test_the_last_day_to_order_for_the_season():
    sea = rules.countdown([{'kind': 'order_by', 'day': '2026-10-20', 'market': 'US', 'mode': 'sea'}], TODAY)
    assert sea['text'] == 'Order in 8 days' and sea['tone'] == 'line' and sea['market'] == 'US'
    assert rules.countdown([{'kind': 'order_by', 'day': '2026-10-15', 'mode': 'air'}], TODAY)['text'] == 'Order in 3 days by air'
    now = rules.countdown([{'kind': 'order_by', 'day': '2026-10-12'}, {'kind': 'eta', 'day': '2026-10-13'}], TODAY)
    assert now['text'] == 'Order today' and now['tone'] == 'bad'
    ev = rules.timeline_events([{'id': 1, 'title': 'Bed', 'order_by': [{'market': 'AU', 'day': '2026-11-02', 'mode': 'air',
                                                                        'peak': 'Dec'}]}], TODAY)
    assert ev == [{'card_id': 1, 'title': 'Bed', 'color': 'blue', 'kind': 'order_by', 'day': '2026-11-02',
                   'label': 'Last day to order (by air) for the Dec peak', 'market': 'AU', 'late': False, 'edit': None}]


def test_a_seasonal_product_shows_when_to_order_until_its_deposit_is_paid(db):
    from engine import planner
    si = [0.8, 0.9, 1.4, 1.7, 1.2, 0.9, 0.85, 0.85, 0.85, 0.85, 0.85, 0.85]
    db.write(storage.save_trends_feature, 'calming dog bed', 'US', {'label': 'Seasonal', 'seasonality': {
        'si': si, 'strength': 0.7, 'peak_month': 4, 'reliable': True}})
    cid = add(db)
    today = dt.date.fromtimestamp(NOW)
    p = planner.plan(si, 0.7, True, 4, today.isoformat(), market='US')
    mode = {'ok': 'sea', 'next_year': 'sea', 'too_late': 'air'}[p['status']]
    cd = board.state(db, S, NOW)['cards'][0]['countdown']
    assert cd['kind'] == 'order_by' and cd['day'] == p[mode]['order_by'] and cd['market'] == 'US'
    ev = [e for e in board.timeline(db, S, NOW)['events'] if e['kind'] == 'order_by']
    assert len(ev) == 1 and ev[0]['day'] == p[mode]['order_by'] and ev[0]['label'].endswith('for the Apr peak')
    db.write(board.set_order, cid, 1, {'unit_cost': 6}, S, NOW)
    db.write(board.set_line, cid, 1, 'US', {'steps': {'deposit': today.isoformat()}}, S, NOW)
    assert not [e for e in board.timeline(db, S, NOW)['events'] if e['kind'] == 'order_by']
    assert (board.state(db, S, NOW)['cards'][0]['countdown'] or {}).get('kind') != 'order_by'
    db.write(storage.save_trends_feature, 'garden hose', 'US', {'label': 'Evergreen', 'seasonality': {
        'si': [1.0] * 12, 'strength': 0.1, 'peak_month': None, 'reliable': False}})
    hose = add(db, title='Garden hose', kw='garden hose')
    assert next(c for c in board.state(db, S, NOW)['cards'] if c['id'] == hose)['countdown'] is None   # no season, no date


def test_amazon_is_asked_every_four_hours_unless_switched_off():
    import collectors
    import scheduler
    s = {**S, 'auto_refresh': True}
    live = [x for x in collectors.task_specs(s, []) if x['kind'] == 'board_live']
    assert len(live) == 1 and live[0]['every_s'] == 4 * 3600 == scheduler.EVERY['board_live']
    assert not any(x['kind'] == 'board_live' for x in collectors.task_specs({**s, 'board_live': False}, []))
