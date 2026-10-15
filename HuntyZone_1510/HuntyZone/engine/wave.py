"""Wave forecast: when will a product whose searches are rising in one market reach another?

The rise start (onset) of each market's Google Trends series, the usual lag between markets learned from the products
that rose in both, and the day Amazon's search suggestions there start to know the phrase (shoppers type it).
Monthly resolution, like trends.compute. Honest about gaps: no date before three products have risen in both
markets. Pure functions; JSON-safe output.
"""
import math
import statistics

THRESH = {
    'min_months': 18,          # months of history before a rise can be dated
    'base_from': 36,           # base level = median 3-month mean from 36 to 12 months ago
    'base_to': 12,
    'recent': 6,               # the rise peaks within the last 6 months
    'rise': 2.0,               # rising when that peak is at least 2x the base
    'min_level': 15.0,         # and at least 15 on Google's 0-100 index
    'onset_share': 0.25,       # the rise started after the last month at or below base + 25% of the rise
    'min_pairs': 3,            # products that rose in both markets before a lag is trusted
    'lag_lo': -6,              # lags outside these months are noise
    'lag_hi': 24,
}
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _series(monthly):
    """[('YYYY-MM', value)] sorted, from trends.compute's monthly [[month, value], ...]."""
    out = []
    for p in monthly or ():
        if isinstance(p, (list, tuple)) and len(p) >= 2 and isinstance(p[0], str) and len(p[0]) == 7:
            v = _num(p[1])
            if v is not None:
                out.append((p[0], v))
    return sorted(out)


def add_months(key, k):
    y, m = int(key[:4]), int(key[5:7]) - 1 + int(k)
    return '%04d-%02d' % (y + m // 12, m % 12 + 1)


def months_between(a, b):
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])


def label(key):
    return '%s %s' % (MONTHS[int(key[5:7]) - 1], key[:4])


def onset(monthly):
    """The month ('YYYY-MM') the current rise of a search series started, or None when it is not rising now."""
    m = _series(monthly)
    if len(m) < THRESH['min_months']:
        return None
    vals = [v for _, v in m]
    s = [sum(vals[max(0, i - 2):i + 1]) / len(vals[max(0, i - 2):i + 1]) for i in range(len(vals))]
    n = len(s)
    window = s[max(0, n - THRESH['base_from']):n - THRESH['base_to']]
    if len(window) < 3:
        return None
    base = statistics.median(window)
    recent = s[-THRESH['recent']:]
    peak = max(recent)
    if peak < THRESH['min_level'] or peak < THRESH['rise'] * max(base, 1.0):
        return None
    cut = base + THRESH['onset_share'] * (peak - base)
    top = n - len(recent) + recent.index(peak)
    low = [k for k in range(max(0, n - THRESH['base_from']), top) if s[k] <= cut]
    return m[low[-1] + 1][0] if low else None


def lag_months(src_monthly, dst_monthly):
    """Months from the rise in one market to the rise in another, or None unless both rose."""
    a, b = onset(src_monthly), onset(dst_monthly)
    return months_between(a, b) if a and b else None


def typical_lag(lags):
    """{'months', 'lo', 'hi', 'n'} (median and quartiles) from the lags of products that rose in both markets, or None
    with fewer than THRESH['min_pairs']."""
    xs = sorted(int(x) for x in (lags or ()) if _num(x) is not None and THRESH['lag_lo'] <= x <= THRESH['lag_hi'])
    if len(xs) < THRESH['min_pairs']:
        return None
    q = lambda p: xs[min(len(xs) - 1, max(0, round(p * (len(xs) - 1))))]      # noqa: E731
    return {'months': int(round(statistics.median(xs))), 'lo': q(0.25), 'hi': q(0.75), 'n': len(xs)}


def forecast(src_monthly, dst_monthly, arrival=None, typical=None, today=None, names=('US', 'there')):
    """When the rise reaches the other market. arrival: {'first_known': 'YYYY-MM-DD' or None, 'known_now'} from
    Amazon's suggestions there; typical: typical_lag(); today: 'YYYY-MM-DD'; names: (source, target) market names.
    {'status': 'here' | 'coming' | 'due' | 'early' | 'unknown', 'eta': [from, to] or None, 'src_onset', 'dst_onset',
    'why'}."""
    src, dst = names
    a, b = onset(src_monthly), onset(dst_monthly)
    arrival = arrival if isinstance(arrival, dict) else {}
    out = {'status': 'unknown', 'eta': None, 'src_onset': a, 'dst_onset': b, 'why': ''}
    if b:
        out.update(status='here', why='searches in %s have been rising since %s' % (dst, label(b)))
    elif arrival.get('known_now'):
        first = arrival.get('first_known')
        out.update(status='here', why='Amazon suggests it to shoppers in %s%s' % (dst, ' since %s' % first if first else ''))
    elif a and typical:
        eta = [add_months(a, typical['lo']), add_months(a, typical['hi'])]
        now = (today or '')[:7]
        span = label(eta[0]) if eta[0] == eta[1] else '%s to %s' % (label(eta[0]), label(eta[1]))
        why = '%s searches took off in %s; %s usually follows %d month%s later (%d products rose in both)' % (
            src, label(a), dst, typical['months'], '' if typical['months'] == 1 else 's', typical['n'])
        if now and now > eta[1]:
            out.update(status='due', eta=eta, why=why + ': expected by %s and not seen yet' % label(eta[1]))
        else:
            out.update(status='coming', eta=eta, why=why + ': expected %s' % span)
    elif a:
        out.update(status='early', why='%s searches took off in %s and are not rising in %s yet (the usual lag is learned '
                                       'once %d products have risen in both)' % (src, label(a), dst, THRESH['min_pairs']))
    return out
