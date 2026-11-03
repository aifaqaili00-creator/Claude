"""SmartScout exports of Amazon Australia, the UAE and the USA, kept as Market Gaps' data.

Kinds (recognised by their headers): brands and sellers (the baseline: who sells what), subcategories (the Niche
Finder table: every subcategory's size, Amazon and Chinese-seller shares, growth, sellers), products (the Products table
in slices such as "few sellers", and a cross-border seller's Offers) and the SmartScout connector's saved answers
(smartscout-mcp*.json: US profiles of ASINs).

SmartScout's numbers are its own monthly estimates, in the marketplace's money. Each export is kept once (recognised by
the hash of its bytes) with its date, so the Market Gaps page can say how old they are; the app's own live readings
(marketgaps.py) keep the picture fresh after that. Name a file "smartscout <AU|UAE|US> <slice> <YYYY-MM-DD>[ part N].xlsx":
the slice says what was exported ("brands", "products-few-sellers", "seller-products-amazon-us") and parts of one big
export share the date. Amazon's Global Store sellers named in a sellers export ("Amazon US" selling on Amazon Australia)
are remembered as sellers based in that country, and product sales next to a sales rank calibrate the app's own
rank -> sales model (calibration points of kind 'ss').
"""
import json
import logging
import math
import re
from pathlib import Path

import file_rank as fr
import importer
import storage
from engine import marketgaps as mg

log = logging.getLogger('pc')

BRAND_COLS = {'brand name': 'brand', 'brand score': 'score', 'main category': 'category',
              'primary subcategory': 'subcategory', 'est monthly revenue': 'revenue', 'trailing 12 months': 't12m',
              'avg price': 'avg_price', 'avg sellers': 'avg_sellers', 'avg fba sellers': 'avg_fba_sellers',
              'dominant seller': 'dominant_seller', 'sales': 'sales_pct', 'est monthly sales': 'units',
              'amazon in stock rate': 'amazon_instock', 'avg rating': 'rating', 'total reviews': 'reviews',
              '1 month growth': 'growth_1m', '12 month growth': 'growth_12m', 'product count': 'products',
              'country': 'country', 'storefront url': 'storefront_url', 'avg in stock rate 90': 'isr_90',
              'avg buy box suppression': 'bb_suppression', 'total est fba fees': 'fba_fees'}
SELLER_COLS = {'seller': 'name', 'seller name': 'name', 'seller id': 'seller_id', 'category': 'category',
               'primary subcategory': 'subcategory', 'estimated monthly revenue': 'revenue', 'monthly revenue': 'revenue',
               'percent fba': 'pct_fba', 'number of brands selling': 'n_brands', 'number of asins': 'n_asins',
               'country': 'country', 'business name': 'business_name', 'started selling date': 'started', 'url': 'url',
               'state': 'state', 'average price': 'avg_price', '12 month growth': 'growth_12m', 'suspended': 'suspended'}
PRODUCT_COLS = {'asin': 'asin', 'title': 'title', 'brand': 'brand', 'main category name': 'category', 'category': 'category',
                'primary subcategory name': 'subcategory', 'subcategory': 'subcategory', 'buy box price': 'price',
                'offer price': 'price', 'est monthly revenue': 'revenue', 'monthly revenue': 'revenue',
                'est monthly units sold': 'units', 'all sellers': 'sellers', 'fba sellers': 'fba_sellers',
                'listing review count': 'reviews', 'rating': 'rating', 'amazon in stock rate': 'amazon_isr',
                'in stock rate 90 day': 'isr_90', 'buy box suppression': 'bb_suppression', '1 month growth': 'growth_1m',
                '12 month growth': 'growth_12m', 'opportunity score': 'opportunity', 'page score': 'page_score',
                'launch date': 'launch', 'main category rank': 'cat_rank', 'rank': 'cat_rank',
                'primary subcategory rank': 'sub_rank', 'bought in past month': 'bought', 'return rate': 'return_rate',
                'parent asin': 'parent', 'product image': 'image', 'image': 'image', 'buy box percentage': 'bb_pct',
                'fba': 'fba'}
SUBCAT_COLS = {'subcategory': 'name', 'node id': 'node_id', 'main category': 'category', 'monthly revenue': 'revenue',
               'trailing 12 months': 't12m', 'seller revenue share': 'seller_share', 'amazon revenue share': 'amazon_share',
               'chinese seller revenue share': 'chinese_share', '1 month growth': 'growth_1m', '12 month growth': 'growth_12m',
               'number of brands': 'brands', 'number of single seller brands': 'single_seller_brands',
               'number of asins': 'asins', 'asins earning 80 of revenue': 'asins_80', 'avg number of sellers': 'avg_sellers',
               'estimated monthly units sold': 'units', 'average price': 'price', 'average review count': 'reviews_avg',
               'average rating': 'rating', 'average age of listing': 'age_days', 'top brand': 'top_brand',
               'top brand revenue share': 'top_brand_share', 'average page score': 'page_score'}
COLS = {'ss_brands': BRAND_COLS, 'ss_sellers': SELLER_COLS, 'ss_products': PRODUCT_COLS, 'ss_subcats': SUBCAT_COLS}
TEXT = {'brand', 'category', 'subcategory', 'dominant_seller', 'name', 'seller_id', 'country', 'business_name', 'started',
        'storefront_url', 'url', 'state', 'asin', 'title', 'launch', 'parent', 'image', 'seller', 'node_id', 'top_brand'}
INTS = {'reviews', 'products', 'n_brands', 'n_asins', 'sellers', 'fba_sellers', 'cat_rank', 'sub_rank', 'bought', 'brands',
        'single_seller_brands', 'asins', 'asins_80'}
BOOLS = {'fba', 'suspended'}
DATES = {'started', 'launch'}
SHARES = {'seller_share', 'amazon_share', 'chinese_share', 'top_brand_share', 'bb_pct'}   # kept as 0-1
SITES = (('amazon.com.au', 'AU'), ('amazon.ae', 'AE'), ('amazon.com', 'US'))
NAME_MARKETS = {'AU': 'AU', 'AUS': 'AU', 'AUSTRALIA': 'AU', 'UAE': 'AE', 'AE': 'AE', 'US': 'US', 'USA': 'US'}
NAME_RX = re.compile(r'smart\s*scout[\s_-]+(AU|AUS|UAE|AE|US|USA)[\s_-]+(.+?)[\s_-]+\d{4}-\d{2}-\d{2}', re.I)
IMAGE_URL = 'https://m.media-amazon.com/images/I/'
# a cross-border seller's Offers export is named after the seller ("seller-products-amazon-us"): its display name
SELLER_SLUGS = {'amazon-us': 'Amazon US', 'amazon-uk': 'Amazon UK', 'amazon-germany': 'Amazon Germany',
                'amazon-japan': 'Amazon Japan', 'jetsouq': 'JetSouq', 'desertcartae': 'DesertcartAE',
                'exbordersfze': 'ExbordersFZE', 'ubuy': 'Ubuy'}
PRODUCT_SIGNS = {'page score', 'opportunity score', 'all sellers', 'est monthly units sold', 'main category rank',
                 'buy box percentage', 'offer price', 'primary subcategory rank'}
CALIB_PER_CATEGORY = 150        # calibration points kept per main category and import (spread over the ranks)


def kind_of(columns):
    """'ss_brands', 'ss_sellers', 'ss_subcats', 'ss_products' or None (not a SmartScout export)."""
    normed = {fr._norm(c) for c in columns}
    if 'subcategory' in normed and normed & {'node id', 'amazon revenue share', 'chinese seller revenue share'}:
        return 'ss_subcats'
    if {'brand name', 'dominant seller', 'primary subcategory'} <= normed:
        return 'ss_brands'
    if 'seller id' in normed and normed & {'seller', 'seller name'} and normed & {'estimated monthly revenue', 'monthly revenue'}:
        return 'ss_sellers'
    if 'asin' in normed and len(normed & PRODUCT_SIGNS) >= 2:
        return 'ss_products'
    return None


def preset_of(name):
    """The slice a file name says was exported ("smartscout AU products-few-sellers 2026-10-25 part 2.xlsx" ->
    'products-few-sellers'), or None for an old-style name."""
    m = NAME_RX.search(Path(name).stem)
    return m.group(2).strip().lower().replace(' ', '-')[:60] if m else None


def seller_of_preset(preset):
    """'seller-products-amazon-us' -> 'Amazon US' (the seller whose Offers were exported), else None."""
    if not preset or not preset.startswith('seller-products-'):
        return None
    slug = preset[len('seller-products-'):]
    return SELLER_SLUGS.get(slug) or slug.replace('-', ' ').title()


def market_of(name, raw=None):
    """AU / AE / US from the file name ("smartscout - AU brands.xlsx"), else from the Amazon links inside it."""
    for tok in re.split(r'[^A-Z]+', Path(name).stem.upper()):
        if tok in NAME_MARKETS:
            return NAME_MARKETS[tok]
    mk = importer.market_from_name(name)
    if mk in ('AU', 'AE', 'US'):
        return mk
    if raw is not None:
        for c in raw.columns:
            if 'url' in fr._norm(c):
                for v in raw[c].dropna().astype(str).head(200):
                    for site, code in SITES:
                        if site in v:
                            return code
    return None


def _value(key, v):
    if v is None or (isinstance(v, float) and (v != v or math.isinf(v))):
        return None
    if key in BOOLS:
        s = str(v).strip().lower()
        return 1 if s in ('true', '1', 'yes', 'y') else 0 if s in ('false', '0', 'no', 'n') else None
    if key in TEXT:
        s = ' '.join(str(v).split())
        if key in DATES:
            s = s[:10]
        if s in ('-', 'null', 'None'):
            return None
        return s or None
    if isinstance(v, str):
        v = v.replace(',', '').replace('%', '').strip()
        if not v or v == '-':
            return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if key in SHARES and f > 1.5:                      # SmartScout mixes 0.77 and 33.65 (percent) in one table
        f = round(f / 100, 6)
    return int(round(f)) if key in INTS else f


def rows_of(raw, kind, preset=None):
    """The export's rows in the storage.SS_KINDS column shape. Rows without their key (brand, seller, ASIN, subcategory)
    are dropped; an ASIN that appears twice is kept once."""
    want = COLS[kind]
    cols = {}
    for c in raw.columns:
        k = want.get(fr._norm(c))
        if k and k not in cols.values():
            cols[c] = k
    seller = seller_of_preset(preset) if kind == 'ss_products' else None
    out, seen = [], set()
    for rec in raw[list(cols)].to_dict('records'):
        r = {cols[c]: _value(cols[c], v) for c, v in rec.items()}
        if kind == 'ss_brands' and not r.get('brand'):
            continue
        if kind == 'ss_sellers' and not (r.get('seller_id') or r.get('name')):
            continue
        if kind == 'ss_subcats' and not r.get('name'):
            continue
        if kind == 'ss_products':
            a = str(r.get('asin') or '').strip().upper()
            if not re.fullmatch(r'[A-Z0-9]{10}', a) or a in seen:
                continue
            seen.add(a)
            r['asin'] = a
            img = r.get('image')
            if img and not img.startswith('http'):
                r['image'] = IMAGE_URL + img
            if seller:
                r['seller'] = seller
        if kind == 'ss_subcats' and r.get('node_id'):
            r['node_id'] = str(r['node_id']).split('.')[0]
        out.append(r)
    return out


CALIB_MIN_UNITS = 50            # the smallest "bought in past month" badge: the floor the sales model corrects for
CALIB_SPREAD = 1.5              # SmartScout's estimate is taken as a range: units / 1.5 .. units x 1.5


def calibration_rows(rows, market, import_id, when, per_category=CALIB_PER_CATEGORY):
    """Calibration points for the rank -> sales model from product rows with a main-category sales rank and units: up to
    per_category per category, spread evenly over the ranks (so a big file does not swamp the fit).

    Every export was filtered by units, so a product deep down the ranks is only in it when it happened to sell well:
    fitted as plain numbers they would flatten the curve. So only products selling CALIB_MIN_UNITS or more are used, each
    as a range (kind 'ss': an interval, like Amazon's badges), and the model fits them given sales >= that floor, the way
    it already corrects for badges that are only shown above it."""
    by = {}
    for r in rows:
        rank, units, cat = r.get('cat_rank'), r.get('units'), r.get('category')
        if rank and rank > 0 and units and units >= CALIB_MIN_UNITS and cat:
            by.setdefault(cat, []).append(r)
    out = []
    for cat, rs in by.items():
        rs.sort(key=lambda r: r['cat_rank'])
        step = max(1, len(rs) / per_category)
        picked = [rs[int(i * step)] for i in range(min(per_category, len(rs)))]
        out += [(market, cat, int(when), math.log(r['cat_rank']), float(r['units']) / CALIB_SPREAD,
                 float(r['units']) * CALIB_SPREAD, 'ss', r['asin'], import_id) for r in picked]
    return out


def seed_global_store(conn, market, rows):
    """Amazon's Global Store sellers of a sellers export, kept as sellers based in their country (only where the app knows
    nothing about them yet). Returns how many were added."""
    ids = mg.global_store_ids(rows, market)
    known = storage.sellers_of(conn, market, list(ids))
    n = 0
    for sid, (name, country) in ids.items():
        if sid in known:
            continue
        storage.save_seller(conn, market, sid, {'name': name, 'business_name': 'Amazon Global Store', 'country': country,
                                                'status': 'smartscout'})
        n += 1
    return n


def import_file(db, path, name=None, market=None, raw=None):
    """Import one SmartScout export (or a saved connector answer, .json). Returns what happened (never raises for a bad
    file)."""
    path = Path(path)
    name = name or path.name
    if path.suffix.lower() == '.json':
        return import_json(db, path, name)
    try:
        sha1 = importer.file_sha1(path)
        if db.read(storage.import_known, sha1):
            return {'file': name, 'status': 'already imported'}
        raw = fr.load(path) if raw is None else raw
        kind = kind_of(raw.columns)
        if not kind:
            return {'file': name, 'status': 'not a SmartScout export'}
        mk = market or market_of(name, raw)
        if mk not in ('AU', 'AE', 'US'):
            return {'file': name, 'status': 'error', 'error': 'Which marketplace? Put AU, UAE or US in the file name.'}
        preset = preset_of(name)
        rows = rows_of(raw, kind, preset)
        when = importer.as_of(path, name)

        def save(conn):
            iid = storage.save_ss_import(conn, kind, mk, name, sha1, rows, when, preset=preset)
            seeded = calib = 0
            if iid and kind == 'ss_sellers':
                seeded = seed_global_store(conn, mk, rows)
            if iid and kind == 'ss_products':
                pts = calibration_rows(rows, mk, iid, when)
                conn.executemany('INSERT INTO calib_point(market, node, ts, ln_bsr, low, high, kind, asin, ref_id) '
                                 'VALUES (?,?,?,?,?,?,?,?,?)', pts)
                calib = len(pts)
                storage.upsert_asins(conn, mk, [r for r in rows if r.get('title')], when)
            return iid, seeded, calib
        iid, seeded, calib = db.write(save, timeout=600)
        mg_cache_clear()
        return {'file': name, 'status': 'imported' if iid else 'already imported', 'kind': kind, 'market': mk,
                'preset': preset, 'rows': len(rows), 'id': iid, 'global_store': seeded, 'calibration': calib}
    except Exception as e:                             # noqa: BLE001 - one bad file must not stop a scan
        log.exception('SmartScout import %s', name)
        return {'file': name, 'status': 'error', 'error': str(e).splitlines()[0][:200] if str(e) else e.__class__.__name__}


def import_json(db, path, name=None):
    """A saved answer of the SmartScout connector: {"source": "smartscout-mcp", "market": "US", "topic", "question",
    "as_of", "rows": [...]} (or the connector's own {"topic", "result": [...]}). Rows keyed by ASIN become US twins."""
    path = Path(path)
    name = name or path.name
    try:
        sha1 = importer.file_sha1(path)
        if db.read(storage.import_known, sha1):
            return {'file': name, 'status': 'already imported'}
        d = json.loads(path.read_text(encoding='utf-8'))
        rows = d.get('rows') if isinstance(d.get('rows'), list) else d.get('result')
        if not isinstance(rows, list):
            return {'file': name, 'status': 'not a SmartScout export'}
        rows = [r for r in rows if isinstance(r, dict)]
        mk = str(d.get('market') or 'US').upper().replace('UAE', 'AE')
        topic = str(d.get('topic') or 'answer')[:80]
        when = importer.as_of(path, name)
        iid = db.write(storage.save_ss_mcp, mk, topic, name, sha1, rows, when)
        mg_cache_clear()
        return {'file': name, 'status': 'imported' if iid else 'already imported', 'kind': 'ss_mcp', 'market': mk,
                'rows': len(rows), 'id': iid}
    except Exception as e:                             # noqa: BLE001
        log.exception('SmartScout answer %s', name)
        return {'file': name, 'status': 'error', 'error': str(e).splitlines()[0][:200] if str(e) else e.__class__.__name__}


def import_folder(db, folder, progress=None):
    """Every SmartScout export in a folder (newest first). Returns the results, imported files first."""
    say = progress or (lambda m: None)
    try:
        files = sorted((p for p in Path(folder).iterdir() if p.is_file() and not p.name.startswith('~$') and
                        (p.suffix.lower() in importer.EXTS or (p.suffix.lower() == '.json' and looks_like(p)))),
                       key=lambda p: -p.stat().st_mtime)
    except OSError as e:
        return [{'file': str(folder), 'status': 'error', 'error': 'Cannot open this folder (%s).' % e.strerror}]
    out = []
    for i, p in enumerate(files, 1):
        say('SmartScout file %d of %d · %s' % (i, len(files), p.name))
        r = import_file(db, p)
        if r['status'] != 'not a SmartScout export':
            out.append(r)
    out.sort(key=lambda r: r['status'] != 'imported')
    return out


def looks_like(path):
    return 'smartscout' in Path(path).name.lower().replace(' ', '').replace('_', '').replace('-', '')


def mg_cache_clear():
    try:
        import marketgaps
        marketgaps.CACHE.clear()
    except Exception:                                  # noqa: BLE001
        pass
