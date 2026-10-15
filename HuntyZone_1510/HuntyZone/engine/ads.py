"""Ad watch: who buys the sponsored places on a keyword's page 1, as one ad-pressure score with its reasons.

Inputs are what HuntyZone already reads: the result cards (each marked sponsored or not) and, for searches saved
since schema 7, the ads that are not result cards (a brand banner at the top, video ads, sponsored carousels).
A brand comes from the listing (product page or Helium 10), else from the title's first word ("from the title").
A search saved before ads were read scores on its result cards only, with low confidence. Pure functions.
"""
import re

THRESH = {
    'top': 20,               # the ad share is measured on the first 20 places
    'share_full': 0.4,       # 40% of them sponsored scores full marks
    'brands_full': 6,        # six advertising brands score full marks
    'videos_full': 2,        # and two video ads
    'w': {'share': 0.40, 'banner': 0.20, 'video': 0.15, 'brands': 0.25},
    'high': 67, 'medium': 34,
}
STOP = {'the', 'a', 'an', 'new', 'original', 'premium', 'genuine', 'sponsored'}
FORMAT = {'listing': 'sponsored listing', 'brand_banner': 'brand banner', 'video': 'video ad', 'carousel': 'sponsored carousel'}


def brand_guess(title):
    """The title's first word (titles start with the brand), skipping words like "The" or "New"."""
    words = re.findall(r"[A-Za-z0-9][\w'&+.-]*", title or '')
    while words and words[0].lower() in STOP:
        words = words[1:]
    return words[0].rstrip('.') if words else None


def brand_of(asin, title, known=None, names=(), avoid=()):
    """(brand, 'listing' | 'title'), or (None, None). The listing's own brand first; else a brand already known in the
    market that the title starts with (longest first: "Cold Power" before "Cold"); else the title's first word, unless
    that word is in `avoid` (the keyword's own words are never a brand)."""
    b = (known or {}).get(asin)
    if b:
        return b, 'listing'
    t = ' '.join((title or '').split()).lower()
    for n in names or ():
        if t == n.lower() or t.startswith(n.lower() + ' '):
            return n, 'listing'
    g = brand_guess(title)
    return (g, 'title') if g and g.lower() not in avoid else (None, None)


def brand_names(names):
    """Known brand names, longest first, for brand_of()."""
    return sorted({n.strip() for n in names or () if n and len(n.strip()) > 1}, key=lambda n: (-len(n), n.lower()))


def avoid_words(kw):
    return {w for w in re.findall(r'[a-z0-9]+', (kw or '').lower())} | {w + 's' for w in re.findall(r'[a-z0-9]+', (kw or '').lower())}


def _clip(x):
    return max(0.0, min(1.0, x))


def ad_pressure(rows, ads=None, brands=None, names=(), kw=''):
    """rows: page 1 in order [{'asin', 'title', 'sponsored'}]; ads: amazon_check.parse_ads output, or None when the
    search was saved before ads were read; brands: {asin: brand} known from listings; names: brand names known in the
    market (brand_names()); kw: the keyword (its words are never taken for a brand).
    Returns {'score' 0-100 (None without rows), 'label', 'conf', 'parts': [{'key', 'label', 'value', 'why'}],
    'brands': [{'brand', 'slots', 'formats', 'src'}], 'unbranded', 'why'}."""
    rows = [r for r in rows or [] if r.get('asin')]
    if not rows:
        return {'score': None, 'label': 'Not read', 'conf': 'low', 'parts': [], 'brands': [], 'unbranded': 0,
                'why': 'no search read'}
    top = rows[:THRESH['top']]
    sp = [r for r in top if r.get('sponsored')]
    found, avoid, lost = {}, avoid_words(kw), [0]

    def add(brand, src, fmt):
        if not brand:
            lost[0] += 1
            return
        k = brand.lower()
        b = found.setdefault(k, {'brand': brand, 'slots': 0, 'formats': [], 'src': src})
        b['slots'] += 1
        if fmt not in b['formats']:
            b['formats'].append(fmt)
        if src == 'listing':
            b['src'] = 'listing'
    for r in [r for r in rows if r.get('sponsored')]:
        add(*brand_of(r['asin'], r.get('title'), brands, names, avoid), FORMAT['listing'])
    for a in ads or []:
        if a['kind'] == 'brand_banner':
            b = a.get('brand') or (brand_of(a['asins'][0], (a.get('titles') or [''])[0], brands, names, avoid)[0]
                                   if a.get('asins') else None)
            add(b, 'listing' if a.get('brand') else 'title', FORMAT['brand_banner'])
            continue
        titles = a.get('titles') or []
        for i, asin in enumerate(a.get('asins') or [None] * len(titles)):
            add(*brand_of(asin, titles[i] if i < len(titles) else '', brands, names, avoid), FORMAT[a['kind']])
    parts = [{'key': 'share', 'label': 'Sponsored places', 'value': _clip(len(sp) / len(top) / THRESH['share_full']),
              'why': '%d of the first %d places are ads' % (len(sp), len(top))}]
    if ads is not None:
        banner = [a for a in ads if a['kind'] == 'brand_banner']
        videos = sum(1 for a in ads if a['kind'] == 'video')
        parts.append({'key': 'banner', 'label': 'Brand banner', 'value': 1.0 if banner else 0.0,
                      'why': 'a brand banner at the top%s' % (' (%s)' % banner[0]['brand'] if banner[0].get('brand') else '')
                      if banner else 'no brand banner at the top'})
        parts.append({'key': 'video', 'label': 'Video ads', 'value': _clip(videos / THRESH['videos_full']),
                      'why': '%d video ad%s' % (videos, '' if videos == 1 else 's')})
    parts.append({'key': 'brands', 'label': 'Brands advertising', 'value': _clip(len(found) / THRESH['brands_full']),
                  'why': '%d brand%s advertise%s' % (len(found), '' if len(found) == 1 else 's', 's' if len(found) == 1 else '')})
    w = THRESH['w']
    score = round(100 * sum(w[p['key']] * p['value'] for p in parts) / sum(w[p['key']] for p in parts))
    for p in parts:
        p['value'] = round(p['value'], 2)
    label = 'High' if score >= THRESH['high'] else 'Medium' if score >= THRESH['medium'] else 'Low'
    out = sorted(found.values(), key=lambda b: (-b['slots'], b['brand'].lower()))
    return {'score': score, 'label': label, 'conf': 'medium' if ads is not None else 'low', 'parts': parts,
            'brands': out, 'unbranded': lost[0], 'why': '; '.join(p['why'] for p in parts) + ('' if ads is not None else
                                                                      ' (banner and video ads not read in this older search)')}
