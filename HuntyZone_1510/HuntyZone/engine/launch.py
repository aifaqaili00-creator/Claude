"""Where to launch: the same product, supplier price and first order compared across marketplaces.

A new listing is assumed to win the page-1 average sales per seller (engine.sellers, from "bought in past month"
badges), less when the top listings have many reviews to beat. Rough by design: every number is an estimate with a
range, and the pick needs a market that makes money and sells a first order out within six months.
Pure functions; JSON-safe output.
"""
import math

import file_rank as fr

from .stats import clip

THRESH = {
    'moat_top': 1.2,           # newcomer share factor = clip(1.2 - review barrier / (4 x the market's review target),
    'moat_div': 4.0,           # 0.3, 1): about full with few reviews to beat, 0.3 when the top has 4x the target or more
    'moat_min': 0.3,
    'moat_unknown': 0.7,       # review barrier unknown
    'spread': 0.5,             # range of +-50% when the per-seller estimate has none
    'max_months': 6.0,         # a market whose first order takes longer to sell is not picked
}


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def newcomer_units(per_seller, review_barrier, market):
    """Sales a month a new listing might win: the page-1 average per seller times a review-moat factor.
    per_seller: {'v', 'lo', 'hi'} (engine.sellers) or a number. Returns an est {'v', 'lo', 'hi', 'conf', 'factor',
    'why'} or None without a per-seller figure."""
    ps = per_seller if isinstance(per_seller, dict) else {'v': per_seller}
    v = _num(ps.get('v'))
    if v is None or v <= 0:
        return None
    target = fr.TARGETS.get(market, fr.TARGETS['US'])['reviews']
    rb = _num(review_barrier)
    f = (clip(THRESH['moat_top'] - rb / (THRESH['moat_div'] * target), THRESH['moat_min'], 1.0) if rb is not None
         else THRESH['moat_unknown'])
    lo, hi = _num(ps.get('lo')), _num(ps.get('hi'))
    lo = lo if lo is not None else v * (1 - THRESH['spread'])
    hi = hi if hi is not None else v * (1 + THRESH['spread'])
    why = 'the page-1 average of about %d sales a month per seller' % round(v)
    why += (', x%.2f because the top listings have about %d reviews (aim: %d or fewer)' % (f, round(rb), target)
            if rb is not None else ', x%.2f because their reviews are unknown' % f)
    return {'v': round(v * f, 1), 'lo': round(lo * f, 1), 'hi': round(hi * f, 1), 'conf': 'low', 'factor': round(f, 2),
            'why': why}


def simulate(units, econ, newcomer):
    """A first order of `units` at the unit economics `econ` (engine.profit.unit_economics, with your supplier price):
    money needed, sales that win it back, months to sell out at the newcomer rate, profit on the order and a month
    (local money and US$). newcomer: newcomer_units() or None."""
    n = max(1, int(units or 0))
    landed, profit = _num(econ.get('Landed')) or 0.0, _num(econ.get('Profit')) or 0.0
    fx = _num(econ.get('fx')) or 1.0
    rate = (newcomer or {}).get('v')
    back = profit + landed
    out = {'units': n, 'cash': round(n * landed, 2), 'profit_unit': round(profit, 2), 'margin': econ.get('Margin'),
           'profit_total': round(n * profit, 2),
           'payback_sales': math.ceil(n * landed / back) if profit > 0 and back > 0 else None,
           'months': round(n / rate, 1) if rate else None,
           'profit_month': round(rate * profit, 2) if rate else None,
           'profit_month_usd': round(rate * profit / fx, 2) if rate else None, 'notes': []}
    if profit <= 0:
        out['notes'].append('each sale loses money at this price')
    elif out['months'] is not None and out['months'] > THRESH['max_months']:
        out['notes'].append('slow: about %s months to sell %d units, and storage after two months is not counted'
                            % (round(out['months']), n))
    if rate is None:
        out['notes'].append('sales unknown: no "bought in past month" badges on page 1')
    return out


def pick(sims):
    """The market to launch in first, from {market: simulate() output with an optional 'gap' score}: the most profit
    a month (US$) among markets that make money and sell out within six months; the gap score breaks ties.
    {'market', 'why'} or None."""
    ok = [(mk, s) for mk, s in (sims or {}).items()
          if s and (s.get('profit_unit') or 0) > 0 and s.get('profit_month_usd')
          and s.get('months') is not None and s['months'] <= THRESH['max_months']]
    if not ok:
        return None
    mk, s = max(ok, key=lambda kv: (kv[1]['profit_month_usd'], kv[1].get('gap') or 0))
    return {'market': mk, 'why': 'about US$%s profit a month, and a first order of %d sells out in about %s months'
            % ('{:,.0f}'.format(s['profit_month_usd']), s['units'], ('%.1f' % s['months']).rstrip('0').rstrip('.'))}
