"""Tests for engine/sales.py. Run with:  python -m pytest -q tests/test_engine_sales.py

The curve dicts are built by hand (the shape engine/curve.fit_market returns), so these tests do not
depend on curve.py.
"""
import calendar
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import badge as bd  # noqa: E402
from engine import sales as s  # noqa: E402

EST_KEYS = {'v', 'lo', 'hi', 'basis', 'conf'}
DAY = 86400
A, B = 13.5, -0.85
COV = [[1e-4, -8e-4, 0.0], [-8e-4, 0.0081, 0.0], [0.0, 0.0, 1e-4]]   # beta, alpha_m, sigma


def make_curve(**kw):
    c = {'market': 'US', 'beta': B, 'alpha': {'_market': A, 'kitchen': 13.8}, 'sigma': 0.6, 'delta_h': 0.0,
         'n_obs': 500, 'status': 'calibrated', 'cov': COV, 'diag': {'hit': 0.6, 'within1': 0.9, 'cov80': 0.8}}
    c.update(kw)
    return c


CURVE = make_curve()


def ts(y, m, d, h=12):
    return calendar.timegm((y, m, d, h, 0, 0))


def f(r, a=A, s_nu=0.25):
    """Expected units per month at rank r (the curve's f with the s_nu^2/2 term)."""
    return math.exp(a + B * math.log(r) + 0.5 * s_nu * s_nu)


def json_safe(x):
    json.dumps(x, allow_nan=False)          # raises on NaN / inf
    return True


def is_est(e):
    return EST_KEYS <= set(e) and e['lo'] <= e['v'] <= e['hi']


# ---------- helpers ----------

def test_thresh_is_a_dict():
    assert isinstance(s.THRESH, dict)
    assert s.THRESH['s_eps'] == 0.45 and s.THRESH['days_per_month'] == 30.44
    assert s.THRESH['backcast_max_months'] == 24 and s.THRESH['backcast_min_q'] == 0.5


def test_est_shape():
    assert s.est(1, 0, 2, 'estimated', 'low') == {'v': 1, 'lo': 0, 'hi': 2, 'basis': 'estimated', 'conf': 'low'}


def test_month_key_forms():
    assert s._month_key('2026-03') == '2026-03'
    assert s._month_key('2026-3-15') == '2026-03'
    assert s._month_key(pd.Timestamp('2026-03-01')) == '2026-03'
    assert s._month_key(pd.Period('2026-03', 'M')) == '2026-03'
    assert s._month_key(np.datetime64('2026-03-05')) == '2026-03'
    assert s._month_key(pd.NaT) is None
    assert s._month_key('2026-13') is None and s._month_key('junk') is None and s._month_key(None) is None
    assert s._add_months('2026-11', 3) == '2027-02' and s._add_months('2026-01', -1) == '2025-12'
    assert s._add_months('2026-01', -24) == '2024-01'


# ---------- units_from_bsr ----------

def test_constant_rank_full_month_matches_f():
    samples = [(ts(2026, 6, 1) + k * DAY, 1000) for k in range(30)]
    out = s.units_from_bsr(samples, CURVE, None)
    assert list(out) == ['2026-06']
    e = out['2026-06']
    assert is_est(e) and e['basis'] == 'estimated' and e['method'] == 'curve'
    assert e['coverage'] == 1.0 and e['partial'] is False and e['n'] == 30
    assert e['v'] == pytest.approx(f(1000) * 30 / 30.44, rel=1e-4)
    assert e['units_covered'] == pytest.approx(e['v'], rel=1e-6)
    assert e['days_covered'] == pytest.approx(30.0)
    assert json_safe(out)


def test_convexity_sum_beats_f_of_mean_rank():
    # alternating ranks: summing f over time must beat f at the mean rank (the curve is convex)
    samples = [(ts(2026, 6, 1) + k * DAY, 100 if k % 2 else 10000) for k in range(30)]
    e = s.units_from_bsr(samples, CURVE, None)['2026-06']
    T = 30.0
    by_mean = f(np.mean([100, 10000])) * T / 30.44
    by_geo_mean = f(math.exp(np.mean(np.log([100, 10000])))) * T / 30.44
    assert e['units_covered'] > by_mean
    assert e['units_covered'] > by_geo_mean * 1.5
    # and it equals the time-weighted sum of f(r): readings are 1 day apart, each covers one day
    assert e['units_covered'] == pytest.approx(15 * (f(100) + f(10000)) / 30.44, rel=1e-3)


def test_coverage_and_partial():
    # readings on 1-10 June at noon: 0.5 day before the first, 9 days between, 1.5 days after the last
    samples = [(ts(2026, 6, 1) + k * DAY, 1000) for k in range(10)]
    e = s.units_from_bsr(samples, CURVE, None)['2026-06']
    assert e['days_covered'] == pytest.approx(11.0)
    assert e['coverage'] == pytest.approx(11 / 30, abs=1e-3)
    assert e['partial'] is True and e['conf'] == 'low'
    assert e['units_covered'] == pytest.approx(f(1000) * 11 / 30.44, rel=1e-4)
    assert e['v'] == pytest.approx(f(1000) * 30 / 30.44, rel=1e-3)        # scaled up to the full month


def test_coverage_at_least_half_is_not_partial():
    samples = [(ts(2026, 6, 1) + k * DAY, 1000) for k in range(16)]      # 0.5 + 15 + 1.5 = 17 days
    e = s.units_from_bsr(samples, CURVE, None)['2026-06']
    assert e['coverage'] == pytest.approx(17 / 30, abs=1e-3)
    assert e['partial'] is False


def test_short_gap_each_reading_covers_half():
    # 2-day gap: each reading covers 1 day of it, plus 1.5 days on the outside
    t1 = ts(2026, 6, 10)
    e = s.units_from_bsr([(t1, 1000), (t1 + 2 * DAY, 4000)], CURVE, None)['2026-06']
    assert e['days_covered'] == pytest.approx(5.0)
    expect = (f(1000) * 2.5 + f(4000) * 2.5) / 30.44
    assert e['units_covered'] == pytest.approx(expect, rel=1e-4)


def test_mid_gap_is_interpolated_in_log_rank():
    # 10-day gap is covered fully, with ln(rank) moving in a straight line
    t1 = ts(2026, 6, 10)
    r0, r1, D = 1000.0, 4000.0, 10.0
    e = s.units_from_bsr([(t1, r0), (t1 + D * DAY, r1)], CURVE, None)['2026-06']
    assert e['days_covered'] == pytest.approx(D + 3.0)
    f0, f1 = f(r0), f(r1)
    gap_units = D * (f1 - f0) / (math.log(f1) - math.log(f0))         # exact integral of an exponential
    expect = (gap_units + 1.5 * f0 + 1.5 * f1) / 30.44
    assert e['units_covered'] == pytest.approx(expect, rel=1e-3)


def test_long_gap_is_missing():
    # 23-day gap: 1.5 days either side of each reading, the middle is missing
    a, b = ts(2026, 6, 3), ts(2026, 6, 26)
    e = s.units_from_bsr([(a, 1000), (b, 1000)], CURVE, None)['2026-06']
    assert e['days_covered'] == pytest.approx(6.0)
    assert e['coverage'] == pytest.approx(0.2, abs=1e-3)
    assert e['partial'] is True


def test_gap_threshold_14_days():
    a = ts(2026, 6, 5)
    e14 = s.units_from_bsr([(a, 1000), (a + 14 * DAY, 1000)], CURVE, None)['2026-06']
    e15 = s.units_from_bsr([(a, 1000), (a + 15 * DAY, 1000)], CURVE, None)['2026-06']
    assert e14['days_covered'] == pytest.approx(17.0)          # 14 interpolated + 1.5 + 1.5
    assert e15['days_covered'] == pytest.approx(6.0)           # missing middle


def test_months_split_and_units_conserved():
    # readings every 2 days from 20 May to 20 June
    samples = [(ts(2026, 5, 20) + 2 * k * DAY, 500 + 50 * k) for k in range(16)]
    out = s.units_from_bsr(samples, CURVE, None)
    assert list(out) == ['2026-05', '2026-06']
    assert out['2026-05']['n'] + out['2026-06']['n'] == 16
    total = sum(e['units_covered'] for e in out.values())
    days = sum(e['days_covered'] for e in out.values())
    assert days == pytest.approx(30 + 3, abs=0.01)
    # same readings, months in UTC+10: the split moves but nothing is lost
    shifted = s.units_from_bsr(samples, CURVE, None, month_fn=lambda t: s._utc_month(t + 10 * 3600))
    assert sum(e['units_covered'] for e in shifted.values()) == pytest.approx(total, rel=1e-4)
    assert shifted['2026-05']['days_covered'] == pytest.approx(out['2026-05']['days_covered'] - 10 / 24, abs=0.01)


def test_month_fn_can_return_timestamps_and_failures_fall_back():
    samples = [(ts(2026, 6, 1) + k * DAY, 1000) for k in range(30)]
    a = s.units_from_bsr(samples, CURVE, None, month_fn=lambda t: pd.Timestamp(t, unit='s'))
    b = s.units_from_bsr(samples, CURVE, None, month_fn=lambda t: 1 / 0)
    ref = s.units_from_bsr(samples, CURVE, None)
    assert a == ref and b == ref


def test_samples_with_no_reading_in_a_month_are_not_reported():
    # the last reading spills 1.5 days into July, but July has no reading of its own
    out = s.units_from_bsr([(ts(2026, 6, 30, 20), 1000), (ts(2026, 6, 29, 20), 1000)], CURVE, None)
    assert list(out) == ['2026-06']


def test_single_snapshot_uses_wider_noise():
    e = s.units_from_bsr([(ts(2026, 6, 15), 1000)], CURVE, None)['2026-06']
    assert e['days_covered'] == pytest.approx(3.0)
    assert e['units_covered'] == pytest.approx(f(1000, s_nu=0.5) * 3 / 30.44, rel=1e-4)
    assert e['partial'] is True and e['conf'] == 'low'


def test_node_alpha_and_market_fallback():
    samples = [(ts(2026, 6, 1) + k * DAY, 1000) for k in range(30)]
    base = s.units_from_bsr(samples, CURVE, None)['2026-06']['v']
    node = s.units_from_bsr(samples, CURVE, 'kitchen')['2026-06']['v']
    other = s.units_from_bsr(samples, CURVE, 'garden')['2026-06']['v']
    assert node == pytest.approx(base * math.exp(13.8 - A), rel=1e-3)
    assert other == base
    # a curve with no alpha at all uses the market prior
    bare = s.units_from_bsr(samples, {'market': 'AU', 'beta': B}, None)['2026-06']
    assert bare['v'] == pytest.approx(f(1000, a=13.68 - math.log(15)) * 30 / 30.44, rel=1e-3)
    assert bare['conf'] == 'low'                              # not calibrated


def test_monotonic_in_rank():
    vs = []
    for r in (100, 1000, 10000, 100000):
        samples = [(ts(2026, 6, 1) + k * DAY, r) for k in range(30)]
        vs.append(s.units_from_bsr(samples, CURVE, None)['2026-06']['v'])
    assert vs == sorted(vs, reverse=True)


def test_band_offset_and_conf():
    samples = [(ts(2026, 6, 1) + k * DAY, 1000) for k in range(30)]
    plain = s.units_from_bsr(samples, CURVE, None)['2026-06']
    up = s.units_from_bsr(samples, CURVE, None, offset=0.3)['2026-06']
    tight = s.units_from_bsr(samples, CURVE, None, offset={'eps': 0.3, 'sd': 0.1})['2026-06']
    assert up['v'] == pytest.approx(plain['v'] * math.exp(0.3), rel=1e-3)
    assert tight['v'] == up['v']
    assert tight['hi'] / tight['lo'] < up['hi'] / up['lo']
    assert plain['conf'] == 'medium' and tight['conf'] == 'high'
    # no cov matrix: a fallback width, never a zero-width band
    nocov = s.units_from_bsr(samples, make_curve(cov=None), None)['2026-06']
    assert nocov['lo'] < nocov['v'] < nocov['hi']
    # prior-only curve is low confidence whatever the width
    assert s.units_from_bsr(samples, make_curve(status='prior_only'), None)['2026-06']['conf'] == 'low'


def test_band_from_cov_by_delta_method():
    samples = [(ts(2026, 6, 1) + k * DAY, 1000) for k in range(30)]
    e = s.units_from_bsr(samples, CURVE, None, offset={'eps': 0.0, 'sd': 0.0})['2026-06']
    l = math.log(1000)
    var = COV[1][1] + 2 * l * COV[0][1] + l * l * COV[0][0] + 0.25 ** 2 / 30
    assert math.log(e['hi'] / e['v']) == pytest.approx(1.2816 * math.sqrt(var), rel=1e-3)


def test_input_forms_and_bad_rows():
    t = ts(2026, 6, 1)
    rows = [(t + k * DAY, 1000) for k in range(30)]
    ref = s.units_from_bsr(rows, CURVE, None)
    dicts = [{'ts': a, 'bsr': r} for a, r in rows]
    millis = [(a * 1000, r) for a, r in rows]
    noisy = list(reversed(rows)) + [(t, None), (t, 0), (t, -5), (None, 1000), (t, float('nan')), 'x', (t,)]
    assert s.units_from_bsr(np.array(rows, dtype=float), CURVE, None) == ref
    assert s.units_from_bsr(dicts, CURVE, None) == ref
    assert s.units_from_bsr(millis, CURVE, None) == ref
    assert s.units_from_bsr(noisy, CURVE, None) == ref


def test_duplicate_timestamps_keep_their_own_f():
    t = ts(2026, 6, 15)
    out = s.units_from_bsr([(t, 100), (t, 10000)], CURVE, None)['2026-06']
    assert out['units_covered'] == pytest.approx(1.5 * (f(100) + f(10000)) / 30.44, rel=1e-3)


@pytest.mark.parametrize('samples,curve', [
    ([], CURVE), (None, CURVE), ('junk', CURVE), (5, CURVE),
    ([(ts(2026, 6, 1), 1000)], None), ([(ts(2026, 6, 1), 1000)], 'curve'),
    (np.array([[ts(2026, 6, 1), 0.0]]), CURVE),
])
def test_units_from_bsr_empty(samples, curve):
    assert s.units_from_bsr(samples, curve, None) == {}


def test_units_from_bsr_year_is_fast_and_json_safe():
    import time as _t
    rng = np.random.default_rng(1)
    t0 = ts(2025, 1, 1)
    samples = [(t0 + k * 3600 * 6 + int(rng.integers(0, 600)), float(np.exp(rng.normal(8, 1))))
               for k in range(4 * 365)]
    samples += [(t0 + 400 * DAY + k * 7 * DAY, 3000) for k in range(10)]        # weekly readings: interpolated
    start = _t.time()
    out = s.units_from_bsr(samples, CURVE, 'kitchen')
    assert _t.time() - start < 2.0
    assert len(out) >= 14
    assert all(is_est(e) for e in out.values())
    assert json_safe(out)


# ---------- asin_offset ----------

def mu_at(r, a=A):
    return a + B * math.log(r)


def test_offset_one_reading_by_hand():
    r = 1000
    mu = mu_at(r)
    e_trunc = bd.truncated_lognormal_mean(mu, 0.6, 1000, 2000)
    e = math.log(e_trunc) - mu
    shrink = 0.45 ** 2 / (0.45 ** 2 + 0.5 ** 2)                      # single snapshot: s_nu 0.5
    assert s.asin_offset(CURVE, None, [{'badge': 1000, 'bsr': r}]) == pytest.approx(e * shrink, abs=1e-4)
    shrink_h = 0.45 ** 2 / (0.45 ** 2 + 0.25 ** 2)                   # 30-day mean rank: s_nu 0.25
    got = s.asin_offset(CURVE, None, [{'badge': '1K+ bought in past month', 'bsr': r, 'history': True}])
    assert got == pytest.approx(e * shrink_h, abs=1e-4)


def test_offset_sign_and_shrinkage():
    hi = s.asin_offset_info(CURVE, None, [{'badge': 10000, 'bsr': 1000}])
    lo = s.asin_offset_info(CURVE, None, [{'badge': 100, 'bsr': 1000}])
    assert hi['eps'] > 0 > lo['eps']
    assert 0 < hi['eps'] < hi['mean_e']                     # pulled toward 0
    # more readings: closer to the raw mean and a smaller sd
    many = s.asin_offset_info(CURVE, None, [{'badge': 10000, 'bsr': 1000}] * 4)
    assert hi['eps'] < many['eps'] < many['mean_e']
    assert many['sd'] < hi['sd'] < 0.45
    assert many['n'] == 4


def test_offset_formula_with_n_readings():
    rd = [(5000, 2000, True)] * 3                          # tuple form (badge, bsr, history)
    info = s.asin_offset_info(CURVE, None, rd)
    mu = mu_at(2000)
    e = math.log(bd.truncated_lognormal_mean(mu, 0.6, 5000, 6000)) - mu
    expect = e * 0.45 ** 2 / (0.45 ** 2 + 0.25 ** 2 / 3)
    assert info['eps'] == pytest.approx(expect, abs=1e-4)
    assert info['sd'] == pytest.approx(1 / math.sqrt(1 / 0.45 ** 2 + 3 / 0.25 ** 2), abs=1e-4)


def test_offset_counts_at_most_six_readings():
    six = s.asin_offset_info(CURVE, None, [{'badge': 2000, 'bsr': 500}] * 6)
    sixty = s.asin_offset_info(CURVE, None, [{'badge': 2000, 'bsr': 500}] * 60)
    assert sixty['eps'] == pytest.approx(six['eps'], abs=1e-6)
    assert sixty['sd'] == pytest.approx(six['sd'], abs=1e-6)
    assert sixty['n'] == 60


def test_offset_open_and_lower_bound_readings():
    open_top = s.asin_offset_info(CURVE, None, [{'badge': 100000, 'bsr': 50}])
    lb = s.asin_offset_info(CURVE, None, [{'low': 1000, 'high': 2000, 'kind': 'badge_lb', 'ln_bsr': math.log(1000)}])
    assert open_top['n'] == 1 and lb['n'] == 1
    assert json_safe(open_top) and json_safe(lb)


@pytest.mark.parametrize('curve,readings', [
    (CURVE, []), (CURVE, None), (CURVE, [None, 'x', {'badge': None, 'bsr': 100}, {'badge': 1000}]),
    (CURVE, [{'badge': 1000, 'bsr': 0}]), (None, [{'badge': 1000, 'bsr': 100}]), (CURVE, 7),
])
def test_offset_empty(curve, readings):
    assert s.asin_offset(curve, None, readings) == 0.0
    info = s.asin_offset_info(curve, None, readings)
    assert info == {'eps': 0.0, 'sd': 0.45, 'n': 0, 'mean_e': None}


def test_offset_feeds_units():
    info = s.asin_offset_info(CURVE, None, [{'badge': 10000, 'bsr': 1000, 'history': True}] * 3)
    samples = [(ts(2026, 6, 1) + k * DAY, 1000) for k in range(30)]
    base = s.units_from_bsr(samples, CURVE, None)['2026-06']
    adj = s.units_from_bsr(samples, CURVE, None, offset=info)['2026-06']
    assert adj['v'] == pytest.approx(base['v'] * math.exp(info['eps']), rel=1e-3)
    assert adj['hi'] / adj['lo'] < base['hi'] / base['lo']


# ---------- niche_month ----------

def snap(y, m, d, badges, status='ok'):
    return {'ts': ts(y, m, d), 'status': status, 'badges': badges}


def test_niche_priority_order():
    sources = {
        'market': 'US',
        'h10': {'2026-03': 5000},
        'badge': [snap(2026, 3, 10, [1000]), snap(2026, 4, 10, [1000, 100])],
        'curve': {'2026-03': s.est(900, 500, 1500, 'estimated', 'medium'),
                  '2026-04': s.est(1200, 700, 2000, 'estimated', 'medium'),
                  '2026-05': s.est(1300, 800, 2100, 'estimated', 'medium')},
    }
    out = s.niche_month(sources)
    assert list(out) == ['2026-03', '2026-04', '2026-05']
    assert out['2026-03']['method'] == 'h10' and out['2026-03']['basis'] == 'reported'
    assert out['2026-03']['v'] == 5000 and out['2026-03']['lo'] == 3750 and out['2026-03']['hi'] == 6250
    assert out['2026-03']['others'] == {'badge': pytest.approx(1386.3, abs=0.1), 'curve': 900}
    assert out['2026-04']['method'] == 'badge'
    assert out['2026-04']['v'] == pytest.approx(1386.3 + 138.6, abs=0.2)
    assert out['2026-04']['lo'] == 1100 and out['2026-04']['hi'] == 1999 + 199
    assert out['2026-04']['others'] == {'curve': 1200}
    assert out['2026-05']['method'] == 'curve' and 'others' not in out['2026-05']
    assert all(is_est(e) for e in out.values())
    assert json_safe(out)


def test_niche_badge_uses_latest_valid_snapshot():
    sources = {'badge': [
        snap(2026, 3, 5, [100]),
        snap(2026, 3, 25, [1000]),                       # latest valid: this one
        snap(2026, 3, 28, [10000], status='blocked'),    # ignored
        snap(2026, 3, 29, [10000], status='wrong_location'),
        snap(2026, 3, 30, [], status='ok'),              # valid but no badge: no value
    ]}
    out = s.niche_month(sources)
    assert out['2026-03']['method'] == 'badge'
    assert out['2026-03']['v'] == pytest.approx(1386.3, abs=0.1)
    assert out['2026-03']['as_of'] == ts(2026, 3, 25)


def test_niche_badge_snapshot_forms():
    sources = {'badge': [
        {'ts': ts(2026, 1, 20), 'status': 'partial',
         'bought_mid_sum': 2000, 'bought_low_sum': 1500, 'bought_high_sum': 2600},
        {'month': '2026-02', 'mid_sum': 700},
        {'ts': ts(2026, 3, 20), 'est': {'v': 300, 'lo': 200, 'hi': 450, 'basis': 'estimated', 'conf': 'medium'}},
    ]}
    out = s.niche_month(sources)
    assert out['2026-01']['v'] == 2000 and out['2026-01']['lo'] == 1500 and out['2026-01']['hi'] == 2600
    assert out['2026-02']['lo'] == pytest.approx(500, abs=0.1) and out['2026-02']['hi'] == pytest.approx(980)
    assert out['2026-03']['v'] == 300 and out['2026-03']['conf'] == 'medium'


def test_niche_h10_rows_latest_wins_and_curve_nested_sum():
    sources = {
        'h10': [{'ts': ts(2026, 5, 2), 'units': 800}, {'ts': ts(2026, 5, 28), 'units': 900},
                {'month': '2026-06', 'sales': 1000}],
        'curve': {'B0AAA': {'2026-07': s.est(100, 60, 160, 'estimated', 'medium')},
                  'B0BBB': {'2026-07': s.est(50, 30, 90, 'estimated', 'high'),
                            '2026-08': {'v': 40, 'lo': 20, 'hi': 70, 'basis': 'estimated', 'conf': 'low',
                                        'partial': True}}},
    }
    out = s.niche_month(sources)
    assert out['2026-05']['v'] == 900 and out['2026-05']['method'] == 'h10'
    assert out['2026-06']['v'] == 1000
    jul = out['2026-07']
    assert jul['method'] == 'curve' and jul['v'] == 150 and jul['lo'] == 90 and jul['hi'] == 250
    assert jul['n_asins'] == 2 and jul['conf'] == 'medium'
    assert out['2026-08']['partial'] is True and out['2026-08']['conf'] == 'low'


def test_niche_accepts_curve_output_from_units_from_bsr():
    samples = [(ts(2026, 6, 1) + k * DAY, 1000) for k in range(30)]
    a = s.units_from_bsr(samples, CURVE, None)
    b = s.units_from_bsr(samples, CURVE, 'kitchen')
    out = s.niche_month({'curve': {'B0A': a, 'B0B': b}})
    assert out['2026-06']['v'] == pytest.approx(a['2026-06']['v'] + b['2026-06']['v'], abs=0.2)


@pytest.mark.parametrize('sources', [None, {}, [], 'x', {'h10': None, 'badge': 'x', 'curve': 5},
                                     {'badge': [snap(2026, 1, 1, [1000], status='captcha')]}])
def test_niche_empty(sources):
    assert s.niche_month(sources) == {}


# ---------- backcast ----------

def trends(start='2023-01', n=40, fn=lambda i: 50.0):
    idx = pd.date_range(start, periods=n, freq='MS')
    return pd.Series([fn(i) for i in range(n)], index=idx, dtype=float)


def test_backcast_needs_quality():
    tr = trends()
    assert s.backcast(1000, '2026-04', tr, 0.49) == {}
    assert s.backcast(1000, '2026-04', tr, None) == {}
    assert s.backcast(1000, '2026-04', tr, float('nan')) == {}
    assert s.backcast(1000, '2026-04', tr, 0.5) != {}


def test_backcast_scales_by_index_ratio():
    tr = trends(fn=lambda i: 20.0 + i)                     # 2023-01 = 20 ... 2026-04 = 59
    out = s.backcast(1180, '2026-04', tr, 0.8)
    ia = 59.0
    assert out['2026-03']['v'] == pytest.approx(1180 * 58 / ia, abs=0.1)
    assert out['2025-01']['v'] == pytest.approx(1180 * 44 / ia, abs=0.1)
    assert all(e['basis'] == 'estimated' and e['method'] == 'backcast' for e in out.values())
    assert all(is_est(e) for e in out.values())
    assert '2026-04' not in out                            # the anchor month itself is measured
    assert json_safe(out)


def test_backcast_limited_to_24_months_and_months_arg():
    tr = trends(start='2022-01', n=52)
    out = s.backcast(1000, '2026-04', tr, 0.9, months=36)
    assert len(out) == 24
    assert min(out) == '2024-04' and max(out) == '2026-03'
    assert list(out) == sorted(out)
    assert len(s.backcast(1000, '2026-04', tr, 0.9, months=6)) == 6
    assert s.backcast(1000, '2026-04', tr, 0.9, months=0) == {}


def test_backcast_band_widens_with_distance():
    out = s.backcast(1000, '2026-04', trends(), 0.9)
    months = sorted(out, reverse=True)                     # nearest first
    widths = [math.log(out[m]['hi'] / out[m]['lo']) for m in months]
    assert all(b > a for a, b in zip(widths, widths[1:]))
    assert [out[m]['distance'] for m in months[:3]] == [1, 2, 3]


def test_backcast_quality_and_anchor_band_widen():
    tr = trends()
    good = s.backcast(1000, '2026-04', tr, 0.95)['2026-01']
    poor = s.backcast(1000, '2026-04', tr, 0.55)['2026-01']
    wide_anchor = s.backcast(s.est(1000, 400, 2500, 'estimated', 'low'), '2026-04', tr, 0.95)['2026-01']
    w = lambda e: e['hi'] / e['lo']
    assert w(poor) > w(good) and w(wide_anchor) > w(good)
    assert good['v'] == poor['v'] == wide_anchor['v'] == 1000


def test_backcast_anchor_month_fallback_and_string_index():
    tr = trends(n=39)                                      # ends 2026-03, so April has no index value
    out = s.backcast(1000, '2026-04', tr, 0.9)
    assert out['2026-03']['v'] == 1000
    as_dict = {k.strftime('%Y-%m'): v for k, v in trends(n=40).items()}
    ref = s.backcast(1000, '2026-04', trends(n=40), 0.9)
    assert s.backcast(1000, pd.Timestamp('2026-04-01'), as_dict, 0.9) == ref
    # anchor more than 2 months past the index: nothing to scale from
    assert s.backcast(1000, '2026-08', tr, 0.9) == {}


def test_backcast_zero_index_months():
    tr = trends(fn=lambda i: 0.0 if i < 30 else 40.0)
    out = s.backcast(800, '2026-04', tr, 0.8)
    z = out['2025-06']
    assert z['v'] == 0 and z['lo'] == 0 and z['hi'] > 0 and z['conf'] == 'low'
    assert json_safe(out)


@pytest.mark.parametrize('anchor,month,tr', [
    (None, '2026-04', trends()), (0, '2026-04', trends()), (-5, '2026-04', trends()),
    (1000, None, trends()), (1000, 'junk', trends()), (1000, '2026-04', None),
    (1000, '2026-04', pd.Series(dtype=float)), (1000, '2026-04', trends(fn=lambda i: 0.0)),
    ({'v': None}, '2026-04', trends()),
])
def test_backcast_empty(anchor, month, tr):
    assert s.backcast(anchor, month, tr, 0.9) == {}


# ---------- forecast ----------

def history(n=24, start='2024-07', fn=lambda i, mo: 1000.0):
    out = {}
    for i in range(n):
        m = s._add_months(start, i)
        out[m] = fn(i, int(m[5:7]))
    return out


SI = [0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.2, 1.0, 0.8]
SI = [x * 12 / sum(SI) for x in SI]


def test_forecast_flat_is_flat():
    out = s.forecast(history(), [1.0] * 12, 0.0)
    assert list(out) == ['2026-07', '2026-08', '2026-09', '2026-10', '2026-11', '2026-12']
    for e in out.values():
        assert e['v'] == pytest.approx(1000) and e['basis'] == 'forecast' and e['method'] == 'forecast'
        assert is_est(e)
    assert json_safe(out)


def test_forecast_band_widens_with_horizon():
    rng = np.random.default_rng(3)
    h = history(fn=lambda i, mo: 1000 * float(np.exp(rng.normal(0, 0.1))))
    out = s.forecast(h, None, 0.1, months=12)
    widths = [math.log(e['hi'] / e['lo']) for e in out.values()]
    assert len(out) == 12
    assert all(b > a for a, b in zip(widths, widths[1:]))
    assert [e['h'] for e in out.values()] == list(range(1, 13))


def test_forecast_follows_seasonal_index():
    h = history(fn=lambda i, mo: 500.0 * SI[mo - 1])
    out = s.forecast(h, SI, 0.0, months=12)
    for m, e in out.items():
        assert e['v'] == pytest.approx(500.0 * SI[int(m[5:7]) - 1], rel=1e-3)
    # a seasonality() dict works too
    assert s.forecast(h, {'si': SI, 'strength': 0.8}, 0.0, months=12) == out


def test_forecast_damped_growth():
    out = s.forecast(history(), None, 0.5, months=12)
    vs = [e['v'] for e in out.values()]
    assert all(b > a for a, b in zip(vs, vs[1:]))           # still growing ...
    r = 1.5 ** (1 / 12) - 1
    assert vs[0] == pytest.approx(1000 * (1 + r) ** 0.85, rel=1e-3)
    assert vs[-1] < 1000 * 1.5                               # ... but damped well below a full year of +50%
    cum = sum(0.85 ** j for j in range(1, 13))
    assert vs[-1] == pytest.approx(1000 * (1 + r) ** cum, rel=1e-3)
    down = s.forecast(history(), None, -0.5, months=3)
    assert [e['v'] for e in down.values()] == sorted([e['v'] for e in down.values()], reverse=True)
    # a huge yoy is capped at +8% a month
    big = s.forecast(history(), None, 50.0, months=1)
    assert next(iter(big.values()))['v'] == pytest.approx(1000 * 1.08 ** 0.85, rel=1e-3)


def test_forecast_level_from_last_three_full_months():
    h = history(n=12, fn=lambda i, mo: 100.0 if i < 9 else 400.0)
    h['2025-07'] = {'v': 5.0, 'lo': 2.0, 'hi': 9.0, 'partial': True}      # partial month: skipped for the level
    out = s.forecast(h, None, None, months=2)
    assert list(out) == ['2025-08', '2025-09']                            # starts after the last month given
    assert out['2025-08']['v'] == pytest.approx(400.0)


def test_forecast_months_clipped_and_crosses_year():
    h = history(n=6, start='2026-06')                                    # ends 2026-11
    out = s.forecast(h, None, 0.0, months=20)
    assert len(out) == 12 and list(out)[:2] == ['2026-12', '2027-01']
    assert s.forecast(h, None, 0.0, months=0) == {}
    assert len(s.forecast(h, None, 0.0, months=None)) == 6


def test_forecast_short_history_and_est_inputs():
    one = s.forecast({'2026-05': s.est(300, 200, 450, 'estimated', 'medium')}, None, 0.0, months=3)
    assert list(one) == ['2026-06', '2026-07', '2026-08']
    assert all(e['v'] == pytest.approx(300) for e in one.values())
    zero = s.forecast({'2026-05': 0}, None, 0.0, months=2)
    assert all(e['v'] == 0 and e['conf'] == 'low' for e in zero.values())
    assert json_safe(zero)


@pytest.mark.parametrize('units', [None, {}, [], 'x', {'junk': 5}, {'2026-01': None}, {'2026-01': -3}])
def test_forecast_empty(units):
    assert s.forecast(units, SI, 0.1) == {}


def test_forecast_bad_si_and_yoy():
    out = s.forecast(history(), [1, 2, None], 'abc')
    assert all(e['v'] == pytest.approx(1000) for e in out.values())
    out = s.forecast(history(), 'nonsense', float('nan'))
    assert all(e['v'] == pytest.approx(1000) for e in out.values())


# ---------- revenue ----------

def test_revenue_by_month_with_carry():
    units = {'2026-01': s.est(100, 80, 130, 'estimated', 'medium'), '2026-02': 200,
             '2026-03': {'v': 50, 'lo': 30, 'hi': 70, 'basis': 'reported', 'conf': 'medium', 'method': 'h10'}}
    prices = {'2026-01': 20.0, '2026-03': s.est(25.0, 25.0, 25.0, 'measured', 'high')}
    out = s.revenue(units, prices)
    assert out['2026-01']['v'] == 2000 and out['2026-01']['lo'] == 1600 and out['2026-01']['hi'] == 2600
    assert out['2026-01']['price_from'] == 'month'
    assert out['2026-02']['v'] == 4000 and out['2026-02']['price_from'] == 'carried'
    assert out['2026-03']['v'] == 1250 and out['2026-03']['basis'] == 'reported'
    assert out['2026-03']['method'] == 'h10'
    assert all(is_est(e) for e in out.values())
    assert json_safe(out)


def test_revenue_scalar_and_nearest_price():
    bc = s.backcast(1000, '2026-04', trends(), 0.9, months=3)
    out = s.revenue(bc, 30)
    assert set(out) == set(bc)
    assert all(out[m]['v'] == pytest.approx(bc[m]['v'] * 30, abs=0.05) for m in bc)
    assert all(e['method'] == 'backcast' and e['price_from'] == 'given' for e in out.values())
    # no earlier price: the nearest later one is assumed and the confidence drops a step
    out = s.revenue({'2025-01': s.est(100, 90, 110, 'estimated', 'high')}, {'2026-01': 10})
    assert out['2025-01']['price_from'] == 'assumed' and out['2025-01']['conf'] == 'medium'
    # carry limit: a price more than 3 months old is not carried
    out = s.revenue({'2026-06': 10}, {'2026-01': 10, '2026-12': 12})
    assert out['2026-06']['price_from'] == 'assumed'


@pytest.mark.parametrize('units,prices', [
    (None, {'2026-01': 10}), ({}, 10), ({'2026-01': 10}, None), ({'2026-01': 10}, {}),
    ({'2026-01': 10}, {'2026-01': 0}), ({'2026-01': None}, 10), ({'2026-01': 10}, 'x'),
])
def test_revenue_empty(units, prices):
    assert s.revenue(units, prices) == {}
