"""Who sells one listing (engine.offers), from its product page or from the SP-API, in one shape."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import offers  # noqa: E402


def test_stock_of_prefers_the_ships_from_country_then_fba_then_the_delivery_text():
    assert offers.stock_of(ships_country='CN', market='AU') == 'abroad'
    assert offers.stock_of(ships_country='au', market='AU') == 'local'
    assert offers.stock_of(intl=True, fba=True) == 'abroad'
    assert offers.stock_of(origin='overseas') == 'abroad'
    assert offers.stock_of(fba=True) == 'local' and offers.stock_of(origin='local') == 'local'
    assert offers.stock_of() is None


def test_from_page_reads_the_buy_box_offer():
    p = {'fulfillment': 'fbm', 'seller_id': 'A1B2C3', 'buybox_seller': 'Glow Ltd', 'origin': 'overseas', 'delivery_days': 14}
    o = offers.from_page(p, 'AU', {'country': 'GB', 'name': 'Glow Limited'})
    assert o == {'seller_id': 'A1B2C3', 'seller': 'Glow Limited', 'country': 'GB', 'amazon': False, 'fba': False,
                 'stock': 'abroad', 'days': 14, 'buybox': True}
    fba = offers.from_page({'fulfillment': 'fba', 'merchant_id': 'M9', 'buybox_seller': 'X'}, 'AU')
    assert fba['seller_id'] == 'M9' and fba['stock'] == 'local' and fba['country'] is None


def test_amazon_itself_and_its_global_store():
    local = offers.from_page({'fulfillment': 'amazon', 'buybox_seller': 'Amazon AU', 'origin': 'local'}, 'AU')
    assert local['amazon'] and local['country'] == 'AU' and local['seller_id'] is None and local['seller'] == 'Amazon'
    store = offers.from_page({'fulfillment': 'amazon', 'buybox_seller': 'Amazon US', 'origin': 'overseas'}, 'AU')
    assert store['amazon'] and store['stock'] == 'abroad' and store['country'] == 'US'
    assert store['seller'] == 'Amazon US (Global Store)'
    told = offers.from_page({'fulfillment': 'amazon', 'buybox_seller': 'Amazon.co.uk', 'origin': 'local'}, 'AE')
    assert told['stock'] == 'abroad' and told['country'] == 'GB'          # the name says Global Store, whatever the date
    assert offers.store_country('Amazon AU', 'AU') is None and offers.store_country('Amazon Warehouse', 'AU') is None
    assert offers.store_country('Amazon Germany', 'AU') == 'DE' and offers.store_country('Glow Ltd', 'AU') is None


def test_a_page_nobody_sells_on_has_no_offer():
    assert offers.from_page({'available': False, 'fulfillment': 'fbm', 'seller_id': 'A1'}, 'AU') is None
    assert offers.from_page({'title': 'x'}, 'AU') is None
    assert offers.from_page(None, 'AU') is None


PAYLOAD = {
    'Summary': {'TotalOfferCount': 3,
                'NumberOfOffers': [{'condition': 'new', 'fulfillmentChannel': 'Amazon', 'OfferCount': 2},
                                   {'condition': 'new', 'fulfillmentChannel': 'Merchant', 'OfferCount': 1},
                                   {'condition': 'used', 'fulfillmentChannel': 'Merchant', 'OfferCount': 4}],
                'BuyBoxPrices': [{'condition': 'New', 'LandedPrice': {'Amount': 24.95, 'CurrencyCode': 'AUD'}}]},
    'Offers': [{'SellerId': 'AMZAU1', 'IsFulfilledByAmazon': True, 'IsBuyBoxWinner': True, 'ListingPrice': {'Amount': 24.95}},
               {'SellerId': 'a1b2', 'IsFulfilledByAmazon': True, 'ListingPrice': {'Amount': 26.0}},
               {'SellerId': 'C3D4', 'IsFulfilledByAmazon': False, 'ShipsFrom': {'Country': 'CN'}, 'ListingPrice': {'Amount': 19.0}}],
}


def test_from_spapi_reads_every_offer_and_the_summary():
    got, s = offers.from_spapi(PAYLOAD, 'AU', ['amzau1'])
    assert [o['seller_id'] for o in got] == [None, 'A1B2', 'C3D4'] and got[0]['amazon'] and got[0]['buybox']
    assert got[1]['stock'] == 'local' and got[2]['stock'] == 'abroad' and got[2]['fba'] is False
    assert s == {'n_offers': 3, 'n_fba': 2, 'n_fbm': 1, 'amazon': True, 'available': True, 'price': 24.95}


def test_from_spapi_without_a_buy_box_takes_the_lowest_price_and_knows_nobody():
    _, s = offers.from_spapi({'Offers': [{'SellerId': 'X', 'ListingPrice': {'Amount': 30}},
                                         {'SellerId': 'Y', 'ListingPrice': {'Amount': 21.5}}]}, 'AE')
    assert s['price'] == 21.5 and s['n_offers'] == 2 and not s['amazon']
    none, s = offers.from_spapi({'Summary': {'TotalOfferCount': 0}, 'Offers': []}, 'AE')
    assert none == [] and s['available'] is False and s['price'] is None
    assert offers.from_spapi(None, 'AE')[1]['n_offers'] == 0


def test_merge_keeps_one_offer_per_seller_and_fills_gaps():
    a = [{'seller_id': 'S1', 'country': None, 'days': 3}, {'amazon': True, 'seller_id': None}]
    b = [{'seller_id': 'S1', 'country': 'CN', 'days': 9}, {'seller_id': 'S2'}]
    m = offers.merge(a, b)
    assert len(m) == 3 and m[0] == {'seller_id': 'S1', 'country': 'CN', 'days': 3} and m[2]['seller_id'] == 'S2'
