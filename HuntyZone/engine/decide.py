"""Go or no-go for one keyword in one market, in plain words.

Deal-breakers first: a product that fails one is a no-go whatever its score (a price outside a workable band, a wall
of reviews, too few sales, too little profit, Amazon itself selling the top listings, a restricted category, demand
in one season only). Then the opportunity score and two warnings (much fast-delivery stock, falling searches) give
Go, Maybe or Weak. Something not read is "unknown": never a pass and never a fail; three or more unknown
deal-breakers cap the decision at Maybe.

Every decision carries the rules it was made under and a short fingerprint of them, so one made under older rules
says so. The limits can be changed per market (merge_rules). The idea of hard filters with confidence tiers comes
from Omniscient; this is written for HuntyZone's markets (US$, A$, AED) and its data.
Pure functions; JSON-safe.
"""
import hashlib
import json
import math

MARKETS = ('US', 'AU', 'AE')
CUR = {'US': 'US$', 'AU': 'A$', 'AE': 'AED'}
RESTRICTED = ('medical', 'fits another brand', 'consumable/chemical', 'electrical', 'kids/toy')
RULES = {
    'price_band': {'kind': 'hard', 'name': 'Price in a workable band',
                   'limit': {'US': [15, 70], 'AU': [20, 110], 'AE': [55, 260]},
                   'means': 'The median page-1 price. Below the band Amazon\'s fees eat the sale; above it buyers '
                            'hesitate and every unit ties up more money.'},
    'review_wall': {'kind': 'hard', 'name': 'Reviews to beat',
                    'limit': {'US': 2000, 'AU': 800, 'AE': 500},
                    'means': 'The median reviews of the top 10 listings. Above this a new listing rarely reaches page 1.'},
    'demand_floor': {'kind': 'hard', 'name': 'Sales on page 1 a month',
                     'limit': {'US': 1500, 'AU': 300, 'AE': 200},
                     'means': 'The "bought in past month" badges on page 1 added up (a floor: listings without a badge sell '
                              'too). Below this the niche is small.'},
    'margin_floor': {'kind': 'hard', 'name': 'Profit margin after fees, freight and ads',
                     'limit': {'US': 0.15, 'AU': 0.15, 'AE': 0.15},
                     'means': 'With your supplier price, ads at 12% of the price. Without your price only a price that '
                              'leaves too little after Amazon\'s fees fails.'},
    'amazon_sells': {'kind': 'hard', 'name': 'Amazon itself sells few of the top listings',
                     'limit': {'US': 0.3, 'AU': 0.3, 'AE': 0.3},
                     'means': 'The share of the top listings read whose seller is Amazon: Amazon usually wins the buy box.'},
    'restricted': {'kind': 'hard', 'name': 'No restricted or risky category',
                   'limit': {mk: ['medical', 'fits another brand'] for mk in MARKETS},
                   'means': 'Words in the top 10 titles that usually need approval or certificates, or invite brand '
                            'complaints. Pick which ones stop you.'},
    'one_season': {'kind': 'hard', 'name': 'Sells all year',
                   'limit': {mk: 0.6 for mk in MARKETS},
                   'means': 'How seasonal Google searches are (0 = the same all year, 1 = one season only), from Google '
                            'Trends; counted only when the pattern repeats every year.'},
    'fast_stock': {'kind': 'soft', 'name': 'Not much fast-delivery stock',
                   'limit': {'US': 40, 'AU': 20, 'AE': 15},
                   'means': 'Listings on page 1 that arrive within your fast-delivery days: many mean buyers are already '
                            'served from local stock.'},
    'searches_falling': {'kind': 'soft', 'name': 'Searches not falling',
                         'limit': {mk: ['Declining', 'Fad'] for mk in MARKETS},
                         'means': 'The Google Trends label of the keyword.'},
}
TIERS = {'go': ('Go', 'good'), 'maybe': ('Maybe', 'warn'), 'weak': ('Weak', 'warn'), 'no_go': ('No-go', 'bad'),
         'unknown': ('Not searched', 'weak')}
SCORE_GO, SCORE_MAYBE = 60, 40
MAX_UNKNOWN = 2                     # more unknown deal-breakers than this: at most Maybe


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _clean_limit(key, mk, v):
    """A typed limit -> a valid one, or None (the default stays)."""
    base = RULES[key]['limit'][mk]
    if isinstance(base, list) and base and isinstance(base[0], str):
        allowed = RESTRICTED if key == 'restricted' else ('Declining', 'Fad', 'Spike (watch)', 'Seasonal', 'Mixed')
        return [x for x in dict.fromkeys(v) if x in allowed] if isinstance(v, list) else None
    if isinstance(base, list):
        if not (isinstance(v, (list, tuple)) and len(v) == 2):
            return None
        lo, hi = _num(v[0]), _num(v[1])
        return [round(max(0.0, lo), 2), round(min(1e6, hi), 2)] if lo is not None and hi is not None and lo < hi else None
    n = _num(v)
    if n is None:
        return None
    if key in ('margin_floor', 'amazon_sells', 'one_season'):
        return round(max(0.0, min(1.0, n)), 3)
    return round(max(0.0, min(1e6, n)))


def merge_rules(saved=None):
    """Your changes on top of the defaults: {key: {'on': bool, 'limit': {market: value}}} -> full, valid rules."""
    saved = saved if isinstance(saved, dict) else {}
    out = {}
    for key, r in RULES.items():
        mine = saved.get(key) if isinstance(saved.get(key), dict) else {}
        lim = dict(r['limit'])
        for mk, v in (mine.get('limit') or {}).items():
            if mk in lim:
                c = _clean_limit(key, mk, v)
                if c is not None:
                    lim[mk] = c
        out[key] = {'on': bool(mine.get('on', True)), 'kind': r['kind'], 'name': r['name'], 'means': r['means'],
                    'limit': lim}
    return out


def changes(rules):
    """Only what differs from the defaults (what is saved)."""
    out = {}
    for key, r in rules.items():
        d = {}
        if not r['on']:
            d['on'] = False
        lim = {mk: v for mk, v in r['limit'].items() if v != RULES[key]['limit'][mk]}
        if lim:
            d['limit'] = lim
        if d:
            out[key] = d
    return out


def used(rules, market):
    """The rules as they apply to one market, and their fingerprint."""
    u = {k: {'on': r['on'], 'limit': r['limit'][market]} for k, r in rules.items()}
    return u, hashlib.sha1(json.dumps(u, sort_keys=True).encode()).hexdigest()[:8]


def _money(v, mk):
    return '%s %s' % (CUR.get(mk, ''), '{:,.0f}'.format(v) if v >= 100 else '{:,.2f}'.format(v).rstrip('0').rstrip('.'))


def _pct(v):
    return '%d%%' % round(100 * v)


def check(key, lim, f, mk):
    """One rule -> {'state': 'pass'|'fail'|'unknown', 'value', 'why', 'fix'}."""
    if key == 'price_band':
        p = _num(f.get('price'))
        if p is None:
            return 'unknown', None, 'No prices on page 1 were read.', 'Search the keyword again.'
        lo, hi = lim
        if p < lo:
            return 'fail', p, 'The usual price, %s, is under %s: Amazon\'s fees take too much of each sale.' % (
                _money(p, mk), _money(lo, mk)), 'Look for a bundle or a premium version that sells higher.'
        if p > hi:
            return 'fail', p, 'The usual price, %s, is over %s: slower sales and more money tied up per unit.' % (
                _money(p, mk), _money(hi, mk)), 'Check that you can fund the stock, or raise the limit.'
        return 'pass', p, 'The usual price is %s (band %s to %s).' % (_money(p, mk), _money(lo, mk), _money(hi, mk)), None
    if key == 'review_wall':
        r = _num(f.get('review_barrier'))
        if r is None:
            return 'unknown', None, 'The reviews of the top listings were not read.', 'Search the keyword again.'
        if r > lim:
            return 'fail', r, 'The top 10 listings have about %s reviews each (limit %s).' % (
                '{:,.0f}'.format(r), '{:,.0f}'.format(lim)), 'Look for a narrower phrase with newer listings on page 1.'
        return 'pass', r, 'About %s reviews to beat (limit %s).' % ('{:,.0f}'.format(r), '{:,.0f}'.format(lim)), None
    if key == 'demand_floor':
        u = _num(f.get('page1_units'))
        if u is None:
            return 'unknown', None, 'No listing on page 1 shows a "bought in past month" badge, so sales are unknown.', \
                'Import a Helium 10 Xray for this keyword to see sales.'
        if u < lim:
            return 'fail', u, 'Page 1 sells at least %s a month by its badges (floor %s).' % (
                '{:,.0f}'.format(u), '{:,.0f}'.format(lim)), 'A small niche: look at the wider keyword.'
        return 'pass', u, 'Page 1 sells at least %s a month (floor %s).' % ('{:,.0f}'.format(u), '{:,.0f}'.format(lim)), None
    if key == 'margin_floor':
        m, after = _num(f.get('margin')), _num(f.get('after_amazon'))
        if m is not None:
            if m < lim:
                return 'fail', m, 'With your product cost the margin is %s (floor %s).' % (_pct(m), _pct(lim)), \
                    'Find a cheaper supplier, ship by sea, or check a higher price.'
            return 'pass', m, 'With your product cost the margin is %s (floor %s).' % (_pct(m), _pct(lim)), None
        if after is not None and after < lim + 0.15:
            return 'fail', after, ('After Amazon\'s fees and ads only %s of the price is left for the product, freight '
                                   'and profit.' % _pct(max(0.0, after))), 'Only a much higher price would work.'
        return 'unknown', after, 'Type your supplier price in the profit calculator to check the margin.', \
            'Ask a supplier for a price.'
    if key == 'amazon_sells':
        s, n = _num(f.get('amazon_share')), int(f.get('amazon_read') or 0)
        if s is None or n < 5:
            return 'unknown', None, 'Fewer than 5 of the top listings\' sellers were read.', \
                'Press Read the top listings under Who sells it.'
        if s > lim:
            return 'fail', s, 'Amazon itself sells %s of the %d top listings read (limit %s).' % (_pct(s), n, _pct(lim)), \
                'Pick a version Amazon does not stock.'
        return 'pass', s, 'Amazon sells %s of the %d top listings read.' % (_pct(s), n), None
    if key == 'restricted':
        hit = [x for x in f.get('risk_flags') or [] if x in lim]
        if f.get('risk_flags') is None:
            return 'unknown', None, 'No titles were read.', 'Search the keyword again.'
        if hit:
            return 'fail', hit, 'The top titles point to %s.' % ' and '.join(hit), \
                'Check Amazon\'s category approval and the certificates you need first.'
        other = [x for x in f.get('risk_flags') or [] if x not in lim]
        return 'pass', other, ('Watch: %s.' % ', '.join(other)) if other else 'No restricted words in the top titles.', None
    if key == 'one_season':
        s = f.get('season') or {}
        st = _num(s.get('strength'))
        if st is None or not f.get('season_read'):
            return 'unknown', None, 'Google Trends was not read for this keyword.', 'Fetch Google Trends in the report.'
        if s.get('reliable') and st >= lim:
            return 'fail', st, 'Searches come in one season (strength %.2f, peak in %s).' % (st, s.get('peak') or '?'), \
                'Only with a launch planned for the season, and stock sold by its end.'
        return 'pass', st, 'Searches are spread over the year (seasonality %.2f).' % st, None
    if key == 'fast_stock':
        n = f.get('fast')
        if n is None:
            return 'unknown', None, 'Delivery times on page 1 were not read.', 'Search the keyword again.'
        if n > lim:
            return 'fail', n, '%d listings on page 1 deliver fast (warning above %d).' % (n, lim), \
                'You need FBA stock in the country from day one.'
        return 'pass', n, '%d listings on page 1 deliver fast.' % n, None
    if key == 'searches_falling':
        lab = f.get('trend')
        if not lab or lab == 'Insufficient':
            return 'unknown', None, 'Google Trends was not read or has too little data.', 'Fetch Google Trends in the report.'
        if lab in lim:
            return 'fail', lab, 'Google searches are "%s".' % lab, 'Check the 5-year chart before ordering.'
        return 'pass', lab, 'Google searches are "%s".' % lab, None
    raise KeyError(key)


def decide(facts, rules=None):
    """facts: {'market', 'searched', 'price', 'review_barrier', 'page1_units', 'margin' (with your cost) or
    'after_amazon' (share of the price left after Amazon's fees and ads), 'amazon_share', 'amazon_read', 'risk_flags',
    'season' {'strength', 'reliable', 'peak'}, 'season_read', 'fast', 'trend', 'score', 'score_label'}.
    -> {'tier', 'label', 'tone', 'why', 'confidence', 'rules': [...], 'used', 'fingerprint'}."""
    f = facts or {}
    mk = f.get('market') if f.get('market') in MARKETS else 'US'
    rules = rules or merge_rules()
    u, fp = used(rules, mk)
    if not f.get('searched'):
        label, tone = TIERS['unknown']
        return {'tier': 'unknown', 'label': label, 'tone': tone, 'why': 'Search this keyword first (Refresh now).',
                'confidence': 'low', 'rules': [], 'used': u, 'fingerprint': fp, 'score': None}
    out = []
    for key, r in rules.items():
        if not r['on']:
            out.append({'key': key, 'name': r['name'], 'kind': r['kind'], 'state': 'off', 'value': None,
                        'limit': r['limit'][mk], 'why': 'Switched off in your rules.', 'fix': None, 'means': r['means']})
            continue
        state, value, why, fix = check(key, r['limit'][mk], f, mk)
        out.append({'key': key, 'name': r['name'], 'kind': r['kind'], 'state': state, 'value': value,
                    'limit': r['limit'][mk], 'why': why, 'fix': fix if state != 'pass' else None, 'means': r['means']})
    hard_fail = [x for x in out if x['kind'] == 'hard' and x['state'] == 'fail']
    soft_fail = [x for x in out if x['kind'] == 'soft' and x['state'] == 'fail']
    unknown = [x for x in out if x['kind'] == 'hard' and x['state'] == 'unknown']
    score = _num(f.get('score'))
    if hard_fail:
        tier = 'no_go'
        why = 'No-go: ' + hard_fail[0]['why'] + (' (and %d more deal-breaker%s)' % (
            len(hard_fail) - 1, '' if len(hard_fail) == 2 else 's') if len(hard_fail) > 1 else '')
    else:
        order = ['go', 'maybe', 'weak']
        i = 0 if score is not None and score >= SCORE_GO else 1 if score is None or score >= SCORE_MAYBE else 2
        i = min(2, i + len(soft_fail))
        if len(unknown) > MAX_UNKNOWN:
            i = max(i, 1)
        tier = order[i]
        bits = ['no deal-breaker failed']
        if score is not None:
            bits.append('opportunity %d/100%s' % (round(score), ' (%s)' % f['score_label'] if f.get('score_label') else ''))
        if soft_fail:
            bits.append('warning: ' + '; '.join(x['why'] for x in soft_fail))
        if unknown:
            bits.append('%d not read yet (%s)' % (len(unknown), ', '.join(x['name'].lower() for x in unknown)))
        why = '%s: %s.' % (TIERS[tier][0], ', '.join(bits))
    conf = 'high' if not unknown else 'medium' if len(unknown) <= MAX_UNKNOWN else 'low'
    label, tone = TIERS[tier]
    return {'tier': tier, 'label': label, 'tone': tone, 'why': why, 'confidence': conf, 'rules': out, 'used': u,
            'fingerprint': fp, 'score': round(score) if score is not None else None}
