"""Alert rules: turn fresh snapshots and features into short "what changed" messages.

Every rule is a pure function that returns a list of alert dicts:
    {'rule', 'severity': 1|2|3, 'market', 'target', 'title', 'body', 'data': {...}, 'dedupe_key', 'ts'}
severity 3 = high, 2 = medium, 1 = low. dedupe_key = 'rule|market|target|YYYY-Www' (the ISO week of the
alert ts, week zero-padded), so the same news is raised at most once a week; dedupe() keeps the first.

Market rules only read valid rows (status ok / empty / partial). A blocked, captcha, wrong_location,
geo_redirect, error, timeout or offline fetch is a gap, never a zero, so it can neither trigger an alert
nor count as the "before" value of one.
Pure functions, numpy only. Nothing here raises on missing data, and every output is JSON-safe (no NaN).
"""
import math
from datetime import date, datetime, timedelta, timezone

import numpy as np

import file_rank as fr

from .stats import robust_z

DAY = 86400.0
VALID = ('ok', 'empty', 'partial')                     # the only row statuses rules may read
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
CURRENCY = {m: t['currency'] for m, t in fr.TARGETS.items()}

THRESH = {
    # all market rules
    'fresh_days': 3,               # the newest valid row must be at most this old, else the data is stale
    'future_slack_s': 300,         # rows stamped later than now_ts + this are ignored
    # demand_up / demand_down
    'demand_z': 3.0,               # robust z of bought_mid_sum ...
    'demand_change': 0.25,         # ... and a change of at least 25% vs the baseline median
    'demand_base_n': 8,            # baseline = up to 8 earlier valid snapshots
    'demand_min_n': 4,             # and at least 4 of them
    'z_floor_share': 0.05,         # a flat baseline (MAD 0) uses 5% of its median as the spread
    # gap_open / gap_closing
    'gap_fast_max': 4,             # at most 4 fast local sellers on page 1 = a gap
    'gap_min_organic': 1,          # and at least one organic result (an empty page is not a gap)
    'closing_fast_up': 3,          # fast local sellers up by 3+ vs about 30 days earlier
    'lookback_days': 30,
    'lookback_slack_days': 3,      # the "30 days ago" row may be up to 33 days old
    # competition_arriving
    'low_review_up': 5,            # low-review listings up by 5+ vs the median of the last 7 days
    'low_review_days': 7,
    # price_war
    'price_drop': 0.15,            # median price down 15%+ vs about 30 days earlier
    # list rules
    'list_window_days': 7,
    'mover_hits': 2,               # on the list in 2+ of the last 3 valid scans
    'riser_streak_days': 3,
    'cluster_min_size': 3,         # 3+ products of one cluster on lists within 7 days
    'cluster_surge_min': 60,
    'list_max_per_rule': 25,       # keep the 25 strongest per rule, so a busy list can't flood the feed
    # trend rules
    'confirm_labels': ('Emerging', 'Breakout', 'Growing'),
    'label_change_skip': ('Insufficient',),   # going into or out of "not enough data" is not news
    'breakout_pct': 5000,          # Google shows "Breakout" for a rise above 5000%
    # ops rules
    'paused_min_s': 600,           # cooldowns shorter than 10 min (e.g. offline blips) are not worth an alert
    'paused_long_s': 6 * 3600,     # a pause of 6 h or more is high severity
    'stale_cadence_mult': 3,       # stale = not refreshed within 3x its cadence
    'drift_list_share': 0.6,       # list pages under 60% of their median item count
    'drift_rank_missing': 0.8,     # sales rank missing on more than 80% of product pages
    'drift_min_pages': 3,          # ... out of at least 3 pages (when the page count is known)
    'fees_max_age_days': 90,
    'xray_max_age_days': 30,
    # order_by
    'order_by_days': 14,
}

SEVERITY = {
    'demand_up': 2, 'demand_down': 2, 'gap_open': 3, 'gap_closing': 2, 'competition_arriving': 2, 'price_war': 2,
    'new_mover': 2, 'persistent_riser': 2, 'cluster_surge': 3,
    'trend_label_change': 2, 'trends_breakout': 2, 'confirmed_trend': 3,
    'captcha_needed': 3, 'source_paused': 2, 'stale_data': 2, 'layout_drift': 3, 'fees_stale': 2, 'rerun_xray': 1,
    'order_by': 3,
}
RULES = tuple(SEVERITY)


# ---- small helpers ----

def _finite(x):
    """x as a float, or None for None / NaN / inf / text / bools."""
    if x is None or isinstance(x, (bool, np.bool_)):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


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


def _items(x, key):
    """A {name: dict} mapping or a [dict with `key`] list, as [(name, dict)]."""
    if isinstance(x, dict):
        return [(str(k), v) for k, v in x.items() if isinstance(v, dict)]
    return [(str(v.get(key) or ''), v) for v in _as_list(x) if isinstance(v, dict)]


def _market(m):
    return str(m or '').strip().upper()


def _target(t):
    return '' if t is None else str(t).strip()


def _asin(x):
    return str(x).strip().upper() if x not in (None, '') else ''


def _status(row):
    return str(row.get('status') or '').strip().lower()


def _valid(row):
    return isinstance(row, dict) and _status(row) in VALID


def _utc(ts):
    """Unix seconds -> aware UTC datetime, or None."""
    f = _finite(ts)
    if f is None:
        return None
    try:                                                # timedelta: negative ts is fine on Windows too
        return _EPOCH + timedelta(seconds=f)
    except (OverflowError, ValueError):
        return None


def _date(x):
    """A date from 'YYYY-MM-DD' (or 'YYYY-MM' = the 1st), a date/datetime, or unix seconds; else None."""
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    if isinstance(x, str):
        s = x.strip()
        try:
            return date.fromisoformat(s[:10]) if len(s) >= 10 else date.fromisoformat(s[:7] + '-01')
        except ValueError:
            return None
    dt = _utc(x)
    return dt.date() if dt is not None else None


def _clean(x):
    """JSON-safe copy: numpy scalars to Python, NaN/inf to None, tuples/sets to lists, floats rounded."""
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (set, frozenset)):
        return [_clean(v) for v in sorted(x, key=str)]
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        f = float(x)
        return round(f, 4) if math.isfinite(f) else None
    if x is None or isinstance(x, str):
        return x
    if isinstance(x, (date, datetime)):
        return x.isoformat()
    return str(x)


def _n(x):
    """1234.4 -> '1,234'."""
    return '{:,}'.format(int(round(x)))


def _pct(x):
    """0.254 -> '+25%'."""
    return '{:+.0f}%'.format(100 * x)


def _money(market, x):
    cur = CURRENCY.get(market, '')
    s = '{:,.2f}'.format(x)
    if not cur:
        return s
    return cur + s if cur.endswith('$') else cur + ' ' + s


def _dur(seconds):
    """3700 -> '1 h 2 min'."""
    m = max(0, int(round(seconds / 60.0)))
    if m < 60:
        return '%d min' % m
    h, m = divmod(m, 60)
    if h < 48:
        return '%d h %d min' % (h, m) if m else '%d h' % h
    return '%d days' % round(h / 24.0)


def _short(text, n=60):
    t = ' '.join(str(text or '').split())
    return t if len(t) <= n else t[:n - 1].rstrip() + '…'


def _day_str(d):
    return '%d %s %d' % (d.day, MONTHS[d.month - 1], d.year)


def _month_name(m):
    f = _finite(m)
    return MONTHS[int(f) - 1] if f is not None and 1 <= f <= 12 else None


# ---- alert dicts and keys ----

def week_key(ts):
    """ISO week of a unix ts as 'YYYY-Www' (ISO year, zero-padded week), e.g. '2026-W41'; None if ts is bad."""
    dt = _utc(ts)
    if dt is None:
        return None
    y, w, _ = dt.isocalendar()
    return '%d-W%02d' % (y, w)


def dedupe_key(rule, market, target, ts):
    """'rule|market|target|YYYY-Www': one alert per rule, market and target each ISO week."""
    return '%s|%s|%s|%s' % (rule, _market(market), _target(target), week_key(ts) or '')


def make_alert(rule, market, target, title, body, data, ts, severity=None):
    """One alert dict. Severity defaults to SEVERITY[rule]; data is made JSON-safe."""
    sev = severity if severity in (1, 2, 3) else SEVERITY.get(rule, 2)
    f = _finite(ts)
    return {'rule': rule, 'severity': int(sev), 'market': _market(market), 'target': _target(target),
            'title': str(title), 'body': str(body), 'data': _clean(data or {}),
            'dedupe_key': dedupe_key(rule, market, target, ts), 'ts': int(f) if f is not None else None}


def dedupe(alerts):
    """Keep the first alert per dedupe_key, in order. Alerts without a (text) key are kept as they are."""
    seen, out = set(), []
    for a in _as_list(alerts):
        if not isinstance(a, dict):
            continue
        k = a.get('dedupe_key')
        if isinstance(k, str) and k:
            if k in seen:
                continue
            seen.add(k)
        out.append(a)
    return out


# ---- keyword (search page) rules ----

def _rows(snaps, now):
    """[(ts, row)] of the valid rows, oldest first; rows stamped after now are left out."""
    out = []
    for i, s in enumerate(_as_list(snaps)):
        if not _valid(s):
            continue
        ts = _finite(s.get('ts'))
        if ts is None or (now is not None and ts > now + THRESH['future_slack_s']):
            continue
        out.append((ts, i, s))
    out.sort(key=lambda r: (r[0], r[1]))
    return [(ts, s) for ts, _, s in out]


def _series(rows, key):
    """[(ts, value)] for rows where `key` is a number."""
    out = []
    for ts, s in rows:
        v = _finite(s.get(key))
        if v is not None:
            out.append((ts, v))
    return out


def _lookback(prev, cur_ts, key, days):
    """(ts, value) of the earlier row closest to `days` before cur_ts, at most days + slack old.

    With a shorter history this is simply the oldest earlier row, so "change in 30 days" still works.
    """
    aim = cur_ts - days * DAY
    lo = cur_ts - (days + THRESH['lookback_slack_days']) * DAY
    cands = [(ts, v) for ts, v in _series(prev, key) if lo <= ts < cur_ts]
    if not cands:
        return None
    return min(cands, key=lambda tv: (abs(tv[0] - aim), tv[0]))


def _z(x, hist):
    """Robust z of x vs hist; a flat history (MAD 0) uses z_floor_share of its median as the spread."""
    z = robust_z(x, hist)
    if z is not None:
        return float(z)
    if len(hist) < THRESH['demand_min_n']:
        return None
    med = float(np.median(hist))
    scale = THRESH['z_floor_share'] * abs(med)
    return (x - med) / scale if scale > 0 else None


def _demand(mk, kw, cur_ts, cur, prev, now):
    x = _finite(cur.get('bought_mid_sum'))
    hist = [v for _, v in _series(prev, 'bought_mid_sum')][-THRESH['demand_base_n']:]
    if x is None or len(hist) < THRESH['demand_min_n']:
        return []
    med = float(np.median(hist))
    if med <= 0:
        return []
    z = _z(x, hist)
    if z is None:
        return []
    change = x / med - 1.0
    if z >= THRESH['demand_z'] and change >= THRESH['demand_change']:
        rule, word = 'demand_up', 'up'
    elif z <= -THRESH['demand_z'] and change <= -THRESH['demand_change']:
        rule, word = 'demand_down', 'down'
    else:
        return []
    return [make_alert(
        rule, mk, kw, 'Demand %s: %s (%s)' % (word, kw, mk),
        'Bought in past month across page 1 is about %s units, vs a usual %s (%s, z %.1f over %d earlier checks).'
        % (_n(x), _n(med), _pct(change), z, len(hist)),
        {'kw': kw, 'bought_mid_sum': x, 'baseline_median': med, 'change': change, 'z': z, 'n_base': len(hist),
         'snap_ts': cur_ts}, now)]


def _gap_open(mk, kw, cur_ts, cur, prev, now):
    f = _finite(cur.get('fast'))
    if f is None or f > THRESH['gap_fast_max'] or _status(cur) == 'empty':
        return []
    org = _finite(cur.get('organic'))
    if org is not None and org < THRESH['gap_min_organic']:
        return []
    before = _series(prev, 'fast')
    was = before[-1][1] if before else None
    if was is not None and was <= THRESH['gap_fast_max']:
        return []                                       # it was already open
    since = 'first check' if was is None else 'was %s' % _n(was)
    return [make_alert(
        'gap_open', mk, kw, 'Gap open: %s (%s)' % (kw, mk),
        'Only %s fast local sellers on page 1 (%s).' % (_n(f), since),
        {'kw': kw, 'fast': f, 'fast_before': was, 'first': was is None, 'local': _finite(cur.get('local')),
         'overseas': _finite(cur.get('overseas')), 'organic': org, 'snap_ts': cur_ts}, now)]


def _gap_closing(mk, kw, cur_ts, cur, prev, now):
    f = _finite(cur.get('fast'))
    ref = _lookback(prev, cur_ts, 'fast', THRESH['lookback_days'])
    if f is None or ref is None or f - ref[1] < THRESH['closing_fast_up']:
        return []
    days = (cur_ts - ref[0]) / DAY
    return [make_alert(
        'gap_closing', mk, kw, 'Gap closing: %s (%s)' % (kw, mk),
        'Fast local sellers on page 1 up from %s to %s in %d days.' % (_n(ref[1]), _n(f), round(days)),
        {'kw': kw, 'fast': f, 'fast_before': ref[1], 'days': days, 'ref_ts': ref[0], 'snap_ts': cur_ts}, now)]


def _competition(mk, kw, cur_ts, cur, prev, now):
    n = _finite(cur.get('low_review_n'))
    lo = cur_ts - THRESH['low_review_days'] * DAY
    week = [v for ts, v in _series(prev, 'low_review_n') if lo <= ts < cur_ts]
    if n is None or not week:
        return []
    med = float(np.median(week))
    if n - med < THRESH['low_review_up']:
        return []
    return [make_alert(
        'competition_arriving', mk, kw, 'Competition arriving: %s (%s)' % (kw, mk),
        '%s low-review listings on page 1, up from a usual %s over the last %d days.'
        % (_n(n), ('%.1f' % med).rstrip('0').rstrip('.'), THRESH['low_review_days']),
        {'kw': kw, 'low_review_n': n, 'median_7d': med, 'n_week': len(week), 'snap_ts': cur_ts}, now)]


def _price_war(mk, kw, cur_ts, cur, prev, now):
    p = _finite(cur.get('price_med'))
    ref = _lookback(prev, cur_ts, 'price_med', THRESH['lookback_days'])
    if p is None or p <= 0 or ref is None or ref[1] <= 0:
        return []
    change = p / ref[1] - 1.0
    if change > -THRESH['price_drop']:
        return []
    days = (cur_ts - ref[0]) / DAY
    return [make_alert(
        'price_war', mk, kw, 'Price war: %s (%s)' % (kw, mk),
        'Median price on page 1 is %s, down %d%% from %s %d days ago.'
        % (_money(mk, p), round(-100 * change), _money(mk, ref[1]), round(days)),
        {'kw': kw, 'price_med': p, 'price_before': ref[1], 'change': change, 'days': days,
         'currency': CURRENCY.get(mk), 'ref_ts': ref[0], 'snap_ts': cur_ts}, now)]


def serp_rules(market, kw, snaps, now_ts):
    """Alerts for one keyword in one market from its search-page snapshots (oldest to newest).

    snaps: [{'ts', 'status', 'fast', 'local', 'overseas', 'organic', 'badged', 'bought_mid_sum', 'price_med',
    'low_review_n'}]. Only valid rows count, the newest valid row is "now" and must be at most fresh_days old.
      demand_up / demand_down  robust z of bought_mid_sum >= 3 (<= -3) vs the previous <= 8 valid rows
                               (4 needed), and a change of 25%+ vs their median
      gap_open                 fast <= 4 now, and it was above 4 on the previous valid row (or this is the first)
      gap_closing              fast up 3+ vs about 30 days earlier
      competition_arriving     low_review_n up 5+ vs the median of the previous 7 days
      price_war                price_med down 15%+ vs about 30 days earlier
    """
    now = _finite(now_ts)
    rows = _rows(snaps, now)
    if not rows:
        return []
    cur_ts, cur = rows[-1]
    if now is None:
        now = cur_ts
    if now - cur_ts > THRESH['fresh_days'] * DAY:
        return []                                       # no fresh valid data: say nothing
    mk, tg, prev = _market(market), _target(kw), rows[:-1]
    out = []
    for rule in (_demand, _gap_open, _gap_closing, _competition, _price_war):
        out += rule(mk, tg, cur_ts, cur, prev, now)
    return out


# ---- Amazon list rules ----

def _feat_map(asin_feats):
    """{ASIN: features} from a dict of features or a list of feature dicts carrying 'asin'."""
    out = {}
    for a, f in _items(asin_feats, 'asin'):
        a = _asin(a)
        if a and a not in out:
            out[a] = f
    return out


def _noisy_or(values):
    q = 1.0
    for s in values:
        f = _finite(s)
        if f is not None:
            q *= 1.0 - min(100.0, max(0.0, f)) / 100.0
    return 100.0 * (1.0 - q)


def _name(asin, f):
    t = _short(f.get('title'), 50)
    return t or asin


def list_rules(market, asin_feats, cluster_infos, now_ts):
    """Alerts from Amazon list history (Movers & Shakers / New Releases) for one market.

    asin_feats: {asin: engine.surge.asin_features(...)} (or a list of those dicts with 'asin'); they already
    count valid scans only. cluster_infos: [{'key', 'asins', 'surge' (optional), 'n_recent' (optional)}].
      new_mover         recent_hits >= 2 (in the last 3 valid scans) and not seen before the 7-day window;
                        low severity when the appearances were deal-driven
      persistent_riser  streak_days >= 3 or rank_improving (3 readings), not deal-driven
      cluster_surge     3+ members on a list within 7 days (appearances >= 1) and cluster surge >= 60
                        (given, else the noisy-or of the recent members' surge scores)
    At most list_max_per_rule alerts per rule are kept, the strongest surge first.
    """
    now = _finite(now_ts)
    if now is None:
        return []
    mk = _market(market)
    feats = _feat_map(asin_feats)
    movers, risers = [], []
    for a, f in feats.items():
        apps = _finite(f.get('appearances'))
        if apps is not None and apps < 1:
            continue                                    # not on any valid scan this week
        surge = _finite(f.get('surge')) or 0.0
        deal = bool(f.get('deal_driven'))
        name = _name(a, f)

        hits = _finite(f.get('recent_hits')) or 0
        before = f.get('seen_before_window')
        if before is None:
            first = _finite(f.get('first_seen_ts'))
            before = first is not None and first <= now - THRESH['list_window_days'] * DAY
        if hits >= THRESH['mover_hits'] and not before:
            body = '%s showed up in %d of the last 3 list scans and was not seen before this week.' % (a, hits)
            if deal:
                body += ' Mostly while on a deal or price cut.'
            movers.append((surge, a, make_alert(
                'new_mover', mk, a, 'New mover: %s (%s)' % (name, mk), body,
                {'asin': a, 'title': f.get('title') or '', 'recent_hits': hits, 'surge': surge, 'deal_driven': deal,
                 'first_seen_ts': _finite(f.get('first_seen_ts')), 'last_rank': _finite(f.get('last_rank'))},
                now, severity=1 if deal else None)))

        streak = _finite(f.get('streak_days')) or 0
        improving = bool(f.get('rank_improving'))
        if (streak >= THRESH['riser_streak_days'] or improving) and not deal:
            why = []
            if streak >= THRESH['riser_streak_days']:
                why.append('on a list %d days in a row' % streak)
            if improving:
                why.append('sales rank better on each of the last 3 readings')
            rank = _finite(f.get('last_rank'))
            body = '%s: %s' % (a, ', '.join(why)) + (' (now #%s).' % _n(rank) if rank else '.')
            risers.append((surge, a, make_alert(
                'persistent_riser', mk, a, 'Persistent riser: %s (%s)' % (name, mk), body,
                {'asin': a, 'title': f.get('title') or '', 'streak_days': streak, 'rank_improving': improving,
                 'surge': surge, 'last_rank': rank, 'bsr_slope': _finite(f.get('bsr_slope'))}, now)))

    out = []
    cap = int(THRESH['list_max_per_rule'])
    for group in (movers, risers):
        group.sort(key=lambda r: (-r[0], r[1]))
        out += [al for _, _, al in group[:cap]]

    clusters = []
    for info in _as_list(cluster_infos):
        if not isinstance(info, dict):
            continue
        asins = list(dict.fromkeys(a for a in (_asin(x) for x in _as_list(info.get('asins'))) if a))
        recent = [a for a in asins if a in feats and (_finite(feats[a].get('appearances')) or 0) >= 1]
        n_given = _finite(info.get('n_recent'))
        n = int(n_given) if n_given is not None else len(recent)
        cs = _finite(info.get('surge'))
        if cs is None:
            cs = _finite(info.get('cluster_surge'))
        if cs is None:
            cs = _noisy_or(_finite(feats[a].get('surge')) for a in recent)
        if n < THRESH['cluster_min_size'] or cs < THRESH['cluster_surge_min']:
            continue
        toks = info.get('tokens')
        toks = sorted(toks, key=str) if isinstance(toks, (set, frozenset)) else _as_list(toks)
        key = _target(info.get('key')) or ' '.join(str(t) for t in toks) or (asins[0] if asins else '')
        if not key:
            continue                                    # nothing to name or dedupe it by
        clusters.append((cs, key, make_alert(
            'cluster_surge', mk, key, 'Cluster surging: %s (%s)' % (key, mk),
            '%d similar products on Amazon lists this week, cluster surge %d/100.' % (n, round(cs)),
            {'key': key, 'asins': asins, 'recent': recent, 'n_recent': n, 'surge': cs,
             'titles': {a: feats[a].get('title') or '' for a in recent}}, now)))
    clusters.sort(key=lambda r: (-r[0], r[1]))
    out += [al for _, _, al in clusters[:cap]]
    return out


# ---- Google Trends rules ----

def _label(feat):
    """(full label, base label) from feat['label'] ('Growing, seasonal peak Dec' -> base 'Growing')."""
    lab = feat.get('label') if isinstance(feat, dict) else None
    if isinstance(lab, (list, tuple)):                  # label() returns (label, why)
        lab = lab[0] if lab else None
    full = str(lab or '').strip()
    return full, full.split(',')[0].strip()


def _why(feat):
    w = feat.get('why') if isinstance(feat, dict) else None
    if isinstance(w, str):
        return [w]
    if w is None and isinstance(feat, dict) and isinstance(feat.get('label'), (list, tuple)) \
            and len(feat['label']) > 1:
        w = feat['label'][1]
    return [str(x) for x in _as_list(w) if x]


def _breakouts(feat):
    """Related queries marked Breakout, lower-cased. A bare True flag counts as one unnamed query ''."""
    if not isinstance(feat, dict):
        return set()
    out = set()
    flag = feat.get('related_breakout')
    if flag is True:
        out.add('')
    for q in list(_as_list(feat.get('breakout_queries'))) + list(_as_list(flag)):
        if isinstance(q, str) and q.strip():
            out.add(q.strip().lower())
    for key in ('related', 'related_queries', 'rising'):
        for r in _as_list(feat.get(key)):
            if not isinstance(r, dict):
                continue
            q = str(r.get('query') or r.get('term') or r.get('title') or '').strip().lower()
            fv = str(r.get('formattedValue') or r.get('formatted') or '').strip().lower()
            v = r.get('value')
            pct = _finite(v)
            if (r.get('breakout') is True or fv == 'breakout' or str(v).strip().lower() == 'breakout'
                    or (pct is not None and pct >= THRESH['breakout_pct'])) and q:
                out.add(q)
    return out


def _amazon_surge(feat):
    """(ok, value): an Amazon cluster surge >= cluster_surge_min, or a True flag."""
    for key in ('cluster_surge', 'amazon_surge'):
        v = feat.get(key)
        if v is True:
            return True, None
        f = _finite(v)
        if f is not None:
            return f >= THRESH['cluster_surge_min'], f
    return False, None


def trend_rules(term, geo, old_feat, new_feat, now_ts):
    """Alerts for one Google Trends term in one geo, comparing the previous and the new feature dicts.

    A feature dict holds 'label' (from trendfeat.label, modifiers allowed), optional 'why', related breakout
    queries ('breakout_queries' list, 'related_breakout' flag/list, or 'related' rows with breakout /
    formattedValue 'Breakout' / value >= 5000), 'cluster_surge' (number or True), 'autocomplete' (bool) and
    optional 'status' (a dict with a non-valid status is ignored).
      trend_label_change  the base label changed (not into or out of Insufficient)
      trends_breakout     the label became Breakout, or a new related query is marked Breakout
      confirmed_trend     Amazon cluster surge >= 60, label Emerging/Breakout/Growing and an autocomplete match
    """
    now = _finite(now_ts)
    new = new_feat if isinstance(new_feat, dict) else None
    if now is None or new is None or ('status' in new and not _valid(new)):
        return []
    old = old_feat if isinstance(old_feat, dict) and ('status' not in old_feat or _valid(old_feat)) else None
    mk, tg = _market(geo), _target(term)
    where = mk or 'worldwide'
    new_full, new_base = _label(new)
    old_full, old_base = _label(old) if old else ('', '')
    why = _why(new)
    out = []

    skip = THRESH['label_change_skip']
    if new_base and old_base and new_base != old_base and new_base not in skip and old_base not in skip:
        out.append(make_alert(
            'trend_label_change', mk, tg, 'Trend changed: %s %s → %s (%s)' % (tg, old_base, new_base, where),
            'Google Trends now reads %s (was %s).' % (new_full, old_full) + (' ' + '; '.join(why) + '.' if why else ''),
            {'term': tg, 'old': old_full, 'new': new_full, 'old_base': old_base, 'new_base': new_base, 'why': why},
            now))

    became = new_base == 'Breakout' and old_base != 'Breakout'
    fresh = sorted(_breakouts(new) - _breakouts(old))
    if became or fresh:
        parts = []
        if became:
            parts.append('searches labelled Breakout' + (' (%s)' % '; '.join(why) if why else ''))
        named = [q for q in fresh if q]
        if named:
            parts.append('related searches marked Breakout: ' + ', '.join(named[:5]))
        elif fresh:
            parts.append('a related search is marked Breakout')
        out.append(make_alert(
            'trends_breakout', mk, tg, 'Breakout on Google: %s (%s)' % (tg, where),
            (parts[0][0].upper() + '; '.join(parts)[1:]) + '.',
            {'term': tg, 'label': new_full, 'label_breakout': became, 'queries': named}, now))

    amazon, cs = _amazon_surge(new)
    ac = bool(new.get('autocomplete') or new.get('autocomplete_match'))
    if amazon and ac and new_base in THRESH['confirm_labels']:
        out.append(make_alert(
            'confirmed_trend', mk, tg, 'Confirmed trend: %s (%s)' % (tg, where),
            'Three signals agree: Amazon products surging%s, Google Trends %s, and Amazon autocomplete suggests it.'
            % (' (cluster %d/100)' % round(cs) if cs is not None else '', new_base),
            {'term': tg, 'label': new_full, 'cluster_surge': cs, 'autocomplete': ac, 'why': why}, now))
    return out


# ---- operations rules ----

def _source_market(name, st):
    m = _market(st.get('market'))
    if m:
        return m
    tail = str(name).rsplit(':', 1)[-1].strip().upper() if ':' in str(name) else ''
    return tail if tail in CURRENCY else ''


def _source_label(name):
    kind, _, mk = str(name).partition(':')
    kind = kind.strip().lower()
    nice = {'amazon': 'Amazon', 'complete': 'Amazon autocomplete', 'google': 'Google Trends'}.get(kind, str(name))
    return ('%s %s' % (nice, mk.strip().upper())).strip()


def _source_rules(state, now):
    out = []
    for name, st in _items(state, 'source'):
        mk = _source_market(name, st)
        label = _source_label(name)
        reason = str(st.get('reason') or '').strip().lower()
        until = _finite(st.get('cooldown_until'))
        if until is None:
            until = _finite(st.get('paused_until'))
        left = until - now if until is not None else None
        last_ok, last_block = _finite(st.get('last_ok')), _finite(st.get('last_block'))
        captcha = 'captcha' in reason or bool(st.get('captcha')) or bool(st.get('needs_captcha'))
        unsolved = last_ok is None or last_block is None or last_block >= last_ok
        data = {'source': name, 'reason': reason, 'cooldown_until': until, 'left_s': left,
                'fail_streak': _finite(st.get('fail_streak')), 'last_ok': last_ok, 'last_block': last_block}
        if captcha and unsolved:
            wait = (' Background checks resume on their own in %s.' % _dur(left)) if left and left > 0 else ''
            out.append(make_alert(
                'captcha_needed', mk, name, 'Captcha needed: %s' % label,
                'A background check hit a captcha on %s. Open the app and choose Solve now.%s' % (label, wait),
                data, now))
        elif (left is not None and left >= THRESH['paused_min_s']) or st.get('paused') is True:
            why = (' after %s' % reason.replace('_', ' ')) if reason else ''
            when = ('resumes in %s' % _dur(left)) if left is not None and left > 0 else 'paused until you resume it'
            long_ = left is not None and left >= THRESH['paused_long_s']
            out.append(make_alert(
                'source_paused', mk, name, 'Paused: %s' % label,
                '%s is cooling down%s; %s.' % (label, why, when), data, now, severity=3 if long_ else None))
    return out


_KIND_NAMES = {'keyword': 'keywords', 'kw': 'keywords', 'watch_kw': 'keywords', 'serp': 'keywords',
               'asin': 'products', 'product': 'products', 'list': 'lists', 'lists': 'lists',
               'trends': 'Trends terms', 'trends_series': 'Trends terms', 'suggest': 'autocomplete seeds',
               'seed': 'autocomplete seeds', 'category': 'categories'}


def _stale_rules(stale, now):
    mult = THRESH['stale_cadence_mult']
    groups = {}
    for it in _as_list(stale):
        if not isinstance(it, dict):
            continue
        last = next((f for f in (_finite(it.get(k)) for k in ('last_ok_ts', 'last_ok', 'last_ts', 'last_run_at'))
                     if f is not None), None)
        cad = _finite(it.get('every_s'))
        if cad is None:
            h = _finite(it.get('cadence_h'))
            cad = h * 3600.0 if h is not None else None
        ref = last if last is not None else _finite(it.get('added_at'))
        if isinstance(it.get('stale'), bool):
            is_stale = it['stale']
        elif ref is not None and cad is not None and cad > 0:
            is_stale = now - ref > mult * cad
        else:
            is_stale = True                             # no timing to check: trust the caller's list
        if not is_stale:
            continue
        mk = _market(it.get('market'))
        kind = str(it.get('kind') or 'data').strip()
        tgt = _target(it.get('display') or it.get('target'))
        groups.setdefault((mk, kind), []).append((tgt, None if ref is None else (now - ref) / DAY))
    out = []
    for (mk, kind), rows in sorted(groups.items()):
        ages = [a for _, a in rows if a is not None]
        names = [t for t, _ in rows if t]
        what = _KIND_NAMES.get(kind.lower(), '%s items' % kind)
        body = 'Not refreshed within %dx their usual schedule' % mult
        body += (' (oldest %.0f days ago)' % max(ages)) if ages else ''
        body += (': ' + ', '.join(names[:5]) + (' and %d more' % (len(names) - 5) if len(names) > 5 else '')
                 if names else '') + '.'
        out.append(make_alert(
            'stale_data', mk, kind, 'Stale data: %d %s%s' % (len(rows), what, ' (%s)' % mk if mk else ''), body,
            {'kind': kind, 'n': len(rows), 'targets': names[:50], 'oldest_age_days': max(ages) if ages else None},
            now))
    return out


def _ratio(x):
    """list_n_vs_median as a ratio: a number, an (n, median) pair or {'n', 'median'}."""
    if isinstance(x, dict):
        n, m = _finite(x.get('n')), _finite(x.get('median'))
    elif isinstance(x, (list, tuple)) and len(x) == 2:
        n, m = _finite(x[0]), _finite(x[1])
    else:
        return _finite(x)
    return n / m if n is not None and m else None


def _drift_rules(health, now):
    out = []
    for mk, h in _items(health, 'market'):
        mk = _market(mk)
        b2, bb = _finite(h.get('badges_last_2_days')), _finite(h.get('badges_before'))
        pages = _finite(h.get('search_pages_last_2_days'))
        if b2 is not None and bb is not None and b2 <= 0 < bb and (pages is None or pages >= 1):
            out.append(make_alert(
                'layout_drift', mk, 'search', 'Amazon layout may have changed: search pages (%s)' % mk,
                'No "bought in past month" badges parsed on valid search pages for 2 days (%s before). '
                'The page layout may have changed; numbers that use badges are on hold.' % _n(bb),
                {'check': 'badges', 'badges_last_2_days': b2, 'badges_before': bb, 'pages_last_2_days': pages},
                now))
        r = _ratio(h.get('list_n_vs_median'))
        if r is not None and r < THRESH['drift_list_share']:
            out.append(make_alert(
                'layout_drift', mk, 'list', 'Amazon layout may have changed: list pages (%s)' % mk,
                'List pages return only %d%% of their usual number of products.' % round(100 * r),
                {'check': 'list_n', 'list_n_vs_median': r}, now))
        miss, n = _finite(h.get('product_rank_missing_share')), _finite(h.get('product_pages'))
        if miss is not None and miss > THRESH['drift_rank_missing'] and (n is None or n >= THRESH['drift_min_pages']):
            out.append(make_alert(
                'layout_drift', mk, 'product', 'Amazon layout may have changed: product pages (%s)' % mk,
                'Sales rank missing on %d%% of product pages%s.' % (round(100 * miss), ' (%s pages)' % _n(n)
                                                                      if n is not None else ''),
                {'check': 'product_rank', 'product_rank_missing_share': miss, 'product_pages': n}, now))
    return out


def _fees_rules(fees_as_of, now):
    today = _utc(now).date()
    pairs = [(m, v) for m, v in fees_as_of.items()] if isinstance(fees_as_of, dict) else [('', fees_as_of)]
    out = []
    for mk, v in pairs:
        d = _date(v)
        if d is None:
            continue
        age = (today - d).days
        if age <= THRESH['fees_max_age_days']:
            continue
        mk = _market(mk)
        out.append(make_alert(
            'fees_stale', mk, 'fees', 'Fee tables are %d days old%s' % (age, ' (%s)' % mk if mk else ''),
            'Fees are as of %s. Check Amazon\'s current fee pages and update the table.' % d.isoformat(),
            {'fees_as_of': d.isoformat(), 'age_days': age}, now))
    return out


def _xray_rules(watched, now):
    out = []
    for w in _as_list(watched):
        if not isinstance(w, dict) or w.get('enabled') in (False, 0):
            continue
        if str(w.get('stage') or '').strip().lower() == 'rejected':
            continue
        ts = _finite(w.get('last_xray_import_ts'))
        if ts is None:
            continue
        age = (now - ts) / DAY
        if age <= THRESH['xray_max_age_days']:
            continue
        mk = _market(w.get('market'))
        tg = _target(w.get('target') or w.get('kw') or w.get('keyword') or w.get('display'))
        name = _target(w.get('display')) or tg
        out.append(make_alert(
            'rerun_xray', mk, tg, 'Re-run Xray: %s (%s)' % (name, mk),
            'The last Helium 10 Xray import for this niche was %d days ago. Export a fresh one to refresh sales.'
            % int(age), {'target': tg, 'last_xray_import_ts': ts, 'age_days': age}, now))
    return out


def ops_rules(ctx, now_ts):
    """Operations alerts from a context dict (any key may be missing):
      source_state   {source: {'cooldown_until', 'fail_streak', 'reason', 'last_ok', 'last_block', 'paused'}}
                     (or a list with 'source'); source names like 'amazon:AU', 'complete:US', 'google'
      stale          [{'kind', 'market', 'target', 'display', 'last_ok_ts', 'cadence_h' | 'every_s', 'added_at',
                       'stale' (bool, optional)}]
      parse_health   {market: {'badges_last_2_days', 'badges_before', 'search_pages_last_2_days' (optional),
                       'list_n_vs_median', 'product_rank_missing_share', 'product_pages' (optional)}}
      fees_as_of     'YYYY-MM-DD' (or {market: date})
      watched        [{'market', 'target', 'display', 'last_xray_import_ts', 'stage', 'enabled'}]
    Rules: captcha_needed (captcha not solved since), source_paused (cooldown of 10+ min left, or paused),
    stale_data (not refreshed within 3x cadence, one alert per market and kind), layout_drift (badges > 0 -> 0
    for 2 days, list n < 60% of median, rank missing on > 80% of product pages), fees_stale (> 90 days),
    rerun_xray (last Xray import > 30 days ago).
    """
    now = _finite(now_ts)
    if now is None or _utc(now) is None or not isinstance(ctx, dict):
        return []
    health = ctx.get('parse_health') if ctx.get('parse_health') is not None else ctx.get('health')
    watched = next((ctx.get(k) for k in ('watched', 'watch', 'niches') if ctx.get(k) is not None), None)
    return (_source_rules(ctx.get('source_state'), now) + _stale_rules(ctx.get('stale'), now)
            + _drift_rules(health, now) + _fees_rules(ctx.get('fees_as_of'), now) + _xray_rules(watched, now))


# ---- launch planner rule ----

def order_by_rule(plan_items, now_ts):
    """order_by alerts: a watched niche whose order-by date is 0-14 days away.

    plan_items: [{'market', 'target', 'display', 'plan': engine.planner.plan(...)}]; the plan keys may also
    sit on the item itself. Sea freight is checked first; when its date has passed, the air date is used.
    Plans with status 'unknown' (unreliable seasonality) are skipped.
    """
    now = _finite(now_ts)
    dt = _utc(now)
    if dt is None:
        return []
    today = dt.date()
    out = []
    for it in _as_list(plan_items):
        if not isinstance(it, dict) or it.get('enabled') in (False, 0):
            continue
        plan = it.get('plan') if isinstance(it.get('plan'), dict) else it
        if str(plan.get('status') or '').strip().lower() == 'unknown':
            continue
        dates = {}
        for mode in ('sea', 'air'):
            m = plan.get(mode)
            d = _date(m.get('order_by')) if isinstance(m, dict) else None
            if d is not None:
                dates[mode] = (d, (d - today).days, m)
        if not dates and plan.get('order_by') is not None:  # a flat plan: {'order_by', 'mode'}
            d = _date(plan.get('order_by'))
            if d is not None:
                dates[str(plan.get('mode') or 'sea')] = (d, (d - today).days, plan)
        hit = next(((mode, *dates[mode]) for mode in dates if 0 <= dates[mode][1] <= THRESH['order_by_days']), None)
        if hit is None:
            continue
        mode, d, left, info = hit
        mk = _market(it.get('market') or plan.get('market'))
        tg = _target(it.get('target') or it.get('kw') or it.get('keyword') or plan.get('target'))
        name = _target(it.get('display')) or tg
        peak = _month_name(plan.get('peak_month'))
        when = 'today' if left == 0 else 'within %d day%s' % (left, '' if left == 1 else 's')
        sea_passed = mode != 'sea' and 'sea' in dates and dates['sea'][1] < 0
        body = ('Too late for sea freight. ' if sea_passed else '')
        body += 'Order %s (by %s) to ship by %s' % (when, _day_str(d), mode)
        body += (' and be in stock before the %s peak.' % peak) if peak else '.'
        out.append(make_alert(
            'order_by', mk, tg, 'Order by %s: %s (%s)' % (_day_str(d), name, mk), body,
            {'target': tg, 'mode': mode, 'order_by': d.isoformat(), 'days_left': left,
             'ship_by': info.get('ship_by'), 'in_stock_by': info.get('in_stock_by'),
             'peak_month': _finite(plan.get('peak_month')), 'upswing_month': _finite(plan.get('upswing_month')),
             'sea_order_by': dates['sea'][0].isoformat() if 'sea' in dates else None,
             'air_order_by': dates['air'][0].isoformat() if 'air' in dates else None}, now))
    return out
