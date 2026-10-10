"""Helium 10 backfill: Xray, Black Box, Magnet and Cerebro exports become history in market.db.

Each export is a snapshot of one month: Xray and Black Box give units and revenue per listing (Helium 10's own
estimates, so they are stored as "reported"), Magnet and Cerebro give monthly search volume per keyword.
The file's date (from its name, else when it was saved) is the "as of" date. A file is imported once,
recognised by the hash of its bytes, so scanning the same Downloads folder again is harmless.
"""
import datetime as dt
import hashlib
import logging
import math
import os
import re
import time
from pathlib import Path

import config
import file_rank as fr
import storage

log = logging.getLogger('pc')

EXTS = ('.csv', '.xlsx', '.xls', '.xlsm')
MAX_FILES = 300                                   # newest files looked at per scan
MAX_AGE_DAYS = 730                                # older files are ignored
DATE_IN_NAME = re.compile(r'(20\d\d)[-_.](\d\d)[-_.](\d\d)')
KEYWORD_COLS = ('keyword phrase', 'keyword', 'search term')
VOLUME_COLS = ('search volume', 'est search volume', 'exact search volume')


def file_sha1(path):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def as_of(path, name=None):
    """The export's date: from a date in the file name (noon UTC), else the time the file was saved."""
    m = DATE_IN_NAME.search(Path(name or path).name)
    if m:
        try:
            return dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 12, tzinfo=dt.timezone.utc).timestamp()
        except ValueError:
            pass
    return Path(path).stat().st_mtime


def market_from_name(name):
    n = Path(name).name.upper()
    for code, keys in (('AU', ('AU_', '_AU_', 'AUSTRALIA')), ('AE', ('AE_', '_AE_', 'UAE')), ('US', ('US_', '_US_', 'USA'))):
        if any(n.startswith(k) or k in n for k in keys):
            return code
    return None


def kind_of(path, columns):
    """xray / blackbox / magnet / cerebro, or None when this is not a Helium 10 export."""
    name = Path(path).name.lower()
    normed = {fr._norm(c) for c in columns}
    if normed & set(KEYWORD_COLS) and normed & set(VOLUME_COLS):
        if 'cerebro' in name or any('cerebro' in c or 'position' in c or 'organic rank' in c for c in normed):
            return 'cerebro'
        return 'magnet'
    has_products = 'asin' in normed and normed & {'title', 'product details', 'product name'}
    has_sales = any(c in normed for c in ('sales', 'monthly sales', 'asin sales', 'parent level sales', 'revenue',
                                          'monthly revenue', 'asin revenue'))
    if not (has_products and has_sales):
        return None
    if 'blackbox' in name.replace('_', '').replace('-', '').replace(' ', ''):
        return 'blackbox'
    if 'xray' in name or 'x-ray' in name or 'display order' in normed:
        return 'xray'
    return 'blackbox' if 'subcategory' in normed or 'number of active sellers' in normed else 'xray'


def _clean(v):
    if v is None:
        return None
    if isinstance(v, float) and (v != v or math.isinf(v)):
        return None
    return v


def _weight_kg(text):
    """'1.2 pounds' -> 0.54. Helium 10 uses pounds unless the text says kg or g."""
    if text is None or (isinstance(text, float) and text != text):
        return None
    s = str(text).lower()
    m = re.search(r'(\d+(?:\.\d+)?)', s.replace(',', ''))
    if not m:
        return None
    n = float(m.group(1))
    if 'kg' in s or 'kilogram' in s:
        return round(n, 3)
    if re.search(r'\d\s*g\b|gram', s):
        return round(n / 1000, 3)
    if 'oz' in s or 'ounce' in s:
        return round(n * 0.02835, 3)
    return round(n * 0.4536, 3)


def _date(v):
    if v is None or (isinstance(v, float) and v != v) or str(v).strip() in ('', '-', 'N/A'):
        return None
    import pandas as pd
    d = pd.to_datetime(str(v), errors='coerce', dayfirst=False)
    return None if d is pd.NaT or d != d else d.strftime('%Y-%m-%d')


def _col(raw, *names):
    normed = {fr._norm(c): c for c in raw.columns}
    for n in names:
        if n in normed:
            return normed[n]
    return None


def read_products(path, market=None):
    """Xray / Black Box export -> (market, rows as dicts for storage.H10_COLS, keyword guess)."""
    raw = fr.load(path)
    mk, d = fr.prepare(path, market or market_from_name(path) or 'auto')
    parent = _col(raw, 'parent level sales')
    asin_sales = _col(raw, 'asin sales', 'monthly sales', 'sales', 'est monthly sales')
    weight = _col(raw, 'weight', 'weight lb', 'item weight')
    rows = []
    raw = raw.loc[raw[_col(raw, 'title', 'product details', 'product name', 'product title')].fillna('').astype(str)
                  .str.strip() != ''].reset_index(drop=True)
    for i, r in d.iterrows():
        src = raw.iloc[i] if i < len(raw) else {}
        sales_asin = fr._num(src[asin_sales]) if asin_sales else r['sales']
        sales_parent = fr._num(src[parent]) if parent else None
        rows.append({
            'asin': str(r['asin']).strip() or None, 'title': str(r['title'])[:300], 'brand': str(r['brand'])[:80] or None,
            'price': _clean(r['price']), 'sales_asin': _clean(sales_asin), 'sales_parent': _clean(sales_parent),
            'revenue': _clean(r['revenue']), 'bsr': int(r['bsr']) if r['bsr'] == r['bsr'] else None,
            'category': str(r['category'])[:120] or None, 'reviews': int(r['reviews']) if r['reviews'] == r['reviews'] else None,
            'rating': _clean(r['rating']), 'fba_fees': _clean(r['fba_fees']), 'size_tier': str(r['size_tier'])[:40] or None,
            'weight_kg': _weight_kg(src[weight]) if weight else None, 'created': _date(r.get('created')),
            'seller_country': str(r['seller_country'])[:40] or None, 'fulfillment': str(r['fulfillment'])[:10] or None,
            'sponsored': 1 if str(r['sponsored']).strip().lower() in ('yes', 'true', '1', 'sponsored') else 0,
        })
    keyword = ''
    try:
        import xray
        keyword = xray.guess_keyword([r['title'] for r in rows])
    except Exception:                                  # noqa: BLE001
        pass
    return mk, rows, keyword


def read_keywords(path):
    """Magnet / Cerebro export -> [{'keyword', 'volume'}] (rows without a volume are skipped)."""
    raw = fr.load(path)
    kc = _col(raw, *KEYWORD_COLS)
    vc = _col(raw, *VOLUME_COLS)
    out = []
    if not kc or not vc:
        return out
    for k, v in zip(raw[kc], raw[vc]):
        n = fr._num(v)
        if isinstance(k, str) and k.strip() and n == n:
            out.append({'keyword': k.strip()[:120], 'volume': int(n)})
    return out


def import_file(db, path, market=None, kind=None, keyword=None, name=None):
    """Import one export. Returns a dict describing what happened (never raises for a bad file)."""
    path = Path(path)
    name = name or path.name
    try:
        sha1 = file_sha1(path)
        if db.read(lambda c: storage.import_known(c, sha1)):
            return {'file': name, 'status': 'already imported'}
        raw = fr.load(path)
        kind = kind or kind_of(name, raw.columns)
        if not kind:
            return {'file': name, 'status': 'not a Helium 10 export'}
        when = as_of(path, name)
        if kind in ('magnet', 'cerebro'):
            mk = market or market_from_name(name) or 'US'
            vols = read_keywords(path)
            seed = keyword or (vols[0]['keyword'] if vols else '')
            iid = db.write(storage.save_import, kind, mk, seed, name, sha1, [], when, vols)
            return {'file': name, 'status': 'imported', 'kind': kind, 'market': mk, 'keywords': len(vols), 'id': iid}
        mk, rows, guess = read_products(path, market)
        iid = db.write(storage.save_import, kind, mk, keyword or (guess if kind == 'xray' else ''), name, sha1, rows, when)
        if iid:
            db.write(save_calibration, mk, iid, rows, when, kind)
        return {'file': name, 'status': 'imported', 'kind': kind, 'market': mk, 'rows': len(rows), 'id': iid,
                'keyword': keyword or guess}
    except Exception as e:                             # noqa: BLE001 - one bad file must not stop a scan
        log.exception('import %s', name)
        return {'file': name, 'status': 'error', 'error': str(e).splitlines()[0][:200] if str(e) else e.__class__.__name__}


def save_calibration(conn, market, import_id, rows, when, kind):
    """Helium 10 sales next to a BSR are calibration points for the BSR -> units curve (Phase 3)."""
    n = 0
    for r in rows:
        bsr, units = r.get('bsr'), r.get('sales_parent') or r.get('sales_asin')
        if bsr and bsr > 0 and units and units > 0:
            conn.execute('INSERT INTO calib_point(market, node, ts, ln_bsr, low, high, kind, asin, ref_id) '
                         'VALUES (?,?,?,?,?,?,?,?,?)',
                         (market, r.get('category') or '', int(when), math.log(bsr), units, units,
                          'h10_parent' if r.get('sales_parent') else 'h10_child', r.get('asin'), import_id))
            n += 1
    return n


def candidates(folders, now=None, max_age_days=MAX_AGE_DAYS):
    """CSV / Excel files in the folders, newest first, that are recent enough to be worth reading."""
    now = now or time.time()
    found = []
    for folder in folders:
        try:
            for p in Path(folder).iterdir():
                if p.suffix.lower() in EXTS and p.is_file() and not p.name.startswith('~$'):
                    mtime = p.stat().st_mtime
                    if now - mtime <= max_age_days * 86400:
                        found.append((mtime, p))
        except OSError:
            continue
    found.sort(key=lambda x: -x[0])
    return [p for _, p in found[:MAX_FILES]]


def scan(db, folders, progress=None):
    """Import every new Helium 10 export in the folders. Returns a list of results (imported files first)."""
    out = []
    files = candidates(folders)
    for i, p in enumerate(files, 1):
        try:
            if not _looks_like_h10(p):
                continue
        except OSError:
            continue
        r = import_file(db, p)
        if r['status'] in ('imported', 'error'):
            out.append(r)
            if progress:
                progress('%s: %s' % (r['file'], r['status']))
    out.sort(key=lambda r: r['status'] != 'imported')
    return out


def _looks_like_h10(path):
    """A cheap header check, so scanning a big Downloads folder stays fast."""
    head = ''
    if path.suffix.lower() == '.csv':
        with open(path, 'rb') as f:
            head = f.read(4000).decode('utf-8', 'ignore').lower()
        if head.startswith('#'):
            head = head.split('\n', 1)[-1]
        first = head.split('\n', 1)[0]
        return 'asin' in first or 'keyword phrase' in first or 'search volume' in first
    return bool(re.search(r'xray|blackbox|black_box|magnet|cerebro|helium', path.name.lower())) or \
        os.path.getsize(path) < 5_000_000
