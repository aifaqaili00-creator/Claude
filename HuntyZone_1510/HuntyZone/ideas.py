"""New product ideas: what is booming or new on Amazon, and what shoppers search for in a niche.

Sources (public Amazon pages, read with the background browser):
- Movers & Shakers: biggest sales-rank gains in the last 24 hours = early sign of a boom
- Hot New Releases: best-selling new products
- Search suggestions (what shoppers type) to grow a niche into keywords
"""
import asyncio
import logging
import math
import os
import re
from urllib.parse import quote, urlparse

import amazon_check as ac
import file_rank as fr

# Category names that bring extra rules or big brands (electrical, kids, food, beauty...). Hidden unless asked for.
RISKY_CATEGORY = re.compile(r'select ?all|baby|beauty|book|clothing|fashion|shoe|jewel|computer|electronic|cell phone|camera|'
                            r'video ?game|software|grocery|gourmet|food|health|personal care|toy|game|music|movie|kindle|'
                            r'digital|app|alexa|gift card|appliance|automotive|industrial|luggage|watch|handmade|collectible|'
                            r'subscription|amazon device|magazine|unique|vehicle|launchpad|audible|prime|renewed|'
                            r'refurbished|coin|currenc|stamp|mobile|phone', re.I)
# Categories a new seller's own product does well in: scanned first (in this order), whatever order Amazon's menu has
FRIENDLY = [re.compile(p, re.I) for p in (r'home ?(&|and) ?kitchen|^home$|home-garden|^home\b', r'kitchen', r'garden|patio|lawn',
                                          r'pet', r'sport|outdoor|fitness', r'office|stationery', r'tool|home improvement|diy|^hi$',
                                          r'craft|sewing|hobb', r'storage|organi', r'bath|bed|furniture|lighting|decor')]
MIN_MENU = 5                                           # fewer department links than this is not the menu (stray links)


def friendly_first(cats):
    """The categories in the order worth scanning: seller-friendly ones first (FRIENDLY's order), then the rest as Amazon
    lists them. A plain alphabetical menu would otherwise start with Amazon Devices, Appliances, Arts..."""
    def rank(c):
        text = '%s %s' % (c.get('name') or '', c.get('slug') or '')
        return next((i for i, rx in enumerate(FRIENDLY) if rx.search(text)), len(FRIENDLY))
    return sorted(cats, key=rank)
DEFAULT_US = [('home-garden', 'Home & Kitchen'), ('kitchen', 'Kitchen & Dining'), ('lawn-garden', 'Patio, Lawn & Garden'),
              ('sporting-goods', 'Sports & Outdoors'), ('pet-supplies', 'Pet Supplies'), ('office-products', 'Office Products'),
              ('hi', 'Tools & Home Improvement'), ('arts-crafts', 'Arts, Crafts & Sewing')]
LISTS = {'movers': '/gp/movers-and-shakers', 'new': '/gp/new-releases', 'best': '/gp/bestsellers'}
COMPLETE = {   # Amazon search-suggestion service per marketplace
    'US': ('https://completion.amazon.com/api/2017/suggestions', 'ATVPDKIKX0DER', 'en_US'),
    'AU': ('https://completion.amazon.com.au/api/2017/suggestions', 'A39IBJ37TRP1C6', 'en_AU'),
    'AE': ('https://completion.amazon.ae/api/2017/suggestions', 'A2VIGQ35RCS4UG', 'en_AE'),
}
for _c in COMPLETE:                                    # tests point these at a local fake
    if os.environ.get('PC_COMPLETE_' + _c):
        COMPLETE[_c] = (os.environ['PC_COMPLETE_' + _c],) + COMPLETE[_c][1:]

# Runs inside a Best Sellers / Movers & Shakers / New Releases page.
EXTRACT_LIST_JS = r"""
() => {
  const cards = [...document.querySelectorAll('[id="gridItemRoot"], .zg-grid-general-faceout, li.zg-item-immersion, [data-client-recs-list] > div')];
  const seen = new Set(), out = [];
  for (const e of cards) {
    const all = e.innerText || e.textContent || '';
    let asin = (e.querySelector('[data-asin]') || {}).dataset?.asin || '';
    if (!asin) { const x = [...e.querySelectorAll('[id]')].map(n => n.id).find(id => /^[A-Z0-9]{10}$/.test(id)); asin = x || ''; }
    const link = e.querySelector('a[href*="/dp/"]');
    if (!asin && link) asin = ((link.getAttribute('href') || '').match(/\/dp\/([A-Z0-9]{10})/) || [])[1] || '';
    if (!asin || seen.has(asin)) continue;
    seen.add(asin);
    const img = e.querySelector('img');
    const clamp = e.querySelector('[class*="line-clamp"], .p13n-sc-truncate, .p13n-sc-truncated');
    const rateEl = e.querySelector('[title*="out of 5"], [aria-label*="out of 5"], .a-icon-alt');
    const rateTxt = rateEl ? (rateEl.getAttribute('title') || rateEl.getAttribute('aria-label') || rateEl.textContent || '') : '';
    const rateLink = e.querySelector('a[title*="out of 5"], a[aria-label*="out of 5"]');
    const countEl = rateLink && rateLink.querySelector('.a-size-small');
    const nums = all.split('\n').map(s => s.trim()).filter(s => /^\d[\d,]*$/.test(s));
    const fromTitle = ((rateTxt.match(/([\d,]+)\s+(?:ratings?|reviews?)/i) || [])[1]) || '';
    const reviews = fromTitle || (countEl && countEl.textContent.trim()) || (nums.length ? nums[nums.length - 1] : '');
    const priceEl = e.querySelector('[class*="price"], .p13n-sc-price, .a-color-price');
    out.push({
      asin, deal: /limited time deal|deal of the day|lightning deal|\bdeal\b|\d+% off/i.test(all),
      title: ((clamp && (clamp.innerText || clamp.textContent)) || (img && img.alt) || (link && link.innerText) || '').trim(),
      image: img ? (img.getAttribute('src') || '') : '',
      rank: ((e.querySelector('.zg-bdg-text') || {}).textContent || (all.match(/#\s?(\d+)/) || [])[0] || ''),
      pct: ((e.querySelector('.zg-percent-change') || {}).textContent || ''),
      movement: ((e.querySelector('.zg-sales-movement') || {}).textContent || (all.match(/Sales rank:[^\n]*/i) || [''])[0]),
      rating: (rateTxt.match(/\d(?:\.\d)?\s+out of 5/) || [''])[0], reviews,
      price: ((priceEl && priceEl.textContent) || (all.match(/(?:A\$|AED|US\$|\$)\s?[\d,]+(?:\.\d{2})?/) || [''])[0]),
    });
  }
  return out;
}
"""

# Department links on a Movers & Shakers / Best Sellers / New Releases page (the menu is drawn by script, so we wait for it).
NAV_SEL = 'a[href*="movers-and-shakers/"], a[href*="new-releases/"], a[href*="bestsellers/"]'
CATEGORIES_JS = r"""
(sel) => [...document.querySelectorAll(sel)]
  .map(a => ({href: a.getAttribute('href') || '', name: (a.innerText || a.textContent || '').trim()}))
  .filter(x => x.name && x.name.length < 60)
"""
NAV_WAIT_JS = '(sel) => document.querySelectorAll(sel).length >= 3'
LIST_PATH = re.compile(r'/(?:movers-and-shakers|new-releases|bestsellers)/([a-z0-9][a-z0-9-]*)(?=[/?#]|$)', re.I)
SEE_MORE = re.compile(r'^(see|view|show)\b|^any department', re.I)
# Used only when Amazon's menu can't be read. A slug that turns out not to exist is dropped after its first read.
DEFAULTS = {
    'US': DEFAULT_US,
    'AU': [('home', 'Home'), ('kitchen', 'Kitchen & Dining'), ('sporting-goods', 'Sports, Fitness & Outdoors'),
           ('pet-supplies', 'Pet Supplies'), ('office-products', 'Stationery & Office Products'), ('garden', 'Garden'),
           ('lawn-garden', 'Garden'), ('home-improvement', 'Home Improvement'), ('hi', 'Home Improvement')],
    'AE': [('home', 'Home'), ('kitchen', 'Kitchen'), ('sporting', 'Sports'), ('sports', 'Sports'),
           ('pet-supplies', 'Pet Supplies'), ('office-products', 'Office Products'), ('garden', 'Garden'),
           ('home-improvement', 'Home Improvement')],
    'GB': [('kitchen', 'Home & Kitchen'), ('diy', 'DIY & Tools'), ('outdoors', 'Garden'), ('sports', 'Sports & Outdoors'),
           ('pet-supplies', 'Pet Supplies'), ('officeproduct', 'Stationery & Office Supplies')],
    'DE': [('kitchen', 'Home & Kitchen'), ('diy', 'DIY & Tools'), ('garden', 'Garden'), ('sports', 'Sports & Outdoors'),
           ('pet-supplies', 'Pet Supplies'), ('officeproduct', 'Stationery & Office Supplies')],
}


class ListMissing(Exception):
    """Amazon has no list for this category slug (it sent the browser somewhere else)."""


def parse_categories(links):
    """[{href, name}] from the page -> [{slug, name, risky}], one per department, in page order."""
    out, by_slug = [], {}
    for l in links:
        m = LIST_PATH.search(l.get('href') or '')
        if not m:
            continue
        slug = m.group(1).lower()
        if slug.isdigit() or slug.startswith('ref'):
            continue
        name = re.sub(r'\s+', ' ', l.get('name') or '').strip()
        good = bool(name) and not SEE_MORE.search(name)
        if slug in by_slug:
            if good and by_slug[slug]['_weak']:
                by_slug[slug].update(name=name, _weak=False)
            continue
        c = {'slug': slug, 'name': name if good else slug.replace('-', ' ').title(), '_weak': not good}
        by_slug[slug] = c
        out.append(c)
    for c in out:
        c.pop('_weak')
        c['risky'] = bool(RISKY_CATEGORY.search(c['name']))
    return out


def _int(s):
    m = re.search(r'\d[\d,]*', str(s or ''))
    return int(m.group(0).replace(',', '')) if m else None


async def _dump(page, code, what):
    """Keep a copy of a page we could not read, so it can be looked at later (the app folder's debug\\)."""
    try:
        d = ac.APP_DIR / 'debug'
        d.mkdir(parents=True, exist_ok=True)
        html = d / ('%s_%s.html' % (what, code))
        html.write_text(await page.content(), encoding='utf-8')
        await page.screenshot(path=str(d / ('%s_%s.png' % (what, code))))
        return str(html)
    except Exception:                                   # noqa: BLE001 - only a debugging aid
        return None


async def _read_categories(checker, code, path):
    site = ac.MARKETS[code]['site']
    page = await checker.new_page()
    try:
        await checker._goto(page, site + path, code)        # a captcha or block raises with a clear message
        try:
            await page.wait_for_function(NAV_WAIT_JS, arg=NAV_SEL, timeout=10000)
        except Exception:                               # noqa: BLE001 - read whatever is there
            pass
        out = parse_categories(await page.evaluate(CATEGORIES_JS, NAV_SEL))
        if len(out) < MIN_MENU:                         # a page without the department menu (a few stray links at most)
            await _dump(page, code, 'categories')
            return []
        return out
    finally:
        await page.close()


async def categories(checker, code):
    """Top-level departments: [{slug, name, risky, default?}]. Never empty for a known marketplace."""
    await checker.start()
    out = await _read_categories(checker, code, LISTS['best'])          # Best Sellers first: Amazon has emptied Movers &
    if not out:                                                         # Shakers, and its page then has no menu at all
        out = await _read_categories(checker, code, LISTS['movers'])
    if not out:
        logging.getLogger('pc').info('%s: could not read the category menu; the built-in categories are used '
                                     '(page saved in the debug folder).' % ac.MARKETS[code]['name'])
        out = [{'slug': s, 'name': n, 'risky': False, 'default': True} for s, n in DEFAULTS.get(code, [])]
    return out


COUNT_JS = "() => document.querySelectorAll('[id=\"gridItemRoot\"], .zg-grid-general-faceout, li.zg-item-immersion').length"


async def read_list(checker, code, kind, slug, name):
    """One Movers & Shakers, New Releases or Best Sellers page (up to 50 products). Raises ListMissing for an unknown
    slug."""
    site = ac.MARKETS[code]['site']
    page = await checker.new_page()
    try:
        http = await checker._goto(page, ac.with_lang('%s%s/%s' % (site, LISTS[kind], slug), code), code)
        if http == 404 or not re.search(r'/%s(?:[/?#]|$)' % re.escape(slug), urlparse(page.url).path):
            raise ListMissing('%s has no %s list for "%s"' % (ac.MARKETS[code]['name'], kind, name))
        last, still = -1, 0
        for _ in range(6):                                   # the second half of the list loads on scroll
            n = await page.evaluate(COUNT_JS)
            if n >= 50:
                break
            still = still + 1 if n == last else 0
            if still >= 2:                                   # two tries without more cards: that is all of it
                break
            last = n
            await page.mouse.wheel(0, 4000)
            try:
                await page.wait_for_function('n => (%s)() > n' % COUNT_JS, arg=n, timeout=1000)
            except Exception:
                pass
        raw = await page.evaluate(EXTRACT_LIST_JS)
    finally:
        await page.close()
    items = []
    for r in raw:
        mv = r.get('movement') or ''
        items.append({
            'asin': r['asin'], 'title': r['title'][:200], 'image': r['image'], 'market': code,
            'url': '%s/dp/%s' % (site, r['asin']), 'category': name, 'kind': kind, 'slug': slug, 'deal': bool(r.get('deal')),
            'rank': _int(r['rank']), 'pct': _int(r['pct']) if kind == 'movers' else None,
            'rank_now': _int(re.search(r'rank:\s*([\d,]+)', mv, re.I).group(1)) if re.search(r'rank:\s*([\d,]+)', mv, re.I) else None,
            'rank_before': _int(re.search(r'previously\s*([\d,]+)', mv, re.I).group(1)) if re.search(r'previously\s*([\d,]+)', mv, re.I) else None,
            'rating': ac.parse_price(r['rating']), 'reviews': _int(r['reviews']), 'price': ac.parse_price(r['price']),
        })
    return items


def score_ideas(items, code, hide_risky=True, price_range=None, exclude_tech=False, stats=None, climbing=None):
    """Merge products from both lists, score them and group similar products into ideas.

    price_range (lo, hi) in the marketplace's money keeps only products selling within it (a product with no price
    shown is kept, scored lower and flagged). exclude_tech leaves out phones, laptops and their accessories.
    stats, if given, receives how many products each filter removed. climbing {asin: why}: products climbing fast on
    New Releases or Best Sellers over the last days (engine.stockwatch.candidates): Booming too, while Amazon shows no
    Movers & Shakers.
    """
    T = fr.TARGETS[code]
    lo, hi = price_range or T['price']
    st = stats if stats is not None else {}
    st.update(out_of_range=0, tech=0, no_price=0)
    by_asin = {}
    for it in items:
        cur = by_asin.setdefault(it['asin'], {**it, 'lists': set()})
        cur['lists'].add(it['kind'])
        for k in ('pct', 'rank_now', 'rank_before'):
            if it.get(k) is not None:
                cur[k] = it[k]
        if it['kind'] == 'new':
            cur['new_rank'] = it['rank']
        else:
            cur['mover_rank'] = it['rank']
    ideas = []
    for p in by_asin.values():
        price, rv = p.get('price'), p.get('reviews') or 0
        if exclude_tech and fr.is_tech(p['title']):
            st['tech'] += 1
            continue
        if price_range and price is not None and not lo <= price <= hi:
            st['out_of_range'] += 1
            continue
        flags = [n for n, rx in fr.CHECKS if rx.search(p['title'])]
        if price is None:
            st['no_price'] += 1
            if price_range:
                flags.append('price unknown')
        elif price < lo:
            flags.append('cheap')
        elif price > hi:
            flags.append('expensive')
        risky = [f for f in flags if f not in ('cheap', 'expensive', 'price unknown')]
        if hide_risky and risky:
            continue
        s, why = 0.0, []
        pct = p.get('pct')
        if pct:
            s += min(40, 13 * math.log10(max(pct, 1)))
            why.append('sales rank up %s%% in 24 h' % f'{pct:,}')
        if 'new' in p['lists'] and p.get('new_rank'):
            s += max(0, 25 - (p['new_rank'] - 1) * 0.5)
            why.append('#%d hot new release' % p['new_rank'])
        climb = (climbing or {}).get(p['asin'])
        if climb and not (pct and pct >= 300):
            s += 30
            why.append(climb[0].lower() + climb[1:])
        if len(p['lists']) > 1:
            s += 10
            why.append('on both lists')
        s += 15 if rv < 100 else 8 if rv < 500 else 0 if rv < 2000 else -10
        if price is not None and lo <= price <= hi:
            s += 10
        elif price is None and price_range:
            s -= 10
        s -= 15 * len(risky)
        label = 'Booming' if (pct and pct >= 300) or climb else 'Rising' if pct and pct >= 50 else             'New' if 'new' in p['lists'] else 'Moving'
        ideas.append({**p, 'lists': sorted(p['lists']), 'flags': flags, 'score': round(s, 1), 'label': label,
                      'why': why, 'search': fr.search_words(p['title'])})
    ideas.sort(key=lambda x: -x['score'])
    groups = {}                                             # similar products -> one idea, keep the best
    for i in ideas:
        key = ' '.join(sorted(w.lower().rstrip('s') for w in i['search'].split()[:3]))
        if key in groups:
            g = groups[key]
            g['similar'] += 1
            g['score'] = round(g['score'] + 3, 1)                              # several products moving = stronger
            g['lists'] = sorted(set(g['lists']) | set(i['lists']))
            if i.get('new_rank') and not g.get('new_rank'):
                g['new_rank'] = i['new_rank']
            if (i.get('pct') or 0) > (g.get('pct') or 0):
                g['pct'], g['label'] = i['pct'], i['label'] if i['label'] != 'New' else g['label']
            g['members'].append({k: i.get(k) for k in ('asin', 'title', 'price', 'reviews', 'url', 'image')})
        else:
            groups[key] = {**i, 'similar': 0, 'members': []}
    out = sorted(groups.values(), key=lambda x: -x['score'])
    for n, i in enumerate(out, 1):
        i['pos'] = n
    return out


async def scan(checker, code, cats, kinds, hide_risky, progress):
    await checker.start()
    sem = asyncio.Semaphore(4)
    items, done = [], [0]
    jobs = [(k, c) for c in cats for k in kinds]

    async def one(kind, cat):
        async with sem:
            try:
                got = await read_list(checker, code, kind, cat['slug'], cat['name'])
                items.extend(got)
            except Exception as e:
                progress('%s / %s failed: %s' % (cat['name'], kind, str(e).splitlines()[0][:80]))
            done[0] += 1
            progress('Read %d of %d lists (%s, %s)' % (done[0], len(jobs), cat['name'],
                                                       'Movers & Shakers' if kind == 'movers' else 'New Releases'))
    await asyncio.gather(*(one(k, c) for k, c in jobs))
    out = {'market': code, 'scanned': len(items), 'lists': len(jobs), 'categories': cats, 'items': items}
    if hide_risky is not None:
        out['ideas'] = score_ideas(items, code, hide_risky)
    return out


async def complete(checker, code, prefix):
    """Amazon's search suggestions for what has been typed so far, best first (blank entries kept in place).
    Raises when the call fails."""
    await checker.start()
    url, mid, lop = COMPLETE[code]
    q = '%s?limit=11&prefix=%s&suggestion-type=KEYWORD&page-type=Search&lop=%s&site-variant=desktop' \
        '&client-info=amazon-search-ui&mid=%s&alias=aps' % (url, quote(prefix), lop, mid)
    async with checker.slot('complete:' + code):
        r = await checker.ctx.request.get(q, timeout=15000)
        data = await r.json()
    return [' '.join(str(s.get('value') or '').lower().split()) for s in data.get('suggestions') or []]


async def suggest(checker, code, seed, progress=None):
    """Grow a niche into the keywords shoppers actually type, most common first."""
    await checker.start()
    seed = ' '.join(seed.lower().split())[:60]
    prefixes = [seed, seed + ' '] + [seed + ' ' + c for c in 'abcdefghiklmnoprstuw'] + ['best ' + seed, seed + ' for']
    found = {}
    sem = asyncio.Semaphore(5)

    failed = [0]

    async def one(i, prefix):
        async with sem:
            try:
                got = await complete(checker, code, prefix)
            except Exception:
                failed[0] += 1
                return
            for n, kw in enumerate(got):
                if not kw:
                    continue
                f = found.setdefault(kw, {'keyword': kw, 'hits': 0, 'best': 99, 'first': (i, n)})
                f['hits'] += 1
                f['best'] = min(f['best'], n + 1)
    await asyncio.gather(*(one(i, p) for i, p in enumerate(prefixes)))
    seed_words = [w.rstrip('s') for w in seed.split()]
    out = [f for f in found.values() if all(w in f['keyword'] for w in seed_words)] or list(found.values())
    out.sort(key=lambda f: (-f['hits'], f['best'], f['first']))
    if progress:
        progress('%d keyword ideas for "%s"' % (len(out), seed))
    return {'market': code, 'seed': seed, 'keywords': out[:40], 'n_calls': len(prefixes), 'n_failed': failed[0]}


def trends_url(keyword, code='US'):
    geo = {'US': 'US', 'AU': 'AU', 'AE': 'AE'}.get(code, 'US')
    return 'https://trends.google.com/trends/explore?date=today%%205-y&geo=%s&q=%s' % (geo, quote(keyword))
