"""Market Gaps -> SmartScout: SmartScout's own tables of one market (its summary, niches, brands, sellers and products),
for the charts and tables of the SmartScout tab. Each comes from the newest import of its kind (a whole export, or every
product slice but the cross-border sellers' offers), filtered, sorted and paged in the database: a page asks for 50 rows
of the 75,000+ products, never for all of them."""
import datetime as dt

import storage
from engine import marketgaps as mg
from engine import offers as offmod

PAGE = 50
MAP_DOTS = 300                     # niches on the niche map: the biggest under the filters
TOP = 12                           # bars in each Summary chart
GROW_MIN = 0.002                   # "fastest growing": niches making at least this share of the market's money
NEW_DAYS = 180
GENERIC = tuple(sorted(mg.GENERIC))
CACHE = {}
# what each table can be sorted by: the page's key -> the column (whitelisted: never the page's text in SQL)
SORTS = {
    'niches': {'revenue': 'revenue', 'units': 'units', 'growth': 'growth_12m', 'brands': 'brands', 'sellers': 'avg_sellers',
               'amazon': 'amazon_share', 'top': 'top_brand_share', 'price': 'price', 'reviews': 'reviews_avg', 'age': 'age_days',
               'name': 'name'},
    'brands': {'revenue': 'revenue', 'growth': 'growth_12m', 'price': 'avg_price', 'reviews': 'reviews', 'rating': 'rating',
               'products': 'products', 'sellers': 'avg_sellers', 'name': 'brand'},
    'sellers': {'revenue': 'revenue', 'brands': 'n_brands', 'asins': 'n_asins', 'price': 'avg_price', 'fba': 'pct_fba',
                'name': 'name'},
    'products': {'revenue': 'p.revenue', 'units': 'p.units', 'sellers': 'p.sellers', 'price': 'p.price', 'reviews': 'p.reviews',
                 'rating': 'p.rating', 'launch': 'p.launch', 'rank': 'p.sub_rank'},
}
ASC_FIRST = {'name', 'sellers', 'rank', 'price', 'age'}     # sorts that start low to high


def _num(v, cast=float):
    try:
        x = cast(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def _like(q):
    q = ' '.join(str(q or '').split())[:80]
    return '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%' if q else None


def _order(view, f):
    key = f.get('sort') if f.get('sort') in SORTS[view] else 'revenue'
    asc = f.get('dir') == 'asc' if f.get('dir') in ('asc', 'desc') else key in ASC_FIRST
    col = SORTS[view][key]
    return key, 'asc' if asc else 'desc', '(%s IS NULL), %s %s' % (col, col, 'ASC' if asc else 'DESC')


def _page(f):
    return max(0, _num(f.get('offset'), int) or 0), max(1, min(200, _num(f.get('limit'), int) or PAGE))


def _cached(conn, market, name, build):
    """build(conn) worked out once per market and SmartScout import (a new import starts afresh)."""
    newest = conn.execute("SELECT MAX(id) FROM import_file WHERE kind LIKE 'ss\\_%' ESCAPE '\\'").fetchone()[0]
    key = (market, name, newest)
    if key not in CACHE:
        if len(CACHE) > 40:
            CACHE.clear()
        CACHE[key] = build(conn)
    return CACHE[key]


def _sellers(conn, market):
    """Every seller of the newest sellers export, named where SmartScout left Amazon's own account unnamed, with its
    role (mg.seller_role) and store country, biggest first."""
    def build(c):
        rows, as_of = storage.ss_sellers(c, market)
        out = []
        for r in rows:
            r = mg.named(r, market)
            out.append({**r, 'role': mg.seller_role(r.get('name'), market) if r.get('name') else 'unknown',
                        'store': offmod.store_country(r.get('name'), market)})
        return out, as_of
    return _cached(conn, market, 'sellers', build)


def summary(conn, market, f=None):
    """The market in SmartScout's numbers: money and brands by category, who takes the money, the biggest and the
    fastest-growing niches and the biggest brands."""
    def build(c):
        out = {'view': 'summary', 'market': market}
        bids, b_as_of = storage.ss_set(c, 'ss_brands', market)
        cids, c_as_of = storage.ss_set(c, 'ss_subcats', market)
        out['as_of'] = b_as_of or c_as_of
        if bids:
            w, ids = 'import_id IN (%s)' % storage._in(bids), list(bids)
            n, rev = c.execute('SELECT COUNT(*), SUM(revenue) FROM ss_brand WHERE ' + w, ids).fetchone()
            out['brands'] = {'n': n, 'revenue': rev}
            out['categories'] = [{'category': cat or 'No category', 'revenue': r, 'brands': k} for cat, r, k in c.execute(
                'SELECT category, SUM(revenue), COUNT(*) FROM ss_brand WHERE %s GROUP BY category ORDER BY 2 DESC' % w, ids)]
            out['top_brands'] = storage._rows(c.execute(
                'SELECT brand, category, subcategory, revenue, growth_12m, dominant_seller FROM ss_brand WHERE %s AND '
                'LOWER(COALESCE(brand, \'\')) NOT IN (%s) ORDER BY revenue DESC LIMIT %d' % (w, storage._in(GENERIC), TOP),
                ids + list(GENERIC)))
            for b in out['top_brands']:
                b['role'] = mg.seller_role(b.get('dominant_seller'), market) if b.get('dominant_seller') else 'unknown'
        if cids:
            w, ids = 'import_id IN (%s)' % storage._in(cids), list(cids)
            n, rev = c.execute('SELECT COUNT(*), SUM(revenue) FROM ss_subcat WHERE ' + w, ids).fetchone()
            out['niches'] = {'n': n, 'revenue': rev}
            cols = 'name, category, revenue, growth_12m, brands, avg_sellers, amazon_share, top_brand, top_brand_share'
            out['top_niches'] = storage._rows(c.execute(
                'SELECT %s FROM ss_subcat WHERE %s ORDER BY revenue DESC LIMIT %d' % (cols, w, TOP), ids))
            out['growing'] = storage._rows(c.execute(
                'SELECT %s FROM ss_subcat WHERE %s AND growth_12m IS NOT NULL AND revenue >= ? ORDER BY growth_12m DESC '
                'LIMIT %d' % (cols, w, TOP), ids + [(rev or 0) * GROW_MIN]))
        sellers, s_as_of = _sellers(c, market)
        if sellers:
            s = mg.sellers_summary(sellers, market, top=TOP)
            out['sellers'] = {k: s[k] for k in ('total', 'sellers', 'top10', 'half', 'shares', 'top')}
            out['as_of'] = out['as_of'] or s_as_of
        return out
    return _cached(conn, market, 'summary', build)


def niches(conn, market, f):
    """A page of SmartScout's niches (its subcategories) and the dots of the niche map, under the filters: cat (main
    category), q (words of the name), min (money a month at least)."""
    ids, as_of = storage.ss_set(conn, 'ss_subcats', market)
    out = {'view': 'niches', 'market': market, 'as_of': as_of, 'rows': [], 'total': 0, 'map': [], 'categories': []}
    out['sort'], out['dir'], order = _order('niches', f)
    if not ids:
        return out
    base, params = ['import_id IN (%s)' % storage._in(ids)], list(ids)
    out['categories'] = _cached(conn, market, 'niche_cats', lambda c: [
        {'category': cat, 'n': n} for cat, n in c.execute('SELECT category, COUNT(*) FROM ss_subcat WHERE %s GROUP BY category '
                                                          'ORDER BY SUM(revenue) DESC' % base[0], params) if cat])
    if f.get('cat'):
        base.append('category = ?')
        params.append(str(f['cat'])[:120])
    if _like(f.get('q')):
        base.append("(name LIKE ? ESCAPE '\\' OR top_brand LIKE ? ESCAPE '\\')")
        params += [_like(f['q'])] * 2
    if _num(f.get('min')):
        base.append('revenue >= ?')
        params.append(_num(f['min']))
    w = ' AND '.join(base)
    out['total'] = conn.execute('SELECT COUNT(*) FROM ss_subcat WHERE ' + w, params).fetchone()[0]
    off, lim = _page(f)
    cols = ', '.join(storage.SS_SUBCAT_COLS)
    out['rows'] = storage._rows(conn.execute('SELECT %s FROM ss_subcat WHERE %s ORDER BY %s LIMIT ? OFFSET ?' % (cols, w, order),
                                             params + [lim, off]))
    out['map'] = storage._rows(conn.execute(
        'SELECT name, category, revenue, brands, avg_sellers, amazon_share, top_brand, top_brand_share, growth_12m '
        'FROM ss_subcat WHERE %s AND revenue > 0 AND brands > 0 ORDER BY revenue DESC LIMIT %d' % (w, MAP_DOTS), params))
    for r in out['rows'] + out['map']:
        r['risk'] = mg.risk_of(r.get('name'), r.get('category'))
    out['offset'] = off
    return out


def brands(conn, market, f):
    """A page of SmartScout's brands under the filters: cat, q (brand or niche), abroad=1 (its main seller ships in from
    abroad), generic=1 (keep SmartScout's "Generic" and other unbranded rows)."""
    ids, as_of = storage.ss_set(conn, 'ss_brands', market)
    out = {'view': 'brands', 'market': market, 'as_of': as_of, 'rows': [], 'total': 0, 'top': [], 'categories': []}
    out['sort'], out['dir'], order = _order('brands', f)
    if not ids:
        return out
    base, params = ['import_id IN (%s)' % storage._in(ids)], list(ids)
    out['categories'] = _cached(conn, market, 'brand_cats', lambda c: [
        {'category': cat, 'n': n} for cat, n in c.execute('SELECT category, COUNT(*) FROM ss_brand WHERE %s GROUP BY category '
                                                          'ORDER BY SUM(revenue) DESC' % base[0], params) if cat])
    if str(f.get('generic')) != '1':
        base.append("LOWER(COALESCE(brand, '')) NOT IN (%s)" % storage._in(GENERIC))
        params += list(GENERIC)
    if f.get('cat'):
        base.append('category = ?')
        params.append(str(f['cat'])[:120])
    if _like(f.get('q')):
        base.append("(brand LIKE ? ESCAPE '\\' OR subcategory LIKE ? ESCAPE '\\')")
        params += [_like(f['q'])] * 2
    if str(f.get('abroad')) == '1':                  # the sellers shipping in from abroad, as named in this export
        names = _cached(conn, market, 'abroad_names', lambda c: sorted(
            {n for (n,) in c.execute('SELECT DISTINCT dominant_seller FROM ss_brand WHERE ' + base[0], list(ids))
             if n and mg.seller_role(n, market) in ('global_store', 'reseller')}))
        base.append('dominant_seller IN (%s)' % (storage._in(names) or "''"))
        params += names
    w = ' AND '.join(base)
    out['total'] = conn.execute('SELECT COUNT(*) FROM ss_brand WHERE ' + w, params).fetchone()[0]
    off, lim = _page(f)
    cols = ', '.join(storage.SS_BRAND_COLS)
    out['rows'] = storage._rows(conn.execute('SELECT %s FROM ss_brand WHERE %s ORDER BY %s LIMIT ? OFFSET ?' % (cols, w, order),
                                             params + [lim, off]))
    out['top'] = storage._rows(conn.execute('SELECT brand, subcategory, revenue, dominant_seller FROM ss_brand WHERE %s '
                                            'ORDER BY revenue DESC LIMIT 15' % w, params))
    for r in out['rows'] + out['top']:
        r['role'] = mg.seller_role(r.get('dominant_seller'), market) if r.get('dominant_seller') else 'unknown'
        r['store'] = offmod.store_country(r.get('dominant_seller'), market)
    out['offset'] = off
    return out


def sellers(conn, market, f):
    """A page of SmartScout's sellers: role (amazon, global_store, reseller, seller, unknown) and q (name or seller id)."""
    rows, as_of = _sellers(conn, market)
    out = {'view': 'sellers', 'market': market, 'as_of': as_of, 'rows': [], 'total': 0}
    out['sort'], out['dir'], _ = _order('sellers', f)
    if not rows:
        return out
    s = summary(conn, market).get('sellers') or {}
    out.update({k: s.get(k) for k in ('total', 'sellers', 'top10', 'half', 'shares')})
    out['money'] = out.pop('total')
    q = ' '.join(str(f.get('q') or '').lower().split())
    pick = [r for r in rows if (not f.get('role') or r['role'] == f['role'])
            and (not q or q in ('%s %s' % (r.get('name') or '', r.get('seller_id') or '')).lower())]
    col = SORTS['sellers'][out['sort']]
    known = [r for r in pick if r.get(col) is not None]            # an unknown value goes last either way
    known.sort(key=lambda r: str(r[col]).lower() if col == 'name' else r[col], reverse=out['dir'] == 'desc')
    pick = known + [r for r in pick if r.get(col) is None]
    off, lim = _page(f)
    total = sum(r['revenue'] or 0 for r in rows) or 1
    out['total'] = len(pick)
    out['rows'] = [{k: r.get(k) for k in ('seller_id', 'name', 'role', 'store', 'revenue', 'n_brands', 'n_asins', 'pct_fba',
                                          'avg_price', 'subcategory', 'growth_12m', 'url')} | {'share': (r['revenue'] or 0) / total}
                   for r in pick[off:off + lim]]
    out['offset'] = off
    return out


def products(conn, market, f):
    """A page of SmartScout's products (every product slice; the cross-border offers are on Shipped in), one row per
    ASIN from its newest import, under the filters: cat, q (title or brand), sellers (at most), units (at least a
    month), new=1 (launched in the last 180 days), slice (one export: "products-few-sellers"...)."""
    out = {'view': 'products', 'market': market, 'rows': [], 'total': 0, 'categories': [], 'slices': []}
    out['sort'], out['dir'], order = _order('products', f)
    imports = {i: (s, a) for i, s, a in conn.execute(     # the product slices: reading them by import is fast (no offers)
        "SELECT id, preset, as_of FROM import_file WHERE kind='ss_products' AND market=? "
        "AND COALESCE(preset, '') NOT LIKE 'seller-products-%'", (market,))}
    if not imports:
        return out
    ids = list(imports)
    base, params = ['p.import_id IN (%s)' % storage._in(ids)], list(ids)
    out['categories'] = _cached(conn, market, 'product_cats', lambda c: [
        {'category': cat, 'n': n} for cat, n in c.execute(
            'SELECT p.category, COUNT(DISTINCT p.asin) FROM ss_product p WHERE %s GROUP BY p.category '
            'ORDER BY SUM(p.revenue) DESC' % base[0], params) if cat])
    out['slices'] = _cached(conn, market, 'product_slices', lambda c: [
        {'slice': s, 'rows': n, 'as_of': a} for s, (n, a) in sorted(storage.ss_product_presets(c, market).items(),
                                                                    key=lambda x: str(x[0]))
        if s and not str(s).startswith('seller-products-')])
    if f.get('slice'):
        keep = [i for i in ids if imports[i][0] == f['slice']] or [-1]
        base[0], params = 'p.import_id IN (%s)' % storage._in(keep), list(keep)
    if f.get('cat'):
        base.append('p.category = ?')
        params.append(str(f['cat'])[:120])
    if _like(f.get('q')):
        base.append("(p.title LIKE ? ESCAPE '\\' OR p.brand LIKE ? ESCAPE '\\')")
        params += [_like(f['q'])] * 2
    if _num(f.get('sellers'), int) is not None:
        base.append('p.sellers <= ?')
        params.append(_num(f['sellers'], int))
    if _num(f.get('units')):
        base.append('p.units >= ?')
        params.append(_num(f['units']))
    today = dt.date.today().isoformat()
    if str(f.get('new')) == '1':                       # SmartScout has a few launch dates in the future (2117): not new
        base.append('p.launch >= ? AND p.launch <= ?')
        params += [(dt.date.today() - dt.timedelta(days=NEW_DAYS)).isoformat(), today]
    w = ' AND '.join(base)
    out['total'] = conn.execute('SELECT COUNT(DISTINCT p.asin) FROM ss_product p WHERE ' + w, params).fetchone()[0]
    if out['sort'] == 'launch':                          # an impossible date sorts with the unknown ones
        order = "(p.launch IS NULL OR p.launch > '%s'), p.launch %s" % (today, out['dir'].upper())
    off, lim = _page(f)
    rows = storage._rows(conn.execute(                    # one row per ASIN: its newest import's (SQLite's MAX rule)
        'SELECT p.asin, p.title, p.brand, p.category, p.subcategory, p.price, p.revenue, p.units, p.sellers, p.reviews, '
        'p.rating, p.launch, p.sub_rank, p.image, p.isr_90, p.growth_12m, MAX(p.import_id) AS newest '
        'FROM ss_product p WHERE %s GROUP BY p.asin ORDER BY %s LIMIT ? OFFSET ?' % (w, order), params + [lim, off]))
    twins = storage.ss_us_twins(conn, [r['asin'] for r in rows]) if rows else {}
    for r in rows:
        r['preset'], r['as_of'] = imports.get(r.pop('newest'), (None, None))
        r['risk'] = mg.risk_of(r.get('subcategory'), r.get('category'))
        r['us'] = mg.us_twin(twins.get(r['asin']))
    out['rows'], out['offset'] = rows, off
    out['as_of'] = max((r.get('as_of') or 0 for r in rows), default=None) or None
    return out


VIEWS = {'summary': summary, 'niches': niches, 'brands': brands, 'sellers': sellers, 'products': products}


def view(db, market, f):
    """GET /api/smartscout: one view of a market's SmartScout data (f: the query's filters, 'view' names it)."""
    fn = VIEWS.get(f.get('view')) or summary
    return db.read(fn, market, f)
