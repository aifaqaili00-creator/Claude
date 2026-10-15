"""Product discovery: is a product climbing Amazon's Movers & Shakers or New Releases the right trend product to sell?

One explainable score for Trend radar and Product ideas, built like engine.scores: a weighted geometric mean of five
signals, each 0..1 with its confidence, its source and a short "why":

  A  Amazon lists   how steadily it stays on and climbs the lists (list presence and surge from engine.surge)
  S  Search trend   Google Trends for its search words in this market (the stored trend score)
  D  Demand         units a month from its "bought in past month" badge, a Helium 10 export or its sales rank
  C  Competition    how many reviews a newcomer has to beat (its review count against the market's target)
  E  Price & fees   what is left of the price after Amazon fees, ads and an assumed 25% for product and freight

A signal without data counts as neutral (0.5) inside the score, but its value stays None, it says "unknown" and it
lowers the confidence, so a gap is never shown as evidence; 'Strong' needs at least three measured signals. Risks (a
climb driven by deals, a search fad, product types with extra rules) take 15% off each. Search words meet stored
Google Trends terms only as whole phrases (singular or plural alike), never by loose similarity.
Pure functions; JSON-safe output.
"""
import functools
import math

import config
import file_rank as fr

from . import profit, scores
from .stats import clip

THRESH = {
    'w': {'A': 0.30, 'S': 0.22, 'D': 0.18, 'C': 0.15, 'E': 0.15},
    'neutral': 0.5,                # a signal without data counts as this inside the score
    'floor': 0.03,                 # geometric-mean floor per signal, as engine.scores
    'a_week_w': 0.6,               # Amazon lists: surge = 0.6 x this week's + 0.4 x the two-week surge, and
    'a_stay_w': 0.5,               # A = 0.5 x surge + 0.5 x staying power (the better of 7- and 14-day list presence)
    'a_scans_high': 5,             # list scans behind the surge for 'high' / 'medium' confidence
    'a_scans_medium': 2,
    'strong_min_known': 3,         # 'Strong' needs at least three measured signals
    'pct_full': 1000.0,            # one list reading only: a 1,000% jump on Movers & Shakers scores full marks,
    'new_rank_span': 100.0,        # and so does #1 of New Releases, falling to 0 at #100
    'demand_div': 3.0,             # demand 0 at the market's sales target / 3 and 1 at 12x that, as engine.scores
    'demand_span': 12.0,
    'reviews_top': 10.0,           # competition 0 at 10x the market's review target
    'left_lo': 0.10,               # price & fees 0 at 10% of the price left and 1 at 35%, as engine.scores' margin
    'left_span': 0.25,
    'assumed_landed': 0.25,        # product and freight assumed at 25% of the price (no supplier cost known)
    'risk_step': 0.15,             # each risk takes 15% off
    'good': 0.67,                  # a signal at or above this reads as good, below 'weak' as bad
    'weak': 0.34,
    'phrase_min_words': 2,         # Google Trends matching: whole phrases of 2-5 search words
    'phrase_max_words': 5,
    'suggest_words': 3,            # the phrase to look up on Google Trends: the first 3 search words
    'prefixes': 3,                 # Amazon suggestions are asked for up to 3 two-word starts of the search words
    'phrase_best_words': 3,        # a 3-word shopper phrase beats a 2-word one; longer ones count as 3
    'cluster_min': 2,              # 2+ similar listings (3 in all) let the group's surge stand in for the product's
}
NAMES = {'A': 'Amazon lists', 'S': 'Search trend', 'D': 'Demand', 'C': 'Competition', 'E': 'Price & fees'}
NOT_RISKS = ('price unknown', 'cheap', 'expensive')
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
_DEMAND_SRC = {'badge': 'its "bought in past month" badge', 'h10': 'Helium 10', 'rank': 'its sales rank',
               'list': 'its sales rank on the list'}


def _finite(x):
    """x as a float, or None for None / NaN / inf / text / bools."""
    if x is None or isinstance(x, bool):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _n(x):
    return '{:,}'.format(int(round(x)))


def _target(market):
    return fr.TARGETS.get(market) or fr.TARGETS['US']


# ---------- search words -> stored Google Trends terms ----------

def _fold(word):
    """Singular and plural alike: 'bags' -> 'bag' ('glass' and 'bus' stay)."""
    return word[:-1] if len(word) > 3 and word.endswith('s') and not word.endswith('ss') else word


def phrase_key(text):
    return ' '.join(_fold(w) for w in config.norm_kw(text).split())


def phrase_variants(search):
    """Whole phrases of a product's search words, longest first, then from the left:
    'bladeless tower fan' -> ['bladeless tower fan', 'bladeless tower', 'tower fan']. A single word only when it is
    the whole phrase."""
    words = config.norm_kw(search).split()[:THRESH['phrase_max_words']]
    out = []
    for n in range(len(words), 0, -1):
        if n < THRESH['phrase_min_words'] and n < len(words):
            break
        for i in range(len(words) - n + 1):
            p = ' '.join(words[i:i + n])
            if p not in out:
                out.append(p)
    return out


def _readable(f):
    return (f.get('status') or 'ok') == 'ok' and _finite(f.get('score')) is not None


def trend_index(features):
    """{phrase key: (term, feature)} from one market's stored Google Trends ({term: feature}). When two terms share a
    key, a readable trend wins, then the newest."""
    best = {}
    for term, f in (features or {}).items():
        k = phrase_key(term)
        if not k or not isinstance(f, dict):
            continue
        rank = (_readable(f), _finite(f.get('computed_at')) or 0)
        if k not in best or rank > best[k][2]:
            best[k] = (term, f, rank)
    return {k: (t, f) for k, (t, f, _) in best.items()}


def match_trend(search, index, phrase=None):
    """The stored Google Trends term for a product: its shopper phrase when stored with a readable trend, else the
    longest whole phrase of its search words with one, else the first stored at all.
    {'term', 'match': 'shopper' | 'exact' (all its search words) | 'part', 'feature'} or None."""
    tries = ([(phrase, 'shopper')] if phrase else []) + [(p, 'exact' if i == 0 else 'part')
                                                         for i, p in enumerate(phrase_variants(search))]
    first = None
    for p, kind in tries:
        hit = (index or {}).get(phrase_key(p))
        if not hit:
            continue
        m = {'term': hit[0], 'match': kind, 'feature': hit[1]}
        if _readable(hit[1]):
            return m
        first = first or m
    return first


def suggest_term(search):
    """What to look up on Google Trends for a product without a stored trend: its first three search words."""
    return ' '.join(config.norm_kw(search).split()[:THRESH['suggest_words']])


# ---------- the phrase shoppers type (Amazon search suggestions) ----------

def phrase_prefixes(search):
    """What to ask Amazon's search suggestions for: two-word starts along the search words (at most 3), or the whole
    phrase when it is shorter: 'bladeless tower fan quiet' -> ['bladeless tower', 'tower fan', 'fan quiet']."""
    words = config.norm_kw(search).split()[:THRESH['phrase_max_words']]
    if len(words) <= 2:
        return [' '.join(words)] if words else []
    return [' '.join(words[i:i + 2]) for i in range(min(THRESH['prefixes'], len(words) - 1))]


def pick_phrase(title, suggestions):
    """The phrase shoppers type for this product: an Amazon suggestion of 2+ words that are all in its title (singular
    or plural alike), 3-word ones first, then the better ranked. None when no suggestion fits."""
    have = {_fold(w) for w in config.norm_kw(title).split()}
    best = None
    for rank, s in enumerate(suggestions or ()):
        words = config.norm_kw(s).split()
        if len(words) < 2 or not all(_fold(w) in have for w in words):
            continue
        key = (min(len(words), THRESH['phrase_best_words']), -rank)
        if best is None or key > best[0]:
            best = (key, ' '.join(words))
    return best[1] if best else None


def autocomplete_knows(phrase, suggestions):
    """True when one of Amazon's suggestions starts with the phrase (singular or plural alike)."""
    want = phrase_key(phrase).split()
    return bool(want) and any(phrase_key(s).split()[:len(want)] == want for s in suggestions or ())


def pick_variants(phrase, suggestions, n=4, max_words=6):
    """Longer versions of a phrase that Amazon suggests (all its words, singular or plural alike, plus at least one
    more, at most max_words), best ranked first and one per distinct phrase. Up to n."""
    want = set(phrase_key(phrase).split())
    out, seen = [], {phrase_key(phrase)}
    for s in suggestions or ():
        key = phrase_key(s)
        words = key.split()
        if not want or key in seen or not want < set(words) or len(words) > max_words:
            continue
        seen.add(key)
        out.append(config.norm_kw(s))
        if len(out) >= n:
            break
    return out


# ---------- the five signals ----------

def _part(key, v, known, conf, src, why, raw=None):
    return {'key': key, 'name': NAMES[key], 'v': round(float(v), 3) if known else None, 'known': bool(known),
            'conf': conf if known else 'low', 'src': src if known else None, 'why': why, 'raw': raw or {},
            '_v': float(v) if known else THRESH['neutral']}


def _unknown(key, why, raw=None):
    return _part(key, THRESH['neutral'], False, 'low', None, why, raw)


def amazon_part(c):
    """A = 0.5 x surge + 0.5 x staying power. surge = 0.6 x this week's + 0.4 x the two-week surge (engine.surge, 0-100);
    staying power = the better of the 7- and 14-day list presence, so a one-day jump scores below a steady climber.
    In a group of similar listings, the group's surge (with 2+ similar) and the days any of them was on the lists count
    when better. Without any list history, one list reading (the Movers & Shakers jump or the New Releases rank)."""
    s7, s14 = _finite(c.get('surge')), _finite(c.get('surge_14d'))
    sim = int(_finite(c.get('similar')) or 0)
    if s7 is not None or s14 is not None:
        wk = THRESH['a_week_w']
        v = (wk * s7 + (1 - wk) * s14 if s7 is not None and s14 is not None else s7 if s7 is not None else s14) / 100.0
        cs = _finite(c.get('cluster_surge'))
        if sim >= THRESH['cluster_min'] and cs is not None:
            v = max(v, clip(cs / 100.0))
        stays = [x for x in (_finite(c.get('persistence')), _finite(c.get('persistence_14d'))) if x is not None]
        g_on, g_read = _finite(c.get('group_on')), _finite(c.get('group_read'))
        if sim and g_on is not None and g_read:
            stays.append(g_on / g_read)
        stay = clip(max(stays)) if stays else None
        if stay is not None:
            v = (1 - THRESH['a_stay_w']) * v + THRESH['a_stay_w'] * stay
        n = int(_finite(c.get('valid_scans')) or 0)
        hits = min(int(_finite(c.get('appearances')) or 0), n)
        streak = int(_finite(c.get('streak_days')) or 0)
        conf = 'high' if n >= THRESH['a_scans_high'] else 'medium' if n >= THRESH['a_scans_medium'] else 'low'
        bits = ['on %d of %d list scans in two weeks' % (hits, n)] if n else []
        if streak >= 2:
            bits.append('%d days running' % streak)
        if c.get('rank_improving'):
            bits.append('sales rank still improving')
        bits.append('surge %d/100 this week' % round(s7 if s7 is not None else s14))
        if sim:
            bits.append('with %d similar listing%s%s' % (sim, '' if sim == 1 else 's', ' on the lists %d of %d days' % (
                g_on, g_read) if g_on is not None and g_read else '') + (
                ' (together %d/100)' % round(cs) if sim >= THRESH['cluster_min'] and cs is not None else ''))
        if c.get('deal_driven'):
            bits.append('mostly while on a deal')
        return _part('A', clip(v), True, conf, 'lists', ', '.join(bits),
                     {'surge': s7, 'surge_14d': s14, 'stay': stay, 'scans': n, 'hits': hits, 'streak': streak,
                      'similar': sim, 'group': [g_on, g_read] if sim and g_read else None})
    pct, nr = _finite(c.get('pct')), _finite(c.get('new_rank'))
    seen = []
    if pct is not None and pct > 0:
        seen.append((clip(math.log10(max(pct, 1.0)) / math.log10(THRESH['pct_full'])), 'sales rank up %s%% in 24 h' % _n(pct)))
    if nr is not None and nr >= 1:
        seen.append((clip(1 - (nr - 1) / THRESH['new_rank_span']), '#%d of Hot New Releases' % nr))
    if seen:
        v, why = max(seen)
        return _part('A', v, True, 'low', 'one reading', why + ' (one list reading, no history yet)' +
                     (', with %d similar listing%s' % (sim, '' if sim == 1 else 's') if sim else ''),
                     {'pct': pct, 'new_rank': int(nr) if nr is not None else None, 'similar': sim})
    return _unknown('A', 'not seen on the lists')


def search_part(trend):
    """S: the stored Google Trends score of the matched term / 100. Confidence follows the trend's data quality; a
    match on part of the search words is at most medium."""
    if not trend:
        return _unknown('S', 'no Google Trends data for its search words yet')
    f = trend.get('feature') or {}
    term = trend.get('term')
    raw = {'term': term, 'match': trend.get('match'), 'label': f.get('label'), 'score': _finite(f.get('score')),
           'status': f.get('status') or 'ok'}
    if f.get('status') == 'low_volume':
        return _unknown('S', 'too few Google searches for "%s" to read a trend' % term, raw)
    if not _readable(f):
        return _unknown('S', 'Google Trends for "%s" could not be read yet' % term, raw)
    q = _finite(f.get('q'))
    conf = scores.confidence(q) if q is not None else 'medium'
    if scores.base_label(f.get('label')) == 'Insufficient':
        conf = 'low'
    if trend.get('match') == 'part':
        conf = scores.confidence(conf, 'medium')
    why = 'Google searches for "%s": %s (%d/100)' % (term, f.get('label') or 'no clear pattern', round(raw['score']))
    return _part('S', clip(raw['score'] / 100.0), True, conf, 'google', why, raw)


def demand_part(units, market):
    """D = clip(ln(units / (target / 3)) / ln 12): 0 at a third of the market's sales target, 1 at 4x the target."""
    u = units if isinstance(units, dict) else {}
    v = _finite(u.get('v'))
    if v is None or v < 0:
        return _unknown('D', 'sales unknown: no badge, Helium 10 export or calibrated sales rank yet')
    T = _target(market)
    base = T['sales'] / THRESH['demand_div']
    d = clip(math.log(v / base) / math.log(THRESH['demand_span'])) if v > 0 else 0.0
    src = u.get('src')
    rank = _finite(u.get('rank'))
    why = 'about %s sold a month (%s%s; target %s+)' % (_n(v), _DEMAND_SRC.get(src, 'estimate'),
                                                       ' #%s' % _n(rank) if rank and src in ('rank', 'list') else '', _n(T['sales']))
    return _part('D', d, True, scores.confidence(u.get('conf')), src, why,
                 {'units': round(v, 1), 'lo': _finite(u.get('lo')), 'hi': _finite(u.get('hi')), 'src': src})


def competition_part(reviews, market):
    """C = 1 - clip(ln(1 + reviews / target) / ln 11): full marks without reviews, 0 at 10x the market's target."""
    r = _finite(reviews)
    if r is None or r < 0:
        return _unknown('C', 'review count unknown')
    T = _target(market)
    rb = clip(math.log1p(r / T['reviews']) / math.log1p(THRESH['reviews_top']))
    why = '%s review%s (a newcomer competes well with %s or fewer)' % (_n(r), '' if round(r) == 1 else 's', _n(T['reviews']))
    return _part('C', 1.0 - rb, True, 'medium', 'listing', why, {'reviews': int(round(r)), 'target': T['reviews']})


def _inp(market, price, category, dims, kg, fba_fee, registered, cogs_usd=0.0):
    inp = {'market': market, 'price': price, 'category': category, 'cogs_usd': cogs_usd, 'registered': registered}
    if dims:
        inp['dims_cm'] = list(dims)
    if kg:
        inp['kg'] = kg
    if fba_fee is not None:
        inp['h10_fba_fee'] = fba_fee
    return inp


@functools.lru_cache(maxsize=8192)
def _fees(market, price, category, dims, kg, fba_fee, registered, fx, cogs_usd=0.0):
    """(share of the price left, Amazon fees, fee source) from engine.profit, or None. Without a product cost the share
    is what is left before product and freight; with your cost (US$) it is your margin. Cached: the radar asks for
    the same listings on every visit. fx (the fee table's rate) only keys the cache."""
    e = profit.unit_economics(_inp(market, price, category, dims, kg, fba_fee, registered, cogs_usd))
    if not e or not e.get('NetSales'):
        return None
    left = e['Margin'] if cogs_usd else (e['Profit'] + e['Landed']) / e['NetSales']
    return left, e['Referral'] + e['FBA'] + e['Storage'] + e['Placement'] + e['FeeTax'], e.get('fee_source')


@functools.lru_cache(maxsize=8192)
def _max_cost(market, price, category, dims, kg, fba_fee, registered, fx, margin):
    return profit.max_cogs(_inp(market, price, category, dims, kg, fba_fee, registered), margin)


def _size(c):
    """(dims_cm tuple or None, kg or None, Helium 10 FBA fee or None) from a candidate."""
    dims = c.get('dims_cm')
    dims = tuple(float(x) for x in dims) if isinstance(dims, (list, tuple)) and len(dims) == 3 and \
        all((_finite(x) or 0) > 0 for x in dims) else None
    kg = _finite(c.get('kg')) if (_finite(c.get('kg')) or 0) > 0 else None
    return dims, kg, _finite(c.get('fba_fee'))


def max_cost(c, market, margin):
    """The highest product cost (US$ a piece, before freight and duty) that still leaves `margin` of the price at the
    candidate's price, size and fees (engine.profit.max_cogs, sea freight). None without a price, or when even free
    stock cannot leave that margin."""
    c = c if isinstance(c, dict) else {}
    price, m = _finite(c.get('price')), _finite(margin)
    if price is None or price <= 0 or m is None:
        return None
    dims, kg, fee = _size(c)
    x = _max_cost(market, price, str(c.get('category') or ''), dims, kg, fee, bool(c.get('registered')), profit.fx(market),
                  round(m, 3))
    return x if x else None


def economics_part(c, market):
    """E = clip((left - 0.10) / 0.25). left = the share of the price after Amazon fees, ads and returns, less an
    assumed 25% for product and freight; with your supplier price (c['cogs_usd'], US$) it is your real margin after
    freight and duty too. Medium confidence without your price or the size, high with both."""
    price = _finite(c.get('price'))
    if price is None or price <= 0:
        return _unknown('E', 'price unknown')
    dims, kg, fee = _size(c)
    cost = _finite(c.get('cogs_usd'))
    cost = cost if cost is not None and cost > 0 else None
    got = _fees(market, price, str(c.get('category') or ''), dims, kg, fee, bool(c.get('registered')), profit.fx(market),
                cost or 0.0)
    if not got:
        return _unknown('E', 'fees for this price could not be worked out')
    after, fees, src = got
    sized = bool(dims or kg or fee is not None)
    cur = _target(market)['currency']
    raw = {'price': price, 'fees': round(fees, 2), 'sized': sized}
    if cost is not None:
        left = max(0.0, after)
        why = 'your cost US$%.2f leaves about %d%% of the %s %.2f price after Amazon fees (%s %.2f), freight, duty, ' \
              'ads and returns' % (cost, round(100 * after), cur, price, cur, fees)
        raw.update(left_pct=round(left, 3), cost_usd=cost, margin=round(after, 3))
    else:
        left = max(0.0, after - THRESH['assumed_landed'])
        why = 'about %d%% of the %s %.2f price is left after Amazon fees (%s %.2f), ads and an assumed 25%% for product ' \
              'and freight' % (round(100 * left), cur, price, cur, fees)
        raw.update(left_pct=round(left, 3))
    if not sized:
        why += '; size unknown, so a small parcel is assumed'
    return _part('E', clip((left - THRESH['left_lo']) / THRESH['left_span']), True,
                 'high' if cost is not None and sized else 'medium', src, why, raw)


def _risks(c):
    """(risks that take 15% off each, notes that do not)."""
    flags = c.get('flags') if isinstance(c.get('flags'), (list, tuple)) else []
    risks = list(dict.fromkeys(f for f in flags if isinstance(f, str) and f and f not in NOT_RISKS))
    notes = []
    if c.get('deal_driven'):
        risks.append('deal-driven')
    f = (c.get('trend') or {}).get('feature') or {}
    if _readable(f):
        lab = scores.base_label(f.get('label'))
        if lab == 'Fad':
            risks.append('search fad')
        elif lab == 'Declining':
            notes.append('searches declining')
        elif lab == 'Spike (watch)':
            notes.append('search spike: too early to tell')
        season = f.get('seasonality') or {}
        peak = _finite(season.get('peak_month'))
        if lab == 'Seasonal' or 'seasonal' in str(f.get('label') or '').lower() or season.get('reliable'):
            notes.append('seasonal' + (': peaks in %s' % MONTHS[int(peak) - 1] if peak and 1 <= peak <= 12 else ''))
    return risks, notes


def score(c):
    """Opportunity 0-100 for one candidate. c (every key optional):
      market, surge, surge_14d, persistence, persistence_14d, valid_scans, appearances, streak_days, rank_improving,
      deal_driven                       list history (engine.surge features of the last 7 and 14 days)
      pct, new_rank                     one list reading, used only without list history
      trend                             match_trend() result
      units                             est dict {'v', 'lo', 'hi', 'conf', 'src', 'rank'}
      reviews, price, category, fba_fee, dims_cm, kg, registered
      cogs_usd                          your supplier price (US$ a piece): price & fees uses your real margin
      flags                             file_rank.CHECKS names hit by the title
      similar, cluster_surge,           its group of similar listings: how many others, their noisy-or surge (0-100)
      group_on, group_read              and on how many of the days read any of them was on the lists
    Returns {'score', 'label', 'conf', 'known', 'of', 'parts': [{'key', 'name', 'v', 'w', 'known', 'conf', 'src',
    'why', 'raw'}], 'risk': {'n', 'flags', 'factor', 'notes'}, 'why'}. 'v' is None for a signal without data."""
    c = c if isinstance(c, dict) else {}
    market = c.get('market') if c.get('market') in fr.TARGETS else 'US'
    parts = [amazon_part(c), search_part(c.get('trend')), demand_part(c.get('units'), market),
             competition_part(c.get('reviews'), market), economics_part(c, market)]
    for p in parts:
        p['w'] = THRESH['w'][p['key']]
    known = [p for p in parts if p['known']]
    risks, notes = _risks(c)
    factor = max(0.0, 1.0 - THRESH['risk_step'] * len(risks))
    s = None
    if known:
        logs = sum(p['w'] * math.log(max(THRESH['floor'], min(1.0, p['_v']))) for p in parts)
        s = round(float(clip(100.0 * math.exp(logs) * factor, 0.0, 100.0)), 1)
    cap = 'high' if len(known) == len(parts) else 'medium' if len(known) >= THRESH['strong_min_known'] else 'low'
    good = sorted((p for p in known if p['_v'] >= THRESH['good']), key=lambda p: -p['_v'])
    bad = sorted((p for p in known if p['_v'] < THRESH['weak']), key=lambda p: p['_v'])
    why = [p['why'] for p in good + bad]
    if risks:
        why.append('%s (-%d%%)' % (', '.join(risks), round(100 * (1 - factor))))
    why += notes + ['%s unknown' % p['name'].lower() for p in parts if not p['known']]
    label = scores.label_for(s)
    if label == 'Strong' and len(known) < THRESH['strong_min_known']:
        label = 'Worth a look'
        why.append("'Strong' needs at least %d measured signals" % THRESH['strong_min_known'])
    return {'score': s, 'label': label, 'conf': scores.confidence(cap, [p['conf'] for p in known]),
            'known': len(known), 'of': len(parts), 'parts': [{k: v for k, v in p.items() if k != '_v'} for p in parts],
            'risk': {'n': len(risks), 'flags': risks, 'factor': round(factor, 3), 'notes': notes}, 'why': why}
