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


def test_who_sells_the_top_listings():
    import datetime as dt

    def x(asin, seller, sid, country, ships, units, brand=None, launched=None):
        return {'asin': asin, 'seller': seller, 'seller_id': sid, 'country': country, 'ships': ships, 'brand': brand,
                'launched': launched, 'units': {'v': units} if units else None}
    w = sellers.who_sells([x('A1', 'Pecute', 'P1', 'CN', 'fba', 900, 'Pecute', '2026-03-01'),
                           x('A2', 'Pecute', 'P1', 'CN', 'fba', 300, 'Pecute', '2025-12-01'),
                           x('A3', 'Amazon', None, 'AU', 'amazon', 400, 'Amazon Basics', '2020-01-01'),
                           x('A4', 'Koala Co', 'K1', 'AU', 'fbm', 200, 'Koala', '2026-01-10'),
                           x('A5', 'Lucky', 'L1', 'CN', 'fbm', None, 'Lucky'), x('A6', None, None, None, None, 200)],
                          'AU', dt.date(2026, 10, 12))
    assert [(s['name'], s['listings'], s['share']) for s in w['sellers']] == [('Pecute', 2, 0.6), ('Amazon', 1, 0.2),
                                                                             ('Koala Co', 1, 0.1), ('Lucky', 1, 0.0)]
    assert w['basis'] == 'sales' and w['known'] == {'seller': 5, 'country': 4} and w['amazon'] == 1
    assert [(c['code'], c['listings']) for c in w['countries']] == [('CN', 3), ('AU', 1)]       # Amazon counted on its own
    assert w['ships'] == {'amazon': 1, 'fba': 2, 'fbm': 2, 'unknown': 1} and w['new'] == {'n': 3, 'known': 4}
    assert w['dominant'] == {'code': 'CN', 'n': 3, 'of': 6, 'ships': None} and w['brand_top']['name'] == 'Pecute'
    assert w['takeaway'] == 'Mostly Chinese sellers: 3 of the top 6, 60% of the sales. Amazon itself sells 1.'
    assert [(r['level'], r['text']) for r in w['reasons']] == [
        ('warn', 'Amazon itself sells 1 of the top 6.'), ('bad', 'One seller (Pecute) has 60% of the sales.'),
        ('warn', 'Sellers based in China have 3 of the top 6 listings: expect price competition.'),
        ('good', "2 of the top 6 are shipped by the seller (FBM): stock in Amazon's warehouse would deliver faster."),
        ('bad', 'One brand (Pecute) has 60% of the sales.')]
    empty = sellers.who_sells([], 'AU')
    assert empty['takeaway'] is None and empty['reasons'] == [] and empty['sellers'] == [] and empty['dominant'] is None


def test_no_local_sellers_and_no_badges():
    p = sellers.per_seller([r('O1', 'overseas', 300), r('O2', 'overseas')], 'AE')
    assert p['per_local_seller'] is None and p['overseas_share_of_sales'] == 1 and p['top3_share'] is None
    q = sellers.per_seller([r('O1', 'overseas'), r('L1', 'local')], 'US')
    assert q['demand']['v'] is None and q['per_seller'] is None and q['no_badge'] == 2
    assert sellers.per_seller([], 'US')['sellers'] == 0


def test_open_top_bucket_is_marked_assumed():
    p = sellers.per_seller([r('L1', 'local', 100000)], 'US')
    assert p['listings'][0]['units']['basis'] == 'assumed'
