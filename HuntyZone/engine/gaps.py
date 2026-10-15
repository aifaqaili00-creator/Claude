"""Global Gap Finder: products proven in big Amazon markets (USA, UK, Germany) that are open in Australia or the UAE.

- proof(): does a product sell well in each proof market (Best Sellers, "bought in past month" badges, Helium 10)?
  Proven = selling well in at least two of them. Movers & Shakers alone only says "rising".
- group(): the same product across markets (same ASIN, else the same brand with nearly the same title).
- generic_phrase(): what shoppers type for it, never with the brand name.
- classify(): the gap in one target market, from the same ASIN there and page 1 of the generic phrase.
- flags(): warnings shown next to a gap (chemical / dangerous goods, electrical, kids, medical), each with its reason.
- second_seller(): a watched lone exporter got company.
A market that could not be read is a gap in the data ('unread'), never "missing". Pure functions; JSON-safe output.
"""
import re

import config
import countries
import file_rank as fr

from . import badge, discovery

TYPES = (
    ('lone_abroad', 'Lone exporter, ships from abroad', 'One seller based abroad ships it in slowly, and nobody else sells it.'),
    ('lone_fba', 'Lone exporter, local FBA', "One foreign seller keeps stock in Amazon's local warehouse, with no rivals."),
    ('amazon_only', 'Amazon only', 'Amazon itself sells it, and no local third-party seller does.'),
    ('imported', 'Imported only', 'Every seller ships it from abroad (Global Store or overseas sellers).'),
    ('missing', 'Missing', 'Nobody sells this product there.'),
    ('few', 'Few local rivals', '1 to 4 local sellers offer the same product.'),
    ('crowded', 'Crowded', '5 or more local sellers offer the same product.'),
    ('unread', 'Not read', 'Amazon could not be read there (blocked or paused): a gap in the data, not in the market.'),
)
LABEL = {k: v for k, v, _ in TYPES}
MEANS = {k: m for k, _, m in TYPES}
ORDER = {k: i for i, (k, _, _) in enumerate(TYPES)}
GAPS = ('lone_abroad', 'lone_fba', 'amazon_only', 'imported', 'missing')
MARKET_NAMES = {'AU': 'Australia', 'AE': 'UAE', 'US': 'USA', 'GB': 'UK', 'DE': 'Germany'}
THRESH = {
    'crowded_min': 5,              # local sellers of the same product from which a market is crowded
    'proven_min': 2,               # proof markets a product must sell well in
    'best_rank_max': 100,          # a Best Sellers top 100 counts as selling well
    'h10_error': 0.25,             # Helium 10 estimates are shown with a +-25% range
    'title_alike': 0.6,            # titles this alike (search words) are the same product, with the same brand
}
DG = re.compile(r'\b(aerosols?|(?<!empty )sprays?(?! bottles?)|(?:stain|rust|nail polish|polish|paint|adhesive|glue|odou?r|'
                r'grease|limescale|mou?ld|ink|wax) removers?|bleach|solvents?|flammable|lithium|butane|propane|lighter fluid|'
                r'fuel|acetone|nail polish|perfumes?|fragrances?|alcohol|isopropyl|corrosive|oxidi[sz]ers?|chlorine|'
                r'pesticides?|insecticides?|herbicides?|fertili[sz]ers?|paints?|varnish|glue|adhesives?|epoxy|resin|'
                r'lubricants?|degreasers?|(?<!vacuum )(?<!pipe )cleaners?|detergents?|disinfectants?|sanitis\w*|sanitiz\w*|'
                r'essential oils?|batteries|battery)\b', re.I)
DG_FORMS = re.compile(r'spray|aerosol|liquid|gel|oil|powder', re.I)
DG_ATTRS = ('supplier_declared_dg_hz_regulation', 'hazmat', 'ghs', 'safety_data_sheet_url', 'is_hazmat')
FLAG_CHECKS = {'electrical': 'electrical', 'kids': 'kids/toy', 'medical': 'medical'}
FLAG_LABELS = {'dg': 'chemical / dangerous goods', 'electrical': 'electrical', 'kids': 'kids', 'medical': 'medical'}


def _n(x):
    return '{:,}'.format(int(round(x)))


# ---------- proof: does it sell well? ----------
def selling_well(evidence, market, min_units):
    """One proof market. evidence: [{'kind': 'best', 'rank', 'category'} | {'kind': 'movers', 'pct', 'rank', 'rank_now',
    'rank_before', 'category'} | {'kind': 'badge', 'low'} | {'kind': 'h10', 'units'} | {'kind': 'curve', 'units': est}].
    Returns {'well': True / False / None (no data), 'units': an estimate {'v', 'lo', 'hi', 'basis', 'conf'} or None, 'src',
    'why'}. Helium 10 wins, then the badge (Amazon's own floor), then the sales-rank curve. A Movers & Shakers product
    whose Best Sellers rank is now in the top 100 of its category sells well, like a Best Sellers top 100."""
    best = [e for e in evidence or () if e.get('kind') == 'best' and e.get('rank')]
    movers = [e for e in evidence or () if e.get('kind') == 'movers']
    badges = [e['low'] for e in evidence or () if e.get('kind') == 'badge' and e.get('low')]
    h10 = [e['units'] for e in evidence or () if e.get('kind') == 'h10' and e.get('units')]
    curve = [e['units'] for e in evidence or () if e.get('kind') == 'curve' and (e.get('units') or {}).get('v') is not None]
    units, src = None, None
    if h10:
        v = max(h10)
        units, src = {'v': round(v), 'lo': round(v * (1 - THRESH['h10_error'])), 'hi': round(v * (1 + THRESH['h10_error'])),
                      'basis': 'reported', 'conf': 'medium'}, 'Helium 10'
    elif badges:
        units, src = badge.badge_estimate(max(badges), market), '"bought in past month" badge'
    elif curve:
        units, src = max(curve, key=lambda u: u['v']), 'sales-rank curve'
    why, well = [], None
    top = min(best, key=lambda e: e['rank']) if best else None
    if top and top['rank'] <= THRESH['best_rank_max']:
        well = True
        why.append('#%d in Best Sellers%s' % (top['rank'], ', ' + top['category'] if top.get('category') else ''))
    climbed = [e for e in movers if e.get('rank_now') and e['rank_now'] <= THRESH['best_rank_max']]
    m = min(climbed, key=lambda e: e['rank_now']) if climbed else None
    if m and not (top and top['rank'] <= m['rank_now']):
        well = True
        why.append('#%d in Best Sellers now%s (Movers & Shakers%s)' % (
            m['rank_now'], ', up from #%s' % _n(m['rank_before']) if m.get('rank_before') else '',
            ', ' + m['category'] if m.get('category') else ''))
    if units and units.get('v') is not None:
        enough = (units.get('lo') if units.get('lo') is not None else units['v']) >= min_units
        well = True if enough else (well or False)
        why.append('about %s a month (%s)' % (_n(units['v']), src))
    if movers and well is None:
        m = max(movers, key=lambda e: e.get('pct') or 0)
        why.append('rising on Movers & Shakers%s' % (' (+%s%%)' % _n(m['pct']) if m.get('pct') else ''))
    return {'well': well, 'units': units, 'src': src, 'why': '; '.join(why) or 'no sales signal read'}


def proof(by_market, mins):
    """by_market {mk: [evidence]} for the proof markets; mins {mk: units a month that count as selling well}.
    {'markets': {mk: selling_well()}, 'n': markets where it sells well, 'proven': n >= 2}."""
    out = {mk: selling_well(ev, mk, mins.get(mk) or 100) for mk, ev in (by_market or {}).items()}
    n = sum(1 for v in out.values() if v['well'])
    return {'markets': out, 'n': n, 'proven': n >= THRESH['proven_min']}


# ---------- the same product across markets ----------
def _brand_key(brand):
    return ' '.join(config.norm_kw(brand or '').split())


def _title_words(title, brand):
    bw = set(_brand_key(brand).split())
    return {discovery._fold(w) for w in config.norm_kw(fr.search_words(title or '', brand or '')).split() if w not in bw}


def group(items):
    """Products from several markets' lists and exports [{'market', 'asin', 'title', 'brand', ...}] -> groups of the same
    product: the same ASIN, else the same brand with nearly the same title words. Without a known brand the title's
    first word stands in for it (titles start with the brand). [{'key', 'items'}], biggest first."""
    items = [dict(i) for i in items if i.get('asin')]
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def join(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    first = {}
    for i, it in enumerate(items):
        if it['asin'] in first:
            join(i, first[it['asin']])
        else:
            first[it['asin']] = i
    words = [_title_words(it.get('title'), it.get('brand')) for it in items]
    by_brand = {}
    for i, it in enumerate(items):
        key = (_brand_key(it.get('brand')) or config.norm_kw(it.get('title') or '')).split()[:1]
        if key:
            by_brand.setdefault(discovery._fold(key[0]), []).append(i)
    for idx in by_brand.values():
        for x, i in enumerate(idx):
            for j in idx[x + 1:]:
                a, b = words[i], words[j]
                if a and b and len(a & b) / len(a | b) >= THRESH['title_alike']:
                    join(i, j)
    groups = {}
    for i, it in enumerate(items):
        groups.setdefault(find(i), []).append(it)
    pref = {m: n for n, m in enumerate(('US', 'GB', 'DE', 'AU', 'AE'))}
    out = []
    for members in groups.values():
        members.sort(key=lambda it: pref.get(it.get('market'), 9))
        out.append({'key': members[0]['asin'], 'items': members})
    out.sort(key=lambda g: -len(g['items']))
    return out


# ---------- what shoppers type, never the brand ----------
def brand_words(brand):
    return {w for w in _brand_key(brand).split() if len(w) > 1}


def generic_phrase(title, brand, suggestions=()):
    """(phrase, how): Amazon's suggestion that fits the title (engine.discovery.pick_phrase) without any brand word,
    else the title's first three search words without the brand. how = 'suggestion' | 'title'."""
    bw = brand_words(brand)
    clean = [s for s in suggestions or () if not (set(config.norm_kw(s).split()) & bw)]
    p = discovery.pick_phrase(title, clean)
    if p:
        return p, 'suggestion'
    words = [w for w in config.norm_kw(fr.search_words(title or '', brand or '')).split() if w not in bw]
    return (' '.join(words[:3]) or None), 'title'


def same_product(phrase, title):
    """True when every word of the generic phrase is in the title (singular or plural alike)."""
    want = {discovery._fold(w) for w in config.norm_kw(phrase or '').split()}
    have = {discovery._fold(w) for w in config.norm_kw(title or '').split()}
    return bool(want) and want <= have


# ---------- the gap in one target market ----------
def _who(o, market):
    c = o.get('country')
    where = countries.name(c) if c and c != market else None
    return '%s%s' % (o.get('seller') or 'one seller', ' (%s)' % where if where else '')


def _out(kind, why, conf, **kw):
    return {'type': kind, 'label': LABEL[kind], 'why': why, 'conf': conf, 'gap': kind in GAPS, 'order': ORDER[kind], **kw}


def classify(market, listing, search, asins=()):
    """The gap for a proven product in one target market (AU or AE).

    listing: the same ASIN there {'status': 'ok' (listed) | 'missing' (not listed) | a blocked status, 'available',
      'n_offers', 'offers': [engine.offers shape]}, or None when it was not looked at.
    search: page 1 of the generic phrase {'status', 'phrase', 'rows': [{'asin', 'title', 'origin', 'days', 'sponsored',
      'offer' (engine.offers shape, or None when its page was not read)}]}, or None.
    asins: the product's own ASINs (they are the listing above, not a rival).
    "Somebody else" = another seller on the same listing, or another listing of the same product (every word of the
    phrase in its title). Other listings on page 1 only count in local_rivals (third-party listings delivering locally).
    Returns {'type', 'label', 'why', 'conf', 'gap', 'order', 'sellers', 'exporter', 'exporters', 'local_rivals',
    'same_local'}."""
    lst, srch = listing or {}, search or {}
    name = MARKET_NAMES.get(market, market)
    lst_known = lst.get('status') in ('ok', 'missing')
    srch_known = srch.get('status') in ('ok', 'empty', 'partial')
    phrase = srch.get('phrase')
    rows = [r for r in srch.get('rows') or [] if not r.get('sponsored')]
    local_rivals = sum(1 for r in rows if r.get('origin') == 'local' and not (r.get('offer') or {}).get('amazon')) \
        if srch_known else None
    if not lst_known and not srch_known:
        st = lst.get('status') or srch.get('status') or 'not read'
        return _out('unread', 'Amazon %s could not be read (%s). Shown as a gap in the data, not as missing.' % (name, st),
                    'low', sellers=0, exporter=None, exporters=[], local_rivals=None, same_local=None)
    asins = set(asins or ())
    sellers, extra = {}, 0
    listed = lst.get('status') == 'ok'
    if listed and lst.get('available') is not False:
        for o in lst.get('offers') or []:
            sellers['amazon' if o.get('amazon') else o.get('seller_id') or o.get('seller') or 'buybox'] = o
        if lst.get('n_offers'):
            extra = max(0, int(lst['n_offers']) - len(sellers))
    same_rows = [r for r in rows if r.get('asin') not in asins and phrase and same_product(phrase, r.get('title'))]
    for r in same_rows:
        o = r.get('offer') or {'seller_id': None, 'seller': None, 'country': None, 'amazon': False, 'fba': None,
                               'stock': {'local': 'local', 'overseas': 'abroad'}.get(r.get('origin')), 'days': r.get('days')}
        key = 'amazon' if o.get('amazon') else o.get('seller_id') or o.get('seller') or 'listing:' + str(r.get('asin'))
        sellers.setdefault(key, o)
    third = [o for o in sellers.values() if not o.get('amazon')]
    local3 = [o for o in third if o.get('stock') == 'local']
    abroad3 = [o for o in third if o.get('stock') == 'abroad']
    unknown3 = [o for o in third if not o.get('stock')]
    amazon = any(o.get('amazon') for o in sellers.values())
    n = len(sellers) + extra
    conf = 'high' if lst_known and srch_known and all(o.get('country') for o in third) and not extra and not unknown3 \
        else 'medium' if lst_known and srch_known else 'low'
    exporters = [{'seller': o.get('seller'), 'country': o.get('country'), 'days': o.get('days'), 'stock': o.get('stock')}
                 for o in abroad3 + [o for o in local3 if o.get('country') and o['country'] != market]]
    common = {'sellers': n, 'exporters': exporters, 'local_rivals': local_rivals, 'same_local': len(local3) + extra}
    if n == 0:
        if not srch_known:
            return _out('unread', 'Nobody sells this ASIN on Amazon %s, but the search for the same product could not be '
                                  'read (%s).' % (name, srch.get('status') or 'not read'), 'low', exporter=None, **common)
        where = ' and no listing of the same product on page 1 for "%s"' % phrase if phrase else ''
        base = ('Listed on Amazon %s, but nobody sells it now' % name if listed else 'Not on Amazon %s' % name) \
            if lst_known else 'Not found on page 1 of Amazon %s' % name
        return _out('missing', base + where + '.', 'medium' if lst_known else 'low', exporter=None,
                    unavailable=listed, **common)
    if amazon and not local3 and not extra:
        return _out('amazon_only', 'Amazon itself sells it on Amazon %s, and no local third-party seller does%s.' % (
            name, ' (%d seller%s ship it from abroad)' % (len(abroad3), '' if len(abroad3) == 1 else 's') if abroad3 else ''),
            conf, exporter=None, **common)
    if n == 1 and third:
        o = third[0]
        foreign = bool(o.get('country')) and o['country'] != market
        ex = {'seller': o.get('seller'), 'country': o.get('country'), 'days': o.get('days'), 'stock': o.get('stock'),
              'seller_id': o.get('seller_id')}
        days = ': delivery in %d day%s' % (o['days'], '' if o['days'] == 1 else 's') if o.get('days') is not None else ''
        if o.get('stock') == 'abroad':
            return _out('lone_abroad', 'Only one seller, %s, ships it from abroad%s. Nobody else sells it on Amazon %s.' % (
                _who(o, market), days, name), conf, exporter=ex, **common)
        if o.get('stock') == 'local' and (foreign or not o.get('country')):
            return _out('lone_fba', "Only one seller, %s, with stock in Amazon's %s warehouse%s. No rivals." % (
                _who(o, market), name, '' if o.get('country') else " (where the seller is based was not read)"),
                conf if foreign else 'low', exporter=ex, **common)
        if not o.get('stock') and foreign:
            return _out('lone_abroad', 'Only one seller, %s; how it ships was not read.' % _who(o, market), 'low',
                        exporter=ex, **common)
    if third and len(abroad3) == len(third) and not amazon and not extra:
        cs = sorted({countries.name(o['country']) for o in abroad3 if o.get('country')})
        return _out('imported', '%d sellers, all shipping it from abroad%s.' % (len(abroad3), ' (%s)' % ', '.join(cs) if cs else ''),
                    conf, exporter=None, **common)
    k = len(local3) + extra
    who = 'seller' if extra else 'local seller'
    note = ' (%d on the same listing; where they ship from was not read)' % extra if extra else ''
    if k >= THRESH['crowded_min']:
        return _out('crowded', '%d %ss offer the same product on Amazon %s%s.' % (k, who, name, note), conf, exporter=None,
                    **common)
    if k:
        return _out('few', '%d %s%s offer%s the same product on Amazon %s%s.' % (
            k, who, '' if k == 1 else 's', 's' if k == 1 else '', name, note), conf, exporter=None, **common)
    return _out('few', '%d seller%s offer%s the same product on Amazon %s; where %s ship from was not read.' % (
        n, '' if n == 1 else 's', 's' if n == 1 else '', name, 'it' if n == 1 else 'some'), 'low', exporter=None, **common)


# ---------- warnings ----------
def flags(title, bullets='', item_form=None, safety=(), attrs=None):
    """Warnings shown next to a gap, each with its reason: [{'key', 'label', 'why'}]. Chemical / dangerous goods comes
    from the title and bullet points, the item form (spray, liquid...), the page's safety lines and, when the SP-API
    is set up, Amazon's own dangerous-goods attributes; electrical, kids and medical from the title."""
    out, why = [], []
    words = sorted({m.group(0).lower() for m in DG.finditer('%s %s' % (title or '', bullets or ''))})
    if words:
        why.append(', '.join(words[:4]))
    if item_form and DG_FORMS.search(item_form):
        why.append('item form: %s' % item_form.lower())
    if safety:
        why.append('the page shows %s' % ', '.join(list(safety)[:3]))
    for k in DG_ATTRS:
        v = (attrs or {}).get(k)
        if v and str(v).lower() not in ('not_applicable', 'none', 'false', 'no', '[]'):
            why.append('Amazon lists it as dangerous goods (%s)' % k.replace('_', ' '))
            break
    if why:
        out.append({'key': 'dg', 'label': FLAG_LABELS['dg'], 'why': '; '.join(why)})
    checks = dict(fr.CHECKS)
    for key, name in FLAG_CHECKS.items():
        m = checks[name].search(title or '')
        if m:
            out.append({'key': key, 'label': FLAG_LABELS[key], 'why': m.group(0).lower()})
    return out


# ---------- the lone exporters watchlist ----------
def seller_keys(offers):
    return sorted({'amazon' if o.get('amazon') else str(o.get('seller_id') or o.get('seller') or '') for o in offers or ()} - {''})


def second_seller(market, asin, title, before, now, ts):
    """An alert when a watched listing that had one seller now has more (before / now: {'offers', 'n_offers'}), else
    None. The dedupe key names the sellers, so each new line-up alerts once."""
    if not before or not now:
        return None
    had = seller_keys(before.get('offers'))
    nb = max(len(had), int(before.get('n_offers') or 0))
    nn = max(len(seller_keys(now.get('offers'))), int(now.get('n_offers') or 0))
    if nb > 1 or nn < 2:
        return None
    new = [o for o in now.get('offers') or () if (seller_keys([o]) or [''])[0] not in had]
    who = ', '.join(_who(o, market) for o in new[:3]) or 'another seller'
    keys = ','.join(seller_keys(now.get('offers'))) or str(nn)
    return {'rule': 'lone_second_seller', 'severity': 3, 'market': market, 'target': asin,
            'title': 'A second seller on %s' % (title or asin)[:90],
            'body': 'Amazon %s now shows %d sellers (new: %s). The lone-exporter gap is closing.' % (
                MARKET_NAMES.get(market, market), nn, who),
            'data': {'asin': asin, 'sellers': seller_keys(now.get('offers')), 'n_offers': nn},
            'dedupe_key': 'lone|%s|%s|%s' % (market, asin, keys), 'ts': ts}
