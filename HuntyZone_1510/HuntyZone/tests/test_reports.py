"""Keyword report payload: monthly priority (Helium 10 over badges), gaps kept, backcast/forecast, JSON-safe."""
import json
import math
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import reports  # noqa: E402
import storage  # noqa: E402
import trends  # noqa: E402
from test_storage import ROWS, summary  # noqa: E402

DAY = 86400
SETTINGS = config.defaults()


@pytest.fixture
def db(tmp_path):
    d = storage.DB(tmp_path / 'm.db').open()
    yield d
    d.close()


def ts(y, m, d):
    import datetime as dt
    return dt.datetime(y, m, d, 12, tzinfo=dt.timezone.utc).timestamp()


def fill(db):
    for when in (ts(2026, 8, 10), ts(2026, 9, 5), ts(2026, 9, 25), ts(2026, 10, 8)):
        db.write(storage.save_serp, summary(at=when), 'user', None, 1, '2000')
    db.write(storage.save_serp, {'market': 'AU', 'keyword': 'moving bags', 'status': 'captcha', 'error': 'captcha',
                                 'checked_at': ts(2026, 10, 1)})
    rows = [{'asin': 'B%02d' % i, 'title': 'Moving bags %d' % i, 'price': 30, 'sales_asin': 200, 'sales_parent': 300,
             'revenue': 6000, 'sponsored': 0} for i in range(5)] + [{'asin': 'AD', 'title': 'ad', 'sales_asin': 999,
                                                                     'sponsored': 1}]
    db.write(storage.save_import, 'xray', 'AU', 'moving bags heavy duty', 'x.csv', 'sha-x', rows, ts(2026, 9, 15))


def test_monthly_prefers_helium10_and_keeps_both_for_cross_checking(db):
    fill(db)
    r = reports.keyword_report(db, 'AU', 'Moving Bags', SETTINGS, now=ts(2026, 10, 9))
    by = {m['month']: m for m in r['monthly']}
    assert set(by) == {'2026-08', '2026-09', '2026-10'}
    assert by['2026-09']['method'] == 'h10' and by['2026-09']['units']['v'] == 1000          # ads excluded
    assert by['2026-09']['units']['basis'] == 'reported' and by['2026-09']['cross']['badge']
    assert by['2026-10']['method'] == 'badge' and by['2026-10']['units']['basis'] == 'estimated'
    assert by['2026-10']['units']['lo'] <= by['2026-10']['units']['v'] <= by['2026-10']['units']['hi']


def test_blocked_searches_are_gaps_not_zeros(db):
    fill(db)
    r = reports.keyword_report(db, 'AU', 'moving bags', SETTINGS)
    assert [g['status'] for g in r['supply']['gaps']] == ['captcha']
    assert len(r['supply']['points']) == 4 and all(p['local'] is not None for p in r['supply']['points'])
    assert r['supply']['points'][0]['new'] is None and r['supply']['points'][1]['new'] == 0
    assert len(r['demand']['amazon']) == 4


def test_report_is_json_safe_and_has_the_latest_search(db):
    fill(db)
    r = reports.keyword_report(db, 'AU', 'moving bags', SETTINGS)
    json.dumps(r, allow_nan=False)
    assert r['now']['total'] == 6 and r['competition']['top'] and r['competition']['review_barrier'] is not None
    assert r['markets']['AU']['n_snapshots'] == 5 and r['markets']['US']['total'] is None
    assert r['provenance']['snapshots'][-1]['status'] in storage.VALID + ('captcha',)


def test_empty_report(db):
    r = reports.keyword_report(db, 'US', 'nothing here', SETTINGS)
    json.dumps(r, allow_nan=False)
    assert r['monthly'] == [] and r['now'] is None and r['trends']['status'] == 'none'


def test_markets_carry_the_typical_price(db):
    fill(db)
    r = reports.keyword_report(db, 'AU', 'moving bags', SETTINGS)
    assert r['markets']['AU']['price_med'] == 20.0 and r['markets']['US']['price_med'] is None    # median of page 1, no ads


def test_size_suggestion_from_the_top_listings(db):
    assert reports.size_suggestion(db.read(lambda c: c), 'AU', 'moving bags')['searched'] is False
    fill(db)
    for asin, dims, kg in (('A1', [30.0, 20.0, 10.0], 0.5), ('A3', [40.0, 30.0, 10.0], 1.5)):
        db.write(storage.save_product, 'AU', asin, {'title': 'x', 'root_rank': 10, 'status': 'ok', 'pkg_cm': dims, 'pkg_kg': kg})
    db.write(storage.save_import, 'xray', 'AU', 'moving bags', 'w.csv', 'sha-w', [{'asin': 'A2', 'title': 't', 'weight_kg': 0.9}],
             ts(2026, 9, 20))
    sz = db.read(reports.size_suggestion, 'AU', 'Moving Bags')
    assert sz['dims_cm'] == [35.0, 25.0, 10.0] and sz['kg'] == 0.9 and sz['n'] == 2      # A2's weight is from Helium 10
    assert sz['asins'] == ['A1', 'A3'] and sz['missing'] == ['A2', 'A4', 'A5']           # the ad (A6) is never used
    assert reports.money(db, 'AU', 'moving bags', SETTINGS)['size'] == sz


def test_who_sells_uses_product_pages_helium10_and_the_sales_rank(db, monkeypatch):
    from engine import curve
    assert reports.who_sells(db, 'AU', 'moving bags')['searched'] is False
    fill(db)
    now = ts(2026, 10, 9)
    db.write(storage.save_import, 'xray', 'AU', 'moving bags', 'x2.csv', 'sha-x2', [{'asin': 'A5', 'title': 't', 'brand': 'Bagz',
             'seller_country': 'cn', 'fulfillment': 'FBM', 'sales_asin': 150}], ts(2026, 9, 20))
    db.write(storage.save_product, 'AU', 'A4', {'title': 'x', 'root_rank': 500, 'root_node': 'home', 'status': 'ok', 'seller_id': 'SX1',
                                                'fulfillment': 'fba', 'brand': 'Packit', 'buybox_seller': 'Packit AU'}, ts(2026, 10, 8))
    db.write(storage.save_seller, 'AU', 'SX1', {'name': 'Packit', 'country': 'AU', 'status': 'ok'}, ts(2026, 10, 8))
    db.write(storage.save_curve, 'AU', {'status': 'calibrated'}, now)
    monkeypatch.setattr(curve, 'predict', lambda cv, node, rank: {'v': 321.0, 'lo': 200.0, 'hi': 400.0, 'basis': 'estimated'})
    w = reports.who_sells(db, 'AU', 'Moving Bags', now=now)
    json.dumps(w, allow_nan=False)
    by = {x['asin']: x for x in w['listings']}
    assert list(by) == ['A1', 'A2', 'A3', 'A4', 'A5', 'A7'] and by['A1']['units']['src'] == 'badge'      # no ad (A6)
    assert by['A4']['units'] == {'v': 321.0, 'lo': 200.0, 'hi': 400.0, 'src': 'rank'} and by['A4']['seller'] == 'Packit'
    assert (by['A4']['country'], by['A4']['ships'], by['A4']['brand']) == ('AU', 'fba', 'Packit') and by['A4']['seller_url'].endswith('SX1')
    assert by['A5']['units'] == {'v': 150, 'lo': None, 'hi': None, 'src': 'h10'}
    assert (by['A5']['country'], by['A5']['ships'], by['A5']['brand'], by['A5']['seller']) == ('CN', 'fbm', 'Bagz', None)
    assert w['todo'] == {'pages': ['A1', 'A2', 'A3', 'A5', 'A7'], 'sellers': []} and w['read_at'] == ts(2026, 10, 8)
    assert 'sentence' in reports.money(db, 'AU', 'moving bags', SETTINGS)
    assert reports.sentence(db, 'AU', 'moving bags', SETTINGS).startswith(('Strong in Australia', 'Worth a look in Australia',
                                                                          'Weak in Australia', 'Skip in Australia'))
    assert reports.sentence(db, 'US', 'moving bags', SETTINGS).startswith('Not searched in the USA yet')


def test_top_negative_reviews_of_the_best_listings(db):
    assert reports.reviews_for(db, 'AU', 'moving bags')['searched'] is False
    fill(db)
    now = ts(2026, 10, 9)

    def rv(i, stars, helpful, local=True):
        return {'id': 'R%010d' % i, 'stars': stars, 'title': 'Problem %d' % i, 'body': 'b', 'day': '2026-09-01',
                'country': 'Australia' if local else 'the United States', 'local': local, 'helpful': helpful, 'verified': True}
    db.write(storage.save_reviews, 'AU', 'A1', [rv(1, 1, 50), rv(2, 2, 40), rv(3, 1, 30), rv(4, 1, 20), rv(9, 5, 999)],
             'critical', now - DAY)
    db.write(storage.save_reviews, 'AU', 'A2', [rv(5, 3, 500), rv(6, 2, 1, local=False)], 'page', now - 20 * DAY)
    db.write(storage.save_product, 'AU', 'A3', {'title': 'x', 'root_rank': 10, 'status': 'ok', 'reviews': 80,
                                                'review_items': [rv(7, 1, 5)]}, now)
    db.write(storage.save_product, 'AU', 'A4', {'title': 'x', 'root_rank': 10, 'status': 'ok', 'reviews': 80, 'review_items': []}, now)
    r = reports.reviews_for(db, 'AU', 'Moving Bags', now=now)
    json.dumps(r, allow_nan=False)
    assert [i['title'] for i in r['items']] == ['Problem 1', 'Problem 2', 'Problem 7', 'Problem 6', 'Problem 5']   # 2 a listing
    assert r['todo'] == ['A2', 'A4', 'A5'] and r['read'] == 3 and r['critical'] == 1 and r['read_at'] == now   # A2 is old, A4 showed none
    first = r['items'][0]
    assert first['listing'] == 'Title A1' and first['url'].endswith('/dp/A1') and 'filterByStar=critical' in first['all_url']
    assert first['local'] is True and first['verified'] is True
    one = reports.reviews_for(db, 'AU', asin='A1', now=now)
    assert [i['title'] for i in one['items']] == ['Problem 1', 'Problem 2', 'Problem 3', 'Problem 4'] and one['todo'] == []


def test_backcast_and_forecast_follow_the_trend_shape(db):
    fill(db)
    import fakeweb
    pts = [[int(p['time']), p['value'][0], p.get('isPartial', False)] for p in fakeweb.weekly('seasonal')]
    db.write(storage.save_trends_sample, 'moving bags', 'AU', trends.SERIES_TF, 'ok', pts, 'WEEK', None, ts(2026, 10, 9))
    data = trends.compute(db.read(storage.trends_samples, 'moving bags', 'AU'))
    db.write(storage.save_trends_feature, 'moving bags', 'AU', data)
    r = reports.keyword_report(db, 'AU', 'moving bags', SETTINGS)
    assert r['trends']['label'] and r['trends']['monthly']
    bc = r['backcast']
    assert bc and len(bc) <= reports.BACKCAST_MONTHS and bc[-1]['month'] < '2026-08'
    assert all(b['units']['basis'] == 'backcast' and b['units']['lo'] < b['units']['hi'] for b in bc)
    fc = r['forecast']
    assert len(fc) == reports.FORECAST_MONTHS and fc[0]['month'] == '2026-11'
    widths = [math.log(f['units']['hi'] / f['units']['lo']) for f in fc]
    assert widths == sorted(widths)                                     # the band widens


def test_backcast_needs_good_trends_quality():
    rows = [{'month': '2026-10', 'units': reports.est(100, 80, 120)}]
    assert reports.backcast(rows, [['2026-09', 50], ['2026-10', 60]], 0.3) == []
    assert reports.backcast(rows, [], 0.9) == []
    assert reports.forecast(rows, {'reliable': False, 'si': [1] * 12}, 0.1) == []


def test_keyword_matching():
    assert reports.same_keyword('moving bags', 'moving bags heavy duty')
    assert reports.same_keyword('Moving Bags!', 'moving bags')
    assert not reports.same_keyword('moving bags', 'storage bins')
    assert not reports.same_keyword('', 'x')


def test_library(db):
    fill(db)
    lib = reports.library(db)
    assert lib['keywords'][0]['kw_norm'] == 'moving bags' and lib['keywords'][0]['markets'] == ['AU']
    assert lib['imports'][0]['kind'] == 'xray' and lib['counts']['serp_snapshot'] == 5


def test_money_section_and_opportunities(db):
    fill(db)
    db.write(storage.add_watch, 'keyword', 'AU', 'moving bags')
    m = reports.money(db, 'AU', 'moving bags', SETTINGS)
    json.dumps(m, allow_nan=False)
    assert m['opportunity']['label'] in ('Strong', 'Worth a look', 'Weak', 'Skip') and m['economics']['Profit'] is not None
    assert m['plan']['status'] == 'unknown' and m['arbitrage'] == {}
    db.write(lambda c: c.execute("UPDATE watch SET cogs=3"))
    m2 = reports.money(db, 'AU', 'moving bags', SETTINGS)
    assert m2['cogs_usd'] == 3 and m2['metrics']['margin'] is not None
    ops = reports.opportunities(db, SETTINGS)
    assert ops and ops[0]['kw'] == 'moving bags'
    us = reports.money(db, 'US', 'moving bags', SETTINGS)
    assert set(us['arbitrage']) == {'AU', 'AE'} and us['arbitrage']['AE'] is None


def test_opportunity_map_uses_the_latest_good_search(db):
    fill(db)
    now = ts(2026, 10, 10)
    m = reports.opportunity_map(db, 'AU', now=now)
    json.dumps(m, allow_nan=False)
    assert len(m['points']) == 1                                            # one keyword, latest good search only
    p = m['points'][0]
    assert p['kw'] == 'moving bags' and p['demand'] > 0 and p['ts'] == ts(2026, 10, 8)      # the captcha after it is skipped
    assert p['per_local_seller'] == round(p['demand'] / p['local'], 1)
    assert reports.opportunity_map(db, 'AE', now=now)['points'] == []
    assert reports.opportunity_map(db, 'AU', now=now + 200 * DAY)['points'] == []          # too old


def test_check_summary_carries_demand_vs_sellers():
    s = summary()
    ps = s['per_seller']
    assert ps['sellers'] == s['total'] and ps['local'] == s['local'] and ps['demand']['badged'] == 3
