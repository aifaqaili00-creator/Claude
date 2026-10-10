"""Tests for engine/curve.py. Run with:  python -m pytest -q tests/test_engine_curve.py"""
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import curve as cv  # noqa: E402
from engine.badge import LADDER  # noqa: E402

EST_KEYS = {'v', 'lo', 'hi', 'basis', 'conf'}
FIT_KEYS = {'market', 'beta', 'alpha', 'sigma', 'delta_h', 'n_obs', 'status', 'cov', 'diag'}
NODE = '1055398'


def json_safe(x):
    """Raises if x holds NaN / inf or anything json can't write."""
    return json.dumps(x, allow_nan=False)


def simulate(n, seed, alpha=13.5, beta=-0.85, sigma=0.6, hide=0.4, h10_n=600, delta=0.15, node=NODE,
             market='US', asin_prefix='A'):
    """ln S = alpha + beta ln R + N(0, sigma^2), R log-uniform in [100, 300k]. Each product shows its
    badge bucket unless hidden (40% dropped) or below the smallest label; Helium 10 rows for h10_n
    random products read S * exp(delta + N(0, 0.35^2)). Returns (obs, lnR, lnS)."""
    rng = np.random.default_rng(seed)
    lnR = rng.uniform(math.log(100), math.log(300000), n)
    lnS = alpha + beta * lnR + rng.normal(0, sigma, n)
    lad = np.array(LADDER[market], dtype=float)
    obs = []
    for i in range(n):
        j = int(np.searchsorted(lad, math.exp(lnS[i]), side='right')) - 1
        if j >= 0 and rng.random() >= hide:
            U = float(lad[j + 1]) if j + 1 < len(lad) else None
            obs.append({'market': market, 'node': node, 'ln_bsr': float(lnR[i]), 'low': float(lad[j]), 'high': U,
                        'kind': 'badge', 'age_days': float(rng.uniform(0, 30)), 'asin': f'{asin_prefix}{seed}_{i}'})
    for i in (rng.choice(n, h10_n, replace=False) if h10_n else []):
        obs.append({'market': market, 'node': node, 'ln_bsr': float(lnR[i]), 'low': None, 'high': None,
                    'kind': 'h10', 'value': float(math.exp(lnS[i] + delta + rng.normal(0, 0.35))),
                    'age_days': 10.0, 'asin': f'{asin_prefix}{seed}_{i}'})
    return obs, lnR, lnS


@pytest.fixture(scope='module')
def sim_fit():
    obs, lnR, lnS = simulate(3000, 1)
    return cv.fit_market(obs, 'US'), obs, lnR, lnS


# ---- shape and missing data ----

def test_thresh_is_a_dict():
    assert isinstance(cv.THRESH, dict)
    assert cv.THRESH['beta_prior'] == (-0.85, 0.15)
    assert cv.THRESH['alpha_prior']['US'] == (13.68, 1.0)
    assert cv.THRESH['alpha_node_sd'] == 1.5 and cv.THRESH['sigma_scale'] == 0.8
    assert cv.THRESH['half_life_days'] == 40.0 and cv.THRESH['calibrated_n'] == 30
    assert cv.THRESH['node_min_obs'] == 40 and cv.THRESH['delta_min_asins'] == 20


@pytest.mark.parametrize('obs', [None, [], 'junk', 5, {'a': 1}, [None, 'x', 3, {}],
                                 [{'kind': 'badge'}, {'kind': 'badge', 'ln_bsr': 5, 'low': None},
                                  {'kind': 'h10', 'ln_bsr': 5, 'value': 0},
                                  {'kind': 'own', 'ln_bsr': float('nan'), 'value': 10},
                                  {'kind': 'mystery', 'ln_bsr': 5, 'low': 100, 'high': 200}]])
def test_empty_or_bad_obs_give_the_prior(obs):
    c = cv.fit_market(obs, 'US')
    assert FIT_KEYS <= set(c)
    assert c['n_obs'] == 0 and c['status'] == 'prior_only' and c['market'] == 'US'
    assert c['beta'] == pytest.approx(-0.85, abs=1e-6)
    assert c['alpha'] == {'_market': pytest.approx(13.68, abs=1e-6)}
    assert c['sigma'] == pytest.approx(0.8, abs=1e-4)      # mode of the half-normal on the log scale
    assert c['delta_h'] == 0.0
    assert c['diag'] == {'hit': None, 'within1': None, 'cov80': None, 'n': 0}
    assert c['cov'][0][0] == pytest.approx(0.15 ** 2, rel=1e-3)
    assert c['cov'][1][1] == pytest.approx(1.0, rel=1e-3)
    json_safe(c)


def test_market_priors():
    au = cv.fit_market([], 'AU')
    ae = cv.fit_market([], 'ae')
    other = cv.fit_market([], 'UK')
    blank = cv.fit_market([], None)
    assert au['alpha']['_market'] == pytest.approx(13.68 - math.log(15), abs=1e-6)
    assert ae['market'] == 'AE' and ae['alpha']['_market'] == pytest.approx(13.68 - math.log(25), abs=1e-6)
    assert au['cov'][1][1] == pytest.approx(1.5 ** 2, rel=1e-3)
    assert other['alpha']['_market'] == pytest.approx(13.68, abs=1e-6)
    assert other['cov'][1][1] == pytest.approx(2.25, rel=1e-3)
    assert blank['market'] == 'US'


def test_rows_are_filtered_and_counted():
    good = {'market': 'US', 'node': 1, 'ln_bsr': 8.0, 'low': 100, 'high': 200, 'kind': 'badge', 'asin': 'A'}
    obs = [
        good,
        dict(good, market='AU'),                         # other market: skipped
        dict(good, market=None),                         # no market: taken as this one
        dict(good, ln_bsr=None, bsr=3000),               # ln_bsr missing, bsr given
        dict(good, ln_bsr=None, bsr=0),                  # no usable rank
        dict(good, low=0),                               # bad low
        dict(good, high=100),                            # empty bucket
        dict(good, high=50),                             # reversed bucket
        dict(good, high='abc'),                          # unreadable top: still S >= low
        dict(good, high=float('nan')),                   # (a DataFrame turns None into NaN)
        dict(good, high=None),                           # open top bucket -> lower bound
        dict(good, kind='badge_lb', high=None),
        {'market': 'US', 'ln_bsr': 8.0, 'kind': 'h10', 'value': 150, 'asin': 'A'},
        {'market': 'US', 'ln_bsr': 8.0, 'kind': 'h10', 'value': -1, 'asin': 'A'},
        {'market': 'us', 'ln_bsr': 8.0, 'kind': 'OWN', 'value': 140, 'asin': 'B'},
        {'market': 'US', 'ln_bsr': 8.0, 'kind': None, 'value': 140},
    ]
    c = cv.fit_market(obs, 'us')
    assert c['n_obs'] == 9
    assert c['n_by_kind'] == {'badge': 6, 'badge_lb': 1, 'h10': 1, 'own': 1}
    json_safe(c)


def test_dataframe_input_works():
    pd = pytest.importorskip('pandas')
    obs, _, _ = simulate(200, 5, h10_n=0)
    a = cv.fit_market(obs, 'US')
    obs.append(dict(obs[0], low=100000, high=None, asin='TOP'))     # open bucket: NaN in a DataFrame
    a = cv.fit_market(obs, 'US')
    b = cv.fit_market(pd.DataFrame(obs), 'US')
    assert a['n_obs'] == b['n_obs'] == len(obs)
    assert cv.fit_market(iter(obs), 'US')['n_obs'] == len(obs)        # any iterable
    assert a['beta'] == pytest.approx(b['beta'], abs=1e-9)


# ---- the spec's recovery test ----

def test_recovers_simulated_curve(sim_fit):
    c, obs, _, _ = sim_fit
    assert c['status'] == 'calibrated' and c['n_obs'] == len(obs) >= 2000
    assert c['converged'] is True
    assert abs(c['beta'] + 0.85) < 0.06
    assert abs(c['alpha'][NODE] - 13.5) < 0.2
    assert abs(c['sigma'] - 0.6) < 0.08
    assert NODE in c['nodes'] and c['nodes'][NODE] >= 40
    json_safe(c)


def test_h10_bias_is_estimated(sim_fit):
    c = sim_fit[0]
    assert c['delta_free'] is True
    assert abs(c['delta_h'] - 0.15) < 0.1


def test_cv_diagnostics(sim_fit):
    d = sim_fit[0]['diag']
    assert 0.72 <= d['cov80'] <= 0.88
    assert 0 < d['hit'] <= d['within1'] <= 1
    assert d['n'] == sum(1 for o in sim_fit[1] if o['kind'] == 'badge')


def test_predictive_band_covers_true_sales(sim_fit):
    c, obs, lnR, lnS = sim_fit
    inside = 0
    for x, y in zip(lnR, lnS):
        p = cv.predict(c, NODE, math.exp(x), sigma_nu=0.0)
        inside += p['lo'] <= math.exp(y) <= p['hi']
    assert 0.74 <= inside / len(lnR) <= 0.86


def test_recovery_holds_across_seeds():
    for seed in (11, 12, 13):
        obs, _, _ = simulate(2500, seed)
        c = cv.fit_market(obs, 'US')
        assert abs(c['beta'] + 0.85) < 0.06
        assert abs(c['alpha'][NODE] - 13.5) < 0.2
        assert 0.72 <= c['diag']['cov80'] <= 0.88


def test_prior_only_below_30_observations():
    obs, _, _ = simulate(400, 3, h10_n=0)
    obs = [o for o in obs if o['kind'] == 'badge']
    few = cv.fit_market(obs[:29], 'US')
    enough = cv.fit_market(obs[:30], 'US')
    assert few['status'] == 'prior_only' and few['n_obs'] == 29
    assert few['diag'] == {'hit': None, 'within1': None, 'cov80': None, 'n': 0}
    assert enough['status'] == 'calibrated' and enough['diag']['n'] == 30
    tiny = cv.fit_market(obs[:5], 'US')
    assert tiny['status'] == 'prior_only'
    assert abs(tiny['beta'] + 0.85) < 0.15                   # the prior dominates
    assert abs(tiny['alpha']['_market'] - 13.68) < 1.0
    assert 0.1 < tiny['sigma'] < 1.2                          # no collapse to the sigma floor
    assert tiny['alpha'].keys() == {'_market'}               # nodes under 40 observations share alpha_m


def test_predictions_monotonic_in_bsr(sim_fit):
    c = sim_fit[0]
    for node in (NODE, None, 'unknown'):
        preds = [cv.predict(c, node, r) for r in (1, 10, 100, 1000, 1e4, 1e5, 1e6)]
        assert all(set(p) == EST_KEYS for p in preds)
        vs = [p['v'] for p in preds]
        assert all(a > b for a, b in zip(vs, vs[1:]))
        assert all(p['lo'] < p['v'] < p['hi'] and p['basis'] == 'estimated' for p in preds)
        json_safe(preds)


# ---- structure of the fit ----

def test_nodes_pool_under_40_observations():
    big, _, _ = simulate(800, 21, alpha=13.5, node='BIG', h10_n=0)
    small = [dict(o, node='SMALL') for o in simulate(100, 22, alpha=12.0, node='SMALL', h10_n=0)[0]][:20]
    c = cv.fit_market(big + small, 'US')
    assert 'BIG' in c['alpha'] and 'SMALL' not in c['alpha']
    assert set(c['cov_node']) == {'BIG'} and c['nodes'] == {'BIG': len(big)}
    assert cv.predict(c, 'SMALL', 5000) == cv.predict(c, None, 5000) == cv.predict(c, 'nope', 5000)
    assert cv.predict(c, 'BIG', 5000)['v'] != cv.predict(c, 'SMALL', 5000)['v']


def test_two_nodes_recover_their_own_intercepts():
    a, _, _ = simulate(1500, 31, alpha=13.5, node=1001, h10_n=0)
    b, _, _ = simulate(1500, 32, alpha=12.8, node=2002.0, h10_n=0, asin_prefix='B')
    c = cv.fit_market(a + b, 'US')
    assert set(c['alpha']) == {'1001', '2002', '_market'}
    assert abs(c['alpha']['1001'] - 13.5) < 0.2
    assert abs(c['alpha']['2002'] - 12.8) < 0.2
    assert abs(c['beta'] + 0.85) < 0.06
    assert cv.predict(c, 1001, 3000)['v'] > cv.predict(c, '2002', 3000)['v']


def test_delta_fixed_with_few_matched_asins(monkeypatch):
    obs, _, _ = simulate(1500, 41, h10_n=0)
    badges_only = cv.fit_market(obs, 'US')
    rng = np.random.default_rng(4)
    h10 = []
    for i in range(300):                        # biased H10 rows on ASINs that never show a badge
        x = rng.uniform(math.log(100), math.log(300000))
        h10.append({'market': 'US', 'node': NODE, 'ln_bsr': x, 'kind': 'h10', 'asin': f'H{i}',
                    'value': math.exp(13.5 - 0.85 * x + 0.5 + rng.normal(0, 0.6))})
    c = cv.fit_market(obs + h10, 'US')
    assert c['delta_free'] is False and c['delta_h'] == 0.0
    monkeypatch.setitem(cv.THRESH, 'h10_fixed_weight', 1.0)
    heavier = cv.fit_market(obs + h10, 'US')
    v = [cv.predict(f, NODE, 3000)['v'] for f in (badges_only, c, heavier)]
    assert v[0] < v[1] < v[2]           # the unfitted bias leaks into the curve, less at half weight


def test_h10_only_fit():
    rng = np.random.default_rng(5)
    obs = []
    for i in range(400):
        x = rng.uniform(math.log(100), math.log(300000))
        obs.append({'market': 'US', 'ln_bsr': x, 'kind': 'h10', 'asin': f'H{i}',
                    'value': math.exp(13.5 - 0.85 * x + rng.normal(0, 0.6))})
    c = cv.fit_market(obs, 'US')
    assert c['status'] == 'calibrated' and c['delta_free'] is False
    assert abs(c['beta'] + 0.85) < 0.1 and abs(c['alpha']['_market'] - 13.5) < 0.4
    assert c['diag']['n'] == 0 and c['diag']['hit'] is None       # diagnostics need badges
    json_safe(c)


def test_recency_weight_favours_new_data():
    new, _, _ = simulate(600, 51, alpha=13.5, h10_n=0)
    old, _, _ = simulate(600, 52, alpha=12.0, h10_n=0, asin_prefix='O')
    old = [dict(o, age_days=400) for o in old]
    c = cv.fit_market(new + old, 'US')
    assert abs(c['alpha'][NODE] - 13.5) < 0.25
    flipped = cv.fit_market([dict(o, age_days=400) for o in new] + [dict(o, age_days=0) for o in old], 'US')
    assert abs(flipped['alpha'][NODE] - 12.0) < 0.25
    # bad ages count as fresh
    same = cv.fit_market([dict(o, age_days=None) for o in new], 'US')
    neg = cv.fit_market([dict(o, age_days=-5) for o in new], 'US')
    assert same['alpha'][NODE] == pytest.approx(neg['alpha'][NODE], abs=1e-9)


def test_lower_bounds_push_the_curve_up():
    obs, _, _ = simulate(600, 61, h10_n=0)
    base = cv.fit_market(obs, 'US')
    lbs = [{'market': 'US', 'node': NODE, 'ln_bsr': o['ln_bsr'], 'low': o['low'] * 3, 'kind': 'badge_lb',
            'asin': o['asin'] + 'p'} for o in obs[:200]]
    up = cv.fit_market(obs + lbs, 'US')
    assert up['alpha'][NODE] > base['alpha'][NODE]
    opened = cv.fit_market(obs + [dict(o, kind='badge', high=None) for o in lbs], 'US')   # open = lower bound
    assert opened['alpha'][NODE] == pytest.approx(up['alpha'][NODE], abs=1e-9)


def test_own_sales_pull_harder_than_h10():
    obs, _, _ = simulate(300, 71, h10_n=0)
    rng = np.random.default_rng(7)
    xs = rng.uniform(5, 11, 25)
    extra = [{'market': 'US', 'node': NODE, 'ln_bsr': float(x), 'value': math.exp(14.5 - 0.85 * x),
              'asin': f'Z{i}'} for i, x in enumerate(xs)]
    own = cv.fit_market(obs + [dict(e, kind='own') for e in extra], 'US')
    h10 = cv.fit_market(obs + [dict(e, kind='h10') for e in extra], 'US')
    base = cv.fit_market(obs, 'US')
    assert own['alpha'][NODE] > h10['alpha'][NODE] > base['alpha'][NODE]


def test_markets_are_fitted_separately():
    us, _, _ = simulate(800, 81, h10_n=0)
    au, _, _ = simulate(800, 82, alpha=13.5 - math.log(15), market='AU', h10_n=0, asin_prefix='U')
    cu = cv.fit_market(us + au, 'US')
    ca = cv.fit_market(us + au, 'AU')
    assert cu['n_obs'] == len(us) and ca['n_obs'] == len(au)
    assert ca['market'] == 'AU'
    assert abs(ca['alpha'][NODE] - (13.5 - math.log(15))) < 0.25
    assert cu['alpha'][NODE] - ca['alpha'][NODE] == pytest.approx(math.log(15), abs=0.35)


def test_truncation_at_the_badge_floor(monkeypatch):
    # AU sells ~15x less, so many listings sit under the smallest badge and never show one
    au_alpha = 13.5 - math.log(15)
    obs, _, _ = simulate(1500, 101, alpha=au_alpha, market='AU', h10_n=0, asin_prefix='U')
    c = cv.fit_market(obs, 'AU')
    assert c['floor'] == 10
    assert abs(c['beta'] + 0.85) < 0.06
    assert abs(c['alpha'][NODE] - au_alpha) < 0.2
    monkeypatch.setitem(cv.THRESH, 'truncate', False)
    naive = cv.fit_market(obs, 'AU')
    assert naive['floor'] is None
    assert naive['beta'] > c['beta'] + 0.04          # leaving the floor out flattens the curve


def test_floor_is_50_unless_lower_labels_are_seen():
    obs, _, _ = simulate(600, 102, h10_n=0)
    high = [o for o in obs if o['low'] >= 100]
    assert cv.fit_market(high, 'US')['floor'] == 50
    assert cv.fit_market(obs, 'US')['floor'] == 10
    lb_only = [dict(o, kind='badge_lb', high=None, low=30) for o in high[:40]]
    assert cv.fit_market(high + lb_only, 'US')['floor'] == 30
    h10 = [{'market': 'US', 'ln_bsr': 8.0, 'kind': 'h10', 'value': 100, 'asin': 'H'}]
    assert cv.fit_market(h10, 'US')['floor'] is None


def test_diag_with_a_floor_of_50():
    obs, _, _ = simulate(1500, 103, h10_n=0)
    high = [o for o in obs if o['low'] >= 50]
    c = cv.fit_market(high, 'US')
    assert c['floor'] == 50 and c['status'] == 'calibrated'
    assert abs(c['beta'] + 0.85) < 0.08
    d = c['diag']
    assert d['n'] == len(high) and 0 < d['hit'] <= d['within1'] <= 1 and 0.7 <= d['cov80'] <= 0.9


def test_node_named_like_the_market_key_is_pooled():
    obs, _, _ = simulate(300, 104, h10_n=0, node='_market')
    c = cv.fit_market(obs, 'US')
    assert set(c['alpha']) == {'_market'} and c['nodes'] == {}


def test_cov_shape():
    c = cv.fit_market(simulate(800, 91)[0], 'US')
    cov = np.array(c['cov'])
    assert cov.shape == (3, 3)
    assert np.allclose(cov, cov.T)
    assert np.all(np.linalg.eigvalsh(cov) > 0)
    assert c['cov_node'][NODE][0] > 0
    # far more data than prior: the slope is much tighter than its prior sd 0.15
    assert math.sqrt(cov[0, 0]) < 0.03


def test_diag_needs_several_asins():
    obs, _, _ = simulate(300, 95, h10_n=0)
    one_asin = [dict(o, asin='SAME') for o in obs]
    c = cv.fit_market(one_asin, 'US')
    assert c['status'] == 'calibrated'
    assert c['diag'] == {'hit': None, 'within1': None, 'cov80': None, 'n': 0}


def test_rows_without_asin_are_their_own_group():
    obs, _, _ = simulate(300, 96, h10_n=0)
    c = cv.fit_market([dict(o, asin=None) for o in obs], 'US')
    assert c['diag']['n'] == len(obs)


def test_fit_is_deterministic():
    obs, _, _ = simulate(500, 97, h10_n=100)
    assert cv.fit_market(obs, 'US') == cv.fit_market(obs, 'US')


def test_fast_enough_for_5000_observations():
    obs = []
    for k, a in enumerate((13.5, 13.0, 12.6, 13.9)):
        obs += simulate(1900, 200 + k, alpha=a, node=f'N{k}', h10_n=150, asin_prefix=f'N{k}_')[0]
    assert len(obs) >= 5000
    t = time.perf_counter()
    c = cv.fit_market(obs, 'US')
    assert time.perf_counter() - t < 2.0
    assert c['status'] == 'calibrated' and len(c['alpha']) == 5


def test_extreme_data_does_not_break_the_fit():
    rng = np.random.default_rng(9)
    obs = [{'market': 'US', 'ln_bsr': float(rng.uniform(0, 15)), 'low': 100000, 'high': None, 'kind': 'badge',
            'asin': f'X{i}'} for i in range(50)]
    obs += [{'market': 'US', 'ln_bsr': 14.0, 'low': 10, 'high': 20, 'kind': 'badge', 'asin': f'Y{i}'}
            for i in range(50)]
    obs += [{'market': 'US', 'ln_bsr': 1.0, 'kind': 'h10', 'value': 1e-3, 'asin': 'Z'}]
    c = cv.fit_market(obs, 'US')
    assert all(math.isfinite(v) for v in (c['beta'], c['sigma'], c['alpha']['_market']))
    assert cv.THRESH['sigma_min'] - 1e-9 <= c['sigma'] <= cv.THRESH['sigma_max'] + 1e-9
    json_safe(c)


# ---- predict ----

def _curve(sigma=0.6, cov=None, beta=-0.85, alpha=13.5):
    return {'market': 'US', 'beta': beta, 'alpha': {'_market': alpha}, 'sigma': sigma, 'delta_h': 0.0,
            'n_obs': 100, 'status': 'calibrated', 'cov': cov or [[0, 0, 0], [0, 0, 0], [0, 0, 0]], 'diag': {}}


def test_predict_mid_and_band():
    c = _curve(sigma=0.6)
    p = cv.predict(c, None, 1000)
    mu = 13.5 - 0.85 * math.log(1000)
    assert p['v'] == pytest.approx(math.exp(mu + 0.5 * 0.5 ** 2))   # sigma_nu 0.5 for one snapshot
    z = cv.THRESH['z80']
    assert p['lo'] == pytest.approx(p['v'] * math.exp(-z * 0.6))
    assert p['hi'] == pytest.approx(p['v'] * math.exp(z * 0.6))
    p2 = cv.predict(c, None, 1000, sigma_nu=0.25)
    assert p2['v'] == pytest.approx(math.exp(mu + 0.5 * 0.25 ** 2))
    p3 = cv.predict(c, None, 1000, sigma_nu=0)
    assert p3['v'] == pytest.approx(math.exp(mu))


def test_predict_delta_method():
    cov = [[0.01, -0.05, 0.0], [-0.05, 0.3, 0.0], [0.0, 0.0, 0.001]]
    c = _curve(sigma=0.6, cov=cov)
    x = math.log(2000)
    var_mu = x * x * 0.01 + 0.3 + 2 * x * -0.05
    z = cv.THRESH['z80']
    p = cv.predict(c, None, 2000, scatter=False)
    assert p['hi'] / p['v'] == pytest.approx(math.exp(z * math.sqrt(var_mu)))
    q = cv.predict(c, None, 2000)
    assert q['hi'] / q['v'] == pytest.approx(math.exp(z * math.sqrt(var_mu + 0.36)))
    assert q['v'] == pytest.approx(p['v'])
    # a node with its own intercept uses its own variance and covariance with beta
    c['alpha']['N1'] = 13.0
    c['cov_node'] = {'N1': [0.5, -0.02]}
    r = cv.predict(c, 'N1', 2000, scatter=False)
    var_n = x * x * 0.01 + 0.5 + 2 * x * -0.02
    assert r['hi'] / r['v'] == pytest.approx(math.exp(z * math.sqrt(var_n)))
    assert r['v'] == pytest.approx(math.exp(13.0 - 0.85 * x + 0.125))


def test_predict_conf_from_band_ratio():
    assert cv.predict(_curve(sigma=0.2), None, 500)['conf'] == 'high'      # hi/lo = 1.67
    assert cv.predict(_curve(sigma=0.5), None, 500)['conf'] == 'medium'    # 3.6
    assert cv.predict(_curve(sigma=0.8), None, 500)['conf'] == 'low'       # 7.8
    assert cv.predict(_curve(sigma=0.8), None, 500, scatter=False)['conf'] == 'high'
    assert cv.predict(cv.fit_market([], 'US'), None, 500)['conf'] == 'low'   # prior only: wide


def test_predict_calibrated_is_tighter_than_prior(sim_fit):
    c = sim_fit[0]
    prior = cv.fit_market([], 'US')
    p, q = cv.predict(c, NODE, 3000, scatter=False), cv.predict(prior, NODE, 3000, scatter=False)
    assert p['hi'] / p['lo'] < 1.3 < q['hi'] / q['lo']


@pytest.mark.parametrize('bsr', [None, 0, -5, float('nan'), float('inf'), 'abc', True])
def test_predict_bad_bsr(bsr):
    assert cv.predict(_curve(), None, bsr) is None


@pytest.mark.parametrize('curve', [None, 'x', {}, {'beta': -0.85}, {'alpha': {'_market': 13}},
                                   {'beta': None, 'alpha': {'_market': 13}}, {'beta': -0.8, 'alpha': 'bad'}])
def test_predict_bad_curve(curve):
    assert cv.predict(curve, None, 1000) is None


def test_predict_survives_broken_cov():
    for cov in (None, 'x', [[1]], [[None, None], [None, None]], [[float('nan')] * 3] * 3):
        c = _curve()
        c['cov'] = cov
        p = cv.predict(c, None, 1000)
        assert p is not None and p['lo'] < p['v'] < p['hi']
        json_safe(p)


def test_predict_huge_values_return_none():
    assert cv.predict(_curve(alpha=800.0), None, 10) is None


def test_predict_accepts_numeric_node_keys():
    c = _curve()
    c['alpha']['1055398'] = 14.0
    assert cv.predict(c, 1055398, 100)['v'] == pytest.approx(cv.predict(c, '1055398', 100)['v'])
    assert cv.predict(c, 1055398.0, 100)['v'] == pytest.approx(cv.predict(c, '1055398', 100)['v'])


# ---- numerical internals ----

def test_log_interval_matches_direct_and_tails():
    from engine.stats import Phi
    zl = np.array([-1.0, -0.2, 0.3, -3.0])
    zu = np.array([1.0, 0.1, 2.0, -2.5])
    direct = np.log(Phi(zu) - Phi(zl))
    assert np.allclose(cv._log_interval(zl, zu), direct, atol=1e-6)
    far = cv._log_interval(np.array([30.0, -41.0]), np.array([31.0, -40.0]))
    # log P(30 < Z < 31) ~ log phi(30)/30 and log P(-41 < Z < -40) ~ log phi(40)/40
    assert far[0] == pytest.approx(-450 - math.log(30) - 0.5 * math.log(2 * math.pi), abs=0.01)
    assert far[1] == pytest.approx(-800 - math.log(40) - 0.5 * math.log(2 * math.pi), abs=0.01)
    assert np.all(np.isfinite(far))


def test_gradient_and_hessian_match_finite_differences():
    rng = np.random.default_rng(3)
    obs = []
    for i in range(240):
        x = float(rng.uniform(4, 12))
        lnS = 13.5 - 0.85 * x + rng.normal(0, 0.6)
        base = {'market': 'US', 'node': 'A' if i % 3 else 'B', 'ln_bsr': x, 'asin': f'a{i}',
                'age_days': float(rng.uniform(0, 80))}
        k = i % 4
        if k == 0:
            obs.append(dict(base, kind='badge', low=math.exp(lnS - 0.3), high=math.exp(lnS + 0.4)))
        elif k == 1:
            obs.append(dict(base, kind='badge_lb', low=math.exp(lnS - 0.3)))
        elif k == 2:
            obs.append(dict(base, kind='h10', value=math.exp(lnS + 0.2)))
            obs.append(dict(base, kind='badge', low=math.exp(lnS - 2), high=math.exp(lnS + 0.1)))
        else:
            obs.append(dict(base, kind='own', value=math.exp(lnS), node=None))
    rows = cv._rows(obs, 'US')
    pb = cv._Problem(rows, ['A', 'B'], True, (13.68, 1.0))
    assert pb.P == 6
    eye = np.eye(pb.P)
    eps = 1e-6
    for th in (np.array([-0.7, 13.0, math.log(0.5), 13.2, 12.9, 0.1]),
               np.array([-0.2, 20.0, math.log(0.06), 22.0, 5.0, 0.1])):     # far out in the tails
        f, g = cv._fg(pb, th)
        gn = np.array([(cv._fg(pb, th + eps * eye[j])[0] - cv._fg(pb, th - eps * eye[j])[0]) / (2 * eps)
                       for j in range(pb.P)])
        assert np.max(np.abs(g - gn) / np.maximum(1, np.abs(g))) < 1e-5
        H = cv._hess(pb, th)
        Hn = np.array([(cv._fg(pb, th + eps * eye[j])[1] - cv._fg(pb, th - eps * eye[j])[1]) / (2 * eps)
                       for j in range(pb.P)])
        assert np.max(np.abs(H - Hn) / np.maximum(1, np.abs(H))) < 1e-4
