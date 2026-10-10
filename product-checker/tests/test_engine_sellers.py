"""Demand vs sellers on one search page: sales per seller, per local seller, and who takes the sales."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import badge, sellers  # noqa: E402


def r(asin, origin, bought=None, sponsored=False, price=30.0):
    return {'asin': asin, 'title': 'T ' + asin, 'price': price, 'origin': origin, 'speed': 'fast' if origin == 'local' else 'slow',
            'days': 2 if origin == 'local' else 14, 'bought': bought, 'sponsored': sponsored}


ROWS = [r('L1', 'local', 1000), r('O1', 'overseas', 100), r('O2', 'overseas', 50), r('U1', 'unknown', 200),
        r('L2', 'local'), r('O3', 'overseas'), r('AD', 'local', 5000, sponsored=True)]


def test_per_seller_numbers():
    p = sellers.per_seller(ROWS, 'AU')
    mid = sum(badge.badge_estimate(b, 'AU')['v'] for b in (1000, 100, 50, 200))
    assert p['sellers'] == 6 and p['local'] == 2 and p['overseas'] == 3 and p['unknown'] == 1     # the ad is not a seller
    assert p['demand']['v'] == pytest.approx(mid, abs=0.2) and p['demand']['badged'] == 4 and p['no_badge'] == 2
    assert p['per_seller']['v'] == pytest.approx(mid / 6, abs=0.1)
    assert p['per_local_seller']['v'] == pytest.approx(mid / 2, abs=0.1)
    assert p['per_seller']['lo'] <= p['per_seller']['v'] <= p['per_seller']['hi']
    assert [x['asin'] for x in p['listings']][:4] == ['L1', 'U1', 'O1', 'O2'] and p['listings'][-1]['units'] is None


def test_who_takes_the_sales():
    p = sellers.per_seller(ROWS, 'AU')
    assert p['local_share_of_sales'] + p['overseas_share_of_sales'] == pytest.approx(1, abs=0.002)
    assert p['local_share_of_sales'] > 0.8                    # 1K+ local vs 150 overseas; unknown origin left out
    assert 0 < p['top3_share'] <= 1


def test_no_local_sellers_and_no_badges():
    p = sellers.per_seller([r('O1', 'overseas', 300), r('O2', 'overseas')], 'AE')
    assert p['per_local_seller'] is None and p['overseas_share_of_sales'] == 1 and p['top3_share'] is None
    q = sellers.per_seller([r('O1', 'overseas'), r('L1', 'local')], 'US')
    assert q['demand']['v'] is None and q['per_seller'] is None and q['no_badge'] == 2
    assert sellers.per_seller([], 'US')['sellers'] == 0


def test_open_top_bucket_is_marked_assumed():
    p = sellers.per_seller([r('L1', 'local', 100000)], 'US')
    assert p['listings'][0]['units']['basis'] == 'assumed'
