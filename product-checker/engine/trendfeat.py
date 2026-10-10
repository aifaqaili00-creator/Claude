"""Google Trends features: weekly points to calendar months, sample consensus, trend features,
a trend label with reasons, a 0-100 trend score and the seasonal index (pure functions, numpy/pandas only).

Trends values are relative (0-100 within one fetch), so every level rule below assumes that scale.
Features only use months from 2022-01 onward, because Google changed its data collection on 1 Jan 2022.
"""
import math

import numpy as np
import pandas as pd

from engine.stats import clip, ols_slope, sigmoid

# Tunable thresholds (synth.md 8.5). Levels are on the Trends 0-100 scale.
THRESH = {
    # data handling
    'min_cover_days': 28,          # a month needs this many covered days to be kept
    'zero_level': 1.0,             # a month below this counts as "no searches" (Google shows 0 or <1)
    'q_single': 0.7,               # q_T when only one sample exists
    'yoy_clip': (-0.95, 5.0),
    'cagr_min_months': 48,
    'si_floor': 0.05,              # never divide by a seasonal index below this
    'launch_lead': 3,              # launch this many months before the seasonal peak
    # 1. Insufficient
    'min_months': 18,
    'max_zero_share': 0.3,
    'min_mean_last12': 5.0,
    # 2. Fad
    'fad_spikiness': 0.35,
    'fad_peak_width': 3,
    'fad_cur_vs_peak': 0.4,
    'fad_months_since_peak': 6,
    # 3. Spike (watch)
    'spike_ratio': 3.0,
    'spike_months_since_peak': 1,
    # 4. Breakout (on top of Emerging)
    'breakout_first12': 10.0,
    'breakout_ratio': 3.0,
    # 5. Emerging
    'emerging_yoy': 0.30,
    'emerging_cur_vs_peak': 0.7,
    'emerging_months_since_peak': 3,
    'emerging_peak_width': 3,
    # 6. Growing
    'growing_cagr': 0.10,
    'growing_yoy': 0.10,
    'growing_m12': 0.10,           # stands in for CAGR when there are fewer than 48 months
    # 7. Seasonal (also the threshold for the ", seasonal peak Mon" modifier)
    'seasonal_fs': 0.6,
    'seasonal_max_abs_yoy': 0.25,
    # 8. Declining
    'declining_yoy': -0.20,
    # 9. Evergreen
    'evergreen_cv': 0.2,
    'evergreen_max_abs_yoy': 0.15,
    # seasonality()
    'si_reliable_fs': 0.3,
    'si_min_months': 24,
    # trend_score()
    'score_yoy_scale': 0.3,
    'score_m12_scale': 0.3,
    'fad_spike_scale': 0.35,
    'fad_drop_scale': 0.6,
    'score_q_default': 0.5,        # q_T used when the caller has none
}

LABELS = ['Insufficient', 'Fad', 'Spike (watch)', 'Breakout', 'Emerging', 'Growing', 'Seasonal', 'Declining',
          'Evergreen', 'Mixed']
MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
FEATURE_KEYS = ['YoY', 'YoY_recent', 'CAGR', 'CMA', 'SI', 'Fs', 'd', 'm12', 'short_mom', 'accel',
                'months_since_peak', 'cur_vs_peak', 'spikiness', 'peak_width', 'half_life', 'zero_share', 'CV_d',
                'peak_month', 'launch_month', 'n_months', 'mean_last12', 'mean_first12', 'sum_last12', 'sum_prev12',
                'last', 'median_prev12', 'peak_at', 'months', 'start', 'end']
_MODIFIABLE = ('Breakout', 'Emerging', 'Growing', 'Declining', 'Mixed')


# ---------- small helpers ----------

def _f(x, nd=4):
    """Round a number for JSON; None for missing, NaN or inf."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, nd) if math.isfinite(x) else None


def _num(v):
    """A Trends value as float ('<1' -> 0.5); NaN when unusable."""
    if v is None or isinstance(v, bool):
        return float('nan')
    if isinstance(v, (list, tuple)):                    # multiline rows carry one value per term
        return _num(v[0]) if v else float('nan')
    if isinstance(v, str):
        v = v.strip().replace(',', '')
        if v.startswith('<'):
            return 0.5
    try:
        v = float(v)
    except (TypeError, ValueError):
        return float('nan')
    return v if math.isfinite(v) else float('nan')


def _truthy(v):
    if isinstance(v, str):
        return v.strip().lower() in ('true', '1', 'yes', 'y')
    return bool(v)


def _empty_series(name=None):
    return pd.Series([], index=pd.DatetimeIndex([], name='month').astype('datetime64[ns]'), dtype=float, name=name)


def _month_index(keys):
    """Month-start DatetimeIndex from 'YYYY-MM' strings, Timestamps, Periods or dates (NaT when unreadable)."""
    out = []
    for k in keys:
        if isinstance(k, np.datetime64):
            k = pd.Timestamp(k)
        if hasattr(k, 'strftime'):                       # Timestamp, datetime, date, Period
            try:
                out.append(k.strftime('%Y-%m'))
            except (ValueError, TypeError):              # NaT
                out.append('')
        else:
            out.append(str(k)[:7])
    return pd.DatetimeIndex(pd.to_datetime(out, format='%Y-%m', errors='coerce')).astype('datetime64[ns]')


def _as_series(x):
    """Monthly Series (month-start index, float values, sorted, no NaN) from a Series, dict or (month, v) pairs."""
    if x is None:
        return _empty_series()
    try:
        if isinstance(x, pd.Series):
            keys, vals = list(x.index), list(x.values)
        elif isinstance(x, dict):
            keys, vals = list(x.keys()), list(x.values())
        else:
            pairs = [p for p in x if isinstance(p, (list, tuple)) and len(p) >= 2]
            keys, vals = [p[0] for p in pairs], [p[1] for p in pairs]
    except TypeError:
        return _empty_series()
    if not keys:
        return _empty_series()
    s = pd.Series([_num(v) for v in vals], index=_month_index(keys), dtype=float)
    s = s[s.index.notna() & s.notna()]
    if not len(s):
        return _empty_series()
    s = s.groupby(level=0).mean().sort_index()
    s.index = pd.DatetimeIndex(s.index, name='month').astype('datetime64[ns]')
    return s


def _contiguous(s):
    """Fill missing months inside the range by straight-line interpolation."""
    if len(s) < 2:
        return s
    full = pd.date_range(s.index.min(), s.index.max(), freq='MS')
    out = s.reindex(full).interpolate(limit_area='inside')
    out.index = pd.DatetimeIndex(out.index, name='month').astype('datetime64[ns]')
    return out.dropna()


def _pct(x):
    """0.46 -> '+46%'."""
    return '%+d%%' % round(100 * x)


def _mname(m):
    return MONTH_NAMES[(int(m) - 1) % 12] if m else '?'


def _ym_label(ym):
    """'2023-09' -> 'Sep 2023'."""
    try:
        return '%s %s' % (MONTH_NAMES[int(ym[5:7]) - 1], ym[:4])
    except (TypeError, ValueError, IndexError):
        return str(ym)


# ---------- weekly points -> calendar months ----------

def _resolution(res, days):
    """'WEEK' | 'DAY' | 'MONTH' from the given resolution or the median gap between points."""
    r = str(res or '').strip().upper()
    if r.startswith('W'):
        return 'WEEK'
    if r.startswith('D') or r.startswith('H') or r.startswith('MIN'):
        return 'DAY'                                     # finer than a day is averaged per day
    if r.startswith('M'):
        return 'MONTH'
    if len(days) < 2:
        return 'WEEK'
    gap = float(np.median(np.diff(np.sort(days))))
    return 'DAY' if gap <= 1.5 else 'WEEK' if gap <= 10 else 'MONTH'


def _parse_points(points):
    """[[ts, value, partial], ...] (or dicts) -> (ts float array, value float array), partial points dropped."""
    ts, vals = [], []
    for p in points or []:
        if isinstance(p, dict):
            t = p.get('ts', p.get('time'))
            v = p.get('value', p.get('v'))
            part = p.get('partial', p.get('isPartial', False))
        elif isinstance(p, (list, tuple)) and len(p) >= 2:
            t, v = p[0], p[1]
            part = p[2] if len(p) > 2 else False
        else:
            continue
        if _truthy(part):
            continue
        try:
            t = float(t)
        except (TypeError, ValueError):
            continue
        v = _num(v)
        if math.isfinite(t) and math.isfinite(v):
            ts.append(t)
            vals.append(v)
    return np.asarray(ts, dtype=float), np.asarray(vals, dtype=float)


def to_monthly(points, resolution=None):
    """Day-weighted calendar-month means of Trends points.

    A weekly point covers 7 days from its timestamp, a daily point 1 day, a monthly point its month.
    Partial points are dropped, and so are months with fewer than 28 covered days.
    Returns a Series indexed by month-start Timestamps (empty when nothing is usable).
    """
    ts, vals = _parse_points(points)
    if not len(ts):
        return _empty_series()
    stamps = pd.to_datetime(ts, unit='s')
    res = _resolution(resolution, ts / 86400.0)
    if res == 'MONTH':
        months = (stamps + pd.Timedelta(days=3)).to_period('M').to_timestamp()   # tolerate time-zone offsets
        s = pd.Series(vals, index=months).groupby(level=0).mean().sort_index()
        s.index = pd.DatetimeIndex(s.index, name='month').astype('datetime64[ns]')
        return s
    span = 7 if res == 'WEEK' else 1
    s = pd.Series(vals, index=stamps.floor('D')).groupby(level=0).mean().sort_index()
    days = pd.date_range(s.index.min(), s.index.max() + pd.Timedelta(days=span - 1), freq='D')
    daily = s.reindex(days)
    if span > 1:
        daily = daily.ffill(limit=span - 1)
    mean = daily.resample('MS').mean()
    covered = daily.resample('MS').count()
    out = mean[covered >= THRESH['min_cover_days']].dropna()
    if not len(out):
        return _empty_series()
    out.index = pd.DatetimeIndex(out.index, name='month').astype('datetime64[ns]')
    return out.astype(float)


# ---------- several samples -> one consensus ----------

def consensus(series_list):
    """Align samples of the same query and average them.

    Each sample is scaled onto the first one by the median ratio over months where both are above 0; the
    consensus is the mean of the scaled samples, rescaled to a maximum of 100. Returns
    (consensus Series, per-month cv Series, q_T) with q_T = clip(1 - median cv); one sample gives q_T 0.7.
    """
    samples = [s for s in (_as_series(x) for x in (series_list or [])) if len(s)]
    if not samples:
        return _empty_series('consensus'), _empty_series('cv'), 0.0
    pos = [s for s in samples if (s > 0).any()]
    ref = pos[0] if pos else samples[0]
    aligned = [ref]
    for s in samples:
        if s is ref:
            continue
        if not (s > 0).any():
            aligned.append(s)                            # all zeros: the scale does not matter
            continue
        both = ref.index.intersection(s.index)
        r, v = ref.reindex(both), s.reindex(both)
        ok = (r > 0) & (v > 0)
        if not ok.any():
            continue                                     # no common months to align on: leave it out
        aligned.append(s * float(np.median(r[ok] / v[ok])))
    df = pd.concat(aligned, axis=1, ignore_index=True).sort_index()
    cons = df.mean(axis=1, skipna=True)
    top = float(cons.max()) if len(cons) else 0.0
    k = 100.0 / top if top > 0 else 1.0
    cons, df = cons * k, df * k
    cons.name = 'consensus'
    cons.index = pd.DatetimeIndex(cons.index, name='month').astype('datetime64[ns]')
    if len(aligned) < 2:
        return cons, _empty_series('cv'), THRESH['q_single']
    n = df.notna().sum(axis=1)
    mean, std = df.mean(axis=1, skipna=True), df.std(axis=1, skipna=True, ddof=1)
    ok = (n >= 2) & (mean > 0)
    cv = (std[ok] / mean[ok]).astype(float)
    cv.name = 'cv'
    cv.index = pd.DatetimeIndex(cv.index, name='month').astype('datetime64[ns]')
    if not len(cv):
        return cons, cv, THRESH['q_single']
    return cons, cv, round(float(clip(1.0 - float(np.median(cv)))), 4)


# ---------- features ----------

def _month_ts(start):
    if start is None or start == '':
        return None
    idx = _month_index([start])
    return None if pd.isna(idx[0]) else idx[0]


def _slope_log(d):
    """OLS slope of ln(d+1) per month; None with fewer than 3 points."""
    return ols_slope(np.log1p(np.clip(np.asarray(d, dtype=float), 0, None))) if len(d) >= 3 else None


def _empty_features():
    f = {k: None for k in FEATURE_KEYS}
    f.update(n_months=0, months=[], CMA=[], d=[])
    return f


def features(monthly, start='2022-01'):
    """Trend features from a monthly series (synth.md 8.5). Months before `start` are dropped.

    Gaps inside the range are filled by interpolation. A feature that needs more data than exists is None
    (YoY needs 24 months, SI 24, CAGR 48). Never raises on short, zero or missing data.
    """
    s = _as_series(monthly)
    t0 = _month_ts(start)
    if t0 is not None:
        s = s[s.index >= t0]
    s = _contiguous(s)
    n = len(s)
    f = _empty_features()
    if n == 0:
        return f
    y = np.clip(s.to_numpy(dtype=float), 0, None)
    months = [ts.strftime('%Y-%m') for ts in s.index]
    cal = np.array([ts.month for ts in s.index])
    lo_c, hi_c = THRESH['yoy_clip']
    f.update(n_months=n, months=months, start=months[0], end=months[-1], last=_f(y[-1]))
    f['mean_last12'] = _f(y[-12:].mean())
    if n >= 12:
        f['mean_first12'] = _f(y[:12].mean())
    if n >= 13:
        f['median_prev12'] = _f(np.median(y[-13:-1]))

    # year on year (sums, +1 so zero years do not blow up)
    if n >= 24:
        l12, p12 = float(y[-12:].sum()), float(y[-24:-12].sum())
        f.update(sum_last12=_f(l12), sum_prev12=_f(p12), YoY=_f(clip((l12 + 1) / (p12 + 1) - 1, lo_c, hi_c)))
    if n >= 15:
        f['YoY_recent'] = _f(clip((y[-3:].sum() + 1) / (y[-15:-12].sum() + 1) - 1, lo_c, hi_c))
    if n >= THRESH['cagr_min_months']:
        years = (n - 12) / 12.0                          # first-12 centre to last-12 centre
        ratio = (y[-12:].mean() + 1) / (y[:12].mean() + 1)
        f['CAGR'] = _f(clip(ratio ** (1 / years) - 1, lo_c, hi_c))

    # centred 2x12 moving average, seasonal index by ratio to it, seasonal strength
    cma = np.full(n, np.nan)
    if n >= 13:
        w = np.r_[0.5, np.ones(11), 0.5] / 12.0
        cma[6:n - 6] = np.convolve(y, w, mode='valid')
    f['CMA'] = [_f(v, 3) for v in cma]
    si = None
    good = np.isfinite(cma) & (cma > 0)
    if good.any():
        ratio = np.where(good, y / np.where(good, cma, 1.0), np.nan)
        per = [ratio[(cal == m) & good] for m in range(1, 13)]
        if all(len(r) for r in per):
            raw = np.array([r.mean() for r in per])
            if raw.sum() > 0:
                si = raw * 12.0 / raw.sum()
    if si is not None:
        si_t = np.maximum(si[cal - 1], THRESH['si_floor'])
        a = y[good] / cma[good] - 1
        b = y[good] / (cma[good] * si_t[good]) - 1
        va = float(np.var(a))
        f['Fs'] = _f(clip(1 - float(np.var(b)) / va) if va > 1e-12 else 0.0)
        f['SI'] = [_f(v) for v in si]
        if float(si.max() - si.min()) > 1e-6:            # a perfectly flat index has no peak
            f['peak_month'] = int(np.argmax(si)) + 1
            f['launch_month'] = (f['peak_month'] - 1 - THRESH['launch_lead']) % 12 + 1
        d = y / si_t
    else:
        d = y.copy()                                      # no seasonal index yet: use the raw series
    f['d'] = [_f(v, 3) for v in d]

    # momentum on the deseasonalised series
    if n >= 12:
        b12 = _slope_log(d[-12:])
        if b12 is not None:
            f['m12'] = _f(math.exp(min(12 * b12, 50)) - 1)
        base = d[-12:].mean()
        f['short_mom'] = _f(d[-3:].mean() / base - 1) if base > 0 else None
        b6, bp6 = _slope_log(d[-6:]), _slope_log(d[-12:-6])
        if b6 is not None and bp6 is not None:
            f['accel'] = _f(b6 - bp6)
        tail = d[-24:]
        f['CV_d'] = _f(tail.std() / tail.mean()) if tail.mean() > 0 else None

    # peak shape
    top = float(y.max())
    f['zero_share'] = _f(float((y < THRESH['zero_level']).sum()) / n)
    if top > 0:
        p = int(n - 1 - np.argmax(y[::-1]))               # latest month at the maximum
        f['months_since_peak'] = n - 1 - p
        f['peak_at'] = months[p]
        f['cur_vs_peak'] = _f(y[-3:].mean() / top)
        f['spikiness'] = _f(np.sort(y)[-3:].sum() / y.sum())
        f['peak_width'] = int((y >= 0.5 * top).sum())
        below = np.nonzero(y[p + 1:] < 0.5 * top)[0]
        f['half_life'] = int(below[0]) + 1 if len(below) else None
    return f


# ---------- label, score, seasonality ----------

def base_label(text):
    """'Growing, seasonal peak Dec' -> 'Growing'."""
    return str(text or '').split(',')[0].strip()


def _g(f, k):
    v = f.get(k) if isinstance(f, dict) else None
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else v


def _yoy_why(yoy):
    if abs(yoy) < 0.05:
        return 'searches flat vs last year (%s)' % _pct(yoy)
    return 'searches %s %d%% vs last year' % ('up' if yoy > 0 else 'down', round(abs(100 * yoy)))


def _seasonal_why(f):
    return 'seasonal peak in %s (strength %.2f), launch by %s' % (
        _mname(_g(f, 'peak_month')), _g(f, 'Fs') or 0, _mname(_g(f, 'launch_month')))


def label(f):
    """(label, why_list). Rules in order: Insufficient, Fad, Spike (watch), Breakout, Emerging, Growing,
    Seasonal, Declining, Evergreen, Mixed. A strong seasonal pattern is appended to a trend label as a
    modifier, e.g. 'Growing, seasonal peak Dec'; base_label() strips it."""
    T = THRESH
    f = f if isinstance(f, dict) else {}
    n = _g(f, 'n_months') or 0
    yoy, m12, cagr, fs = _g(f, 'YoY'), _g(f, 'm12'), _g(f, 'CAGR'), _g(f, 'Fs')
    cvp, msp, spk, pw = _g(f, 'cur_vs_peak'), _g(f, 'months_since_peak'), _g(f, 'spikiness'), _g(f, 'peak_width')
    zs, ml12, cvd = _g(f, 'zero_share'), _g(f, 'mean_last12'), _g(f, 'CV_d')

    # 1. Insufficient
    why = []
    if n < T['min_months']:
        why.append('only %d months of data since %s (need %d)' % (n, _g(f, 'start') or '2022', T['min_months']))
    if zs is not None and zs > T['max_zero_share']:
        why.append('%d%% of months show almost no searches' % round(100 * zs))
    if ml12 is not None and ml12 < T['min_mean_last12']:
        why.append('average interest only %.1f/100 over the last 12 months' % ml12)
    if not n:
        return 'Insufficient', ['no Google Trends data']
    if why:
        return 'Insufficient', why

    def peak_why():
        return 'peaked %s (%d months ago), now at %d%% of the peak' % (
            _ym_label(_g(f, 'peak_at')), msp or 0, round(100 * (cvp or 0)))

    # 2. Fad
    if (cvp is not None and msp is not None
            and ((spk is not None and spk > T['fad_spikiness']) or (pw is not None and pw <= T['fad_peak_width']))
            and cvp < T['fad_cur_vs_peak'] and msp >= T['fad_months_since_peak']):
        why = [peak_why()]
        if spk is not None and spk > T['fad_spikiness']:
            why.append('top 3 months hold %d%% of all searches' % round(100 * spk))
        else:
            why.append('only %d months above half the peak' % pw)
        return 'Fad', why

    # 3. Spike (watch)
    last, med = _g(f, 'last'), _g(f, 'median_prev12')
    if (last is not None and med is not None and msp is not None and last > T['spike_ratio'] * med
            and msp <= T['spike_months_since_peak']):
        times = ('%.1fx' % (last / med)) if med > 0 else 'far above'
        return 'Spike (watch)', ['last month jumped to %s the usual level' % times,
                                 'too early to tell if it lasts']

    emerging = (yoy is not None and m12 is not None and cvp is not None and msp is not None and pw is not None
                and yoy > T['emerging_yoy'] and m12 > 0 and cvp >= T['emerging_cur_vs_peak']
                and msp <= T['emerging_months_since_peak'] and pw > T['emerging_peak_width'])
    lab, why = None, []
    # 4. Breakout
    mf12, l12, p12 = _g(f, 'mean_first12'), _g(f, 'sum_last12'), _g(f, 'sum_prev12')
    if (emerging and mf12 is not None and l12 is not None and p12 is not None
            and mf12 < T['breakout_first12'] and l12 > T['breakout_ratio'] * p12):
        lab = 'Breakout'
        why = [_yoy_why(yoy), 'rose from a low base (%.1f/100 in the first year)' % mf12,
               'now at %d%% of its peak' % round(100 * cvp)]
    # 5. Emerging
    elif emerging:
        lab = 'Emerging'
        why = [_yoy_why(yoy), 'underlying trend %s a year' % _pct(m12), 'now at %d%% of its peak' % round(100 * cvp)]
    # 6. Growing
    elif (yoy is not None and yoy > T['growing_yoy']
          and ((cagr is not None and cagr > T['growing_cagr'])
               or (cagr is None and m12 is not None and m12 > T['growing_m12']))):
        lab = 'Growing'
        why = ['growing about %d%% a year since %s' % (round(100 * cagr), (_g(f, 'start') or '2022')[:4])
               if cagr is not None else 'underlying trend %s a year' % _pct(m12), _yoy_why(yoy)]
    # 7. Seasonal
    elif fs is not None and yoy is not None and fs > T['seasonal_fs'] and abs(yoy) < T['seasonal_max_abs_yoy']:
        return 'Seasonal', ['same pattern every year (strength %.2f)' % fs,
                            'peaks in %s, launch by %s' % (_mname(_g(f, 'peak_month')), _mname(_g(f, 'launch_month'))),
                            _yoy_why(yoy)]
    # 8. Declining
    elif yoy is not None and m12 is not None and yoy < T['declining_yoy'] and m12 < 0:
        lab = 'Declining'
        why = [_yoy_why(yoy), 'underlying trend %s a year' % _pct(m12)]
    # 9. Evergreen
    elif cvd is not None and yoy is not None and cvd < T['evergreen_cv'] and abs(yoy) < T['evergreen_max_abs_yoy']:
        return 'Evergreen', ['steady demand (month-to-month variation %d%%)' % round(100 * cvd), _yoy_why(yoy)]
    # 10. Mixed
    else:
        lab = 'Mixed'
        why = ['no clear pattern']
        if yoy is not None:
            why.append(_yoy_why(yoy))
        if m12 is not None:
            why.append('underlying trend %s a year' % _pct(m12))
    if lab in _MODIFIABLE and fs is not None and fs > T['seasonal_fs'] and _g(f, 'peak_month'):
        lab = '%s, seasonal peak %s' % (lab, _mname(_g(f, 'peak_month')))
        why.append(_seasonal_why(f))
    return lab, why


def trend_score(f, q_T):
    """0..100: 100*[.30 sig(YoY/.3) + .25 sig(m12/.3) + .20 cur_vs_peak + .15 (1 - fad_lik) + .10 q_T].

    Missing parts count as neutral (YoY and m12 as 0, cur_vs_peak as 0.5); no data at all scores 0."""
    T = THRESH
    if not isinstance(f, dict) or not _g(f, 'n_months'):
        return 0.0
    yoy, m12 = _g(f, 'YoY') or 0.0, _g(f, 'm12') or 0.0
    cvp = _g(f, 'cur_vs_peak')
    cvp = 0.5 if cvp is None else clip(float(cvp))
    spk = _g(f, 'spikiness') or 0.0
    fad = clip(spk / T['fad_spike_scale']) * clip((1 - cvp) / T['fad_drop_scale'])
    try:
        q = float(q_T)
    except (TypeError, ValueError):
        q = None
    q = clip(q) if q is not None and math.isfinite(q) else T['score_q_default']
    ts = 100 * (0.30 * sigmoid(clip(yoy / T['score_yoy_scale'], -50, 50))
                + 0.25 * sigmoid(clip(m12 / T['score_m12_scale'], -50, 50))
                + 0.20 * cvp + 0.15 * (1 - fad) + 0.10 * q)
    return round(float(clip(ts, 0.0, 100.0)), 1)


def seasonality(f):
    """{'si': 12 floats (mean 1), 'strength', 'peak_month', 'launch_month', 'reliable'}.

    Without a seasonal index the si is flat 1.0 and reliable is False."""
    f = f if isinstance(f, dict) else {}
    si = _g(f, 'SI')
    n = _g(f, 'n_months') or 0
    if not si or len(si) != 12 or any(v is None for v in si):
        return {'si': [1.0] * 12, 'strength': 0.0, 'peak_month': None, 'launch_month': None, 'reliable': False}
    fs = float(_g(f, 'Fs') or 0.0)
    return {'si': [round(float(v), 4) for v in si], 'strength': round(fs, 4),
            'peak_month': _g(f, 'peak_month'), 'launch_month': _g(f, 'launch_month'),
            'reliable': bool(fs > THRESH['si_reliable_fs'] and n >= THRESH['si_min_months'])}
