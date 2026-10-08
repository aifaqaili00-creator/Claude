"""Run with:  python -m pytest -q   (from the project folder)"""
import csv
import datetime as dt
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import amazon_check as ac  # noqa: E402
import file_rank as fr  # noqa: E402
import xray  # noqa: E402

XRAY_HEAD = ['Display Order', 'Product Details', 'ASIN', 'URL', 'Image URL', 'Brand', 'Price  AU$', 'Sales', 'Revenue',
             'BSR', 'FBA Fees', 'Active Sellers #', 'Ratings', 'Review Count', 'Images', 'Review velocity', 'Buy Box',
             'Category', 'Size Tier', 'Fulfillment', 'Dimensions', 'Weight', 'Creation Date', 'Seller Country/Region',
             'Sponsored']


def make_xray(path, rows):
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(XRAY_HEAD)
        for i, r in enumerate(rows, 1):
            created = (dt.date.today() - dt.timedelta(days=int(r.get('age', 24) * 30.4))).strftime('%m/%d/%Y')
            w.writerow([i, r.get('title', 'Moving Bags Heavy Duty %d' % i), 'B0TEST%04d' % i,
                        'https://www.amazon.com.au/dp/B0TEST%04d' % i, '', r.get('brand', 'Brand%d' % i),
                        r.get('price', 39.99), r['sales'], round(r['sales'] * r.get('price', 39.99), 2), 1000 + i,
                        r.get('fees', 8.5), 1, r.get('rating', 4.5), r['reviews'], 7, 3, r.get('buybox', 'Seller%d' % i),
                        'Home', r.get('tier', 'Standard'), r.get('ful', 'FBA'), '', '1.2 pounds', created,
                        r.get('country', 'AU'), r.get('ad', 'No')])


def test_delivery_days():
    today = dt.date(2026, 10, 8)
    assert ac.delivery_days('FREE delivery Tomorrow, 9 Oct', today) == 1
    assert ac.delivery_days('FREE delivery Fri, 10 Oct', today) == 2
    assert ac.delivery_days('FREE delivery Sat, Oct 11', today) == 3
    assert ac.delivery_days('Get it 21 Oct - 5 Nov', today) == 13
    assert ac.delivery_days('Arrives Friday, 10 October', today) == 2
    assert ac.delivery_days('delivery 3 Jan - 9 Jan', dt.date(2026, 12, 20)) == 14
    assert ac.delivery_days('Only 3 left in stock - market 5', today) is None
    assert ac.delivery_days('', today) is None


def test_counts_and_prices():
    assert ac.parse_count('2.1K ratings') == 2100
    assert ac.parse_count('1,234') == 1234
    assert ac.parse_count('200+') == 200
    assert ac.parse_price('AED 1,299.00') == 1299.0


def test_summarize_opportunity():
    rows = [{'sponsored': False, 'speed': s, 'reviews': 10, 'bought': None, 'price': 30.0}
            for s in ['fast', 'fast', 'slow', 'slow', 'slow', 'unknown']]
    s = ac.summarize('AU', 'moving bags', 'Sydney 2000', rows)
    assert s['fast'] == 2 and s['slow'] == 3 and s['level'] == 'opportunity' and s['location_ok']


def test_search_words():
    assert fr.search_words('Warmi 5 Pack Extra Large Heavy Duty Moving Bags, with Zippers', 'Warmi') == 'Moving Bags'
    assert fr.search_words('[20 Pack] STEUGO 3 Compartment Meal Prep Containers (39 oz)', 'STEUGO') == 'Compartment Meal Prep Containers'
    assert fr.search_words('VEDEN Sauna Hat, Premium Merino', 'VEDEN') == 'Sauna Hat'


def test_rank_blackbox_style(tmp_path):
    p = tmp_path / 'AU_AMAZON_blackBoxProducts.csv'
    with open(p, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['URL', 'ASIN', 'Title', 'Brand', 'Price', 'Monthly Sales', 'Review Count', 'Reviews Rating',
                    'Listing Age (Months)', 'Seller', 'Fulfillment', 'Sales Trend (90 days) (%)'])
        w.writerow(['https://amazon.com.au/dp/B1', 'B1', 'Moving Bags 5 Pack', 'Warmi', 41.99, 275, 58, 4.8, 7, 'WARMI', 'FBA', 91])
        w.writerow(['https://amazon.com.au/dp/B2', 'B2', 'LED Desk Lamp USB', 'X', 39.0, 400, 20, 4.5, 5, 'X', 'FBA', 0])
        w.writerow(['https://amazon.com.au/dp/B3', 'B3', 'Cheap Cups', 'Y', 9.0, 900, 10, 4.5, 5, 'Y', 'FBA', 0])
        w.writerow(['https://amazon.com.au/dp/B4', 'B4', 'Storage Box', 'Z', 30.0, 120, 900, 4.5, 30, 'Z', 'FBA', 0])
    mk, top, all_, summary = fr.rank(str(p))
    assert mk == 'AU'
    by = {r['asin']: r for r in fr.to_records(all_)}
    assert by['B1']['verdict'] == 'Good pick' and all_.iloc[0]['asin'] == 'B1'
    assert 'electrical' in by['B2']['flags'] and by['B2']['verdict'] == 'Check first'
    assert 'cheap' in by['B3']['flags']
    assert by['B4']['verdict'] == 'Close'          # sells enough but has too many reviews


def test_xray_promising(tmp_path):
    p = tmp_path / 'xray_moving_bags.csv'
    rows = [{'sales': 400 - i * 15, 'reviews': 40 + i * 10, 'age': 6 if i < 5 else 20, 'brand': 'B%d' % i}
            for i in range(20)]
    rows.append({'sales': 300, 'reviews': 5, 'ad': 'Yes', 'age': 2})
    make_xray(p, rows)
    a = xray.analyse(str(p), 'AU')
    assert a['market'] == 'AU' and a['keyword'] == 'moving bags'
    assert a['metrics']['sponsored'] == 1 and a['metrics']['organic'] == 20
    assert a['metrics']['new_winners'] >= 3 and a['metrics']['amazon_top10'] == 0
    assert a['verdict'] == 'Promising', a['reasons']
    assert a['metrics']['left_pct'] > 0.45


def test_xray_hard(tmp_path):
    p = tmp_path / 'xray_hard.csv'
    rows = [{'sales': 3000, 'reviews': 25000, 'brand': 'BigCo', 'ful': 'AMZ', 'buybox': 'Amazon', 'age': 60}
            for _ in range(3)]
    rows += [{'sales': 40, 'reviews': 3000, 'age': 40, 'price': 12.0, 'fees': 6.0} for _ in range(10)]
    make_xray(p, rows)
    a = xray.analyse(str(p), 'AU', 'phone case')
    assert a['keyword'] == 'phone case'
    assert a['verdict'] == 'Hard'
    texts = ' '.join(r['text'] for r in a['reasons'])
    assert 'Amazon itself sells 3' in texts and 'Dominated' in texts


def test_xray_excel_export(tmp_path):
    p = tmp_path / 'x.csv'
    make_xray(p, [{'sales': 200, 'reviews': 50} for _ in range(8)])
    a = xray.analyse(str(p), 'AU')
    out = tmp_path / 'out.xlsx'
    xray.export_excel(a, str(out))
    import openpyxl
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == ['Summary', 'Listings'] and wb['Listings'].max_row == 9


def test_find_new_export(tmp_path, monkeypatch):
    monkeypatch.setenv('PC_DOWNLOADS', str(tmp_path))
    (tmp_path / 'notes.csv').write_text('a,b\n1,2\n')
    make_xray(tmp_path / 'Helium_10_Xray.csv', [{'sales': 100, 'reviews': 5}])
    old = time.time() - 10
    for f in tmp_path.iterdir():
        os.utime(f, (old, old))
    assert xray.find_new_export(time.time() - 60).name == 'Helium_10_Xray.csv'
    assert xray.find_new_export(time.time()) is None
