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


# ---------- Phase 2 screens ----------
RADAR_DAYS = 14


def radar(db, market, now=None, limit=60):
    """Amazon surges for one market (Movers & Shakers / New Releases history), grouped into similar products."""
    import file_rank as fr
    from engine import surge
    now = now or time.time()

    def build(conn):
        scans = storage.list_scans(conn, market, now - RADAR_DAYS * 86400)
        if not scans:
            return {'market': market, 'scans': 0, 'items': [], 'clusters': [], 'last_scan': None}
        feats = surge.all_features(scans, days=7)
        info = {}
        for sc in scans:                                           # newest wins
            for it in sc['items']:
                info[it['asin']] = {**it, 'category': sc['category'], 'kind': sc['kind'], 'slug': sc['slug']}
        rows = []
        for a, f in feats.items():
            if not f.get('appearances') or a not in info:
                continue
            it = info[a]
            label = ('Deal-driven' if f.get('deal_driven') else
                     'New on the lists' if (f.get('recent_hits') or 0) >= 2 and not f.get('seen_before_window') else
                     'Rising' if (f.get('streak_days') or 0) >= 3 or f.get('rank_improving') else 'Moving')
            flags = [name for name, rx in fr.CHECKS if rx.search(it.get('title') or '')]
            rows.append({'asin': a, 'title': it.get('title'), 'image': it.get('image'), 'category': it['category'],
                         'price': it.get('price'), 'reviews': it.get('reviews'), 'rating': it.get('rating'),
                         'pct': it.get('pct'), 'rank_now': it.get('rank_now'), 'surge': f.get('surge'), 'label': label,
                         'persistence': f.get('persistence'), 'streak_days': f.get('streak_days'), 'flags': flags,
                         'deal': bool(f.get('deal_driven')), 'search': fr.search_words(it.get('title') or ''),
                         'spark': [r for _, r in surge.rank_history(scans, a)][-14:],
                         'url': '%s/dp/%s' % (ac.MARKETS[market]['site'], a)})
        rows.sort(key=lambda r: -(r['surge'] or 0))
        clusters = []
        for c in surge.clusters([{'asin': r['asin'], 'title': r['title']} for r in rows if not r['deal']]):
            if len(c['asins']) >= 2:
                s = surge.cluster_surge([feats[a].get('surge') for a in c['asins']])
                clusters.append({'key': c['key'], 'asins': c['asins'], 'surge': s, 'size': len(c['asins'])})
        clusters.sort(key=lambda c: -(c['surge'] or 0))
        valid = [s for s in scans if s['status'] in ('ok', 'partial')]
        return {'market': market, 'scans': len(scans), 'valid_scans': len(valid), 'items': rows[:limit],
                'clusters': clusters[:20], 'last_scan': scans[-1]['ts'], 'first_scan': scans[0]['ts']}
    return db.read(build)


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
