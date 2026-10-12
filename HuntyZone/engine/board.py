"""Product board rules: when a stage is done, what an order costs, stock colours, how fast stock sells and when to
re-order. Pure functions. Days are 'YYYY-MM-DD' strings (or dates), times unix seconds, money in the currency given
and totals in US$ through `per_usd` ({'USD': 1, 'AUD': 1.55, ...})."""
import datetime as dt
import math

STAGES = ('sourcing', 'launch', 'reorder')
STAGE_NAMES = {'sourcing': 'Sourcing & PO', 'launch': 'Launch & strategy', 'reorder': 'Re-order'}
MODES = ('sea', 'air')
HANDLED = ('done', 'adjusted', 'skip')
MARKET_NAMES = {'US': 'USA', 'AU': 'Australia', 'AE': 'UAE'}
THRESH = {
    'amber_pct': 50,            # stock is amber at or below this share of its shipment (red: settings board_low_pct)
    'soon_days': 14,            # ... or when the re-order date is this close
    'due_soon_days': 3,         # a due date this close is amber
    'rate_window_days': 45,     # stock counts used for the sell rate
    'rate_min_days': 3,         # the counts must span at least this long
    'month_days': 30.4,
}
DAY = 86400


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _r(x, d=2):
    return None if x is None else round(x, d)


def day(x):
    """A date from 'YYYY-MM-DD' or a date; None otherwise."""
    if isinstance(x, dt.date):
        return x
    try:
        return dt.date.fromisoformat(str(x)[:10])
    except (TypeError, ValueError):
        return None


def to_usd(amount, cur, per_usd):
    v, r = _num(amount), (per_usd or {}).get(cur or 'USD')
    return v / r if v is not None and r else None


def line_cost(unit_cost, cost_cur, line, per_usd):
    """One market's shipment in US$: goods, shipping, total, and per unit (None where a number is missing)."""
    units = int(_num(line.get('units')) or 0)
    unit_usd = to_usd(unit_cost, cost_cur, per_usd)
    ship_usd = to_usd(line.get('ship_cost'), line.get('ship_cur') or 'USD', per_usd)
    goods = unit_usd * units if unit_usd is not None and units else None
    total = goods + ship_usd if goods is not None and ship_usd is not None else None
    return {'units': units, 'unit_usd': _r(unit_usd, 4), 'goods_usd': _r(goods), 'ship_usd': _r(ship_usd),
            'total_usd': _r(total), 'ship_unit_usd': _r(ship_usd / units, 4) if ship_usd is not None and units else None,
            'landed_unit_usd': _r(total / units, 4) if total is not None and units else None}


def sourcing_status(order, lines, per_usd):
    """Is an order complete: a price per unit and, for every market in it, units, shipment type and shipping cost?
    order {'unit_cost', 'cost_cur'}; lines [{'market', 'units', 'ship_cost', 'ship_cur', 'ship_mode'}].
    Returns {'complete', 'missing': [plain text], 'progress' 0-1, 'markets': {mk: line_cost + 'mode'}, 'units',
    'total_usd' (None while a cost is missing), 'known_usd' (what is known so far)}."""
    missing = []
    if not (_num(order.get('unit_cost')) or 0) > 0:
        missing.append('the price per unit')
    if not lines:
        missing.append('at least one market')
    markets, units, known, whole = {}, 0, 0.0, bool(lines)
    for ln in lines:
        name = MARKET_NAMES.get(ln.get('market'), ln.get('market'))
        if not (_num(ln.get('units')) or 0) > 0:
            missing.append('units for ' + name)
        if ln.get('ship_mode') not in MODES:
            missing.append('the shipment type to ' + name)
        if _num(ln.get('ship_cost')) is None:
            missing.append('the shipping cost to ' + name)
        c = line_cost(order.get('unit_cost'), order.get('cost_cur'), ln, per_usd)
        c['mode'] = ln.get('ship_mode')
        markets[ln.get('market')] = c
        units += c['units']
        if c['total_usd'] is None:
            whole = False
            known += (c['goods_usd'] or 0) + (c['ship_usd'] or 0)
        else:
            known += c['total_usd']
    need = 1 + max(1, 3 * len(lines))
    return {'complete': not missing, 'missing': missing, 'progress': round(1 - min(len(missing), need) / need, 3),
            'markets': markets, 'units': units, 'total_usd': round(known, 2) if whole else None,
            'known_usd': round(known, 2)}


def launch_status(hints, live):
    """Launch & strategy is done when every hint is handled (done, changed or not relevant) and the product is live
    on Amazon in at least one market. hints [{'status'}]; live {market: 'YYYY-MM-DD' or None}."""
    total = len(hints or ())
    done = sum(1 for h in hints or () if h.get('status') in HANDLED)
    on = {mk: d for mk, d in (live or {}).items() if day(d)}
    missing = []
    if done < total:
        missing.append('%d hint%s to check' % (total - done, '' if total - done == 1 else 's'))
    if not on:
        missing.append('the day it went live on Amazon')
    return {'complete': not missing, 'done': done, 'total': total, 'live': on, 'missing': missing,
            'progress': round((done + (1 if on else 0)) / (total + 1), 3)}


def unlocked(milestones):
    """The stages a card may be in: Launch after 'Order ready', Re-order after 'Launched'."""
    ms = milestones or {}
    return ['sourcing'] + (['launch'] if ms.get('order_ready') else []) + (['reorder'] if ms.get('launched') else [])


def sell_rate(counts, sent=None, arrived=None):
    """Units sold a day from stock counts [(ts, units_left)] oldest first: the least-squares slope of the counts of
    the last 45 days when they span 3 days or more, else (shipment - newest count) / days since it arrived."""
    pts = [(t, _num(v)) for t, v in counts or () if t and _num(v) is not None]
    if not pts:
        return None
    last = pts[-1][0]
    recent = [(t, v) for t, v in pts if t >= last - THRESH['rate_window_days'] * DAY]
    if len(recent) >= 2 and recent[-1][0] - recent[0][0] >= THRESH['rate_min_days'] * DAY:
        xs = [(t - recent[0][0]) / DAY for t, _ in recent]
        ys = [v for _, v in recent]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        return round(max(0.0, -slope), 3)
    a, s = day(arrived), _num(sent)
    if a and s:
        days = (dt.date.fromtimestamp(last) - a).days
        if days >= THRESH['rate_min_days']:
            return round(max(0.0, s - pts[-1][1]) / days, 3)
    return None


def forecast(left, rate, lead_days, counted):
    """When stock runs out at `rate` a day after the count on day `counted`, and the last day to re-order (the
    stock-out day minus the lead time)."""
    v, r, c = _num(left), _num(rate), day(counted)
    if v is None or not r or not c:
        return {'days_left': None, 'sellout': None, 'reorder_by': None}
    gone = c + dt.timedelta(days=int(v / r))
    return {'days_left': int(v / r), 'sellout': gone.isoformat(),
            'reorder_by': (gone - dt.timedelta(days=int(lead_days or 0))).isoformat()}


def stock_status(sent, left, low_pct=30, reorder_by=None, today=None):
    """Red at or below low_pct of the shipment, amber at or below 50% or when the re-order day is within 14 days,
    else green. order_now: the re-order day has come."""
    s, v = _num(sent), _num(left)
    rb, t = day(reorder_by), day(today) or dt.date.today()
    order_now = rb is not None and rb <= t
    if not s or v is None:
        return {'pct': None, 'level': 'unknown', 'order_now': order_now}
    p = max(0.0, v / s * 100)
    soon = rb is not None and (rb - t).days <= THRESH['soon_days']
    level = 'red' if p <= low_pct else 'amber' if p <= THRESH['amber_pct'] or soon else 'green'
    return {'pct': round(p, 1), 'level': level, 'order_now': order_now}


def due_state(due, done=False, today=None):
    """'late' after the due day, 'soon' within 3 days of it, else 'ok'; None without a due day or once done."""
    d, t = day(due), day(today) or dt.date.today()
    if not d or done:
        return None
    if t > d:
        return 'late'
    return 'soon' if (d - t).days <= THRESH['due_soon_days'] else 'ok'


def target_progress(rate, target):
    """Sales a month at `rate` a day against a monthly target: {'month', 'pct'}."""
    r, t = _num(rate), _num(target)
    if r is None or not t:
        return None
    m = r * THRESH['month_days']
    return {'month': round(m), 'pct': round(m / t * 100)}
