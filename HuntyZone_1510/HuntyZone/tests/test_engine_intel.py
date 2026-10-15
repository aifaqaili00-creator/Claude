"""Market intelligence rules (engine.intel, engine.ads) and the page parsers they rely on, with real Amazon AU markup
recorded on 14 October 2026 (tests/fixtures/intel_au)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import amazon_check as ac  # noqa: E402
import product  # noqa: E402
from engine import ads, intel  # noqa: E402

FIX = Path(__file__).parent / 'fixtures' / 'intel_au'
TS = 1_760_000_000


def load(name):
    return json.loads((FIX / name).read_text('utf-8'))


# ---------- parsing the recorded pages ----------
def test_product_page_carousels_and_listing_facts():
    raw = load('product_B01EIG6A4Q.json')
    rel = product.parse_related(raw['related'], 'B01EIG6A4Q')
    assert set(rel) == {'bought', 'viewed', 'sponsored'} and len(rel['bought']) == 6 and len(rel['viewed']) == 6
    assert all(x['asin'] != 'B01EIG6A4Q' for v in rel.values() for x in v)              # never itself
    assert rel['bought'][0]['title']                                                       # titles come with them
    assert product.parse_listing(raw) == {'images': 6, 'video': True, 'aplus': False, 'bullets': 3, 'title_len': 59}


def test_related_kinds_from_headings():
    blocks = [{'head': 'Frequently bought together', 'items': [{'asin': 'B0AAAAAAA1', 'title': 'Pen'}]},
              {'head': 'Customers also bought', 'items': [{'asin': 'B0AAAAAAA2', 'title': 'x'}, {'asin': 'bad', 'title': ''}]},
              {'head': 'Best Sellers in Home', 'items': [{'asin': 'B0AAAAAAA3', 'title': 'y'}]},
              {'head': 'Compare with similar items', 'items': [{'asin': 'B0AAAAAAA4', 'title': 'z'}]}]
    assert product.parse_related(blocks) == {'fbt': [{'asin': 'B0AAAAAAA1', 'title': 'Pen'}],
                                             'bought': [{'asin': 'B0AAAAAAA2', 'title': 'x'}],
                                             'viewed': [{'asin': 'B0AAAAAAA4', 'title': 'z'}]}
    assert product.parse_listing({}) is None


def test_search_page_ads():
    got = ac.parse_ads(load('search_ads.json'))
    kinds = [a['kind'] for a in got]
    assert kinds.count('brand_banner') == 1 and kinds.count('video') == 2 and kinds.count('carousel') == 1
    banner = next(a for a in got if a['kind'] == 'brand_banner')
    assert banner['brand'] == 'Vanish' and banner['asins'][0] == 'B0D1BTTVFZ'
    carousel = next(a for a in got if a['kind'] == 'carousel')
    assert not any(t.startswith('Sponsored Ad') for t in carousel['titles'])
    assert ac.parse_ads([{'kind': 'other'}, {'kind': 'video', 'asins': None, 'alts': None}]) == \
        [{'kind': 'video', 'brand': None, 'asins': [], 'titles': []}]


# ---------- Ad watch ----------
def test_ad_pressure_on_the_recorded_page():
    rows = [{'asin': r['asin'], 'title': r['title'], 'sponsored': r['sponsored']} for r in load('search_rows.json')]
    p = ads.ad_pressure(rows, ac.parse_ads(load('search_ads.json')), {'B07W6Y95P7': 'Vanish'})
    assert p['label'] in ('Medium', 'High') and 0 < p['score'] <= 100 and p['conf'] == 'medium'
    vanish = next(b for b in p['brands'] if b['brand'] == 'Vanish')
    assert vanish['src'] == 'listing' and 'brand banner' in vanish['formats'] and vanish['slots'] >= 3
    assert 'a brand banner at the top (Vanish)' in p['why'] and '2 video ads' in p['why']


def test_ad_pressure_without_ads_read_and_without_rows():
    rows = [{'asin': 'B%09d' % i, 'title': 'Acme Thing %d' % i, 'sponsored': i < 2} for i in range(20)]
    p = ads.ad_pressure(rows)
    assert p['conf'] == 'low' and [x['key'] for x in p['parts']] == ['share', 'brands'] and 'older search' in p['why']
    assert p['brands'] == [{'brand': 'Acme', 'slots': 2, 'formats': ['sponsored listing'], 'src': 'title'}]
    assert ads.ad_pressure([])['score'] is None
    none = ads.ad_pressure(rows[2:], [])
    assert none['score'] == 0 and none['label'] == 'Low'


def test_brand_guess_skips_filler():
    assert ads.brand_guess('The Pink Stuff Miracle Spray') == 'Pink'
    assert ads.brand_guess("Miss Mouth's Stain Remover") == 'Miss'
    assert ads.brand_guess('') is None
    assert ads.brand_of('B1', 'Acme Pen', {'B1': 'Acme Inc'}) == ('Acme Inc', 'listing')


def test_known_brand_names_win_and_keyword_words_are_never_brands():
    names = ads.brand_names(['Cold', 'Cold Power', 'Vanish', '', 'x'])
    assert names == ['Cold Power', 'Vanish', 'Cold']
    assert ads.brand_of('B1', 'Cold Power Advanced Clean 4L', {}, names) == ('Cold Power', 'listing')
    avoid = ads.avoid_words('stain remover clothes')
    assert ads.brand_of('B2', 'Stain Remover Pen 3 Pack', {}, names, avoid) == (None, None)
    assert ads.brand_of('B3', 'Clothes Stain Spray', {}, names, avoid) == (None, None)
    p = ads.ad_pressure([{'asin': 'B2', 'title': 'Stain Remover Pen', 'sponsored': True}], [], {}, names, 'stain remover')
    assert p['brands'] == [] and p['unbranded'] == 1


# ---------- Seller Explorer and origins ----------
def curve(node, rank):
    return {'v': 10000 / rank, 'lo': 5000 / rank, 'hi': 20000 / rank}


PAGES = [
    {'asin': 'A1', 'seller_id': 'S1', 'buybox_seller': 'Glow', 'fulfillment': 'fba', 'brand': 'Glow', 'badge_low': 100, 'ts': TS},
    {'asin': 'A2', 'seller_id': 'S1', 'buybox_seller': 'Glow', 'fulfillment': 'fbm', 'brand': 'Glow', 'root_rank': 100, 'ts': TS + 10},
    {'asin': 'A3', 'fulfillment': 'amazon', 'buybox_seller': 'Amazon', 'brand': 'Basics', 'root_rank': 50, 'ts': TS},
    {'asin': 'A4', 'merchant_id': 'S2', 'fulfillment': 'fba', 'brand': 'Nova', 'ts': TS},
    {'asin': 'A5', 'fulfillment': None},
]


def test_seller_explorer():
    rows = intel.sellers_table('AU', PAGES, {'S1': {'name': 'Glow Ltd', 'country': 'GB', 'rating_pct': 97.0},
                                             'S2': {'name': 'Nova', 'country': 'CN'}},
                               [{'asin': 'A9', 'offers': [{'seller_id': 'S2', 'fba': False}, {'amazon': True}]}],
                               {'S1': TS - 500}, curve)
    by = {r['name']: r for r in rows}
    glow = by['Glow Ltd']
    assert glow['listings'] == 2 and glow['buybox'] == 2 and glow['ships'] == 'both' and glow['country'] == 'GB'
    assert glow['brands'] == [{'brand': 'Glow', 'listings': 2}] and glow['first_seen'] == TS - 500 and glow['last_seen'] == TS + 10
    assert glow['units']['src'] == ['badge', 'sales-rank curve'] and glow['units']['lo'] <= glow['units']['v'] <= glow['units']['hi']
    amazon = by['Amazon']
    assert amazon['amazon'] and amazon['country'] == 'AU' and amazon['seller_id'] is None and amazon['units']['v'] == 200
    nova = by['Nova']
    assert nova['listings'] == 2 and nova['ships'] == 'both' and nova['units'] is None and nova['share'] is None
    assert abs(sum(r['share'] or 0 for r in rows) - 1) < 0.01 and rows[0]['units']['v'] >= rows[1]['units']['v']
    assert rows[-1]['name'] == 'Nova'                                             # units unknown: last


def test_origins_group_sellers_by_country():
    rows = intel.origins([{'country': 'CN', 'listings': 3, 'units': {'v': 300}}, {'country': 'CN', 'listings': 1, 'units': None},
                          {'country': 'AU', 'listings': 2, 'units': {'v': 100}}, {'country': None, 'listings': 1, 'units': None}])
    assert [(r['country'], r['sellers'], r['share']) for r in rows] == [('CN', 2, 0.75), ('AU', 1, 0.25), (None, 1, 0.0)]


# ---------- Brand Explorer ----------
def test_brand_shares_growth_and_amazon():
    now = [{'asin': 'A1', 'title': 'Glow Spray', 'bought': 1000}, {'asin': 'A2', 'title': 'Glow Gel', 'bought': 100},
           {'asin': 'A3', 'title': 'Basics Spray', 'bought': 300}, {'asin': 'A4', 'title': 'Newco Thing', 'bought': None},
           {'asin': 'A5', 'title': 'Ad Brand', 'sponsored': True, 'bought': 5000}]
    before = [{'asin': 'A1', 'title': 'Glow Spray', 'bought': 300}, {'asin': 'A3', 'title': 'Basics Spray', 'bought': 300}]
    out = intel.brand_shares(now, 'AU', {'A3': 'Amazon Basics'}, {'A3'}, before)
    b = {x['brand']: x for x in out['brands']}
    assert set(b) == {'Glow', 'Amazon Basics', 'Newco'}                          # the ad is not page-1 sales
    assert b['Glow']['listings'] == 2 and b['Glow']['growth'] > 0 and b['Glow']['src'] == 'title'
    assert b['Amazon Basics']['amazon'] and b['Amazon Basics']['src'] == 'listing' and b['Amazon Basics']['growth'] == 0
    assert b['Newco']['new'] is True and b['Newco']['share'] is None
    assert out['amazon_listings'] == 1 and out['listings'] == 4 and out['top_share'] == b['Glow']['share']
    assert intel.brand_shares([], 'AU')['brands'] == []


# ---------- Category gap explorer ----------
def test_category_gaps_share_imported_amazon_local():
    checks = [{'market': 'AU', 'gap_type': t, 'data': {'category': 'Kitchen'}}
              for t in ('lone_abroad', 'imported', 'amazon_only', 'few', 'crowded', 'missing', 'unread')]
    checks += [{'market': 'AE', 'gap_type': 'few', 'data': {}}]
    rows = intel.category_gaps(checks)
    k = rows[0]
    assert (k['category'], k['market'], k['checked'], k['unread']) == ('Kitchen', 'AU', 6, 1)
    assert (k['imported'], k['amazon_only'], k['missing'], k['local'], k['open']) == (2, 1, 1, 2, 4)
    assert k['share_open'] == round(4 / 6, 3) and k['share_local'] == round(2 / 6, 3)
    assert rows[1]['category'] == 'Other' and rows[1]['share_open'] == 0


# ---------- Related products ----------
def test_related_graph_finds_competitors_and_add_ons():
    links = [{'asin': 'C', 'kind': 'viewed', 'other': 'V1', 'pos': 1}, {'asin': 'C', 'kind': 'bought', 'other': 'B1', 'pos': 1},
             {'asin': 'C', 'kind': 'bought', 'other': 'V1', 'pos': 2}, {'asin': 'X', 'kind': 'viewed', 'other': 'C', 'pos': 3},
             {'asin': 'C', 'kind': 'viewed', 'other': 'C', 'pos': 4}]
    titles = {'V1': 'Oven Cleaner Spray Heavy Duty', 'B1': 'Microfibre Cleaning Cloths 12 Pack', 'X': 'Oven Cleaner Gel'}
    g = intel.related_graph('C', 'Glisten Oven Cleaner Spray 500ml', links, titles, {'V1': 'Easy-Off'})
    n = {x['asin']: x for x in g['nodes']}
    assert set(n) == {'V1', 'B1', 'X'} and n['V1']['kinds'] == ['viewed', 'bought'] and n['V1']['role'] == 'competitor'
    assert n['B1']['role'] == 'add-on' and n['X']['links_back'] and n['X']['kinds'] == []
    assert [x['asin'] for x in g['addons']] == ['B1'] and g['brands'] == [{'brand': 'Easy-Off', 'n': 1}] and g['read']
    assert intel.related_graph('C', 'x', [], {})['read'] is False


# ---------- Amazon out of stock ----------
def test_amazon_stockout_alerts_once_per_outage():
    before = {'fulfillment': 'amazon', 'available': True, 'ts': TS}
    gone = intel.amazon_stockout('AU', 'B0X', 'Kettle', before, {'fulfillment': None, 'available': False}, TS + 86400)
    assert gone['rule'] == 'amazon_stockout' and gone['severity'] == 3 and 'out of stock' in gone['title']
    assert gone['dedupe_key'] == 'stockout|AU|B0X|%d' % TS
    lost = intel.amazon_stockout('AE', 'B0X', 'Kettle', before, {'fulfillment': 'fba', 'available': True,
                                                                  'buybox_seller': 'Dubai Shop'}, TS)
    assert lost['severity'] == 2 and 'Dubai Shop' in lost['body'] and 'UAE' in lost['body']
    assert intel.amazon_stockout('AU', 'B0X', 'K', before, before, TS) is None
    assert intel.amazon_stockout('AU', 'B0X', 'K', {'fulfillment': 'fba'}, {'available': False}, TS) is None
    assert intel.amazon_stockout('AU', 'B0X', 'K', None, before, TS) is None


# ---------- Listing scorecard ----------
def test_scorecard_against_the_top_10():
    top = [{'title_len': 150, 'bullets': 5, 'images': 7, 'video': i < 6, 'aplus': i < 3, 'rating': 4.5, 'reviews': 800,
            'price': 20.0} for i in range(10)]
    mine = {'title_len': 60, 'bullets': 5, 'images': 3, 'video': False, 'aplus': True, 'rating': 4.0, 'reviews': 1500, 'price': 25.0}
    s = intel.scorecard(mine, top)
    v = {i['key']: i['verdict'] for i in s['items']}
    assert v == {'title_len': 'behind', 'bullets': 'level', 'images': 'behind', 'video': 'behind', 'aplus': 'ahead',
                 'rating': 'behind', 'reviews': 'ahead', 'price': 'info'}
    tips = {i['key']: i['tip'] for i in s['items']}
    assert 'Add a video: 6 of the top 10 have one.' == tips['video'] and '25% above' in tips['price']
    assert s['score'] == round(100 * (2 + 0.6) / 7) and s['n_top'] == 10
    assert intel.scorecard(None, top)['read'] is False
    unknown = intel.scorecard({'title_len': 80}, [None, {}])
    assert all(i['verdict'] in ('unknown', 'info') for i in unknown['items']) and unknown['score'] is None
