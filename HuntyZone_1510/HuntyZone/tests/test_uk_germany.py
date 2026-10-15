"""The UK and Germany as proof markets: prices, delivery dates and texts, English pages, export names, product-page
facts the Gap Finder reads (availability, merchant, safety), settings, and schema version 6."""
import datetime as dt
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import amazon_check as ac  # noqa: E402
import config  # noqa: E402
import file_rank as fr  # noqa: E402
import ideas  # noqa: E402
import importer  # noqa: E402
import product  # noqa: E402
import storage  # noqa: E402

TODAY = dt.date(2026, 10, 14)


@pytest.mark.parametrize('text,want', [('12,99 €', 12.99), ('1.299,99 €', 1299.99), ('€ 7,5', 7.5), ('£24.95', 24.95),
                                       ('A$ 1,299.00', 1299.0), ('1,299', 1299.0), ('1.299 €', 1299.0), ('', None)])
def test_prices_in_every_format(text, want):
    assert ac.parse_price(text) == want


def test_german_delivery_dates():
    assert ac.delivery_days('Lieferung morgen, 15. Oktober', TODAY) == 1
    assert ac.delivery_days('GRATIS Lieferung übermorgen', TODAY) == 2
    assert ac.delivery_days('Lieferung heute', TODAY) == 0
    assert ac.delivery_days('Lieferung Montag, 20. Okt.', TODAY) == 6
    assert ac.delivery_days('Lieferung 3. - 12. Nov.', TODAY) is not None
    assert ac.delivery_days('Delivery Friday, 17 October', TODAY) == 3              # the UK writes English


def test_shipped_from_abroad_in_german():
    row = ac.classify({'days': 5, 'delivery': 'Versand aus dem Ausland'})
    assert row['origin'] == 'overseas' and row['intl']


def test_english_pages_for_the_uae_and_germany_only():
    assert ac.search_url('DE', 'ofen reiniger') == 'https://www.amazon.de/s?k=ofen+reiniger&language=en_GB'
    assert ac.search_url('GB', 'oven cleaner', 2) == 'https://www.amazon.co.uk/s?k=oven+cleaner&page=2'
    assert ac.with_lang('https://www.amazon.ae/dp/B0X', 'AE') == 'https://www.amazon.ae/dp/B0X?language=en_AE'
    assert product.seller_url('DE', 'A1') == 'https://www.amazon.de/sp?seller=A1&language=en_GB'
    assert ac.MARKETS['GB']['currency'] == '£' and ac.MARKETS['DE']['site'] == 'https://www.amazon.de'


def test_uk_and_german_exports_are_recognised():
    assert importer.market_from_name('UK_oven_cleaner_Xray.csv') == 'GB'
    assert importer.market_from_name('Germany_Black_Box.xlsx') == 'DE'
    assert importer.market_from_name('AU_tower_fan.csv') == 'AU'
    df = pd.DataFrame({'URL': ['https://www.amazon.co.uk/dp/B0X']})
    assert fr.detect_market(df, {'url': 'URL'}, 'export.csv') == 'GB'
    assert fr.detect_market(pd.DataFrame({'URL': ['https://www.amazon.de/dp/B0X']}), {'url': 'URL'}, 'x.csv') == 'DE'
    assert fr.TARGETS['GB']['currency'] == '£' and fr.MARKET_NAMES['DE'] == 'Germany'


def test_proof_markets_have_built_in_best_sellers_categories():
    assert ideas.LISTS['best'] == '/gp/bestsellers'
    assert ('kitchen', 'Home & Kitchen') in ideas.DEFAULTS['GB'] and ('garden', 'Garden') in ideas.DEFAULTS['DE']


def test_product_page_availability_merchant_and_safety():
    raw = {'title': 'Oven Cleaner', 'availability': 'Currently unavailable.', 'merchant_id': ' a1b2c3 ',
           'rows': {'Item form': 'Spray'}, 'safety': 'Safety Information: Flammable. Keep away from heat.', 'sds': True,
           'bullets': 'Cuts grease fast'}
    p = product.parse_product(raw, 'AU', TODAY)
    assert p['available'] is False and p['merchant_id'] == 'A1B2C3' and p['item_form'] == 'Spray'
    assert 'flammable' in p['safety'] and 'safety data sheet' in p['safety'] and p['bullets'] == 'Cuts grease fast'
    assert product.parse_product({'title': 'x', 'price': '$12.00'}, 'US', TODAY)['available'] is True
    assert product.parse_product({'title': 'x'}, 'US', TODAY)['available'] is None
    assert product.page_status({'status': 'missing', 'title': ''}) == 'missing'
    assert product.COUNTRY['GB'] == 'united kingdom'


def test_settings_for_the_gap_finder():
    s = config.defaults()
    assert s['locations']['GB'] == 'SW1A 1AA' and s['locations']['DE'] == '10115'
    assert s['gap_auto'] is True and (s['proof_us'], s['proof_gb'], s['proof_de']) == (300, 100, 100)
    new, errors = config.validate({'proof_gb': '250', 'locations': {'DE': '80331'}}, s)
    assert not errors and new['proof_gb'] == 250 and new['locations']['DE'] == '80331' and new['locations']['AU'] == '2000'
    assert set(s['categories']) == set(config.ALL_MARKETS) and config.MARKETS == ('US', 'AU', 'AE')


def test_schema_6_keeps_offers_and_gap_checks(tmp_path):
    db = storage.DB(tmp_path / 'm.db').open()
    p = product.parse_product({'title': 'Oven Cleaner', 'price': '$9.00', 'merchant_id': 'M1', 'rows': {'Item form': 'Gel'}},
                              'AU', TODAY)
    p['status'] = 'ok'
    db.write(storage.save_product, 'AU', 'B0X', p)
    got = db.read(storage.latest_page, 'AU', 'B0X')
    assert got['merchant_id'] == 'M1' and got['available'] == 1 and got['item_form'] == 'Gel' and got['title'] == 'Oven Cleaner'
    gone = product.parse_product({}, 'AE')
    gone['status'] = 'missing'
    db.write(storage.save_product, 'AE', 'B0X', gone)
    assert db.read(storage.latest_page, 'AE', 'B0X')['status'] == 'missing'
    assert db.read(storage.latest_page, 'AE', 'B0X', time.time() + 60) is None
    db.write(storage.save_offers, 'AU', 'B0X', [{'seller_id': 'S1'}], {'n_offers': 1, 'available': True}, 'page', 'ok', 100)
    db.write(storage.save_offers, 'AU', 'B0X', [{'seller_id': 'S1'}, {'seller_id': 'S2'}], {'n_offers': 2}, 'spapi', 'ok', 200)
    assert db.read(storage.latest_offers, 'AU', 'B0X')['n_offers'] == 2
    assert db.read(storage.latest_offers, 'AU', 'B0X', 200)['source'] == 'page'
    db.write(storage.save_gap_check, 'AU', 'B0X', 'B0X', 'oven cleaner', 'ok', 'missing', {'a': 1}, 100)
    db.write(storage.save_gap_check, 'AU', 'B0X', 'B0X', 'oven cleaner', 'ok', 'few', {'a': 2}, 200)
    db.write(storage.save_gap_check, 'AE', 'B0X', 'B0X', 'oven cleaner', 'unread', 'unread', {}, 150)
    rows = db.read(storage.gap_checks)
    assert [(r['market'], r['gap_type'], r['data']) for r in rows] == [('AU', 'few', {'a': 2}), ('AE', 'unread', {})]
    out = db.write(storage.retention, 200 + 181 * 86400)
    assert out['gap_checks_dropped'] == 1 and out['offer_readings_dropped'] == 1
    db.close()


def test_a_version_5_database_gets_the_gap_finder_tables(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'SCHEMA_VERSION', 5)
    d = storage.DB(tmp_path / 'm.db').open()
    d.close()
    monkeypatch.undo()
    d = storage.DB(tmp_path / 'm.db').open()
    assert d.one("SELECT name FROM sqlite_master WHERE name='gap_check'") and d.one("SELECT name FROM sqlite_master WHERE name='offer_snapshot'")
    assert {'available', 'merchant_id', 'safety_json'} <= {r['name'] for r in d.all('PRAGMA table_info(product_snapshot)')}
    assert list((tmp_path / 'backup').glob('pre-migrate-v5-*.db'))
    d.close()
