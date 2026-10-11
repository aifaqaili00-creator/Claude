"""Tests for engine/badge.py. Run with:  python -m pytest -q tests/test_engine_badge.py"""
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import badge as b  # noqa: E402

EST_KEYS = {'v', 'lo', 'hi', 'basis', 'conf'}


def json_safe(x):
    """Raises if x holds NaN / inf or anything json can't write."""
    return json.dumps(x, allow_nan=False)


# ---- ladder ----

def test_ladder_shape():
    us = b.LADDER['US']
    assert us[:5] == [10, 20, 30, 40, 50]
    assert us[5:14] == list(range(100, 1000, 100))
    assert us[14:23] == list(range(1000, 10000, 1000))
    assert us[23:32] == list(range(10000, 100000, 10000))
    assert us[-1] == 100000
    assert len(us) == 33
    assert all(x < y for x, y in zip(us, us[1:]))
    assert b.LADDER['AU'] == us and b.LADDER['AE'] == us


def test_thresh_is_a_dict():
    assert isinstance(b.THRESH, dict) and b.THRESH['open_hi_mult'] == 2.0


# ---- parse_badge ----

@pytest.mark.parametrize('text, want', [
    ('5K+ bought in past month', 5000),
    ('200+', 200),
    ('1.5K+', 1500),
    ('None', None),
    (None, None),
    ('', None),
    ('   ', None),
    ('50+ bought in past month', 50),
    ('100K+ bought in past month', 100000),
    ('1,000+ bought in past month', 1000),
    ('10K+ bought in past month', 10000),
    ('2k+ bought in past month', 2000),
    ('1M+ bought in past month', 1000000),
    ("Amazon's Choice 300+ bought in past month", 300),
    ('List: $20.99  700+ bought in past month', 700),
    ('1,5K+', 1500),                      # comma as the decimal point
    ('2.000+', 2000),                     # dot as the thousands separator
    ('٥٠+', 50),                          # Arabic-Indic digits
    ('300\xa0+ bought in past month', 300),
    ('400', 400),
    ('1.5K', 1500),
    ('List Price $20.99', None),          # a price is not a badge
    ('bought in past month', None),
    ('0+', None),
    ('abc', None),
])
def test_parse_badge_text(text, want):
    assert b.parse_badge(text) == want


def test_parse_badge_numbers():
    assert b.parse_badge(3000) == 3000
    assert b.parse_badge(3000.0) == 3000
    assert b.parse_badge(np.int64(500)) == 500
    assert b.parse_badge(np.float64(500.0)) == 500
    assert b.parse_badge(float('nan')) is None
    assert b.parse_badge(float('inf')) is None
    assert b.parse_badge(0) is None
    assert b.parse_badge(-50) is None
    assert b.parse_badge(True) is None
    assert b.parse_badge([1, 2]) is None
    assert isinstance(b.parse_badge('5K+'), int)


# ---- bucket ----

@pytest.mark.parametrize('L, want', [
    (10, (10, 20)),
    (40, (40, 50)),
    (50, (50, 100)),
    (100, (100, 200)),
    (900, (900, 1000)),
    (1000, (1000, 2000)),
    (9000, (9000, 10000)),
    (90000, (90000, 100000)),
    (100000, (100000, None)),             # top label: open
    (1500, (1500, None)),                 # not on the ladder: open
    (200000, (200000, None)),
    (None, (None, None)),
    (0, (None, None)),
    (-100, (None, None)),
    (float('nan'), (None, None)),
    ('junk', (None, None)),
])
def test_bucket(L, want):
    assert b.bucket(L) == want


def test_bucket_types_labels_and_markets():
    L, U = b.bucket(1000.0)
    assert (L, U) == (1000, 2000) and isinstance(L, int) and isinstance(U, int)
    assert b.bucket('5K+ bought in past month') == (5000, 6000)
    for m in ('US', 'AU', 'AE', 'au', 'xx', None):
        assert b.bucket(20, m) == (20, 30)
        assert b.bucket(100000, m) == (100000, None)


# ---- pareto_mean ----

@pytest.mark.parametrize('L, want', [(50, 69.3), (100, 138.6), (1000, 1386.3), (10000, 13862.9), (900, 948.2)])
def test_pareto_mean_examples(L, want):
    assert b.pareto_mean(*b.bucket(L)) == pytest.approx(want, abs=0.05)


def test_pareto_mean_inside_bucket_and_below_midpoint():
    lad = b.LADDER['US']
    for L, U in zip(lad, lad[1:]):
        m = b.pareto_mean(L, U)
        assert L < m < (L + U) / 2              # Pareto mass leans to the low end


def test_pareto_mean_open_and_bad_input():
    assert b.pareto_mean(100000, None) == pytest.approx(150000)
    assert b.pareto_mean(None, 100) is None
    assert b.pareto_mean(0, 100) is None
    assert b.pareto_mean(200, 100) is None
    assert b.pareto_mean(100, 100) == 100
    assert b.pareto_mean(float('nan'), 100) is None


# ---- badge_estimate ----

def test_badge_estimate_bounded():
    e = b.badge_estimate(1000)
    assert set(e) == EST_KEYS
    assert e['v'] == pytest.approx(1386.3, abs=0.05)
    assert (e['lo'], e['hi']) == (1000, 1999)
    assert e['basis'] == 'estimated' and e['conf'] in ('high', 'medium', 'low')
    assert e['lo'] <= e['v'] <= e['hi']
    e = b.badge_estimate(50, 'AU')
    assert (e['lo'], e['hi']) == (50, 99) and e['v'] == pytest.approx(69.3, abs=0.05)
    json_safe(e)


def test_badge_estimate_open_top_bucket():
    e = b.badge_estimate(100000)
    assert (e['lo'], e['hi']) == (100000, 200000)
    assert e['basis'] == 'assumed' and e['conf'] == 'low'
    assert e['lo'] <= e['v'] <= e['hi']
    e = b.badge_estimate(1500, 'AE')                 # off the ladder: treated as open too
    assert (e['lo'], e['hi'], e['basis']) == (1500, 3000, 'assumed')
    json_safe(e)


def test_badge_estimate_missing():
    for x in (None, 'None', '', 0, float('nan'), 'junk'):
        assert b.badge_estimate(x) is None
    assert b.badge_estimate('200+ bought in past month')['lo'] == 200


def test_est_helper():
    assert b.est(1, 0, 2, 'measured', 'high') == {'v': 1, 'lo': 0, 'hi': 2, 'basis': 'measured', 'conf': 'high'}


# ---- truncated_lognormal_mean ----

def mc_mean(mu, sigma, L, U, n=1_000_000, seed=0):
    x = np.exp(np.random.default_rng(seed).normal(mu, sigma, n))
    keep = (x >= (L or 0)) & (x < (U if U is not None else np.inf))
    assert keep.sum() > 20000                         # enough samples for a 1% check
    return float(x[keep].mean())


@pytest.mark.parametrize('mu, sigma, L, U', [
    (math.log(300), 0.8, 100, 200),
    (math.log(300), 0.8, 1000, None),               # open top bucket
    (math.log(150), 0.5, 100, 200),
    (math.log(2000), 1.0, 1000, 2000),
    (math.log(800), 1.5, 50, 100),
    (math.log(10000), 2.0, None, 1000),             # open below
    (math.log(60), 0.3, 50, 100),
    (math.log(30000), 0.9, 20000, 30000),
])
def test_truncated_lognormal_matches_monte_carlo(mu, sigma, L, U):
    got = b.truncated_lognormal_mean(mu, sigma, L, U)
    assert got == pytest.approx(mc_mean(mu, sigma, L, U), rel=0.01)


def test_truncated_lognormal_whole_line_is_lognormal_mean():
    mu, s = 5.0, 0.7
    assert b.truncated_lognormal_mean(mu, s, None, None) == pytest.approx(math.exp(mu + s * s / 2), rel=1e-5)
    assert b.truncated_lognormal_mean(mu, s, 1e-6, 1e9) == pytest.approx(math.exp(mu + s * s / 2), rel=1e-5)


def test_truncated_lognormal_far_tails_stay_in_bucket():
    # bucket far above the distribution: the mean hugs L
    m = b.truncated_lognormal_mean(math.log(50), 0.5, 10000, 20000)
    assert 10000 <= m < 11000
    m_open = b.truncated_lognormal_mean(math.log(50), 0.5, 10000, None)
    assert m_open == pytest.approx(m, rel=1e-3)
    # bucket far below the distribution: the mean hugs U
    m = b.truncated_lognormal_mean(math.log(1e5), 0.5, 50, 100)
    assert 90 < m <= 100
    # absurdly far away still gives a finite number inside the bucket
    m = b.truncated_lognormal_mean(0.0, 0.05, 1e5, 2e5)
    assert 1e5 <= m <= 2e5 and math.isfinite(m)
    m = b.truncated_lognormal_mean(30.0, 0.05, 50, 100)
    assert 50 <= m <= 100


def test_truncated_lognormal_monotone_and_smooth_in_mu():
    # sweeps the tail switch inside log_Phi (z = -8) without jumps
    mus = np.linspace(math.log(1000) - 12 * 0.6, math.log(2000), 400)
    ms = [b.truncated_lognormal_mean(m, 0.6, 1000, 2000) for m in mus]
    assert all(1000 <= m < 2000 for m in ms)
    assert all(y >= x - 1e-6 for x, y in zip(ms, ms[1:]))
    assert max(abs(y - x) for x, y in zip(ms, ms[1:])) < 10


def test_truncated_lognormal_zero_sigma_limit():
    assert b.truncated_lognormal_mean(math.log(150), 0.0, 100, 200) == pytest.approx(150)
    assert b.truncated_lognormal_mean(math.log(50), 0.0, 100, 200) == pytest.approx(100)
    assert b.truncated_lognormal_mean(math.log(500), 0.0, 100, 200) == pytest.approx(200)
    # tiny sigma agrees with the limit
    assert b.truncated_lognormal_mean(math.log(150), 1e-4, 100, 200) == pytest.approx(150, rel=1e-3)


def test_truncated_lognormal_bad_input():
    assert b.truncated_lognormal_mean(None, 0.5, 100, 200) is None
    assert b.truncated_lognormal_mean(5.0, None, 100, 200) is None
    assert b.truncated_lognormal_mean(5.0, -1, 100, 200) is None
    assert b.truncated_lognormal_mean(float('nan'), 0.5, 100, 200) is None
    assert b.truncated_lognormal_mean(5.0, 0.5, 200, 100) is None
    assert b.truncated_lognormal_mean(5.0, 0.5, 100, 100) is None
    assert b.truncated_lognormal_mean(5.0, 0.5, -5, 100) is None
    assert b.truncated_lognormal_mean(5.0, 0.5, 'x', 100) is None


def test_truncated_lognormal_beats_pareto_when_bucket_is_far_above():
    # far up the right tail the density falls faster than Pareto(1), so the mean sits nearer L
    t = b.truncated_lognormal_mean(math.log(100), 0.6, 1000, 2000)
    assert 1000 < t < b.pareto_mean(1000, 2000)


# ---- page_demand ----

def test_page_demand_sums():
    vals = ['5K+ bought in past month', None, 200, '50+', float('nan'), 'None', 1000]
    d = b.page_demand(vals)
    assert d['badged'] == 4 and d['open'] == 0 and d['top'] == 5000
    assert d['low_sum'] == 5000 + 200 + 50 + 1000
    assert d['high_sum'] == 5999 + 299 + 99 + 1999
    want_mid = b.pareto_mean(5000, 6000) + b.pareto_mean(200, 300) + b.pareto_mean(50, 100) + b.pareto_mean(1000, 2000)
    assert d['mid_sum'] == pytest.approx(want_mid, abs=0.05)
    assert d['low_sum'] < d['mid_sum'] < d['high_sum']
    e = d['est']
    assert set(e) >= EST_KEYS
    assert (e['v'], e['lo'], e['hi']) == (d['mid_sum'], d['low_sum'], d['high_sum'])
    assert e['basis'] == 'estimated' and e['conf'] == 'medium'
    json_safe(d)


def test_page_demand_beats_old_lower_bound_total():
    vals = [50, 100, 300, 1000, 2000]
    d = b.page_demand(vals)
    assert d['low_sum'] == sum(vals)                  # what bought_total used to report
    assert d['mid_sum'] > 1.25 * sum(vals)            # bucket means lift it by roughly a third


def test_page_demand_open_bucket():
    d = b.page_demand([100000, 50])
    assert d['open'] == 1 and d['top'] == 100000
    assert d['low_sum'] == 100050 and d['high_sum'] == 200000 + 99
    assert d['mid_sum'] == pytest.approx(150000 + 69.3, abs=0.05)
    assert d['est']['conf'] == 'low'                  # most of the total is an assumption


def test_page_demand_empty():
    for vals in (None, [], [None, None], ['None', '', float('nan')]):
        d = b.page_demand(vals)
        assert d['badged'] == 0 and d['open'] == 0
        assert d['low_sum'] is None and d['mid_sum'] is None and d['high_sum'] is None
        assert d['top'] is None and d['est'] is None
        json_safe(d)


def test_page_demand_market_and_types():
    d = b.page_demand([10, 20, np.int64(30)], market='AE')
    assert d['badged'] == 3 and d['low_sum'] == 60 and d['high_sum'] == 19 + 29 + 39
    assert isinstance(d['low_sum'], int) and isinstance(d['high_sum'], int) and isinstance(d['top'], int)
    json_safe(d)
