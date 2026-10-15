"""BSR to monthly units: fit ln(units) = alpha_node + beta_market * ln(BSR), one market at a time.

Amazon's "N+ bought in past month" badge says a listing sold somewhere in [N, next label) units in the
last 30 days. Paired with the listing's Best Sellers Rank, every badge is an interval observation of the
sales curve. Helium 10 rows and the seller's own sales are noisy point observations. The fit is a MAP
interval regression with priors, so a market with little data falls back on sensible defaults and says
so ('prior_only').

Listings under the smallest badge label never show a badge and are left out, so badge rows are fitted
given sales >= that floor; otherwise the curve would flatten where sales are small (AU and AE most of
all). Helium 10 and own-sales rows are normal on ln units with variance sigma^2 + 0.35^2 / 0.15^2.

Observations (any markets mixed; rows of other markets are skipped):
    {'market', 'node', 'ln_bsr', 'low', 'high' (None = open), 'kind': 'badge'|'badge_lb'|'h10'|'own',
     'value' (h10/own: units), 'age_days', 'asin'}
Pure functions, numpy only. Nothing here raises on missing data.
"""
import math

import numpy as np

from .badge import LADDER
from .stats import LOG_SQRT_2PI, log_Phi

THRESH = {
    'half_life_days': 40.0,         # recency weight w = 0.5 ** (age_days / 40)
    'beta_prior': (-0.85, 0.15),    # slope per market ~ N(-0.85, 0.15^2)
    'alpha_prior': {                # market intercept ~ N(mean, sd^2)
        'US': (13.68, 1.0),
        'AU': (13.68 - math.log(15.0), 1.5),
        'AE': (13.68 - math.log(25.0), 1.5),
    },
    'alpha_other_sd': 1.5,          # any other market: the US mean with a wider sd
    'alpha_node_sd': 1.5,           # node intercept ~ N(market intercept, 1.5^2)
    'sigma_scale': 0.8,             # sigma ~ half-normal(0.8)
    'delta_sd': 1.0,                # weak prior on the Helium 10 bias while it is free
    'h10_sd': 0.35,                 # Helium 10 noise around a listing's true sales (log scale)
    'own_sd': 0.15,                 # the seller's own sales: nearly exact
    'h10_fixed_weight': 0.5,        # Helium 10 rows count half while their bias is fixed at 0
    'delta_min_asins': 20,          # the bias is fitted once 20 ASINs have both a badge and an H10 row
    'node_min_obs': 40,             # nodes with fewer observations share the market intercept
    'calibrated_n': 30,             # 'calibrated' from 30 observations, else 'prior_only'
    'truncate': True,               # badges only show at or above the market floor: fit badges given S >= floor
    'badge_floor': 50,              # the floor, or the lowest badge label seen in the market if lower
    'cv_folds': 5,                  # diagnostics: 5-fold cross-validation grouped by ASIN
    'cv_min_groups': 5,
    'z80': 1.2815515655446004,      # 80% band: mid -/+ 1.28 sd on the log scale
    'sigma_nu': 0.5,                # BSR noise of a single snapshot (0.25 with BSR history)
    'sigma_min': 0.05,              # keeps the fit away from a zero or runaway spread
    'sigma_max': 5.0,
    'max_iter': 60,                 # damped Newton iterations (full fit)
    'cv_max_iter': 25,              # per fold, warm-started from the full fit
    'conf_high_ratio': 2.5,         # hi/lo below this -> 'high'
    'conf_medium_ratio': 5.0,       # hi/lo below this -> 'medium', otherwise 'low'
}

_INTERVAL, _LOWER, _NORMAL = 0, 1, 2   # likelihood types
_BADGE_KINDS = ('badge', 'badge_lb', 'open')
_FD_STEP = 1e-4                        # finite-difference step for the Hessian


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


def _market(m):
    return str(m).strip().upper() if m is not None and str(m).strip() else None


def _key(v):
    """A node or ASIN as a string key (1055398.0 -> '1055398'); None when blank."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, float):
        if not math.isfinite(v):
            return None
        if v.is_integer():
            v = int(v)
    s = str(v).strip()
    return s or None


def _alpha_prior(market):
    return THRESH['alpha_prior'].get(market, (THRESH['alpha_prior']['US'][0], THRESH['alpha_other_sd']))


# ---- observations ----

def _rows(obs, market):
    """The usable observations of one market as plain dicts. Bad rows are skipped."""
    if hasattr(obs, 'to_dict'):                 # a DataFrame works too
        try:
            obs = obs.to_dict('records')
        except Exception:
            return []
    if obs is None or isinstance(obs, (str, bytes, dict)) or not hasattr(obs, '__iter__'):
        return []
    half = THRESH['half_life_days']
    rows = []
    for i, o in enumerate(obs):
        if not isinstance(o, dict):
            continue
        om = _market(o.get('market'))
        if om is not None and om != market:     # rows without a market are taken as this market's
            continue
        x = _finite(o.get('ln_bsr'))
        if x is None:
            r = _finite(o.get('bsr'))
            x = math.log(r) if r is not None and r > 0 else None
        if x is None:
            continue
        kind = str(o.get('kind') or '').strip().lower()
        row = {'x': x, 'node': _key(o.get('node')), 'asin': _key(o.get('asin')) or f'#row{i}',
               'kind': kind, 'lnL': math.nan, 'lnU': math.nan, 'y': math.nan, 'tau2': 0.0}
        if kind in ('badge', 'badge_lb', 'open'):
            lo = _finite(o.get('low'))
            if lo is None or lo <= 0:
                continue
            row['lnL'] = math.log(lo)
            hi = _finite(o.get('high'))
            if kind == 'badge' and hi is not None:
                if hi <= lo * (1 + 1e-6):           # an empty or reversed bucket is bad data
                    continue
                row['t'] = _INTERVAL
                row['lnU'] = math.log(hi)
            else:
                # open top bucket, a child's badge for its parent, or no readable top: "L+" still means S >= L
                row['t'] = _LOWER
        elif kind in ('h10', 'own'):
            v = _finite(o.get('value'))
            if v is None or v <= 0:
                continue
            row['t'] = _NORMAL
            row['y'] = math.log(v)
            row['tau2'] = (THRESH['h10_sd'] if kind == 'h10' else THRESH['own_sd']) ** 2
        else:
            continue
        age = _finite(o.get('age_days'))
        row['w'] = 0.5 ** (max(0.0, age) / half) if age is not None else 1.0
        rows.append(row)
    return rows


class _Problem:
    """The observations packed into arrays (sorted by likelihood type) plus the parameter layout.

    Parameters: [beta, alpha_market, ln sigma, alpha_node_1..K, (delta_h when free)].
    With ln_floor, every badge row also gets a 'trunc' row: a lower bound at the floor with negative
    weight, which turns P(bucket) into P(bucket | S >= floor). Listings under the floor never show a
    badge and are left out, so without this the curve would flatten where sales are small."""

    def __init__(self, rows, nodes, delta_free, prior, ln_floor=None):
        if ln_floor is not None:
            rows = rows + [dict(r, kind='trunc', t=_LOWER, lnL=ln_floor, lnU=math.nan, w=-r['w'])
                           for r in rows if r['kind'] in _BADGE_KINDS]
        rows = sorted(rows, key=lambda r: r['t'])
        self.K = len(nodes)
        self.delta_free = bool(delta_free)
        self.P = 3 + self.K + (1 if self.delta_free else 0)
        self.prior = prior
        node_ix = {nd: k + 1 for k, nd in enumerate(nodes)}
        hw = 1.0 if self.delta_free else THRESH['h10_fixed_weight']
        self.n = len(rows)
        self.x = np.array([r['x'] for r in rows], dtype=float)
        self.w = np.array([r['w'] * (hw if r['kind'] == 'h10' else 1.0) for r in rows], dtype=float)
        self.gi = np.array([node_ix.get(r['node'], 0) for r in rows], dtype=np.intp)
        self.e = np.array([1.0 if (r['kind'] == 'h10' and self.delta_free) else 0.0 for r in rows])
        self.real = np.array([r['kind'] != 'trunc' for r in rows], dtype=bool)
        self.lnL = np.array([r['lnL'] for r in rows], dtype=float)
        self.lnU = np.array([r['lnU'] for r in rows], dtype=float)
        self.y = np.array([r['y'] for r in rows], dtype=float)
        self.tau2 = np.array([r['tau2'] for r in rows], dtype=float)
        t = [r['t'] for r in rows]
        n_i, n_b = t.count(_INTERVAL), t.count(_LOWER)
        self.sI = slice(0, n_i)
        self.sB = slice(n_i, n_i + n_b)
        self.sN = slice(n_i + n_b, self.n)
        self.aidx = np.array([1] + list(range(3, 3 + self.K)), dtype=np.intp)   # alpha of group g


def _log1mexp(d):
    """log(1 - exp(d)) for d < 0."""
    return np.where(d > -0.6931471805599453, np.log(-np.expm1(d)), np.log1p(-np.exp(d)))


def _log_interval(zl, zu):
    """log(Phi(zu) - Phi(zl)) for zl < zu, vectorised, stable deep in either tail."""
    flip = zl > 0                               # upper tail: mirror into the lower tail
    a = np.where(flip, -zu, zl)
    b = np.where(flip, -zl, zu)
    lb = log_Phi(b)
    d = np.minimum(log_Phi(a) - lb, -1e-12)
    return lb + _log1mexp(d)


def _mean(pb, th):
    alphas = np.concatenate(([th[1]], th[3:3 + pb.K]))
    m = alphas[pb.gi] + th[0] * pb.x
    if pb.delta_free:
        m = m + th[-1] * pb.e
    return m


def _obs_terms(pb, m, s):
    """Per-observation log-likelihood and its derivatives in the mean m and in s = ln sigma."""
    sig = math.exp(s)
    ll = np.empty(pb.n)
    gm = np.empty(pb.n)
    gs = np.empty(pb.n)
    i = pb.sI
    if i.stop > i.start:                        # badge bucket [L, U)
        zl = (pb.lnL[i] - m[i]) / sig
        zu = (pb.lnU[i] - m[i]) / sig
        lp = _log_interval(zl, zu)
        rl = np.exp(-0.5 * zl * zl - LOG_SQRT_2PI - lp)
        ru = np.exp(-0.5 * zu * zu - LOG_SQRT_2PI - lp)
        ll[i] = lp
        gm[i] = (rl - ru) / sig
        gs[i] = zl * rl - zu * ru
    b = pb.sB
    if b.stop > b.start:                        # open bucket / lower bound: S >= L
        zl = (pb.lnL[b] - m[b]) / sig
        lp = log_Phi(-zl)
        h = np.exp(-0.5 * zl * zl - LOG_SQRT_2PI - lp)
        ll[b] = lp
        gm[b] = h / sig
        gs[b] = h * zl
    k = pb.sN
    if k.stop > k.start:                        # H10 / own sales: normal on ln units
        r = pb.y[k] - m[k]
        v = sig * sig + pb.tau2[k]
        ll[k] = -0.5 * np.log(2 * math.pi * v) - r * r / (2 * v)
        gm[k] = r / v
        gs[k] = sig * sig * (r * r / (v * v) - 1.0 / v)
    return ll, gm, gs


def _fg(pb, th):
    """Negative log posterior and its analytic gradient."""
    K = pb.K
    with np.errstate(all='ignore'):
        ll, gm, gs = _obs_terms(pb, _mean(pb, th), th[2])
        f = -float(np.dot(pb.w, ll))
        wg = pb.w * gm
        g = np.zeros(pb.P)
        g[0] = -float(np.dot(wg, pb.x))
        ga = np.bincount(pb.gi, weights=wg, minlength=K + 1)
        g[1] = -ga[0]
        g[3:3 + K] = -ga[1:]
        g[2] = -float(np.dot(pb.w, gs))
        if pb.delta_free:
            g[-1] = -float(np.dot(wg, pb.e))
    # priors
    b0, sb = THRESH['beta_prior']
    m0, sm = pb.prior
    sn, s0 = THRESH['alpha_node_sd'], THRESH['sigma_scale']
    beta, am, s = th[0], th[1], th[2]
    a = th[3:3 + K]
    sig2 = math.exp(2 * s)
    f += (beta - b0) ** 2 / (2 * sb * sb) + (am - m0) ** 2 / (2 * sm * sm)
    f += float(np.sum((a - am) ** 2)) / (2 * sn * sn)
    f += sig2 / (2 * s0 * s0) - s               # half-normal on sigma, taken on the log scale
    g[0] += (beta - b0) / (sb * sb)
    g[1] += (am - m0) / (sm * sm) - float(np.sum(a - am)) / (sn * sn)
    g[3:3 + K] += (a - am) / (sn * sn)
    g[2] += sig2 / (s0 * s0) - 1.0
    if pb.delta_free:
        sd = THRESH['delta_sd']
        f += th[-1] ** 2 / (2 * sd * sd)
        g[-1] += th[-1] / (sd * sd)
    return f, g


def _hess(pb, th):
    """Hessian of the negative log posterior: per-observation finite differences of the analytic
    gradient in (mean, ln sigma), assembled over the parameters."""
    K, P, h = pb.K, pb.P, _FD_STEP
    H = np.zeros((P, P))
    if pb.n:
        with np.errstate(all='ignore'):
            m, s = _mean(pb, th), th[2]
            _, gm_p, gs_p = _obs_terms(pb, m + h, s)
            _, gm_m, gs_m = _obs_terms(pb, m - h, s)
            _, gm_sp, gs_sp = _obs_terms(pb, m, s + h)
            _, gm_sm, gs_sm = _obs_terms(pb, m, s - h)
            hmm = (gm_p - gm_m) / (2 * h)
            hms = ((gm_sp - gm_sm) + (gs_p - gs_m)) / (4 * h)
            hss = (gs_sp - gs_sm) / (2 * h)
            w, x, gi, ai = pb.w, pb.x, pb.gi, pb.aidx
            wmm, wms = w * hmm, w * hms
            H[0, 0] = np.dot(wmm, x * x)
            H[0, 2] = H[2, 0] = np.dot(wms, x)
            H[2, 2] = np.dot(w, hss)
            H[ai, ai] = np.bincount(gi, weights=wmm, minlength=K + 1)
            H[0, ai] = H[ai, 0] = np.bincount(gi, weights=wmm * x, minlength=K + 1)
            H[2, ai] = H[ai, 2] = np.bincount(gi, weights=wms, minlength=K + 1)
            if pb.delta_free:
                d, e = P - 1, pb.e
                H[d, d] = np.dot(wmm, e)
                H[d, 0] = H[0, d] = np.dot(wmm * e, x)
                H[d, 2] = H[2, d] = np.dot(wms, e)
                H[d, ai] = H[ai, d] = np.bincount(gi, weights=wmm * e, minlength=K + 1)
        H = -H
    sb, sm = THRESH['beta_prior'][1], pb.prior[1]
    sn, s0 = THRESH['alpha_node_sd'], THRESH['sigma_scale']
    H[0, 0] += 1 / (sb * sb)
    H[1, 1] += 1 / (sm * sm) + K / (sn * sn)
    for k in range(3, 3 + K):
        H[k, k] += 1 / (sn * sn)
        H[1, k] -= 1 / (sn * sn)
        H[k, 1] -= 1 / (sn * sn)
    H[2, 2] += 2 * math.exp(2 * th[2]) / (s0 * s0)
    if pb.delta_free:
        H[P - 1, P - 1] += 1 / THRESH['delta_sd'] ** 2
    return H


def _solve_damped(H, g):
    """Newton step from H d = -g, adding lambda*I until H is positive definite. None if hopeless."""
    if not (np.all(np.isfinite(H)) and np.all(np.isfinite(g))):
        return None
    eye = np.eye(len(g))
    scale = max(1e-8, float(np.max(np.abs(np.diag(H)))))
    lam = 0.0
    for _ in range(25):
        A = H + lam * eye
        try:
            np.linalg.cholesky(A)
            return np.linalg.solve(A, -g)
        except np.linalg.LinAlgError:
            lam = 1e-6 * scale if lam == 0 else lam * 10
    return None


def _clip_s(th):
    th[2] = min(max(th[2], math.log(THRESH['sigma_min'])), math.log(THRESH['sigma_max']))
    return th


def _newton(pb, th, max_iter):
    """Damped Newton with a backtracking line search. Returns (theta, converged)."""
    th = _clip_s(np.array(th, dtype=float))
    f, g = _fg(pb, th)
    if not math.isfinite(f):
        return th, False
    for _ in range(max_iter):
        d = _solve_damped(_hess(pb, th), g)
        if d is None:
            return th, False
        gd = float(np.dot(g, d))
        if -gd < 1e-10:                         # Newton decrement: nothing left to gain
            return th, True
        big = float(np.max(np.abs(d)))
        if big > 2.0:                           # never jump more than 2 units in one step
            d = d * (2.0 / big)
            gd = float(np.dot(g, d))
        t = 1.0
        while t >= 1e-6:
            tn = _clip_s(th + t * d)
            fn, gn = _fg(pb, tn)
            if math.isfinite(fn) and fn <= f + 1e-4 * t * gd:
                break
            t *= 0.5
        else:
            return th, abs(gd) < 1e-6
        th, f, g = tn, fn, gn
    return th, False


def _start(pb):
    """Starting values: a quick least-squares line through bucket middles, kept near the priors."""
    th = np.zeros(pb.P)
    b0 = THRESH['beta_prior'][0]
    th[0], th[1], th[2] = b0, pb.prior[0], math.log(THRESH['sigma_scale'])
    th[3:3 + pb.K] = pb.prior[0]
    real = pb.real
    if int(real.sum()) >= 3 and float(np.ptp(pb.x[real])) > 0.5:
        y = pb.y.copy()
        y[pb.sI] = 0.5 * (pb.lnL[pb.sI] + pb.lnU[pb.sI])
        y[pb.sB] = pb.lnL[pb.sB] + 0.4
        x, y, gi = pb.x[real], y[real], pb.gi[real]
        xm = x - x.mean()
        beta = float(np.dot(xm, y - y.mean()) / np.dot(xm, xm))
        beta = min(max(beta, b0 - 0.6), b0 + 0.6)
        res = y - beta * x
        th[0] = beta
        th[1] = float(np.mean(res[gi == 0])) if np.any(gi == 0) else float(np.mean(res))
        th[2] = math.log(min(max(float(np.std(res - np.mean(res))), 0.3), 1.5))
        for k in range(pb.K):
            sel = gi == k + 1
            th[3 + k] = float(np.mean(res[sel])) if np.any(sel) else th[1]
    return th


def _fit(rows, nodes, delta_free, prior, ln_floor=None, th0=None, max_iter=None):
    """MAP fit. Returns (problem, theta, covariance over theta, converged)."""
    pb = _Problem(rows, nodes, delta_free, prior, ln_floor)
    start = _start(pb) if th0 is None else np.array(th0, dtype=float)
    th, ok = _newton(pb, start, max_iter or THRESH['max_iter'])
    if not np.all(np.isfinite(th)):
        th, ok = _start(_Problem([], nodes, delta_free, prior)), False
    H = _hess(pb, th)
    C = None
    if np.all(np.isfinite(H)):
        try:
            np.linalg.cholesky(H)
            C = np.linalg.inv(H)
        except np.linalg.LinAlgError:
            C = np.linalg.pinv(H + 1e-6 * max(1.0, float(np.max(np.abs(np.diag(H))))) * np.eye(pb.P))
        C = 0.5 * (C + C.T)
    if C is None or not np.all(np.isfinite(C)):
        C = np.zeros((pb.P, pb.P))
    return pb, th, C, ok


# ---- diagnostics ----

def _var_mu(C, aidx, x, gi):
    """Variance of mu = alpha_g + beta*x from the parameter covariance."""
    ai = aidx[gi]
    return np.maximum(C[0, 0] * x * x + 2 * x * C[0, ai] + C[ai, ai], 0.0)


def _cv_diag(rows, nodes, delta_free, prior, ln_floor, th_full, market):
    """Hit rate, within-one-bucket rate and 80% interval coverage on held-out badge observations,
    5-fold cross-validation grouped by ASIN."""
    empty = {'hit': None, 'within1': None, 'cov80': None, 'n': 0}
    groups = sorted({r['asin'] for r in rows})
    k = THRESH['cv_folds']
    if len(groups) < max(k, THRESH['cv_min_groups']):
        return empty
    order = np.random.default_rng(0).permutation(len(groups))
    fold = {groups[j]: int(pos % k) for pos, j in enumerate(order)}
    ladder = np.array(LADDER.get(market) or LADDER['US'], dtype=float)
    if ln_floor is not None and np.any(ladder >= math.exp(ln_floor) * (1 - 1e-9)):
        ladder = ladder[ladder >= math.exp(ln_floor) * (1 - 1e-9)]     # no badge below the floor
    edges = np.log(ladder)
    z80 = THRESH['z80']
    hits, near, cover = [], [], []
    for f in range(k):
        test = [r for r in rows if fold[r['asin']] == f and r['kind'] == 'badge']
        if not test:
            continue
        train = [r for r in rows if fold[r['asin']] != f]
        pb, th, C, _ = _fit(train, nodes, delta_free, prior, ln_floor, th0=th_full,
                               max_iter=THRESH['cv_max_iter'])
        node_ix = {nd: j + 1 for j, nd in enumerate(nodes)}
        x = np.array([r['x'] for r in test])
        gi = np.array([node_ix.get(r['node'], 0) for r in test], dtype=np.intp)
        lnL = np.array([r['lnL'] for r in test])
        lnU = np.array([r['lnU'] if r['t'] == _INTERVAL else math.inf for r in test])
        alphas = np.concatenate(([th[1]], th[3:3 + pb.K]))
        mu = alphas[gi] + th[0] * x
        sd = np.sqrt(math.exp(2 * th[2]) + _var_mu(C, pb.aidx, x, gi))
        with np.errstate(all='ignore'):
            # most likely badge bucket (given there is a badge) vs the one Amazon showed
            zlo = (edges[None, :] - mu[:, None]) / sd[:, None]
            lp = np.empty_like(zlo)
            lp[:, :-1] = _log_interval(zlo[:, :-1], zlo[:, 1:])
            lp[:, -1] = log_Phi(-zlo[:, -1])
            pred = np.argmax(lp, axis=1)
            actual = np.clip(np.searchsorted(ladder, np.exp(lnL) * (1 + 1e-9), side='right') - 1,
                             0, len(ladder) - 1)
            hits.extend((pred == actual).tolist())
            near.extend((np.abs(pred - actual) <= 1).tolist())
            # P(inside the 80% band | inside the bucket), under the predictive distribution
            zL = (lnL - mu) / sd
            zU = np.where(np.isfinite(lnU), (lnU - mu) / sd, 40.0)
            zU = np.maximum(zU, zL + 1e-9)
            a = np.maximum(zL, -z80)
            b = np.minimum(zU, z80)
            inside = b > a
            ratio = np.zeros(len(test))
            if np.any(inside):
                ratio[inside] = np.exp(_log_interval(a[inside], b[inside])
                                       - _log_interval(zL[inside], zU[inside]))
            cover.extend(np.clip(np.nan_to_num(ratio), 0.0, 1.0).tolist())
    if not hits:
        return empty
    return {'hit': round(float(np.mean(hits)), 3), 'within1': round(float(np.mean(near)), 3),
            'cov80': round(float(np.mean(cover)), 3), 'n': len(hits)}


# ---- public ----

def fit_market(obs, market):
    """Fit the BSR -> monthly units curve of one market from badge, Helium 10 and own-sales rows.

    Returns {'market', 'beta', 'alpha': {node: a, '_market': a_m}, 'sigma', 'delta_h', 'n_obs',
    'status': 'calibrated' | 'prior_only', 'cov' (3x3 over beta / alpha_m / sigma), 'diag': {'hit',
    'within1', 'cov80', 'n'}} plus 'cov_node' {node: [var(alpha_node), cov(alpha_node, beta)]},
    'nodes' {node: n_obs}, 'n_by_kind', 'delta_free', 'floor' (the badge floor the fit conditions on)
    and 'converged'. Nodes with fewer than 40 observations share alpha_m; predict() falls back to
    alpha_m for any node not in 'alpha'. Under 30 observations the fit still runs, but the priors
    dominate and the status is 'prior_only'."""
    market = _market(market) or 'US'
    prior = _alpha_prior(market)
    rows = _rows(obs, market)
    n = len(rows)
    per_node = {}
    for r in rows:
        if r['node'] is not None:
            per_node[r['node']] = per_node.get(r['node'], 0) + 1
    nodes = sorted(nd for nd, c in per_node.items() if c >= THRESH['node_min_obs'] and nd != '_market')
    with_h10 = {r['asin'] for r in rows if r['kind'] == 'h10'}
    with_badge = {r['asin'] for r in rows if r['kind'] in _BADGE_KINDS}
    delta_free = len(with_h10 & with_badge) >= THRESH['delta_min_asins']
    lows = [r['lnL'] for r in rows if r['kind'] in _BADGE_KINDS]
    ln_floor = min(min(lows), math.log(THRESH['badge_floor'])) if lows and THRESH['truncate'] else None

    pb, th, C, ok = _fit(rows, nodes, delta_free, prior, ln_floor)
    sig = math.exp(th[2])
    J = np.diag([1.0, 1.0, sig])                # ln sigma -> sigma
    cov3 = J @ C[:3, :3] @ J
    alpha = {nd: float(th[3 + k]) for k, nd in enumerate(nodes)}
    alpha['_market'] = float(th[1])
    calibrated = n >= THRESH['calibrated_n']
    diag = (_cv_diag(rows, nodes, delta_free, prior, ln_floor, th, market) if calibrated
            else {'hit': None, 'within1': None, 'cov80': None, 'n': 0})
    kinds = {}
    for r in rows:
        kk = 'badge_lb' if r['kind'] == 'open' else r['kind']
        kinds[kk] = kinds.get(kk, 0) + 1
    return {
        'market': market,
        'beta': float(th[0]),
        'alpha': alpha,
        'sigma': float(sig),
        'delta_h': float(th[-1]) if delta_free else 0.0,
        'n_obs': n,
        'status': 'calibrated' if calibrated else 'prior_only',
        'cov': [[float(v) for v in row] for row in cov3],
        'cov_node': {nd: [float(C[3 + k, 3 + k]), float(C[3 + k, 0])] for k, nd in enumerate(nodes)},
        'diag': diag,
        'nodes': {nd: per_node[nd] for nd in nodes},
        'n_by_kind': kinds,
        'delta_free': delta_free,
        'floor': round(math.exp(ln_floor), 6) if ln_floor is not None else None,
        'converged': bool(ok),
    }


def _conf(ratio):
    if ratio < THRESH['conf_high_ratio']:
        return 'high'
    return 'medium' if ratio < THRESH['conf_medium_ratio'] else 'low'


def predict(curve, node, bsr, sigma_nu=None, scatter=True):
    """Monthly units at a BSR, as an est dict (basis 'estimated').

    mid = exp(mu + sigma_nu^2/2), with sigma_nu the BSR noise of one snapshot (0.5 by default, pass
    0.25 for a BSR averaged over its history). The 80% band comes from the delta method on mu (the
    curve's own uncertainty) plus, with scatter=True, the spread of listings around the curve (sigma).
    Pass scatter=False for the curve-only band. None for a missing curve or a bad BSR."""
    if not isinstance(curve, dict):
        return None
    r = _finite(bsr)
    beta = _finite(curve.get('beta'))
    alpha = curve.get('alpha') if isinstance(curve.get('alpha'), dict) else {}
    if r is None or r <= 0 or beta is None:
        return None
    key = _key(node)
    a = _finite(alpha.get(key)) if key is not None and key != '_market' else None
    use_node = a is not None
    if a is None:
        a = _finite(alpha.get('_market'))
    if a is None:
        return None
    x = math.log(r)
    mu = a + beta * x
    sn = THRESH['sigma_nu'] if sigma_nu is None else (_finite(sigma_nu) or 0.0)
    # delta method: var(mu) = x^2 var(beta) + var(alpha) + 2 x cov(alpha, beta)
    var_mu = 0.0
    cov = curve.get('cov')
    try:
        vbb = _finite(cov[0][0]) or 0.0
        if use_node:
            cn = (curve.get('cov_node') or {}).get(key) or [0.0, 0.0]
            vaa, vab = _finite(cn[0]) or 0.0, _finite(cn[1]) or 0.0
        else:
            vaa, vab = _finite(cov[1][1]) or 0.0, _finite(cov[0][1]) or 0.0
        var_mu = max(0.0, x * x * vbb + vaa + 2 * x * vab)
    except (TypeError, IndexError, KeyError, AttributeError):
        var_mu = 0.0
    sig = _finite(curve.get('sigma')) or 0.0
    sd = math.sqrt(var_mu + (sig * sig if scatter else 0.0))
    z = THRESH['z80']
    log_mid = mu + 0.5 * sn * sn
    if not math.isfinite(log_mid) or log_mid + z * sd > 700:
        return None
    mid = math.exp(log_mid)
    lo, hi = math.exp(log_mid - z * sd), math.exp(log_mid + z * sd)
    return est(mid, lo, hi, 'estimated', _conf(hi / lo if lo > 0 else math.inf))
