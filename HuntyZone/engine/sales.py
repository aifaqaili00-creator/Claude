"""Monthly units and revenue (synth.md 8.3-8.4, as changed by the plan).

- units_from_bsr: per-ASIN monthly units from BSR readings through the fitted curve, summing f(rank) over
  time (never f(mean rank), the curve is convex), with coverage per month.
- asin_offset: how far one ASIN sits above or below the curve, from its badges (empirical Bayes).
- niche_month: the niche's monthly units, taking Helium 10, then badges, then the curve.
- backcast: months before tracking started, from the Google Trends shape (at most 24 months).
- forecast: seasonal-naive x damped YoY, with a band that widens with the horizon.
- revenue: units x the price seen that month.

Every number is an est dict {'v', 'lo', 'hi', 'basis', 'conf'} with an 80% band.
Pure functions, numpy/pandas only, JSON-safe output (no NaN).
"""
import calendar
import math
import re
import time

import numpy as np
import pandas as pd

from .badge import bucket, page_demand, truncated_lognormal_mean

# Tunable thresholds.
THRESH = {
    # units_from_bsr
    'days_per_month': 30.44,       # f(rank) is units per month, so the daily rate is f / 30.44
    'cover_side_days': 1.5,        # a reading covers half the gap to each neighbour, at most 1.5 days a side
    'interp_min_days': 3.0,        # gaps of 3-14 days are filled by interpolating ln(rank)
    'interp_max_days': 14.0,       # longer gaps are left missing
    'interp_step_hours': 1.0,      # step used to add up an interpolated gap
    'sigma_nu_history': 0.25,      # day-to-day noise of ln(units) when there is a BSR history
    'sigma_nu_single': 0.5,        # ... and for a single BSR snapshot
    'partial_coverage': 0.5,       # months covered less than this are marked partial
    'param_sd_fallback': 0.3,      # ln-scale sd of the curve when it carries no covariance matrix
    'extrap_sd': 0.3,              # extra ln-scale sd for scaling a part-covered month up to a full month
    # asin_offset
    's_eps': 0.45,                 # prior sd of an ASIN's offset from the curve (ln units)
    'offset_max_n': 6,             # badge windows overlap (30 days each), so count at most this many readings
    'eps_clip': 1.5,
    # bands and confidence
    'z80': 1.2816,                 # 80% band = mid x/÷ exp(1.2816 sd)
    'conf_high_ratio': 2.5,        # hi/lo below this -> 'high'
    'conf_medium_ratio': 5.0,      # hi/lo below this -> 'medium', otherwise 'low'
    # niche_month
    'h10_err': 0.25,               # Helium 10 numbers are themselves a model: about +-25%
    'badge_valid_status': ('ok', 'partial'),
    'badge_band_ratio': 1.4,       # band when a snapshot gives only the mid sum
    # backcast
    'backcast_min_q': 0.5,         # no backcast when the Trends quality q_T is below this
    'backcast_max_months': 24,
    'backcast_rw_sd': 0.05,        # ln-scale sd added per month of distance from the anchor (as variance)
    'backcast_lookback': 2,        # use an index month up to this many months before the anchor month
    'anchor_sd_default': 0.25,     # ln-scale sd of an anchor given as a plain number
    'trends_round_sd': 0.29,       # Trends values are whole numbers: rounding sd (0.5 / sqrt 3)
    'trends_q_sd_min': 0.05,
    # forecast
    'forecast_max_months': 12,
    'damp': 0.85,                  # each later month's growth counts 0.85x the month before
    'max_monthly_growth': 0.08,    # +-8% a month at most
    'si_floor': 0.05,              # never divide by a seasonal index below this
    'fc_level_sd_min': 0.1,
    'fc_rw_sd_default': 0.1,       # month-to-month ln sd when the history is too short to measure it
    'fc_rw_sd_min': 0.03,
    'fc_rw_sd_max': 0.5,
    # revenue
    'price_carry_months': 3,       # carry a price forward at most this many months
}

DAY = 86400.0
_LN15, _LN25 = math.log(15), math.log(25)
PRIOR = {'beta': -0.85, 'sigma': 0.6, 'alpha': {'US': 13.68, 'AU': 13.68 - _LN15, 'AE': 13.68 - _LN25}}
_CONF_ORDER = ['low', 'medium', 'high']
_RE_MONTH = re.compile(r'^\s*(\d{4})-(\d{1,2})(?!\d)')


def est(v, lo, hi, basis, conf):
    """An estimated number with its range, where it came from and how sure we are."""
    return {'v': v, 'lo': lo, 'hi': hi, 'basis': basis, 'conf': conf}


# ---------- small helpers ----------

def _finite(x):
    """x as a float, or None for None / NaN / inf / text / bools."""
    if x is None or isinstance(x, bool):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _r(x, nd=1):
    """Round for JSON; None for missing or non-finite."""
    f = _finite(x)
    return None if f is None else round(f, nd)


def _ts(x):
    """Unix seconds (milliseconds are converted); None when unusable."""
    t = _finite(x)
    if t is None or t <= 0:
        return None
    if t > 1e11:
        t /= 1000.0
    return t if t < 1e11 else None


def _utc_month(t):
    return time.strftime('%Y-%m', time.gmtime(t))


def _month_key(x):
    """'YYYY-MM' from a string, Timestamp, datetime, date or Period; None when unreadable."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, np.datetime64):
        x = pd.Timestamp(x)
    if isinstance(x, str):
        m = _RE_MONTH.match(x)
        if not m or not 1 <= int(m.group(2)) <= 12:
            return None
        return f'{int(m.group(1)):04d}-{int(m.group(2)):02d}'
    if hasattr(x, 'year') and hasattr(x, 'month'):
        try:
            if pd.isna(x):                      # NaT
                return None
        except (TypeError, ValueError):
            pass
        try:
            return f'{int(x.year):04d}-{int(x.month):02d}'
        except (TypeError, ValueError):
            return None
    return None


def _key_fn(month_fn):
    """ts -> 'YYYY-MM' using month_fn (any time zone), falling back to UTC when it fails."""
    if month_fn is None:
        return _utc_month

    def key(t):
        try:
            k = _month_key(month_fn(t))
        except Exception:                       # a caller's function: never let it break the sum
            k = None
        return k or _utc_month(t)
    return key


def _add_months(m, k):
    y, mo = int(m[:4]), int(m[5:7])
    i = y * 12 + (mo - 1) + int(k)
    return f'{i // 12:04d}-{i % 12 + 1:02d}'


def _days_in(m):
    try:
        return calendar.monthrange(int(m[:4]), int(m[5:7]))[1]
    except (TypeError, ValueError, IndexError, calendar.IllegalMonthError):
        return None


def _conf(lo, hi):
    """Confidence from the width of the band: hi/lo < 2.5 high, < 5 medium, otherwise low."""
    if lo is None or hi is None or lo <= 0:
        return 'low'
    r = hi / lo
    return 'high' if r < THRESH['conf_high_ratio'] else 'medium' if r < THRESH['conf_medium_ratio'] else 'low'


def _cap_conf(conf, cap):
    """The lower of two confidence levels."""
    a = _CONF_ORDER.index(conf) if conf in _CONF_ORDER else 0
    b = _CONF_ORDER.index(cap) if cap in _CONF_ORDER else 0
    return _CONF_ORDER[min(a, b)]


def _band(v, sd):
    """80% band around v for a ln-scale sd."""
    z = THRESH['z80'] * max(0.0, sd)
    return v * math.exp(-z), v * math.exp(z)


def _est_parts(x):
    """(v, lo, hi) from an est dict or a plain number; lo/hi default to v. (None, None, None) if unusable."""
    if isinstance(x, dict):
        v = _finite(x.get('v'))
        if v is None:
            return None, None, None
        lo, hi = _finite(x.get('lo')), _finite(x.get('hi'))
        return v, (v if lo is None else lo), (v if hi is None else hi)
    v = _finite(x)
    return v, v, v


def _as_list(x):
    """list(x), or [] for None, text or anything that is not iterable."""
    if x is None or isinstance(x, (str, bytes, dict)):
        return []
    try:
        return list(x)
    except TypeError:
        return []


def _month_items(x):
    """[(month, raw value)] sorted by month from a dict, a Series or (month, value) pairs; later rows win."""
    if x is None:
        return []
    try:
        if isinstance(x, pd.Series):
            pairs = list(zip(x.index, x.values))
        elif isinstance(x, dict):
            pairs = list(x.items())
        else:
            pairs = [(p[0], p[1]) for p in x if isinstance(p, (list, tuple)) and len(p) >= 2]
    except TypeError:
        return []
    out = {}
    for k, v in pairs:
        m = _month_key(k)
        if m is not None:
            out[m] = v
    return sorted(out.items())


# ---------- the curve ----------

def _curve_params(curve, node):
    """alpha, beta, sigma, status and the (beta, alpha) covariance from a curve dict; None if not a dict.

    Missing pieces fall back to the priors, so a half-filled curve still gives numbers."""
    if not isinstance(curve, dict):
        return None
    market = str(curve.get('market') or 'US').upper()
    beta = _finite(curve.get('beta'))
    beta = PRIOR['beta'] if beta is None else beta
    alphas = curve.get('alpha')
    a = None
    if isinstance(alphas, dict):
        if node is not None:
            a = _finite(alphas.get(node))
            if a is None:
                a = _finite(alphas.get(str(node)))
        if a is None:
            a = _finite(alphas.get('_market'))
    else:
        a = _finite(alphas)
    if a is None:
        a = PRIOR['alpha'].get(market, PRIOR['alpha']['US'])
    sigma = _finite(curve.get('sigma'))
    if sigma is None or sigma <= 0:
        sigma = PRIOR['sigma']
    cov = None
    try:
        c = np.asarray(curve.get('cov'), dtype=float)
        if c.ndim == 2 and c.shape[0] >= 2 and c.shape[1] >= 2 and np.isfinite(c[:2, :2]).all():
            cov = c[:2, :2]                     # rows/cols: beta, alpha_m (sigma is not needed here)
    except (TypeError, ValueError):
        cov = None
    return {'market': market, 'alpha': a, 'beta': beta, 'sigma': sigma, 'cov': cov,
            'status': curve.get('status') or 'prior_only'}


def _param_var(p, ln_r):
    """Delta-method variance of mu = alpha + beta ln r from the curve's covariance."""
    c = p['cov']
    if c is None:
        return THRESH['param_sd_fallback'] ** 2
    return max(0.0, float(c[1, 1] + 2 * ln_r * c[0, 1] + ln_r * ln_r * c[0, 0]))


# ---------- units_from_bsr ----------

def _readings(samples):
    """Sorted (ts, ln rank) arrays from [(ts, bsr)] or [{'ts', 'bsr'|'rank'}]; bad rows dropped.

    Readings at the same second are nudged 1 s apart, so each keeps its own f(rank)."""
    rows = []
    it = _as_list(samples)
    for s in it:
        if isinstance(s, dict):
            t = s.get('ts')
            r = s.get('bsr', s.get('rank', s.get('rank_now')))
        elif isinstance(s, (list, tuple, np.ndarray)) and len(s) >= 2:
            t, r = s[0], s[1]
        else:
            continue
        t, r = _ts(t), _finite(r)
        if t is None or r is None or r <= 0:
            continue
        rows.append((t, math.log(r)))
    rows.sort()
    ts = [t for t, _ in rows]
    for i in range(1, len(ts)):
        if ts[i] <= ts[i - 1]:
            ts[i] = ts[i - 1] + 1.0
    return np.array(ts, dtype=float), np.array([l for _, l in rows], dtype=float)


def _pieces(ts, lr):
    """Covered time as pieces (start, end, ln rank), ln rank constant on each piece.

    - gap under 3 days: each reading covers half the gap
    - gap of 3-14 days: ln rank is interpolated across the whole gap (hourly steps)
    - longer gap: each side covers 1.5 days, the rest is missing
    - before the first and after the last reading: 1.5 days
    """
    side = THRESH['cover_side_days'] * DAY
    g_lo, g_hi = THRESH['interp_min_days'] * DAY, THRESH['interp_max_days'] * DAY
    step = THRESH['interp_step_hours'] * 3600.0
    t0, t1, ll = [], [], []

    def add(a, b, l):
        if b > a:
            t0.append(a)
            t1.append(b)
            ll.append(l)

    add(ts[0] - side, ts[0], lr[0])
    for k in range(len(ts) - 1):
        a, b = ts[k], ts[k + 1]
        g = b - a
        if g < g_lo:
            mid = a + g / 2
            add(a, mid, lr[k])
            add(mid, b, lr[k + 1])
        elif g <= g_hi:
            n = max(1, int(math.ceil(g / step)))
            edges = np.linspace(a, b, n + 1)
            mids = (edges[:-1] + edges[1:]) / 2
            t0.extend(edges[:-1])
            t1.extend(edges[1:])
            ll.extend(lr[k] + (lr[k + 1] - lr[k]) * (mids - a) / g)
        else:
            add(a, a + side, lr[k])
            add(b - side, b, lr[k + 1])
    add(ts[-1], ts[-1] + side, lr[-1])
    return np.array(t0, dtype=float), np.array(t1, dtype=float), np.array(ll, dtype=float)


def _month_spans(t0, t1, key):
    """[(month, start, end)] covering [t0, t1). Boundaries are found by stepping a day and then bisecting,
    so `key` may use any time zone as long as it never goes backwards in time."""
    spans = []
    t, m = t0, key(t0)

    def bisect(lo, hi):                         # key(lo) == m, key(hi) != m
        while hi - lo > 0.5:
            mid = (lo + hi) / 2
            if key(mid) == m:
                lo = mid
            else:
                hi = mid
        return hi

    while t < t1:
        probe, nxt = t, None
        while True:
            probe2 = probe + DAY
            if probe2 >= t1:
                last = t1 - 1e-3
                if last > probe and key(last) != m:
                    nxt = bisect(probe, last)
                break
            if key(probe2) != m:
                nxt = bisect(probe, probe2)
                break
            probe = probe2
        if nxt is None or nxt <= t:
            spans.append((m, t, t1))
            break
        spans.append((m, t, nxt))
        t, m = nxt, key(nxt)
    return spans


def _offset(offset):
    """(eps, sd) from a float or an asin_offset_info dict; no offset gives (0, s_eps)."""
    s_eps = THRESH['s_eps']
    if isinstance(offset, dict):
        eps = _finite(offset.get('eps')) or 0.0
        sd = _finite(offset.get('sd'))
        sd = s_eps if sd is None or sd < 0 else sd
    else:
        eps, sd = (_finite(offset) or 0.0), s_eps
    c = THRESH['eps_clip']
    return max(-c, min(c, eps)), sd


def units_from_bsr(samples, curve, node=None, month_fn=None, offset=None):
    """Monthly units for one ASIN from its BSR readings: {month: est dict}.

    samples  [(ts, bsr)] (or dicts with 'ts' and 'bsr'/'rank'), in any order. Movers & Shakers rank_now /
             rank_before can be added as extra readings.
    curve    a curve dict from engine.curve.fit_market ('alpha', 'beta', 'sigma', 'cov', 'status').
    node     the root category node, for a node-level alpha; otherwise the market alpha is used.
    month_fn ts -> 'YYYY-MM' (for a marketplace time zone); default UTC.
    offset   optional ASIN offset: a float from asin_offset or the dict from asin_offset_info.

    The daily rate is f(rank)/30.44 with f = exp(alpha + beta ln rank + s_nu^2/2) (s_nu 0.25 with a
    history, 0.5 for a single reading). Each reading covers half the gap to its neighbours, at most 1.5
    days a side (3 days in all); gaps of 3-14 days are interpolated in ln rank, longer gaps are missing.
    f is summed over time, never taken at the mean rank.

    v/lo/hi are the full-month estimate (covered units / coverage). Extra keys: coverage (0..1), partial
    (coverage < 0.5), units_covered (units in the covered days only), days_covered, n (readings that
    month), method 'curve'. Only months holding at least one reading are returned. {} without data.
    """
    p = _curve_params(curve, node)
    ts, lr = _readings(samples)
    if p is None or not len(ts):
        return {}
    key = _key_fn(month_fn)
    s_nu = THRESH['sigma_nu_single'] if len(ts) == 1 else THRESH['sigma_nu_history']
    eps, s_off = _offset(offset)
    t0, t1, ll = _pieces(ts, lr)
    expo = np.clip(p['alpha'] + p['beta'] * ll + 0.5 * s_nu * s_nu, -700, 700)
    rate = np.exp(expo) / THRESH['days_per_month']          # units per day on each piece

    n_in = {}
    for t in ts.tolist():
        m = key(t)
        n_in[m] = n_in.get(m, 0) + 1

    acc = {}                                                # month -> [seconds, units, units * ln rank]
    for m, b0, b1 in _month_spans(float(t0.min()), float(t1.max()), key):
        if m not in n_in:
            continue
        ov = np.clip(np.minimum(t1, b1) - np.maximum(t0, b0), 0.0, None)
        if not ov.any():
            continue
        u = ov / DAY * rate
        a = acc.setdefault(m, [0.0, 0.0, 0.0])
        a[0] += float(ov.sum())
        a[1] += float(u.sum())
        a[2] += float((u * ll).sum())

    out = {}
    mult = math.exp(eps)
    for m in sorted(acc):
        secs, units, ul = acc[m]
        days_cov = secs / DAY
        days_in = _days_in(m) or THRESH['days_per_month']
        cover = min(1.0, days_cov / days_in)
        if cover <= 0:
            continue
        lbar = ul / units if units > 0 else float(np.mean(ll))
        v = units / cover * mult
        var = (_param_var(p, lbar) + s_off * s_off + s_nu * s_nu / max(1.0, days_cov)
               + THRESH['extrap_sd'] ** 2 * (1.0 - cover))
        lo, hi = _band(v, math.sqrt(var))
        partial = cover < THRESH['partial_coverage']
        conf = 'low' if partial or p['status'] != 'calibrated' else _conf(lo, hi)
        rec = est(_r(v), _r(lo), _r(hi), 'estimated', conf)
        rec.update(coverage=_r(cover, 3), partial=bool(partial), units_covered=_r(units * mult),
                   days_covered=_r(days_cov, 2), n=int(n_in.get(m, 0)), method='curve')
        out[m] = rec
    return out


# ---------- asin_offset ----------

def _badge_reading(rd, market):
    """(L, U, ln rank, s_nu) from one badge reading; None when unusable.

    A reading is a dict {'badge' (number or label) | 'low' + 'high', 'bsr' | 'ln_bsr', 'history': bool,
    'kind': 'badge'|'badge_lb'} or a tuple (badge, bsr[, history]). 'history' True means the rank is a
    30-day geometric mean (s_nu 0.25); otherwise it is a single snapshot (s_nu 0.5)."""
    history = False
    if isinstance(rd, dict):
        if 'low' in rd:
            L, U = _finite(rd.get('low')), _finite(rd.get('high'))
        else:
            L, U = bucket(rd.get('badge', rd.get('L')), market)
        if rd.get('kind') == 'badge_lb':        # a lower bound only (badge on a variation child)
            U = None
        ln_r = _finite(rd.get('ln_bsr'))
        if ln_r is None:
            r = _finite(rd.get('bsr', rd.get('rank')))
            ln_r = math.log(r) if r is not None and r > 0 else None
        history = bool(rd.get('history'))
    elif isinstance(rd, (list, tuple)) and len(rd) >= 2:
        L, U = bucket(rd[0], market)
        r = _finite(rd[1])
        ln_r = math.log(r) if r is not None and r > 0 else None
        history = bool(rd[2]) if len(rd) > 2 else False
    else:
        return None
    L = _finite(L)
    if L is None or L <= 0 or ln_r is None or (U is not None and U <= L):
        return None
    s_nu = THRESH['sigma_nu_history'] if history else THRESH['sigma_nu_single']
    return L, U, ln_r, s_nu


def asin_offset_info(curve, node, badge_readings):
    """The ASIN's offset from the curve with its posterior sd: {'eps', 'sd', 'n', 'mean_e'}.

    For each badge reading k: e_k = ln E_trunc,k - mu_k, where E_trunc is the curve's lognormal mean
    inside the badge bucket. eps = mean(e) * s_eps^2 / (s_eps^2 + s_nu^2/n), s_eps = 0.45 (precision
    weighted when readings differ in s_nu; at most 6 readings count, as badge windows overlap).
    No usable reading gives eps 0 and sd s_eps."""
    s_eps = THRESH['s_eps']
    out = {'eps': 0.0, 'sd': s_eps, 'n': 0, 'mean_e': None}
    p = _curve_params(curve, node)
    if p is None:
        return out
    es, ws = [], []
    for rd in _as_list(badge_readings):
        parsed = _badge_reading(rd, p['market'])
        if parsed is None:
            continue
        L, U, ln_r, s_nu = parsed
        mu = p['alpha'] + p['beta'] * ln_r
        e = truncated_lognormal_mean(mu, p['sigma'], L, U)
        if e is None or e <= 0:
            continue
        es.append(math.log(e) - mu)
        ws.append(1.0 / (s_nu * s_nu))
    if not es:
        return out
    es, ws = np.array(es), np.array(ws)
    mean_e = float((es * ws).sum() / ws.sum())
    prec = float(ws.sum()) * min(1.0, THRESH['offset_max_n'] / len(es))
    prior = 1.0 / (s_eps * s_eps)
    c = THRESH['eps_clip']
    eps = max(-c, min(c, mean_e * prec / (prec + prior)))
    out.update(eps=round(eps, 4), sd=round(1.0 / math.sqrt(prec + prior), 4), n=len(es), mean_e=round(mean_e, 4))
    return out


def asin_offset(curve, node, badge_readings):
    """eps: the empirical-Bayes shrink of ln E_trunc - mu over the ASIN's badge readings (s_eps 0.45).

    Multiply the ASIN's curve units by exp(eps). 0.0 when there is nothing to go on.
    See asin_offset_info for the reading format and the posterior sd."""
    return asin_offset_info(curve, node, badge_readings)['eps']


# ---------- niche_month ----------

def _as_est(x, basis, rel=None, ratio=None, conf=None):
    """est dict from an est dict or a number. A number gets the band v*(1 -+ rel) or v /x ratio."""
    v, lo, hi = _est_parts(x)
    if v is None or v < 0:
        return None
    if isinstance(x, dict):
        b = x.get('basis') or basis
        c = x.get('conf') or conf or _conf(lo, hi)
        rec = est(_r(v), _r(lo), _r(hi), b, c)
        if x.get('partial') is not None:
            rec['partial'] = bool(x['partial'])
        for k, nd in (('coverage', 3), ('as_of', 0), ('n', 0)):
            val = _r(x.get(k), nd)
            if val is not None:
                rec[k] = int(val) if nd == 0 else val
        return rec
    if rel is not None:
        lo, hi = v * (1 - rel), v * (1 + rel)
    elif ratio:
        lo, hi = v / ratio, v * ratio
    return est(_r(v), _r(lo), _r(hi), basis, conf or _conf(lo, hi))


def _latest_by_month(rows, key, value_fn):
    """{month: est} from dict rows with 'month' or 'ts', keeping the latest row (by ts) that gives a value."""
    best = {}                                   # month -> ((ts, row order), est)
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        t = _ts(row.get('ts'))
        m = _month_key(row.get('month')) or (key(t) if t is not None else None)
        if m is None:
            continue
        e = value_fn(row)
        if e is None:
            continue
        if t is not None:
            e['as_of'] = int(t)
        rank = (t if t is not None else -1.0, i)
        if m not in best or rank >= best[m][0]:
            best[m] = (rank, e)
    return {m: e for m, (_, e) in best.items()}


def _h10_months(x, key):
    """Helium 10 monthly totals: {month: units | est} or rows [{'month'|'ts', 'units'|'v'|'sales'}]."""
    def num(v):
        return _as_est(v, 'reported', rel=THRESH['h10_err'], conf='medium')
    if isinstance(x, (dict, pd.Series)):
        out = {m: num(v) for m, v in _month_items(x)}
        return {m: e for m, e in out.items() if e is not None}
    if isinstance(x, (list, tuple)):
        def value(row):
            for k in ('units', 'v', 'sales', 'total'):
                if row.get(k) is not None:
                    return num(row[k])
            return None
        return _latest_by_month(x, key, value)
    return {}


def _snapshot_est(s, market):
    """Badge demand from one page snapshot: its 'est', its 'badges' list or its mid/low/high sums."""
    if isinstance(s.get('est'), dict):
        return _as_est(s['est'], 'estimated', ratio=THRESH['badge_band_ratio'])
    if isinstance(s.get('badges'), (list, tuple)):
        e = page_demand(list(s['badges']), market).get('est')
        return dict(e) if e else None
    mid = _finite(s.get('bought_mid_sum', s.get('mid_sum')))
    if mid is None or mid <= 0:
        return None
    lo = _finite(s.get('bought_low_sum', s.get('low_sum')))
    hi = _finite(s.get('bought_high_sum', s.get('high_sum')))
    if lo is None or hi is None:
        return _as_est(mid, 'estimated', ratio=THRESH['badge_band_ratio'])
    return est(_r(mid), _r(lo), _r(hi), 'estimated', _conf(lo, hi))


def _badge_months(x, market, key):
    """Badge sums per month from page snapshots, using the latest valid snapshot of each month."""
    if isinstance(x, (dict, pd.Series)):
        out = {m: _as_est(v, 'estimated', ratio=THRESH['badge_band_ratio']) for m, v in _month_items(x)}
        return {m: e for m, e in out.items() if e is not None}
    if not isinstance(x, (list, tuple)):
        return {}
    valid = THRESH['badge_valid_status']
    rows = [s for s in x if isinstance(s, dict) and (s.get('status') is None or s.get('status') in valid)]
    return _latest_by_month(rows, key, lambda s: _snapshot_est(s, market))


def _curve_months(x):
    """Curve totals: {month: est} as is, or {asin: {month: est}} summed per month."""
    if not isinstance(x, (dict, pd.Series)):
        return {}
    sums = {}                                   # month -> [v, lo, hi, n, partial, worst conf]
    for k, val in (x.items() if isinstance(x, dict) else zip(x.index, x.values)):
        m = _month_key(k)
        if m is not None and (isinstance(val, dict) and 'v' in val or _finite(val) is not None):
            parts = {m: val}                    # already a niche total for this month
        elif isinstance(val, dict):
            parts = dict(_month_items(val))     # one ASIN's months
        else:
            continue
        for mm, e in parts.items():
            mm = _month_key(mm)
            v, lo, hi = _est_parts(e)
            if mm is None or v is None:
                continue
            s = sums.setdefault(mm, [0.0, 0.0, 0.0, 0, False, 'high'])
            s[0] += v
            s[1] += lo
            s[2] += hi
            s[3] += 1
            if isinstance(e, dict):
                s[4] = s[4] or bool(e.get('partial'))
                s[5] = _cap_conf(s[5], e.get('conf') or _conf(lo, hi))
            else:
                s[5] = _cap_conf(s[5], 'low')
    out = {}
    for m, (v, lo, hi, n, partial, conf) in sums.items():
        rec = est(_r(v), _r(lo), _r(hi), 'estimated', conf)
        rec.update(n_asins=n, partial=partial)
        out[m] = rec
    return out


def niche_month(sources, month_fn=None):
    """The niche's monthly units: {month: est dict + 'method'}, months sorted.

    sources = {
      'market': 'US' | 'AU' | 'AE',
      'h10':   Helium 10 totals: {month: units | est} or [{'month'|'ts', 'units'}]   (basis 'reported'),
      'badge': page snapshots [{'ts', 'status', 'badges': [...] | 'est' | 'bought_mid_sum' (+ low/high)}]
               or {month: units | est},
      'curve': {month: est} niche totals, or {asin: {month: est}} from units_from_bsr (summed here),
    }
    Priority per month: 'h10' when an import exists, then 'badge' (the latest valid snapshot of the month,
    status ok/partial; the badge already counts 30 days), then 'curve'. The values of the sources not used
    are kept in 'others' ({method: v}) for a cross-check. {} without data.
    """
    if not isinstance(sources, dict):
        return {}
    market = str(sources.get('market') or 'US').upper()
    key = _key_fn(month_fn)
    layers = [('h10', _h10_months(sources.get('h10'), key)),
              ('badge', _badge_months(sources.get('badge'), market, key)),
              ('curve', _curve_months(sources.get('curve')))]
    months = sorted(set().union(*[set(d) for _, d in layers]))
    out = {}
    for m in months:
        found = [(name, d[m]) for name, d in layers if m in d]
        name, e = found[0]
        rec = dict(e)
        rec['method'] = name
        if len(found) > 1:
            rec['others'] = {n: x.get('v') for n, x in found[1:]}
        out[m] = rec
    return out


# ---------- backcast ----------

def _index_map(x):
    """{month: index value >= 0} from a Trends Series (month-start index), a dict or pairs."""
    out = {}
    for m, v in _month_items(x):
        f = _finite(v)
        if f is not None and f >= 0:
            out[m] = f
    return out


def backcast(anchor_units, anchor_month, trends_monthly, q_T, months=24):
    """Units for the months before tracking started, from the Google Trends shape:
    {month: est dict, basis 'estimated', 'method': 'backcast'}.

    units[m] = anchor * idx[m] / idx[anchor]. anchor_units is a number or an est dict (its band sets the
    anchor's uncertainty). If the anchor month has no index value, the latest of the 2 months before it is
    used. At most 24 months before the anchor month (the anchor month itself is not returned).
    The ln-scale band is sqrt(sd_anchor^2 + sd_T^2 + d*0.05^2 + rounding), so it widens with the distance d.
    Returns {} when q_T < 0.5 or the inputs are unusable.
    """
    q = _finite(q_T)
    if q is None or q < THRESH['backcast_min_q']:
        return {}
    av, alo, ahi = _est_parts(anchor_units)
    am = _month_key(anchor_month)
    if av is None or av <= 0 or am is None:
        return {}
    idx = _index_map(trends_monthly)
    ia = None
    for k in range(THRESH['backcast_lookback'] + 1):
        x = idx.get(_add_months(am, -k))
        if x is not None and x > 0:
            ia = x
            break
    if ia is None:
        return {}
    n = _finite(months)
    n = int(max(0, min(THRESH['backcast_max_months'], n if n is not None else THRESH['backcast_max_months'])))
    if isinstance(anchor_units, dict) and alo is not None and alo > 0 and ahi > alo:
        sd_anchor = (math.log(ahi) - math.log(alo)) / (2 * THRESH['z80'])
    else:
        sd_anchor = THRESH['anchor_sd_default']
    sd_t = max(THRESH['trends_q_sd_min'], 1.0 - min(1.0, q))
    rnd = THRESH['trends_round_sd']
    base_var = sd_anchor ** 2 + sd_t ** 2 + (rnd / ia) ** 2
    out = {}
    for d in range(n, 0, -1):                       # oldest first
        m = _add_months(am, -d)
        x = idx.get(m)
        if x is None:
            continue
        var = base_var + d * THRESH['backcast_rw_sd'] ** 2
        if x > 0:
            v = av * x / ia
            lo, hi = _band(v, math.sqrt(var + (rnd / x) ** 2))
            conf = _cap_conf(_conf(lo, hi), 'medium')
        else:                                       # Trends shows 0: too few searches to measure
            v, lo = 0.0, 0.0
            hi = _band(av * 0.5 / ia, math.sqrt(var))[1]
            conf = 'low'
        rec = est(_r(v), _r(lo), _r(hi), 'estimated', conf)
        rec.update(method='backcast', index=_r(x, 2), distance=d)
        out[m] = rec
    return out


# ---------- forecast ----------

def _si_array(si):
    """12 seasonal index values (floored), flat 1.0 when missing. Takes a list or a seasonality() dict."""
    if isinstance(si, dict):
        si = si.get('si')
    try:
        a = np.array([_finite(v) for v in si], dtype=float) if si is not None else None
    except TypeError:
        a = None
    if a is None or a.shape != (12,) or not np.isfinite(a).all() or a.sum() <= 0:
        return np.ones(12)
    return np.maximum(a, THRESH['si_floor'])


def forecast(monthly_units, si=None, yoy=None, months=6):
    """The next months' units: {month: est dict basis 'forecast', 'method': 'forecast', 'h'}.

    Seasonal-naive x damped YoY:
      level = mean of the last 3 deseasonalised months (units / SI); partial months are skipped
      r     = (1 + yoy)^(1/12) - 1, clipped to +-8% a month
      G(h)  = (1 + r)^(0.85 + 0.85^2 + ... + 0.85^h)   (the YoY growth, damped by 0.85^h each month)
      units = level * SI[month] * G(h)
    The ln-scale band is sqrt(s_level^2 + h * s_rw^2), s_rw being the month-to-month sd of the
    deseasonalised history, so it widens with h. Forecasts start the month after the last month given.
    months is clipped to 0..12. {} without data.
    """
    n = _finite(months)
    n = int(max(0, min(THRESH['forecast_max_months'], n if n is not None else 6)))
    hist = []
    for m, val in _month_items(monthly_units):
        v, _, _ = _est_parts(val)
        if v is not None and v >= 0:
            hist.append((m, v, isinstance(val, dict) and bool(val.get('partial'))))
    if n == 0 or not hist:
        return {}
    s = _si_array(si)
    full = [(m, v) for m, v, part in hist if not part] or [(m, v) for m, v, _ in hist]
    d_all = np.array([v / s[int(m[5:7]) - 1] for m, v in full], dtype=float)
    last3 = d_all[-3:]
    level = float(last3.mean())

    y = _finite(yoy)
    g = THRESH['max_monthly_growth']
    r = -g if y is not None and y <= -1 else ((1 + y) ** (1 / 12) - 1 if y is not None else 0.0)
    r = max(-g, min(g, r))

    pos = d_all[-13:]
    pos = pos[pos > 0]
    if len(pos) >= 4:
        s_rw = float(np.std(np.diff(np.log(pos)), ddof=1))
        s_rw = max(THRESH['fc_rw_sd_min'], min(THRESH['fc_rw_sd_max'], s_rw))
    else:
        s_rw = THRESH['fc_rw_sd_default']
    k = int((last3 > 0).sum())
    s_level = THRESH['fc_level_sd_min']
    if k >= 2:
        s_level = max(s_level, float(np.std(np.log(last3[last3 > 0]), ddof=1)) / math.sqrt(k))

    out = {}
    last = hist[-1][0]
    cum = 0.0
    for h in range(1, n + 1):
        cum += THRESH['damp'] ** h
        m = _add_months(last, h)
        v = level * float(s[int(m[5:7]) - 1]) * (1 + r) ** cum
        lo, hi = _band(v, math.sqrt(s_level ** 2 + h * s_rw ** 2))
        conf = _cap_conf(_conf(lo, hi), 'medium') if v > 0 else 'low'
        rec = est(_r(v), _r(lo), _r(hi), 'forecast', conf)
        rec.update(method='forecast', h=h)
        out[m] = rec
    return out


# ---------- revenue ----------

def revenue(units_by_month, price_by_month):
    """Revenue per month = units x the price in effect: {month: est dict + 'price', 'price_from', 'units'}.

    units_by_month: {month: units | est dict} (any of the outputs above).
    price_by_month: {month: price | est dict}, or one number used for every month (for example the current
    median price for a backcast).
    A month without its own price takes the latest earlier price within 3 months ('carried'); failing that
    the nearest observed price ('assumed', confidence lowered one step). Months with no price at all are
    left out. The basis and method of the units are kept.
    """
    units = _month_items(units_by_month)
    if not units:
        return {}
    scalar = None
    prices = {}
    if isinstance(price_by_month, (dict, pd.Series, list, tuple)):
        for m, p in _month_items(price_by_month):
            v, _, _ = _est_parts(p)
            if v is not None and v > 0:
                prices[m] = v
    else:
        scalar = _finite(price_by_month)
        scalar = scalar if scalar is not None and scalar > 0 else None
    seen = sorted(prices)

    def price_for(m):
        if m in prices:
            return prices[m], 'month'
        earlier = [k for k in seen if k < m and k >= _add_months(m, -THRESH['price_carry_months'])]
        if earlier:
            return prices[earlier[-1]], 'carried'
        if scalar is not None:
            return scalar, 'given'
        if seen:
            ym = int(m[:4]) * 12 + int(m[5:7])
            near = min(seen, key=lambda k: (abs(int(k[:4]) * 12 + int(k[5:7]) - ym), k))
            return prices[near], 'assumed'
        return None, None

    out = {}
    for m, val in units:
        v, lo, hi = _est_parts(val)
        if v is None or v < 0:
            continue
        p, how = price_for(m)
        if p is None:
            continue
        if isinstance(val, dict):
            basis, conf = val.get('basis') or 'estimated', val.get('conf') or _conf(lo, hi)
        else:
            basis, conf = 'estimated', 'medium'
        if how == 'assumed':
            conf = _CONF_ORDER[max(0, _CONF_ORDER.index(conf) - 1)] if conf in _CONF_ORDER else 'low'
        rec = est(_r(v * p, 2), _r(lo * p, 2), _r(hi * p, 2), basis, conf)
        rec.update(price=_r(p, 2), price_from=how, units=_r(v))
        if isinstance(val, dict):
            for k in ('method', 'partial'):
                if k in val:
                    rec[k] = val[k]
        out[m] = rec
    return out
