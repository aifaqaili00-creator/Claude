"""Product board rules: when a stage is done, what an order costs, stock colours, how fast stock sells and when to
re-order. Pure functions. Days are 'YYYY-MM-DD' strings (or dates), times unix seconds, money in the currency given
and totals in US$ through `per_usd` ({'USD': 1, 'AUD': 1.55, ...})."""
import datetime as dt
import math

STAGES = ('idea', 'approval', 'sourcing', 'launch', 'reorder')
STAGE_NAMES = {'idea': 'Idea', 'approval': 'Approval', 'sourcing': 'Sourcing & PO', 'launch': 'Launching strategy',
               'reorder': 'Re-order'}
# what a new product is checked against before money goes into it; each card can add its own or remove some
APPROVAL_CHECKS = (
    'Demand checked: enough buyers a month in the target market',
    'Few local sellers: a real gap in Australia or the UAE',
    'Profit checked: margin is good after fees, shipping and ads',
    'Supplier found on Alibaba with a sample price',
    'Not restricted: no hazmat, brand gating or certificates needed',
    'Size and weight are fine for FBA',
)
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
    'sales_min_days': 7,        # Amazon's daily order counts needed before they set the selling pace
    'soon_chip_days': 7,        # a countdown this close is amber
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
    """Launching strategy is done when every hint is handled (done, changed or not relevant) and the product is live
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


def idea_status(asin, alibaba):
    """An idea is complete once it has the product's Amazon ASIN and the supplier's Alibaba link."""
    missing = [what for v, what in ((asin, 'the Amazon ASIN'), (alibaba, 'the Alibaba link')) if not v]
    return {'complete': not missing, 'done': 2 - len(missing), 'total': 2, 'missing': missing,
            'progress': round((2 - len(missing)) / 2, 3)}


def approval_status(items):
    """The approval checklist: approved when every check is ticked (and there is at least one)."""
    items = items or []
    done = sum(1 for i in items if i.get('done'))
    total = len(items)
    return {'complete': bool(total) and done == total, 'done': done, 'total': total,
            'missing': [i['text'] for i in items if not i.get('done')],
            'progress': round(done / total, 3) if total else 0.0}


def unlocked(milestones):
    """The stages a card may be in: Approval once the idea is done, Sourcing after 'Approved', Launch after
    'Order ready', Re-order after 'Launched'."""
    ms = milestones or {}
    return ['idea'] + (['approval'] if ms.get('idea_done') else []) + (['sourcing'] if ms.get('approved') else []) + \
        (['launch'] if ms.get('order_ready') else []) + (['reorder'] if ms.get('launched') else [])


def next_step(milestones, idea, approval, sourcing, launch, stocks=(), reorders=()):
    """The one thing to do next on a product, in plain words: {'text', 'tab' (the card tab that does it), 'tone'
    ('todo', 'warn' or 'bad'), 'order' (the order to show on the Sourcing & PO tab)}, or None while nothing waits.
    idea, approval, sourcing (the first order) and launch are the *_status results; stocks [{'market', 'level',
    'order_now'}]; reorders [{'n', 'missing'}] are the re-orders not complete yet."""
    ms = milestones or {}

    def step(text, tab, tone='todo', order=None):
        return {'text': text, 'tab': tab, 'tone': tone, 'order': order}

    def more(miss):
        return miss[0] + (' and %d more' % (len(miss) - 1) if len(miss) > 1 else '')

    if not ms.get('idea_done'):
        return step('Add ' + ' and '.join(idea['missing'] or ['the Amazon ASIN']), 'idea')
    if not ms.get('approved'):
        left = approval['total'] - approval['done']
        return step('Tick %d approval check%s' % (left, '' if left == 1 else 's') if left > 0 else 'Add an approval check',
                    'approval')
    if not ms.get('order_ready'):
        return step('Add ' + more(sourcing['missing'] or ['the order details']), 'sourcing', order=1)
    if not ms.get('launched'):
        left = launch['total'] - launch['done']
        return step('Check %d launch hint%s' % (left, '' if left == 1 else 's') if left > 0 else
                    'Set the day it went live on Amazon', 'launch')
    for r in reorders:
        if r['missing']:
            return step('Finish re-order #%d: add %s' % (r['n'], more(r['missing'])), 'sourcing', order=r['n'])
    for tone, hit, text in (('bad', lambda s: s.get('order_now'), 'Re-order now for %s'),
                            ('warn', lambda s: s.get('level') == 'red', 'Low stock in %s: plan a re-order')):
        s = next((s for s in stocks if hit(s)), None)
        if s:
            return step(text % MARKET_NAMES.get(s['market'], s['market']), 'reorder', tone)
    return None


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


# ---------- live sales, the card face, the timeline ----------
def sales_rate(days, today=None, window=28):
    """Units sold a day from Amazon's daily order counts [('YYYY-MM-DD', units)] (SP-API Sales): the average of the
    last `window` complete days that have a count; None with fewer than THRESH['sales_min_days'] of them."""
    t = day(today) or dt.date.today()
    recent = [_num(u) for d, u in days or () if day(d) and _num(u) is not None and 0 < (t - day(d)).days <= window]
    if len(recent) < THRESH['sales_min_days']:
        return None
    return round(sum(recent) / len(recent), 3)


def ring(progress, late=False):
    """A card's progress ring: every stage an equal share. {'pct', 'tone': 'bad' (overdue) | 'good' (all done) |
    'accent'}."""
    p = round(sum(min(1.0, max(0.0, _num((progress or {}).get(k)) or 0.0)) for k in STAGES) / len(STAGES) * 100)
    return {'pct': p, 'tone': 'bad' if late else 'good' if p >= 100 else 'accent'}


def countdown(dates, today=None):
    """The one date a card waits for, as a chip. dates: [{'kind': 'reorder' | 'due' | 'eta' | 'order_by' (the last day
    to order for the season, with 'mode' sea or air), 'day', 'market'?}].
    The nearest one wins; a re-order day or a due day that has passed comes first. -> {'kind', 'day', 'days',
    'text', 'tone'} or None."""
    t = day(today) or dt.date.today()
    found = [(day(d.get('day')), d) for d in dates or () if day(d.get('day')) and d.get('kind') in COUNTDOWN]
    if not found:
        return None
    d, it = min(found, key=lambda x: ((x[0] - t).days >= 0 if x[1]['kind'] != 'eta' else True, x[0]))
    n = (d - t).days
    k = it['kind']
    if k == 'reorder':
        text = 'Re-order now' if n <= 0 else 'Re-order in %d day%s' % (n, '' if n == 1 else 's')
    elif k == 'due':
        text = '%d day%s late' % (-n, '' if n == -1 else 's') if n < 0 else 'Due today' if n == 0 else \
            'Due in %d day%s' % (n, '' if n == 1 else 's')
    elif k == 'order_by':
        text = ('Order today' if n <= 0 else 'Order in %d day%s' % (n, '' if n == 1 else 's')) + \
            (' by air' if it.get('mode') == 'air' else '')
    else:
        text = 'At Amazon today' if n <= 0 else 'At Amazon in %d day%s' % (n, '' if n == 1 else 's')
    tone = 'bad' if n < 0 or (k in ('reorder', 'order_by') and n <= 0) else 'warn' if n <= THRESH['soon_chip_days'] else 'line'
    return {'kind': k, 'day': d.isoformat(), 'days': n, 'text': text, 'tone': tone, 'market': it.get('market')}


COUNTDOWN = ('reorder', 'due', 'eta', 'order_by')
TIMELINE = {'due': 'Due', 'step': None, 'eta': 'At Amazon (expected)', 'live': 'Live on Amazon', 'reorder': 'Re-order by',
            'order_by': 'Last day to order'}
STEP_NAMES = {'deposit': 'Deposit paid', 'produced': 'Produced', 'shipped': 'Shipped', 'arrived': 'Arrived'}


def timeline_events(cards, today=None):
    """Every dated thing on the board, for the timeline. cards: [{'id', 'title', 'color', 'stage', 'tasks': [{'id',
    'stage', 'due_day', 'start_day', 'done'}], 'lines': [{'n', 'market', 'steps', 'eta'}], 'live': {mk: day},
    'reorder': [{'market', 'day'}], 'order_by': [{'market', 'day', 'mode', 'peak'}]}]. -> [{'card_id', 'title', 'color',
    'kind', 'day', 'label', 'market', 'late',
    'edit': how to move it ({'type': 'task', 'task_id', 'start_day'} | {'type': 'step', 'n', 'market', 'step'} |
    {'type': 'live', 'market'}) or None for dates the app works out}], by day."""
    t = day(today) or dt.date.today()
    out = []

    def add(c, kind, d, label, market=None, edit=None, late=False):
        if day(d):
            out.append({'card_id': c['id'], 'title': c['title'], 'color': c.get('color') or 'blue', 'kind': kind,
                        'day': day(d).isoformat(), 'label': label, 'market': market, 'late': bool(late), 'edit': edit})
    for c in cards or ():
        for task in c.get('tasks') or ():
            if not task.get('done'):
                add(c, 'due', task.get('due_day'), '%s due' % STAGE_NAMES.get(task.get('stage'), task.get('stage')),
                    edit={'type': 'task', 'task_id': task['id'], 'start_day': task.get('start_day')},
                    late=day(task.get('due_day')) < t)
        for ln in c.get('lines') or ():
            for step, d in (ln.get('steps') or {}).items():
                add(c, 'step', d, STEP_NAMES.get(step, step), ln.get('market'),
                    {'type': 'step', 'n': ln.get('n'), 'market': ln.get('market'), 'step': step})
            if ln.get('eta') and not (ln.get('steps') or {}).get('arrived'):
                add(c, 'eta', ln['eta'], TIMELINE['eta'], ln.get('market'))
        for mk, d in (c.get('live') or {}).items():
            add(c, 'live', d, TIMELINE['live'], mk, {'type': 'live', 'market': mk})
        for r in c.get('reorder') or ():
            add(c, 'reorder', r.get('day'), TIMELINE['reorder'], r.get('market'), late=day(r.get('day')) <= t)
        for r in c.get('order_by') or ():
            add(c, 'order_by', r.get('day'), TIMELINE['order_by'] + (' (by air)' if r.get('mode') == 'air' else '') +
                (' for the %s peak' % r['peak'] if r.get('peak') else ''), r.get('market'), late=day(r.get('day')) < t)
    out.sort(key=lambda e: (e['day'], e['card_id'], e['kind']))
    return out


def clean_tags(tags):
    """A card's tags: up to 8 short words or phrases, without repeats (case-insensitive)."""
    out, seen = [], set()
    for x in tags or ():
        s = ' '.join(str(x).split())[:24]
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out[:8]
