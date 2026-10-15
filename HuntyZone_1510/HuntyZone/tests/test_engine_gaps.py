"""Global Gap Finder rules (engine.gaps): proof, grouping, the generic phrase, the gap types, warnings, second seller."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import gaps  # noqa: E402

TS = 1_760_000_000


def offer(seller_id='S1', seller='Shop', country='CN', amazon=False, stock='abroad', days=12, fba=False):
    return {'seller_id': seller_id, 'seller': seller, 'country': country, 'amazon': amazon, 'fba': fba, 'stock': stock,
            'days': days, 'buybox': True}


def listing(*offers, n=None, status='ok', available=True):
    return {'status': status, 'available': available, 'n_offers': n if n is not None else len(offers), 'offers': list(offers)}


def search(rows=(), phrase='oven cleaner spray', status='ok'):
    return {'status': status, 'phrase': phrase, 'rows': list(rows)}


def srow(asin, title, origin='local', sponsored=False, offer=None):
    return {'asin': asin, 'title': title, 'origin': origin, 'days': 2 if origin == 'local' else 14, 'sponsored': sponsored,
            'offer': offer}


# ---------- proof ----------
def test_a_best_sellers_top_100_place_proves_a_market():
    w = gaps.selling_well([{'kind': 'best', 'rank': 12, 'category': 'Kitchen'}], 'US', 300)
    assert w['well'] is True and '#12 in Best Sellers, Kitchen' in w['why'] and w['units'] is None
    assert gaps.selling_well([{'kind': 'best', 'rank': 150}], 'US', 300)['well'] is None


def test_units_prove_a_market_with_their_range_and_source():
    w = gaps.selling_well([{'kind': 'h10', 'units': 1000}], 'GB', 100)
    assert w['well'] is True and w['src'] == 'Helium 10' and (w['units']['lo'], w['units']['hi']) == (750, 1250)
    low = gaps.selling_well([{'kind': 'h10', 'units': 80}], 'GB', 100)
    assert low['well'] is False and 'about 80 a month (Helium 10)' in low['why']
    b = gaps.selling_well([{'kind': 'badge', 'low': 1000}], 'US', 300)
    assert b['well'] is True and 'badge' in b['src'] and b['units']['lo'] >= 1000
    c = gaps.selling_well([{'kind': 'curve', 'units': {'v': 900, 'lo': 500, 'hi': 1500}}], 'US', 300)
    assert c['well'] is True and c['src'] == 'sales-rank curve'
    both = gaps.selling_well([{'kind': 'curve', 'units': {'v': 9000, 'lo': 5000, 'hi': 15000}}, {'kind': 'h10', 'units': 400}], 'US', 300)
    assert both['src'] == 'Helium 10'                                    # reported numbers win over the curve


def test_movers_and_shakers_alone_only_says_rising():
    w = gaps.selling_well([{'kind': 'movers', 'pct': 350, 'rank': 3}], 'US', 300)
    assert w['well'] is None and 'rising on Movers & Shakers (+350%)' in w['why']
    assert gaps.selling_well([{'kind': 'movers', 'pct': 350, 'rank': 3, 'rank_now': 640}], 'GB', 100)['well'] is None
    assert gaps.selling_well([], 'US', 300) == {'well': None, 'units': None, 'src': None, 'why': 'no sales signal read'}


def test_a_mover_now_in_the_best_sellers_top_100_proves_a_market():
    m = gaps.selling_well([{'kind': 'movers', 'pct': 900, 'rank': 1, 'rank_now': 85, 'rank_before': 2140,
                            'category': 'Kitchen'}], 'DE', 100)
    assert m['well'] is True and m['why'] == '#85 in Best Sellers now, up from #2,140 (Movers & Shakers, Kitchen)'
    both = gaps.selling_well([{'kind': 'best', 'rank': 12, 'category': 'Kitchen'},
                              {'kind': 'movers', 'pct': 50, 'rank': 9, 'rank_now': 40}], 'GB', 100)
    assert both['why'] == '#12 in Best Sellers, Kitchen'                 # the better Best Sellers place is enough


def test_proven_needs_two_markets():
    one = gaps.proof({'US': [{'kind': 'best', 'rank': 5}], 'GB': [{'kind': 'movers', 'pct': 90}]}, {'US': 300, 'GB': 100})
    assert one['n'] == 1 and not one['proven']
    two = gaps.proof({'US': [{'kind': 'best', 'rank': 5}], 'DE': [{'kind': 'h10', 'units': 300}]}, {'US': 300, 'DE': 100})
    assert two['n'] == 2 and two['proven'] and set(two['markets']) == {'US', 'DE'}


# ---------- the same product across markets ----------
def test_group_joins_the_same_asin_and_the_same_brand_with_alike_titles():
    items = [
        {'market': 'GB', 'asin': 'B0A', 'title': 'Bedsure Fleece Blanket Throw Size Grey', 'brand': 'Bedsure'},
        {'market': 'US', 'asin': 'B0B', 'title': 'Bedsure Fleece Blanket Throw Size Navy', 'brand': 'Bedsure'},
        {'market': 'DE', 'asin': 'B0A', 'title': 'Bedsure Kuscheldecke', 'brand': None},
        {'market': 'US', 'asin': 'B0C', 'title': 'Bedsure Cooling Pillow Queen', 'brand': 'Bedsure'},
        {'market': 'US', 'asin': 'B0D', 'title': 'Other Fleece Blanket Throw Size Grey', 'brand': 'Other'},
    ]
    groups = gaps.group(items)
    big = groups[0]
    assert big['key'] == 'B0B' and {(i['market'], i['asin']) for i in big['items']} == {('US', 'B0B'), ('GB', 'B0A'), ('DE', 'B0A')}
    assert [i['market'] for i in big['items']] == ['US', 'GB', 'DE']       # the USA leads
    assert len(groups) == 3


def test_group_uses_the_first_title_word_when_the_brand_is_unknown():
    items = [{'market': 'US', 'asin': 'B01', 'title': 'Glisten Oven Cleaner Spray Heavy Duty 500ml', 'brand': None},
             {'market': 'GB', 'asin': 'B02', 'title': 'Glisten Oven Cleaner Spray Heavy Duty 500 ml', 'brand': 'Glisten'},
             {'market': 'DE', 'asin': 'B03', 'title': 'Shiny Oven Cleaner Spray Heavy Duty', 'brand': None}]
    keys = [{i['asin'] for i in g['items']} for g in gaps.group(items)]
    assert {'B01', 'B02'} in keys and {'B03'} in keys


# ---------- the phrase shoppers type ----------
def test_generic_phrase_never_has_the_brand():
    title = 'Glisten Oven Cleaner Spray Heavy Duty 500ml'
    assert gaps.generic_phrase(title, 'Glisten', ['glisten oven cleaner', 'oven cleaner spray', 'oven cleaner']) == \
        ('oven cleaner spray', 'suggestion')
    assert gaps.generic_phrase(title, 'Glisten', ['glisten oven cleaner']) == ('oven cleaner spray', 'title')
    assert gaps.generic_phrase('', None, []) == (None, 'title')


def test_same_product_needs_every_phrase_word_in_the_title():
    assert gaps.same_product('oven cleaner spray', 'Easy-Off Oven Cleaners Spray, Fume Free')
    assert not gaps.same_product('oven cleaner spray', 'Oven Cleaner Gel 500ml')
    assert not gaps.same_product('', 'anything')


# ---------- the gap in one market ----------
def test_one_seller_abroad_is_a_lone_exporter():
    g = gaps.classify('AU', listing(offer(country='GB', seller='UK Shop')),
                      search([srow('B0X', 'Kettle Descaler', 'local')]), ['B0LONE'])
    assert g['type'] == 'lone_abroad' and g['gap'] and g['conf'] == 'high'
    assert g['exporter']['country'] == 'GB' and 'UK Shop (United Kingdom)' in g['why'] and 'delivery in 12 days' in g['why']
    assert g['local_rivals'] == 1 and g['sellers'] == 1


def test_one_foreign_seller_with_local_fba_stock_is_a_lone_exporter_too():
    g = gaps.classify('AU', listing(offer(country='CN', stock='local', fba=True, days=2)), search(), ['B0LONE'])
    assert g['type'] == 'lone_fba' and g['gap'] and g['conf'] == 'high'
    unknown = gaps.classify('AU', listing(offer(country=None, stock='local', fba=True)), search(), ['B0LONE'])
    assert unknown['type'] == 'lone_fba' and unknown['conf'] == 'low'


def test_another_listing_of_the_same_product_delivering_locally_ends_the_lone_exporter():
    rows = [srow('B0OTHER', 'Oven Cleaner Spray 500ml by Local Co', 'local', offer=offer('L1', 'Local Co', 'AU', stock='local'))]
    g = gaps.classify('AU', listing(offer(country='GB')), search(rows), ['B0LONE'])
    assert g['type'] == 'few' and g['same_local'] == 1


def test_similar_products_only_count_as_local_rivals():
    rows = [srow('B0R%d' % i, 'Kitchen Degreaser Gel', 'local') for i in range(6)]
    g = gaps.classify('AU', listing(offer(country='GB')), search(rows), ['B0LONE'])
    assert g['type'] == 'lone_abroad' and g['local_rivals'] == 6


def test_sponsored_and_own_listings_are_not_rivals():
    rows = [srow('B0LONE', 'Oven Cleaner Spray', 'local'), srow('B0AD', 'Oven Cleaner Spray', 'local', sponsored=True)]
    g = gaps.classify('AU', listing(offer(country='GB')), search(rows), ['B0LONE'])
    assert g['type'] == 'lone_abroad' and g['local_rivals'] == 1          # the listing itself, not the ad


def test_amazon_only_imported_missing_few_crowded():
    amazon = {'seller_id': None, 'seller': 'Amazon', 'country': 'AU', 'amazon': True, 'fba': True, 'stock': 'local'}
    assert gaps.classify('AU', listing(amazon), search(), ['B0'])['type'] == 'amazon_only'
    two_abroad = listing(offer('S1', country='CN'), offer('S2', country='US'))
    imp = gaps.classify('AE', two_abroad, search(), ['B0'])
    assert imp['type'] == 'imported' and 'China' in imp['why'] and 'United States' in imp['why']
    gone = gaps.classify('AE', listing(status='missing', available=False), search(), ['B0'])
    assert gone['type'] == 'missing' and gone['gap'] and 'Not on Amazon UAE' in gone['why']
    few = gaps.classify('AU', listing(offer('S1', country='AU', stock='local'), offer('S2', country='AU', stock='local')), search(), ['B0'])
    assert few['type'] == 'few' and not few['gap'] and '2 local sellers' in few['why']
    many = gaps.classify('AU', listing(offer('S1', country='AU', stock='local'), n=6), search(), ['B0'])
    assert many['type'] == 'crowded' and many['same_local'] == 6


def test_a_listing_nobody_sells_now_is_missing():
    g = gaps.classify('AU', listing(available=False), search(), ['B0'])
    assert g['type'] == 'missing' and g['unavailable'] and 'nobody sells it now' in g['why']


def test_a_market_that_could_not_be_read_is_a_gap_in_the_data_never_missing():
    g = gaps.classify('AU', {'status': 'captcha'}, {'status': 'captcha', 'rows': []}, ['B0'])
    assert g['type'] == 'unread' and not g['gap'] and 'never' not in g['why'] and 'not as missing' in g['why']
    half = gaps.classify('AU', listing(status='missing', available=False), {'status': 'blocked', 'rows': []}, ['B0'])
    assert half['type'] == 'unread'                                        # not listed, but the search was not read
    assert gaps.classify('AU', None, None, ['B0'])['type'] == 'unread'


def test_unknown_sellers_on_the_listing_are_never_called_a_gap():
    g = gaps.classify('AU', listing(offer(country='GB'), n=3), search(), ['B0'])
    assert g['type'] == 'few' and not g['gap'] and g['conf'] == 'medium'
    assert '2 sellers offer the same product' in g['why'] and 'where they ship from was not read' in g['why']


# ---------- warnings ----------
def test_flags_explain_chemical_and_dangerous_goods():
    f = gaps.flags('Oven Cleaner Spray 500ml', 'Removes grease. Flammable aerosol.', 'Spray', ['warning'], None)
    dg = f[0]
    assert dg['key'] == 'dg' and dg['label'] == 'chemical / dangerous goods'
    assert 'item form: spray' in dg['why'] and 'the page shows warning' in dg['why'] and 'flammable' in dg['why']
    attrs = gaps.flags('Plain Thing', '', None, (), {'hazmat': 'yes'})
    assert attrs and 'Amazon lists it as dangerous goods (hazmat)' in attrs[0]['why']
    assert gaps.flags('Plain Thing', '', None, (), {'hazmat': 'not_applicable'}) == []


def test_flags_leave_harmless_lookalikes_alone():
    assert gaps.flags('Empty Spray Bottle 500ml', '', None) == []
    assert gaps.flags('Cordless Vacuum Cleaner Bags', '', None) == [] or all(x['key'] != 'dg' for x in gaps.flags('Cordless Vacuum Cleaner Bags'))
    assert not [x for x in gaps.flags('Pipe Cleaners for Crafts 100 pack') if x['key'] == 'dg']


def test_flags_for_electrical_kids_and_medical_products():
    keys = {x['key'] for x in gaps.flags('USB Rechargeable Baby Toy Night Light')}
    assert {'electrical', 'kids'} <= keys


# ---------- the lone exporters you watch ----------
def test_a_second_seller_raises_one_alert_per_line_up():
    before = {'offers': [offer('S1')], 'n_offers': 1}
    now = {'offers': [offer('S1'), offer('S2', 'New Shop', 'AU', stock='local')], 'n_offers': 2}
    a = gaps.second_seller('AU', 'B0LONE', 'Oven Cleaner', before, now, TS)
    assert a['rule'] == 'lone_second_seller' and a['severity'] == 3 and a['dedupe_key'] == 'lone|AU|B0LONE|S1,S2'
    assert 'New Shop' in a['body'] and a['data']['asin'] == 'B0LONE' and a['data']['n_offers'] == 2
    assert gaps.second_seller('AU', 'B0LONE', 'x', now, now, TS) is None             # it already had two
    assert gaps.second_seller('AU', 'B0LONE', 'x', before, before, TS) is None
    assert gaps.second_seller('AU', 'B0LONE', 'x', None, now, TS) is None
    counted = gaps.second_seller('AU', 'B0LONE', 'x', before, {'offers': [offer('S1')], 'n_offers': 3}, TS)
    assert counted and 'another seller' in counted['body']                           # only the count says so


def test_seller_keys():
    assert gaps.seller_keys([offer('S2'), {'amazon': True}, offer('S1'), {'seller': None}]) == ['S1', 'S2', 'amazon']
