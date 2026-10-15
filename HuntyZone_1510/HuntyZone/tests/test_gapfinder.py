"""Gap Finder pipeline (gapfinder.py): proof lists, proven products, checks in Australia and the UAE, blocked reads kept as
gaps in the data, the SP-API path, the lone-exporter watch, and the Gap Finder table."""
import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import amazon_check as ac  # noqa: E402
import analysis  # noqa: E402
import config  # noqa: E402
import gapfinder  # noqa: E402
import ideas  # noqa: E402
import product  # noqa: E402
import reports  # noqa: E402
import storage  # noqa: E402

TITLE = 'Glisten Oven Cleaner Spray Heavy Duty 500ml'


class FakeChecker:
    def __init__(self, calls, rows):
        self.calls, self.rows = calls, rows

    async def start(self):
        pass

    async def check(self, kw, code, fast, pages, progress, local):
        self.calls.append(('search', code, kw))
        today = config.market_today(code)
        rows = [{**r, 'market': code, 'days': ac.delivery_days(r['delivery'], today), 'url': '', 'image': '', 'rating': 4.5,
                 'reviews': 10, 'bought': r.get('bought'), 'prime': False} for r in self.rows]
        return ac.summarize(code, kw, ac.MARKETS[code]['location'], rows, fast, local, time.time())


class FakeS:
    def __init__(self, db, calls, rows):
        self.db, self.calls = db, calls
        self.settings = config.defaults()
        self.checker = FakeChecker(calls, rows)
        self.spapi = None
        self.said = []

    def days(self):
        return 3, 9, 1

    def cached(self, code, kw, pages):
        return None

    async def save_check(self, s, run_id, pages, source='user'):
        s['snapshot_id'] = self.db.write(storage.save_serp, s, source, run_id, pages, None)

    def say(self, m):
        self.said.append(m)

    def window_focused(self):
        return False


def page(**kw):
    p = product.parse_product({}, 'AU')
    p.update({'status': 'ok', 'title': TITLE, 'brand': 'Glisten', 'root_rank': 900, 'price': 24.95, 'available': True, **kw})
    return p


def search_row(asin, title, delivery, sponsored=False, intl=False, bought=None):
    return {'asin': asin, 'title': title, 'delivery': delivery, 'sponsored': sponsored, 'intl': intl, 'price': 19.0,
            'bought': bought}


ROWS = [search_row('B0RIVAL1', 'Kitchen Degreaser Gel 750ml', 'FREE delivery Tomorrow', bought=300),
        search_row('B0RIVAL2', 'Bench Wipes Lemon 100 pack', 'FREE delivery Tomorrow', bought=100),
        search_row('B0ADVERT', 'Oven Cleaner Spray by Someone', 'FREE delivery Tomorrow', sponsored=True)]


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = storage.DB(tmp_path / 'm.db').open()
    calls = []
    S = FakeS(db, calls, list(ROWS))
    pages = {('AU', 'B0OVEN'): page(fulfillment='fbm', seller_id='SGB1', buybox_seller='Glow Ltd', origin='overseas',
                                    delivery_days=14, bullets='Cuts grease. Flammable aerosol.', item_form='Spray',
                                    safety=['warning']),
             ('US', 'B0OVEN'): page(), ('GB', 'B0OVEN'): page()}
    sellers = {'SGB1': {'name': 'Glow Ltd', 'business_name': 'Glow Limited', 'country': 'GB', 'rating_pct': 98.0,
                        'ratings': 120, 'status': 'ok'}}

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
        return dict(sellers[sid])

    async def complete(checker, code, prefix):
        calls.append(('complete', code, prefix))
        return ['glisten oven cleaner', 'oven cleaner spray', 'oven cleaner']

    notified = []
    monkeypatch.setattr(product, 'read_product', read_product)
    monkeypatch.setattr(product, 'read_seller', read_seller)
    monkeypatch.setattr(ideas, 'complete', complete)
    monkeypatch.setattr(analysis, 'notify_new', lambda S, fresh: notified.append(fresh))
    yield S, calls, pages, notified
    db.close()


def best_list(S, market, items, slug='kitchen', ts=None):
    S.db.write(storage.save_list, market, 'best', slug, 'Kitchen',
               [{'asin': a, 'title': t, 'image': '', 'price': 9.99} for a, t in items], 'ok', None, ts)


def proven_oven(S):
    best_list(S, 'US', [('B0FILL%d' % i, 'Filler Thing %d' % i) for i in range(4)] + [('B0OVEN', TITLE)])
    best_list(S, 'GB', [('B0OVEN', TITLE), ('B0UKONLY', 'Tea Towel Set')])


def test_proof_lists_are_read_and_kept_out_of_the_radar(env, monkeypatch):
    S = env[0]
    seen = []

    async def read_list(checker, code, kind, slug, name):
        seen.append((code, kind, slug))
        if slug == 'outdoors':
            raise ideas.ListMissing('no list')
        return [{'asin': 'B0%03d' % i, 'title': 'Thing %d' % i, 'image': '', 'market': code, 'rank_now': i + 1}
                for i in range(40)]
    monkeypatch.setattr(ideas, 'read_list', read_list)
    out = asyncio.run(gapfinder.read_proof_lists(S, 'GB'))
    cats = ['kitchen', 'diy', 'outdoors', 'sports', 'pet-supplies', 'officeproduct']
    assert seen == [('GB', 'best', c) for c in cats] + [('GB', 'movers', c) for c in cats if c != 'outdoors']
    assert out['status'] == 'ok' and '1 category not on Amazon UK' in out['note']
    assert out['note'].startswith('5 Best Sellers lists and 5 Movers & Shakers lists read on Amazon UK')
    assert 'GB' not in config.MARKETS and S.db.read(storage.list_scans, 'US') == []   # the radar reads US / AU / UAE only
    assert len(S.db.read(storage.list_scans, 'GB', 0, ('best', 'movers'))) == 10
    seen.clear()
    again = asyncio.run(gapfinder.read_proof_lists(S, 'GB'))                # read today: kept; the missing one is tried once
    assert seen == [('GB', 'best', 'outdoors')] and again['status'] == 'ok' and '10 read in the last 20 hours kept' in again['note']
    asyncio.run(gapfinder.read_proof_lists(S, 'US'))
    assert {x[1] for x in seen} == {'best'}                                 # the radar's scans bring the US movers
    S.settings['categories']['US'] = ['kitchen']
    assert gapfinder.proof_categories(S.settings, 'US') == [{'slug': 'kitchen', 'name': 'Kitchen'}]


def test_a_mover_now_in_the_best_sellers_top_100_is_proof(env):
    S = env[0]
    best_list(S, 'US', [('B0OVEN', TITLE)])
    S.db.write(storage.save_list, 'DE', 'movers', 'kitchen', 'Kitchen',
               [{'asin': 'B0OVEN', 'title': TITLE, 'image': '', 'pct': 900, 'rank_now': 85, 'rank_before': 2140}])
    c = S.db.read(gapfinder.proven, S.settings)
    assert [x['cluster'] for x in c] == ['B0OVEN'] and c[0]['proof']['n'] == 2
    assert c[0]['proof']['markets']['DE']['why'].startswith('#85 in Best Sellers now, up from #2,140 (Movers & Shakers, Kitchen)')
    S.db.write(storage.save_list, 'GB', 'movers', 'kitchen', 'Kitchen',
               [{'asin': 'B0OTHER', 'title': 'Steel Wool Pads 20 pack', 'image': '', 'pct': 300, 'rank_now': 640}])
    assert not [x for x in S.db.read(gapfinder.proven, S.settings) if x['cluster'] == 'B0OTHER']   # rising, not proof


def test_a_block_with_nothing_read_is_raised(env, monkeypatch):
    async def read_list(checker, code, kind, slug, name):
        raise ac.CaptchaRequired('captcha')
    monkeypatch.setattr(ideas, 'read_list', read_list)
    with pytest.raises(ac.CaptchaRequired):
        asyncio.run(gapfinder.read_proof_lists(env[0], 'DE'))


def test_proven_needs_two_proof_markets(env):
    S = env[0]
    proven_oven(S)
    c = S.db.read(gapfinder.proven, S.settings)
    assert [x['cluster'] for x in c] == ['B0OVEN'] and c[0]['proof']['n'] == 2 and c[0]['best'] == 1
    assert [i['market'] for i in c[0]['items']] == ['US', 'GB'] and c[0]['title'] == TITLE
    assert 'Best Sellers' in c[0]['proof']['markets']['US']['why']


def test_a_lone_exporter_is_found_and_kept_with_its_warnings(env):
    S, calls, _, _ = env
    proven_oven(S)
    out = asyncio.run(gapfinder.check_market(S, 'AU', 5))
    assert out['status'] == 'ok' and out['counts'] == {'lone_abroad': 1} and '1 gap' in out['note']
    assert ('page', 'AU', 'B0OVEN') in calls and ('seller', 'AU', 'SGB1') in calls
    assert ('search', 'AU', 'oven cleaner spray') in calls                    # the phrase without the brand
    assert ('page', 'US', 'B0OVEN') not in calls                              # the page there had the brand
    rows = S.db.read(storage.gap_checks)
    assert len(rows) == 1 and rows[0]['status'] == 'ok' and rows[0]['gap_type'] == 'lone_abroad'
    d = rows[0]['data']
    assert d['gap']['exporter']['country'] == 'GB' and d['gap']['local_rivals'] == 2
    assert d['flags'][0]['label'] == 'chemical / dangerous goods' and 'item form: spray' in d['flags'][0]['why']
    assert d['listing']['price'] == 24.95 and d['search']['bought_total'] == 400
    o = S.db.read(storage.latest_offers, 'AU', 'B0OVEN')
    assert o['source'] == 'page' and o['offers'][0]['seller_id'] == 'SGB1'
    assert S.db.read(gapfinder.due, S.db.read(gapfinder.proven, S.settings), 'AU', time.time(), 5) == []   # done for a week


def test_a_blocked_market_is_a_gap_in_the_data_not_missing(env):
    S, calls, pages, _ = env
    proven_oven(S)
    pages[('AE', 'B0OVEN')] = ac.CaptchaRequired('captcha')
    out = asyncio.run(gapfinder.check_market(S, 'AE', 5))
    rows = S.db.read(storage.gap_checks)
    assert rows[0]['gap_type'] == 'unread' and rows[0]['status'] == 'unread' and out['status'] == 'partial'
    assert 'stopped' in out['note'] and not [c for c in calls if c[0] == 'search']        # nothing more was read
    assert S.db.read(gapfinder.due, S.db.read(gapfinder.proven, S.settings), 'AE', time.time(), 5)   # tried again next run

    async def background():
        ac.BACKGROUND.set(True)
        return await gapfinder.check_market(S, 'AE', 5)
    with pytest.raises(ac.CaptchaRequired):
        asyncio.run(background())


def test_a_product_not_listed_there_with_nobody_on_page_1_is_missing(env):
    S, calls, pages, _ = env
    proven_oven(S)
    del pages[('AU', 'B0OVEN')]
    S.checker.rows = [search_row('B0RIVAL1', 'Kitchen Degreaser Gel', 'FREE delivery Tomorrow')]
    asyncio.run(gapfinder.check_market(S, 'AU', 5))
    d = S.db.read(storage.gap_checks)[0]['data']
    assert d['gap']['type'] == 'missing' and 'Not on Amazon Australia' in d['gap']['why']
    assert ('page', 'US', 'B0OVEN') in calls                                  # brand and bullets from the proof page


class FakeApi:
    def __init__(self, payload, item=True):
        self.payload, self.item, self.asked = payload, item, []

    def has(self, market):
        return market == 'AU'

    def catalog_item(self, market, asin):
        self.asked.append(('catalog', asin))
        return {'summaries': [{'marketplaceId': 'A39IBJ37TRP1C6', 'itemName': TITLE, 'brand': 'Glisten'}],
                'attributes': {'hazmat': [{'value': 'yes'}]}} if self.item else None

    def item_offers(self, market, asin):
        self.asked.append(('offers', asin))
        return self.payload


def test_the_seller_api_answers_instead_of_the_page(env):
    S, calls, _, _ = env
    proven_oven(S)
    S.spapi = FakeApi({'Summary': {'TotalOfferCount': 1}, 'Offers': [{'SellerId': 'SGB1', 'IsFulfilledByAmazon': False,
                                                                     'ShipsFrom': {'Country': 'GB'}, 'IsBuyBoxWinner': True,
                                                                     'ListingPrice': {'Amount': 22.5}}]})
    asyncio.run(gapfinder.check_market(S, 'AU', 5))
    assert ('page', 'AU', 'B0OVEN') not in calls and ('seller', 'AU', 'SGB1') in calls
    d = S.db.read(storage.gap_checks)[0]['data']
    assert d['gap']['type'] == 'lone_abroad' and d['listing']['source'] == 'spapi' and d['listing']['price'] == 22.5
    assert any('hazmat' in f['why'] for f in d['flags'])
    assert S.db.read(storage.latest_offers, 'AU', 'B0OVEN')['source'] == 'spapi'


def test_a_watched_lone_exporter_that_gets_company_alerts_once(env):
    S, calls, pages, notified = env
    proven_oven(S)
    asyncio.run(gapfinder.check_market(S, 'AU', 5))
    S.db.write(storage.add_watch, 'lone', 'AU', 'B0OVEN', 'Oven cleaner')
    pages[('AU', 'B0OVEN')] = page(fulfillment='fba', seller_id='SAU9', buybox_seller='Aussie Clean', origin='local',
                                   delivery_days=1, offers=2)
    out = asyncio.run(gapfinder.recheck_lone(S, 'AU'))
    alerts = S.db.read(storage.list_alerts)
    assert out['note'].startswith('1 of 1 watched listing read, 1 with a new seller')
    assert len(alerts) == 1 and alerts[0]['rule'] == 'lone_second_seller' and alerts[0]['data']['asin'] == 'B0OVEN'
    assert 'Aussie Clean' in alerts[0]['body'] and len(notified) == 1
    asyncio.run(gapfinder.recheck_lone(S, 'AU'))
    assert len(S.db.read(storage.list_alerts)) == 1 and len(notified) == 1    # the same line-up alerts once


def test_the_gap_finder_table(env):
    S = env[0]
    proven_oven(S)
    asyncio.run(gapfinder.check_market(S, 'AU', 5))
    S.db.write(storage.add_watch, 'lone', 'AU', 'B0OVEN', 'Oven cleaner')
    t = reports.gaps_table(S.db, S.settings)
    r = t['rows'][0]
    assert t['proven'] == 1 and r['type'] == 'lone_abroad' and r['gap'] and r['label'] == 'Lone exporter, ships from abroad'
    assert r['currency'] == 'A$' and r['price'] == 24.95 and r['price_basis'] == 'listing'
    assert r['max_cogs_20'] is not None and r['max_cogs_20'] > 0 and r['size_known'] is False
    assert r['proof']['US']['well'] and r['watch_id'] and r['demand_floor'] == 400
    assert t['lone'][0]['asin'] == 'B0OVEN' and t['lone'][0]['sellers'] == 1


def test_your_own_run_skips_proof_lists_read_today(env, monkeypatch):
    S, calls, _, _ = env
    proven_oven(S)
    read = []

    async def read_list(checker, code, kind, slug, name):
        read.append(code)
        return []
    monkeypatch.setattr(ideas, 'read_list', read_list)
    out = asyncio.run(gapfinder.run(S, lambda m: None, ['AU']))
    assert 'US' not in read and 'GB' not in read and set(read) == {'DE'}
    assert any(n.startswith('Amazon Australia: 1 of 1 proven product checked') for n in out['notes'])
