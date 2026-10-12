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
    assert rules.unlocked({}) == ['sourcing']
    assert rules.unlocked({'order_ready': 1}) == ['sourcing', 'launch']
    assert rules.unlocked({'order_ready': 1, 'launched': 2}) == ['sourcing', 'launch', 'reorder']


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
