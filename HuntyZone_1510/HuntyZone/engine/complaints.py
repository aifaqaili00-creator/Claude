"""Customer complaints: group the 1-3 star reviews of a product's best listings into themes ("broke or cracked x9")
and turn them into a short brief for suppliers. Keyword rules in English (reviews in other languages count as
'other'), no AI. Pure functions; JSON-safe output."""
import math
import re

# (key, name, words that show the complaint, what to ask the supplier)
THEMES = [
    ('broke', 'Broke or cracked',
     r"broke|broken|breaks|crack(?:ed|s|ing)?|snapp(?:ed|s)|shatter(?:ed|s)?|fell apart|falling apart|came apart",
     'Ask for thicker or stronger material and reinforced joints, and for a drop test'),
    ('stopped', 'Stopped working',
     r"stopped working|quit working|died|dead on arrival|doesn'?t work|does not work|didn'?t work|did not work|"
     r"not working|defective|faulty|malfunction\w*",
     'Ask for the defect rate, an inspection before shipping and spare units'),
    ('small', 'Too small', r"too small|smaller than|so small|tiny|runs small|too short|too narrow|too thin",
     'Offer a larger size and show exact measurements with something for scale'),
    ('big', 'Too big or heavy', r"too big|too large|bigger than|huge|bulky|too long|too wide|too heavy",
     'Offer a compact size and state measurements and weight clearly'),
    ('quality', 'Cheap or flimsy',
     r"cheap(?:ly)? made|cheaply|flimsy|poor(?:ly)? (?:quality|made)|low quality|bad quality|feels cheap|cheap plastic|plasticky",
     'Upgrade the material and finish; compare samples from two or three suppliers'),
    ('smell', 'Bad smell', r"smell(?:s|y|ed)?|stinks?|odou?r|chemical",
     'Ask for odour-free or food-grade material and its test reports'),
    ('noise', 'Noisy', r"loud|noisy|noise|rattl\w+|squeak\w*|buzz\w*",
     'Ask for quieter parts and the noise level in decibels'),
    ('battery', 'Battery or charging', r"battery|batteries|charg(?:e|es|ed|ing|er)|won'?t (?:turn|power) on",
     'Ask for battery capacity tests, safety certificates and a better charger'),
    ('missing', 'Missing parts', r"missing|not included|wasn'?t included|incomplete",
     'Add a packing checklist and check every carton before it ships'),
    ('setup', 'Hard to set up',
     r"instructions?|manual|confusing|hard to (?:assemble|install|set up|put together)|difficult to (?:assemble|install|set up)",
     'Include clear picture instructions and a QR-code video'),
    ('damaged', 'Arrived damaged', r"arrived (?:damaged|broken)|damaged|dented|scratch(?:ed|es)|torn|packag(?:e|ing)",
     'Use stronger packaging and drop-test the retail box'),
    ('described', 'Not as pictured',
     r"not as (?:described|pictured|shown|advertised)|(?:different|differs) (?:from|than)|misleading|"
     r"looks nothing like|wrong (?:item|colou?r|size)",
     'Use honest photos and make the product match the listing exactly'),
    ('leak', 'Leaks', r"leak(?:s|ed|ing|y)?|spill(?:s|ed)?|drip(?:s|ping)?",
     'Ask for better seals and a leak test'),
    ('clean', 'Stains, rust or peeling', r"hard to clean|difficult to clean|stain(?:s|ed)?|mou?ldy?|rust(?:s|ed|y)?|peel(?:s|ed|ing)?",
     'Use rust-proof, easy-clean material and a coating that does not peel'),
    ('weak', 'Weak hold or power',
     r"weak|no suction|(?:doesn'?t|won'?t|does not) (?:stick|hold)|falls? off|fell off|slipp(?:ery|ed|ing)|not strong|not powerful",
     'Ask for stronger adhesive, suction or motor, and test how much it holds'),
    ('heat', 'Overheats', r"overheat\w*|gets? (?:very |too )?hot|too hot|melt(?:s|ed|ing)?",
     'Ask for heat tests and electrical safety certificates'),
    ('comfort', 'Uncomfortable', r"uncomfortable|not comfortable|itch(?:y|es)?|scratchy|stiff|hurts?|painful",
     'Use softer material and test the fit on real users'),
]
_RX = [(k, name, re.compile(r'\b(?:%s)\b' % words, re.I), ask) for k, name, words, ask in THEMES]
QUOTE_LEN = 140


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _quote(text, rx):
    """The sentence of text where the complaint shows, cut to QUOTE_LEN characters, or None."""
    for s in re.split(r'(?<=[.!?])\s+|\n+', text or ''):
        if rx.search(s):
            s = ' '.join(s.split())
            return s if len(s) <= QUOTE_LEN else s[:QUOTE_LEN - 1].rstrip() + '\u2026'
    return None


def themes(reviews, top=6):
    """The commonest complaints among critical reviews [{'stars', 'title', 'body', 'helpful', 'asin'}]: each review
    counts once per theme, 1-2 star and helpful reviews weigh more. Returns {'themes': [{'key', 'name', 'n', 'share',
    'listings', 'quotes' (up to 2), 'ask'}] best first (at most `top`), 'n': reviews read, 'other': reviews that fit no
    theme}."""
    rows = [r for r in reviews or () if isinstance(r, dict)]
    found, matched = {}, set()
    order = sorted(range(len(rows)), key=lambda i: -(_num(rows[i].get('helpful')) or 0))     # quotes from the most helpful
    for i in order:
        r = rows[i]
        text = '%s. %s' % (r.get('title') or '', r.get('body') or '')
        stars, helpful = _num(r.get('stars')), _num(r.get('helpful')) or 0
        weight = (1.5 if stars is not None and stars <= 2 else 1.0) * (1 + math.log1p(max(helpful, 0)) / 2)
        for key, name, rx, ask in _RX:
            if not rx.search(text):
                continue
            matched.add(i)
            t = found.setdefault(key, {'key': key, 'name': name, 'n': 0, 'weight': 0.0, 'asins': set(), 'quotes': [],
                                       'ask': ask})
            t['n'] += 1
            t['weight'] += weight
            if r.get('asin'):
                t['asins'].add(r['asin'])
            q = _quote(r.get('body'), rx) or _quote(r.get('title'), rx)
            if q and len(t['quotes']) < 2 and q not in t['quotes']:
                t['quotes'].append(q)
    best = sorted(found.values(), key=lambda t: (-t['weight'], -t['n'], t['key']))[:top]
    out = [{'key': t['key'], 'name': t['name'], 'n': t['n'], 'share': round(t['n'] / len(rows), 3),
            'listings': len(t['asins']), 'quotes': t['quotes'], 'ask': t['ask']} for t in best]
    return {'themes': out, 'n': len(rows), 'other': len(rows) - len(matched)}


def brief(found, subject, listings=None):
    """A plain-text brief for suppliers from themes(): what buyers complain about and what to ask for."""
    t = (found or {}).get('themes') or []
    if not t:
        return ''
    head = 'Supplier brief for "%s": what buyers %scomplain about (%d critical review%s)' % (
        subject, 'of the %d best-selling listings ' % listings if listings else '', found['n'], '' if found['n'] == 1 else 's')
    lines = [head, '']
    for n, x in enumerate(t, 1):
        lines.append('%d. %s: %d review%s. %s.' % (n, x['name'], x['n'], '' if x['n'] == 1 else 's', x['ask']))
        lines += ['   "%s"' % q for q in x['quotes'][:1]]
    return '\n'.join(lines)
