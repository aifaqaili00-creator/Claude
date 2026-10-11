"""Launch planner (synth.md 8.10, as changed by the plan): when to order so stock is in before the season.

- plan: works back from the seasonal upswing to sea and air order-by dates. Uses the market's own
  seasonal index only when it is reliable; there is no hemisphere shift.
- order_quantity: units to order for a selling window, share x forecast plus one sd of safety stock.
- season_months: the months of the high season, for order_quantity's window.

Pure functions (today's date is passed in), numpy only, JSON-safe output (no NaN).
"""
import math
import re
from datetime import date, datetime, timedelta, timezone

import numpy as np

# Tunable thresholds.
THRESH = {
    'si_up': 1.0,                                   # a month is in the season when SI >= 1.0
    'ramp_days': 45,                                # be in stock this long before the upswing starts
    'check_in': {'US': 10, 'AU': 7, 'AE': 7},       # days for Amazon to receive the stock
    'check_in_q4': {'US': 21, 'AU': 7, 'AE': 7},    # ... when the in-stock date falls in Oct-Dec
    'q4_months': (10, 11, 12),
    'transit': {'sea': {'US': 40, 'AU': 30, 'AE': 28}, 'air': {'US': 10, 'AU': 10, 'AE': 10}},
    'production_days': 30,
    'buffer_days': 7,
    'storage_peak_months': (10, 11, 12),            # storage costs more in these months
    'storage_peak_markets': ('US', 'AU'),           # AE storage is one flat rate
    'max_years_ahead': 3,                           # look at most this many seasons ahead
    # order quantity
    'share': 0.1,                                   # default share of the niche's units
    'z80': 1.2816,                                  # an est band is the 80% range: sd = (hi - lo) / (2 * 1.2816)
    'plain_rel_sd': 0.25,                           # sd of a forecast given as a plain number (assumed)
    'safety_sd': 1.0,                               # Q = share * sum(units) + 1 sd
    'conf_high_ratio': 2.5,
    'conf_medium_ratio': 5.0,
}

MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
MODES = ('sea', 'air')
_CONF_ORDER = ['low', 'medium', 'high']
_RE_DATE = re.compile(r'^\s*(\d{4})-(\d{1,2})-(\d{1,2})')
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


def _market(m):
    m = str(m or 'US').strip().upper()
    m = 'AE' if m == 'UAE' else m
    return m if m in THRESH['check_in'] else 'US'


def _date(x):
    """A date from 'YYYY-MM-DD' (time part ignored), a date / datetime / Timestamp or unix seconds."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, datetime):
        try:
            return None if x != x else x.date()     # NaT
        except (TypeError, ValueError):
            return None
    if isinstance(x, date):
        return x
    if isinstance(x, str):
        m = _RE_DATE.match(x)
        if not m:
            return None
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    t = _finite(x)
    if t is not None and t > 0:
        if t > 1e11:
            t /= 1000.0
        try:
            return datetime.fromtimestamp(t, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    if hasattr(x, 'year') and hasattr(x, 'month') and hasattr(x, 'day'):
        try:
            return date(int(x.year), int(x.month), int(x.day))
        except (TypeError, ValueError):
            return None
    return None


def _si(si):
    """12 finite seasonal index values, or None. Takes a list or a seasonality() dict."""
    if isinstance(si, dict):
        si = si.get('si')
    if si is None or isinstance(si, (str, bytes)):
        return None
    try:
        vals = [_finite(v) for v in si]
    except TypeError:
        return None
    if len(vals) != 12 or any(v is None for v in vals):
        return None
    return np.array(vals, dtype=float)


def _month_num(x):
    v = _finite(x)
    if v is None or v != int(v) or not 1 <= int(v) <= 12:
        return None
    return int(v)


def _mname(m):
    return MONTHS[m - 1] if m and 1 <= m <= 12 else '?'


def _fmt(d):
    """2026-11-03 -> '3 Nov 2026'."""
    return '%d %s %d' % (d.day, _mname(d.month), d.year)


def _days(x, default):
    v = _finite(x)
    return int(round(v)) if v is not None and v >= 0 else default


def _times(mk, mode_times):
    """Lead times per mode: {'sea': {'ramp', 'check_in', 'check_in_q4', 'transit', 'production', 'buffer'}}.

    mode_times may override: {'sea': 35, 'air': 7} (transit days), {'sea': {'transit', 'check_in',
    'production', 'buffer', 'ramp'}}, or top-level 'production' / 'buffer' / 'ramp' / 'check_in' for both.
    """
    mt = mode_times if isinstance(mode_times, dict) else {}
    out = {}
    for mode in MODES:
        t = {'ramp': THRESH['ramp_days'], 'check_in': THRESH['check_in'][mk],
             'check_in_q4': THRESH['check_in_q4'][mk], 'transit': THRESH['transit'][mode][mk],
             'production': THRESH['production_days'], 'buffer': THRESH['buffer_days']}
        for k in ('ramp', 'production', 'buffer'):
            t[k] = _days(mt.get(k), t[k])
        if mt.get('check_in') is not None:
            t['check_in'] = t['check_in_q4'] = _days(mt.get('check_in'), t['check_in'])
        own = mt.get(mode)
        if isinstance(own, dict):
            for k in ('ramp', 'transit', 'production', 'buffer'):
                t[k] = _days(own.get(k), t[k])
            if own.get('check_in') is not None:
                t['check_in'] = t['check_in_q4'] = _days(own.get('check_in'), t['check_in'])
        else:
            t['transit'] = _days(own, t['transit'])
        out[mode] = t
    return out


def upswing_month(si, peak_month):
    """First month of the contiguous SI >= 1.0 run that leads into the peak (wrapping round the year).
    The peak itself when SI at the peak is below 1.0."""
    a = _si(si)
    pk = _month_num(peak_month)
    if a is None or pk is None:
        return None
    up = pk
    for _ in range(11):
        prev = 12 if up == 1 else up - 1
        if a[prev - 1] < THRESH['si_up'] or prev == pk:
            break
        up = prev
    return up


def _season_end(a, pk):
    """Last month of the contiguous SI >= 1.0 run after the peak."""
    end = pk
    for _ in range(11):
        nxt = 1 if end == 12 else end + 1
        if a[nxt - 1] < THRESH['si_up'] or nxt == pk:
            break
        end = nxt
    return end


def _months_between(y1, m1, y2, m2):
    """['YYYY-MM', ...] from (y1, m1) to (y2, m2) inclusive."""
    out = []
    i, j = y1 * 12 + m1 - 1, y2 * 12 + m2 - 1
    while i <= j and len(out) < 36:
        out.append('%04d-%02d' % (i // 12, i % 12 + 1))
        i += 1
    return out


def _dates(up_start, mk, times, today):
    """Back-scheduled dates for one mode: in stock 45 d before the upswing, minus check-in and transit
    to ship, minus production and buffer to order."""
    in_stock = up_start - timedelta(days=times['ramp'])
    ci = times['check_in_q4'] if in_stock.month in THRESH['q4_months'] else times['check_in']
    ship = in_stock - timedelta(days=ci + times['transit'])
    order = ship - timedelta(days=times['production'] + times['buffer'])
    return {'order_by': order.isoformat(), 'ship_by': ship.isoformat(), 'in_stock_by': in_stock.isoformat(),
            'days_left': (order - today).days, 'check_in_days': ci, 'transit_days': times['transit'],
            'production_days': times['production'], 'buffer_days': times['buffer'],
            'ramp_days': times['ramp'], 'lead_days': (up_start - order).days}


def _season(a, pk, up, year, mk, times, today):
    """Dates for the season whose peak is in `year`."""
    uy = year if up <= pk else year - 1
    up_start = date(uy, up, 1)
    out = {'peak': '%04d-%02d' % (year, pk), 'upswing_start': up_start.isoformat()}
    for mode in MODES:
        out[mode] = _dates(up_start, mk, times[mode], today)
    end = _season_end(a, pk)
    ey = year if end >= pk else year + 1
    out['season_months'] = _months_between(uy, up, ey, end)
    return out


def _unknown(mk, note, strength=None):
    return {'status': 'unknown', 'market': mk, 'peak_month': None, 'upswing_month': None, 'sea': None,
            'air': None, 'note': note, 'too_late': False, 'late_by_days': None, 'warnings': [],
            'strength': _r(strength)}


def _r(x, nd=3):
    f = _finite(x)
    return None if f is None else round(f, nd)


def _storage_warning(mk, s):
    """Stock that sits in Amazon through Oct-Dec pays the higher storage rate."""
    if mk not in THRESH['storage_peak_markets']:
        return None
    d = date.fromisoformat(s['sea']['in_stock_by'])
    pk_y, pk_m = int(s['peak'][:4]), int(s['peak'][5:7])
    held = _months_between(d.year, d.month, pk_y, pk_m)
    if any(int(m[5:7]) in THRESH['storage_peak_months'] for m in held):
        return 'Stock sits in Amazon during Oct-Dec, when storage costs more.'
    return None


# ---------- plan ----------

def plan(si, strength, reliable, peak_month, today_str, mode_times=None, market='US'):
    """Order-by dates for the next seasonal peak, by sea and by air.

    si: 12 seasonal index values (mean 1) or a trendfeat.seasonality() dict. Used only when `reliable`;
    otherwise status 'unknown'. peak_month 1-12 (the SI maximum when missing). today_str 'YYYY-MM-DD'.

    Upswing = first month of the contiguous SI >= 1.0 run leading into the peak. Working back from the
    first day of that month: in stock 45 d before, ship check-in (US 10 d, 21 d when the in-stock date
    is in Oct-Dec; AU / AE 7 d) + transit (sea US 40 / AU 30 / AE 28, air 10) earlier, order production
    30 d + buffer 7 d before that.

    The season is the current or next peak (its month not yet over). Status:
      'ok'        the sea order-by date is today or later
      'too_late'  sea has passed ('too late by N days'), the air date still works
      'next_year' both have passed; sea / air hold the next season's dates, 'missed' this season's
      'unknown'   seasonality not reliable, or no usable SI / date
    Returns {'status', 'market', 'peak_month', 'upswing_month', 'sea': {'order_by', 'ship_by',
    'in_stock_by', 'days_left', ...}, 'air': {...}, 'note', 'too_late', 'late_by_days', 'peak',
    'upswing_start', 'season_months', 'warnings', 'strength'}.
    """
    mk = _market(market)
    if not reliable or (isinstance(reliable, str) and reliable.strip().lower() in ('false', '0', 'no', '')):
        return _unknown(mk, 'Seasonality unknown for this market', strength)
    a = _si(si)
    if a is None:
        return _unknown(mk, 'Seasonality unknown for this market', strength)
    today = _date(today_str)
    if today is None:
        return _unknown(mk, "Today's date is missing", strength)
    pk = _month_num(peak_month) or int(np.argmax(a)) + 1
    up = upswing_month(a, pk)
    times = _times(mk, mode_times)

    year = today.year if pk >= today.month else today.year + 1
    cur = _season(a, pk, up, year, mk, times, today)
    sea_left, air_left = cur['sea']['days_left'], cur['air']['days_left']
    out = {'status': 'ok', 'market': mk, 'peak_month': pk, 'upswing_month': up, 'too_late': False,
           'late_by_days': None, 'warnings': [], 'strength': _r(strength)}
    pk_txt = '%s %d' % (_mname(pk), year)
    if sea_left >= 0:
        s = cur
        sea = s['sea']
        note = ('Order by %s to ship by sea on %s and be in stock by %s, ahead of the %s upswing '
                '(peak %s).' % (_fmt(date.fromisoformat(sea['order_by'])), _fmt(date.fromisoformat(sea['ship_by'])),
                                _fmt(date.fromisoformat(sea['in_stock_by'])), _mname(up), pk_txt))
    elif air_left >= 0:
        s = cur
        out.update(status='too_late', too_late=True, late_by_days=-sea_left)
        air = s['air']
        note = ('Too late for sea freight by %d day%s. Order by %s and ship by air on %s to be in stock by %s '
                'for the %s peak.' % (-sea_left, '' if sea_left == -1 else 's',
                                      _fmt(date.fromisoformat(air['order_by'])), _fmt(date.fromisoformat(air['ship_by'])),
                                      _fmt(date.fromisoformat(air['in_stock_by'])), pk_txt))
    else:
        s = cur
        for k in range(1, THRESH['max_years_ahead'] + 1):
            s = _season(a, pk, up, year + k, mk, times, today)
            if s['sea']['days_left'] >= 0:
                break
        out.update(status='next_year', too_late=True, late_by_days=-sea_left,
                   missed={'peak': cur['peak'], 'sea': cur['sea'], 'air': cur['air']})
        nxt_pk = int(s['peak'][:4])
        note = ('Too late for the %s peak by %d days (sea). Next season (peak %s %d): order by %s by sea '
                'or %s by air.' % (pk_txt, -sea_left, _mname(pk), nxt_pk,
                                   _fmt(date.fromisoformat(s['sea']['order_by'])),
                                   _fmt(date.fromisoformat(s['air']['order_by']))))
    out.update(sea=s['sea'], air=s['air'], peak=s['peak'], upswing_start=s['upswing_start'],
               season_months=s['season_months'], note=note)
    w = _storage_warning(mk, s)
    if w:
        out['warnings'].append(w)
    return out


def season_months(plan_result):
    """The high-season months ('YYYY-MM') of a plan, for order_quantity's window. [] when unknown."""
    if not isinstance(plan_result, dict):
        return []
    sm = plan_result.get('season_months')
    return list(sm) if isinstance(sm, (list, tuple)) else []


# ---------- order quantity ----------

def _month_key(x):
    """'YYYY-MM' from a string, Timestamp, datetime or date; None when unreadable."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, str):
        m = _RE_MONTH.match(x)
        if not m or not 1 <= int(m.group(2)) <= 12:
            return None
        return '%04d-%02d' % (int(m.group(1)), int(m.group(2)))
    if hasattr(x, 'year') and hasattr(x, 'month'):
        try:
            if x != x:                              # NaT
                return None
            return '%04d-%02d' % (int(x.year), int(x.month))
        except (TypeError, ValueError):
            return None
    return None


def _month_items(x):
    """[(month, value)] sorted by month, from a dict, a pandas Series or a list of (month, value)."""
    if x is None:
        return []
    if hasattr(x, 'items') and callable(x.items):
        pairs = list(x.items())
    elif isinstance(x, (list, tuple)):
        pairs = [p for p in x if isinstance(p, (list, tuple)) and len(p) == 2]
    else:
        return []
    out = {}
    for k, v in pairs:
        mk = _month_key(k)
        if mk is not None:
            out[mk] = v
    return sorted(out.items())


def _parts(x):
    """(v, sd, basis, conf) from an est dict (sd from its 80% band) or a plain number (25% sd, assumed)."""
    if isinstance(x, dict):
        v = _finite(x.get('v'))
        if v is None or v < 0:
            return None
        lo, hi = _finite(x.get('lo')), _finite(x.get('hi'))
        if lo is not None and hi is not None and hi >= lo:
            sd = (hi - lo) / (2 * THRESH['z80'])
        else:
            sd = THRESH['plain_rel_sd'] * v
        conf = x.get('conf') if x.get('conf') in _CONF_ORDER else None
        return v, sd, x.get('basis'), conf
    v = _finite(x)
    if v is None or v < 0:
        return None
    return v, THRESH['plain_rel_sd'] * v, 'assumed', None


def _conf(lo, hi):
    if lo is None or hi is None or lo <= 0:
        return 'low'
    r = hi / lo
    return 'high' if r < THRESH['conf_high_ratio'] else 'medium' if r < THRESH['conf_medium_ratio'] else 'low'


def order_quantity(units_forecast_by_month, window_months, share=0.1):
    """Units to order for a selling window: Q = share * sum(units) + 1 * sqrt(sum var), as an est dict.

    units_forecast_by_month: {month: est dict | number} (e.g. sales.forecast output).
    window_months: a list of 'YYYY-MM', a plan() result (its season months), a number N (the first N
    forecast months) or None (every month given).
    share: the share of the niche's units you expect to take (0..1; 1-100 read as a percentage).
    The variance is that of share x units, from each month's 80% band (a plain number gets a 25% sd).
    lo / hi are the 80% band of the units sold in the window. Extra keys: 'months', 'missing', 'mean',
    'safety', 'share'. None when no forecast month falls in the window.
    """
    items = _month_items(units_forecast_by_month)
    if not items:
        return None
    sh = _finite(share)
    if sh is None:
        sh = THRESH['share']
    if 1.0 < sh <= 100.0:
        sh /= 100.0
    sh = max(0.0, min(1.0, sh))

    fc = dict(items)
    if isinstance(window_months, dict):
        window_months = season_months(window_months)
    n = _finite(window_months) if not isinstance(window_months, (list, tuple, set)) else None
    if window_months is None:
        want = [m for m, _ in items]
    elif n is not None:
        want = [m for m, _ in items][:max(0, int(n))]
    elif isinstance(window_months, (list, tuple, set)):
        want = sorted({k for k in (_month_key(m) for m in window_months) if k is not None})
    else:
        want = []
    used, missing = [], []
    mean, var = 0.0, 0.0
    bases, confs = set(), []
    for m in want:
        p = _parts(fc.get(m)) if m in fc else None
        if p is None:
            missing.append(m)
            continue
        v, sd, basis, conf = p
        used.append(m)
        mean += sh * v
        var += (sh * sd) ** 2
        bases.add(basis)
        if conf:
            confs.append(conf)
    if not used:
        return None
    sd_tot = math.sqrt(var)
    q = mean + THRESH['safety_sd'] * sd_tot
    lo = max(0.0, mean - THRESH['z80'] * sd_tot)
    hi = mean + THRESH['z80'] * sd_tot
    v = int(math.ceil(q - 1e-9))
    lo_i, hi_i = int(math.floor(lo)), max(v, int(math.ceil(hi - 1e-9)))
    basis = 'forecast' if 'forecast' in bases else 'assumed' if bases == {'assumed'} else 'estimated'
    conf = min(confs + [_conf(lo, hi)], key=_CONF_ORDER.index)
    if missing:
        conf = min([conf, 'medium'], key=_CONF_ORDER.index)
    out = est(v, lo_i, hi_i, basis, conf)
    out.update(months=used, missing=missing, mean=round(mean, 1), safety=round(THRESH['safety_sd'] * sd_tot, 1),
               share=round(sh, 4))
    return out
