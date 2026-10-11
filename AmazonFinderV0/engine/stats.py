"""Small numeric helpers shared by the analysis engine (numpy only, no scipy).

Everything here is vectorised: pass floats or numpy arrays.
"""
import math

import numpy as np

SQRT2 = math.sqrt(2.0)
LOG_SQRT_2PI = 0.5 * math.log(2 * math.pi)


def erfc(x):
    """Complementary error function, vectorised (W. J. Cody style rational approx, |error| < 1.2e-7)."""
    x = np.asarray(x, dtype=float)
    z = np.abs(x)
    t = 1.0 / (1.0 + 0.5 * z)
    r = t * np.exp(-z * z - 1.26551223 + t * (1.00002368 + t * (0.37409196 + t * (0.09678418 + t * (
        -0.18628806 + t * (0.27886807 + t * (-1.13520398 + t * (1.48851587 + t * (-0.82215223 + t * 0.17087277)))))))))
    return np.where(x >= 0, r, 2.0 - r)


def Phi(z):
    """Standard normal CDF."""
    return 0.5 * erfc(-np.asarray(z, dtype=float) / SQRT2)


def log_Phi(z):
    """log of the standard normal CDF, stable far into the lower tail."""
    z = np.asarray(z, dtype=float)
    out = np.empty_like(z)
    low = z < -8
    out[~low] = np.log(np.maximum(Phi(z[~low]), 1e-300))
    zl = z[low]
    z2 = zl * zl
    out[low] = -0.5 * z2 - np.log(-zl) - LOG_SQRT_2PI + np.log1p(-1 / z2 + 3 / (z2 * z2))
    return out


def phi(z):
    """Standard normal density."""
    z = np.asarray(z, dtype=float)
    return np.exp(-0.5 * z * z - LOG_SQRT_2PI)


def interval_prob(lo_z, hi_z):
    """P(lo_z < Z < hi_z) for a standard normal Z, accurate when both are in the upper tail.

    Use -inf / +inf for open ends.
    """
    lo_z = np.asarray(lo_z, dtype=float)
    hi_z = np.asarray(hi_z, dtype=float)
    upper = lo_z > 0                            # compute in the lower tail for precision
    p_direct = Phi(hi_z) - Phi(lo_z)
    p_flip = Phi(-lo_z) - Phi(-hi_z)
    return np.maximum(np.where(upper, p_flip, p_direct), 1e-300)


def clip(x, lo=0.0, hi=1.0):
    return np.clip(x, lo, hi) if isinstance(x, np.ndarray) else max(lo, min(hi, x))


def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x)) if not isinstance(x, np.ndarray) else 1.0 / (1.0 + np.exp(-x))


def robust_z(x, history):
    """(x - median) / (1.4826 * MAD) over `history`; None when history is too short or flat."""
    h = np.asarray([v for v in history if v is not None and v == v], dtype=float)
    if len(h) < 4:
        return None
    med = float(np.median(h))
    mad = float(np.median(np.abs(h - med))) * 1.4826
    if mad <= 1e-9:
        return None
    return (x - med) / mad


def ols_slope(y, x=None):
    """Least-squares slope of y on x (x defaults to 0..n-1). None if fewer than 3 points."""
    y = np.asarray(y, dtype=float)
    if x is None:
        x = np.arange(len(y), dtype=float)
    x = np.asarray(x, dtype=float)
    ok = ~(np.isnan(y) | np.isnan(x))
    if ok.sum() < 3:
        return None
    x, y = x[ok], y[ok]
    xm = x - x.mean()
    den = float((xm * xm).sum())
    return float((xm * (y - y.mean())).sum() / den) if den > 0 else None


def safe_div(a, b, default=None):
    try:
        return a / b if b else default
    except (TypeError, ZeroDivisionError):
        return default
