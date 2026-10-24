"""Out of stock (engine/stockwatch.py, stockwatch.py): booming and brand-new products of Product ideas, read in the USA,
Australia and the UAE; a product that ran out recently with fewer than 5 sellers is shown and raises one alert; a read
that was blocked is never "out of stock"."""
import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import amazon_check as ac  # noqa: E402
import analysis  # noqa: E402
import product  # noqa: E402
import spapi  # noqa: E402
import stockwatch  # noqa: E402
import storage  # noqa: E402
from engine import stockwatch as sw  # noqa: E402
from test_gapfinder import FakeS  # noqa: E402

NOW = 1_800_000_000
DAY = 86400


def row(kind, asin, pct=None, pos=1, ts=NOW - DAY, market='US', title='Mini Ice Maker'):
    return {'kind': kind, 'asin': asin, 'pct': pct, 'pos': pos, 'ts': ts, 'market': market, 'title': title, 'image': '',
            'category': 'Kitchen', 'price': 99.0}


def test_candidates_are_booming_or_brand_new():
    rows = [row('movers', 'B1', pct=540), row('movers', 'B2', pct=120), row('new', 'B3', pos=4), row('new', 'B4', pos=45),
            row('movers', 'B5', pct=900, ts=NOW - 30 * DAY), row('movers', 'B1', pct=300, ts=NOW - 2 * DAY)]
    c = sw.candidates(rows, NOW)
    assert [x['asin'] for x in c] == ['B1', 'B3']                     # booming 540% first, then brand new #4
    by = {x['asin']: x for x in c}
    assert by['B1']['why'] == 'Booming: sales rank up 540%' and by['B1']['kind'] == 'booming'
    assert by['B3']['why'] == 'Brand new: #4 in New Releases, Kitchen' and by['B3']['kind'] == 'new'


def test_climbing_fast_is_booming_while_amazon_shows_no_movers_and_shakers():
    def r(asin, pos, days_ago, kind='new'):
        return dict(row(kind, asin, pos=pos, ts=NOW - days_ago * DAY), slug='kitchen')
    rows = [r('BX', 38, 4), r('BX', 20, 2), r('BX', 5, 0),                # climbed 33 places into the top 20
            r('BY', 30, 4), r('BY', 26, 0),                               # 4 places: not booming, not top 10
            r('BOLD', 1, 4), r('BZ', 7, 0),                               # new on a list read for 4 days, already #7
            r('BB', 40, 3, 'best'), r('BB', 12, 0, 'best')]               # Best Sellers climb
    c = {x['asin']: x for x in sw.candidates(rows, NOW)}
    assert c['BX']['kind'] == 'booming' and c['BX']['why'] == 'Climbing fast: #38 to #5 in New Releases, Kitchen'
    assert c['BZ']['kind'] == 'booming' and c['BZ']['why'] == 'New on New Releases, Kitchen: already #7'
    assert c['BB']['why'] == 'Climbing fast: #40 to #12 in Best Sellers, Kitchen'
    assert 'BY' not in c and c['BOLD']['kind'] == 'new'


def rd(ts, status='ok', available=True, n=None, rank=None):
    return {'ts': ts, 'status': status, 'available': available, 'n_offers': n, 'rank': rank, 'badge': None, 'price': 50}


def test_state_out_of_stock_recently_with_few_sellers():
    s = sw.state([rd(NOW, available=False, n=0), rd(NOW - DAY, available=False), rd(NOW - 3 * DAY, n=3)], NOW)
    assert s['state'] == 'out' and s['recent'] and s['fits'] and s['sellers'] == 3
    assert s['since'] == NOW - DAY and s['last_in'] == NOW - 3 * DAY and 'was in stock' in s['why']
    many = sw.state([rd(NOW, available=False), rd(NOW - DAY, n=8)], NOW)
    assert many['state'] == 'out' and not many['fits'] and many['sellers'] == 8         # 8 sellers: not few
    ranked = sw.state([rd(NOW, available=False, n=1, rank=1200)], NOW)
    assert ranked['fits'] and 'still ranked #1,200' in ranked['why']                   # first read: it sold until lately
    cold = sw.state([rd(NOW, available=False)], NOW)
    assert cold['state'] == 'out' and not cold['recent'] and not cold['fits']
    old = sw.state([rd(NOW, available=False), rd(NOW - 60 * DAY, n=2)], NOW)
    assert not old['recent']                                                           # in stock two months ago only
    assert sw.state([rd(NOW, n=2)], NOW)['state'] == 'in'
    assert sw.state([rd(NOW, status='missing', available=None)], NOW)['state'] == 'missing'
    blocked = sw.state([rd(NOW, status='captcha', available=None)], NOW)
    assert blocked['state'] == 'unread' and not blocked['fits'] and 'captcha' in blocked['why']
    assert sw.state([rd(NOW, available=False, n=5)], NOW, max_sellers=5)['state'] == 'out'


def test_rows_and_what_to_read_next():
    cands = sw.candidates([row('movers', 'B1', pct=540), row('new', 'B2', pos=3, market='AU')], NOW)
    checks = {('AU', 'B1'): [rd(NOW - 2 * DAY, available=False, n=2), rd(NOW - 5 * DAY, n=2)],
              ('US', 'B1'): [rd(NOW - 2 * DAY, n=40)], ('AE', 'B1'): [rd(NOW - 2 * DAY, status='missing', available=None)]}
    rs = sw.rows(cands, checks, NOW)
    assert rs[0]['asin'] == 'B1' and rs[0]['fits'] and rs[0]['out'] == ['AU'] and rs[0]['reached'] == ['US', 'AU']
    assert rs[0]['markets']['AE']['state'] == 'missing' and not rs[1]['fits']
    todo = sw.plan(cands, checks, NOW, 10)
    assert todo[0] == ('AU', 'B1')                                     # out of stock now: did it come back?
    assert ('AU', 'B2') in todo and ('AE', 'B2') in todo and ('US', 'B2') not in todo   # spotted in Australia
    assert todo[-1] == ('AE', 'B1')                                    # not there yet: read last
    assert sw.plan(cands, checks, NOW, 10, ('US',)) == [('US', 'B1')]   # one market's own share
    fresh = {k: [dict(v[0], ts=NOW - 3600)] + v[1:] for k, v in checks.items()}
    assert ('AU', 'B1') not in sw.plan(cands, fresh, NOW, 10)          # read in the last 20 hours


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = storage.DB(tmp_path / 'm.db').open()
    calls = []
    S = FakeS(db, calls, [])

    def page(**kw):
        p = product.parse_product({}, 'US')
        p.update({'status': 'ok', 'title': 'Mini Ice Maker Countertop', 'price': 99.0, 'available': True,
                  'fulfillment': 'fba', 'seller_id': 'S1', 'buybox_seller': 'Cool Co', 'offers': 3, **kw})
        return p
    pages = {('US', 'B0ICE'): page(offers=30), ('AU', 'B0ICE'): page(available=False, offers=2, root_rank=850)}

    async def read_product(checker, asin, market, reviews=False):
        calls.append(('page', market, asin))
        got = pages.get((market, asin))
        if isinstance(got, Exception):
            raise got
        if got is None:
            p = product.parse_product({}, market)
            p['status'] = 'missing'
            return p
        return dict(got)

    notified = []
    monkeypatch.setattr(product, 'read_product', read_product)
    monkeypatch.setattr(analysis, 'notify_new', lambda S, fresh: notified.append(fresh))
    db.write(storage.save_list, 'US', 'movers', 'kitchen', 'Kitchen',
             [{'asin': 'B0ICE', 'title': 'Mini Ice Maker Countertop', 'image': '', 'pct': 640, 'rank_now': 40}])
    yield S, calls, pages, notified
    db.close()


def test_a_booming_product_out_of_stock_in_australia_with_few_sellers(env):
    S, calls, pages, notified = env
    out = asyncio.run(stockwatch.run(S))
    assert out['status'] == 'ok' and '1 product out of stock with 4 sellers or fewer' in out['note']
    assert {c[1] for c in calls if c[0] == 'page'} == {'US', 'AU', 'AE'}
    t = stockwatch.table(S.db, S.settings)
    r = t['rows'][0]
    assert r['fits'] and r['out'] == ['AU'] and r['markets']['US']['state'] == 'in' and r['markets']['AE']['state'] == 'missing'
    assert r['markets']['AU']['sellers'] == 2 and 'still ranked #850' in r['markets']['AU']['why']
    alerts = S.db.read(storage.list_alerts)
    assert len(alerts) == 1 and alerts[0]['rule'] == 'oos_few' and alerts[0]['market'] == 'AU' and len(notified) == 1
    calls.clear()
    again = asyncio.run(stockwatch.run(S))                               # read in the last 20 hours: nothing to read
    assert not [c for c in calls if c[0] == 'page'] and len(S.db.read(storage.list_alerts)) == 1
    assert 'all read today' in again['note']


def test_a_blocked_read_is_not_out_of_stock(env):
    S, _, pages, _ = env
    pages[('AU', 'B0ICE')] = ac.CaptchaRequired('captcha')
    out = asyncio.run(stockwatch.run(S, ['AU']))
    assert out['status'] == 'partial' and 'stopped' in out['note']
    r = stockwatch.table(S.db, S.settings)['rows'][0]
    assert r['markets']['AU']['state'] == 'unread' and not r['fits']
    token = ac.BACKGROUND.set(True)
    try:
        with pytest.raises(ac.CaptchaRequired):                         # the scheduler pauses that marketplace
            asyncio.run(stockwatch.run(S, ['AU']))
    finally:
        ac.BACKGROUND.reset(token)


def test_a_dropped_connection_to_the_seller_api_is_its_usual_error(monkeypatch):
    def boom(*a, **k):
        raise ConnectionResetError(10054, 'An existing connection was forcibly closed by the remote host')
    monkeypatch.setattr(spapi.urllib.request, 'urlopen', boom)
    with pytest.raises(spapi.SpApiError) as e:
        spapi._http('GET', 'https://example.invalid/', {})
    assert 'could not be reached' in str(e.value)


def test_no_ideas_yet(tmp_path):
    db = storage.DB(tmp_path / 'm.db').open()
    try:
        S = FakeS(db, [], [])
        assert 'No booming or brand-new products yet' in asyncio.run(stockwatch.run(S))['note']
        t = stockwatch.table(db, S.settings)
        assert t['rows'] == [] and t['watched'] == 0 and t['max_sellers'] == 4
    finally:
        db.close()
