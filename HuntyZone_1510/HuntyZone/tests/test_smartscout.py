"""SmartScout exports (smartscout.py): brands and sellers detected and kept once with their market, Amazon's Global
Store sellers remembered, and the Downloads scan routing them here."""
import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importer  # noqa: E402
import marketgaps  # noqa: E402
import smartscout  # noqa: E402
import storage  # noqa: E402

BRAND_HEAD = ['Brand Name', 'Brand Score', 'Main Category', 'Primary Subcategory', 'Est. Monthly Revenue', 'Trailing 12 Months',
              'Avg. Price', 'Avg. Volume', 'Avg. FBA Sellers', 'Avg. Sellers', 'Dominant Seller', 'Country', 'Sales %',
              'Est. Monthly Sales', 'Amazon In-Stock Rate', 'Avg. Rating', 'Total Reviews', '1 Month Growth', '12 Month Growth',
              'Product Count', 'Storefront', 'Notes', 'Storefront Url']
SELLER_HEAD = ['', '', 'Seller', 'Seller ID', 'Category', 'Primary Subcategory', 'Estimated Monthly Revenue', 'Percent FBA',
               'Full Brand Coverage', 'Number of ASINs', 'Top ASINs Count', 'State', 'Country', 'Business Name',
               'Number of Brands Selling', 'Started Selling Date', 'URL']


def xlsx(path, sheet, head, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(head)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


def brands_file(tmp_path, name='smartscout - AU brands.xlsx'):
    def b(name, sub, rev, seller):
        return [name, 6.0, 'Home', sub, rev, rev * 12, 50.0, 0, 0.5, 1.2, seller, '', 50, 100, 0.5, 4.5, 300, -0.1, 0.2, 12,
                'false', '', '']
    return xlsx(tmp_path / name, 'Brands', BRAND_HEAD, [
        b('KingGardan', 'Small Kitchen Appliances', 300000.5, 'Amazon US'), b('Local', 'Small Kitchen Appliances', 20000, 'Shop AU'),
        b('Bonds', "Men's Pants", 150000, 'Amazon AU'), b(1234, 'Cake Toppers', 30000, ''), b('', 'Nothing', 5, '')])


def sellers_file(tmp_path, name='SmartScout - AU Sellers - 2k+.xlsx'):
    def s(name, sid, rev):
        return ['', '', name, sid, 'Home', 'Mugs', rev, 0.4, 3, 900, 10, '', '', '', 12, '', 'https://www.amazon.com.au/sp?seller=' + sid]
    return xlsx(tmp_path / name, 'Sellers', SELLER_HEAD, [s('Amazon AU', 'A39IBJ37TRP1C6', 1e8), s('Amazon US', 'A4XRJ8S0WXSO0', 6e7),
                                                          s('Artiss', 'AARTISS', 2.7e6), s('', 'ANONAME', 5000)])


def test_market_from_the_name_or_the_links(tmp_path):
    assert smartscout.market_of('smartscout - AU brands.xlsx') == 'AU'
    assert smartscout.market_of('SmartScout - UAE Sellers.xlsx') == 'AE'
    assert smartscout.market_of('smartscout US brands 200k+.xlsx') == 'US'
    raw = smartscout.fr.load(sellers_file(tmp_path, 'export (3).xlsx'))
    assert smartscout.market_of('export (3).xlsx', raw) == 'AU'


def test_brands_and_sellers_are_imported_once(tmp_path):
    db = storage.DB(tmp_path / 'm.db').open()
    try:
        r = smartscout.import_file(db, brands_file(tmp_path))
        assert r['status'] == 'imported' and r['kind'] == 'ss_brands' and r['market'] == 'AU' and r['rows'] == 4
        rows, as_of = db.read(storage.ss_brands, 'AU')
        by = {x['brand']: x for x in rows}
        assert as_of and by['KingGardan']['revenue'] == 300000.5 and by['KingGardan']['dominant_seller'] == 'Amazon US'
        assert by['1234']['dominant_seller'] is None and by['Bonds']['reviews'] == 300 and by['Bonds']['sales_pct'] == 50
        assert smartscout.import_file(db, brands_file(tmp_path))['status'] == 'already imported'
        s = smartscout.import_file(db, sellers_file(tmp_path))
        assert s['status'] == 'imported' and s['kind'] == 'ss_sellers' and s['rows'] == 4 and s['global_store'] == 1
        known = db.read(storage.sellers_of, 'AU', ['A4XRJ8S0WXSO0', 'A39IBJ37TRP1C6'])
        assert known['A4XRJ8S0WXSO0']['country'] == 'US' and 'A39IBJ37TRP1C6' not in known     # the local Amazon is not "abroad"
        sellers, _ = db.read(storage.ss_sellers, 'AU')
        assert [x['name'] for x in sellers][:2] == ['Amazon AU', 'Amazon US'] and sellers[-1]['name'] is None
        assert {i['kind'] for i in db.read(storage.ss_imports)} == {'ss_brands', 'ss_sellers'}
        assert db.read(storage.imports_for, 'AU', 'kettle') == []            # never taken for a Helium 10 keyword import
    finally:
        db.close()


def test_a_seller_already_read_keeps_its_profile(tmp_path):
    db = storage.DB(tmp_path / 'm.db').open()
    try:
        db.write(storage.save_seller, 'AU', 'A4XRJ8S0WXSO0', {'name': 'Amazon.com Services', 'country': 'US', 'status': 'ok'})
        assert smartscout.import_file(db, sellers_file(tmp_path))['global_store'] == 0
        assert db.read(storage.sellers_of, 'AU', ['A4XRJ8S0WXSO0'])['A4XRJ8S0WXSO0']['name'] == 'Amazon.com Services'
    finally:
        db.close()


def test_the_downloads_scan_and_a_folder_import_route_smartscout_files(tmp_path):
    db = storage.DB(tmp_path / 'm.db').open()
    try:
        folder = tmp_path / 'scout'
        folder.mkdir()
        brands_file(folder)
        sellers_file(folder)
        (folder / 'notes.xlsx').write_bytes(b'not a workbook')
        out = importer.scan(db, [folder])
        assert sorted(r['kind'] for r in out if r['status'] == 'imported') == ['ss_brands', 'ss_sellers']
        again = smartscout.import_folder(db, folder)
        assert {r['status'] for r in again} <= {'already imported', 'error'} and \
            [r for r in again if r['status'] == 'already imported']
        assert smartscout.import_folder(db, tmp_path / 'nope')[0]['status'] == 'error'
        nm = smartscout.import_file(db, xlsx(tmp_path / 'brands.xlsx', 'Brands', BRAND_HEAD, [['A', 1, 'Home', 'Mugs', 9] + [None] * 18]))
        assert nm['status'] == 'error' and 'AU, UAE or US' in nm['error']
    finally:
        db.close()


def test_a_new_import_refreshes_the_ranking(tmp_path):
    db = storage.DB(tmp_path / 'm.db').open()
    try:
        assert marketgaps.ranked(db, 'AU') == []
        smartscout.import_file(db, brands_file(tmp_path))
        ns = marketgaps.ranked(db, 'AU')
        assert ns and ns[0]['name'] == 'Small Kitchen Appliances' and ns[0]['gap']['type'] == 'global_store'
    finally:
        db.close()
