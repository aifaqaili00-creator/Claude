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
        f = brands_file(tmp_path)               # the same file twice (writing it again changes its saved time)
        r = smartscout.import_file(db, f)
        assert r['status'] == 'imported' and r['kind'] == 'ss_brands' and r['market'] == 'AU' and r['rows'] == 4
        rows, as_of = db.read(storage.ss_brands, 'AU')
        by = {x['brand']: x for x in rows}
        assert as_of and by['KingGardan']['revenue'] == 300000.5 and by['KingGardan']['dominant_seller'] == 'Amazon US'
        assert by['1234']['dominant_seller'] is None and by['Bonds']['reviews'] == 300 and by['Bonds']['sales_pct'] == 50
        assert smartscout.import_file(db, f)['status'] == 'already imported'
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


# ---------- the October 2026 harvest: products, offers, subcategories, parts and slices, connector answers ----------
PRODUCT_HEAD = ['Product Image', 'ASIN', 'Page Score', 'Title', 'Brand', 'Est. Monthly Revenue', 'Est. 12 Month Revenue',
                'Est. Monthly Units Sold', 'Est. 12 Month Units Sold', 'Main Category Rank', 'Main Category Name',
                'Primary Subcategory Rank', 'Primary Subcategory Name', '1 Month Growth', '12 Month Growth', 'Opportunity Score',
                'Buy Box Price', 'FBA Sellers', 'All Sellers', 'Amazon In-Stock Rate', 'Listing Review Count', 'Rating',
                'Bought in Past Month', 'Parent ASIN', 'In-Stock Rate 90 Day', 'Buy Box Suppression', 'Launch Date']
OFFER_HEAD = [None, 'Image', 'ASIN', 'Monthly Revenue', 'Buy Box Percentage', 'Brand', 'Category', 'Rank', 'Subcategory', 'FBA',
              'Offer Price']
SUBCAT_HEAD = [None, 'Subcategory', 'Node ID', 'Main Category', 'Monthly Revenue', 'Chinese Seller Revenue Share',
               'Top Brand Revenue Share', 'Top Brand', 'Number of Brands', 'Number of ASINs', 'Seller Revenue Share',
               'Amazon Revenue Share', '1 Month Growth', '12 Month Growth', 'Avg. Number of Sellers', 'Average Age of Listing (days)']
NEW_SELLER_HEAD = [None, None, 'Seller Name', 'Seller ID', 'Category', 'Primary Subcategory', 'Monthly Revenue', 'Average Price',
                   'Percent FBA', 'Number of ASINs', 'Country', 'Business Name', 'Number of Brands Selling', 'Suspended', 'URL']


def product(asin, title, units, sellers, rank=500, cat='Home', sub='Leg Rests', brand='Comfy', isr=1.0):
    return ['%s.jpg' % asin[:4], asin, 7.5, title, brand, units * 40.0, units * 480.0, units, units * 12, rank, cat, 12, sub,
            0.1, 0.5, 6.1, 39.95, 1, sellers, 0, 120, 4.4, units, None, isr, 0, '2026-08-01T00:00:00']


def products_file(folder, name='smartscout AU products-few-sellers 2026-10-25 part 1.xlsx', rows=None):
    return xlsx(folder / name, 'Products', PRODUCT_HEAD, rows or [
        product('B0LEGREST1', 'Leg Rest Pillow for Sleeping', 450, 2, rank=900),
        product('B0LEGREST1', 'duplicate row', 1, 1),
        product('B0COLA0001', 'Cola Soft Drink 30 x 375ml', 9000, 1, rank=10, cat='Pantry Food & Drinks', sub='Soft Drinks'),
        product('B0SMALL001', 'Tiny Seller Thing', 40, 3, rank=40000),
        product('notanasin', 'Bad row', 99, 1)])


def test_product_offer_and_subcategory_exports_are_recognised_and_never_taken_for_helium_10(tmp_path):
    db = storage.DB(tmp_path / 'm.db').open()
    try:
        raw = smartscout.fr.load(products_file(tmp_path))
        assert smartscout.kind_of(raw.columns) == 'ss_products'
        r = importer.import_file(db, products_file(tmp_path))          # SmartScout first, although it looks like an Xray
        assert r['status'] == 'imported' and r['kind'] == 'ss_products' and r['preset'] == 'products-few-sellers'
        assert r['rows'] == 3 and r['calibration'] == 2                 # duplicate and bad ASINs dropped, under 50 a month too
        rows = {p['asin']: p for p in db.read(storage.ss_products, 'AU')}
        leg = rows['B0LEGREST1']
        assert leg['units'] == 450 and leg['sellers'] == 2 and leg['cat_rank'] == 900 and leg['launch'] == '2026-08-01'
        assert leg['image'].startswith('https://m.media-amazon.com/images/I/') and leg['preset'] == 'products-few-sellers'
        obs = db.read(storage.calib_obs, 'AU')
        assert len(obs) == 2 and {o['kind'] for o in obs} == {'badge'} and {o['node'] for o in obs} == {'Home', 'Pantry Food & Drinks'}
        leg_obs = next(o for o in obs if o['asin'] == 'B0LEGREST1')        # a range around SmartScout's estimate
        assert leg_obs['low'] == 300 and leg_obs['high'] == 675
        off = xlsx(tmp_path / 'smartscout AU seller-products-amazon-us 2026-10-25.xlsx', 'Offers', OFFER_HEAD,
                   [[None, '51F6.jpg', 'B0USONLY01', 44496.48, 100, None, 'Beauty', 1562, 'Hair Clippers', 'false', 149.67]])
        o = smartscout.import_file(db, off)
        assert o['kind'] == 'ss_products' and o['preset'] == 'seller-products-amazon-us' and o['calibration'] == 0
        us = db.read(storage.ss_products, 'AU', None, 'seller-products-%')
        assert us[0]['seller'] == 'Amazon US' and us[0]['fba'] == 0 and us[0]['bb_pct'] == 1.0 and us[0]['cat_rank'] == 1562
        sub = xlsx(tmp_path / 'smartscout AU subcategories 2026-10-25.xlsx', 'Subcategories', SUBCAT_HEAD,
                   [[None, 'Leg Rests', 7058125051, 'Home', 120000, 21.9, 33.65, 'Comfy', 40, 300, 0.77, 0.23, -0.07, 1.31, 3, 400]])
        s = smartscout.import_file(db, sub)
        assert s['kind'] == 'ss_subcats' and s['rows'] == 1
        subs, _ = db.read(storage.ss_subcats, 'AU')
        assert subs[0]['node_id'] == '7058125051' and subs[0]['chinese_share'] == 0.219 and subs[0]['amazon_share'] == 0.23
        assert subs[0]['top_brand_share'] == 0.3365 and subs[0]['age_days'] == 400
    finally:
        db.close()


def test_a_big_export_in_parts_is_one_baseline_and_a_filtered_slice_never_replaces_it(tmp_path):
    db = storage.DB(tmp_path / 'm.db').open()
    try:
        def b(name, rev, seller='Shop AU'):
            return [name, 6.0, 'Home', 'Mugs', rev, rev * 12, 50.0, 0, 0.5, 1.2, seller, '', 50, 100, 0.5, 4.5, 300, -0.1, 0.2,
                    12, 'false', '', 'https://www.amazon.com.au/stores/' + name]
        xlsx(tmp_path / 'smartscout AU brands 2026-10-25 part 1.xlsx', 'Brands', BRAND_HEAD, [b('Big', 90000), b('Bigger', 80000)])
        xlsx(tmp_path / 'smartscout AU brands 2026-10-25 part 2.xlsx', 'Brands', BRAND_HEAD, [b('Small', 1500)])
        xlsx(tmp_path / 'smartscout AU brands-amazon-us 2026-10-26.xlsx', 'Brands', BRAND_HEAD, [b('Only US', 5000, 'Amazon US')])
        for f in sorted(tmp_path.glob('smartscout AU brands*.xlsx')):
            assert smartscout.import_file(db, f)['status'] == 'imported'
        rows, as_of = db.read(storage.ss_brands, 'AU')
        assert sorted(r['brand'] for r in rows) == ['Big', 'Bigger', 'Small']        # both parts, never the newer slice
        assert rows[0]['storefront_url'].startswith('https://www.amazon.com.au/stores/')
        ns = xlsx(tmp_path / 'smartscout AU sellers 2026-10-25.xlsx', 'Sellers', NEW_SELLER_HEAD,
                  [[None, None, 'Amazon US', 'A4XRJ8S0WXSO0', 'Home', 'Mugs', 4.4e7, 75.3, 0.26, 2352715, None, None, 8453, 'false',
                    'https://www.amazon.com.au/sp?seller=A4XRJ8S0WXSO0'], [None, None, 'null', 'ANULL', 'Books', 'x', 1e8]])
        s = smartscout.import_file(db, ns)
        assert s['kind'] == 'ss_sellers' and s['rows'] == 2 and s['global_store'] == 1
        sellers, _ = db.read(storage.ss_sellers, 'AU')
        assert [x['name'] for x in sellers] == [None, 'Amazon US'] and sellers[1]['url'].endswith('A4XRJ8S0WXSO0')
        assert sellers[1]['suspended'] == 0 and sellers[1]['avg_price'] == 75.3
    finally:
        db.close()


def test_the_connectors_answers_become_us_twins(tmp_path):
    import json
    db = storage.DB(tmp_path / 'm.db').open()
    try:
        f = tmp_path / 'smartscout-mcp US asin-profiles 2026-10-25.json'
        f.write_text(json.dumps({'source': 'smartscout-mcp', 'market': 'US', 'topic': 'asin-profiles', 'rows': [
            {'ASIN': 'B0LEGREST1', 'Product Title': 'Leg Rest Pillow', 'Monthly Unit Sales Estimate': 2100,
             'Buy Box Price': 29.99, 'Number of Sellers': 3}]}), encoding='utf-8')
        out = importer.scan(db, [tmp_path])
        assert [r['kind'] for r in out if r['status'] == 'imported'] == ['ss_mcp']
        twins = db.read(storage.ss_us_twins, ['B0LEGREST1', 'B0NOTHERE1'])
        assert list(twins) == ['B0LEGREST1'] and twins['B0LEGREST1']['Monthly Unit Sales Estimate'] == 2100
        assert importer.import_file(db, f)['status'] == 'already imported'
    finally:
        db.close()


def test_the_market_gaps_page_gets_smartscout_products_and_who_ships_in(tmp_path):
    db = storage.DB(tmp_path / 'm.db').open()
    try:
        smartscout.import_file(db, brands_file(tmp_path))
        smartscout.import_file(db, sellers_file(tmp_path))
        smartscout.import_file(db, products_file(tmp_path))
        xlsx(tmp_path / 'smartscout AU seller-products-amazon-us 2026-10-25.xlsx', 'Offers', OFFER_HEAD,
             [[None, 'a.jpg', 'B0LEGREST1', 9000, 100, 'Comfy', 'Home', 900, 'Leg Rests', 'true', 39.95]])
        smartscout.import_file(db, tmp_path / 'smartscout AU seller-products-amazon-us 2026-10-25.xlsx')
        B = marketgaps.baseline(db, 'AU')
        finds = {p['asin']: p for p in B['ss_finds']}
        assert finds['B0LEGREST1']['tier'] == 'strong' and finds['B0LEGREST1']['units'] == 450
        assert finds['B0COLA0001']['risk_why'] == 'food & drink' and finds['B0SMALL001']['tier'] == 'look'
        us = next(s for s in B['shipped']['sources'] if s['seller'] == 'Amazon US')
        assert us['role'] == 'global_store' and us['country'] == 'US' and us['brands'][0]['brand'] == 'KingGardan'
        assert us['products'][0]['asin'] == 'B0LEGREST1' and us['products'][0]['title'] == 'Leg Rest Pillow for Sleeping'
    finally:
        db.close()


def test_amazons_own_unnamed_account_is_named_by_its_id():
    from engine import marketgaps as mg
    assert mg.named({'name': None, 'seller_id': 'anegb3wvevkzb'}, 'AU')['name'] == 'Amazon.com.au'
    assert mg.seller_role('Amazon.com.au', 'AU') == 'amazon' and mg.seller_role('Amazon.ae', 'AE') == 'amazon'
    assert mg.named({'name': 'Shop', 'seller_id': 'ANEGB3WVEVKZB'}, 'AU')['name'] == 'Shop'      # a name is never replaced
    assert mg.named({'name': None, 'seller_id': 'ANEGB3WVEVKZB'}, 'AE')['name'] is None          # only in its own market


def test_the_smartscout_tab_reads_each_table_filtered_sorted_and_paged(tmp_path):
    import ssview
    db = storage.DB(tmp_path / 'm.db').open()
    try:
        smartscout.import_file(db, brands_file(tmp_path))
        xlsx(tmp_path / 'SmartScout - AU Sellers.xlsx', 'Sellers', SELLER_HEAD, [
            ['', '', name, sid, 'Home', 'Mugs', rev, 0.4, 3, 900, 10, '', '', '', 12, '', '']
            for name, sid, rev in (('', 'ANEGB3WVEVKZB', 1e8), ('Amazon US', 'A4XRJ8S0WXSO0', 6e7), ('Artiss', 'AARTISS', 2.7e6))])
        smartscout.import_file(db, tmp_path / 'SmartScout - AU Sellers.xlsx')
        smartscout.import_file(db, products_file(tmp_path))
        top = product('B0LEGREST1', 'Leg Rest Pillow for Sleeping', 450, 2, rank=900)
        future = product('B0FUTURE01', 'Dated in the future', 120, 4)
        future[-1] = '2117-11-30T00:00:00'
        smartscout.import_file(db, products_file(tmp_path, 'smartscout AU products-top 2026-10-25.xlsx', [top, future]))
        xlsx(tmp_path / 'smartscout AU seller-products-amazon-us 2026-10-25.xlsx', 'Offers', OFFER_HEAD,
             [[None, 'a.jpg', 'B0OFFER001', 9000, 100, 'Comfy', 'Home', 900, 'Leg Rests', 'true', 39.95]])
        smartscout.import_file(db, tmp_path / 'smartscout AU seller-products-amazon-us 2026-10-25.xlsx')
        xlsx(tmp_path / 'smartscout AU subcategories 2026-10-25.xlsx', 'Subcategories', SUBCAT_HEAD, [
            [None, 'Leg Rests', 1, 'Home', 120000, 0, 0.3, 'Comfy', 40, 300, 0.8, 0.2, 0, 1.31, 3, 400],
            [None, 'Mugs', 2, 'Home', 900000, 0, 0.1, 'Mug Co', 400, 3000, 0.9, 0.1, 0, -0.2, 9, 900],
            [None, 'Mobile Phones', 3, 'Electronics', 5e6, 0, 0.3, 'Samsung', 90, 1500, 0.7, 0.3, 0, 0.1, 80, 1200]])
        smartscout.import_file(db, tmp_path / 'smartscout AU subcategories 2026-10-25.xlsx')

        s = db.read(ssview.summary, 'AU', {})
        assert s['brands']['n'] == 4 and s['categories'][0]['category'] == 'Home'
        assert [b['brand'] for b in s['top_brands']][:1] == ['KingGardan'] and s['top_brands'][0]['role'] == 'global_store'
        assert s['top_niches'][0]['name'] == 'Mobile Phones' and s['growing'][0]['name'] == 'Leg Rests'
        sh = s['sellers']
        assert sh['top'][0]['name'] == 'Amazon.com.au' and sh['top'][0]['role'] == 'amazon' and sh['shares']['amazon'] > 0.6

        n = db.read(ssview.niches, 'AU', {'cat': 'Home', 'sort': 'growth'})
        assert n['total'] == 2 and [r['name'] for r in n['rows']] == ['Leg Rests', 'Mugs'] and n['sort'] == 'growth'
        assert db.read(ssview.niches, 'AU', {'q': 'mug'})['total'] == 1
        assert db.read(ssview.niches, 'AU', {'min': '1000000'})['rows'][0]['risk'] == 'mobile phones'
        page = db.read(ssview.niches, 'AU', {'limit': '1', 'offset': '1'})
        assert page['total'] == 3 and len(page['rows']) == 1 and page['rows'][0]['name'] == 'Mugs' and len(page['map']) == 3
        assert db.read(ssview.niches, 'AU', {'sort': 'name; DROP TABLE x'})['sort'] == 'revenue'    # never the page's text

        b = db.read(ssview.brands, 'AU', {'abroad': '1'})
        assert [r['brand'] for r in b['rows']] == ['KingGardan'] and b['rows'][0]['store'] == 'US'
        assert db.read(ssview.brands, 'AU', {'q': 'pants'})['rows'][0]['brand'] == 'Bonds'

        se = db.read(ssview.sellers, 'AU', {'role': 'global_store'})
        assert [r['name'] for r in se['rows']] == ['Amazon US'] and se['half'] == 1
        assert [r['name'] for r in db.read(ssview.sellers, 'AU', {'sort': 'name'})['rows']] == ['Amazon US', 'Amazon.com.au', 'Artiss']

        p = db.read(ssview.products, 'AU', {})
        assert p['total'] == 4 and 'B0OFFER001' not in {r['asin'] for r in p['rows']}          # offers are on Shipped in
        leg = next(r for r in p['rows'] if r['asin'] == 'B0LEGREST1')                          # in two slices, shown once
        assert leg['preset'] == 'products-top' and leg['units'] == 450
        assert [r['asin'] for r in db.read(ssview.products, 'AU', {'sellers': '1'})['rows']] == ['B0COLA0001']
        assert db.read(ssview.products, 'AU', {'slice': 'products-top'})['total'] == 2
        newest = db.read(ssview.products, 'AU', {'sort': 'launch'})['rows']
        assert newest[-1]['asin'] == 'B0FUTURE01'                                                # an impossible date sorts last
        assert {c['category'] for c in p['categories']} == {'Home', 'Pantry Food & Drinks'}
    finally:
        db.close()
