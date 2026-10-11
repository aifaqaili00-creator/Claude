"""Niche scores: saturation, opportunity and arbitrage (synth.md 8.7, as changed by the plan).

- saturation: how crowded a niche is, 0-100 (higher = harder to get into).
- opportunity: a weighted geometric mean of demand, momentum, competition, local gap and margin, so one
  very weak part pulls the whole score down. Each risk flag takes 15% off.
- arbitrage: a US boom that the target market (AU / AE) has not caught up with yet. No lead-lag model:
  the timing part only looks at the target's own trend label.
- confidence: the lowest of the component confidences.

A part with no data counts as neutral (0.5) and lowers the confidence; it never raises an error.
Every score comes with its parts and a short "why" line per part.
Pure functions, numpy only, JSON-safe output (no NaN).
"""
import math

import numpy as np

import file_rank as fr

from .stats import clip

# Tunable thresholds.
THRESH = {
    'neutral': 0.5,                # a part with no data counts as this
    'floor': 0.03,                 # geometric mean floor per part, so one zero does not give exactly 0
    # saturation
    'sat_w': {'rb': 0.25, 'conc': 0.20, 'ads': 0.15, 'crowd': 0.20, 'entry': 0.20},
    'rb_top': 10.0,                # rb = 1 when the top-10 median reviews reach 10x the target's maximum
    'hhi_lo': 0.05,                # conc = clip((HHI - 0.05) / 0.30)
    'hhi_span': 0.30,
    'crowd_n': 30,                 # crowd = 1 with 30 or more listings selling
    'hhi_top_n': 20,               # HHI over the top 20 unit shares
    'selling_min': 30,             # a listing is 'selling' with units >= max(30, target sales / 3)
    # opportunity
    'opp_w': {'D': 0.25, 'M': 0.15, 'C': 0.20, 'G': 0.20, 'P': 0.20},
    'opp_w_us_G': 0.05,            # for a US target the local-gap weight drops to .05 (then renormalised)
    'demand_div': 3.0,             # D = 0 at target sales / 3 ...
    'demand_span': 12.0,           # ... and 1 at 12x that (4x the target)
    'gap_fast_ref': 12.0,          # G = 0.5 OSD + 0.5 clip((12 - fast_local) / 10)
    'gap_fast_span': 10.0,
    'margin_lo': 0.10,             # P = clip((margin - 0.10) / 0.25)
    'margin_span': 0.25,
    'left_pct_weight': 0.5,        # Xray 'left after fees' instead of a real margin: half weight
    'risk_step': 0.15,             # each risk flag takes 15% off
    'labels': ((70, 'Strong'), (50, 'Worth a look'), (30, 'Weak')),
    'label_else': 'Skip',
    'label_none': 'Unknown',       # no part of the score has data
    # arbitrage
    'arb_w': {'B': 0.30, 'Rd': 0.15, 'G': 0.25, 'P': 0.20, 'W': 0.10},
    'rd_w': {'autocomplete': 0.4, 'trends': 0.3, 'badges': 0.3},
    'rd_floor': 0.2,
    'rd_trends_months': 12,
    'arb_fast_span': 10.0,         # G_t = 0.5 OSD + 0.5 clip(1 - fast_local / 10)
    'w_open': 1.0,                 # target's trend is not rising yet
    'w_rising': 0.6,               # target's trend label is Emerging / Growing / Breakout
    'w_crowded': 0.3,              # Growing and 10+ fast local sellers already
    'w_crowded_fast': 10,
    'rising_labels': ('Emerging', 'Growing', 'Breakout'),
    # niche momentum (synth 8.6): 0.6 TS + 0.4 CS when the Trends quality is good enough, else CS
    'mom_ts_w': 0.6,
    'mom_q_min': 0.5,
    # niche quality Q (synth 8.7)
    'q_w': {'coverage': 0.35, 'cv_within1': 0.25, 'q_T': 0.20, 'density': 0.20},
    'q_high': 0.7,
    'q_medium': 0.4,
    'conf_high_ratio': 2.5,        # est dicts: hi/lo below this -> 'high'
    'conf_medium_ratio': 5.0,      # ... below this -> 'medium', otherwise 'low'
}

_CONF_ORDER = ['low', 'medium', 'high']
_NAMES = {'D': 'Demand', 'M': 'Momentum', 'C': 'Competition', 'G': 'Local gap', 'P': 'Margin',
          'B': 'US boom', 'Rd': 'Readiness', 'W': 'Timing',
          'rb': 'Review barrier', 'conc': 'Concentration', 'ads': 'Ads', 'crowd': 'Crowd', 'entry': 'Entry'}


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


def _val(x):
    """A number from a plain value or an est dict {'v', ...}."""
    return _finite(x.get('v')) if isinstance(x, dict) else _finite(x)


def _share(x):
    """A share as 0..1; 1-100 is read as a percentage."""
    v = _val(x)
    if v is None:
        return None
    return v / 100.0 if 1.0 < v <= 100.0 else v


def _r(x, nd=3):
    f = _finite(x)
    return None if f is None else round(f, nd)


def _get(m, *keys):
    """First key present (not None) in dict m."""
    if not isinstance(m, dict):
        return None
    for k in keys:
        if m.get(k) is not None:
            return m.get(k)
    return None


def _market(m):
    """'US' / 'AU' / 'AE' from m['market'], else from the target's currency; None when unknown."""
    if not isinstance(m, dict):
        return None
    mk = str(m.get('market') or '').strip().upper()
    if mk == 'UAE':
        mk = 'AE'
    if mk in fr.TARGETS:
        return mk
    t = m.get('target')
    if isinstance(t, dict):
        for code, tt in fr.TARGETS.items():
            if t.get('currency') and t.get('currency') == tt['currency']:
                return code
    return None


def _target(m):
    """The target dict {'currency', 'price', 'sales', 'reviews'}; the market's TARGETS entry, US by default."""
    t = m.get('target') if isinstance(m, dict) else None
    base = fr.TARGETS.get(_market(m) or 'US', fr.TARGETS['US'])
    if not isinstance(t, dict):
        return dict(base)
    out = dict(base)
    for k in ('currency', 'price', 'sales', 'reviews'):
        if t.get(k) is not None:
            out[k] = t[k]
    return out


def _conf_word(c):
    """'high' / 'medium' / 'low' from a word, an est dict (its conf, or its band) or a 0..1 quality."""
    if c is None or isinstance(c, bool):
        return None
    if isinstance(c, str):
        c = c.strip().lower()
        return c if c in _CONF_ORDER else None
    if isinstance(c, dict):
        if isinstance(c.get('conf'), str):
            return _conf_word(c.get('conf'))
        lo, hi = _finite(c.get('lo')), _finite(c.get('hi'))
        if lo is not None and hi is not None and lo > 0:
            r = hi / lo
            return 'high' if r < THRESH['conf_high_ratio'] else 'medium' if r < THRESH['conf_medium_ratio'] else 'low'
        return None
    q = _finite(c)
    if q is None:
        return None
    return 'high' if q >= THRESH['q_high'] else 'medium' if q >= THRESH['q_medium'] else 'low'


def _min_conf(*cs):
    words = [w for w in (_conf_word(c) for c in cs) if w is not None]
    return min(words, key=_CONF_ORDER.index) if words else 'low'


def _input_conf(m, key, known):
    """Confidence of one input: m['conf'][key] or m[key + '_conf'], else the est dict's own, else
    'high' when the value is there and 'low' when it is missing."""
    if not known:
        return 'low'
    given = None
    if isinstance(m.get('conf'), dict):
        given = _conf_word(m['conf'].get(key))
    given = given or _conf_word(m.get(key + '_conf'))
    if given is None and isinstance(m.get(key), dict):
        given = _conf_word(m.get(key))
    return given or 'high'


def _pct(x):
    return '%d%%' % round(100 * x)


def _n(x):
    """1234.5 -> '1,235'."""
    return '{:,}'.format(int(round(x)))


def _part(key, v, w, why, conf, known=True, **extra):
    """One score part; '_v' / '_w' keep full precision for the maths and are dropped by _strip."""
    d = {'key': key, 'name': _NAMES.get(key, key), 'v': _r(v), 'w': _r(w), 'why': why, 'conf': conf,
         'known': bool(known), '_v': float(v), '_w': float(w)}
    d.update(extra)
    return d


def _normalise(weights):
    s = sum(weights.values())
    return {k: (w / s if s > 0 else 0.0) for k, w in weights.items()}


def _geo(parts):
    """100 * prod(max(v, floor)^w) over the parts (weights already sum to 1)."""
    fl = THRESH['floor']
    logs = [p['_w'] * math.log(max(fl, min(1.0, p['_v']))) for p in parts]
    return 100.0 * math.exp(sum(logs))


def _strip(parts):
    """Drop the full-precision working keys before returning."""
    return [{k: v for k, v in p.items() if not k.startswith('_')} for p in parts]


def _n_risk(flags):
    """Number of distinct risk flags from a list of names or a count."""
    if flags is None or isinstance(flags, bool):
        return 0, []
    if isinstance(flags, str):
        flags = [flags]
    if isinstance(flags, (list, tuple, set)):
        names = []
        for f in flags:
            s = str(f).strip() if f is not None else ''
            if s and s not in names:
                names.append(s)
        return len(names), names
    n = _finite(flags)
    return (max(0, int(n)) if n is not None else 0), []


def label_for(score):
    """Strong >= 70, Worth a look >= 50, Weak >= 30, otherwise Skip ('Unknown' without a score)."""
    s = _finite(score)
    if s is None:
        return THRESH['label_none']
    for cut, name in THRESH['labels']:
        if s >= cut:
            return name
    return THRESH['label_else']


def base_label(text):
    """'Growing, seasonal peak Dec' -> 'Growing'."""
    return str(text or '').split(',')[0].strip()


# ---------- confidence ----------

def confidence(*items):
    """The lowest of the component confidences: 'high' | 'medium' | 'low'.

    Each item may be a word ('high' / 'medium' / 'low'), an est dict (its 'conf', or else its band:
    hi/lo < 2.5 high, < 5 medium), a 0..1 quality number (>= 0.7 high, >= 0.4 medium), a score result
    with 'conf' or 'parts', or a list / tuple / dict of these. None is skipped. Nothing usable gives 'low'.
    """
    words = []

    def walk(x, depth=0):
        if x is None or depth > 4:
            return
        if isinstance(x, dict):
            if 'conf' in x and not isinstance(x.get('conf'), dict):
                w = _conf_word(x.get('conf'))
                if w:
                    words.append(w)
                return
            if 'parts' in x and isinstance(x.get('parts'), (list, tuple)):
                for p in x['parts']:
                    walk(p, depth + 1)
                return
            if 'lo' in x and 'hi' in x:
                w = _conf_word(x)
                if w:
                    words.append(w)
                return
            for v in x.values():
                walk(v, depth + 1)
            return
        if isinstance(x, (list, tuple, set, np.ndarray)):
            for v in x:
                walk(v, depth + 1)
            return
        w = _conf_word(x)
        if w:
            words.append(w)

    for it in items:
        walk(it)
    return min(words, key=_CONF_ORDER.index) if words else 'low'


def niche_quality(coverage=None, cv_within1=None, q_T=None, density=None):
    """Data quality of a niche, Q = .35 snapshot coverage (30 d) + .25 curve within-1-bucket rate
    + .20 Trends quality + .20 badge / import density, each 0..1. Missing parts count as 0.
    Returns {'q', 'conf'} (Q >= 0.7 high, >= 0.4 medium, otherwise low)."""
    w = THRESH['q_w']
    vals = {'coverage': coverage, 'cv_within1': cv_within1, 'q_T': q_T, 'density': density}
    q = 0.0
    for k, x in vals.items():
        v = _share(x)
        q += w[k] * (clip(v) if v is not None else 0.0)
    return {'q': round(q, 3), 'conf': _conf_word(q)}


def niche_momentum(trend_score=None, cluster_surge=None, q_T=None):
    """Niche momentum 0..1 (synth 8.6): 0.6 TS + 0.4 CS when q_T >= 0.5, else CS.

    TS and CS are 0..100. Uses whichever is available; None when neither is."""
    ts, cs, q = _val(trend_score), _val(cluster_surge), _val(q_T)
    ts = None if ts is None else clip(ts, 0.0, 100.0)
    cs = None if cs is None else clip(cs, 0.0, 100.0)
    good = q is not None and q >= THRESH['mom_q_min']
    if ts is not None and cs is not None:
        m = THRESH['mom_ts_w'] * ts + (1 - THRESH['mom_ts_w']) * cs if good else cs
    elif cs is not None:
        m = cs
    elif ts is not None and good:
        m = ts
    else:
        return None
    return round(m / 100.0, 4)


# ---------- derived metrics from page-1 products ----------

def niche_metrics(products, target=None, market=None):
    """Niche metrics from page-1 rows, in page order.

    products: [{'units' (number or est dict), 'reviews', 'origin': 'local'|'overseas'|'unknown',
    'speed': 'fast'|'slow'|..., 'sponsored': bool, 'age_months' (optional)}].
    Returns the inputs saturation() / opportunity() expect: units_top10_median, review_barrier, hhi,
    top3_share, sponsored_share, n_selling, osd_share, fast_local, plus dps (units per selling listing)
    and nesr (share of listings under 12 months old in the top that are selling). None where unknown.
    """
    rows = [p for p in (products if isinstance(products, (list, tuple)) else []) if isinstance(p, dict)]
    T = _target({'target': target, 'market': market})
    out = {'units_top10_median': None, 'review_barrier': None, 'hhi': None, 'top3_share': None,
           'sponsored_share': None, 'n_selling': None, 'osd_share': None, 'fast_local': None,
           'dps': None, 'nesr': None, 'n_organic': 0, 'target': T}
    if not rows:
        return out
    organic = [p for p in rows if not p.get('sponsored')]
    out['n_organic'] = len(organic)
    out['sponsored_share'] = round((len(rows) - len(organic)) / len(rows), 4)
    out['fast_local'] = sum(1 for p in organic if str(p.get('speed') or '').lower() == 'fast')
    top10 = organic[:10]
    u10 = [u for u in (_val(p.get('units')) for p in top10) if u is not None and u >= 0]
    if u10:
        out['units_top10_median'] = round(float(np.median(u10)), 1)
    r10 = [r for r in (_val(p.get('reviews')) for p in top10) if r is not None and r >= 0]
    if r10:
        out['review_barrier'] = round(float(np.median(r10)), 1)
    units = [(p, _val(p.get('units'))) for p in organic]
    units = [(p, u) for p, u in units if u is not None and u >= 0]
    if units:
        sell_min = max(THRESH['selling_min'], float(T['sales']) / 3.0)
        selling = [p for p, u in units if u >= sell_min]
        out['n_selling'] = len(selling)
        total = sum(u for _, u in units)
        if total > 0:
            out['osd_share'] = round(sum(u for p, u in units if str(p.get('origin') or '').lower() == 'overseas')
                                     / total, 4)
            out['dps'] = round(total / len(selling), 1) if selling else None
        top = sorted((u for _, u in units[:THRESH['hhi_top_n']]), reverse=True)
        ts = sum(top)
        if ts > 0:
            s = np.array(top) / ts
            out['hhi'] = round(float((s * s).sum()), 4)
            out['top3_share'] = round(float(s[:3].sum()), 4)
        young = [(p, u) for p, u in units if (_val(p.get('age_months')) is not None
                                               and _val(p.get('age_months')) <= 12)]
        if young:
            out['nesr'] = round(sum(1 for _, u in young if u >= sell_min) / len(young), 4)
    return out


def risk_flags(titles, oversize=False):
    """Distinct file_rank.CHECKS names hit across the titles, plus 'oversize'."""
    if isinstance(titles, str):
        titles = [titles]
    names = []
    for t in (titles if isinstance(titles, (list, tuple, set)) else []):
        if not t:
            continue
        for name, rx in fr.CHECKS:
            if name not in names and rx.search(str(t)):
                names.append(name)
    if oversize and 'oversize' not in names:
        names.append('oversize')
    return names


# ---------- saturation ----------

def _hhi(m):
    """(HHI 0..1, from_top3) from m['hhi'], else estimated from the top-3 share (top 3 equal, the other 17
    of the top 20 equal)."""
    h = _val(m.get('hhi'))
    if h is not None:
        if 1.0 < h <= 10000.0:          # 0..10000 scale
            h /= 10000.0
        return clip(h), False
    s3 = _share(m.get('top3_share'))
    if s3 is None:
        return None, False
    s3 = clip(s3)
    return s3 * s3 / 3.0 + (1 - s3) ** 2 / (THRESH['hhi_top_n'] - 3), True


def saturation(m):
    """How crowded the niche is, 0-100 (synth 8.7):

      rb    = clip(ln(1 + RB / T.reviews) / ln 11)       RB = median reviews of the top 10
      conc  = clip((HHI - 0.05) / 0.30)                   HHI estimated from the top-3 share if missing
      ads   = sponsored share of page-1 slots
      crowd = clip(n_selling / 30)
      entry = clip(0.5 + (supply growth - demand growth)) 0.5 when either is unknown
      SAT   = 100 (.25 rb + .20 conc + .15 ads + .20 crowd + .20 entry)

    A missing part counts as 0.5 and lowers the confidence.
    Returns {'score', 'parts': [{'key', 'name', 'v', 'w', 'why', 'conf', 'known'}], 'why', 'conf'}.
    """
    m = m if isinstance(m, dict) else {}
    T = _target(m)
    w = THRESH['sat_w']
    neutral = THRESH['neutral']
    parts = []

    rb_raw = _val(m.get('review_barrier'))
    if rb_raw is not None and rb_raw >= 0 and T.get('reviews'):
        rb = clip(math.log1p(rb_raw / float(T['reviews'])) / math.log1p(THRESH['rb_top']))
        why = 'top 10 have a median of %s reviews (target %s or fewer)' % (_n(rb_raw), _n(T['reviews']))
        parts.append(_part('rb', rb, w['rb'], why, _input_conf(m, 'review_barrier', True), raw=_r(rb_raw, 1)))
    else:
        parts.append(_part('rb', neutral, w['rb'], 'review counts unknown', 'low', known=False))

    h, from_top3 = _hhi(m)
    if h is not None:
        conc = clip((h - THRESH['hhi_lo']) / THRESH['hhi_span'])
        s3 = _share(m.get('top3_share'))
        why = 'sales concentration HHI %.2f' % h
        if s3 is not None:
            why += ' (top 3 hold %s)' % _pct(clip(s3))
        if from_top3:
            why += ', estimated from the top-3 share'
        c = _input_conf(m, 'top3_share' if from_top3 else 'hhi', True)
        parts.append(_part('conc', conc, w['conc'], why, _min_conf(c, 'medium') if from_top3 else c,
                           raw=_r(h, 4)))
    else:
        parts.append(_part('conc', neutral, w['conc'], 'sales concentration unknown', 'low', known=False))

    ads = _share(m.get('sponsored_share'))
    if ads is not None:
        ads = clip(ads)
        parts.append(_part('ads', ads, w['ads'], 'sponsored listings take %s of page-1 slots' % _pct(ads),
                           _input_conf(m, 'sponsored_share', True), raw=_r(ads, 4)))
    else:
        parts.append(_part('ads', neutral, w['ads'], 'ad share unknown', 'low', known=False))

    ns = _val(m.get('n_selling'))
    if ns is not None and ns >= 0:
        crowd = clip(ns / THRESH['crowd_n'])
        sell_min = max(THRESH['selling_min'], float(T['sales']) / 3.0)
        why = '%d listing%s sell %s+ units a month' % (round(ns), '' if round(ns) == 1 else 's', _n(sell_min))
        parts.append(_part('crowd', crowd, w['crowd'], why, _input_conf(m, 'n_selling', True), raw=_r(ns, 1)))
    else:
        parts.append(_part('crowd', neutral, w['crowd'], 'number of selling listings unknown', 'low',
                           known=False))

    gs, gd = _val(m.get('supply_growth')), _val(m.get('demand_growth'))
    if gs is not None and gd is not None:
        diff = gs - gd
        entry = clip(neutral + diff)
        if abs(diff) < 0.05:
            why = 'sellers and demand growing at about the same pace'
        elif diff > 0:
            why = 'sellers growing %s faster than demand (%s vs %s a year)' % (_pct(diff), _pct(gs), _pct(gd))
        else:
            why = 'demand growing %s faster than sellers (%s vs %s a year)' % (_pct(-diff), _pct(gd), _pct(gs))
        c = _min_conf(_input_conf(m, 'supply_growth', True), _input_conf(m, 'demand_growth', True))
        parts.append(_part('entry', entry, w['entry'], why, c, raw=_r(diff, 4)))
    else:
        parts.append(_part('entry', neutral, w['entry'], 'growth of sellers vs demand unknown (counted as even)',
                           'low', known=False))

    score = 100.0 * sum(p['_v'] * p['_w'] for p in parts) / sum(p['_w'] for p in parts)
    missing = sum(1 for p in parts if not p['known'])
    cap = 'high' if missing == 0 else 'medium' if missing <= 2 else 'low'
    conf = _min_conf(cap, *[p['conf'] for p in parts if p['known']])
    score = round(float(clip(score, 0.0, 100.0)), 1) if missing < len(parts) else None
    return {'score': score, 'parts': _strip(parts), 'why': [p['why'] for p in parts], 'conf': conf,
            'missing': missing}


# ---------- opportunity ----------

def _margin_part(m, base_w):
    """(P, weight, why, conf, known) from margin, else Xray left_pct at half weight, else neutral."""
    mg = _val(m.get('margin'))
    if mg is not None:
        if 1.0 < abs(mg) <= 100.0:          # given as a percentage
            mg /= 100.0
        p = clip((mg - THRESH['margin_lo']) / THRESH['margin_span'])
        why = 'margin %s at the page-1 median price' % _pct(mg)
        return p, base_w, why, _input_conf(m, 'margin', True), True, mg
    lp = _share(m.get('left_pct'))
    if lp is not None:
        p = clip((lp - THRESH['margin_lo']) / THRESH['margin_span'])
        why = 'about %s of the price is left after Amazon fees (no product cost given, half weight)' % _pct(lp)
        return (p, base_w * THRESH['left_pct_weight'], why, _min_conf('medium', _input_conf(m, 'left_pct', True)),
                True, lp)
    return THRESH['neutral'], base_w, 'margin unknown (counted as neutral)', 'low', False, None


def _gap(m, fast_fn):
    """(G, why, conf, known) = 0.5 OSD + 0.5 fast_fn(fast_local); a missing half counts as 0.5."""
    neutral = THRESH['neutral']
    osd = _share(m.get('osd_share'))
    fl = _val(m.get('fast_local'))
    osd_v = clip(osd) if osd is not None else neutral
    fl_v = fast_fn(fl) if fl is not None and fl >= 0 else neutral
    g = 0.5 * osd_v + 0.5 * fl_v
    bits = []
    if fl is not None and fl >= 0:
        bits.append('%d fast local seller%s on page 1' % (round(fl), '' if round(fl) == 1 else 's'))
    if osd is not None:
        bits.append('%s of units go to overseas sellers' % _pct(clip(osd)))
    known = (osd is not None) + (fl is not None and fl >= 0)
    if known == 0:
        return g, 'local competition unknown (counted as neutral)', 'low', False
    why = ', '.join(bits)
    if known == 1:
        why += ' (the other half unknown)'
    c = 'medium' if known == 1 else 'high'
    c = _min_conf(c, *[_input_conf(m, k, True) for k in ('osd_share', 'fast_local') if m.get(k) is not None])
    return g, why, c, True


def _is_us(m):
    return _market(m) == 'US'


def opportunity(m):
    """Opportunity 0-100: a weighted geometric mean (synth 8.7).

      D = clip(ln(u_med10 / (T.sales / 3)) / ln 12)     median est. units of the top-10 organic
      M = niche momentum (0..1; 0.5 and low confidence when unknown)
      C = 1 - SAT / 100
      G = 0.5 OSD + 0.5 clip((12 - fast_local) / 10)
      P = clip((margin - 0.10) / 0.25)                 Xray left_pct at half weight when there is no margin
      w = D .25, M .15, C .20, G .20, P .20; for a US target G is .05; weights renormalised
      OPP = 100 * prod(max(x, 0.03)^w) * max(0, 1 - 0.15 * n_risk_flags)

    Labels: Strong >= 70, Worth a look >= 50, Weak >= 30, Skip.
    Returns {'score', 'label', 'parts': [{'key', 'name', 'v', 'w', 'why', 'conf', 'known'}], 'conf',
    'why', 'risk': {'n', 'flags', 'factor'}, 'saturation'}.
    """
    m = m if isinstance(m, dict) else {}
    T = _target(m)
    neutral = THRESH['neutral']
    w = dict(THRESH['opp_w'])
    if _is_us(m):
        w['G'] = THRESH['opp_w_us_G']
    raw = []                                 # (key, value, why, conf, known)

    u = _val(m.get('units_top10_median'))
    if u is not None and u >= 0 and T.get('sales'):
        base = float(T['sales']) / THRESH['demand_div']
        d = clip(math.log(u / base) / math.log(THRESH['demand_span'])) if u > 0 else 0.0
        why = 'top-10 median about %s units a month (target %s+)' % (_n(u), _n(T['sales']))
        raw.append(('D', d, why, _input_conf(m, 'units_top10_median', True), True))
    else:
        raw.append(('D', neutral, 'demand unknown (counted as neutral)', 'low', False))

    mo = _val(m.get('momentum'))
    if mo is not None:
        mo = mo / 100.0 if 1.0 < mo <= 100.0 else mo
        mo = clip(mo)
        raw.append(('M', mo, 'momentum %d/100' % round(100 * mo), _input_conf(m, 'momentum', True), True))
    else:
        raw.append(('M', neutral, 'momentum unknown (counted as neutral)', 'low', False))

    sat = saturation(m)
    if sat['score'] is not None:
        raw.append(('C', 1.0 - sat['score'] / 100.0, 'saturation %d/100 (lower is better)' % round(sat['score']),
                    sat['conf'], True))
    else:
        raw.append(('C', neutral, 'competition unknown (counted as neutral)', 'low', False))

    g, why, gc, gk = _gap(m, lambda fl: clip((THRESH['gap_fast_ref'] - fl) / THRESH['gap_fast_span']))
    raw.append(('G', g, why, gc, gk))

    p, pw, why, pc, pk, _ = _margin_part(m, w['P'])
    w['P'] = pw
    raw.append(('P', p, why, pc, pk))

    wn = _normalise(w)
    parts = [_part(k, v, wn[k], why, conf, known) for k, v, why, conf, known in raw]
    n_risk, names = _n_risk(m.get('risk_flags'))
    factor = max(0.0, 1.0 - THRESH['risk_step'] * n_risk)
    score = round(float(clip(_geo(parts) * factor, 0.0, 100.0)), 1) if any(p['known'] for p in parts) else None
    conf = confidence([p['conf'] for p in parts])
    why = sorted(parts, key=lambda p: p['_v'])
    why_lines = [p['why'] for p in why]
    if n_risk:
        why_lines.append('%d risk flag%s%s (-%s)' % (n_risk, '' if n_risk == 1 else 's',
                                                    (': ' + ', '.join(names)) if names else '', _pct(1 - factor)))
    return {'score': score, 'label': label_for(score), 'parts': _strip(parts), 'conf': conf, 'why': why_lines,
            'risk': {'n': n_risk, 'flags': names, 'factor': round(factor, 3)}, 'saturation': sat['score']}


# ---------- arbitrage ----------

def _boom(us):
    """(B 0..1, why, conf, known) = max(US trend score, US cluster surge) / 100."""
    us = us if isinstance(us, dict) else {}
    ts = _val(_get(us, 'trend_score', 'ts', 'TS'))
    cs = _val(_get(us, 'cluster_surge', 'cs', 'CS', 'surge'))
    mo = _val(us.get('momentum'))
    vals = []
    if ts is not None:
        vals.append(('US searches score %d/100' % round(clip(ts, 0.0, 100.0)), clip(ts / 100.0)))
    if cs is not None:
        vals.append(('US Amazon lists surge %d/100' % round(clip(cs, 0.0, 100.0)), clip(cs / 100.0)))
    if not vals and mo is not None:
        mo = mo / 100.0 if 1.0 < mo <= 100.0 else mo
        vals.append(('US momentum %d/100' % round(100 * clip(mo)), clip(mo)))
    if not vals:
        return THRESH['neutral'], 'US boom strength unknown (counted as neutral)', 'low', False
    why, b = max(vals, key=lambda t: t[1])
    return b, why, _conf_word(us.get('conf')) or 'high', True


def _trends_share(t):
    """Share of non-zero Trends months in the target market (last 12), from a share or a list of values."""
    s = _share(_get(t, 'trends_nonzero_share', 'trends_share'))
    if s is not None:
        return clip(s)
    vals = _get(t, 'trends_monthly', 'trends_values', 'trends_months')
    if hasattr(vals, 'tolist'):
        vals = vals.tolist()
    if isinstance(vals, dict):
        vals = [vals[k] for k in sorted(vals)]
    if isinstance(vals, (list, tuple)) and vals:
        last = [_val(v) for v in list(vals)[-THRESH['rd_trends_months']:]]
        last = [v for v in last if v is not None]
        if last:
            return sum(1 for v in last if v > 0) / float(len(last))
    return None


def _readiness(t, T, mk_name):
    """(Rd, why, conf, known) = .4 autocomplete + .3 Trends share + .3 clip(badge units / T.sales),
    floored at 0.2. A missing piece counts as 0.5."""
    neutral = THRESH['neutral']
    rw = THRESH['rd_w']
    ac = _get(t, 'autocomplete', 'autocomplete_match', 'suggest')
    ac = None if ac is None else bool(ac)
    ts = _trends_share(t)
    bought = _val(_get(t, 'bought_mid_sum', 'badge_units'))
    bd = clip(bought / float(T['sales'])) if bought is not None and bought >= 0 and T.get('sales') else None
    rd = (rw['autocomplete'] * (neutral if ac is None else float(ac))
          + rw['trends'] * (neutral if ts is None else ts)
          + rw['badges'] * (neutral if bd is None else bd))
    rd = max(THRESH['rd_floor'], rd)
    bits = []
    if ac is not None:
        bits.append('%s autocomplete %s the term' % (mk_name, 'knows' if ac else 'does not know'))
    if ts is not None:
        bits.append('Trends shows searches in %d of the last 12 months' % round(12 * ts))
    if bought is not None and bought >= 0:
        bits.append('badges show about %s units a month' % _n(bought))
    known = sum(x is not None for x in (ac, ts, bd))
    if known == 0:
        return rd, '%s readiness unknown (counted as neutral)' % mk_name, 'low', False
    conf = 'high' if known == 3 else 'medium' if known == 2 else 'low'
    return rd, '; '.join(bits), conf, True


def _window(t, mk_name):
    """(W, window dict): 1.0 when the target's trend is not rising yet, 0.6 when it is (Emerging /
    Growing / Breakout), 0.3 when it is Growing and 10+ fast local sellers are already there."""
    lab = base_label(_get(t, 'trend_label', 'label'))
    fl = _val(t.get('fast_local'))
    if lab == 'Growing' and fl is not None and fl >= THRESH['w_crowded_fast']:
        w, status = THRESH['w_crowded'], 'crowded'
        why = '%s searches already growing and %d fast local sellers are in' % (mk_name, round(fl))
    elif lab in THRESH['rising_labels']:
        w, status = THRESH['w_rising'], 'closing'
        why = '%s searches already %s' % (mk_name, lab.lower())
    else:
        w, status = THRESH['w_open'], 'open'
        why = ('%s searches not rising yet (%s)' % (mk_name, lab) if lab
               else '%s trend unknown (assumed not rising yet)' % mk_name)
    return w, {'w': w, 'status': status, 'label': lab or None, 'why': why}


def arbitrage(us, target_m):
    """US boom -> target market (AU / AE) score, 0-100 (synth 8.7 without lead-lag):

      B  = max(US trend score, US cluster surge) / 100
      Rd = .4 [autocomplete in t knows the term] + .3 share of non-zero Trends months (last 12)
           + .3 clip(bought_mid_sum_t / T_t.sales), at least 0.2
      G  = 0.5 OSD_t + 0.5 clip(1 - fast_local_t / 10)
      P  = clip((margin_t - 0.10) / 0.25)   (Xray left_pct at half weight when there is no margin)
      W  = 1.0 if the target's trend label is not Emerging / Growing / Breakout, else 0.6,
           or 0.3 if it is Growing and fast_local_t >= 10
      ARB = 100 * B^.30 * Rd^.15 * G^.25 * P^.20 * W^.10   (each term at least 0.03)

    us: {'trend_score', 'cluster_surge'} (0..100; 'momentum' 0..1 as a fallback).
    target_m: {'market', 'target', 'autocomplete', 'trends_nonzero_share' or 'trends_monthly',
    'bought_mid_sum', 'osd_share', 'fast_local', 'margin' or 'left_pct', 'trend_label'}.
    Returns {'score', 'label', 'parts', 'why', 'window', 'conf'}.
    """
    t = target_m if isinstance(target_m, dict) else {}
    T = _target(t)
    mk = _market(t)
    mk_name = fr.MARKET_NAMES.get(mk, 'target market') if mk else 'target market'
    w = dict(THRESH['arb_w'])
    raw = []

    b, why, bc, bk = _boom(us)
    raw.append(('B', b, why, bc, bk))
    rd, why, rc, rk = _readiness(t, T, mk_name)
    raw.append(('Rd', rd, why, rc, rk))
    g, why, gc, gk = _gap(t, lambda fl: clip(1.0 - fl / THRESH['arb_fast_span']))
    raw.append(('G', g, why, gc, gk))
    p, pw, why, pc, pk, _ = _margin_part(t, w['P'])
    w['P'] = pw
    raw.append(('P', p, why, pc, pk))
    wv, window = _window(t, mk_name)
    lab_known = window['label'] is not None
    raw.append(('W', wv, window['why'], _input_conf(t, 'trend_label', True) if lab_known else 'low', lab_known))

    wn = _normalise(w)
    parts = [_part(k, v, wn[k], why, conf, known) for k, v, why, conf, known in raw]
    score = round(float(clip(_geo(parts), 0.0, 100.0)), 1) if any(p['known'] for p in parts) else None
    conf = confidence([p['conf'] for p in parts])
    return {'score': score, 'label': label_for(score), 'parts': _strip(parts),
            'why': [p['why'] for p in sorted(parts, key=lambda p: p['_v'])], 'window': window, 'conf': conf,
            'market': mk}
