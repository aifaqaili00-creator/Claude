"""Market intelligence from the database (intel.py) and the Amazon stock-out alert (analysis.stockout_alerts)."""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import amazon_check as ac  # noqa: E402
import analysis  # noqa: E402
import intel  # noqa: E402
import product  # noqa: E402
import storage  # noqa: E402

NOW = time.time()
DAY = 86400


@pytest.fixture
def db(tmp_path):
    d = storage.DB(tmp_path / 'm.db').open()
    yield d
    d.close()


def page(title, **kw):
    p = product.parse_product({}, 'AU')
    p.update({'status': 'ok', 'title': title, 'root_rank': 500, 'available': True, **kw})
    return p


def search(db, kw, rows, ads=None, at=None):
    full = [{'asin': a, 'title': t, 'price': 20.0, 'rating': 4.5, 'reviews': 10, 'bought': b, 'prime': True,
             'sponsored': sp, 'delivery': 'FREE delivery Tomorrow', 'days': 1, 'intl': False, 'url': '', 'image': ''}
            for a, t, b, sp in rows]
    s = ac.summarize('AU', kw, 'Sydney 2000', full, 3, 9, at or NOW)
    s['ads'] = ads
    return db.write(storage.save_serp, s)


def fill(db):
    db.write(storage.save_product, 'AU', 'B0GLOW0001', page('Glow Oven Cleaner Spray', brand='Glow', seller_id='SGLOW',
                                                            buybox_seller='Glow Ltd', fulfillment='fba', badge_low=1000,
                                                            listing={'images': 7, 'video': True, 'aplus': True, 'bullets': 5,
                                                                     'title_len': 120}, price=20.0, rating=4.6, reviews=900,
                                                            related={'bought': [{'asin': 'B0CLOTH001', 'title': 'Microfibre Cloths 12 Pack'}],
                                                                     'viewed': [{'asin': 'B0RIVAL001', 'title': 'Rival Oven Cleaner Spray'}]}),
             NOW - DAY)
    db.write(storage.save_product, 'AU', 'B0AMZN0001', page('Basics Oven Cleaner', brand='Amazon Basics', fulfillment='amazon',
                                                            buybox_seller='Amazon AU'), NOW - DAY)
    db.write(storage.save_product, 'AU', 'B0MINE0001', page('My Oven Cleaner', brand='Mine', seller_id='SME',
                                                            fulfillment='fbm', listing={'images': 3, 'video': False, 'aplus': False,
                                                                                        'bullets': 5, 'title_len': 40},
                                                            price=25.0, rating=4.1, reviews=12), NOW - DAY)
    db.write(storage.save_seller, 'AU', 'SGLOW', {'name': 'Glow Ltd', 'country': 'GB', 'status': 'ok'})
    rows = [('B0AD000001', 'Adco Spray', None, True), ('B0GLOW0001', 'Glow Oven Cleaner Spray', 1000, False),
            ('B0AMZN0001', 'Basics Oven Cleaner', 300, False), ('B0MINE0001', 'My Oven Cleaner', None, False)]
    search(db, 'oven cleaner', [r for r in rows if r[0] != 'B0AD000001'], None, NOW - 30 * DAY)
    search(db, 'oven cleaner', rows, [{'kind': 'brand_banner', 'brand': 'Adco', 'asins': ['B0AD000001'], 'titles': []}])


def test_seller_explorer_and_origins(db):
    fill(db)
    s = intel.sellers(db, 'AU')
    names = [r['name'] for r in s['sellers']]
    assert names[0] == 'Glow Ltd' and 'Amazon' in names and s['pages'] == 3 and s['profiles'] >= 2
    glow = s['sellers'][0]
    assert glow['country'] == 'GB' and glow['units']['src'] == ['badge'] and glow['brands'][0]['brand'] == 'Glow'
    o = intel.origins(db, 'AU')
    assert o['countries'][0]['country'] == 'GB' and {c['country'] for c in o['countries']} >= {'GB', 'AU', None}
    niche = intel.origins(db, 'AU', 'oven cleaner')
    assert niche['searched'] and niche['kw'] == 'oven cleaner'


def test_brand_explorer_for_a_niche_and_across_niches(db):
    fill(db)
    b = intel.brands(db, 'AU', 'Oven Cleaner')
    by = {x['brand']: x for x in b['brands']}
    assert b['searched'] and b['compared_with'] and by['Glow']['src'] == 'listing' and by['Amazon Basics']['amazon']
    assert b['amazon_listings'] == 1 and 'Adco' not in by                     # the ad is not page-1 sales
    allb = intel.brands(db, 'AU')
    assert allb['niches'] == 1 and allb['brands'][0]['n_niches'] == 1 and allb['keywords'][0]['kw'] == 'oven cleaner'
    assert intel.brands(db, 'AU', 'never searched')['searched'] is False


def test_category_gaps_from_the_gap_finder(db):
    db.write(storage.save_gap_check, 'AU', 'B0X', 'B0X', 'x', 'ok', 'lone_abroad', {'category': 'Kitchen'})
    db.write(storage.save_gap_check, 'AU', 'B0Y', 'B0Y', 'y', 'ok', 'crowded', {'category': 'Kitchen'})
    rows = intel.categories(db)['rows']
    assert rows[0]['category'] == 'Kitchen' and rows[0]['share_open'] == 0.5 and rows[0]['share_local'] == 0.5


def test_related_products_and_the_list_of_pages_read(db):
    fill(db)
    idx = intel.related(db, 'AU')
    assert [r['asin'] for r in idx['read']] == ['B0GLOW0001'] and idx['read'][0]['links'] == 2
    g = intel.related(db, 'AU', 'B0GLOW0001')
    roles = {n['asin']: n['role'] for n in g['nodes']}
    assert roles == {'B0CLOTH001': 'add-on', 'B0RIVAL001': 'competitor'} and g['read'] and g['page_read_at']
    assert storage.asin_info.__name__ and db.read(storage.asin_info, 'AU', 'B0CLOTH001')['title'] == 'Microfibre Cloths 12 Pack'


def test_ad_watch(db):
    fill(db)
    one = intel.ads(db, 'AU', 'oven cleaner')
    assert one['score'] is not None and one['conf'] == 'medium' and one['brands'][0]['brand'] == 'Adco'
    assert 'brand banner' in one['brands'][0]['formats'] and one['display'] == 'oven cleaner'
    allk = intel.ads(db, 'AU')
    assert [r['kw'] for r in allk['rows']] == ['oven cleaner']
    assert intel.ads(db, 'AU', 'nothing here')['label'] == 'Not read'


def test_listing_scorecard(db):
    fill(db)
    s = intel.scorecard(db, 'AU', 'B0MINE0001', 'oven cleaner')
    v = {i['key']: i['verdict'] for i in s['items']}
    assert s['read'] and s['n_top'] == 2 and v['images'] == 'behind' and v['reviews'] == 'behind'
    assert s['todo'] == ['B0AMZN0001'] and not s['mine_todo']                # Basics was read before listing facts
    assert intel.scorecard(db, 'AU', 'B0MINE0001')['kw'] is None             # no keyword known for it yet
    db.write(storage.set_pref, 'AU', 'B0MINE0001', phrase='oven cleaner')
    assert intel.scorecard(db, 'AU', 'B0MINE0001')['kw'] == 'oven cleaner'


def test_amazon_stockout_alert_for_watched_products(db):
    db.write(storage.add_watch, 'asin', 'AU', 'B0KETTLE01')
    db.write(storage.save_product, 'AU', 'B0KETTLE01', page('Kettle', fulfillment='amazon', buybox_seller='Amazon AU'), NOW - 2 * DAY)
    db.write(storage.save_product, 'AU', 'B0KETTLE01', page('Kettle', fulfillment=None, available=False), NOW - 3600)
    got = db.read(analysis.stockout_alerts, NOW)
    assert len(got) == 1 and got[0]['rule'] == 'amazon_stockout' and got[0]['severity'] == 3
    db.write(storage.save_product, 'AU', 'B0OTHER001', page('Other', fulfillment='amazon'), NOW - 2 * DAY)
    db.write(storage.save_product, 'AU', 'B0OTHER001', page('Other', available=False), NOW - 3600)
    assert len(db.read(analysis.stockout_alerts, NOW)) == 1                 # not watched: no alert
