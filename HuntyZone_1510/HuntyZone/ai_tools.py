"""Read-only questions about your data, for an AI assistant: the MCP connector (mcp_server.py, for Claude Desktop) and
the optional Ask AI page (ai_chat.py) use the same tools.

Every tool only reads market.db. Nothing is fetched from Amazon, nothing is clicked, sent, bought or changed. Each
answer says where its numbers come from and when they were read; an estimate carries its range and basis; something
that was not read (blocked, paused or never searched) is null with a note, never 0."""
import contextlib
import datetime as dt
import json
import re
import sqlite3
import time

import amazon_check as ac
import config
import storage
from engine.board import STAGES as BOARD_STAGES

MAX_ROWS = 50
NAMES = {'USA': 'US', 'UNITED STATES': 'US', 'AUSTRALIA': 'AU', 'UAE': 'AE', 'EMIRATES': 'AE', 'UK': 'GB',
         'UNITED KINGDOM': 'GB', 'GERMANY': 'DE'}
ASIN_RX = re.compile(r'^[A-Z0-9]{10}$')


class ToolError(Exception):
    """Bad input or nothing to read; the message is meant for the AI and for you."""


class ReadOnlyDB(storage.DB):
    """market.db opened for reading only (mode=ro and query_only), so the app can keep running and writing."""

    def open(self):
        if not self.path.is_file():
            raise ToolError('No data yet: start Product Checker once (it makes market.db on the first start).')
        with contextlib.closing(self._connect()) as c:
            v = c.execute('PRAGMA user_version').fetchone()[0]
        if v > storage.SCHEMA_VERSION:
            raise ToolError('market.db was written by a newer Product Checker: update this copy.')
        if v < storage.SCHEMA_VERSION:
            raise ToolError('Start Product Checker once so it can update market.db, then ask again.')
        return self

    def _connect(self, readonly=True):
        conn = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10, isolation_level=None,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA busy_timeout=5000')
        conn.execute('PRAGMA query_only=1')
        return conn

    def write(self, fn, *args, **kw):
        raise PermissionError('The AI tools only read your data.')

    def close(self):
        while True:
            try:
                self._pool.get_nowait().close()
            except Exception:                                           # noqa: BLE001 - the pool is empty
                break


class Ctx:
    """What the tools need: the database, your settings and the time."""

    def __init__(self, db, settings, now=None):
        self.db, self.settings, self.now = db, settings, now


def load_settings():
    try:
        saved = json.loads((config.APP_DIR / 'settings.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        saved = {}
    return config.merge(saved if isinstance(saved, dict) else {})


# ---------- small helpers ----------
def _at(ts):
    return dt.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M') if ts else None


def _r(v, d=2):
    return round(v, d) if isinstance(v, float) else v


def _est(e):
    """An engine estimate {'v', 'lo', 'hi', 'basis', 'conf'} -> a plain one, or None."""
    if not isinstance(e, dict) or e.get('v') is None:
        return None
    return {'value': _r(e['v'], 1), 'low': _r(e.get('lo'), 1), 'high': _r(e.get('hi'), 1),
            'basis': e.get('basis') or e.get('src'), 'confidence': e.get('conf')}


def _market(v, optional=False, allowed=config.MARKETS):
    if v in (None, '') and optional:
        return None
    m = str(v or '').strip().upper()
    m = NAMES.get(m, m)
    if m not in allowed:
        raise ToolError('market must be one of %s.' % ', '.join(allowed))
    return m


def _limit(v, default=20, hi=MAX_ROWS):
    try:
        n = int(v)
    except (TypeError, ValueError):
        n = default
    return max(1, min(hi, n))


def _asin(v):
    a = str(v or '').strip().upper()
    if not ASIN_RX.match(a):
        raise ToolError('asin must be 10 letters and digits, like B0C1234567.')
    return a


def _text(v, what, lo=2, hi=120):
    s = ' '.join(str(v or '').split())
    if not lo <= len(s) <= hi:
        raise ToolError('%s: %d to %d characters.' % (what, lo, hi))
    return s


def _cur(mk):
    return ac.MARKETS[mk]['currency']


# ---------- the tools ----------
def list_keywords(ctx, market=None, limit=50):
    mk = _market(market, True)
    rows = ctx.db.read(storage.keywords_overview, 1000)
    out = [{'keyword': r['display'] or r['kw_norm'], 'markets': sorted(x for x in (r['markets'] or '').split(',') if x),
            'searches_saved': r['n'], 'last_searched': _at(r['last_ts'])}
           for r in rows if not mk or mk in (r['markets'] or '').split(',')]
    return {'keywords': out[:_limit(limit, 50, 200)], 'total': len(out),
            'source': 'Amazon searches this app saved (page 1 of each search)'}


def _page_row(p, mk, curve_fn):
    from engine import intel as en
    p = dict(p)
    units = en.units_of({'badge_low': p.get('badge_low'), 'root_rank': p.get('root_rank'), 'root_node': p.get('root_node')},
                        mk, curve_fn)
    return {'price': p.get('price'), 'currency': _cur(mk), 'rating': p.get('rating'), 'reviews': p.get('reviews'),
            'best_seller_rank': p.get('root_rank'), 'bought_past_month_badge': p.get('badge_low'),
            'sales_a_month': _est(units), 'sellers_on_listing': p.get('offers'), 'buy_box_seller': p.get('buybox_seller'),
            'ships_from': p.get('ships_from'), 'amazon_sells_it': None if p.get('sold_by_amazon') is None else bool(p['sold_by_amazon']),
            'delivery_days': p.get('delivery_days'), 'seller_based_in': p.get('origin'), 'read_at': _at(p.get('ts')),
            'page_status': p.get('status')}


def search_products(ctx, query, market=None, limit=20):
    q = _text(query, 'query')
    mk = _market(market, True, config.ALL_MARKETS)
    like = '%' + re.sub(r'([\\%_])', r'\\\1', q) + '%'

    def build(conn):
        import intel
        sql = ("SELECT market, asin, title, brand, root_name, last_seen FROM asin WHERE (title LIKE ? ESCAPE '\\' OR "
               "brand LIKE ? ESCAPE '\\' OR asin=?)" + (' AND market=?' if mk else '') + ' ORDER BY last_seen DESC LIMIT ?')
        args = [like, like, q.upper()] + ([mk] if mk else []) + [_limit(limit)]
        out, curves = [], {}
        for r in conn.execute(sql, args).fetchall():
            m = r['market']
            if m not in curves:
                curves[m] = intel._curve_fn(conn, m)
            p = conn.execute('SELECT * FROM product_snapshot WHERE market=? AND asin=? AND status IN (%s) ORDER BY ts DESC '
                             'LIMIT 1' % ','.join('?' * len(storage.VALID)), (m, r['asin'], *storage.VALID)).fetchone()
            item = {'asin': r['asin'], 'market': m, 'title': r['title'], 'brand': r['brand'], 'category': r['root_name'],
                    'last_seen_in_a_search': _at(r['last_seen'])}
            if p:
                item.update(_page_row(p, m, curves[m]), source='Amazon product page')
            else:
                item.update(source='seen in search results only: the product page was not read yet')
            out.append(item)
        return out
    rows = ctx.db.read(build)
    return {'query': q, 'products': rows, 'found': len(rows),
            'note': None if rows else 'Nothing read matches. The app only knows products it has seen on Amazon.'}


def get_product(ctx, asin, market):
    import reports
    a, mk = _asin(asin), _market(market, allowed=config.ALL_MARKETS)
    if mk not in config.MARKETS:
        return _proof_product(ctx, a, mk)
    rep = reports.product_report(ctx.db, mk, a, ctx.settings, ctx.now)
    info, now = rep.get('info') or {}, rep.get('now')
    if not info and not now:
        return {'asin': a, 'market': mk, 'found': False,
                'note': 'This ASIN was not read in %s yet. Open it in the app (a product report) to read it.' % mk}

    def extra(conn):
        import intel
        o = storage.latest_offers(conn, mk, a)
        g = conn.execute('SELECT market, ts, status, gap_type, data_json FROM gap_check WHERE asin=? ORDER BY ts DESC LIMIT 3',
                         (a,)).fetchall()
        return o, g, intel._curve_fn(conn, mk)
    offers, gaps, curve_fn = ctx.db.read(extra)
    out = {'asin': a, 'market': mk, 'found': True, 'url': rep['url'], 'title': info.get('title'), 'brand': info.get('brand'),
           'category': info.get('root_name'), 'variations': info.get('n_variations'),
           'package': {'cm': [info.get('pkg_l_cm'), info.get('pkg_w_cm'), info.get('pkg_h_cm')], 'kg': info.get('pkg_kg')}
           if info.get('pkg_kg') or info.get('pkg_l_cm') else None,
           'now': _page_row(now, mk, curve_fn) if now else None,
           'sales_basis': 'the "bought in past month" badge (Amazon\'s floor, as a range), else the best-seller rank through '
                          'this market\'s rank-to-sales curve',
           'monthly': [{'month': m['month'], 'units': _est(m['units']), 'revenue': _est(m.get('revenue')),
                        'partial_month': m.get('partial')} for m in rep.get('monthly') or []][-6:],
           'helium10_rows': [{k: h.get(k) for k in ('month', 'kind', 'sales_asin', 'revenue', 'bsr', 'price')}
                             for h in rep.get('h10') or []][-6:],
           'other_markets': {m: ({'price': o.get('price'), 'currency': _cur(m), 'best_seller_rank': o.get('root_rank'),
                                  'sellers_on_listing': o.get('offers'), 'seller_based_in': o.get('origin'),
                                  'read_at': _at(o.get('ts'))} if o else None)
                             for m, o in (rep.get('other_markets') or {}).items()},
           'source': 'Amazon product pages this app read (newest %s)' % (_at(now['ts']) if now else 'none')}
    if offers:
        out['sellers'] = {'read_at': _at(offers['ts']), 'source': 'Amazon SP-API offers' if offers['source'] == 'spapi'
                          else 'the listing\'s "other sellers" page', 'count': offers.get('n_offers'),
                          'offers': [{'seller': x.get('seller'), 'seller_id': x.get('seller_id'), 'amazon_itself': x.get('amazon'),
                                      'based_in': x.get('country'), 'fba': x.get('fba'), 'stock': x.get('stock'),
                                      'buy_box': x.get('buybox'), 'delivery_days': x.get('days')}
                                     for x in offers['offers'][:15]]}
    if gaps:
        out['gap_finder'] = [{'market': g['market'], 'checked_at': _at(g['ts']), 'status': g['status'], 'type': g['gap_type'],
                              'why': ((json.loads(g['data_json'] or '{}').get('gap') or {}).get('why'))} for g in gaps]
    return out


def _proof_product(ctx, a, mk):
    def build(conn):
        info = storage.asin_info(conn, mk, a) or {}
        p = conn.execute('SELECT * FROM product_snapshot WHERE market=? AND asin=? ORDER BY ts DESC LIMIT 1', (mk, a)).fetchone()
        return info, dict(p) if p else None
    info, p = ctx.db.read(build)
    if not info and not p:
        return {'asin': a, 'market': mk, 'found': False, 'note': 'Not read in %s (the Gap Finder reads its proof lists).' % mk}
    return {'asin': a, 'market': mk, 'found': True, 'title': info.get('title'), 'brand': info.get('brand'),
            'now': _page_row(p, mk, None) if p else None,
            'source': 'Amazon %s, read by the Gap Finder as proof that it sells' % mk}


def keyword_report(ctx, keyword, market):
    import reports
    kw, mk = _text(keyword, 'keyword'), _market(market)
    rep = reports.keyword_report(ctx.db, mk, kw, ctx.settings, ctx.now)
    now = rep.get('now')
    if not now and not rep.get('monthly'):
        return {'keyword': kw, 'market': mk, 'found': False,
                'note': 'Not searched in %s yet. Search it in the app (Check sellers) and ask again.' % mk}
    comp = rep.get('competition') or {}
    prices = sorted(comp.get('prices') or [])

    def q(f):
        return _r(prices[min(len(prices) - 1, int(f * len(prices)))]) if prices else None
    last = (rep.get('monthly') or [None])[-1]
    tr = rep.get('trends') or {}
    snaps = (rep.get('provenance') or {}).get('snapshots') or []
    gaps = [s for s in snaps if s['status'] not in storage.VALID][-3:]
    return {
        'keyword': rep['keyword'], 'market': mk, 'currency': rep['currency'], 'found': True,
        'in_one_sentence': reports.sentence(ctx.db, mk, kw, ctx.settings),
        'last_search': {'at': _at(now['checked_at']), 'delivery_to': now.get('location'), 'location_ok': now.get('location_ok'),
                        'source': 'Amazon search, page 1'} if now else None,
        'sellers': {'level': now.get('level'), 'verdict': now.get('verdict'), 'page1_listings': now.get('total'),
                    'sponsored': now.get('sponsored'), 'fast_delivery': now.get('fast'), 'local_fast': now.get('local'),
                    'overseas': now.get('overseas'), 'origin_unknown': now.get('origin_unknown')} if now else None,
        'sales_a_month_page1': {'month': last['month'], 'units': _est(last.get('units')), 'revenue': _est(last.get('revenue')),
                                'method': last.get('method'),
                                'basis': 'Helium 10 export' if last.get('method') == 'h10' else
                                'sum of the "bought in past month" badges on page 1 (a floor: listings without a badge add more)'}
        if last else None,
        'monthly': [{'month': m['month'], 'units': _est(m.get('units')), 'method': m.get('method')}
                    for m in rep.get('monthly') or []][-6:],
        'price': {'p25': q(0.25), 'median': q(0.5), 'p75': q(0.75), 'listings_priced': len(prices)} if prices else None,
        'reviews_to_beat': comp.get('review_barrier'), 'listings_under_100_reviews': comp.get('low_review_n'),
        'top3_share_of_badged_sales': comp.get('top3_share'),
        'top_listings': [{'asin': t.get('asin'), 'title': (t.get('title') or '')[:120], 'price': t.get('price'),
                          'rating': t.get('rating'), 'reviews': t.get('reviews'), 'sales_a_month': _est(t.get('units')),
                          'seller_based_in': t.get('origin'), 'delivery_days': t.get('days')}
                         for t in (comp.get('top') or [])[:10]],
        'google_trend': {'label': tr.get('label'), 'why': tr.get('why'), 'as_of': tr.get('as_of')} if tr.get('label') else None,
        'searches_not_read': [{'at': _at(s['t']), 'status': s['status'], 'error': s.get('error')} for s in gaps],
        'other_markets': {m: {'page1_listings': v.get('total'), 'level': v.get('level'), 'searched_at': _at(v.get('checked_at')),
                              'badge_sales_a_month': _est(v.get('units'))} if v and v.get('checked_at') else None
                          for m, v in (rep.get('markets') or {}).items() if m != mk},
    }


def find_gaps(ctx, market=None, gap_type=None, only_gaps=True, limit=20):
    import reports
    from engine import gaps as gp
    mk = _market(market, True, config.TARGET_MARKETS)
    kind = str(gap_type or '').strip().lower() or None
    if kind and kind not in gp.LABEL:
        raise ToolError('gap_type must be one of %s.' % ', '.join(gp.LABEL))
    t = reports.gaps_table(ctx.db, ctx.settings, ctx.now)
    rows = [r for r in t['rows'] if (not mk or r['market'] == mk) and (not kind or r['type'] == kind)
            and (not only_gaps or r['gap'] or kind)]
    out = []
    for r in rows[:_limit(limit)]:
        unread = r['status'] not in ('ok', 'partial')                   # nothing read there: no counts, never 0
        read = True if r['status'] == 'ok' else 'partly: the listing or the search was not read' if not unread else \
            'not read (%s): a gap in the data, not a result' % r['status']
        out.append({'asin': r['asin'], 'market': r['market'], 'title': r['title'], 'brand': r['brand'],
                    'type': r['type'], 'label': r['label'], 'means': r['means'], 'why': r['why'], 'confidence': r['conf'],
                    'is_gap': r['gap'], 'checked_at': _at(r['ts']), 'read': read,
                    'sellers_there': None if unread else r['sellers'], 'local_rivals': None if unread else r['local_rivals'],
                    'proven_in': {m: {'sells_well': v.get('well'), 'sales_a_month': _est(v.get('units')), 'why': v.get('why')}
                                  if isinstance(v, dict) else v for m, v in (r['proof'] or {}).items()},
                    'price_there': r['price'], 'currency': r['currency'], 'price_basis': r['price_basis'],
                    'highest_product_cost_for_20pct_margin': r['max_cogs_20'], 'flags': r['flags'],
                    'page1_badge_sales_floor': r['demand_floor'], 'search_phrase': r['phrase']})
    return {'gaps': out, 'matching': len(rows), 'proven_products': t['proven'],
            'types': {k: gp.MEANS.get(k) for k in gp.LABEL},
            'source': 'Gap Finder checks of the last %d days: proof from the Amazon US/UK/Germany Best Sellers lists, checks on '
                      'Amazon Australia/UAE product and search pages' % reports.GAP_SHOW_DAYS}


def lookup_seller(ctx, market, query, limit=10):
    import intel
    mk, q = _market(market), _text(query, 'query', 1, 80).lower()
    s = intel.sellers(ctx.db, mk, ctx.now)
    rows = [r for r in s['sellers'] if q in (r.get('name') or '').lower() or q == (r.get('seller_id') or '').lower()
            or (q == 'amazon' and r.get('amazon'))]
    return {'market': mk, 'query': query, 'sellers': [{
        'name': r.get('name'), 'seller_id': r.get('seller_id'), 'amazon_itself': r.get('amazon'), 'based_in': r.get('country'),
        'rating_pct': r.get('rating_pct'), 'ratings': r.get('ratings'), 'listings': r.get('listings'),
        'buy_boxes': r.get('buybox'), 'brands': [b['brand'] for b in r.get('brands') or []], 'ships': r.get('ships'),
        'sales_a_month': _est(r.get('units')),
        'share_of_sales_read': r.get('share'), 'first_seen': _at(r.get('first_seen')), 'last_seen': _at(r.get('last_seen'))}
        for r in rows[:_limit(limit)]], 'found': len(rows),
        'source': 'product pages read in %s in the last 90 days (%d pages); a listing\'s sales go to its buy-box seller'
                  % (mk, s['pages'])}


def lookup_brand(ctx, market, brand=None, keyword=None, limit=20):
    import intel
    mk = _market(market)
    b = _text(brand, 'brand', 1, 80).lower() if brand else None
    if keyword:
        kw = _text(keyword, 'keyword')
        r = intel.brands(ctx.db, mk, kw, ctx.now)
        if not r.get('searched'):
            return {'market': mk, 'keyword': kw, 'found': False, 'note': 'Not searched in %s yet.' % mk}
        rows = [x for x in r['brands'] if not b or b in x['brand'].lower()]
        return {'market': mk, 'keyword': r.get('display') or kw, 'searched_at': _at(r.get('checked_at')),
                'compared_with_search_of': _at(r.get('compared_with')),
                'brands': [{'brand': x['brand'], 'from': x['src'], 'listings': x['listings'], 'sales_a_month': _est(x['units']),
                            'share': x['share'], 'growth': x.get('growth'), 'new': x.get('new'), 'amazon_sells': x['amazon']}
                           for x in rows[:_limit(limit)]],
                'source': 'page 1 of the newest search; shares from "bought in past month" badges'}
    r = intel.brands(ctx.db, mk, None, ctx.now)
    rows = [x for x in r['brands'] if not b or b in x['brand'].lower()]
    return {'market': mk, 'niches_searched': r['niches'],
            'brands': [{'brand': x['brand'], 'from': x['src'], 'niches': x['n_niches'],
                        'top_niches': [{'keyword': n['display'], 'share': n['share']} for n in x['niches']],
                        'sales_a_month_sum': x['units'], 'listings': x['listings'], 'amazon_sells': x['amazon']}
                       for x in rows[:_limit(limit)]], 'found': len(rows),
            'source': 'page 1 of every keyword searched in %s in the last 120 days' % mk}


def board_status(ctx, stage=None):
    import board
    st = board.state(ctx.db, ctx.settings, ctx.now)
    stg = str(stage or '').strip().lower() or None
    cards = [c for c in st['cards'] if not stg or c['stage'] == stg]
    return {'products': [{
        'id': c['id'], 'title': c['title'], 'market': c['market'], 'asin': c['asin'], 'keyword': c['kw'],
        'stage': c['stage'], 'progress_pct': (c.get('ring') or {}).get('pct'), 'next': (c.get('countdown') or {}).get('text'),
        'late': c.get('late'), 'tags': c.get('tags'), 'invested_usd': c['invested_usd'],
        'stock': [{'market': m['market'], 'first_order_units': m['units'],
                   **({'pct_left': _r((m['stock'] or {}).get('pct'), 0), 'units_left': m['stock'].get('left'),
                       'level': m['stock'].get('level'), 're_order_now': m['stock'].get('order_now'),
                       're_order_by': m['stock'].get('reorder_by')} if m['stock'] else {})} for m in c['markets']],
        'deadline': {'due': c['task']['due_day'], 'state': c['task']['due']} if c.get('task') else None}
        for c in cards], 'totals': {'products': len(st['cards']), 'invested_usd': st['invested_usd'], 'low_on_stock': st['low'],
                                    'late': st['late'], 'trophies': st['trophies']},
        'source': 'your Product board in this app'}


def live_stock(ctx, query=None):
    import board
    q = ' '.join(str(query or '').split()).lower()
    st = board.state(ctx.db, ctx.settings, ctx.now)
    out = []
    for c in st['cards']:
        if q and q not in ('%s %s %s' % (c['title'], c['asin'] or '', c['kw'])).lower():
            continue
        d = board.card_detail(ctx.db, c['id'], ctx.settings, ctx.now)
        for s in d['stock']:
            if s.get('sent') is None and s.get('left') is None:
                continue
            sales = [u for _, u in s.get('sales') or []]
            out.append({'product': c['title'], 'asin': c['asin'], 'market': s['market'], 'order': s['n'], 'units_sent': s['sent'],
                        'units_left': s['left'], 'pct_left': _r(s.get('pct'), 0), 'counted': s.get('counted'),
                        'count_from': 'Amazon (SP-API)' if s.get('live_at') and s['counts'] and s['counts'][-1][3] == 'spapi'
                        else 'typed by you' if s['counts'] else None,
                        'sells_a_day': _r(s.get('rate'), 1), 'pace_from': s.get('rate_src'),
                        'sold_last_7_days': sum(sales[-7:]) if sales else None,
                        'sells_out_about': s.get('sellout'), 're_order_by': s.get('reorder_by'),
                        're_order_now': s.get('order_now'), 'shipping_lead_days': s.get('lead_days'),
                        'target_a_month': s.get('target'), 'level': s.get('level')})
    return {'stock': out, 'note': None if out else 'No shipments on the board yet (or none matching).',
            'source': 'your Product board: counts typed by you or read from Amazon (SP-API) by the app; this tool does not '
                      'ask Amazon itself'}


def recent_alerts(ctx, market=None, limit=20):
    mk = _market(market, True)
    rows = ctx.db.read(storage.list_alerts, 0, False, 400)
    rows = [r for r in rows if not mk or r['market'] == mk][:_limit(limit)]
    return {'alerts': [{'at': _at(r['ts']), 'rule': r['rule'], 'market': r['market'], 'title': r['title'], 'body': r['body']}
                       for r in rows], 'source': 'Home alerts in this app (not dismissed)'}


def go_no_go(ctx, keyword, market, product_cost_usd=None, first_order_units=None):
    import reports
    kw, mk = _text(keyword, 'keyword'), _market(market)
    cost = None
    if product_cost_usd not in (None, ''):
        try:
            cost = float(product_cost_usd)
        except (TypeError, ValueError):
            raise ToolError('product_cost_usd must be a number (US$ a unit).') from None
        if not 0 < cost <= 100000:
            raise ToolError('product_cost_usd must be above 0.')
    units = _limit(first_order_units, 300, 100000)
    r = reports.decision(ctx.db, mk, kw, ctx.settings, cost, units, now=ctx.now)
    d, p = r['decision'], r['plan'] or {}
    out = {'keyword': kw, 'market': mk, 'decision': d['label'], 'why': d['why'], 'confidence': d['confidence'],
           'rules_fingerprint': d['fingerprint'],
           'rules': [{'rule': x['name'], 'kind': 'deal-breaker' if x['kind'] == 'hard' else 'warning', 'state': x['state'],
                      'why': x['why'], 'what_to_do': x['fix']} for x in d['rules']],
           'source': 'the newest Amazon search saved, the sellers read, Google Trends and your go/no-go rules (Settings); '
                     'nothing was fetched'}
    if p.get('status') == 'ok':
        out['first_year'] = {'currency': p['currency'], 'on_sale_from': p['start'], 'first_order_units': p['units'],
                             'cases': {k: {f: c[f] for f in ('profit_year', 'money_back_week', 'most_money_needed', 'units_year',
                                                             'lost_year', 'stockout_weeks', 'reorders', 'stock_end')}
                                       for k, c in p['cases'].items()}, 'assumptions': p['assumptions']}
    elif p:
        out['first_year'] = {'status': p.get('status'), 'why': p.get('why')}
    return out


MK = {'type': 'string', 'enum': list(config.MARKETS), 'description': 'US, AU (Australia) or AE (UAE)'}
TOOLS = {
    'list_keywords': (list_keywords, 'The keywords (search phrases) the app has searched on Amazon, newest first, with the '
                      'markets and when. Use it to see what can be asked about.',
                      {'market': {**MK, 'description': 'only this market (optional)'},
                       'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200}}, []),
    'search_products': (search_products, 'Find products the app has read on Amazon by words of the title, a brand or an '
                        'ASIN: price, rating, reviews, best-seller rank, estimated sales a month (range), sellers, buy box.',
                        {'query': {'type': 'string'},
                         'market': {'type': 'string', 'enum': list(config.ALL_MARKETS), 'description': 'optional'},
                         'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_ROWS}}, ['query']),
    'get_product': (get_product, 'One listing in one market: what its page showed, estimated sales a month (range and '
                    'basis), the last months, the sellers on it, other markets and any Gap Finder result.',
                    {'asin': {'type': 'string'}, 'market': {'type': 'string', 'enum': list(config.ALL_MARKETS)}},
                    ['asin', 'market']),
    'keyword_report': (keyword_report, 'A keyword (niche) in one market: page-1 sales a month (range), price band, '
                       'reviews to beat, who sells and how fast they deliver, the top 10 listings, the Google trend.',
                       {'keyword': {'type': 'string'}, 'market': MK}, ['keyword', 'market']),
    'go_no_go': (go_no_go, 'Go, Maybe, Weak or No-go for a keyword in a market: each deal-breaker (price band, reviews '
                 'to beat, page-1 sales, margin, Amazon selling, restricted category, one season) and warning with why, '
                 'and the first year in a good, likely and poor case (profit, the week the money is back, the most money '
                 'tied up). Give product_cost_usd for the margin and the first year.',
                 {'keyword': {'type': 'string'}, 'market': MK,
                  'product_cost_usd': {'type': 'number', 'description': 'your supplier price a unit in US$ (optional)'},
                  'first_order_units': {'type': 'integer', 'minimum': 1, 'description': 'default 300'}},
                 ['keyword', 'market']),
    'find_gaps': (find_gaps, 'Global Gap Finder: products proven in the USA, UK or Germany and their state in Australia or '
                  'the UAE (open, lone exporter, imported, few or crowded sellers...).',
                  {'market': {'type': 'string', 'enum': list(config.TARGET_MARKETS), 'description': 'AU or AE (optional)'},
                   'gap_type': {'type': 'string', 'description': 'one Gap Finder type (optional)'},
                   'only_gaps': {'type': 'boolean', 'description': 'default true: only real gaps'},
                   'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_ROWS}}, []),
    'lookup_seller': (lookup_seller, 'Sellers in a market by name or seller id (or "amazon"): where they are based, '
                      'rating, listings, buy boxes, brands, FBA/FBM, estimated sales and share.',
                      {'market': MK, 'query': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_ROWS}},
                      ['market', 'query']),
    'lookup_brand': (lookup_brand, 'Brands in a market: in one niche (give keyword) their share of page-1 sales, growth '
                     'and whether Amazon sells there; or across every niche searched (optionally one brand).',
                     {'market': MK, 'brand': {'type': 'string'}, 'keyword': {'type': 'string'},
                      'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_ROWS}}, ['market']),
    'board_status': (board_status, 'Your Product board: each product\'s stage, progress, the next date that matters, '
                     'stock left per market and deadlines.',
                     {'stage': {'type': 'string', 'enum': list(BOARD_STAGES)}}, []),
    'live_stock': (live_stock, 'Units left, selling pace, sell-out and re-order days of the products on your board, from '
                   'your counts or Amazon\'s (SP-API) as the app last saved them.',
                   {'query': {'type': 'string', 'description': 'part of the product name, ASIN or keyword (optional)'}}, []),
    'recent_alerts': (recent_alerts, 'The newest Home alerts (sellers moving in, stock-outs, low stock, new sellers...).',
                      {'market': {**MK, 'description': 'optional'}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_ROWS}},
                      []),
}


def schemas():
    """[{'name', 'description', 'input_schema'}] for every tool."""
    return [{'name': n, 'description': d, 'input_schema': {'type': 'object', 'properties': props, 'required': req,
                                                           'additionalProperties': False}}
            for n, (_, d, props, req) in TOOLS.items()]


def call(ctx, name, args):
    """Run one tool. Raises ToolError for a bad name or input (the message is safe to show)."""
    if name not in TOOLS:
        raise ToolError('Unknown tool %s.' % name)
    fn, _, props, req = TOOLS[name]
    args = args if isinstance(args, dict) else {}
    missing = [k for k in req if args.get(k) in (None, '')]
    if missing:
        raise ToolError('Missing %s.' % ', '.join(missing))
    unknown = [k for k in args if k not in props]
    if unknown:
        raise ToolError('Unknown input %s.' % ', '.join(unknown))
    ctx.now = ctx.now or time.time()
    out = fn(ctx, **args)
    json.dumps(out, allow_nan=False)
    return out
