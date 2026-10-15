"""Market Gaps live readings (marketgaps.py): baseline niches checked on Amazon, Best Sellers categories turned into
brand and buy-box concentration, blocked reads kept as 'unread', alerts when a gap opens or fills up, and the page data."""
import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import amazon_check as ac  # noqa: E402
import analysis  # noqa: E402
import gapfinder  # noqa: E402
import ideas  # noqa: E402
import marketgaps  # noqa: E402
import product  # noqa: E402
import storage  # noqa: E402
from engine import marketgaps as mg  # noqa: E402
from test_gapfinder import FakeS, page, search_row  # noqa: E402

ROWS = [search_row('B0A', 'Oven Cleaner Spray Max 500ml', 'FREE International delivery 20 - 25 Nov', intl=True, bought=200),
        search_row('B0B', 'Oven Cleaner Gel 750ml', 'FREE delivery Tomorrow', bought=100),
        search_row('B0AD', 'Oven Cleaner by Someone', 'FREE delivery Tomorrow', sponsored=True)]


def baseline(db):
    def b(name, sub, rev, seller, sellers=1.0):
        return {'brand': name, 'subcategory': sub, 'revenue': rev, 'dominant_seller': seller, 'avg_sellers': sellers,
                'avg_price': 30.0, 'reviews': 100, 'products': 5, 'category': 'Home'}
    rows = [b('Glisten', 'Oven Cleaner', 200000, 'Amazon US'), b('Shine', 'Oven Cleaner', 20000, 'Shop AU', 2),
            b('Bonds', "Men's Pants", 30000, 'Amazon AU'),
            b('A', 'Cutlery', 90000, 'S1', 5), b('B', 'Cutlery', 90000, 'S2', 5), b('C', 'Cutlery', 90000, 'S3', 5),
            b('D', 'Cutlery', 90000, 'S4', 5)]
    db.write(storage.save_ss_import, 'ss_brands', 'AU', 'smartscout - AU brands.xlsx', 'sha-au', rows, time.time())


@pytest.fixture
def env(tmp_path, monkeypatch):
    marketgaps.CACHE.clear()
    db = storage.DB(tmp_path / 'm.db').open()
    calls = []
    S = FakeS(db, calls, list(ROWS))
    pages = {('AU', 'B0A'): page(fulfillment='amazon', buybox_seller='Amazon US', origin='overseas', delivery_days=14),
             ('AU', 'B0B'): page(fulfillment='fba', seller_id='SLOC', buybox_seller='Local Co', origin='local', delivery_days=1)}

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

    async def read_seller(checker, sid, market):
        calls.append(('seller', market, sid))
        return {'name': 'Local Co', 'business_name': 'Local Co Pty', 'country': 'AU', 'rating_pct': 99.0, 'ratings': 10,
                'status': 'ok'}

    async def complete(checker, code, prefix):
        calls.append(('complete', code, prefix))
        return ['oven cleaner spray', 'oven cleaner', 'glisten oven cleaner']

    notified = []
    monkeypatch.setattr(product, 'read_product', read_product)
    monkeypatch.setattr(product, 'read_seller', read_seller)
    monkeypatch.setattr(ideas, 'complete', complete)
    monkeypatch.setattr(analysis, 'notify_new', lambda S, fresh: notified.append(fresh))
    baseline(db)
    yield S, calls, pages, notified
    db.close()


def test_open_niches_are_checked_best_first_and_kept(env):
    S, calls, _, _ = env
    out = asyncio.run(marketgaps.check_niches(S, 'AU', 1))
    assert out['counts'] == {'open': 1} and '1 still open' in out['note']
    assert ('search', 'AU', 'oven cleaner') in calls                   # Amazon's own suggestion, the shortest that fits
    assert ('page', 'AU', 'B0A') in calls and ('page', 'AU', 'B0B') in calls and ('seller', 'AU', 'SLOC') in calls
    key = mg.niche_key('Oven Cleaner')
    c = S.db.read(storage.mg_checks, 'AU')[key]
    assert c['verdict'] == 'open' and c['status'] == 'ok' and c['phrase'] == 'oven cleaner'
    d = c['data']
    assert d['buyboxes'] == {'amazon': 0, 'store': 1, 'abroad': 0, 'local': 1, 'unknown': 0} and d['fast'] == 1
    assert d['over_share'] == round(200 / 300, 4) and d['baseline']['gap'] == 'global_store'
    assert d['top'][0]['offer']['seller'] == 'Amazon US (Global Store)'
    again = asyncio.run(marketgaps.check_niches(S, 'AU', 5))             # checked this week: the next open niche instead
    assert again['counts'] and mg.niche_key("Men's Pants") in S.db.read(storage.mg_checks, 'AU')
    assert mg.niche_key('Cutlery') not in S.db.read(storage.mg_checks, 'AU')          # spread out: not a gap, not checked


def test_a_gap_filling_up_raises_one_alert(env):
    S, _, _, notified = env
    key = mg.niche_key('Oven Cleaner')
    asyncio.run(marketgaps.refresh_market(S, 'AU', 1, 0, keys=[key]))
    S.checker.rows = [search_row('B0L%d' % i, 'Oven Cleaner Local %d' % i, 'FREE delivery Tomorrow', bought=50) for i in range(7)]
    out = asyncio.run(marketgaps.refresh_market(S, 'AU', 1, 0, keys=[key]))
    assert out['status'] == 'ok' and '0 still open' in out['note']
    c = S.db.read(storage.mg_checks, 'AU')[key]
    assert c['verdict'] == 'closing' and [v for _, v in c['history']] == ['open', 'closing']
    alerts = S.db.read(storage.list_alerts)
    assert len(alerts) == 1 and alerts[0]['rule'] == 'mg_closing' and 'Filling up: Oven Cleaner' in alerts[0]['title']
    assert len(notified) == 1
    asyncio.run(marketgaps.refresh_market(S, 'AU', 1, 0, keys=[key]))       # still filling up: no second alert
    assert len(S.db.read(storage.list_alerts)) == 1


def test_a_blocked_search_is_unread_never_open(env, monkeypatch):
    S = env[0]

    async def blocked(*a, **k):
        raise ac.CaptchaRequired('captcha')
    monkeypatch.setattr(S.checker, 'check', blocked)
    out = asyncio.run(marketgaps.refresh_market(S, 'AU', 3, 0))
    assert out['status'] == 'partial' and 'stopped' in out['note']
    checks = S.db.read(storage.mg_checks, 'AU')
    assert len(checks) == 1 and next(iter(checks.values()))['verdict'] == 'unread'      # the run ended at the block
    assert next(iter(checks.values()))['status'] == 'unread'
    token = ac.BACKGROUND.set(True)
    try:
        with pytest.raises(ac.CaptchaRequired):                               # the scheduler pauses that marketplace
            asyncio.run(marketgaps.refresh_market(S, 'AU', 3, 0))
    finally:
        ac.BACKGROUND.reset(token)


def test_best_sellers_categories_become_brand_and_buy_box_concentration(env, monkeypatch):
    S, calls, pages, _ = env
    seen = []

    async def read_list(checker, code, kind, slug, name):
        seen.append((code, kind, slug))
        if slug == 'kitchen':
            raise ideas.ListMissing('no list')
        return [{'asin': a, 'title': t, 'image': '', 'market': code, 'price': 25.0, 'reviews': 40, 'rank': i}
                for i, (a, t) in enumerate([('B0A', 'Glisten Oven Cleaner Spray'), ('B0B', 'Glisten Gel 750ml'),
                                            ('B0C', 'Shine Cloths 10 pack'), ('B0D', 'Spark Sponge')], 1)]
    monkeypatch.setattr(ideas, 'read_list', read_list)
    note = asyncio.run(marketgaps.sweep(S, 'AU', 2))
    assert seen == [('AU', 'best', 'home'), ('AU', 'best', 'kitchen')] and '1 Best Sellers category read' in note
    assert 'not on Amazon Australia' in note
    cats = S.db.read(storage.mg_categories, 'AU')
    assert len(cats) == 1 and cats[0]['category'] == 'Home'
    d = cats[0]['data']
    assert d['brands'][0]['brand'] == 'Glisten' and d['brands'][0]['listings'] == 2 and d['basis'] == 'rank'
    assert d['buyboxes']['store'] == 1 and d['buyboxes']['local'] == 1 and d['top'][0]['asin'] == 'B0A'
    seen.clear()
    asyncio.run(marketgaps.sweep(S, 'AU', 1))                               # Home was read: the next category instead
    assert seen and seen[0][2] != 'home'


def test_the_page_data_and_the_gap_finder_preference(env):
    S = env[0]
    asyncio.run(marketgaps.check_niches(S, 'AU', 1))
    t = marketgaps.table(S.db, S.settings, 'AU')
    assert t['baseline']['file'] == 'smartscout - AU brands.xlsx' and t['kpis']['checked'] == 1 and t['kpis']['open'] == 1
    first = t['niches'][0]
    assert first['live'] is None or first['live']['verdict'] in mg.VLABEL
    oven = next(n for n in t['niches'] if n['name'] == 'Oven Cleaner')
    assert oven['live']['verdict'] == 'open' and oven['live']['phrase'] == 'oven cleaner'
    assert t['kpis']['global_store'] > 0 and t['missing'] == [] and t['sellers']['sellers'] == 0
    strong = marketgaps.strong_niches(S.db, 'AU', 0)
    assert mg.niche_key('Oven Cleaner') in strong and mg.niche_key('Cutlery') not in strong
    cands = [{'cluster': 'X1', 'title': 'Tea Towel Set'}, {'cluster': 'X2', 'title': 'Glisten Oven Cleaner Spray'}]
    assert [c['cluster'] for c in S.db.read(gapfinder.due, cands, 'AU', time.time(), 2, strong)] == ['X2', 'X1']
    assert [c['cluster'] for c in S.db.read(gapfinder.due, cands, 'AU', time.time(), 2)] == ['X1', 'X2']
