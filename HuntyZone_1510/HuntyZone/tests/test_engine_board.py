"""Product board rules: stage completeness, order totals, locks, stock colours, sell rate and re-order dates."""
import datetime as dt
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import board as rules  # noqa: E402
from engine import planner  # noqa: E402

PER_USD = {'USD': 1.0, 'AUD': 1.5, 'AED': 3.6725, 'EUR': 0.9}
DAY = 86400


def line(mk, units=None, cost=None, cur='USD', mode=None):
    return {'market': mk, 'units': units, 'ship_cost': cost, 'ship_cur': cur, 'ship_mode': mode}


def test_an_order_is_complete_with_a_price_and_every_markets_numbers():
    empty = rules.sourcing_status({'unit_cost': None, 'cost_cur': 'USD'}, [], PER_USD)
    assert not empty['complete'] and empty['missing'] == ['the price per unit', 'at least one market']
    o = {'unit_cost': 6, 'cost_cur': 'USD'}
    half = rules.sourcing_status(o, [line('US', 300, 450, mode='sea'), line('AU', 200)], PER_USD)
    assert half['missing'] == ['the shipment type to Australia', 'the shipping cost to Australia']
    assert half['total_usd'] is None and half['known_usd'] == 300 * 6 + 450 + 200 * 6 and 0 < half['progress'] < 1
    full = rules.sourcing_status(o, [line('US', 300, 450, mode='sea'), line('AU', 200, 300, 'AUD', 'air')], PER_USD)
    assert full['complete'] and full['missing'] == [] and full['progress'] == 1 and full['units'] == 500
    au = full['markets']['AU']
    assert au['ship_usd'] == 200 and au['total_usd'] == 1400 and au['landed_unit_usd'] == 7 and au['mode'] == 'air'
    assert full['total_usd'] == 300 * 6 + 450 + 1400


def test_prices_in_other_currencies_are_converted():
    st = rules.sourcing_status({'unit_cost': 9, 'cost_cur': 'AUD'}, [line('AE', 100, 367.25, 'AED', 'sea')], PER_USD)
    assert st['markets']['AE']['unit_usd'] == 6 and st['markets']['AE']['ship_usd'] == 100 and st['total_usd'] == 700


def test_launch_needs_every_hint_handled_and_a_live_day():
    hints = [{'status': 'done'}, {'status': 'adjusted'}, {'status': 'todo'}]
    s = rules.launch_status(hints, {})
    assert not s['complete'] and s['missing'] == ['1 hint to check', 'the day it went live on Amazon']
    s = rules.launch_status([dict(h, status='skip') for h in hints], {'US': '2026-10-03', 'AU': None})
    assert s['complete'] and s['live'] == {'US': '2026-10-03'} and s['progress'] == 1
    assert rules.launch_status([], {'US': '2026-10-03'})['complete']


def test_stages_unlock_in_order():
    assert rules.unlocked({}) == ['idea', 'precheck']
    assert rules.unlocked({'idea_done': 1}) == ['idea', 'precheck', 'approval']
    assert rules.unlocked({'idea_done': 1, 'approved': 1}) == ['idea', 'precheck', 'approval', 'sourcing']
    assert rules.unlocked({'idea_done': 1, 'approved': 1, 'order_ready': 1}) == ['idea', 'precheck', 'approval', 'sourcing', 'launch']
    assert rules.unlocked({'idea_done': 1, 'approved': 1, 'order_ready': 1, 'launched': 2}) == list(rules.STAGES)


def test_an_idea_needs_the_asin_and_the_alibaba_link():
    assert rules.idea_status(None, None) == {'complete': False, 'done': 0, 'total': 2, 'progress': 0.0,
                                             'missing': ['the Amazon ASIN', 'the Alibaba link']}
    assert rules.idea_status('B0DOGBED01', '')['missing'] == ['the Alibaba link']
    s = rules.idea_status('B0DOGBED01', 'https://www.alibaba.com/product-detail/1.html')
    assert s['complete'] and s['progress'] == 1 and s['missing'] == []


def test_sell_rate_from_counts_or_from_the_arrival():
    t0 = time.mktime((2026, 10, 1, 12, 0, 0, 0, 0, -1))
    counts = [(t0, 500), (t0 + 5 * DAY, 450), (t0 + 10 * DAY, 400)]
    assert rules.sell_rate(counts) == pytest.approx(10)
    assert rules.sell_rate([(t0, 500), (t0 + DAY, 490)]) is None                 # too close together, no arrival
    assert rules.sell_rate([(t0 + 10 * DAY, 400)], sent=500, arrived='2026-10-01') == pytest.approx(10)
    assert rules.sell_rate([(t0 + 10 * DAY, 520)], sent=500, arrived='2026-10-01') == 0
    assert rules.sell_rate([]) is None
    old = [(t0 - 100 * DAY, 2000)] + counts                                     # counts older than 45 days are left out
    assert rules.sell_rate(old) == pytest.approx(10)


def test_forecast_and_stock_colours():
    f = rules.forecast(150, 10, 54, '2026-10-12')
    assert f == {'days_left': 15, 'sellout': '2026-10-27', 'reorder_by': '2026-09-03'}
    assert rules.forecast(150, None, 54, '2026-10-12')['reorder_by'] is None
    today = dt.date(2026, 10, 12)
    assert rules.stock_status(500, 150, 30, None, today) == {'pct': 30.0, 'level': 'red', 'order_now': False}
    assert rules.stock_status(500, 151, 30, None, today)['level'] == 'amber'
    assert rules.stock_status(500, 250, 30, None, today)['level'] == 'amber'
    assert rules.stock_status(500, 400, 30, None, today)['level'] == 'green'
    assert rules.stock_status(500, 400, 30, '2026-10-20', today)['level'] == 'amber'      # re-order day close
    assert rules.stock_status(500, 400, 30, '2026-10-12', today)['order_now'] is True
    assert rules.stock_status(None, 10, 30)['level'] == 'unknown'


def test_due_days_and_targets():
    today = dt.date(2026, 10, 12)
    assert rules.due_state('2026-10-11', False, today) == 'late'
    assert rules.due_state('2026-10-15', False, today) == 'soon'
    assert rules.due_state('2026-10-20', False, today) == 'ok'
    assert rules.due_state('2026-10-11', True, today) is None and rules.due_state(None) is None
    assert rules.target_progress(10, 300) == {'month': 304, 'pct': 101}
    assert rules.target_progress(None, 300) is None and rules.target_progress(5, 0) is None


def test_lead_days_add_production_buffer_transit_and_check_in():
    assert planner.lead_days('US', 'sea') == 30 + 7 + 40 + 10
    assert planner.lead_days('AU', 'air') == 30 + 7 + 10 + 7
    assert planner.lead_days('US', 'sea', q4=True) == 30 + 7 + 40 + 21
    assert planner.lead_days('XX', 'boat') == planner.lead_days('US', 'sea')


def test_the_next_step_is_the_one_thing_left_to_do_on_a_product():
    idea, empty = rules.idea_status(None, None), rules.sourcing_status({}, [], PER_USD)
    ap = rules.approval_status([{'text': 'a', 'done': True}, {'text': 'b'}, {'text': 'c'}])
    ls = rules.launch_status([{'status': 'done'}, {'status': 'open'}], {})

    def nxt(ms, **kw):
        return rules.next_step(ms, kw.get('idea', idea), kw.get('ap', ap), kw.get('src', empty), kw.get('ls', ls),
                               kw.get('stocks', ()), kw.get('reorders', ()))

    assert nxt({}) == {'text': 'Add the Amazon ASIN and the Alibaba link', 'tab': 'idea', 'tone': 'todo', 'order': None}
    assert nxt({}, idea=rules.idea_status('B0X', None))['text'] == 'Add the Alibaba link'
    assert nxt({'idea_done': 1}) == {'text': 'Tick 2 approval checks', 'tab': 'approval', 'tone': 'todo', 'order': None}
    assert nxt({'idea_done': 1}, ap=rules.approval_status([]))['text'] == 'Add an approval check'
    ms = {'idea_done': 1, 'approved': 2}
    assert nxt(ms) == {'text': 'Add the price per unit and 1 more', 'tab': 'sourcing', 'tone': 'todo', 'order': 1}
    src = rules.sourcing_status({'unit_cost': 6}, [{'market': 'AU', 'units': 300, 'ship_mode': 'sea'}], PER_USD)
    assert nxt(ms, src=src)['text'] == 'Add the shipping cost to Australia'
    ms['order_ready'] = 3
    assert nxt(ms)['text'] == 'Check 1 launch hint' and nxt(ms)['tab'] == 'launch'
    assert nxt(ms, ls=rules.launch_status([], {}))['text'] == 'Set the day it went live on Amazon'
    ms['launched'] = 4
    assert nxt(ms) is None                                                         # nothing waits
    red = [{'market': 'US', 'level': 'green', 'order_now': False}, {'market': 'AE', 'level': 'red', 'order_now': False}]
    assert nxt(ms, stocks=red) == {'text': 'Low stock in UAE: plan a re-order', 'tab': 'reorder', 'tone': 'warn',
                                   'order': None}
    now = red + [{'market': 'AU', 'level': 'amber', 'order_now': True}]
    assert nxt(ms, stocks=now)['text'] == 'Re-order now for Australia' and nxt(ms, stocks=now)['tone'] == 'bad'
    open_ = [{'n': 2, 'missing': ['units for USA', 'the shipping cost to USA']}]
    assert nxt(ms, stocks=now, reorders=open_) == {'text': 'Finish re-order #2: add units for USA and 1 more',
                                                   'tab': 'sourcing', 'tone': 'todo', 'order': 2}
