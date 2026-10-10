"""Report payloads for the window, built from market.db plus the analysis engine. Read-only.

Every estimated number carries its range and basis ({'v', 'lo', 'hi', 'basis', 'conf'}), and every series keeps
its gaps: a blocked or failed search is listed under `gaps`, never drawn as zero.
"""
import math
import time

import amazon_check as ac
import config
import storage

H10_ERROR = 0.25          # Helium 10 estimates are typically 15-30% off: shown as a +-25% range
BACKCAST_MONTHS = 24
FORECAST_MONTHS = 6
MIN_TRENDS_QUALITY = 0.5


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
                                              'fast_days', 'local_days')} if now_s else None,
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
