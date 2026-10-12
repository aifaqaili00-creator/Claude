"""engine/launch.py: the sales a new listing might win, what a first order means, and which market to start in."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import launch as L  # noqa: E402


def test_a_newcomer_wins_less_where_the_top_listings_have_many_reviews():
    few = L.newcomer_units({'v': 100, 'lo': 60, 'hi': 160}, 50, 'US')
    many = L.newcomer_units({'v': 100}, 5000, 'US')
    unknown = L.newcomer_units(100, None, 'US')
    assert few['v'] > unknown['v'] > many['v'] and many['factor'] == 0.3 and unknown['factor'] == 0.7
    assert few['lo'] < few['v'] < few['hi'] and few['conf'] == 'low' and 'about 50 reviews' in few['why']
    assert L.newcomer_units(None, 5, 'US') is None and L.newcomer_units({'v': 0}, 5, 'US') is None


def test_a_first_order_in_numbers():
    econ = {'Landed': 6.0, 'Profit': 4.0, 'Margin': 0.2, 'fx': 1.55}
    s = L.simulate(300, econ, {'v': 100})
    assert s['cash'] == 1800 and s['profit_total'] == 1200 and s['payback_sales'] == 180 and s['months'] == 3.0
    assert s['profit_month'] == 400 and s['profit_month_usd'] == round(400 / 1.55, 2) and s['notes'] == []
    slow = L.simulate(1000, econ, {'v': 20})
    assert slow['months'] == 50.0 and slow['notes'][0].startswith('slow')
    loss = L.simulate(100, {'Landed': 9.0, 'Profit': -1.0}, None)
    assert loss['payback_sales'] is None and loss['months'] is None and 'loses money' in loss['notes'][0]


def test_the_pick_needs_profit_and_a_quick_sell_out():
    us = L.simulate(300, {'Landed': 6, 'Profit': 4, 'fx': 1.0}, {'v': 100})       # US$400 a month, 3 months
    au = L.simulate(300, {'Landed': 6, 'Profit': 8, 'fx': 1.55}, {'v': 100})      # A$800 = about US$516 a month
    ae = L.simulate(300, {'Landed': 6, 'Profit': 9, 'fx': 1.0}, {'v': 10})        # 30 months: too slow
    p = L.pick({'US': us, 'AU': au, 'AE': ae})
    assert p['market'] == 'AU' and 'US$516' in p['why'] and 'about 3 months' in p['why']
    assert L.pick({'AE': ae}) is None and L.pick({}) is None
