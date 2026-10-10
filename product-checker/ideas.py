"""New product ideas: what is booming or new on Amazon, and what shoppers search for in a niche.

Sources (public Amazon pages, read with the background browser):
- Movers & Shakers: biggest sales-rank gains in the last 24 hours = early sign of a boom
- Hot New Releases: best-selling new products
- Search suggestions (what shoppers type) to grow a niche into keywords
"""
import asyncio
import math
import os
import re
from urllib.parse import quote

import amazon_check as ac
import file_rank as fr

# Category names that bring extra rules or big brands (electrical, kids, food, beauty...). Hidden unless asked for.
RISKY_CATEGORY = re.compile(r'select ?all|baby|beauty|book|clothing|fashion|shoe|jewel|computer|electronic|cell phone|camera|'
                            r'video ?game|software|grocery|gourmet|food|health|personal care|toy|game|music|movie|kindle|'
                            r'digital|app|alexa|gift card|appliance|automotive|industrial|luggage|watch|handmade|collectible|'
                            r'subscription|amazon device|magazine|unique|vehicle|launchpad|audible|prime', re.I)
DEFAULT_US = [('home-garden', 'Home & Kitchen'), ('kitchen', 'Kitchen & Dining'), ('lawn-garden', 'Patio, Lawn & Garden'),
              ('sporting-goods', 'Sports & Outdoors'), ('pet-supplies', 'Pet Supplies'), ('office-products', 'Office Products'),
              ('hi', 'Tools & Home Improvement'), ('arts-crafts', 'Arts, Crafts & Sewing')]
LISTS = {'movers': '/gp/movers-and-shakers', 'new': '/gp/new-releases'}
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

CATEGORIES_JS = r"""
(base) => [...document.querySelectorAll('a[href*="' + base + '/"]')]
  .map(a => ({href: a.getAttribute('href') || '', name: (a.innerText || a.textContent || '').trim()}))
  .filter(x => x.name && x.name.length < 60)
"""


def _int(s):
    m = re.search(r'\d[\d,]*', str(s or ''))
    return int(m.group(0).replace(',', '')) if m else None


async def categories(checker, code):
    """Top-level categories of the Movers & Shakers page: [{slug, name, risky}]."""
    await checker.start()
    site = ac.MARKETS[code]['site']
    page = await checker.ctx.new_page()
    try:
        await checker._goto(page, site + LISTS['movers'], code)
        links = await page.evaluate(CATEGORIES_JS, LISTS['movers'])
    finally:
        await page.close()
    out, seen = [], set()
    for l in links:
        m = re.search(re.escape(LISTS['movers']) + r'/([a-z0-9-]+)/?(?:ref|\?|$)', l['href'])
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        out.append({'slug': m.group(1), 'name': l['name'], 'risky': bool(RISKY_CATEGORY.search(l['name']))})
    if not out and code == 'US':
        out = [{'slug': s, 'name': n, 'risky': False} for s, n in DEFAULT_US]
    return out


COUNT_JS = "() => document.querySelectorAll('[id=\"gridItemRoot\"], .zg-grid-general-faceout, li.zg-item-immersion').length"


async def read_list(checker, code, kind, slug, name):
    """One Movers & Shakers or New Releases page (up to 50 products)."""
    site = ac.MARKETS[code]['site']
    page = await checker.ctx.new_page()
    try:
        await checker._goto(page, '%s%s/%s' % (site, LISTS[kind], slug), code)
        last = -1
        for _ in range(8):                                   # the second half of the list loads on scroll
            n = await page.evaluate(COUNT_JS)
            if n >= 50 or (n == last and n > 0):
                break
            last = n
            await page.mouse.wheel(0, 4000)
            try:
                await page.wait_for_function('n => (%s)() > n' % COUNT_JS, arg=n, timeout=1500)
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


def score_ideas(items, code, hide_risky=True):
    """Merge products from both lists, score them and group similar products into ideas."""
    T = fr.TARGETS[code]
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
        flags = [n for n, rx in fr.CHECKS if rx.search(p['title'])]
        price, rv = p.get('price'), p.get('reviews') or 0
        if price is not None and price < T['price'][0]:
            flags.append('cheap')
        if price is not None and price > T['price'][1]:
            flags.append('expensive')
        risky = [f for f in flags if f not in ('cheap', 'expensive')]
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
        if len(p['lists']) > 1:
            s += 10
            why.append('on both lists')
        s += 15 if rv < 100 else 8 if rv < 500 else 0 if rv < 2000 else -10
        if price is not None and T['price'][0] <= price <= T['price'][1]:
            s += 10
        s -= 15 * len(risky)
        label = 'Booming' if pct and pct >= 300 else 'Rising' if pct and pct >= 50 else 'New' if 'new' in p['lists'] else 'Moving'
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
        else:
            groups[key] = {**i, 'similar': 0}
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


async def suggest(checker, code, seed, progress=None):
    """Grow a niche into the keywords shoppers actually type, most common first."""
    await checker.start()
    url, mid, lop = COMPLETE[code]
    seed = ' '.join(seed.lower().split())[:60]
    prefixes = [seed, seed + ' '] + [seed + ' ' + c for c in 'abcdefghiklmnoprstuw'] + ['best ' + seed, seed + ' for']
    found = {}
    sem = asyncio.Semaphore(5)

    failed = [0]

    async def one(i, prefix):
        async with sem:
            q = '%s?limit=11&prefix=%s&suggestion-type=KEYWORD&page-type=Search&lop=%s&site-variant=desktop' \
                '&client-info=amazon-search-ui&mid=%s&alias=aps' % (url, quote(prefix), lop, mid)
            try:
                async with checker.slot('complete:' + code):
                    r = await checker.ctx.request.get(q, timeout=15000)
                    data = await r.json()
            except Exception:
                failed[0] += 1
                return
            for n, s in enumerate(data.get('suggestions') or []):
                kw = ' '.join(str(s.get('value') or '').lower().split())
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
