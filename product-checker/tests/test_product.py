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
