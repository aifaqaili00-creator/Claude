"""Launch & strategy hints for one product in one market, from the app's research: price band, profit at your real
landed cost, reviews to beat, competition, complaints to fix, shopper phrases, timing, a first sales target and the
search trend. Each hint says when to check it again, and moved() says when its numbers changed enough to look at it
again. Pure functions; JSON-safe output."""
import math

MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
RECHECK = {'price': 14, 'competition': 14, 'trend': 14, 'search': 1}
RECHECK_DEFAULT = 30
MOVED = {'price': 0.10, 'reviews': 0.25, 'competition': 2, 'target': 0.30, 'profit': 0.05}
TREND = {
    'Breakout': 'Google searches are breaking out: move fast.',
    'Emerging': 'Google searches are starting to rise.',
    'Growing': 'Google searches keep growing.',
    'Seasonal': 'Google searches follow the same pattern every year: time the stock for the season.',
    'Evergreen': 'Google searches are steady all year.',
    'Declining': 'Google searches are falling: keep the first order small.',
    'Fad': 'Google searches look like a fad: keep the first order small.',
    'Spike (watch)': 'Google searches jumped recently: watch whether it lasts.',
    'Mixed': 'Google searches go up and down.',
}


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _n(v):
    return '{:,.0f}'.format(v)


def _m(cur, v):
    """'A$36', 'A$9.10', 'AED 120'."""
    s = '{:,.0f}'.format(v) if abs(v) >= 100 or float(v).is_integer() else '{:,.2f}'.format(v)
    return cur + ('' if cur.endswith('$') or not cur else ' ') + s


def _d(iso):
    """'2026-10-01' -> '1 Oct'."""
    try:
        return '%d %s' % (int(iso[8:10]), MONTHS[int(iso[5:7]) - 1])
    except (TypeError, ValueError, IndexError):
        return str(iso)


def _h(key, cat, text, data):
    return {'key': key, 'cat': cat, 'text': text, 'data': data, 'recheck_days': RECHECK.get(key, RECHECK_DEFAULT)}


def make(r):
    """The hints for one market from research r: {'market', 'market_name', 'kw', 'searched', 'cur', 'price', 'p25',
    'p75', 'econ' (engine.profit.unit_economics at your landed cost, or None), 'review_barrier', 'fast_local',
    'takeaway' (who sells, one line), 'themes' (engine.complaints), 'phrases', 'plan' (engine.planner.plan or None),
    'newcomer' ({'v', 'lo', 'hi'} or None), 'trend' ({'label'} or None)}. Unknowns are left out."""
    name = r.get('market_name') or r.get('market')
    if not r.get('searched'):
        return [_h('search', 'search', 'Not searched in %s yet: the app searches "%s" there within a day and then fills '
                   'in these hints.' % (name, r.get('kw') or ''), {})]
    cur, out = r.get('cur') or '', []
    m = lambda v: _m(cur, v)                                                    # noqa: E731
    p, lo, hi = _num(r.get('price')), _num(r.get('p25')), _num(r.get('p75'))
    if p:
        text = ('Price it between %s and %s: the middle of page 1 is %s.' % (m(lo), m(hi), m(p)) if lo and hi and lo < hi
                else 'The middle of page 1 is %s.' % m(p))
        out.append(_h('price', 'price', text, {'price': p, 'lo': lo, 'hi': hi}))
    e = r.get('econ') or {}
    if p and _num(e.get('Profit')) is not None:
        prof, mg, acos = e['Profit'], _num(e.get('Margin')) or 0, _num(e.get('BreakEvenACoS')) or 0
        text = ('At %s you keep about %s a sale (%d%%) after Amazon fees, shipping and duty; ads can take up to %d%% of '
                'sales before a sale loses money.' % (m(p), m(prof), round(mg * 100), round(acos * 100)) if prof > 0
                else 'At %s each sale loses about %s after Amazon fees, shipping and duty: raise the price or lower your '
                'costs.' % (m(p), m(-prof)))
        out.append(_h('profit', 'money', text, {'profit': prof, 'margin': round(mg, 4), 'acos': round(acos, 4)}))
    rb = _num(r.get('review_barrier'))
    if rb is not None:
        text = ('The top listings have about %s reviews: plan how you get your first ones (for example Amazon Vine).' % _n(rb)
                if rb >= 100 else 'The top listings have only about %s reviews: a new listing can catch up quickly.' % _n(rb))
        out.append(_h('reviews', 'reviews', text, {'reviews': rb}))
    fl, take = r.get('fast_local'), r.get('takeaway')
    if fl is not None or take:
        parts = []
        if fl is not None:
            parts.append('%d seller%s deliver%s fast from stock in %s.' % (fl, '' if fl == 1 else 's', 's' if fl == 1 else '',
                                                                          name))
        if take:
            parts.append(take)
        out.append(_h('competition', 'competition', ' '.join(parts), {'fast_local': fl}))
    th = [t for t in r.get('themes') or () if t.get('n')][:3]
    if th:
        out.append(_h('complaints', 'product', 'Buyers complain about: %s. Fix it with your supplier and show it in your '
                      'photos.' % ', '.join('%s (%d%%)' % (t['name'].lower(), round((t.get('share') or 0) * 100)) for t in th),
                      {'top': th[0]['key'], 'asks': [t.get('ask') for t in th]}))
    ph = [x for x in r.get('phrases') or () if x][:5]
    if ph:
        out.append(_h('phrases', 'listing', 'Shoppers search: %s. Use these words in your title and search terms.'
                      % ', '.join('"%s"' % x for x in ph), {'phrases': ph}))
    plan = r.get('plan') or {}
    pk = plan.get('peak_month')
    if plan.get('status') in ('ok', 'too_late', 'next_year') and pk:
        sea, air = plan.get('sea') or {}, plan.get('air') or {}
        text = 'Demand peaks in %s' % MONTHS[int(pk) - 1]
        if plan['status'] == 'ok' and sea.get('in_stock_by'):
            text += ': be in stock by %s (order by %s by sea or %s by air).' % (
                _d(sea['in_stock_by']), _d(sea.get('order_by')), _d(air.get('order_by')))
        else:
            text += '. ' + (plan.get('note') or '').strip()
        out.append(_h('timing', 'timing', text.strip(), {'peak': pk}))
    elif plan.get('status') == 'flat':
        out.append(_h('timing', 'timing', 'No strong season: you can launch any time.', {'peak': None}))
    nw = r.get('newcomer') or {}
    if _num(nw.get('v')):
        out.append(_h('target', 'sales', 'A new seller might sell about %s a month here (%s to %s): a fair first sales '
                      'target.' % (_n(nw['v']), _n(nw.get('lo') or nw['v']), _n(nw.get('hi') or nw['v'])),
                      {'v': round(nw['v'])}))
    label = str((r.get('trend') or {}).get('label') or '').split(',')[0].strip()
    if label in TREND:
        out.append(_h('trend', 'trend', TREND[label], {'label': label}))
    return out


def _rel(a, b):
    a, b = _num(a), _num(b)
    return abs(b - a) / abs(a) if a and b is not None else None


def moved(key, old, new):
    """{'from', 'to'} when a hint's numbers changed enough to check it again, else None."""
    o, n = old or {}, new or {}
    pairs = {'price': 'price', 'reviews': 'reviews', 'target': 'v'}
    if key in pairs:
        f = pairs[key]
        ch = _rel(o.get(f), n.get(f))
        return {'from': o.get(f), 'to': n.get(f)} if ch is not None and ch >= MOVED[key] else None
    if key == 'profit':
        a, b = _num(o.get('margin')), _num(n.get('margin'))
        return {'from': a, 'to': b} if a is not None and b is not None and abs(b - a) >= MOVED['profit'] else None
    if key == 'competition':
        a, b = o.get('fast_local'), n.get('fast_local')
        return {'from': a, 'to': b} if a is not None and b is not None and abs(b - a) >= MOVED['competition'] else None
    f = {'complaints': 'top', 'timing': 'peak', 'trend': 'label'}.get(key)
    if f and o.get(f) is not None and n.get(f) is not None and o.get(f) != n.get(f):
        return {'from': o.get(f), 'to': n.get(f)}
    return None
