"""Amazon list history: which products keep showing up on Movers & Shakers / New Releases, and how fast they climb.

A product that sits on a list day after day, gains sales rank fast and keeps gaining is surging. A product
that only shows up while it is on a deal or after a big price cut is not, so those appearances are left out.
Similar products are grouped into clusters by the words in their titles, and a cluster of surging products
is a stronger signal than one product.

History is a list of list-scan dicts (oldest or newest first, any order):
    {'ts', 'day', 'market', 'kind': 'movers'|'new', 'slug', 'n', 'n_median', 'status',
     'items': [{'asin', 'pos', 'pct', 'rank_now', 'rank_before', 'price', 'reviews', 'title', 'deal'}]}
Pure functions, numpy only. Nothing here raises on missing data.
"""
import math
from datetime import datetime, timedelta, timezone

import numpy as np

import file_rank as fr

from .stats import clip, ols_slope

DAY = 86400.0
LN10 = math.log(10.0)
LN2 = math.log(2.0)

THRESH = {
    'readable': ('ok', 'partial'),  # scan statuses whose items we trust
    'valid_n_share': 0.8,           # a scan counts (absence is evidence) only when n >= 80% of the list median
    'deal_price_ratio': 0.8,        # price below 80% of the last normal price -> deal-driven appearance
    'deal_share': 0.5,              # deal_driven when at least half of the window appearances are deal-driven
    'w_persist': 0.4,               # surge weights: persistence, rank gain, rank slope
    'w_gain': 0.3,
    'w_slope': 0.3,
    'gain_full': LN10,              # mean ln(rank_before/rank_now) of ln 10 (10x better in a day) scores full marks
    'slope_full': LN2 / 7.0,        # rank halving every week (ln rank falls ln 2 per 7 days) scores full marks
    'recent_scans': 3,              # 'recent_hits' looks at the last 3 valid scans
    'improving_n': 3,               # 'rank_improving' needs the last 3 rank readings to keep getting better
    'cluster_jaccard': 0.6,         # default title-word similarity to merge two products into one cluster
}


# ---- small helpers ----

def _finite(x):
    """x as a float, or None for None / NaN / inf / text / bools."""
    if x is None or isinstance(x, bool):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _rank(x):
    """A sales rank as a positive float, or None."""
    f = _finite(x)
    return f if f is not None and f > 0 else None


def _asin(x):
    return '' if x is None else str(x).strip().upper()


def _status(scan):
    return str(scan.get('status') or '').strip().lower()


def _day(scan, ts):
    """The scan's day 'YYYY-MM-DD' (as stored, else the UTC date of ts)."""
    d = scan.get('day')
    if isinstance(d, str) and len(d) >= 10:
        return d[:10]
    try:                                                # timedelta: negative ts is fine on Windows too
        return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=ts)).strftime('%Y-%m-%d')
    except (OverflowError, ValueError):
        return ''


def _as_list(x):
    """Lists and tuples as they are, other iterables (not text or dicts) as a list, anything else empty."""
    if isinstance(x, (list, tuple)):
        return x
    if x is None or isinstance(x, (str, bytes, dict)):
        return []
    try:
        return list(x)
    except TypeError:
        return []


def _list_key(scan):
    """One list = market + kind + category slug."""
    return (str(scan.get('market') or ''), str(scan.get('kind') or ''), str(scan.get('slug') or ''))


def _round(x, nd):
    return None if x is None else round(float(x), nd)


# ---- reading the history once ----

def _scans(history):
    """Clean scan records sorted by ts: [{'i', 'ts', 'day', 'key', 'slug', 'readable', 'valid', 'items'}].

    A scan is valid when its status is ok/partial and it read at least 80% of the list's usual length
    (n_median; when that is missing, the median n of the same list in this history).
    """
    raw = []
    for i, s in enumerate(_as_list(history)):
        if not isinstance(s, dict):
            continue
        ts = _finite(s.get('ts'))
        if ts is None:
            continue
        items = _as_list(s.get('items'))
        n = _finite(s.get('n'))
        raw.append({'i': i, 'ts': ts, 'scan': s, 'items': items, 'n': len(items) if n is None else n})
    # fallback list medians from the history itself
    by_list = {}
    for r in raw:
        if _status(r['scan']) in THRESH['readable'] and r['n'] > 0:
            by_list.setdefault(_list_key(r['scan']), []).append(r['n'])
    med = {k: float(np.median(v)) for k, v in by_list.items()}
    out = []
    for r in raw:
        s = r['scan']
        key = _list_key(s)
        readable = _status(s) in THRESH['readable']
        n_med = _finite(s.get('n_median'))
        if n_med is None or n_med <= 0:
            n_med = med.get(key)
        valid = readable and (n_med is None or r['n'] >= THRESH['valid_n_share'] * n_med)
        out.append({'i': r['i'], 'ts': r['ts'], 'day': _day(s, r['ts']), 'key': key, 'slug': (key[0], key[2]),
                    'readable': readable, 'valid': valid, 'items': r['items']})
    out.sort(key=lambda r: (r['ts'], r['i']))
    return out


def _appearances(scans):
    """{asin: [appearance, ...]} in time order, one per scan, from readable scans only.

    Each appearance is {'scan', 'ts', 'rank_now', 'rank_before', 'price', 'deal_flag', 'deal_driven', 'title'}.
    deal_driven: a deal badge, or a price below 80% of the last normal (not deal-driven) price seen before.
    """
    apps = {}
    for sc in scans:
        if not sc['readable']:
            continue
        seen = set()
        for it in sc['items']:
            if not isinstance(it, dict):
                continue
            a = _asin(it.get('asin'))
            if not a or a in seen:                       # count an ASIN once per scan
                continue
            seen.add(a)
            apps.setdefault(a, []).append({
                'scan': sc, 'ts': sc['ts'], 'rank_now': _rank(it.get('rank_now')),
                'rank_before': _rank(it.get('rank_before')), 'price': _finite(it.get('price')),
                'deal_flag': bool(it.get('deal')), 'title': str(it.get('title') or '')})
    for lst in apps.values():
        last_normal = last_any = None                   # prices seen at earlier ts
        i = 0
        while i < len(lst):
            j = i                                       # appearances at the same ts compare with earlier ones
            while j < len(lst) and lst[j]['ts'] == lst[i]['ts']:
                j += 1
            ref = last_normal if last_normal is not None else last_any
            for a in lst[i:j]:
                p = a['price']
                cut = p is not None and ref is not None and ref > 0 and p < THRESH['deal_price_ratio'] * ref
                a['deal_driven'] = a['deal_flag'] or cut
            for a in lst[i:j]:
                if a['price'] is not None and a['price'] > 0:
                    last_any = a['price']
                    if not a['deal_driven']:
                        last_normal = a['price']
            i = j
    return apps


def _main_slug(apps):
    """The (market, slug) with the most rank readings: ranks from different categories don't mix."""
    count, last = {}, {}
    for a in apps:
        if a['rank_now'] is not None:
            k = a['scan']['slug']
            count[k] = count.get(k, 0) + 1
            last[k] = max(last.get(k, a['ts']), a['ts'])
    if not count:
        return None
    return max(count, key=lambda k: (count[k], last[k]))


def _empty_features():
    return {'appearances': 0, 'valid_scans': 0, 'persistence': None, 'first_seen_ts': None, 'streak_days': 0,
            'g': None, 'bsr_slope': None, 'deal_driven': False, 'surge': 0.0,
            'deal_hits': 0, 'recent_hits': 0, 'seen_before_window': False, 'rank_improving': False,
            'last_rank': None, 'title': ''}


def surge_score(p, g, s):
    """AS = 100*[.4*clip(p) + .3*clip(g/ln10) + .3*clip(-7*s/ln2)]; missing parts add nothing."""
    parts = 0.0
    if p is not None:
        parts += THRESH['w_persist'] * clip(p)
    if g is not None:
        parts += THRESH['w_gain'] * clip(g / THRESH['gain_full'])
    if s is not None:
        parts += THRESH['w_slope'] * clip(-s / THRESH['slope_full'])
    return round(100.0 * parts, 1)


def _by_list(scans):
    """{list key: [scan, ...]} in time order."""
    out = {}
    for sc in scans:
        out.setdefault(sc['key'], []).append(sc)
    return out


def _features(apps, scans, days, by_list=None):
    """Features for one ASIN from the pre-read scans and its appearances."""
    out = _empty_features()
    if not scans or not apps:
        return out
    by_list = by_list if by_list is not None else _by_list(scans)
    days = _finite(days)
    days = days if days is not None and days > 0 else 7.0
    now = scans[-1]['ts']
    start = now - days * DAY
    lists = {a['scan']['key'] for a in apps}            # only the lists this product has been on
    mine = sorted((sc for k in lists for sc in by_list.get(k, ())), key=lambda sc: (sc['ts'], sc['i']))
    valid_win = [sc for sc in mine if sc['valid'] and sc['ts'] > start]
    win = [a for a in apps if a['ts'] > start]
    win_valid = [a for a in win if a['scan']['valid']]
    organic_valid = [a for a in win_valid if not a['deal_driven']]

    out['appearances'] = len(win_valid)
    out['deal_hits'] = len(win_valid) - len(organic_valid)
    out['valid_scans'] = len(valid_win)
    out['persistence'] = _round(clip(len(organic_valid) / len(valid_win)), 3) if valid_win else None
    out['first_seen_ts'] = int(apps[0]['ts'])
    out['seen_before_window'] = apps[0]['ts'] <= start
    out['deal_driven'] = bool(win) and sum(a['deal_driven'] for a in win) >= THRESH['deal_share'] * len(win)
    out['title'] = next((a['title'] for a in reversed(apps) if a['title']), '')

    # rank gain on the day of each normal appearance: mean ln(rank_before / rank_now)
    organic = [a for a in win if not a['deal_driven']]
    gains = [math.log(a['rank_before'] / a['rank_now']) for a in organic
             if a['rank_now'] is not None and a['rank_before'] is not None]
    out['g'] = _round(float(np.mean(gains)), 4) if gains else None

    # slope of ln(rank_now) per day, within one category so ranks are comparable
    main = _main_slug(organic)
    by_ts = {}                                          # one reading per timestamp
    for a in organic:
        if a['rank_now'] is not None and a['scan']['slug'] == main:
            by_ts[a['ts']] = math.log(a['rank_now'])
    pts = sorted(by_ts.items())
    if pts:
        out['bsr_slope'] = _round(ols_slope([y for _, y in pts], [x / DAY for x, _ in pts]), 5)
    k = THRESH['improving_n']
    lr = [y for _, y in pts][-k:]
    out['rank_improving'] = len(lr) >= k and all(b < a for a, b in zip(lr, lr[1:]))
    main_all = _main_slug(apps)
    ranks = [a['rank_now'] for a in apps if a['rank_now'] is not None and a['scan']['slug'] == main_all]
    out['last_rank'] = int(ranks[-1]) if ranks else None

    # current streak: consecutive scan days (newest first) with a normal appearance on a valid scan.
    # Days without any valid scan neither add to nor break the streak.
    hit_day = {}
    for sc in mine:
        if sc['valid']:
            hit_day.setdefault(sc['day'], False)
    for a in apps:
        if a['scan']['valid'] and not a['deal_driven']:
            hit_day[a['scan']['day']] = True
    streak = 0
    for d in sorted(hit_day, reverse=True):
        if not hit_day[d]:
            break
        streak += 1
    out['streak_days'] = streak

    # appearances on the last few valid scans (for 'new mover' alerts)
    recent = {sc['i'] for sc in [sc for sc in mine if sc['valid']][-THRESH['recent_scans']:]}
    out['recent_hits'] = sum(1 for a in apps if a['scan']['i'] in recent)

    out['surge'] = surge_score(out['persistence'], out['g'], out['bsr_slope'])
    return out


# ---- public API ----

def asin_features(history, asin, days=7):
    """List-history features for one ASIN over the last `days` days of scans (ending at the newest scan).

    Returns {'appearances', 'valid_scans', 'persistence', 'first_seen_ts', 'streak_days', 'g', 'bsr_slope',
    'deal_driven', 'surge'} plus a few extras ('deal_hits', 'recent_hits', 'seen_before_window',
    'rank_improving', 'last_rank', 'title').
      appearances  - scans in the window, valid ones only, that list the product (deal-driven ones included)
      valid_scans  - valid scans in the window of the lists the product has ever been on
      persistence  - normal (not deal-driven) appearances / valid_scans, None without valid scans
      g            - mean ln(rank_before/rank_now) over normal appearances (the day-on-day rank gain)
      bsr_slope    - OLS slope of ln(rank_now) per day (negative = climbing), None under 3 readings
      deal_driven  - at least half of the window appearances came with a deal badge or a 20%+ price cut
      surge        - 0..100, 100*[.4 p + .3 clip(g/ln10) + .3 clip(-7 s/ln2)]
    """
    a = _asin(asin)
    if not a:
        return _empty_features()
    scans = _scans(history)
    return _features(_appearances(scans).get(a, []), scans, days)


def all_features(history, days=7):
    """{asin: asin_features(...)} for every ASIN in the history, reading the history only once."""
    scans = _scans(history)
    by_list = _by_list(scans)
    return {a: _features(lst, scans, days, by_list) for a, lst in _appearances(scans).items()}


def rank_history(history, asin):
    """[(ts, rank_now), ...] oldest first, for a sparkline.

    Only readable scans, and only the category the product has the most readings in (ranks from
    different categories are not comparable). One reading per timestamp.
    """
    a = _asin(asin)
    if not a:
        return []
    apps = _appearances(_scans(history)).get(a, [])
    main = _main_slug(apps)
    if main is None:
        return []
    by_ts = {}
    for x in apps:
        if x['rank_now'] is not None and x['scan']['slug'] == main:
            by_ts[int(x['ts'])] = int(x['rank_now'])
    return sorted(by_ts.items())


def _tokens(title):
    return set(fr.search_words(title or '').lower().split()) if str(title or '').strip() else set()


def clusters(items_with_titles, threshold=0.6):
    """Group similar products: union-find on the Jaccard similarity of their search-word sets.

    items_with_titles: [{'asin', 'title', ...}]. Tokens = set(file_rank.search_words(title).lower().split()).
    Two products join when |A & B| / |A | B| >= threshold. Every product lands in exactly one cluster
    (singletons too). Returns [{'key', 'asins', 'tokens'}], biggest first. 'key' is the words shared by at
    least half the members, in alphabetical order (stable as members come and go); 'tokens' is every word.
    """
    t = _finite(threshold)
    t = THRESH['cluster_jaccard'] if t is None else min(1.0, max(1e-6, t))
    toks, order = {}, []
    for it in _as_list(items_with_titles):
        if not isinstance(it, dict):
            continue
        a = _asin(it.get('asin'))
        if not a:
            continue
        if a not in toks:
            toks[a] = set()
            order.append(a)
        toks[a] |= _tokens(it.get('title'))             # the same ASIN twice -> one product
    if not order:
        return []

    parent = list(range(len(order)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # Prefix filter: order words rarest first; two sets with Jaccard >= t must share a word among the
    # first |A| - ceil(t|A|) + 1 words of each, so only those pairs are compared.
    freq = {}
    for a in order:
        for w in toks[a]:
            freq[w] = freq.get(w, 0) + 1
    index = {}
    for i, a in enumerate(order):
        s = toks[a]
        if not s:
            continue
        words = sorted(s, key=lambda w: (freq[w], w))
        plen = len(words) - math.ceil(t * len(words) - 1e-9) + 1
        cands = set()
        for w in words[:max(1, plen)]:
            cands.update(index.get(w, ()))
            index.setdefault(w, []).append(i)
        for j in cands:
            if find(i) == find(j):
                continue
            o = toks[order[j]]
            inter = len(s & o)
            if inter and inter >= t * len(s | o) - 1e-12:
                parent[find(i)] = find(j)

    groups = {}
    for i, a in enumerate(order):
        groups.setdefault(find(i), []).append(a)
    out = []
    for members in groups.values():
        count = {}
        for a in members:
            for w in toks[a]:
                count[w] = count.get(w, 0) + 1
        core = sorted(w for w, c in count.items() if c >= len(members) / 2.0)
        if not core and count:                          # a loose chain: fall back to the commonest words
            top = max(count.values())
            core = sorted(w for w, c in count.items() if c == top)
        key = ' '.join(core) or members[0]
        out.append({'key': key, 'asins': members, 'tokens': sorted(count)})
    out.sort(key=lambda c: (-len(c['asins']), c['key']))
    return out


def cluster_surge(member_surges):
    """Noisy-or of member surge scores: 100*(1 - prod(1 - s/100)). Missing values are skipped; 0..100."""
    q = 1.0
    for s in _as_list(member_surges):
        f = _finite(s)
        if f is not None:
            q *= 1.0 - clip(f, 0.0, 100.0) / 100.0
    return round(100.0 * (1.0 - q), 1)
