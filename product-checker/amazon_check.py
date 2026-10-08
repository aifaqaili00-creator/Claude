"""Search Amazon AU / UAE / US in a real browser window and count how many listings deliver fast.

Fast delivery (within a few days) means the stock is already in that country: Amazon itself,
FBA or a local seller. Slow delivery (1-3 weeks) means it ships from overseas.
The browser keeps its own profile, so your Helium 10 login and Amazon delivery locations are remembered.
"""
import datetime as dt
import os
import re
import time
from pathlib import Path
from urllib.parse import quote_plus

MARKETS = {
    'AU': {'name': 'Australia', 'site': 'https://www.amazon.com.au', 'location': 'postcode 2000 (Sydney)'},
    'AE': {'name': 'UAE', 'site': 'https://www.amazon.ae', 'location': 'Dubai'},
    'US': {'name': 'USA', 'site': 'https://www.amazon.com', 'location': 'ZIP 10001 (New York)'},
}
HELIUM10_URL = 'https://members.helium10.com/'
PROFILE_DIR = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'ProductChecker' / 'browser-profile'

MONTHS = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}
MON = r'(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b'
DAY_MON = re.compile(r'\b(\d{1,2})\s+' + MON, re.I)          # 10 Oct (AU, UAE)
MON_DAY = re.compile(r'\b' + MON + r'\.?\s+(\d{1,2})\b', re.I)  # Oct 10 (US)


def delivery_days(text, today=None):
    """Days until the fastest delivery date mentioned in Amazon's delivery text, or None if there is none."""
    if not text:
        return None
    today = today or dt.date.today()
    t = text.lower()
    days = []
    if re.search(r'\btoday\b|\bovernight\b|\bwithin \d+ hours?\b', t):
        days.append(0)
    if 'tomorrow' in t:
        days.append(1)
    found = [(int(d), m) for d, m in DAY_MON.findall(t)] + [(int(d), m) for m, d in MON_DAY.findall(t)]
    for day, mon in found:
        month = MONTHS[mon[:3]]
        try:
            date = dt.date(today.year, month, day)
        except ValueError:
            continue
        if date < today - dt.timedelta(days=7):              # e.g. "3 Jan" seen in December
            date = dt.date(today.year + 1, month, day)
        days.append((date - today).days)
    return min(days) if days else None


def parse_count(text):
    """'1,234' -> 1234, '1.2K' -> 1200, '2K+' -> 2000."""
    if not text:
        return None
    m = re.search(r'(\d[\d,.]*)\s*([kKmM]?)', str(text))
    if not m:
        return None
    n = float(m.group(1).replace(',', ''))
    n *= {'k': 1e3, 'm': 1e6}.get(m.group(2).lower(), 1)
    return int(n)


def parse_price(text):
    m = re.search(r'(\d[\d,]*\.?\d*)', (text or '').replace('\xa0', ' '))
    return float(m.group(1).replace(',', '')) if m else None


# Runs inside the Amazon page: one record per search result.
EXTRACT_JS = r"""
() => [...document.querySelectorAll('div[data-component-type="s-search-result"][data-asin]')]
  .filter(e => e.dataset.asin)
  .map(e => {
    const q = s => e.querySelector(s);
    const txt = s => { const x = q(s); return x ? x.innerText.trim() : ''; };
    const all = e.innerText || '';
    const star = q('[aria-label*="out of 5"]') || q('i[class*="a-star"] .a-icon-alt');
    const labels = [...e.querySelectorAll('[aria-label]')].map(x => x.getAttribute('aria-label'))
      .filter(a => /rating|review/i.test(a) && !/out of 5/i.test(a));
    let delivery = txt('[data-cy="delivery-recipe"]') || txt('.udm-primary-delivery-message');
    if (!delivery) delivery = all.split('\n').filter(l => /deliver|get it|arrives|ships/i.test(l)).join(' | ');
    const bought = (all.match(/([\d.,]+\s*[KkMm]?\+?)\s+bought in past month/i) || [])[1] || '';
    return {
      asin: e.dataset.asin,
      title: txt('h2') || txt('[data-cy="title-recipe"]'),
      price: (q('.a-price:not([data-a-strike]) .a-offscreen') || {}).textContent || '',
      rating: star ? (star.getAttribute('aria-label') || star.textContent || '') : '',
      reviews: labels[0] || txt('a[href*="customerReviews"] span') || txt('[data-cy="reviews-block"] .s-underline-text'),
      delivery: delivery.replace(/\s+/g, ' ').trim(),
      prime: !!q('i.a-icon-prime, [aria-label="Amazon Prime"], .s-prime'),
      sponsored: !!q('.puis-sponsored-label-text, .s-sponsored-label-text') || /^\s*Sponsored/m.test(all),
      bought: bought,
    };
  })
"""


class Browser:
    """One visible Chrome/Edge window with a saved profile. Use it from a single thread only."""

    def __init__(self, log=print):
        self.log = log
        self.pw = None
        self.ctx = None
        self.tabs = {}

    def start(self):
        if self.ctx:
            try:
                self.ctx.pages              # still open?
                return
            except Exception:
                self.ctx = None
        from playwright.sync_api import sync_playwright
        if not self.pw:
            self.pw = sync_playwright().start()
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        opts = dict(user_data_dir=str(PROFILE_DIR), headless=False, no_viewport=True,
                    ignore_default_args=['--enable-automation', '--disable-extensions'],
                    args=['--disable-blink-features=AutomationControlled', '--start-maximized'])
        last = None
        exe = os.environ.get('PRODUCT_CHECKER_BROWSER')   # optional: path to a browser to use instead
        if exe:
            self.ctx = self.pw.chromium.launch_persistent_context(executable_path=exe, **opts)
        for channel in () if self.ctx else ('chrome', 'msedge', None):  # your Chrome, else Edge (always on Windows), else Playwright's own
            try:
                self.ctx = self.pw.chromium.launch_persistent_context(channel=channel, **opts) if channel else \
                    self.pw.chromium.launch_persistent_context(**opts)
                self.log('Browser started (%s).' % (channel or 'chromium'))
                break
            except Exception as e:
                last = e
        if not self.ctx:
            raise RuntimeError('Could not start Chrome or Edge: %s' % last)
        self.ctx.on('close', lambda *_: setattr(self, 'ctx', None))
        self.tabs = {}

    def _tab(self, key):
        page = self.tabs.get(key)
        if page is None or page.is_closed():
            blank = [p for p in self.ctx.pages if p.url in ('about:blank', 'chrome://newtab/', 'edge://newtab/')]
            page = blank[0] if blank and blank[0] not in self.tabs.values() else self.ctx.new_page()
            self.tabs[key] = page
        return page

    def open_setup(self):
        """Open Helium 10 and the three Amazon sites so you can log in and set delivery locations once."""
        self.start()
        self._tab('H10').goto(HELIUM10_URL, wait_until='domcontentloaded')
        for code, m in MARKETS.items():
            try:
                self._tab(code).goto(m['site'], wait_until='domcontentloaded', timeout=45000)
            except Exception as e:
                self.log('%s did not load: %s' % (m['site'], e))
        self.tabs['H10'].bring_to_front()

    def check(self, keyword, code, fast_days=3, pages=1, stop=lambda: False):
        """Search one marketplace and return (location_text, rows)."""
        self.start()
        m = MARKETS[code]
        page = self._tab(code)
        rows, seen = [], set()
        for n in range(1, pages + 1):
            url = '%s/s?k=%s%s' % (m['site'], quote_plus(keyword), '&page=%d' % n if n > 1 else '')
            page.goto(url, wait_until='domcontentloaded', timeout=60000)
            self._wait_for_results(page, code, stop)
            for _ in range(6):                           # scroll so lazy parts of the page load
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(350)
            raw = page.evaluate(EXTRACT_JS)
            for r in raw:
                if r['asin'] in seen:
                    continue
                seen.add(r['asin'])
                d = delivery_days(r['delivery'])
                rows.append({
                    'market': code, 'asin': r['asin'], 'title': r['title'][:150],
                    'price': parse_price(r['price']), 'rating': parse_price(r['rating']),
                    'reviews': parse_count(r['reviews']), 'bought': parse_count(r['bought']),
                    'prime': r['prime'], 'sponsored': r['sponsored'],
                    'delivery': r['delivery'][:160], 'days': d,
                    'speed': 'fast' if d is not None and d <= fast_days else 'slow' if d is not None else 'unknown',
                    'url': '%s/dp/%s' % (m['site'], r['asin']),
                })
            if not raw or stop():
                break
            time.sleep(1.5)
        try:
            location = page.locator('#glow-ingress-line2').inner_text(timeout=3000).strip()
        except Exception:
            location = '?'
        return location, rows

    def _wait_for_results(self, page, code, stop):
        deadline = time.time() + 180
        warned = False
        while time.time() < deadline and not stop():
            if page.locator('div[data-component-type="s-search-result"]').count():
                return
            if page.locator('form[action*="validateCaptcha"], #captchacharacters').count():
                if not warned:
                    self.log('%s: Amazon is showing a "type the characters" check. Solve it in the browser window.' % code)
                    page.bring_to_front()
                    warned = True
            elif page.locator('.s-no-results, [data-component-type="s-no-results"]').count() or \
                    'did not match any products' in (page.content() or ''):
                return
            page.wait_for_timeout(1000)

    def close(self):
        for obj in (self.ctx, self.pw):
            try:
                obj and (obj.close() if obj is self.ctx else obj.stop())
            except Exception:
                pass
        self.ctx = self.pw = None


def summarize(code, location, rows, fast_days=3):
    """Plain-language summary for one marketplace."""
    m = MARKETS[code]
    organic = [r for r in rows if not r['sponsored']]
    fast = [r for r in organic if r['speed'] == 'fast']
    slow = [r for r in organic if r['speed'] == 'slow']
    unknown = [r for r in organic if r['speed'] == 'unknown']
    bought = [r['bought'] for r in organic if r['bought']]
    if not organic:
        verdict = 'No results.'
    elif len(fast) <= 4:
        verdict = 'OPPORTUNITY: only %d listing(s) deliver fast. Stock sent to Amazon (FBA) would stand out.' % len(fast)
    elif len(fast) <= 10:
        verdict = 'Some local competition: %d listings deliver fast.' % len(fast)
    else:
        verdict = 'Crowded locally: %d listings deliver fast.' % len(fast)
    lines = [
        '%s  (delivering to: %s; set it to %s)' % (m['name'].upper(), location or '?', m['location']),
        '  %d results (+%d sponsored): %d fast (<= %d days), %d slow, %d no date shown' % (
            len(organic), len(rows) - len(organic), len(fast), fast_days, len(slow), len(unknown)),
    ]
    if bought:
        lines.append('  Demand: %d listings show "bought in past month", top %s+, total about %s+/month' % (
            len(bought), f'{max(bought):,}', f'{sum(bought):,}'))
    if fast:
        rv = sorted(r['reviews'] or 0 for r in fast)
        lines.append('  Fast sellers have %s-%s reviews (median %s)' % (f'{rv[0]:,}', f'{rv[-1]:,}', f'{rv[len(rv) // 2]:,}'))
    lines.append('  ' + verdict)
    return '\n'.join(lines)
