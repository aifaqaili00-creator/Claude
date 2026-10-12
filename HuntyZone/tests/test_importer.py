"""Helium 10 backfill: kinds, markets, dates, sales columns, keyword volumes and de-duplication."""
import csv
import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importer  # noqa: E402
import storage  # noqa: E402

XRAY_HEAD = ['Display Order', 'Product Details', 'ASIN', 'URL', 'Brand', 'Price  AU$', 'ASIN Sales', 'ASIN Revenue',
             'Parent Level Sales', 'Parent Level Revenue', 'BSR', 'FBA Fees', 'Ratings', 'Review Count', 'Category',
             'Size Tier', 'Fulfillment', 'Weight', 'Creation Date', 'Seller Country/Region', 'Sponsored']


def write_csv(path, head, rows):
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(head)
        w.writerows(rows)
    return path


def xray_file(folder, name='AU_AMAZON_xray_2026-09-15.csv', n=12):
    rows = []
    for i in range(1, n + 1):
        rows.append([i, 'Moving Bags Heavy Duty Extra Large %d' % i, 'B0X%07d' % i,
                     'https://www.amazon.com.au/dp/B0X%07d' % i, 'Brand%d' % i, 39.99, 100 * i, 3999 * i, 150 * i,
                     5998 * i, 1000 * i, 8.5, 4.4, 30 * i, 'Home & Kitchen', 'Standard', 'FBA', '1.2 pounds',
                     '03/14/2025', 'CN', 'No' if i > 2 else 'Yes'])
    return write_csv(folder / name, XRAY_HEAD, rows)


def magnet_file(folder, name='US_AMAZON_magnet_2026-10-01.csv'):
    return write_csv(folder / name, ['Keyword Phrase', 'Magnet IQ Score', 'Search Volume', 'Competing Products'],
                     [['tower fan', 900, '45,000', '>1000'], ['bladeless tower fan', 500, '6,200', '800'], ['', 1, '5', '1'],
                      ['quiet fan', 300, '-', '10']])


def cerebro_file(folder, name='AU_AMAZON_cerebro_B0X0000001_2026-10-02.csv'):
    return write_csv(folder / name, ['Keyword Phrase', 'Cerebro IQ Score', 'Search Volume', 'Position (Rank)'],
                     [['moving bags', 400, 2400, 3], ['moving boxes', 300, 9100, 41]])


@pytest.fixture
def db(tmp_path):
    d = storage.DB(tmp_path / 'm.db').open()
    yield d
    d.close()


def test_xray_import_keeps_child_and_parent_sales(db, tmp_path):
    r = importer.import_file(db, xray_file(tmp_path))
    assert r['status'] == 'imported' and r['kind'] == 'xray' and r['market'] == 'AU' and r['rows'] == 12
    assert 'moving bags' in r['keyword']
    imp = db.read(storage.imports_for, 'AU')[0]
    assert imp['month'] == '2026-09' and dt.datetime.fromtimestamp(imp['as_of'], dt.timezone.utc).date() == dt.date(2026, 9, 15)
    rows = db.read(storage.h10_rows, imp['id'])
    first = rows[0]
    assert first['sales_asin'] == 100 and first['sales_parent'] == 150 and first['bsr'] == 1000
    assert first['created'] == '2025-03-14' and abs(first['weight_kg'] - 0.544) < 0.01 and first['sponsored'] == 1
    assert db.one('SELECT COUNT(*) AS n FROM calib_point')['n'] == 12
    assert db.one("SELECT kind FROM calib_point LIMIT 1")['kind'] == 'h10_parent'


def test_the_same_file_is_imported_once(db, tmp_path):
    p = xray_file(tmp_path)
    assert importer.import_file(db, p)['status'] == 'imported'
    copy = tmp_path / 'copy of it.csv'
    copy.write_bytes(p.read_bytes())
    assert importer.import_file(db, copy)['status'] == 'already imported'


def test_keyword_exports(db, tmp_path):
    r = importer.import_file(db, magnet_file(tmp_path))
    assert r['kind'] == 'magnet' and r['market'] == 'US' and r['keywords'] == 2
    vols = db.read(storage.kw_volume, 'US', 'tower fan')
    assert vols[0]['volume'] == 45000 and vols[0]['month'] == '2026-10'
    r = importer.import_file(db, cerebro_file(tmp_path))
    assert r['kind'] == 'cerebro' and r['market'] == 'AU'
    assert db.read(storage.kw_volume, 'AU', 'moving boxes')[0]['volume'] == 9100


def test_blackbox_export_from_the_user(db, tmp_path):
    src = Path('/root/.claude/uploads/50434fb3-d4bb-5ed1-b062-b01a93ebba9a/9257cf85-AU_AMAZON_blackBoxProducts_1_2026-10-08.csv')
    if not src.exists():
        pytest.skip('sample export not available')
    p = tmp_path / 'AU_AMAZON_blackBoxProducts_1_2026-10-08.csv'
    p.write_bytes(src.read_bytes())
    r = importer.import_file(db, p)
    assert r['status'] == 'imported' and r['kind'] == 'blackbox' and r['market'] == 'AU' and r['rows'] >= 100
    rows = db.read(storage.h10_rows, r['id'])
    assert sum(1 for x in rows if x['sales_asin']) > 50 and all(x['asin'] for x in rows)


def test_other_files_are_ignored(db, tmp_path):
    p = write_csv(tmp_path / 'bank.csv', ['Date', 'Amount'], [['2026-01-01', 5]])
    assert importer.import_file(db, p)['status'] == 'not a Helium 10 export'
    bad = tmp_path / 'AU_AMAZON_xray_broken.xlsx'
    bad.write_bytes(b'not really excel')
    assert importer.import_file(db, bad)['status'] == 'error'


def test_scan_imports_new_files_only(db, tmp_path):
    folder = tmp_path / 'Downloads'
    folder.mkdir()
    xray_file(folder)
    magnet_file(folder)
    cerebro_file(folder)
    write_csv(folder / 'notes.csv', ['a', 'b'], [[1, 2]])
    out = importer.scan(db, [folder])
    assert sorted(r['kind'] for r in out if r['status'] == 'imported') == ['cerebro', 'magnet', 'xray']
    assert importer.scan(db, [folder]) == []
    assert importer.scan(db, [tmp_path / 'missing']) == []


def test_weight_and_kind_helpers():
    assert importer._weight_kg('2 pounds') == 0.907
    assert importer._weight_kg('500 g') == 0.5
    assert importer._weight_kg('1.5 kg') == 1.5
    assert importer._weight_kg('16 ounces') == 0.454
    assert importer._weight_kg(None) is None and importer._weight_kg('n/a') is None
    assert importer.market_from_name('AE_AMAZON_xray.csv') == 'AE' and importer.market_from_name('x.csv') is None
