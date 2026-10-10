"""'Bought in past month' badges: read the label, find its bucket and estimate the units behind it.

Amazon shows "N+ bought in past month", which means the listing sold somewhere in [N, next label)
units in the last 30 days. The badge is a coarse bucket, so every number here comes with a range.
Pure functions, numpy only.
"""
import math
import re

import numpy as np

from .stats import Phi, log_Phi


def _ladder():
    """10+ ... 40+, 50+, 100+ ... 900+, 1K+ ... 9K+, 10K+ ... 90K+, 100K+."""
    return ([10, 20, 30, 40, 50] + list(range(100, 1000, 100)) + list(range(1000, 10000, 1000))
            + list(range(10000, 100000, 10000)) + [100000])


# Badge labels per marketplace. Labels below 50 have been seen on some marketplaces, so every market
# uses the same full ladder.
LADDER = {'US': _ladder(), 'AU': _ladder(), 'AE': _ladder()}

THRESH = {
    'open_mean_mult': 1.5,       # open top bucket mean = 1.5 L (Pareto with theta 3, an assumption)
    'open_hi_mult': 2.0,         # open top bucket upper end = 2 L (assumed)
    'conf_high_ratio': 2.5,      # hi/lo below this -> 'high' confidence
    'conf_medium_ratio': 5.0,    # hi/lo below this -> 'medium', otherwise 'low'
    'page_open_share_low': 0.5,  # page total is 'low' confidence when open buckets give over half of it
}

_MULT = {'k': 1e3, 'm': 1e6}
_ARABIC_DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789')
_NUM = r'(\d[\d.,]*)\s*([kKmM])?'
_RE_BOUGHT = re.compile(_NUM + r'\s*\+?\s*bought', re.I)      # "5K+ bought in past month"
_RE_PLUS = re.compile(_NUM + r'\s*\+')                         # "200+"
_RE_BARE = re.compile(r'^\s*' + _NUM + r'\s*$')                # "200" or "1.5K"


def est(v, lo, hi, basis, conf):
    """An estimated number with its range, where it came from and how sure we are."""
    return {'v': v, 'lo': lo, 'hi': hi, 'basis': basis, 'conf': conf}


def _finite(x):
    """x as a float, or None for None / NaN / inf / text / bools."""
    if x is None or isinstance(x, bool):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _to_number(digits, has_suffix):
    """'1,000' -> 1000, '1.5' -> 1.5, '1,5' -> 1.5 (comma decimal), '1.000' -> 1000 (dot thousands)."""
    parts = re.split(r'[.,]', digits.strip('.,'))
    if len(parts) == 1:
        return float(parts[0])
    head, last = parts[:-1], parts[-1]
    if not has_suffix and len(last) == 3 and all(len(p) == 3 for p in head[1:]):
        return float(''.join(parts))                       # every separator groups thousands
    return float(''.join(head) + '.' + last)               # last separator is the decimal point


def _market(market):
    m = str(market or 'US').upper()
    return m if m in LADDER else 'US'


def _int_if_whole(x):
    return int(x) if float(x).is_integer() else float(x)


def parse_badge(text):
    """'5K+ bought in past month' -> 5000, '200+' -> 200, '1.5K+' -> 1500. None when there is no badge."""
    if isinstance(text, (int, float, np.integer, np.floating)) and not isinstance(text, bool):
        f = _finite(text)
        return int(round(f)) if f is not None and f > 0 else None
    if text is None or isinstance(text, bool):
        return None
    s = str(text).translate(_ARABIC_DIGITS).replace('＋', '+').replace('\xa0', ' ')
    m = _RE_BOUGHT.search(s) or _RE_PLUS.search(s) or _RE_BARE.search(s)
    if not m:
        return None
    suffix = (m.group(2) or '').lower()
    try:
        n = _to_number(m.group(1), bool(suffix)) * _MULT.get(suffix, 1)
    except ValueError:
        return None
    n = int(round(n))
    return n if n > 0 else None


def bucket(L, market='US'):
    """(L, U): the badge range [L, U), U being the next ladder label. U is None above the top label or
    for a value that is not on the ladder. A label string is parsed first. (None, None) for no badge."""
    if isinstance(L, str):
        L = parse_badge(L)
    f = _finite(L)
    if f is None or f <= 0:
        return (None, None)
    L = _int_if_whole(f)
    ladder = LADDER[_market(market)]
    if L in ladder:
        i = ladder.index(L)
        return (L, ladder[i + 1] if i + 1 < len(ladder) else None)
    return (L, None)


def pareto_mean(L, U):
    """Mean of a Pareto (theta 1) between L and U: L*U*ln(U/L)/(U-L). 50 -> 69.3, 1000 -> 1386.3.

    U None means the open top bucket, which uses 1.5 L (theta 3, an assumption). None for bad input."""
    L, U = _finite(L), _finite(U)
    if L is None or L <= 0:
        return None
    if U is None:
        return THRESH['open_mean_mult'] * L
    if U <= L:
        return L if U == L else None
    return L * U * math.log(U / L) / (U - L)


def _conf_from_ratio(lo, hi):
    if not lo or lo <= 0 or hi is None:
        return 'low'
    r = hi / lo
    return 'high' if r < THRESH['conf_high_ratio'] else 'medium' if r < THRESH['conf_medium_ratio'] else 'low'


def badge_estimate(L, market='US'):
    """Monthly units behind one badge as an est dict: v = bucket mean, lo = L, hi = U-1.

    The open top bucket (or a value off the ladder) gets hi = 2 L and basis 'assumed'. None for no badge."""
    L, U = bucket(L, market)
    if L is None:
        return None
    v = pareto_mean(L, U)
    if U is None:
        hi = _int_if_whole(THRESH['open_hi_mult'] * L)
        return est(round(v, 1), L, hi, 'assumed', 'low')
    return est(round(v, 1), L, U - 1, 'estimated', _conf_from_ratio(L, U - 1))


def _log_interval(a, b):
    """log(Phi(b) - Phi(a)) for a < b, stable deep in either tail. a may be -inf, b may be +inf."""
    if not b > a:
        return -math.inf
    if a > 0:                                   # upper tail: mirror into the lower tail
        a, b = -b, -a
    lp = lambda z: float(log_Phi(np.array([z], dtype=float))[0])
    if b == math.inf:
        return lp(-a) if a > -math.inf else 0.0
    if a == -math.inf:
        return lp(b)
    if b <= 0:
        lb, la = lp(b), lp(a)
        d = la - lb
        return lb + math.log1p(-math.exp(d)) if d < 0 else -math.inf
    p = float(Phi(b)) - float(Phi(a))           # straddles 0: the plain difference is accurate
    return math.log(p) if p > 0 else -math.inf


def truncated_lognormal_mean(mu, sigma, L, U):
    """E[S | L <= S < U] for S ~ lognormal(mu, sigma). U None means open above, L None or 0 open below.

    exp(mu + s^2/2) * [Phi(zU - s) - Phi(zL - s)] / [Phi(zU) - Phi(zL)], worked in logs so buckets far
    in the tails stay accurate. The answer is kept inside [L, U]. None for bad input."""
    mu, sigma = _finite(mu), _finite(sigma)
    lo = _finite(L) if L is not None else 0.0
    hi = _finite(U) if U is not None else math.inf
    if mu is None or sigma is None or sigma < 0 or lo is None or hi is None or lo < 0 or hi <= lo:
        return None
    if sigma < 1e-9:                            # no spread: the limit is exp(mu) pushed into the bucket
        return float(min(max(math.exp(min(mu, 700.0)), lo), hi))
    a = (math.log(lo) - mu) / sigma if lo > 0 else -math.inf
    b = (math.log(hi) - mu) / sigma if hi < math.inf else math.inf
    den = _log_interval(a, b)
    num = _log_interval(a - sigma, b - sigma)
    if math.isfinite(den) and math.isfinite(num):
        log_m = mu + 0.5 * sigma * sigma + num - den
        if log_m < 700:
            return float(min(max(math.exp(log_m), lo), hi))
        return float(hi) if hi < math.inf else None    # too large for a float
    # numbers ran out of range: fall back to the side of the bucket nearest the distribution
    if hi == math.inf:
        return float(lo) if lo > 0 else None
    if lo == 0:
        return float(hi)
    return float(lo) if a > 0 else float(hi) if b < 0 else pareto_mean(lo, hi)


def page_demand(badge_values, market='US'):
    """Page-1 demand from the badged listings: sums of bucket lows, means and highs.

    badge_values are badge numbers (or labels); None means no badge and is skipped. Listings without
    a badge are left out, so the total is an undercount. Sums are None when no listing has a badge.
    This replaces the old bought_total, which added up the lower bounds only."""
    rows = []                                   # (lo, mid, hi, open bucket?)
    for val in badge_values or []:
        L, U = bucket(val, market)
        if L is None:
            continue
        hi = U - 1 if U is not None else _int_if_whole(THRESH['open_hi_mult'] * L)
        rows.append((L, pareto_mean(L, U), hi, U is None))
    out = {'badged': len(rows), 'low_sum': None, 'mid_sum': None, 'high_sum': None, 'top': None, 'est': None,
           'open': sum(1 for r in rows if r[3])}
    if not rows:
        return out
    low = _int_if_whole(sum(r[0] for r in rows))
    mid = sum(r[1] for r in rows)
    high = _int_if_whole(sum(r[2] for r in rows))
    open_mid = sum(r[1] for r in rows if r[3])
    conf = 'low' if mid > 0 and open_mid / mid > THRESH['page_open_share_low'] else 'medium'
    out.update(low_sum=low, mid_sum=round(mid, 1), high_sum=high, top=max(r[0] for r in rows),
               est=est(round(mid, 1), low, high, 'estimated', conf))
    return out
