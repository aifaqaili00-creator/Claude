"""Report payloads for the window, built from market.db plus the analysis engine. Read-only.

Every estimated number carries its range and basis ({'v', 'lo', 'hi', 'basis', 'conf'}), and every series keeps
its gaps: a blocked or failed search is listed under `gaps`, never drawn as zero.
"""
import datetime as dt
import json
import math
import time

import amazon_check as ac
import config
import countries
import product
import storage

H10_ERROR = 0.25
ASSUMED_LANDED_SHARE = 0.25   # product + freight as a share of the price, until you enter a product cost          # Helium 10 estimates are typically 15-30% off: shown as a +-25% range
BACKCAST_MONTHS = 24
FORECAST_MONTHS = 6
MIN_TRENDS_QUALITY = 0.5
LISTING_MAX_AGE = 14 * 86400  # a listing's product page and reviews are read again after two weeks
SELLER_MAX_AGE = 30 * 86400   # a seller's profile page after a month
TOP_LISTINGS = 10             # the best page-1 listings whose sellers are read
H10_SHIPS = {'FBA': 'fba', 'FBM': 'fbm', 'AMZ': 'amazon', 'AMAZON': 'amazon'}


def est(v, lo=None, hi=None, basis='estimated', conf='medium', **extra):
    def r(x):
        return None if x is None or (isinstance(x, float) and (x != x or math.isinf(x))) else round(float(x), 2)
    return {'v': r(v), 'lo': r(lo), 'hi': r(hi), 'basis': basis, 'conf': conf, **extra}


def _words(s):
    return set(config.norm_kw(s).split())


def same_keyword(a, b):
    """'moving bags' matches 'moving bags heavy duty' (Xray's guessed keyword) but not 'storage bags'."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    return wa <= wb or wb <= wa or len(wa & wb) / len(wa | wb) >= 0.6


def _month_add(key, n):
    y, m = map(int, key.split('-'))
    m += n
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return '%04d-%02d' % (y, m)


# ---------- monthly units and revenue ----------
def h10_months(conn, market, kw):
    """{month: {...}} from Xray imports for this keyword: the sum over organic listings, as reported by Helium 10."""
    out = {}
    for imp in storage.imports_for(conn, market):
        if imp['kind'] != 'xray' or not same_keyword(imp['kw_norm'] or '', kw):
            continue
        rows = [r for r in storage.h10_rows(conn, imp['id']) if not r['sponsored']]
        units = sum((r['sales_asin'] if r['sales_asin'] is not None else r['sales_parent']) or 0 for r in rows)
        revenue = sum(r['revenue'] or ((r['price'] or 0) * ((r['sales_asin'] or r['sales_parent']) or 0)) for r in rows)
        if not units:
            continue
        prev = out.get(imp['month'])
        if prev and prev['as_of'] >= imp['as_of']:
            continue
        out[imp['month']] = {'units': est(units, units * (1 - H10_ERROR), units * (1 + H10_ERROR), 'reported', 'medium'),
                             'revenue': est(revenue, revenue * (1 - H10_ERROR), revenue * (1 + H10_ERROR), 'reported', 'medium'),
                             'method': 'h10', 'as_of': imp['as_of'], 'listings': len(rows), 'file': imp['file']}
    return out


def badge_months(snaps):
    """{month: {...}} from the newest valid search of each month: the sum of badge ranges on page 1."""
    out = {}
    for s in snaps:
        if s['status'] not in storage.VALID or s['bought_mid_sum'] is None:
            continue
        out[s['month']] = s                                       # snapshots come oldest first: the last one wins
    res = {}
    for month, s in out.items():
        mid, lo, hi = s['bought_mid_sum'] or 0, s['bought_low_sum'] or 0, s['bought_high_sum'] or 0
        rev = s['revenue_mid']
        k = (rev / mid) if rev and mid else None
        res[month] = {'units': est(mid, lo, hi, 'estimated', 'low' if s['badged'] and s['badged'] < 3 else 'medium'),
                      'revenue': est(rev, lo * k if k else None, hi * k if k else None, 'estimated', 'low') if rev else None,
                      'method': 'badge', 'as_of': s['ts'], 'badged': s['badged']}
    return res


def monthly(conn, market, kw, snaps):
    """One row per month: Helium 10 when there is an import that month, otherwise the badge estimate."""
    h10, badge = h10_months(conn, market, kw), badge_months(snaps)
    months = sorted(set(h10) | set(badge))
    rows = []
    for m in months:
        pick = h10.get(m) or badge.get(m)
        rows.append({'month': m, **pick, 'cross': {k: v['units']['v'] for k, v in (('h10', h10.get(m)), ('badge', badge.get(m))) if v}})
    return rows


def backcast(rows, trend_monthly, q):
    """Units for up to 24 months before tracking began: the latest month's units scaled by the Trends shape."""
    if not rows or not trend_monthly or (q or 0) < MIN_TRENDS_QUALITY:
        return []
    idx = dict(trend_monthly)
    anchor = rows[-1]
    a_month = anchor['month'] if anchor['month'] in idx else max((m for m in idx if m <= anchor['month']), default=None)
    if not a_month or not idx.get(a_month):
        return []
    a_units = anchor['units']['v'] or 0
    rev = anchor.get('revenue') or {}
    price = rev['v'] / a_units if rev.get('v') and a_units else None          # today's price: past prices were not seen
    width0 = math.log(max(anchor['units']['hi'] or a_units, 1) / max(anchor['units']['lo'] or a_units, 1)) / 2 / 1.2816
    first = rows[0]['month']
    out = []
    for n in range(1, BACKCAST_MONTHS + 1):
        m = _month_add(first, -n)
        if m not in idx:
            continue
        v = a_units * idx[m] / idx[a_month]
        sd = math.sqrt(width0 ** 2 + (1 - q) ** 2 + (0.02 * n) ** 2 + 0.04)
        lo, hi = v * math.exp(-1.2816 * sd), v * math.exp(1.2816 * sd)
        out.append({'month': m, 'units': est(v, lo, hi, 'backcast', 'low'), 'method': 'backcast',
                    'revenue': est(v * price, lo * price, hi * price, 'backcast', 'low') if price else None})
    return sorted(out, key=lambda r: r['month'])


def forecast(rows, season, yoy):
    """The next 6 months: seasonal pattern x damped yearly growth, with a band that widens."""
    if not rows or not season or not season.get('reliable'):
        return []
    si = season['si']
    last = rows[-1]
    base_m = int(last['month'][5:]) - 1
    level = (last['units']['v'] or 0) / max(si[base_m], 0.05)
    g = max(-0.6, min(1.5, yoy or 0.0))
    out = []
    for h in range(1, FORECAST_MONTHS + 1):
        m = _month_add(last['month'], h)
        damp = sum(0.85 ** j for j in range(1, h + 1)) / 12
        v = level * si[int(m[5:]) - 1] * math.exp(math.log1p(g) * damp)
        sd = math.sqrt(0.25 ** 2 + h * 0.08 ** 2)
        out.append({'month': m, 'units': est(v, v * math.exp(-1.2816 * sd), v * math.exp(1.2816 * sd), 'forecast', 'low'),
                    'method': 'forecast'})
    return out


# ---------- supply over time ----------
def supply(conn, snaps):
    """Counts per valid search (local, fast, overseas, selling, new entrants) plus the gaps."""
    valid = [s for s in snaps if s['status'] in storage.VALID]
    ids = [s['id'] for s in valid]
    asins = {}
    if ids:
        for r in conn.execute('SELECT snapshot_id, asin FROM serp_item WHERE sponsored=0 AND snapshot_id IN (%s)'
                              % ','.join('?' * len(ids)), ids):
            asins.setdefault(r[0], set()).add(r[1])
    seen, points = set(), []
    for i, s in enumerate(valid):
        now = asins.get(s['id'], set())
        points.append({'t': s['ts'], 'local': s['local'], 'fast': s['fast'], 'overseas': s['overseas'],
                       'unknown': s['origin_unknown'], 'organic': s['organic'], 'selling': s['badged'],
                       'results_total': s['results_total'], 'new': len(now - seen) if i else None,
                       'price_med': s['price_med'], 'reviews_med_top10': s['reviews_med_top10']})
        seen |= now
    gaps = [{'t': s['ts'], 'status': s['status'], 'error': s['error']} for s in snaps if s['status'] not in storage.VALID]
    return points, gaps


# ---------- the latest search, recounted with today's settings ----------
def latest(conn, market, kw, settings):
    s = storage.latest_serp(conn, market, kw)
    if not s:
        return None
    out = ac.summarize(market, s['keyword'], s['location'], s['rows'], settings['fast_days'], settings['local_days'],
                       s['checked_at'])
    out['snapshot_id'] = s['snapshot_id']
    return out


def competition(summary):
    from engine import badge
    if not summary:
        return None
    organic = [r for r in summary['rows'] if not r['sponsored']]
    top = []
    for r in organic[:20]:
        u = badge.badge_estimate(r['bought'], summary['market']) if r.get('bought') else None
        top.append({k: r.get(k) for k in ('asin', 'title', 'price', 'reviews', 'rating', 'bought', 'origin', 'speed', 'days',
                                          'url', 'image', 'intl', 'why')} | {'units': u})
    badged = sorted((t['units']['v'] for t in top if t['units']), reverse=True)
    top3 = sum(badged[:3]) / sum(badged) if len(badged) >= 3 and sum(badged) else None
    return {'prices': [r['price'] for r in organic if r.get('price')],
            'reviews': [r['reviews'] for r in organic if r.get('reviews') is not None],
            'top': top, 'top3_share': round(top3, 3) if top3 is not None else None,
            'review_barrier': _median([r.get('reviews') or 0 for r in organic[:10]]),
            'low_review_n': sum(1 for r in organic if (r.get('reviews') or 0) < storage.LOW_REVIEWS)}


def _median(v):
    v = sorted(x for x in v if x is not None)
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def market_glance(conn, kw, settings):
    """Every market side by side: sellers now, demand now, trend label (the gap matrix)."""
    out = {}
    for code in config.MARKETS:
        s = latest(conn, code, kw, settings)
        snaps = storage.serp_history(conn, code, kw)
        b = badge_months(snaps)
        last_month = b[max(b)] if b else None
        tf = storage.trends_feature(conn, kw, code)
        out[code] = {
            'checked_at': s['checked_at'] if s else None, 'total': s['total'] if s else None,
            'local': s['local'] if s else None, 'fast': s['fast'] if s else None, 'overseas': s['overseas'] if s else None,
            'level': s['level'] if s else None, 'units': last_month['units'] if last_month else None,
            'price_med': s.get('price_med') if s else None,
            'trend': {'label': tf.get('label'), 'score': tf.get('score'), 'status': tf.get('status')} if tf else None,
            'n_snapshots': len(snaps),
        }
    return out


def keyword_report(db, market, kw, settings, now=None):
    now = now or time.time()
    kw_norm = config.norm_kw(kw)

    def build(conn):
        snaps = storage.serp_history(conn, market, kw_norm)
        disp = conn.execute('SELECT display FROM keyword WHERE kw_norm=?', (kw_norm,)).fetchone()
        rows = monthly(conn, market, kw_norm, snaps)
        tf = storage.trends_feature(conn, kw_norm, market)
        samples = storage.trends_samples(conn, kw_norm, market)
        tmonthly = (tf or {}).get('monthly') or []
        feats = (tf or {}).get('features') or {}
        bc = backcast(rows, tmonthly, (tf or {}).get('q'))
        fc = forecast(rows, (tf or {}).get('seasonality'), feats.get('YoY'))
        points, gaps = supply(conn, snaps)
        now_s = latest(conn, market, kw_norm, settings)
        demand_pts = [{'t': s['ts'], 'v': s['bought_mid_sum'], 'lo': s['bought_low_sum'], 'hi': s['bought_high_sum'],
                       'badged': s['badged']} for s in snaps if s['status'] in storage.VALID and s['bought_mid_sum'] is not None]
        h10 = h10_months(conn, market, kw_norm)
        vols = storage.kw_volume(conn, market, kw_norm)
        imports = [i for i in storage.imports_for(conn, market) if i['kind'] in ('magnet', 'cerebro') or same_keyword(i['kw_norm'] or '', kw_norm)]
        return {
            'keyword': disp[0] if disp else kw, 'kw_norm': kw_norm, 'market': market,
            'currency': ac.MARKETS[market]['currency'], 'site': ac.MARKETS[market]['site'],
            'search_url': ac.search_url(market, kw), 'trends_url': 'https://trends.google.com/trends/explore?date=today%%205-y&geo=%s&q=%s' % (market, kw_norm.replace(' ', '%20')),
            'now': {k: now_s.get(k) for k in ('checked_at', 'total', 'sponsored', 'fast', 'local', 'local_other', 'overseas',
                                              'overseas_intl', 'origin_unknown', 'level', 'verdict', 'location', 'location_ok',
                                              'fast_days', 'local_days', 'per_seller')} if now_s else None,
            'monthly': rows, 'backcast': bc, 'forecast': fc,
            'demand': {'amazon': demand_pts, 'h10': [{'month': m, **v} for m, v in sorted(h10.items())],
                       'volume': vols},
            'trends': {'label': tf.get('label'), 'why': tf.get('why') or [], 'score': tf.get('score'), 'q': tf.get('q'),
                       'status': tf.get('status'), 'monthly': tmonthly, 'seasonality': tf.get('seasonality'),
                       'features': feats, 'as_of': tf.get('as_of'),
                       'samples': [{'t': s['fetched_at'], 'status': s['status']} for s in samples]} if tf else
                      {'status': 'none', 'samples': [{'t': s['fetched_at'], 'status': s['status']} for s in samples]},
            'supply': {'points': points, 'gaps': gaps},
            'competition': competition(now_s),
            'markets': market_glance(conn, kw_norm, settings),
            'provenance': {'snapshots': [{'t': s['ts'], 'status': s['status'], 'source': s['source'], 'location': s['location'],
                                          'organic': s['organic'], 'error': s['error']} for s in snaps][-60:],
                           'imports': [{k: i[k] for k in ('kind', 'file', 'as_of', 'month', 'n_rows')} for i in imports][-20:]},
            'generated_at': now,
        }
    return db.read(build)


def library(db):
    """Keywords, imports and watched items for the Library view."""
    def build(conn):
        kws = storage.keywords_overview(conn)
        feats = {}
        for r in conn.execute('SELECT term, geo, label, score FROM trends_feature'):
            feats.setdefault(r[0], {})[r[1]] = {'label': r[2], 'score': r[3]}
        for k in kws:
            k['markets'] = sorted((k['markets'] or '').split(','))
            k['trends'] = feats.get(k['kw_norm'], {})
        imports = [dict(r) for r in conn.execute('SELECT kind, market, kw_norm, file, as_of, month, n_rows, imported_at '
                                                 'FROM import_file ORDER BY imported_at DESC LIMIT 100')]
        return {'keywords': kws, 'imports': imports, 'watch': storage.watches(conn), 'counts': storage.counts(conn)}
    return db.read(build)


def opportunity_map(db, market, now=None, days=90):
    """Every keyword checked in the last `days` in one marketplace: buyers a month (badge estimate) against local
    sellers on page 1, from its latest good search. Blocked and wrong-location searches are left out."""
    now = now or time.time()

    def build(conn):
        q = ('SELECT s.kw_norm, k.display, s.ts, s.organic, s.local, s.overseas, s.fast, s.badged, s.bought_low_sum, '
             's.bought_mid_sum, s.bought_high_sum, s.price_med FROM serp_snapshot s JOIN keyword k ON k.kw_norm=s.kw_norm '
             'WHERE s.market=? AND s.ts>=? AND s.status IN (%s) AND COALESCE(s.location_ok, 1)=1 '
             'AND s.ts=(SELECT MAX(t.ts) FROM serp_snapshot t WHERE t.market=s.market AND t.kw_norm=s.kw_norm '
             'AND t.status IN (%s) AND COALESCE(t.location_ok, 1)=1)' % ((','.join('?' * len(storage.VALID)),) * 2))
        rows = storage._rows(conn.execute(q, (market, now - days * 86400) + storage.VALID + storage.VALID))
        points, no_badge = [], []
        for r in rows:
            base = {'kw': r['kw_norm'], 'display': r['display'] or r['kw_norm'], 'ts': r['ts'], 'local': r['local'] or 0,
                    'overseas': r['overseas'] or 0, 'sellers': r['organic'] or 0, 'fast': r['fast'] or 0,
                    'price_med': r['price_med']}
            if not r['bought_mid_sum']:
                no_badge.append(base)
                continue
            d = r['bought_mid_sum']
            points.append({**base, 'demand': round(d, 1), 'lo': r['bought_low_sum'], 'hi': r['bought_high_sum'],
                           'per_local_seller': round(d / base['local'], 1) if base['local'] else None,
                           'per_seller': round(d / base['sellers'], 1) if base['sellers'] else None})
        points.sort(key=lambda p: -(p['demand'] / (p['local'] + 1)))
        return {'market': market, 'points': points, 'no_badge': no_badge, 'days': days,
                'local_line': 4.5, 'demand_line': _median([p['demand'] for p in points])}
    return db.read(build)


# ---------- Phase 2 screens ----------
RADAR_DAYS = 14
PRODUCT_PAGE_DAYS = 30        # an older product page reading says nothing about today's sales
H10_FRESH_DAYS = 90           # older Helium 10 sales count with low confidence
GAP_DAYS = 60                 # a search on another market older than this says nothing about the gap there
TARGETS = {'US': ('AU', 'AE'), 'AU': ('AE',), 'AE': ('AU',)}   # where a product found on one market could sell next
QUALIFY_FRESH = {'page': 7, 'phrase': 30, 'check': 14, 'arrival': 7}   # days before qualifying reads these again
GROUP_TOP = 5                 # a group's surge counts its five strongest listings, so a big generic group does not inflate
TASTE_MIN = 0.4               # title words this alike to a product you starred count as 'like your star'
MARKET_WORDS = {'US': 'US', 'AU': 'Australia', 'AE': 'the UAE'}
RISING_DAYS = 30             # Google's related searches of the last month feed 'Rising on Google'
RISING_MIN = 200              # a rising related search counts from +200% (Breakout always counts)


def radar_filters(settings, market):
    """Your price range (in the marketplace's money) and the phone / laptop filter, from settings."""
    import fxrates
    return {'price_range': fxrates.ranges(settings)[market] if settings.get('price_filter', True) else None,
            'exclude_tech': bool(settings.get('exclude_tech', True))}


def radar(db, market, now=None, limit=60, price_range=None, exclude_tech=False, settings=None):
    """Amazon surges for one market (Movers & Shakers / New Releases history), ranked by the discovery opportunity
    (engine.discovery), one row per group of similar products (the others under 'members'). `limit` counts rows;
    rows with a market gap score are kept beyond it.

    price_range (lo, hi) in local money keeps products selling within it (no price shown: kept, flagged);
    exclude_tech leaves out phones, laptops and their accessories. 'filtered' says how many each removed."""
    import file_rank as fr
    from engine import surge
    now = now or time.time()

    def build(conn):
        scans = storage.list_scans(conn, market, now - RADAR_DAYS * 86400)
        if not scans:
            return {'market': market, 'scans': 0, 'items': [], 'clusters': [], 'last_scan': None,
                    'coverage': _coverage([]), 'presence_days': [], 'targets': list(TARGETS[market]),
                    'hidden': storage.hidden_products(conn, market)}
        feats = surge.all_features(scans, days=7)
        info = {}
        for sc in scans:                                           # newest wins
            for it in sc['items']:
                info[it['asin']] = {**it, 'category': sc['category'], 'kind': sc['kind'], 'slug': sc['slug']}
        rows, filtered = [], {'out_of_range': 0, 'tech': 0}
        for a, f in feats.items():
            if not f.get('appearances') or a not in info:
                continue
            it = info[a]
            if exclude_tech and fr.is_tech(it.get('title')):
                filtered['tech'] += 1
                continue
            price = it.get('price')
            if price_range and price is not None and not price_range[0] <= price <= price_range[1]:
                filtered['out_of_range'] += 1
                continue
            label = ('Deal-driven' if f.get('deal_driven') else
                     'New on the lists' if (f.get('recent_hits') or 0) >= 2 and not f.get('seen_before_window') else
                     'Rising' if (f.get('streak_days') or 0) >= 3 or f.get('rank_improving') else 'Moving')
            flags = [name for name, rx in fr.CHECKS if rx.search(it.get('title') or '')]
            if price_range and price is None:
                flags.append('price unknown')
            rows.append({'asin': a, 'title': it.get('title'), 'image': it.get('image'), 'category': it['category'],
                         'slug': it['slug'], 'price': it.get('price'), 'reviews': it.get('reviews'), 'rating': it.get('rating'),
                         'pct': it.get('pct'), 'rank_now': it.get('rank_now'), 'surge': f.get('surge'), 'label': label,
                         'persistence': f.get('persistence'), 'streak_days': f.get('streak_days'), 'flags': flags,
                         'deal': bool(f.get('deal_driven')), 'search': fr.search_words(it.get('title') or ''),
                         'url': '%s/dp/%s' % (ac.MARKETS[market]['site'], a)})
        groups = surge.clusters([{'asin': r['asin'], 'title': r['title']} for r in rows if not r['deal']])
        found = discover(conn, market, rows, scans, settings, now, feats, groups)
        rows = found['rows']
        rows.sort(key=lambda r: (-(r['opportunity']['score'] or 0), -r['opportunity']['known'], -(r['surge'] or 0)))
        best_gap = lambda r: max((g['score'] or 0) for g in r['gap'].values() if g)          # noqa: E731
        top = rows[:limit] + sorted((r for r in rows[limit:] if any(r['gap'].values())), key=lambda r: -best_gap(r))[:limit]
        trails = surge.rank_histories(scans, [r['asin'] for r in top])
        for r in top:                                              # the rank trail only for what is shown
            r['spark'] = [x for _, x in trails[r['asin']]][-14:]
        clusters = []
        for c in groups:
            if len(c['asins']) >= 2:
                s = surge.cluster_surge([feats[a].get('surge') for a in c['asins']])
                clusters.append({'key': c['key'], 'asins': c['asins'], 'surge': s, 'size': len(c['asins'])})
        clusters.sort(key=lambda c: -(c['surge'] or 0))
        valid = [s for s in scans if s['status'] in ('ok', 'partial')]
        return {'market': market, 'scans': len(scans), 'valid_scans': len(valid), 'items': top,
                'filtered': filtered, 'price_range': price_range, 'coverage': _coverage(top),
                'presence_days': found['days'], 'targets': list(TARGETS[market]), 'hidden': found['hidden'],
                'clusters': clusters[:20], 'last_scan': scans[-1]['ts'], 'first_scan': scans[0]['ts']}
    return db.read(build)


def ideas_discovery(db, market, items, settings=None, now=None, phrases=None):
    """Score product ideas like the radar, from the stored list history (a scan you just ran is in it), one row per
    group of similar ideas, in their order. phrases {asin: search words you typed} replace an idea's own.
    Returns {'ideas', 'coverage', 'presence_days', 'targets'}."""
    from engine import surge
    now = now or time.time()

    def build(conn):
        groups = surge.clusters([{'asin': i['asin'], 'title': i.get('title') or ''} for i in items if not i.get('deal')])
        return discover(conn, market, items, storage.list_scans(conn, market, now - RADAR_DAYS * 86400), settings, now,
                        groups=groups, phrases=phrases)
    found = db.read(build)
    return {'ideas': found['rows'], 'coverage': _coverage(found['rows']), 'presence_days': found['days'],
            'targets': list(TARGETS[market]), 'hidden': found['hidden']}


def discover(conn, market, rows, scans, settings=None, now=None, feats=None, groups=None, phrases=None):
    """Score candidates (radar rows or product ideas) with engine.discovery and fold each group of similar listings
    (surge.clusters `groups`) into its best row. Returns {'rows', 'days'}: the kept rows (in their order) and the
    market days the presence marks stand for.

    Each kept row gets 'opportunity'; 'search', the phrase shoppers type when Amazon's suggestions know one ('phrase_src'
    'amazon'; 'you' when `phrases` {asin: words} set it; 'title' keeps the title's words, also kept in 'title_search')
    and 'phrase_at' (when the suggestions were read, None while one is missing); 'trend' (the stored Google Trends term
    it matched, or None) and 'trend_phrase' (what to look up); 'presence' (see _presence, the whole group's);
    'members' (the other listings) and 'similar' (how many); 'page_at' (its product page of the last 30 days),
    'buy' {max_usd: the highest supplier price leaving settings' target_margin_pct, margin_pct, sized, cost_usd and
    margin: your supplier price (product_pref) and the margin it leaves}, 'pref' (see _taste) and
    'gap' {market it could sell in next: the arbitrage score, None until its phrase was searched there} and 'wave'
    {that market: when its rise in searches gets there, see _waves}. Products you
    marked Not for me are left out; 'hidden' lists them."""
    from engine import discovery, surge
    now = now or time.time()
    settings = settings or config.defaults()
    phrases = phrases or {}
    pref = storage.prefs(conn, market)
    hidden = {a for a, p in pref.items() if p.get('hidden_at')}
    rows = [r for r in rows if r['asin'] not in hidden]
    liked = _liked(conn, market, pref)
    margin = (settings.get('target_margin_pct') or 20) / 100.0
    feats = feats if feats is not None else surge.all_features(scans, days=7)
    feats14 = surge.all_features(scans, days=RADAR_DAYS)
    days, marks = _presence(scans, market, now)
    pages, h10, size = _evidence(conn, market, [r['asin'] for r in rows], now)
    cv = storage.get_curve(conn, market)
    terms = {t: {**json.loads(data or '{}'), 'computed_at': at}
             for t, data, at in conn.execute('SELECT term, data_json, computed_at FROM trends_feature WHERE geo=?', (market,))}
    index = discovery.trend_index(terms)
    registered = bool((settings.get('registered') or {}).get(market))
    prefixes = {r['asin']: discovery.phrase_prefixes(r.get('search') or '') for r in rows}
    sugg = storage.suggestions(conn, market, sorted({p for ps in prefixes.values() for p in ps}))
    for r in rows:
        ps = prefixes[r['asin']]
        own = phrases.get(r['asin']) or (pref.get(r['asin']) or {}).get('phrase')
        picked = discovery.pick_phrase(r.get('title') or '', [k for p in ps for k in sugg.get(p, {}).get('keywords', [])])
        read = [sugg[p]['ts'] for p in ps if p in sugg]
        r['title_search'] = r.get('search') or ''
        r['phrase_at'] = min(read) if ps and len(read) == len(ps) else None
        r['search'], r['phrase_src'] = ((own, 'you') if own else (picked, 'amazon') if picked else
                                        (r['title_search'], 'title'))
    cluster_of = {}
    for n, c in enumerate(groups or []):
        mine = [a for a in c['asins'] if a in prefixes]
        if len(mine) >= 2:
            cluster_of.update((a, n) for a in mine)
    grouped = {}
    for r in rows:
        grouped.setdefault(cluster_of.get(r['asin'], r['asin']), []).append(r)
    out = []
    for g in grouped.values():
        asins = [r['asin'] for r in g] + [m['asin'] for r in g for m in r.get('members') or []]
        gm = _group_marks([marks(a) for a in asins])
        top = sorted(((feats.get(a) or {}).get('surge') or 0 for a in asins), reverse=True)[:GROUP_TOP]
        group = {'similar': len(asins) - 1, 'cluster_surge': surge.cluster_surge(top) if len(asins) >= 3 else None,
                 'group_on': sum(1 for m in gm if m in (1, 2)), 'group_read': sum(1 for m in gm if m is not None)}
        for r in g:
            a = r['asin']
            f7, f14 = feats.get(a) or {}, feats14.get(a) or {}
            page, h, sz = pages.get(a) or {}, h10.get(a) or {}, size.get(a) or {}
            listed = bool(f14.get('appearances'))
            dims = [sz.get('pkg_l_cm'), sz.get('pkg_w_cm'), sz.get('pkg_h_cm')]
            shopper = r['search'] if r['phrase_src'] != 'title' else None
            trend = discovery.match_trend(r['title_search'], index, shopper)
            c = {'market': market, 'trend': trend, 'units': _units(market, r, page, h, cv, now),
                 'reviews': next((x for x in (r.get('reviews'), page.get('reviews'), h.get('reviews')) if x is not None), None),
                 'price': r.get('price') if r.get('price') is not None else page.get('price'), 'category': r.get('category'),
                 'fba_fee': h.get('fba_fees'), 'dims_cm': dims if all(dims) else None,
                 'kg': sz.get('pkg_kg') or h.get('weight_kg'), 'registered': registered, 'flags': r.get('flags'),
                 'cogs_usd': (pref.get(a) or {}).get('cogs_usd'),
                 'deal_driven': f7.get('deal_driven') if listed else bool(r.get('deal')), **group}
            if listed:
                c.update(surge=f7.get('surge'), surge_14d=f14.get('surge'), persistence=f7.get('persistence'),
                         persistence_14d=f14.get('persistence'), valid_scans=f14.get('valid_scans'),
                         appearances=f14.get('appearances'), streak_days=f7.get('streak_days'),
                         rank_improving=f7.get('rank_improving'))
            else:                                                  # no list history yet: this one reading only
                c.update(pct=r.get('pct'), new_rank=r.get('new_rank'))
            r['opportunity'] = discovery.score(c)
            r['trend'] = _trend_summary(trend)
            r['trend_phrase'] = trend['term'] if trend else shopper or discovery.suggest_term(r['title_search'])
            r['page_at'] = page.get('ts')
            e = next(p for p in r['opportunity']['parts'] if p['key'] == 'E')['raw']
            r['buy'] = {'max_usd': discovery.max_cost(c, market, margin), 'margin_pct': round(100 * margin),
                        'sized': bool(e.get('sized')), 'cost_usd': c['cogs_usd'], 'margin': e.get('margin')}
        best = max(g, key=lambda r: r['opportunity']['score'] or 0)          # the first of equals: keeps their order
        best['members'] = [m for m in [_member(r) for r in g if r is not best] + [m for r in g for m in r.get('members') or []]
                           if m['asin'] not in hidden]
        best['similar'] = len(best['members'])
        best['presence'] = gm
        best['pref'] = _taste(best, pref, liked)
        best['boom'] = _boom(best)
        out.append(best)
    _gaps(conn, market, out, settings, now)
    _waves(conn, market, out, terms, now)
    return {'rows': out, 'days': days, 'hidden': storage.hidden_products(conn, market)}


def _waves(conn, market, rows, terms, now):
    """Each row's 'wave': for every market it could sell in next, when its rise in searches gets there
    (engine.wave.forecast), from its Google Trends term here (terms: {term: feature}) and there, and from when Amazon's
    suggestions there first knew its phrase. None when there is nothing to say."""
    from engine import wave
    today = config.market_day(now, market)
    kws = {r['asin']: config.norm_kw(r.get('search') or '') for r in rows}
    want = sorted({config.norm_kw(r.get('trend_phrase') or '') for r in rows} - {''})
    for t in TARGETS[market]:
        there = {}
        for i in range(0, len(want), 400):
            part = want[i:i + 400]
            there.update((term, json.loads(data or '{}')) for term, data in conn.execute(
                'SELECT term, data_json FROM trends_feature WHERE geo=? AND term IN (%s)' % ','.join('?' * len(part)), (t, *part)))
        hist = storage.suggest_history(conn, t, kws.values())
        typical = _typical_lag(conn, market, t)
        for r in rows:
            term = config.norm_kw(r.get('trend_phrase') or '')
            f = wave.forecast((terms.get(term) or {}).get('monthly'), (there.get(term) or {}).get('monthly'),
                              _arrival(kws[r['asin']], hist.get(kws[r['asin']])), typical, today,
                              (MARKET_WORDS[market], MARKET_WORDS[t]))
            r.setdefault('wave', {})[t] = f if f['status'] != 'unknown' else None


def _typical_lag(conn, source, target):
    """The usual months between a rise in source's and in target's searches, from every term stored in both."""
    from engine import wave
    lags = [wave.lag_months(json.loads(a or '{}').get('monthly'), json.loads(b or '{}').get('monthly'))
            for a, b in conn.execute('SELECT a.data_json, b.data_json FROM trends_feature a JOIN trends_feature b '
                                     'ON a.term=b.term WHERE a.geo=? AND b.geo=?', (source, target))]
    return wave.typical_lag([x for x in lags if x is not None])


def _arrival(kw, snaps):
    """{'first_known': the day Amazon's suggestions first knew the phrase, 'known_now'} from their history."""
    from engine import discovery
    first, known = None, False
    for ts, words in snaps or ():
        known = discovery.autocomplete_knows(kw, words)
        if known and first is None:
            first = ts
    return {'first_known': time.strftime('%Y-%m-%d', time.gmtime(first)) if first else None, 'known_now': known}


def _liked(conn, market, pref):
    """The products you starred in a market: [(asin, title, title words)]."""
    import file_rank as fr
    out = []
    for a, p in pref.items():
        title = (storage.asin_info(conn, market, a) or {}).get('title') if p.get('liked_at') else None
        if title:
            out.append((a, title, set(fr.search_words(title).lower().split())))
    return out


def _taste(r, pref, liked):
    """{'liked': you starred it (or one of its similar listings), 'fit': 0..1 how much its title words look like a
    product you starred, 'like_of': that product's title when they look alike}."""
    import file_rank as fr
    asins = [r['asin']] + [m['asin'] for m in r.get('members') or []]
    words = set(fr.search_words(r.get('title') or '').lower().split())
    fit, like_of = 0.0, None
    for a, title, lw in liked:
        if a in asins or not words or not lw:
            continue
        j = len(words & lw) / len(words | lw)
        if j > fit:
            fit, like_of = j, title
    return {'liked': any((pref.get(a) or {}).get('liked_at') for a in asins), 'fit': round(fit, 2),
            'like_of': like_of if fit >= TASTE_MIN else None}


def _group_marks(lists):
    """One presence row for a group: on a list (1) when any listing was, else deal-only (2), else read and off (0)."""
    out = []
    for day in zip(*lists) if lists else ():
        known = [m for m in day if m is not None]
        out.append(1 if 1 in known else 2 if 2 in known else 0 if known else None)
    return out


def _member(r):
    return {'asin': r['asin'], 'title': r.get('title'), 'price': r.get('price'), 'reviews': r.get('reviews'),
            'url': r.get('url'), 'image': r.get('image'), 'score': r['opportunity']['score']}


def _gaps(conn, market, rows, settings, now):
    """Each row's 'gap': for every market it could sell in next (TARGETS) the arbitrage score (engine.scores) of its
    search phrase from the newest search there in the last GAP_DAYS days, or None when there is none."""
    targets = TARGETS[market]
    kws = {r['asin']: config.norm_kw(r.get('search') or '') for r in rows}
    for r in rows:
        r['gap'] = {t: None for t in targets}
    for t in targets:
        have = _searched(conn, t, kws.values(), now - GAP_DAYS * 86400)
        if not have:
            continue
        sugg = storage.suggestions(conn, t, sorted(have))
        for r in rows:
            if kws[r['asin']] in have:
                r['gap'][t] = _gap_for(conn, market, _boom(r), t, kws[r['asin']], settings, sugg,
                                       (r.get('buy') or {}).get('cost_usd'))


def _boom(r):
    """A scored row's boom on its own market for the arbitrage score: its search trend and its Amazon lists signal."""
    parts = {p['key']: p for p in r['opportunity']['parts']}
    return {'trend_score': (parts['S'].get('raw') or {}).get('score') if parts['S']['known'] else None,
            'cluster_surge': 100 * parts['A']['v'] if parts['A']['known'] else None}


def _searched(conn, market, kws, since):
    """The phrases (kw_norm) with a usable search in `market` since `since`."""
    want, have = sorted({k for k in kws if k}), set()
    for i in range(0, len(want), 400):
        part = want[i:i + 400]
        have.update(x[0] for x in conn.execute(
            'SELECT DISTINCT kw_norm FROM serp_snapshot WHERE market=? AND ts>=? AND status IN (%s) AND kw_norm IN (%s)'
            % (','.join('?' * len(storage.VALID)), ','.join('?' * len(part))), (market, since, *storage.VALID, *part)))
    return have


def _gap_for(conn, source, boom, t, kw, settings, sugg=None, cogs_usd=None):
    """The arbitrage score (engine.scores) of selling `kw` in market t from its newest search there, for a boom on
    market `source` (boom: {'trend_score', 'cluster_surge'} 0-100). None when it was never searched there.
    sugg: t's stored suggestions when already loaded; cogs_usd: your supplier price (its margin there counts)."""
    from engine import discovery, scores
    n = niche_inputs(conn, t, kw, settings, cogs_usd)
    if not n:
        return None
    sugg = sugg if sugg is not None else storage.suggestions(conn, t, [kw])
    m, tf = n['metrics'], n['trend'] or {}
    tm = {'market': t, 'trends_monthly': [p[1] for p in tf.get('monthly') or [] if isinstance(p, (list, tuple))],
          'trend_label': tf.get('label'),
          'autocomplete': discovery.autocomplete_knows(kw, sugg[kw]['keywords']) if kw in sugg else None,
          'bought_mid_sum': n['bought_mid_sum'], 'osd_share': m.get('osd_share'), 'fast_local': m.get('fast_local'),
          'margin': m.get('margin'), 'left_pct': m.get('left_pct')}
    a = scores.arbitrage({'market': source, **boom}, tm)
    return {'score': a['score'], 'label': a['label'], 'conf': a['conf'], 'why': a['why'],
            'window': (a.get('window') or {}).get('status'), 'phrase': kw,
            'fast_local': m.get('fast_local'), 'osd_share': m.get('osd_share'),
            'units': n['bought_mid_sum'] or None, 'price': n['price'], 'searched_at': n['summary'].get('checked_at')}


def google_rising(db, market, settings, now=None, limit=20):
    """Google-first discovery: searches Google marks Breakout, or rising RISING_MIN% or more, among the related searches
    of the terms read in this market in the last RISING_DAYS days; not a term already read or a watched keyword. Each
    with the terms it came from, its opportunity here once searched on Amazon, and its gap score in the markets it
    could sell in next. Breakouts first, then the biggest rise."""
    from engine import discovery
    now = now or time.time()

    def build(conn):
        rows = conn.execute(
            "SELECT r.term, r.query, r.value, r.breakout, r.fetched_at FROM trends_related r JOIN (SELECT term, "
            "MAX(fetched_at) AS f FROM trends_related WHERE geo=? AND fetched_at>=? GROUP BY term) n ON n.term=r.term "
            "AND n.f=r.fetched_at WHERE r.geo=? AND r.kind='rising'", (market, now - RISING_DAYS * 86400, market)).fetchall()
        known = {w['target'] for w in storage.watches(conn) if w['kind'] == 'keyword'}
        known |= {t for (t,) in conn.execute('SELECT DISTINCT term FROM trends_related WHERE geo=?', (market,))}
        found = {}
        for term, query, value, breakout, at in rows:
            q = config.norm_kw(query or '')
            if not q or q in known or (not breakout and (value or 0) < RISING_MIN):
                continue
            f = found.setdefault(discovery.phrase_key(q), {'query': q, 'from': [], 'breakout': False, 'rise': 0, 'at': at})
            if term not in f['from']:
                f['from'].append(term)
            f['breakout'] = f['breakout'] or bool(breakout)
            f['rise'], f['at'] = max(f['rise'], value or 0), max(f['at'], at)
        out = sorted(found.values(), key=lambda f: (not f['breakout'], -f['rise'], f['query']))[:limit]
        since = now - GAP_DAYS * 86400
        here = _searched(conn, market, [f['query'] for f in out], since)
        abroad = {t: _searched(conn, t, [f['query'] for f in out], since) for t in TARGETS[market]}
        for f in out:
            n = niche_inputs(conn, market, f['query'], settings) if f['query'] in here else None
            f['opportunity'] = {'score': n['opportunity'].get('score'), 'label': n['opportunity'].get('label')} if n else None
            boom = {'trend_score': (storage.trends_feature(conn, f['from'][0], market) or {}).get('score'), 'cluster_surge': None}
            f['gap'] = {t: _gap_for(conn, market, boom, t, f['query'], settings) if f['query'] in abroad[t] else None
                        for t in TARGETS[market]}
        return out
    return db.read(build)


def variant_gaps(db, market, phrase, variants, boom, settings, hits=None):
    """Find the underserved version: each variant's gap score in the markets the product could sell in next (from
    their newest searches there), best first, and the phrase's own for comparison. hits: {variant: Amazon hits}."""
    def build(conn):
        sugg = {t: storage.suggestions(conn, t, [phrase] + list(variants)) for t in TARGETS[market]}

        def gaps(kw):
            return {t: _gap_for(conn, market, boom, t, kw, settings, sugg[t]) for t in TARGETS[market]}
        rows = []
        for v in variants:
            g = gaps(v)
            scored = [x['score'] for x in g.values() if x and x['score'] is not None]
            rows.append({'kw': v, 'hits': (hits or {}).get(v), 'gap': g, 'best': max(scored) if scored else None})
        rows.sort(key=lambda r: -(r['best'] if r['best'] is not None else -1))
        return {'phrase': phrase, 'market': market, 'parent': gaps(phrase), 'variants': rows}
    return db.read(build)


def qualify_candidates(db, market, settings, now=None, n=10, checks=0):
    """The radar's best rows that still miss something qualifying reads: a product page of the last week, Amazon's
    suggestions of the last month, a Google trend for the phrase, or (the first `checks` rows only) a search on a
    market it could sell in next. Deal-driven and risky rows are left out."""
    now = now or time.time()
    r = radar(db, market, now=now, limit=4 * n, settings=settings, **radar_filters(settings, market))
    ok = [it for it in r['items'] if not it['deal'] and not any(f != 'price unknown' for f in it['flags'])]

    def needs(k, it):
        return ((it.get('page_at') or 0) < now - QUALIFY_FRESH['page'] * 86400
                or (it.get('phrase_at') or 0) < now - QUALIFY_FRESH['phrase'] * 86400
                or not it.get('trend') or (k < checks and not all((it.get('gap') or {}).values())))
    return [it for k, it in enumerate(ok) if needs(k, it)][:n]


def _trend_summary(m):
    if not m:
        return None
    f = m['feature']
    season = f.get('seasonality') or {}
    return {'term': m['term'], 'match': m['match'], 'status': f.get('status') or 'ok', 'label': f.get('label'),
            'score': f.get('score'), 'q': f.get('q'), 'at': f.get('computed_at'),
            'peak_month': season.get('peak_month') if season.get('reliable') else None}


def _presence(scans, market, now, days=RADAR_DAYS):
    """The last `days` market days (oldest first) and a function asin -> one mark per day: 1 on a list, 2 on a list only
    with a deal badge, 0 its lists were read in full and it was not on them, None no full reading of its lists."""
    from engine import surge
    keys = [config.market_day(now - k * 86400, market) for k in range(days - 1, -1, -1)]
    full, seen, lists = {}, {}, {}
    for sc in scans:
        if sc['status'] not in ('ok', 'partial'):
            continue
        lk = (sc['kind'], sc['slug'])
        if (sc['n'] or 0) >= surge.THRESH['valid_n_share'] * (sc.get('n_median') or 0):
            full.setdefault(lk, set()).add(sc['day'])
        for it in sc['items']:
            lists.setdefault(it['asin'], set()).add(lk)
            d = seen.setdefault(it['asin'], {})
            d[sc['day']] = d.get(sc['day'], True) and bool(it.get('deal'))

    def marks(asin):
        hit, mine = seen.get(asin, {}), lists.get(asin, ())
        return [(2 if hit[k] else 1) if k in hit else 0 if any(k in full.get(lk, ()) for lk in mine) else None
                for k in keys]
    return keys, marks


def _evidence(conn, market, asins, now):
    """What market.db knows about these listings besides the lists: the newest product page of the last 30 days, the
    newest Helium 10 row and the package size. Three {asin: row} dicts."""
    pages, h10, size = {}, {}, {}
    asins = list(dict.fromkeys(asins))
    for i in range(0, len(asins), 400):
        part = asins[i:i + 400]
        q = ','.join('?' * len(part))
        for r in conn.execute("SELECT asin, ts, badge_low, reviews, price, root_rank, root_node FROM product_snapshot WHERE "
                              "market=? AND asin IN (%s) AND status IN ('ok','partial') AND ts>=? ORDER BY ts" % q,
                              (market, *part, now - PRODUCT_PAGE_DAYS * 86400)):
            pages[r['asin']] = dict(r)
        for r in conn.execute('SELECT r.asin, r.sales_asin, r.reviews, r.fba_fees, r.weight_kg, f.as_of FROM h10_row r '
                              'JOIN import_file f ON f.id=r.import_id WHERE f.market=? AND r.asin IN (%s) ORDER BY f.as_of' % q,
                              (market, *part)):
            h10[r['asin']] = dict(r)
        for r in conn.execute('SELECT asin, pkg_l_cm, pkg_w_cm, pkg_h_cm, pkg_kg FROM asin WHERE market=? AND asin IN (%s)' % q,
                              (market, *part)):
            size[r['asin']] = dict(r)
    return pages, h10, size


def _units(market, row, page, h, cv, now):
    """Monthly units and their source: the product page's badge, then Helium 10, then the sales rank (product page, else
    the list) on the market's curve, at low confidence while the curve is not calibrated. None when nothing is known."""
    from engine import badge
    from engine import curve as curvemod
    if page.get('badge_low'):
        b = badge.badge_estimate(page['badge_low'], market)
        if b:
            return {**b, 'src': 'badge'}
    if h.get('sales_asin'):
        return {'v': h['sales_asin'], 'src': 'h10',
                'conf': 'medium' if (h.get('as_of') or 0) >= now - H10_FRESH_DAYS * 86400 else 'low'}
    for node, rank, src in ((page.get('root_node'), page.get('root_rank'), 'rank'), (row.get('slug'), row.get('rank_now'), 'list')):
        u = curvemod.predict(cv, node, rank) if cv and rank else None
        if u:
            return {**u, 'src': src, 'rank': rank, 'conf': u['conf'] if cv.get('status') == 'calibrated' else 'low'}
    return None


def _coverage(rows):
    """How many candidates have each discovery signal measured."""
    known = {'A': 0, 'S': 0, 'D': 0, 'C': 0, 'E': 0}
    for r in rows:
        for p in (r.get('opportunity') or {}).get('parts') or []:
            if p['known']:
                known[p['key']] += 1
    return {'n': len(rows), 'known': known}


def search_trends(db):
    """Google Trends labels of watched keywords, grouped into lanes."""
    lanes = {'Rising': ('Breakout', 'Emerging', 'Growing'), 'Seasonal': ('Seasonal',), 'Watch': ('Spike (watch)', 'Mixed'),
             'Steady': ('Evergreen',), 'Fading': ('Declining', 'Fad')}
    out = {k: [] for k in lanes}

    def build(conn):
        for r in conn.execute('SELECT term, geo, label, score, computed_at FROM trends_feature ORDER BY score DESC'):
            base = (r[2] or '').split(',')[0].strip()
            for lane, labels in lanes.items():
                if base in labels:
                    out[lane].append({'term': r[0], 'geo': r[1], 'label': r[2], 'score': r[3], 'at': r[4]})
        return out
    return db.read(build)


def watchlist(db, settings, now=None):
    now = now or time.time()

    def build(conn):
        rows = []
        tasks = {(t['kind'], t['market'], t['target']): t for t in storage._rows(conn.execute('SELECT * FROM task'))}
        for w in storage.watches(conn, enabled_only=False):
            r = dict(w)
            if w['kind'] == 'keyword':
                snaps = storage.serp_history(conn, w['market'], w['target'], now - 90 * 86400)
                valid = [s for s in snaps if s['status'] in storage.VALID]
                last = valid[-1] if valid else None
                week = [s for s in valid if s['ts'] <= now - 6 * 86400]
                prev = week[-1] if week else None
                tf = storage.trends_feature(conn, w['target'], w['market'])
                t = tasks.get(('watch_kw', w['market'], w['target']))
                r.update({
                    'spark': [s['bought_mid_sum'] for s in valid][-20:],
                    'units': est(last['bought_mid_sum'], last['bought_low_sum'], last['bought_high_sum']) if last and last['bought_mid_sum'] is not None else None,
                    'fast': last['fast'] if last else None, 'local': last['local'] if last else None,
                    'fast_change': (last['fast'] - prev['fast']) if last and prev and last['fast'] is not None and prev['fast'] is not None else None,
                    'overseas_share': round(last['overseas'] / last['organic'], 3) if last and last['organic'] else None,
                    'price_med': last['price_med'] if last else None, 'review_barrier': last['reviews_med_top10'] if last else None,
                    'checked_at': last['ts'] if last else None, 'last_status': snaps[-1]['status'] if snaps else None,
                    'trend': {'label': tf.get('label'), 'score': tf.get('score')} if tf else None,
                    'next_run': t['next_run_at'] if t and t['enabled'] else None, 'currency': ac.MARKETS[w['market']]['currency'],
                    'display': (conn.execute('SELECT display FROM keyword WHERE kw_norm=?', (w['target'],)).fetchone() or [w['display']])[0],
                })
            rows.append(r)
        return rows
    return db.read(build)


def home(db, S, now=None):
    now = now or time.time()

    def build(conn):
        alerts = storage.list_alerts(conn, now - 14 * 86400, limit=80)
        week = [a for a in alerts if a['ts'] >= now - 7 * 86400]
        watches = storage.watches(conn)
        gaps = []
        for w in watches:
            if w['kind'] != 'keyword' or w['market'] not in ('AU', 'AE'):
                continue
            s = storage.serp_history(conn, w['market'], w['target'], now - 30 * 86400, valid_only=True)
            if s and s[-1]['organic'] and (s[-1]['fast'] or 0) <= 4:
                gaps.append({'market': w['market'], 'kw': w['target'], 'fast': s[-1]['fast'], 'units': s[-1]['bought_mid_sum']})
        gaps.sort(key=lambda g: -(g['units'] or 0))
        counts = storage.counts(conn)
        imports = counts['import_file']
        return {'alerts': alerts, 'new_7d': len(week), 'market_alerts_7d': sum(1 for a in week if a['rule'] in (
                    'new_mover', 'persistent_riser', 'cluster_surge', 'confirmed_trend', 'trends_breakout', 'trend_label_change')),
                'watch_n': len(watches), 'gaps': gaps[:5], 'counts': counts,
                'checklist': {'checked': counts['serp_snapshot'] > 0, 'imported': imports > 0, 'watching': len(watches) > 0,
                              'auto': bool(S.settings.get('auto_refresh')) if S else False,
                              'startup': bool(S.settings.get('start_with_windows')) if S else False}}
    out = db.read(build)
    if S is not None:
        out['scheduler'] = S.scheduler.status() if getattr(S, 'scheduler', None) else None
        out['sources'] = {k: v for k, v in S.throttle.state(now).items() if v['cooldown_until']} if getattr(S, 'throttle', None) else {}
        out['trends'] = S.trends.breaker.state()
    return out


def health(db, S, now=None):
    now = now or time.time()

    def build(conn):
        runs = storage._rows(conn.execute('SELECT * FROM run ORDER BY id DESC LIMIT 20'))
        tasks = storage._rows(conn.execute('SELECT * FROM task WHERE enabled=1 ORDER BY kind, market, target'))
        return {'runs': runs, 'tasks': tasks, 'counts': storage.counts(conn)}
    out = db.read(build)
    out.update(db_mb=db.size_mb(), db_path=str(db.path),
               sources=S.throttle.state(now) if S else {}, scheduler=S.scheduler.status() if S else None,
               trends=S.trends.breaker.state() if S else None, browser={'state': S.checker.state, 'error': S.checker.error} if S else None)
    return out


# ---------- Phase 3: product report and calibration ----------
def product_report(db, market, asin, settings, now=None):
    """One listing: BSR history, estimated monthly units and revenue (from the market's curve), Helium 10 rows."""
    from engine import curve as curvemod
    from engine import sales
    now = now or time.time()

    def build(conn):
        info = storage.asin_info(conn, market, asin) or {}
        hist = storage.product_history(conn, market, asin)
        valid = [h for h in hist if h['status'] in storage.VALID]
        cv = storage.get_curve(conn, market)
        node = info.get('root_node')
        samples = [(h['ts'], h['root_rank']) for h in valid if h['root_rank']]
        if node:                                                   # list ranks are the same root ranking
            for r in conn.execute('SELECT s.ts, i.rank_now FROM list_item i JOIN list_snapshot s ON s.id=i.snapshot_id '
                                  'WHERE s.market=? AND s.slug=? AND i.asin=? AND i.rank_now IS NOT NULL', (market, node, asin)):
                samples.append((r[0], r[1]))
        badges = [{'badge': h['badge_low'], 'bsr': h['root_rank']} for h in valid if h['badge_low'] and h['root_rank']]
        month_fn = lambda t: config.month_key(t, market)            # noqa: E731
        units, offset = {}, None
        if cv and samples:
            offset = sales.asin_offset_info(cv, node, badges)
            units = sales.units_from_bsr(samples, cv, node, month_fn, offset)
        prices = {}
        for h in valid:
            if h['price']:
                prices[config.month_key(h['ts'], market)] = h['price']
        rev = sales.revenue(units, prices) if units and prices else {}
        h10 = [dict(r) for r in conn.execute(
            'SELECT f.month, f.as_of, f.kind, r.sales_asin, r.sales_parent, r.revenue, r.bsr, r.price FROM h10_row r '
            'JOIN import_file f ON f.id=r.import_id WHERE f.market=? AND r.asin=? ORDER BY f.as_of', (market, asin))]
        last = valid[-1] if valid else None
        now_units = curvemod.predict(cv, node, last['root_rank']) if cv and last and last['root_rank'] else None
        other = {}
        for m in config.MARKETS:
            if m == market:
                continue
            r = conn.execute('SELECT ts, root_rank, price, offers, origin FROM product_snapshot WHERE market=? AND asin=? '
                             "AND status IN ('ok','partial') ORDER BY ts DESC LIMIT 1", (m, asin)).fetchone()
            other[m] = dict(r) if r else None
        return {
            'market': market, 'asin': asin, 'currency': ac.MARKETS[market]['currency'],
            'url': '%s/dp/%s' % (ac.MARKETS[market]['site'], asin), 'info': info,
            'history': [{k: h[k] for k in ('ts', 'status', 'root_rank', 'badge_low', 'price', 'offers', 'rating', 'reviews',
                                           'buybox_seller', 'ships_from', 'sold_by_amazon', 'origin', 'delivery_days')}
                        for h in hist][-400:],
            'now': last, 'units_now': now_units,
            'monthly': [{'month': m, 'units': {k: v.get(k) for k in ('v', 'lo', 'hi', 'basis', 'conf')},
                         'coverage': v.get('coverage'), 'partial': v.get('partial'),
                         'revenue': {k: rev[m].get(k) for k in ('v', 'lo', 'hi', 'basis', 'conf')} if m in rev else None}
                        for m, v in sorted(units.items())],
            'offset': offset, 'h10': h10,
            'curve': {k: cv.get(k) for k in ('status', 'n_obs', 'beta', 'sigma', 'fitted_at', 'diag')} if cv else None,
            'other_markets': other,
        }
    return db.read(build)


def calibration(db):
    """Per market: how well the BSR -> units curve is pinned down, in plain words."""
    def build(conn):
        out = {}
        for m in config.MARKETS:
            cv = storage.get_curve(conn, m)
            kinds = {r[0]: r[1] for r in conn.execute('SELECT kind, COUNT(*) FROM calib_point WHERE market=? GROUP BY kind', (m,))}
            out[m] = {'points': kinds, 'curve': {k: cv.get(k) for k in ('status', 'n_obs', 'beta', 'sigma', 'delta_h', 'fitted_at',
                                                                       'diag', 'n_by_kind', 'converged')} if cv else None}
        return out
    return db.read(build)


# ---------- Phase 4: money and decisions ----------
def niche_inputs(conn, market, kw, settings, cogs_usd=None):
    """Everything the scores need for one keyword in one market, from the newest valid search."""
    from engine import badge, profit, scores
    s = latest(conn, market, kw, settings)
    if not s:
        return None
    organic = [r for r in s['rows'] if not r['sponsored']]
    products = [{'units': badge.badge_estimate(r['bought'], market) if r.get('bought') else None, 'reviews': r.get('reviews'),
                 'origin': r.get('origin'), 'speed': r.get('speed'), 'sponsored': r.get('sponsored')} for r in s['rows']]
    m = scores.niche_metrics(products, market=market)
    tf = storage.trends_feature(conn, kw, market) or {}
    m['momentum'] = scores.niche_momentum(tf.get('score'), None, tf.get('q'))
    m['risk_flags'] = scores.risk_flags([r.get('title') for r in organic[:10]])
    m['market'] = market
    prices = sorted(r['price'] for r in organic if r.get('price'))
    price = prices[len(prices) // 2] if prices else None
    econ = None
    if price:
        econ = profit.unit_economics({'market': market, 'price': price, 'cogs_usd': cogs_usd or 0.0,
                                      'registered': bool(settings.get('registered', {}).get(market)),
                                      'category': ''})
        if cogs_usd:
            m['margin'] = econ['Margin']
        else:                                     # without a product cost: what is left after Amazon, minus an assumed
            # 25% of the price for product and freight (scores count this at half weight)
            after = (econ['Profit'] + econ['Landed']) / econ['NetSales'] if econ['NetSales'] else None
            m['left_pct'] = max(0.0, after - ASSUMED_LANDED_SHARE) if after is not None else None
    sat = scores.saturation(m)
    return {'summary': s, 'metrics': m, 'price': price, 'economics': econ, 'opportunity': scores.opportunity(m),
            'saturation': sat, 'trend': tf, 'bought_mid_sum': sum((p['units'] or {}).get('v') or 0 for p in products
                                                                if not p['sponsored'])}


def size_suggestion(conn, market, kw, top=5):
    """The typical package size and weight for a keyword: the median of its best page-1 listings whose Amazon product
    page gave a size (a Helium 10 export can give the weight). 'missing' = top listings without a size, best first."""
    out = {'dims_cm': None, 'kg': None, 'n': 0, 'asins': [], 'missing': [], 'searched': False}
    s = storage.latest_serp(conn, market, kw)
    if not s:
        return out
    out['searched'] = True
    dims, kgs = [], []
    for a in list(dict.fromkeys(r['asin'] for r in s['rows'] if not r.get('sponsored') and r.get('asin')))[:top]:
        info = storage.asin_info(conn, market, a) or {}
        d, kg = [info.get('pkg_l_cm'), info.get('pkg_w_cm'), info.get('pkg_h_cm')], info.get('pkg_kg')
        if not kg:
            h = conn.execute('SELECT r.weight_kg FROM h10_row r JOIN import_file f ON f.id=r.import_id WHERE f.market=? '
                             'AND r.asin=? AND r.weight_kg > 0 ORDER BY f.as_of DESC LIMIT 1', (market, a)).fetchone()
            kg = h[0] if h else None
        if kg:
            kgs.append(kg)
        if all(d):
            dims.append(d)
            out['asins'].append(a)
        else:
            out['missing'].append(a)
    if dims:                                            # each listing is stored longest side first
        out['dims_cm'] = [round(_median([x[i] for x in dims]), 1) for i in range(3)]
    out['kg'] = round(_median(kgs), 3) if kgs else None
    out['n'] = len(dims)
    return out


def pick_reviews(rows, n=5, per_listing=2):
    """The top negative reviews: 1-2 stars before 3 stars, this marketplace before other countries, then the most
    helpful and the newest. At most `per_listing` from one listing while other listings still have some."""
    ranked = sorted(rows, key=lambda r: (r['stars'] > 2, not r['local'], -(r['helpful'] or 0),
                                         -int((r['day'] or '0').replace('-', ''))))
    out, per = [], {}
    for r in ranked:
        if len(out) < n and per.get(r['asin'], 0) < per_listing:
            out.append(r)
            per[r['asin']] = per.get(r['asin'], 0) + 1
    out += [r for r in ranked if r not in out][:n - len(out)]
    return sorted(out, key=ranked.index)


def reviews_for(db, market, kw=None, asin=None, top=5, now=None):
    """The top negative reviews of a keyword's best page-1 listings (or of one listing), with the listing each one is
    about. `todo` = those listings whose reviews were not read in the last two weeks, best first. 'themes' groups all
    their stored critical reviews into complaints (engine.complaints) and 'brief' says what to ask suppliers."""
    from engine import complaints
    now = now or time.time()
    site = ac.MARKETS[market]['site']

    def build(conn):
        out = {'market': market, 'items': [], 'asins': [], 'todo': [], 'searched': True, 'read_at': None, 'read': 0, 'critical': 0}
        names = {}
        if asin:
            asins = [asin]
        else:
            s = storage.latest_serp(conn, market, config.norm_kw(kw or ''))
            if not s:
                out['searched'] = False
                return out
            organic = [r for r in s['rows'] if not r.get('sponsored') and r.get('asin')]
            names = {r['asin']: r.get('title') for r in organic}
            asins = list(dict.fromkeys(r['asin'] for r in organic))[:top]
        rows, reads = storage.reviews_of(conn, market, asins)
        found = complaints.themes(rows)
        subject = kw or ((storage.asin_info(conn, market, asin) or {}).get('title') or asin)[:80]
        out.update(themes=found['themes'], n_reviews=found['n'], other=found['other'],
                   brief=complaints.brief(found, subject, None if asin else len({r['asin'] for r in rows})))
        for a in asins:
            rd = reads.get(a) or {}
            last = max(rd.get('page_at') or 0, rd.get('critical_at') or 0)
            if last < now - LISTING_MAX_AGE:
                out['todo'].append(a)
            out['read_at'] = max(out['read_at'] or 0, last) or None
            out['read'] += 1 if last else 0                        # listings read, and how many from their reviews page
            out['critical'] += 1 if rd.get('critical_at') else 0
        out['asins'] = asins
        for r in pick_reviews(rows):
            name = names.get(r['asin']) or (storage.asin_info(conn, market, r['asin']) or {}).get('title') or r['asin']
            out['items'].append({k: r[k] for k in ('asin', 'stars', 'title', 'body', 'day', 'country', 'helpful')} |
                                {'local': bool(r['local']), 'verified': bool(r['verified']), 'listing': name,
                                 'country_code': countries.code_of((r['country'] or '').removeprefix('the ')),
                                 'url': '%s/dp/%s' % (site, r['asin']), 'all_url': product.critical_url(market, r['asin'])})
        return out
    return db.read(build)


def _who_sells(conn, market, kw, now):
    from engine import badge
    from engine import curve as curvemod
    from engine import sellers
    out = {'market': market, 'searched': False, 'listings': [], 'read_at': None, 'todo': {'pages': [], 'sellers': []}}
    s = storage.latest_serp(conn, market, config.norm_kw(kw or ''))
    top, seen = [], set()
    for r in (s or {}).get('rows') or []:
        if not r.get('sponsored') and r.get('asin') and r['asin'] not in seen:
            seen.add(r['asin'])
            top.append(r)
    top = top[:TOP_LISTINGS]
    asins = [r['asin'] for r in top]
    prods = storage.latest_products(conn, market, asins)
    known = storage.sellers_of(conn, market, [p.get('seller_id') for p in prods.values()])
    h10 = {}
    if asins:
        for r in conn.execute('SELECT r.asin, r.brand, r.seller_country, r.fulfillment, r.sales_asin FROM h10_row r JOIN '
                              'import_file f ON f.id=r.import_id WHERE f.market=? AND r.asin IN (%s) ORDER BY f.as_of'
                              % ','.join('?' * len(asins)), (market, *asins)):
            h10[r['asin']] = dict(r)                                   # the newest export wins
    cv = storage.get_curve(conn, market)
    site = ac.MARKETS[market]['site']
    for r in top:
        a, p, h = r['asin'], prods.get(r['asin']) or {}, h10.get(r['asin']) or {}
        ships = p.get('fulfillment') or H10_SHIPS.get(str(h.get('fulfillment') or '').strip().upper())
        amazon, sid = ships == 'amazon', p.get('seller_id')
        sl = known.get(sid) or {}
        units = src = None
        if r.get('bought'):
            units, src = badge.badge_estimate(r['bought'], market), 'badge'
        elif p.get('root_rank') and cv:
            units, src = curvemod.predict(cv, p.get('root_node'), p['root_rank']), 'rank'
        elif h.get('sales_asin'):
            units, src = {'v': h['sales_asin']}, 'h10'
        out['listings'].append({
            'asin': a, 'title': r.get('title'), 'url': r.get('url') or '%s/dp/%s' % (site, a), 'price': r.get('price'),
            'reviews': r.get('reviews'), 'rating': r.get('rating'),
            'units': {k: units.get(k) for k in ('v', 'lo', 'hi')} | {'src': src} if units and units.get('v') else None,
            'seller': 'Amazon' if amazon else sl.get('name') or p.get('buybox_seller'), 'seller_id': None if amazon else sid,
            'seller_url': product.seller_url(market, sid) if sid and not amazon else None,
            'country': market if amazon else sl.get('country') or countries.code_of(h.get('seller_country')),
            'business': sl.get('business_name'), 'seller_rating': sl.get('rating_pct'), 'seller_ratings': sl.get('ratings'),
            'ships': ships, 'brand': p.get('brand') or h.get('brand') or None, 'launched': p.get('dfa_day'),
            'read_at': p.get('ts')})
        if not p or (p.get('ts') or 0) < now - LISTING_MAX_AGE:
            out['todo']['pages'].append(a)
        if sid and not amazon and (sid not in known or (known[sid].get('fetched_at') or 0) < now - SELLER_MAX_AGE) \
                and sid not in out['todo']['sellers']:
            out['todo']['sellers'].append(sid)
    out['searched'] = bool(s)
    out['read_at'] = max((x['read_at'] or 0 for x in out['listings']), default=0) or None
    out.update(sellers.who_sells(out['listings'], market, config.market_today(market)))
    return out


def who_sells(db, market, kw, now=None):
    """Who sells a keyword's best page-1 listings: seller, their country, brand and how each ships (from the product
    and seller pages read so far; Helium 10 exports fill gaps). `todo` = product pages older than two weeks and
    sellers older than a month, best first."""
    return db.read(_who_sells, market, kw, now or time.time())


def _sentence(conn, market, kw_norm, settings, n=None, cogs=None, now=None):
    from engine import verdict
    n = n or niche_inputs(conn, market, kw_norm, settings, cogs)
    if not n:
        return verdict.sentence(market, searched=False)
    w, e = _who_sells(conn, market, kw_norm, now or time.time()), n['economics'] or {}
    return verdict.sentence(
        market, label=(n['opportunity'] or {}).get('label'), units=n['bought_mid_sum'] or None,
        fast_local=n['metrics'].get('fast_local'), dominant=w['dominant'], amazon=w['amazon'] if w['known']['seller'] else None,
        price=n['price'], profit=e.get('Profit') if cogs and e else None,
        left=e['Profit'] + e['Landed'] if e and not cogs else None, cur=ac.MARKETS[market]['currency'],
        trend=(n['trend'] or {}).get('label'))


def sentence(db, market, kw, settings):
    """The keyword's situation in one plain sentence (from what is stored; nothing is fetched)."""
    kw_norm = config.norm_kw(kw or '')

    def build(conn):
        w = conn.execute("SELECT cogs FROM watch WHERE kind='keyword' AND market=? AND target=?", (market, kw_norm)).fetchone()
        return _sentence(conn, market, kw_norm, settings, cogs=w[0] if w and w[0] else None)
    return db.read(build)


def money(db, market, kw, settings, now=None):
    """Opportunity, saturation, unit economics at the median price, launch plan, and US -> AU/UAE arbitrage."""
    from engine import planner, scores
    kw_norm = config.norm_kw(kw)

    def build(conn):
        w = conn.execute("SELECT cogs FROM watch WHERE kind='keyword' AND market=? AND target=?", (market, kw_norm)).fetchone()
        cogs = w[0] if w and w[0] else None
        n = niche_inputs(conn, market, kw_norm, settings, cogs)
        tf = storage.trends_feature(conn, kw_norm, market) or {}
        season = tf.get('seasonality') or {}
        plan = planner.plan(season.get('si'), season.get('strength'), season.get('reliable'), season.get('peak_month'),
                            config.market_today(market).isoformat(), market=market) if season.get('si') else \
            {'status': 'unknown', 'note': 'Seasonality unknown: fetch Google Trends for this keyword first'}
        out = {'market': market, 'cogs_usd': cogs, 'plan': plan, 'arbitrage': {}, 'size': size_suggestion(conn, market, kw_norm),
               'sentence': _sentence(conn, market, kw_norm, settings, n, cogs, now)}
        if n:
            m = dict(n['metrics'])
            m.pop('target', None)
            out.update(opportunity=n['opportunity'], saturation=n['saturation'], economics=n['economics'], price=n['price'],
                       metrics=m)
        if market == 'US':
            us = {'trend_score': tf.get('score')}
            for t in ('AU', 'AE'):
                nt = niche_inputs(conn, t, kw_norm, settings, cogs)
                if not nt:
                    out['arbitrage'][t] = None
                    continue
                ttf = nt['trend']
                tm = {'market': t, 'autocomplete': None, 'trends_monthly': ttf.get('monthly'), 'bought_mid_sum': nt['bought_mid_sum'],
                      'osd_share': nt['metrics'].get('osd_share'), 'fast_local': nt['metrics'].get('fast_local'),
                      'margin': nt['metrics'].get('margin'), 'left_pct': nt['metrics'].get('left_pct'), 'trend_label': ttf.get('label')}
                out['arbitrage'][t] = scores.arbitrage(us, tm)
        return out
    return db.read(build)


def launch(db, market, kw, settings, cogs_usd=None, units=300, dims=None, kg=None, freight_mode='sea', boom=None):
    """Where to launch: for the keyword's own market and the markets it could sell in next (TARGETS), from their
    newest stored search: the page-1 median price, your profit there with your supplier price (US$), the most you could
    pay for settings' target margin, the sales a new listing might win, what a first order of `units` means
    (engine.launch) and the market gap score (for `boom`, else the keyword's search trend). Markets not searched yet
    say so. Returns {'kw', 'units', 'cost_usd', 'margin_pct', 'markets': {mk: {...}}, 'pick'}."""
    from engine import launch as lch
    from engine import profit
    kw_norm = config.norm_kw(kw)
    margin = (settings.get('target_margin_pct') or 20) / 100.0

    def build(conn):
        tf = storage.trends_feature(conn, kw_norm, market) or {}
        b = {'trend_score': tf.get('score'), 'cluster_surge': None}
        b.update({k: v for k, v in (boom or {}).items() if v is not None})
        out = {'kw': kw_norm, 'market': market, 'units': units, 'cost_usd': cogs_usd, 'margin_pct': round(100 * margin),
               'markets': {}, 'pick': None}
        for mk in (market,) + TARGETS[market]:
            n = niche_inputs(conn, mk, kw_norm, settings)
            if not n or not n['price']:
                out['markets'][mk] = {'searched': bool(n)}
                continue
            inp = {'market': mk, 'price': n['price'], 'category': '', 'cogs_usd': cogs_usd or 0.0, 'freight_mode': freight_mode,
                   'registered': bool((settings.get('registered') or {}).get(mk)), 'date': config.market_today(mk).isoformat()}
            if dims:
                inp['dims_cm'] = list(dims)
            if kg:
                inp['kg'] = kg
            e = profit.unit_economics(inp)
            new = lch.newcomer_units((n['summary'].get('per_seller') or {}).get('per_seller'), n['metrics'].get('review_barrier'), mk)
            m = {'searched': True, 'price': n['price'], 'searched_at': n['summary'].get('checked_at'),
                 'fast_local': n['metrics'].get('fast_local'), 'newcomer': new, 'max_usd': profit.max_cogs(inp, margin) or None,
                 'sim': lch.simulate(units, e, new) if cogs_usd and e else None,
                 'gap': _gap_for(conn, market, b, mk, kw_norm, settings, cogs_usd=cogs_usd) if mk != market else None}
            out['markets'][mk] = m
        out['pick'] = lch.pick({mk: dict(m['sim'], gap=(m.get('gap') or {}).get('score'))
                                for mk, m in out['markets'].items() if m.get('sim')})
        return out
    return db.read(build)


def opportunities(db, settings, limit=10):
    """Watched keywords ranked by opportunity, and US keywords ranked by how well they would travel to AU / UAE."""
    def build(conn):
        rows = []
        for w in storage.watches(conn):
            if w['kind'] != 'keyword':
                continue
            n = niche_inputs(conn, w['market'], w['target'], settings, w.get('cogs'))
            if n and n['opportunity'].get('score') is not None:
                rows.append({'market': w['market'], 'kw': w['target'], 'score': n['opportunity']['score'],
                             'label': n['opportunity']['label'], 'why': n['opportunity']['why'][:2]})
        rows.sort(key=lambda r: -r['score'])
        return rows[:limit]
    return db.read(build)


# ---------- Go or no-go, and the first year ----------
RULES_KEY = 'decide_rules'
DECISIONS_KEY = 'decisions'
DECISIONS_KEEP = 500


def decide_rules(conn):
    from engine import decide as dc
    return dc.merge_rules(storage.get_state(conn, RULES_KEY))


def decision(db, market, kw, settings, cogs_usd=None, units=300, dims=None, kg=None, freight_mode='sea', now=None):
    """Go or no-go for a keyword in a market (engine.decide) and its first year week by week (engine.yearplan), from
    the newest stored search, the sellers read, Google Trends and your supplier price (US$). Nothing is fetched.
    -> {'decision', 'plan', 'facts'}"""
    from engine import decide as dc
    from engine import launch as lch
    from engine import planner, profit, yearplan
    from engine.trendfeat import MONTH_NAMES
    now = now or time.time()
    kw_norm = config.norm_kw(kw)

    def build(conn):
        rules = decide_rules(conn)
        n = niche_inputs(conn, market, kw_norm, settings, cogs_usd)
        if not n:
            return {'decision': dc.decide({'market': market, 'searched': False}, rules), 'plan': None, 'facts': None}
        m, s, tf = n['metrics'], n['summary'], n['trend'] or {}
        organic = [r for r in s['rows'] if not r['sponsored']]
        econ = None
        if n['price']:
            inp = {'market': market, 'price': n['price'], 'category': '', 'cogs_usd': cogs_usd or 0.0,
                   'freight_mode': freight_mode, 'registered': bool((settings.get('registered') or {}).get(market)),
                   'date': config.market_today(market).isoformat()}
            if dims:
                inp['dims_cm'] = list(dims)
            if kg:
                inp['kg'] = kg
            econ = profit.unit_economics(inp)
        w = _who_sells(conn, market, kw_norm, now)
        read = [x for x in w['listings'] if x.get('ships')]
        season = tf.get('seasonality') or {}
        facts = {'market': market, 'searched': True, 'price': n['price'], 'review_barrier': m.get('review_barrier'),
                 'page1_units': n['bought_mid_sum'] if any(r.get('bought') for r in organic) else None,
                 'margin': econ['Margin'] if econ and cogs_usd else None,
                 'after_amazon': round((econ['Profit'] + econ['Landed']) / econ['NetSales'], 4)
                 if econ and not cogs_usd and econ['NetSales'] else None,
                 'amazon_share': round(sum(1 for x in read if x['ships'] == 'amazon') / len(read), 3) if read else None,
                 'amazon_read': len(read), 'risk_flags': m.get('risk_flags'),
                 'season': {'strength': season.get('strength'), 'reliable': season.get('reliable'),
                            'peak': MONTH_NAMES[int(season['peak_month']) - 1] if season.get('peak_month') else None},
                 'season_read': bool(season) and tf.get('label') not in (None, 'Insufficient'),
                 'fast': m.get('fast_local'), 'trend': tf.get('label'),
                 'score': (n['opportunity'] or {}).get('score'), 'score_label': (n['opportunity'] or {}).get('label'),
                 'searched_at': s.get('checked_at')}
        new = lch.newcomer_units((s.get('per_seller') or {}).get('per_seller'), m.get('review_barrier'), market)
        lead = planner.lead_days(market, freight_mode)
        start = (config.market_today(market) + dt.timedelta(days=lead)).isoformat()
        plan = yearplan.plan(econ, new, units, lead, start, season.get('si') if season.get('reliable') else None)
        return {'decision': dc.decide(facts, rules), 'plan': plan, 'facts': facts}
    return db.read(build)


def record_decision(conn, market, kw_norm, d, now=None):
    """Keep the newest decision per keyword with the rules it was made under. Returns the one before it when the tier
    or the rules changed since (else None)."""
    if d['tier'] == 'unknown':
        return None
    log = storage.get_state(conn, DECISIONS_KEY) or {}
    key = '%s|%s' % (market, kw_norm)
    prev = log.get(key)
    log[key] = {'tier': d['tier'], 'label': d['label'], 'at': int(now or time.time()), 'fingerprint': d['fingerprint']}
    if len(log) > DECISIONS_KEEP:
        for k in sorted(log, key=lambda k: log[k]['at'])[:len(log) - DECISIONS_KEEP]:
            del log[k]
    storage.set_state(conn, DECISIONS_KEY, log)
    if prev and (prev['tier'] != d['tier'] or prev['fingerprint'] != d['fingerprint']):
        return {**prev, 'rules_changed': prev['fingerprint'] != d['fingerprint']}
    return None


# ---------- Global Gap Finder ----------
GAP_SHOW_DAYS = 60            # checks older than this are not shown


def gap_row(conn, r, settings, watched):
    """One gap_check row -> one line of the Gap Finder table (JSON-safe)."""
    from engine import gaps as gp
    from engine import profit
    d, mk = r['data'], r['market']
    g, lst, srch = d.get('gap') or {}, d.get('listing') or {}, d.get('search') or {}
    price = lst.get('price') or srch.get('price_med')
    info = storage.asin_info(conn, mk, r['asin']) or {}
    dims = lst.get('dims_cm') or [info.get('pkg_l_cm'), info.get('pkg_w_cm'), info.get('pkg_h_cm')]
    dims = dims if all(dims or [None]) else None
    inp = {'market': mk, 'price': price, 'category': d.get('category') or info.get('root_name') or '',
           'kg': lst.get('kg') or info.get('pkg_kg'), 'dims_cm': dims,
           'registered': bool((settings.get('registered') or {}).get(mk)), 'date': config.market_today(mk).isoformat()}
    w = watched.get((mk, r['asin']))
    proof = d.get('proof') or {}
    return {'id': r['id'], 'market': mk, 'asin': r['asin'], 'cluster': r['cluster'], 'phrase': r['phrase'],
            'status': r['status'], 'ts': r['ts'], 'type': r['gap_type'], 'label': g.get('label') or gp.LABEL.get(r['gap_type']),
            'means': gp.MEANS.get(r['gap_type']), 'why': g.get('why'), 'conf': g.get('conf'), 'gap': bool(g.get('gap')),
            'order': g.get('order', 99), 'sellers': g.get('sellers'), 'local_rivals': g.get('local_rivals'),
            'same_local': g.get('same_local'), 'exporter': g.get('exporter'), 'flags': d.get('flags') or [],
            'title': d.get('title'), 'brand': d.get('brand'), 'image': d.get('image'), 'items': d.get('items') or [],
            'proof': proof.get('markets') or {}, 'proof_n': proof.get('n') or 0,
            'price': price, 'price_basis': 'listing' if lst.get('price') else 'page 1 median' if srch.get('price_med') else None,
            'currency': ac.MARKETS[mk]['currency'], 'demand_floor': srch.get('bought_total'), 'page1': srch.get('total'),
            'max_cogs_20': profit.max_cogs(inp, 0.20) if price else None, 'size_known': bool(dims),
            'delivery_days': lst.get('days'), 'listing_status': lst.get('status'), 'source': lst.get('source'),
            'search_status': srch.get('status'), 'stopped': d.get('stopped'), 'same': d.get('same') or [],
            'watch_id': w['id'] if w else None}


def gaps_table(db, settings, now=None):
    """The Gap Finder page: the newest check of each proven product in Australia and the UAE (last 60 days), gaps
    first, each with its proof, who sells it there, the price there and the highest product cost that still leaves a
    20% margin; the lone exporters you watch; and the automatic runs."""
    import gapfinder
    now = now or time.time()

    def build(conn):
        watched = {(w['market'], w['target']): w for w in storage.watches(conn, enabled_only=False) if w['kind'] == 'lone'}
        rows = [gap_row(conn, r, settings, watched) for r in storage.gap_checks(conn, now - GAP_SHOW_DAYS * 86400)]
        rows.sort(key=lambda x: (not x['gap'], x['order'], -x['proof_n'], -x['ts']))
        lone = []
        for w in watched.values():
            o = storage.latest_offers(conn, w['market'], w['target'])
            lone.append({'id': w['id'], 'market': w['market'], 'asin': w['target'], 'title': w['display'],
                         'added_at': w['added_at'], 'sellers': max(len(o['offers']), o['n_offers'] or 0) if o else None,
                         'checked_at': o['ts'] if o else None, 'source': o['source'] if o else None})
        tasks = storage._rows(conn.execute("SELECT * FROM task WHERE kind IN ('proof','gaps','lone') AND enabled=1 "
                                           'ORDER BY kind, market'))
        return {'rows': rows, 'lone': lone, 'tasks': tasks, 'proven': len(gapfinder.proven(conn, settings, now))}
    return db.read(build)
