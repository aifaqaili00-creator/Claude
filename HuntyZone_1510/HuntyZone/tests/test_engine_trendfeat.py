"""Tests for engine/trendfeat.py (synthetic data only).  Run:  python -m pytest -q tests/test_engine_trendfeat.py"""
import calendar
import datetime as dt
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import trendfeat as tf  # noqa: E402


# ---------- helpers ----------

def ts(y, m, d, h=0):
    return calendar.timegm(dt.datetime(y, m, d, h).timetuple())


def monthly(values, start='2022-01'):
    idx = pd.date_range(start + '-01', periods=len(values), freq='MS')
    return pd.Series(np.asarray(values, dtype=float), index=idx)


N = 57                                         # 2022-01 .. 2026-09
IDX = pd.date_range('2022-01-01', periods=N, freq='MS')
T = np.arange(N)


def rng(seed=1):
    return np.random.default_rng(seed)


def sine_peak(peak, amp=40.0, base=50.0, noise=2.0, seed=1, n=N, start='2022-01'):
    idx = pd.date_range(start + '-01', periods=n, freq='MS')
    v = base + amp * np.cos(2 * np.pi * (idx.month - peak) / 12) + rng(seed).normal(0, noise, n)
    return pd.Series(v, index=idx)


SERIES = {
    'sine': sine_peak(7),
    'linear': monthly(20 + 1.0 * T + rng(2).normal(0, 1.5, N)),
    'fad': monthly(np.r_[np.full(20, 8.0), [100, 60, 35, 20, 12], np.full(N - 25, 8.0)] + rng(3).normal(0, 0.5, N)),
    'spike': monthly(np.r_[20 + rng(4).normal(0, 1, N - 1), 80]),
    'flat': monthly(50 + rng(5).normal(0, 1, N)),
    'flat_exact': monthly(np.full(N, 50.0)),
    'zeros': monthly(np.where(T % 3 == 0, 10.0, 0.0)),
    'breakout': monthly(np.r_[np.full(N - 12, 3.0), np.linspace(10, 100, 12)]),
    'emerging': monthly(np.r_[np.full(N - 12, 30.0), np.linspace(36, 100, 12)]),
    'declining': monthly(80 - 1.0 * T + rng(6).normal(0, 1, N)),
}


def lab(name_or_series):
    s = SERIES[name_or_series] if isinstance(name_or_series, str) else name_or_series
    return tf.label(tf.features(s))


def json_safe(obj):
    json.dumps(obj, allow_nan=False)          # raises on NaN / inf
    return True


# ---------- to_monthly ----------

WEEKS = [ts(2024, 1, 1) + 7 * 86400 * k for k in range(10)]   # Mondays from 1 Jan 2024 to 4 Mar 2024
WEEK_VALS = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]


def test_week_straddling_two_months_is_split_by_days():
    pts = [[t, v, False] for t, v in zip(WEEKS, WEEK_VALS)] + [[ts(2024, 3, 11), 999, True]]
    s = tf.to_monthly(pts, 'WEEK')
    assert list(s.index) == [pd.Timestamp('2024-01-01'), pd.Timestamp('2024-02-01')]   # March has 10 days only
    # Jan: 4 full weeks + 3 days of the week starting 29 Jan
    assert s.iloc[0] == pytest.approx((7 * (10 + 20 + 30 + 40) + 3 * 50) / 31)
    # Feb 2024 (29 days): 4 days of week 5, weeks 6-8, 4 days of the week starting 26 Feb
    assert s.iloc[1] == pytest.approx((4 * 50 + 7 * (60 + 70 + 80) + 4 * 90) / 29)


def test_resolution_is_inferred_from_spacing():
    pts = [[t, v, False] for t, v in zip(WEEKS, WEEK_VALS)]
    a, b = tf.to_monthly(pts), tf.to_monthly(pts, 'WEEK')
    pd.testing.assert_series_equal(a, b)


def test_partial_point_is_dropped_and_string_flags_work():
    pts = [[t, v, 'false'] for t, v in zip(WEEKS[:5], WEEK_VALS[:5])]
    pts[0][2] = 'true'                         # first week partial -> January loses 7 days
    s = tf.to_monthly(pts, 'WEEK')
    assert len(s) == 0                         # Jan has 8..31 = 24 days (< 28), Feb only 4 days


def test_missing_week_drops_the_month():
    pts = [[t, v, False] for t, v in zip(WEEKS, WEEK_VALS) if t != WEEKS[6]]   # no week of 12 Feb
    s = tf.to_monthly(pts, 'WEEK')
    assert pd.Timestamp('2024-02-01') not in s.index
    assert pd.Timestamp('2024-01-01') in s.index


def test_first_month_needs_28_covered_days():
    pts = [[ts(2024, 1, 10) + 7 * 86400 * k, 50, False] for k in range(8)]   # 10 Jan .. 6 Mar
    s = tf.to_monthly(pts, 'WEEK')
    assert list(s.index) == [pd.Timestamp('2024-02-01')]
    assert s.iloc[0] == pytest.approx(50.0)


def test_daily_points():
    jan = [[ts(2024, 1, d), d, False] for d in range(1, 32)]
    feb = [[ts(2024, 2, d), 100, False] for d in range(1, 21)]                # only 20 days
    s = tf.to_monthly(jan + feb)
    assert list(s.index) == [pd.Timestamp('2024-01-01')]
    assert s.iloc[0] == pytest.approx(16.0)


def test_monthly_points_and_timezone_offset():
    pts = [[ts(2023, 12, 31, 22), 40, False], [ts(2024, 2, 1), 60, False], [ts(2024, 3, 1, 5), 80, False]]
    s = tf.to_monthly(pts, 'MONTH')
    assert [d.strftime('%Y-%m') for d in s.index] == ['2024-01', '2024-02', '2024-03']
    assert list(s.values) == [40, 60, 80]


def test_to_monthly_values_and_bad_input():
    pts = [[t, '<1', False] for t in WEEKS[:5]] + [[WEEKS[5], None, False], ['x', 5, False], 'junk', [WEEKS[6]]]
    s = tf.to_monthly(pts, 'WEEK')
    assert s.iloc[0] == pytest.approx(0.5)     # '<1' counts as 0.5
    for bad in (None, [], [[None, None, None]], 'nope', [[1, 'abc']]):
        out = tf.to_monthly(bad)
        assert isinstance(out, pd.Series) and len(out) == 0


def test_dict_points_from_raw_timeline():
    pts = [{'time': str(t), 'value': [v], 'isPartial': False} for t, v in zip(WEEKS, WEEK_VALS)]
    a = tf.to_monthly(pts)
    b = tf.to_monthly([[t, v, False] for t, v in zip(WEEKS, WEEK_VALS)])
    pd.testing.assert_series_equal(a, b)


# ---------- consensus ----------

def test_scaled_copies_give_the_same_consensus():
    s = SERIES['sine']
    c1, cv1, q1 = tf.consensus([s])
    c3, cv3, q3 = tf.consensus([s, 0.5 * s, 2.3 * s])
    pd.testing.assert_series_equal(c1, c3, check_names=False)
    assert float(c3.max()) == pytest.approx(100.0)
    assert np.allclose((c3 / (s * 100 / s.max())).values, 1.0)
    assert q3 == pytest.approx(1.0)
    assert float(cv3.max()) == pytest.approx(0.0, abs=1e-12)
    assert q1 == tf.THRESH['q_single'] == 0.7 and len(cv1) == 0


def test_consensus_noise_lowers_quality():
    s = SERIES['sine']
    r = rng(9)
    low = [s * (1 + r.normal(0, 0.03, N)) for _ in range(4)]
    high = [s * (1 + r.normal(0, 0.3, N)) for _ in range(4)]
    q_low, q_high = tf.consensus(low)[2], tf.consensus(high)[2]
    assert 0.9 < q_low < 1.0
    assert 0.0 <= q_high < q_low


def test_consensus_union_of_months_and_dict_input():
    a = monthly([10, 20, 30, 40], '2023-01')
    b = {'2023-03': 15, '2023-04': 20, '2023-05': 25}            # half the scale of a, one extra month
    c, cv, q = tf.consensus([a, b])
    assert [d.strftime('%Y-%m') for d in c.index] == ['2023-01', '2023-02', '2023-03', '2023-04', '2023-05']
    assert c.iloc[-1] == pytest.approx(100.0)                     # b aligned (x2) -> 50 is the max
    assert len(cv) == 2 and q == pytest.approx(1.0)


def test_consensus_empty_and_unalignable():
    c, cv, q = tf.consensus([])
    assert len(c) == 0 and len(cv) == 0 and q == 0.0
    c, cv, q = tf.consensus(None)
    assert len(c) == 0 and q == 0.0
    c, cv, q = tf.consensus([None, pd.Series(dtype=float), monthly([5, 10])])
    assert q == 0.7 and float(c.max()) == pytest.approx(100.0)
    # no overlapping months: second sample left out
    c, cv, q = tf.consensus([monthly([5, 10], '2022-01'), monthly([7, 9], '2024-01')])
    assert len(c) == 2 and q == 0.7
    # all-zero sample does not break anything
    c, cv, q = tf.consensus([monthly([0, 0, 0]), monthly([0, 0, 0])])
    assert len(c) == 3 and float(c.max()) == 0.0 and q == 0.7


# ---------- features ----------

def test_feature_keys_and_json_safety():
    for name, s in SERIES.items():
        f = tf.features(s)
        for k in tf.FEATURE_KEYS:
            assert k in f, (name, k)
        assert json_safe(f), name
        assert f['n_months'] == N and f['start'] == '2022-01' and f['end'] == '2026-09'
        assert json_safe(tf.label(f)) and json_safe(tf.seasonality(f))


def test_data_before_2022_is_ignored():
    old = pd.date_range('2019-01-01', '2021-12-01', freq='MS')
    pre = pd.Series(np.where(old.year == 2020, 100.0, 5.0), index=old)        # covid spike before 2022
    s = pd.concat([pre, SERIES['flat']])
    f = tf.features(s)
    assert f['start'] == '2022-01' and f['n_months'] == N
    assert f == tf.features(SERIES['flat'])
    assert tf.label(f)[0] == 'Evergreen'
    assert tf.features(s, start=None)['n_months'] == N + len(old)
    assert tf.features(s, start=None)['peak_at'].startswith('2020')


def test_yoy_formula_and_data_requirements():
    s = monthly([1.0] * 12 + [2.0] * 12)
    f = tf.features(s)
    assert f['YoY'] == pytest.approx((24 + 1) / (12 + 1) - 1, abs=1e-4)
    assert f['YoY_recent'] == pytest.approx((6 + 1) / (3 + 1) - 1, abs=1e-4)
    assert tf.features(monthly([1.0] * 23))['YoY'] is None
    assert tf.features(monthly([1.0] * 14))['YoY_recent'] is None
    assert tf.features(monthly(np.full(47, 30.0)))['CAGR'] is None
    f48 = tf.features(monthly(np.r_[np.full(12, 10.0), np.full(24, 20.0), np.full(12, 40.0)]))
    assert f48['CAGR'] == pytest.approx((41 / 11) ** (1 / 3) - 1, abs=1e-4)          # 3 years between centres
    big = tf.features(monthly(np.r_[np.zeros(36), np.full(12, 100.0)]))
    assert big['YoY'] == 5.0                                                          # clipped


def test_peak_shape_features():
    v = np.full(30, 10.0)
    v[20], v[21], v[22] = 100.0, 60.0, 40.0
    f = tf.features(monthly(v))
    assert f['months_since_peak'] == 9 and f['peak_at'] == '2023-09'
    assert f['cur_vs_peak'] == pytest.approx(0.1)
    assert f['peak_width'] == 2
    assert f['half_life'] == 2                                                        # 40 < 50 two months after the peak
    assert f['spikiness'] == pytest.approx(200 / (27 * 10 + 200), abs=1e-4)
    assert f['zero_share'] == 0.0
    still_high = tf.features(monthly(np.r_[np.full(20, 10.0), np.full(10, 100.0)]))
    assert still_high['half_life'] is None and still_high['months_since_peak'] == 0   # latest month at the max


def test_seasonal_index_and_cma():
    f = tf.features(SERIES['sine'])
    si = np.array(f['SI'])
    assert len(si) == 12 and si.mean() == pytest.approx(1.0, abs=1e-3)
    assert f['peak_month'] == 7 and f['launch_month'] == 4
    assert f['Fs'] > 0.9
    assert len(f['CMA']) == N and f['CMA'][0] is None and f['CMA'][6] == pytest.approx(50, abs=3)
    assert len(f['d']) == N
    exact = tf.features(SERIES['flat_exact'])
    assert exact['Fs'] == 0.0 and exact['peak_month'] is None and exact['CV_d'] == 0.0


@pytest.mark.parametrize('peak,launch', [(1, 10), (2, 11), (3, 12), (4, 1), (12, 9)])
def test_launch_month_wraps(peak, launch):
    f = tf.features(sine_peak(peak))
    assert f['peak_month'] == peak and f['launch_month'] == launch


def test_interior_gaps_are_filled():
    s = SERIES['linear'].drop(SERIES['linear'].index[[10, 11, 30]])
    f = tf.features(s)
    assert f['n_months'] == N and json_safe(f)


def test_short_empty_and_zero_inputs_never_raise():
    for bad in (None, [], {}, pd.Series(dtype=float), monthly([]), monthly([np.nan] * 5), 'junk', 42,
                monthly([5.0]), monthly([0.0] * 40), monthly([3, 4, 5]), {'2023-01': '<1', '2023-02': None},
                monthly([1e9, -5, 0, 3] * 8)):
        f = tf.features(bad)
        assert json_safe(f)
        lbl, why = tf.label(f)
        assert lbl in tf.LABELS and isinstance(why, list) and why
        sc = tf.trend_score(f, 0.7)
        assert 0.0 <= sc <= 100.0
        sea = tf.seasonality(f)
        assert len(sea['si']) == 12 and json_safe(sea)
    assert tf.features(None)['n_months'] == 0
    assert tf.label(tf.features(monthly([0.0] * 40)))[0] == 'Insufficient'
    assert tf.trend_score(tf.features(None), 0.7) == 0.0


def test_features_accept_dict_and_string_months():
    d = {m.strftime('%Y-%m'): float(v) for m, v in SERIES['linear'].items()}
    assert tf.features(d) == tf.features(SERIES['linear'])
    per = SERIES['linear'].copy()
    per.index = per.index.to_period('M')
    assert tf.features(per) == tf.features(SERIES['linear'])
    pairs = [(m.date(), v) for m, v in SERIES['linear'].items()] + [(pd.NaT, 5.0), (np.datetime64('NaT', 'ns'), 1.0)]
    assert tf.features(pairs) == tf.features(SERIES['linear'])


# ---------- labels ----------

def test_label_sine_is_seasonal_with_july_peak():
    f = tf.features(SERIES['sine'])
    lbl, why = tf.label(f)
    assert lbl == 'Seasonal'
    assert f['peak_month'] == 7
    assert any('Jul' in w for w in why)


def test_label_linear_growth_is_growing():
    lbl, why = lab('linear')
    assert tf.base_label(lbl) == 'Growing'
    assert any('%' in w for w in why)


def test_label_spike_then_decay_is_fad():
    assert lab('fad')[0] == 'Fad'


def test_label_recent_spike_is_spike_watch():
    lbl, why = lab('spike')
    assert lbl == 'Spike (watch)' and 'x' in why[0]


def test_label_flat_is_evergreen():
    assert lab('flat')[0] == 'Evergreen'
    assert lab('flat_exact')[0] == 'Evergreen'


def test_label_mostly_zeros_is_insufficient():
    lbl, why = lab('zeros')
    assert lbl == 'Insufficient' and any('no searches' in w for w in why)


def test_label_late_ramp_is_emerging_or_breakout():
    assert lab('breakout')[0] == 'Breakout'                  # from a near-zero base
    assert lab('emerging')[0] == 'Emerging'                  # from a solid base
    for name in ('breakout', 'emerging'):
        assert tf.base_label(lab(name)[0]) in ('Emerging', 'Breakout')


def test_label_declining():
    lbl, why = lab('declining')
    assert lbl == 'Declining' and any('down' in w for w in why)


def test_label_short_series_is_insufficient():
    lbl, why = tf.label(tf.features(SERIES['linear'].iloc[:12]))
    assert lbl == 'Insufficient' and any('12 months' in w for w in why)
    lbl, why = tf.label(tf.features(monthly(np.full(30, 3.0))))
    assert lbl == 'Insufficient' and any('3.0/100' in w for w in why)


def test_growing_with_seasonal_modifier():
    si = 1 + 0.5 * np.cos(2 * np.pi * (IDX.month - 12) / 12)
    s = monthly((20 + 1.0 * T) * si + rng(7).normal(0, 0.5, N))
    f = tf.features(s)
    lbl, why = tf.label(f)
    assert lbl == 'Growing, seasonal peak Dec'
    assert tf.base_label(lbl) == 'Growing'
    assert any('seasonal peak in Dec' in w for w in why)


def test_label_on_crafted_feature_dicts():
    base = {'n_months': 30, 'zero_share': 0.0, 'mean_last12': 50.0, 'start': '2022-01'}
    assert tf.label(dict(base, YoY=0.2, m12=0.05, CAGR=None, Fs=0.1, CV_d=0.5))[0] == 'Mixed'
    assert tf.label(dict(base, YoY=0.2, m12=0.2, CAGR=None, Fs=0.1, CV_d=0.5))[0] == 'Growing'   # m12 stands in
    assert tf.label(dict(base, YoY=0.2, m12=0.2, CAGR=0.05, Fs=0.1, CV_d=0.5))[0] == 'Mixed'
    # order: Fad wins over Declining
    fad = dict(base, YoY=-0.5, m12=-0.3, cur_vs_peak=0.2, months_since_peak=10, spikiness=0.5, peak_width=2,
               peak_at='2023-11')
    lbl, why = tf.label(fad)
    assert lbl == 'Fad' and 'Nov 2023' in why[0]
    assert tf.label(None) == ('Insufficient', ['no Google Trends data'])
    assert tf.label({})[0] == 'Insufficient'


def test_label_why_has_numbers():
    for name in SERIES:
        lbl, why = lab(name)
        assert why and all(isinstance(w, str) and w for w in why)
        assert any(ch.isdigit() for w in why for ch in w), name


def test_thresholds_are_tunable(monkeypatch):
    f = tf.features(SERIES['linear'])
    monkeypatch.setitem(tf.THRESH, 'growing_cagr', 5.0)       # make Growing impossible
    assert tf.base_label(tf.label(f)[0]) != 'Growing'


# ---------- trend score ----------

def test_trend_score_formula():
    f = {'n_months': 40, 'YoY': 0.0, 'm12': 0.0, 'cur_vs_peak': 1.0, 'spikiness': 0.0}
    assert tf.trend_score(f, 1.0) == pytest.approx(100 * (0.15 + 0.125 + 0.20 + 0.15 + 0.10), abs=0.05)
    assert tf.trend_score(f, 0.0) == pytest.approx(62.5, abs=0.05)
    assert tf.trend_score(f, None) == pytest.approx(67.5, abs=0.05)      # q_T unknown counts as 0.5
    assert tf.trend_score(f, float('nan')) == pytest.approx(67.5, abs=0.05)
    fad = dict(f, cur_vs_peak=0.1, spikiness=0.7)                          # fad_lik = 1
    assert tf.trend_score(fad, 1.0) == pytest.approx(100 * (0.15 + 0.125 + 0.02 + 0.0 + 0.10), abs=0.05)
    hot = dict(f, YoY=5.0, m12=100.0)
    assert 95 < tf.trend_score(hot, 1.0) <= 100


def test_trend_score_orders_series_sensibly():
    sc = {k: tf.trend_score(tf.features(s), 0.9) for k, s in SERIES.items()}
    assert all(0 <= v <= 100 for v in sc.values())
    assert sc['breakout'] > sc['linear'] > sc['flat'] > sc['declining']
    assert sc['emerging'] > sc['fad']
    assert tf.trend_score(None, 0.9) == 0.0


# ---------- seasonality ----------

def test_seasonality_sine():
    sea = tf.seasonality(tf.features(SERIES['sine']))
    assert sea['reliable'] is True and sea['peak_month'] == 7 and sea['launch_month'] == 4
    assert len(sea['si']) == 12 and sum(sea['si']) == pytest.approx(12, abs=0.01)
    assert sea['strength'] > 0.9
    assert max(range(12), key=lambda i: sea['si'][i]) == 6


def test_seasonality_unreliable_cases():
    flat = tf.seasonality(tf.features(SERIES['flat_exact']))
    assert flat['reliable'] is False and flat['strength'] == 0.0 and flat['peak_month'] is None
    short = tf.seasonality(tf.features(sine_peak(7, n=20)))
    assert short == {'si': [1.0] * 12, 'strength': 0.0, 'peak_month': None, 'launch_month': None,
                     'reliable': False}
    assert tf.seasonality(None)['reliable'] is False
    # strength alone is not enough with fewer than 24 months
    f = dict(tf.features(SERIES['sine']), n_months=23)
    assert tf.seasonality(f)['reliable'] is False


# ---------- full pipeline: weekly samples -> monthly -> consensus -> label ----------

def weekly_sine_points(scale=1.0, noise=0.0, seed=0):
    """5 years of weekly points, peak mid-July, rounded like Trends, last week partial."""
    r = rng(seed)
    start = ts(2021, 10, 3)                     # a Sunday
    pts = []
    for k in range(261):
        t = start + 7 * 86400 * k
        mid = dt.datetime.fromtimestamp(t + 3 * 86400, dt.timezone.utc)
        doy = mid.timetuple().tm_yday
        v = (50 + 40 * math.cos(2 * math.pi * (doy - 196) / 365.25)) * scale * (1 + r.normal(0, noise))
        pts.append([t, max(0, round(v)), k == 260])
    return pts


def test_weekly_pipeline_end_to_end():
    samples = [tf.to_monthly(weekly_sine_points(scale, 0.05, seed)) for scale, seed in ((1.0, 1), (0.6, 2))]
    assert samples[0].index[0] == pd.Timestamp('2021-10-01')
    cons, cv, q = tf.consensus(samples)
    assert 0.85 < q <= 1.0 and float(cons.max()) == pytest.approx(100.0)
    f = tf.features(cons)
    assert f['start'] == '2022-01'                                    # 2021 months ignored
    lbl, why = tf.label(f)
    assert lbl == 'Seasonal'
    sea = tf.seasonality(f)
    assert sea['peak_month'] == 7 and sea['reliable'] is True
    assert 0 <= tf.trend_score(f, q) <= 100
    assert json_safe([f, lbl, why, sea, q])
