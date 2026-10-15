"""SmartScout exports (brands and sellers of Amazon Australia, the UAE and the USA) as Market Gaps' baseline.

SmartScout's numbers are its own monthly estimates, in the marketplace's money. Each export is kept once (recognised by
the hash of its bytes) with its date, so the Market Gaps page can say how old the baseline is; the app's own live
readings (marketgaps.py) keep the picture fresh after that. Amazon's Global Store sellers named in a sellers export
("Amazon US" selling on Amazon Australia) are remembered as sellers based in that country.
"""
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
              '1 month growth': 'growth_1m', '12 month growth': 'growth_12m', 'product count': 'products'}
SELLER_COLS = {'seller': 'name', 'seller id': 'seller_id', 'category': 'category', 'primary subcategory': 'subcategory',
               'estimated monthly revenue': 'revenue', 'percent fba': 'pct_fba', 'number of brands selling': 'n_brands',
               'number of asins': 'n_asins', 'country': 'country', 'business name': 'business_name',
               'started selling date': 'started'}
TEXT = {'brand', 'category', 'subcategory', 'dominant_seller', 'name', 'seller_id', 'country', 'business_name', 'started'}
INTS = {'reviews', 'products', 'n_brands', 'n_asins'}
SITES = (('amazon.com.au', 'AU'), ('amazon.ae', 'AE'), ('amazon.com', 'US'))
NAME_MARKETS = {'AU': 'AU', 'AUS': 'AU', 'AUSTRALIA': 'AU', 'UAE': 'AE', 'AE': 'AE', 'US': 'US', 'USA': 'US'}


def kind_of(columns):
    """'ss_brands', 'ss_sellers' or None (not a SmartScout export)."""
    normed = {fr._norm(c) for c in columns}
    if {'brand name', 'dominant seller', 'primary subcategory'} <= normed:
        return 'ss_brands'
    if {'seller', 'seller id', 'estimated monthly revenue'} <= normed:
        return 'ss_sellers'
    return None


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
    if key in TEXT:
        s = ' '.join(str(v).split())
        if key == 'started':
            s = s[:10]
        return s or None
    if isinstance(v, str):
        v = v.replace(',', '').replace('%', '').strip()
        if not v or v == '-':
            return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(round(f)) if key in INTS else f


def rows_of(raw, kind):
    """The export's rows in storage.SS_BRAND_COLS / SS_SELLER_COLS shape (brand rows without a name are dropped)."""
    want = BRAND_COLS if kind == 'ss_brands' else SELLER_COLS
    cols = {}
    for c in raw.columns:
        k = want.get(fr._norm(c))
        if k and k not in cols.values():
            cols[c] = k
    out = []
    for rec in raw[list(cols)].to_dict('records'):
        r = {cols[c]: _value(cols[c], v) for c, v in rec.items()}
        if kind == 'ss_brands' and not r.get('brand'):
            continue
        if kind == 'ss_sellers' and not (r.get('seller_id') or r.get('name')):
            continue
        out.append(r)
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
    """Import one SmartScout export. Returns what happened (never raises for a bad file)."""
    path = Path(path)
    name = name or path.name
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
        rows = rows_of(raw, kind)
        when = importer.as_of(path, name)

        def save(conn):
            iid = storage.save_ss_import(conn, kind, mk, name, sha1, rows, when)
            return iid, (seed_global_store(conn, mk, rows) if iid and kind == 'ss_sellers' else 0)
        iid, seeded = db.write(save, timeout=300)
        mg_cache_clear()
        return {'file': name, 'status': 'imported' if iid else 'already imported', 'kind': kind, 'market': mk,
                'rows': len(rows), 'id': iid, 'global_store': seeded}
    except Exception as e:                             # noqa: BLE001 - one bad file must not stop a scan
        log.exception('SmartScout import %s', name)
        return {'file': name, 'status': 'error', 'error': str(e).splitlines()[0][:200] if str(e) else e.__class__.__name__}


def import_folder(db, folder, progress=None):
    """Every SmartScout export in a folder (newest first). Returns the results, imported files first."""
    say = progress or (lambda m: None)
    try:
        files = sorted((p for p in Path(folder).iterdir() if p.suffix.lower() in importer.EXTS and p.is_file()
                        and not p.name.startswith('~$')), key=lambda p: -p.stat().st_mtime)
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
