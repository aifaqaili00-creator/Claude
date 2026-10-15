"""The first year of a product, week by week, in three cases (good, likely, poor).

Sales start slowly and reach a new listing's full pace as reviews come in; ads cost more at launch and less later;
the season (Google Trends) moves sales up and down; stock runs out when a re-order comes too late, and each re-order
is paid when it is placed. The result: the week your money is back, the most money tied up at once, the profit in a
year, the units sold, lost and left.

The idea of 52-week good / likely / poor projections comes from Omniscient; this is written for HuntyZone: the sales
a new listing might win come from engine.launch.newcomer_units (page-1 badges and the review wall), the money per
sale from engine.profit.unit_economics (with your supplier price), the season from engine.trendfeat and the time to
restock from engine.planner.lead_days. Rough by design: every number is an estimate.
Pure functions; JSON-safe.
"""
import datetime as dt
import math

WEEKS = 52
WEEKS_PER_MONTH = 52 / 12
RAMP_START = 0.2                 # a new listing sells at 20% of its full pace in week 1 ...
COVER_WEEKS = 8                  # a re-order covers the time to restock plus this many weeks (at least the first order)
PHASES = ((4, 'launch'), (12, 'growth'), (WEEKS, 'steady'))      # ... ads by phase: weeks 1-4, 5-12, 13-52
CASES = {
    'good': {'units': 'hi', 'ramp_weeks': 6, 'ads': (0.30, 0.15, 0.08), 'price': 1.0},
    'likely': {'units': 'v', 'ramp_weeks': 10, 'ads': (0.35, 0.20, 0.10), 'price': 1.0},
    'poor': {'units': 'lo', 'ramp_weeks': 16, 'ads': (0.45, 0.25, 0.12), 'price': 0.95},
}
NAMES = {'good': 'Good case', 'likely': 'Likely case', 'poor': 'Poor case'}


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def ad_share(case, week):
    """Ad spend as a share of sales in a week of one case."""
    for i, (last, _) in enumerate(PHASES):
        if week <= last:
            return CASES[case]['ads'][i]
    return CASES[case]['ads'][-1]


def ramp(case, week):
    """Share of the full selling pace a new listing reaches in a week (reviews come in)."""
    n = CASES[case]['ramp_weeks']
    return min(1.0, RAMP_START + (1 - RAMP_START) * (week - 1) / n)


def run_case(case, monthly, econ, units, lead_days, start, si=None):
    """One case. monthly: full selling pace (units a month); econ: unit economics with your product cost; units: the
    first order (and each re-order); start: the day stock is on sale (week 1)."""
    c = CASES[case]
    price = _num(econ.get('price')) or 0.0
    landed = _num(econ.get('Landed')) or 0.0
    before_ads = (_num(econ.get('Profit')) or 0.0) + (_num(econ.get('Ads')) or 0.0)    # profit a sale before ads
    p_price = price * c['price']
    p_before = before_ads - (price - p_price)                                         # a lower price comes off profit
    lead_w = max(1, math.ceil((lead_days or 0) / 7))
    weekly = monthly / WEEKS_PER_MONTH
    stock, cash, cum = float(units), -units * landed, 0.0
    pending, rows = [], []
    for w in range(1, WEEKS + 1):
        day = start + dt.timedelta(days=7 * (w - 1))
        stock += sum(q for at, q in pending if at == w)
        pending = [(at, q) for at, q in pending if at > w]
        season = si[(day + dt.timedelta(days=3)).month - 1] if si else 1.0
        want = weekly * ramp(case, w) * season
        sold = min(stock, want)
        stock -= sold
        revenue = sold * p_price
        ads = revenue * ad_share(case, w)
        profit = sold * p_before - ads
        cum += profit
        cash += sold * (p_before + landed) - ads
        reorder = 0
        if not pending and w <= WEEKS - lead_w and stock <= want * (lead_w + 1):
            reorder = max(units, math.ceil(weekly * season * (lead_w + COVER_WEEKS)))
            pending.append((w + lead_w, reorder))
            cash -= reorder * landed
        rows.append({'week': w, 'day': day.isoformat(), 'units': round(sold, 1), 'lost': round(want - sold, 1),
                     'revenue': round(revenue, 2), 'ads': round(ads, 2), 'profit': round(profit, 2),
                     'profit_so_far': round(cum, 2), 'cash': round(cash, 2), 'stock': round(stock, 1),
                     'reorder': reorder})
    low = min([-units * landed] + [r['cash'] for r in rows])
    back = next((r['week'] for r in rows if r['cash'] >= 0), None)
    out_w = next((r['week'] for r in rows if r['lost'] >= 0.5), None)
    on_order = sum(q for _, q in pending)
    return {'case': case, 'name': NAMES[case], 'monthly_pace': round(monthly, 1), 'price': round(p_price, 2),
            'profit_year': round(cum, 2), 'money_back_week': back, 'money_back_day': rows[back - 1]['day'] if back else None,
            'most_money_needed': round(-low, 2), 'cash_end': round(rows[-1]['cash'], 2),
            'units_year': round(sum(r['units'] for r in rows)), 'lost_year': round(sum(r['lost'] for r in rows)),
            'stockout_weeks': sum(1 for r in rows if r['lost'] >= 0.5), 'first_stockout_week': out_w,
            'reorders': sum(1 for r in rows if r['reorder']), 'reordered_units': sum(r['reorder'] for r in rows),
            'stock_end': round(stock + on_order), 'stock_end_value': round((stock + on_order) * landed, 2),
            'ads_year': round(sum(r['ads'] for r in rows), 2), 'revenue_year': round(sum(r['revenue'] for r in rows), 2),
            'weeks': rows}


def plan(econ, newcomer, units, lead_days, start, si=None):
    """The three cases. econ: engine.profit.unit_economics with your product cost; newcomer: engine.launch.newcomer_units;
    units: first order; lead_days: order to on sale (engine.planner.lead_days); start: 'YYYY-MM-DD' the stock goes on
    sale; si: 12 monthly season multipliers (mean 1) or None. -> {'status': 'ok', 'cases', 'assumptions', ...} or
    {'status': 'needs_cost' | 'unknown_sales', 'why'}."""
    if not econ or not (_num(econ.get('COGS')) or 0) > 0:
        return {'status': 'needs_cost', 'why': 'Type your supplier price in the profit calculator to see the first year.'}
    if not newcomer or _num(newcomer.get('v')) is None:
        return {'status': 'unknown_sales', 'why': 'No listing on page 1 shows a "bought in past month" badge, so the '
                                                  'sales a new listing might win are unknown.'}
    n = max(1, int(units or 0))
    day = dt.date.fromisoformat(start)
    s = [float(x) for x in si] if si and len(si) == 12 and all(_num(x) is not None for x in si) else None
    cases = {}
    for k, c in CASES.items():
        m = _num(newcomer.get(c['units'])) or _num(newcomer['v'])
        cases[k] = run_case(k, max(0.0, m), econ, n, lead_days, day, s)
    phases = ', '.join('weeks %s %d%%' % (rng, round(100 * CASES['likely']['ads'][i])) for i, rng in
                       enumerate(('1-4', '5-12', '13-52')))
    return {'status': 'ok', 'start': start, 'units': n, 'lead_days': lead_days, 'seasonal': bool(s),
            'currency': econ.get('currency'), 'cases': cases,
            'assumptions': [
                'Full pace: the sales a new listing might win (%s a month likely, %s to %s), from %s.' % (
                    round(newcomer['v']), round(newcomer.get('lo') or newcomer['v']), round(newcomer.get('hi') or newcomer['v']),
                    newcomer.get('why') or 'page-1 badges'),
                'Week 1 is when the stock goes on sale (about %s); sales start at %d%% of the full pace and reach it '
                'after %d weeks (good %d, poor %d).' % (start, round(100 * RAMP_START), CASES['likely']['ramp_weeks'],
                                                       CASES['good']['ramp_weeks'], CASES['poor']['ramp_weeks']),
                'Ads as a share of sales (likely case): %s. The poor case also sells 5%% cheaper.' % phases,
                'Re-orders are paid when placed and on sale %d days later; each covers that time plus %d weeks of '
                'sales (at least %d units, your first order).' % (lead_days, COVER_WEEKS, n),
                'Moved by the Google Trends season month by month.' if s else 'No season: Google Trends not read or flat.',
                'Profit per sale before ads: %s %s (your product cost, Amazon\'s fees, freight and duty).' % (
                    econ.get('currency') or '', '{:,.2f}'.format((_num(econ.get('Profit')) or 0) + (_num(econ.get('Ads')) or 0)))]}
