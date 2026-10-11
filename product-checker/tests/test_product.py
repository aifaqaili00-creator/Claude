"""Product pages: parsing, storage with calibration points, the curve refit and the product report."""
import datetime as dt
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import product  # noqa: E402
import reports  # noqa: E402
import storage  # noqa: E402

RAW = {
    'title': 'Moving Bags Heavy Duty, 8 Pack', 'price': '$39.99', 'rating': '4.6 out of 5 stars',
    'reviews': '1,234 ratings', 'badge': '1K+ bought in past month', 'offers': 'New (12) from $35.00',
    'delivery': 'FREE delivery Tomorrow, October 11', 'sold_by': 'Amazon.com', 'ships_from': 'Amazon.com', 'variations': 3,
    'image': 'http://img', 'bsr_links': ['/gp/bestsellers/home-garden/ref=pd_zg_ts_home-garden', '/gp/bestsellers/home-garden/3744391/ref=x'],
    'rows': {'Best Sellers Rank': '#1,234 in Home & Kitchen (See Top 100 in Home & Kitchen) #5 in Moving Boxes',
             'Date First Available': 'March 3, 2023', 'Product Dimensions': '30 x 20 x 10 inches; 2.5 Pounds'},
}


def test_parse_product_us():
    p = product.parse_product(RAW, 'US', dt.date(2026, 10, 10))
    assert p['root_rank'] == 1234 and p['root_name'] == 'Home & Kitchen' and p['sub_ranks'] == [{'rank': 5, 'name': 'Moving Boxes'}]
    assert p['root_node'] == 'home-garden' and p['badge_low'] == 1000 and p['price'] == 39.99 and p['reviews'] == 1234
    assert p['offers'] == 12 and p['sold_by_amazon'] == 1 and p['delivery_days'] == 1 and p['origin'] == 'local'
    assert p['dfa_day'] == '2023-03-03' and p['pkg_cm'] == [76.2, 50.8, 25.4] and p['pkg_kg'] == 1.134
    assert product.page_status(p) == 'ok'


def test_parse_product_au_formats_and_missing_bits():
    raw = {'title': 'Thing', 'rows': {'Best Sellers Rank:': '89,021 in Home (See Top 100 in Home)',
                                      'Date First Available': '3 March 2023', 'Package Dimensions': '30 x 20 x 10 cm; 500 g'}}
    p = product.parse_product(raw, 'AU', dt.date(2026, 10, 10))
    assert p['root_rank'] == 89021 and p['dfa_day'] == '2023-03-03' and p['pkg_cm'] == [30, 20, 10] and p['pkg_kg'] == 0.5
    assert p['badge_low'] is None and p['offers'] is None and p['origin'] == 'unknown'
    assert product.page_status({**p, 'root_rank': None}) == 'partial'
    assert product.page_status(product.parse_product({}, 'AU')) == 'empty_suspect'
    assert product.parse_ranks('') == [] and product.parse_date('soon') is None and product.parse_dims('n/a') == (None, None)


def test_size_pasted_from_a_supplier_page():
    alibaba = 'Selling Units: Single item\nSingle package size: 30X20X10 cm\nSingle gross weight: 0.500 kg'
    assert product.parse_size_text(alibaba) == ([30, 20, 10], 0.5)
    assert product.parse_size_text('Size: 20*35*8cm  N.W.: 0.45kg  G.W.: 0.6kg') == ([35, 20, 8], 0.6)        # gross wins
    assert product.parse_size_text('carton 30 x 20 x 10 cm, gross weight 650 g') == ([30, 20, 10], 0.65)
    assert product.parse_size_text('15.7 x 11.8 x 3.9 inches; 1.1 Pounds') == ([39.88, 29.97, 9.91], 0.499)
    assert product.parse_size_text('600g') == (None, 0.6) and product.parse_size_text('hello') == (None, None)


def test_reviews_as_the_page_shows_them():
    items = [
        {'id': 'customer_review-R1AAAAAAAAAA1', 'stars': '1.0 out of 5 stars', 'title': '1.0 out of 5 stars Fell apart in a week',
         'body': 'The zip broke. The media could not be loaded. Read more', 'date': 'Reviewed in Australia on 3 March 2025',
         'helpful': '12 people found this helpful', 'verified': True, 'local': True},
        {'id': 'R2BBBBBBBBBB2', 'stars': '2', 'title': 'Too small', 'body': 'Smaller than the photos',
         'date': 'Reviewed in the United States on March 5, 2025', 'helpful': 'One person found this helpful', 'local': True},
        {'id': 'R3CCCCCCCCCC3', 'stars': '', 'title': 'No stars', 'body': 'x'},                    # no rating: skipped
        {'id': 'R1AAAAAAAAAA1', 'stars': '1.0 out of 5 stars', 'title': 'again', 'body': 'x'},     # the same review twice
        {'id': '', 'stars': '5,0 out of 5 stars', 'title': 'Love it', 'body': 'Great', 'date': '', 'helpful': ''},
    ]
    r = product.parse_reviews(items, 'AU')
    assert r[0] == {'id': 'R1AAAAAAAAAA1', 'stars': 1.0, 'title': 'Fell apart in a week', 'body': 'The zip broke.',
                    'day': '2025-03-03', 'country': 'Australia', 'local': True, 'helpful': 12, 'verified': True}
    assert (r[1]['stars'], r[1]['day'], r[1]['local'], r[1]['helpful'], r[1]['verified']) == (2.0, '2025-03-05', False, 1, False)
    assert len(r) == 3 and r[2]['id'].startswith('h') and r[2]['stars'] == 5.0 and r[2]['local'] is True
    p = product.parse_product({**RAW, 'review_items': items[:1], 'signed_in': False}, 'US', dt.date(2026, 10, 10))
    assert p['review_items'][0]['local'] is False and p['signed_in'] is False                 # an Australian review on amazon.com
    assert product.parse_product(RAW, 'US')['review_items'] is None
    assert product.critical_url('AE', 'B0X') == 'https://www.amazon.ae/product-reviews/B0X/?filterByStar=critical&sortBy=helpful' \
        '&reviewerType=all_reviews&language=en_AE'


@pytest.fixture
def db(tmp_path):
    d = storage.DB(tmp_path / 'm.db').open()
    yield d
    d.close()


def simulate(db, n_asins=40, days=30, seed=1):
    """Product pages whose badges follow ln S = 11.5 - 0.85 ln R (AU-ish), read daily."""
    import random
    rnd = random.Random(seed)
    from engine import badge
    now = 1_791_590_400
    for a in range(n_asins):
        r0 = math.exp(rnd.uniform(math.log(200), math.log(60000)))
        for d in range(days):
            r = r0 * math.exp(rnd.gauss(0, 0.15))
            s = math.exp(11.5 - 0.85 * math.log(r) + rnd.gauss(0, 0.3))
            lab = max([L for L in badge.LADDER['AU'] if L <= s], default=None)
            p = {'title': 'P%d' % a, 'root_rank': int(r), 'root_node': 'home-garden', 'badge_low': lab, 'price': 30.0,
                 'status': 'ok'}
            db.write(storage.save_product, 'AU', 'B%09d' % a, p, now - (days - d) * 86400)
    return now


def test_storage_calibration_curve_and_report(db):
    from engine import curve
    now = simulate(db)
    obs = db.read(storage.calib_obs, 'AU', now)
    assert len(obs) > 200 and all(o['kind'] == 'badge' for o in obs)
    fit = curve.fit_market(obs, 'AU')
    db.write(storage.save_curve, 'AU', fit, now)
    assert fit['status'] == 'calibrated' and abs(fit['beta'] + 0.85) < 0.2
    r = reports.product_report(db, 'AU', 'B000000003', config.defaults(), now)
    json.dumps(r, allow_nan=False)
    assert r['monthly'] and r['monthly'][-1]['units']['lo'] <= r['monthly'][-1]['units']['v'] <= r['monthly'][-1]['units']['hi']
    assert r['curve']['status'] == 'calibrated' and r['units_now']['v'] > 0 and r['info']['root_node'] == 'home-garden'
    assert r['monthly'][-1]['revenue']['v'] == pytest.approx(r['monthly'][-1]['units']['v'] * 30.0, rel=1e-6)
    cal = reports.calibration(db)
    assert cal['AU']['points']['badge'] > 200 and cal['US']['curve'] is None


def test_report_without_curve_or_data(db):
    r = reports.product_report(db, 'US', 'B000000000', config.defaults())
    json.dumps(r, allow_nan=False)
    assert r['monthly'] == [] and r['now'] is None and r['curve'] is None


def test_tracked_asins(db):
    import test_storage as ts
    db.write(storage.save_serp, ts.summary(), 'user', None, 1, '2000')
    db.write(storage.add_watch, 'keyword', 'AU', 'moving bags')
    db.write(storage.add_watch, 'asin', 'AU', 'B0WATCHED1')
    t = db.read(storage.tracked_asins, 'AU')
    assert t[0] == 'B0WATCHED1' and 'A1' in t and 'A6' not in t and len(t) == 6      # A6 is sponsored
