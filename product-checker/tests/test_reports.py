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
