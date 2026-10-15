"""Market Gaps rules (engine.marketgaps): niches from SmartScout brands, the gap types, the score, brands missing from a
market, the live verdict (never "open" when Amazon could not be read) and Best Sellers concentration."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import gaps  # noqa: E402
from engine import marketgaps as mg  # noqa: E402


def brand(name, sub, rev, seller='', sellers=1.0, price=40, reviews=200, products=10, cat='Home', growth=None):
    return {'brand': name, 'subcategory': sub, 'revenue': rev, 'dominant_seller': seller, 'avg_sellers': sellers,
            'avg_price': price, 'reviews': reviews, 'products': products, 'category': cat, 'growth_12m': growth}


def test_seller_roles_and_niche_keys():
    assert mg.seller_role('Amazon AU', 'AU') == 'amazon'
    assert mg.seller_role('Amazon US', 'AU') == 'global_store' and mg.seller_role('Amazon Germany', 'AE') == 'global_store'
    assert mg.seller_role('DesertcartAE', 'AE') == 'reseller' and mg.seller_role('JetSouq', 'AE') == 'reseller'
    assert mg.seller_role('Artiss Furnishings', 'AU') == 'seller' and mg.seller_role('', 'AU') == 'unknown'
    assert mg.niche_key('Leg & Foot Supports') == mg.niche_key('foot and leg support') == 'foot leg support'


def test_a_niche_shipped_in_by_the_global_store_is_a_gap():
    rows = [brand('KingGardan', 'Small Kitchen Appliances', 300000, 'Amazon US'),
            brand('Other', 'Small Kitchen Appliances', 20000, 'Amazon Germany'),
            brand('Local', 'Small Kitchen Appliances', 10000, 'Kitchen Co AU', sellers=3)]
    n = mg.niches(rows, 'AU')[0]
    assert n['name'] == 'Small Kitchen Appliances' and n['brands'] == 3 and round(n['cross'], 2) == 0.97
    assert list(n['stores']) == ['US', 'DE']
    g = mg.niche_gap(n)
    assert g['type'] == 'global_store' and g['gap'] and "Global Store 97% (US, DE)" in g['why']


def test_amazon_then_a_big_gap():
    rows = [brand('Bonds', "Men's Pants", 200000, 'Amazon AU', sellers=2.5),
            brand('Next', "Men's Pants", 10000, 'Some Shop', sellers=3), brand('Third', "Men's Pants", 8000, 'X', sellers=3)]
    g = mg.niche_gap(mg.niches(rows, 'AU')[0])
    assert g['type'] == 'amazon_then_gap' and 'Amazon itself takes 92%' in g['why'] and '5% of what Amazon makes' in g['why']
    close = rows[:1] + [brand('Rival', "Men's Pants", 120000, 'Rival AU', sellers=3)] + rows[1:]
    assert mg.niche_gap(mg.niches(close, 'AU')[0])['type'] != 'amazon_then_gap'      # the next brand is close behind


def test_few_brands_needs_a_real_brand_and_few_sellers():
    thin = [brand('A', 'Towing Ropes', 90000, 'Rope Co', sellers=1.0), brand('B', 'Towing Ropes', 5000, 'Y', sellers=1.0)]
    assert mg.niche_gap(mg.niches(thin, 'AE')[0])['type'] == 'thin'
    dust = [brand('B%d' % i, 'Wearables', 1000, 'S%d' % i, sellers=1.0) for i in range(40)]    # nobody has a real share
    assert mg.niche_gap(mg.niches(dust, 'AU')[0])['type'] == 'crowded'


def test_generic_rows_are_left_out_and_risky_niches_are_marked():
    rows = [brand('Generic', "Men's Sports Tracksuits", 5000000), brand('Real', "Men's Sports Tracksuits", 30000, 'Shop'),
            brand('Samsung', 'Mobile Phones', 700000, 'Amazon AU')]
    ns = {n['name']: n for n in mg.niches(rows, 'AU')}
    assert ns["Men's Sports Tracksuits"]['revenue'] == 30000
    assert ns['Mobile Phones']['risky'] and ns['Mobile Phones']['risk_why'] == 'mobile phones'


def test_bigger_in_the_usa_and_the_ranking():
    au = [brand('A', 'Dried Plants', 30000, 'Plant Co', sellers=3), brand('B', 'Dried Plants', 30000, 'Leaf', sellers=3),
          brand('C', 'Dried Plants', 30000, 'Stem', sellers=3), brand('D', 'Dried Plants', 30000, 'Root', sellers=3),
          brand('Big', 'Cutlery', 2000000, 'Fork AU', sellers=5), brand('Big2', 'Cutlery', 1900000, 'Knife AU', sellers=5),
          brand('Big3', 'Cutlery', 1800000, 'Spoon AU', sellers=5), brand('Big4', 'Cutlery', 1700000, 'Ladle AU', sellers=5)]
    us = [brand('X', 'Dried Plants', 20000000, 'Amazon.com'), brand('Y', 'Cutlery', 1000000, 'Amazon.com')]
    out = {n['name']: n for n in mg.rank_niches(au, 'AU', us)}
    plants = out['Dried Plants']
    assert plants['gap']['type'] == 'us_proven' and plants['us_ratio'] > 3 and plants['us_revenue'] == 20000000
    assert out['Cutlery']['gap']['type'] == 'crowded' and not out['Cutlery']['gap']['gap']
    for n in out.values():
        assert 0 <= n['score'] <= 100 and set(n['parts']) == {'demand', 'open', 'barrier', 'price', 'momentum'}
    assert mg.rank_niches(au, 'AU', us, min_revenue=1000000)[0]['name'] == 'Cutlery'


def test_missing_brands_are_big_us_brands_absent_here():
    us = [brand('Homeforia', 'Frames', 3e8, 'Homeforia'), brand('Bonds', 'Pants', 5e5, 'Amazon.com'),
          brand('Small', 'Pans', 1e5, 'X'), brand('Amazon Basics', 'Paper', 2e8, 'Amazon.com'), brand('Generic', 'X', 9e8)]
    au = [brand('bonds', 'Pants', 1000)]
    out = mg.missing_brands(us, au)
    assert [b['brand'] for b in out] == ['Homeforia'] and out[0]['us_revenue'] == 300000000


def test_sellers_summary_and_global_store_ids():
    rows = [{'name': 'Amazon AU', 'seller_id': 'A1', 'revenue': 50}, {'name': 'Amazon US', 'seller_id': 'a2', 'revenue': 30},
            {'name': 'Shop', 'seller_id': 'A3', 'revenue': 15}, {'name': None, 'seller_id': 'A4', 'revenue': 5}]
    s = mg.sellers_summary(rows, 'AU')
    assert s['half'] == 1 and s['top10'] == 1 and s['shares']['amazon'] == 0.5 and s['shares']['global_store'] == 0.3
    assert s['top'][1]['store'] == 'US' and s['shares']['unknown'] == 0.05
    assert mg.global_store_ids(rows, 'AU') == {'A2': ('Amazon US', 'US')}
    assert mg.sellers_summary([], 'AU')['sellers'] == 0


def srow(asin, origin, speed='slow', bought=None, sponsored=False):
    return {'asin': asin, 'title': 'Brand%s Foot Support' % asin, 'origin': origin, 'speed': speed, 'bought': bought,
            'sponsored': sponsored}


def offer(amazon=False, stock='local', country=None):
    return {'seller': 'x', 'amazon': amazon, 'stock': stock, 'country': country}


def test_live_verdict_open_when_shoppers_buy_from_abroad():
    rows = [srow('1', 'overseas', bought=300), srow('2', 'overseas', bought=100), srow('3', 'local', 'fast', bought=50),
            srow('4', 'local', 'fast', sponsored=True)]
    v = mg.live_verdict({'status': 'ok', 'phrase': 'foot support', 'rows': rows},
                        [offer(True, 'abroad', 'US'), offer(stock='abroad', country='CN'), offer(country='AU')], 'AU')
    assert v['verdict'] == 'open' and v['gap'] and v['fast'] == 1 and v['over_share'] == round(400 / 450, 4)
    assert v['buyboxes'] == {'amazon': 0, 'store': 1, 'abroad': 1, 'local': 1, 'unknown': 0} and v['conf'] == 'high'
    assert '1 Global Store' in v['why']


def test_live_verdict_amazon_only_few_closing_closed():
    rows = [srow('1', 'local', 'fast', 100), srow('2', 'local', 'slow', 50)]
    amz = mg.live_verdict({'status': 'ok', 'rows': rows}, [offer(True, 'local', 'AU'), offer(True, 'local', 'AU')], 'AU')
    assert amz['verdict'] == 'amazon_only' and amz['gap']
    few = mg.live_verdict({'status': 'ok', 'rows': rows}, [offer(country='AU'), offer(country='AU')], 'AU')
    assert few['verdict'] == 'few' and not few['gap']
    six = [srow(str(i), 'local', 'fast') for i in range(6)]
    assert mg.live_verdict({'status': 'ok', 'rows': six}, [], 'AU')['verdict'] == 'closing'
    twelve = [srow(str(i), 'local', 'fast') for i in range(12)]
    assert mg.live_verdict({'status': 'ok', 'rows': twelve}, [], 'AU')['verdict'] == 'closed'


def test_a_market_that_could_not_be_read_is_never_open():
    for st in ('captcha', 'blocked', 'paused', 'budget', None):
        v = mg.live_verdict({'status': st, 'rows': []}, [], 'AE')
        assert v['verdict'] == 'unread' and not v['gap'] and 'not in the market' in v['why']
    assert mg.live_verdict(None, None, 'AU')['verdict'] == 'unread'
    nothing = mg.live_verdict({'status': 'empty', 'phrase': 'x', 'rows': []}, [], 'AU')
    assert nothing['verdict'] == 'open' and nothing['conf'] == 'low'


def test_niche_phrase_prefers_amazons_suggestion():
    assert mg.niche_phrase('Leg & Foot Supports') == 'leg foot supports'
    assert mg.niche_phrase('Leg & Foot Supports', ['foot support for leg pain', 'leg and foot supports', 'leg rest']) == \
        'leg and foot supports'
    assert mg.niche_phrase('') is None


def test_category_table_brand_concentration_and_buy_boxes():
    items = [{'asin': 'A%d' % i, 'title': ('Acme' if i < 3 else 'Brand%d' % i) + ' Kettle', 'price': 30, 'reviews': 10 * i}
             for i in range(1, 11)]
    t = mg.category_table(items, 'AU', None, {}, (), {'A1': offer(True, 'abroad', 'US'), 'A2': offer(stock='abroad', country='CN'),
                                                     'A3': offer(country='AU')})
    assert t['basis'] == 'rank' and t['brands'][0]['brand'] == 'Acme' and t['brands'][0]['listings'] == 2
    assert t['buyboxes']['store'] == 1 and t['read'] == 3 and t['gap']['type'] == 'global_store'
    curve = mg.category_table(items, 'AU', lambda rank: {'v': 100 / rank}, {}, ())
    assert curve['basis'] == 'curve' and curve['units_total'] == round(sum(100 / r for r in range(1, 11)))


def test_niche_boost_matches_every_word_of_a_strong_niche():
    strong = {mg.niche_key('Leg & Foot Supports'): 88, mg.niche_key('Kettles'): 60}
    assert mg.niche_boost('TASZOOS Leg and Foot Support Pillow', strong) == 88
    assert mg.niche_boost('Glass Kettle 1.7L', strong) == 60 and mg.niche_boost('Oven cleaner', strong) == 0
    assert mg.niche_boost('anything', None) == 0


def test_only_amazons_global_store_is_an_imported_gap():
    """engine.gaps: a listing only Amazon US sells on Amazon Australia, shipped in, is imported (not Amazon only)."""
    store = {'seller_id': None, 'seller': 'Amazon US (Global Store)', 'country': 'US', 'amazon': True, 'fba': True,
             'stock': 'abroad', 'days': 12}
    g = gaps.classify('AU', {'status': 'ok', 'available': True, 'n_offers': 1, 'offers': [store]},
                      {'status': 'ok', 'phrase': 'x', 'rows': []}, ['B0'])
    assert g['type'] == 'imported' and g['gap'] and "Global Store" in g['why'] and 'United States' in g['why']
    both = dict(store, seller_id=None)
    two = gaps.classify('AU', {'status': 'ok', 'available': True, 'n_offers': 2, 'offers': [
        both, {'seller_id': 'S1', 'seller': 'CN Shop', 'country': 'CN', 'amazon': False, 'stock': 'abroad'}]},
        {'status': 'ok', 'phrase': 'x', 'rows': []}, ['B0'])
    assert two['type'] == 'imported' and '2 sellers' in two['why']
