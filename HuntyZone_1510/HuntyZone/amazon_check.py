"""Search Amazon AU / UAE / US and count how many listings deliver fast.

Fast delivery (within a few days) means the stock is already in that country: Amazon itself,
FBA or a local seller. Slow delivery (1-3 weeks) means it ships from overseas.

The checks run in their own browser profile (Chrome blocks automation of your everyday profile),
kept minimised in the background. It shows itself only when Amazon asks for a captcha.
"""
import asyncio
import contextlib
import contextvars
import datetime as dt
import os
import re
import statistics
import time
from pathlib import Path
from urllib.parse import quote_plus, urlparse

import config

APP_DIR = config.APP_DIR
PROFILE_DIR = APP_DIR / 'checker-browser'

MARKETS = {
    'AU': {'name': 'Australia', 'site': 'https://www.amazon.com.au', 'currency': 'A$',
           'location': '2000', 'expect': ('2000', 'sydney')},
    'AE': {'name': 'UAE', 'site': 'https://www.amazon.ae', 'currency': 'AED',
           'location': 'Dubai', 'expect': ('dubai',)},
    'US': {'name': 'USA', 'site': 'https://www.amazon.com', 'currency': 'US$',
           'location': '10001', 'expect': ('10001', 'new york')},
    'GB': {'name': 'UK', 'site': 'https://www.amazon.co.uk', 'currency': '£',
           'location': 'SW1A 1AA', 'expect': ('sw1a', 'london')},
    'DE': {'name': 'Germany', 'site': 'https://www.amazon.de', 'currency': '€',
           'location': '10115', 'expect': ('10115', 'berlin')},
}
for _code in MARKETS:                                                # used by tests
    if os.environ.get('PC_SITE_' + _code):
        MARKETS[_code]['site'] = os.environ['PC_SITE_' + _code].rstrip('/')
LANG = {'AE': 'en_AE', 'DE': 'en_GB'}                               # pages asked for in English, so the texts can be read

MONTHS = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}
MON = r'(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b'
DAY_MON = re.compile(r'\b(\d{1,2})\s+' + MON, re.I)            # 10 Oct (AU, UAE)
MON_DAY = re.compile(r'\b' + MON + r'\.?\s+(\d{1,2})\b', re.I)  # Oct 10 (US)
RANGE = re.compile(r'\b(\d{1,2})\s*[-–]\s*\d{1,2}\s+' + MON, re.I)   # 15 - 16 Oct: the first day has no month
# German pages (in case amazon.de ignores the English request): "Lieferung Dienstag, 21. Oktober", "morgen"
MONTHS_DE = {'jan': 1, 'feb': 2, 'mär': 3, 'mrz': 3, 'apr': 4, 'mai': 5, 'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9, 'okt': 10,
             'nov': 11, 'dez': 12}
DAY_MON_DE = re.compile(r'\b(\d{1,2})\.?\s+(jan(?:uar)?|feb(?:ruar)?|mär(?:z)?|mrz|apr(?:il)?|mai|jun[i]?|jul[i]?|aug(?:ust)?|'
                        r'sep(?:t(?:ember)?)?|okt(?:ober)?|nov(?:ember)?|dez(?:ember)?)\b', re.I)

# Text that means the item ships from another country (Amazon Global Store, "ships from abroad"...)
INTERNATIONAL = re.compile(r'international delivery|international items|ships from abroad|global store|'
                           r'ships from outside|imported from|from overseas|international shipping|'
                           r'versand aus dem ausland|internationale[rn]? (?:versand|lieferung)', re.I)
LOCAL_MAX_DAYS = 9          # slower than this (and no "international" text) = probably shipped from abroad
SIGNIN = re.compile(r'/(?:ap/signin|ax/claim)\b')        # Amazon's sign-in pages

# True while the scheduler runs background work: then a captcha must never pop the window up.
BACKGROUND = contextvars.ContextVar('pc_background', default=False)


class BlockedError(Exception):
    """Amazon did not give us a normal page. `status` is stored with the snapshot, so it shows as a gap."""
    status = 'blocked'

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class CaptchaRequired(BlockedError):
    """A captcha appeared during background work. The user can solve it later from the app."""

    def __init__(self, message):
        super().__init__('captcha', message)


class CaptchaTimeout(BlockedError):
    def __init__(self, message):
        super().__init__('captcha', message)


BLOCK_TEXT = {
    'blocked': '%s showed an error page instead of results. It may be limiting requests; try again later.',
    'geo_redirect': '%s sent the browser to a different Amazon site.',
    'timeout': '%s did not load in 60 seconds.',
    'offline': 'No internet connection while opening %s.',
}


def _same_page(got, want):
    """True if the browser is still on the page we asked for (ignoring ref= tails and query strings)."""
    def key(u):
        p = urlparse(u or '')
        path = re.sub(r'/ref=[^/]*$', '', p.path).rstrip('/')
        return (p.hostname or '').lower(), path
    g, w = key(got), key(want)
    if g[0] != w[0]:
        return False
    return g[1] == w[1] or g[1].startswith(w[1] + '/') or (w[1] and g[1].endswith(w[1]))   # /dp/X -> /Name/dp/X


# Runs inside the page: Amazon's error and robot pages.
BLOCK_JS = r"""
() => {
  const title = (document.title || '').toLowerCase();
  const body = ((document.body && document.body.innerText) || '').slice(0, 4000).toLowerCase();
  return {
    dog: /sorry! something went wrong|something went wrong on our end|meet the dogs of amazon/.test(body)
         || !!document.querySelector('img[alt*="Dogs of Amazon" i], a[href*="dogsofamazon"]'),
    robot: title.includes('robot check') || /not a robot|to discuss automated access|api-services-support@amazon/.test(body),
  };
}
"""

# Runs inside a search page: "1-48 of over 2,000 results for ..." and the no-results message.
RESULT_INFO_JS = r"""
() => {
  const bar = document.querySelector('[data-component-type="s-result-info-bar"], .s-desktop-toolbar, .s-breadcrumb');
  const body = ((document.body && document.body.innerText) || '').slice(0, 6000);
  return {text: bar ? (bar.innerText || bar.textContent || '').slice(0, 300) : '',
          no_results: /no results for|did not match any products|try checking your spelling/i.test(body)};
}
"""


def parse_results_info(text):
    """'1-48 of over 2,000 results for "x"' -> (2000, True). (None, None) when Amazon shows no count."""
    t = (text or '').replace('\xa0', ' ')
    m = re.search(r'of\s+(over\s+|more than\s+)?([\d,.]+)\s+results', t, re.I) or \
        re.search(r'^\s*(over\s+)?([\d,.]+)\s+results', t, re.I)
    if not m:
        return None, None
    n = parse_count(m.group(2))
    return n, bool(m.group(1)) if n is not None else None


def parse_health(rows):
    """Share of listings where the page parser found a title and a price or a delivery text (layout canary)."""
    if not rows:
        return None
    good = sum(1 for r in rows if r.get('title') and (r.get('price') or r.get('delivery')))
    return round(good / len(rows), 2)


def delivery_days(text, today=None):
    """Days until the fastest delivery date in Amazon's delivery text, or None if it shows no date."""
    if not text:
        return None
    today = today or dt.date.today()
    t = text.lower()
    days = []
    if re.search(r'\btoday\b|\bovernight\b|\bwithin \d+ hours?\b|\bheute\b', t):
        days.append(0)
    if 'tomorrow' in t or re.search(r'(?<!über)morgen\b', t):
        days.append(1)
    if 'übermorgen' in t:
        days.append(2)
    found = [(int(d), m) for d, m in DAY_MON.findall(t)] + [(int(d), m) for m, d in MON_DAY.findall(t)] + \
            [(int(d), m) for d, m in RANGE.findall(t)]
    if not found:
        found = [(int(d), m) for d, m in DAY_MON_DE.findall(t)]
    for day, mon in found:
        month = MONTHS.get(mon[:3]) or MONTHS_DE.get(mon[:3])
        if not month:
            continue
        try:
            date = dt.date(today.year, month, day)
        except ValueError:
            continue
        if date < today - dt.timedelta(days=7):                # "3 Jan" seen in December
            date = dt.date(today.year + 1, month, day)
        days.append((date - today).days)
    return min(days) if days else None


def classify(row, fast_days=3, local_days=LOCAL_MAX_DAYS):
    """Speed (fast / slow / unknown) and origin (local / overseas / unknown) of one listing, with the reason."""
    d, text = row.get('days'), row.get('delivery') or ''
    intl = bool(row.get('intl')) or bool(INTERNATIONAL.search(text))
    if intl:
        origin, why = 'overseas', 'international delivery'
    elif d is not None and d <= local_days:
        origin, why = 'local', 'arrives today' if d == 0 else 'arrives tomorrow' if d == 1 else 'arrives in %d days' % d
    elif d is not None:
        origin, why = 'overseas', 'takes %d+ days' % d
    elif row.get('prime') or row.get('first_order') or 'first order' in text.lower():
        origin, why = 'local', 'Prime / shipped by Amazon' if row.get('prime') else 'free first-order delivery (shipped by Amazon)'
    else:
        origin, why = 'unknown', 'no delivery date shown'
    speed = 'fast' if d is not None and d <= fast_days and not intl else 'slow' if d is not None or intl else 'unknown'
    first_order = bool(row.get('first_order')) or 'first order' in text.lower()
    return {**row, 'intl': intl, 'first_order': first_order, 'speed': speed, 'origin': origin, 'why': why}


def parse_count(text):
    """'1,234' -> 1234, '1.2K' -> 1200, '2K+' -> 2000."""
    m = re.search(r'(\d[\d,.]*)\s*([kKmM]?)', str(text or ''))
    if not m:
        return None
    try:
        n = float(m.group(1).replace(',', ''))
    except ValueError:
        return None
    return int(n * {'k': 1e3, 'm': 1e6}.get(m.group(2).lower(), 1))


def parse_price(text):
    """'A$ 1,299.00' -> 1299.0; a German price like '12,99 €' or '1.299,99 €' -> 12.99 / 1299.99."""
    m = re.search(r'\d[\d.,]*', (text or '').replace('\xa0', ' ').replace('\u202f', ''))
    if not m:
        return None
    s = m.group(0).rstrip('.,')
    if ',' in s and '.' in s:
        s = s.replace('.', '').replace(',', '.') if s.rfind(',') > s.rfind('.') else s.replace(',', '')
    elif ',' in s:
        s = s.replace(',', '.') if re.fullmatch(r'\d+,\d{1,2}', s) else s.replace(',', '')
    elif re.fullmatch(r'\d{1,3}(?:\.\d{3})+', s):
        s = s.replace('.', '')
    try:
        return float(s)
    except ValueError:
        return None


# Runs inside the Amazon page: one record per search result.
EXTRACT_JS = r"""
() => [...document.querySelectorAll('div[data-component-type="s-search-result"][data-asin]')]
  .filter(e => e.dataset.asin)
  .map(e => {
    const q = s => e.querySelector(s);
    const txt = s => { const x = q(s); return x ? (x.innerText || x.textContent || '').trim() : ''; };
    const all = e.innerText || e.textContent || '';
    const star = q('[aria-label*="out of 5"]') || q('i[class*="a-star"] .a-icon-alt');
    const labels = [...e.querySelectorAll('[aria-label]')].map(x => x.getAttribute('aria-label'))
      .filter(a => /rating|review/i.test(a) && !/out of 5/i.test(a));
    let delivery = [...e.querySelectorAll('[data-cy="delivery-recipe"], .udm-primary-delivery-message, .udm-secondary-delivery-message')]
      .map(x => (x.innerText || x.textContent || '').trim()).filter(Boolean).join(' | ');
    if (!delivery) delivery = all.split('\n').filter(l => /deliver|get it|arrives|ships/i.test(l)).join(' | ');
    const bought = (all.match(/([\d.,]+\s*[KkMm]?\+?)\s+bought in past month/i) || [])[1] || '';
    const badge = (all.split('\n').find(l => /bought in (the )?past month/i.test(l)) || '').trim();
    const img = q('img.s-image');
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
      badge: badge.slice(0, 80),
      image: img ? (img.getAttribute('src') || '') : '',
      intl: /international delivery|international items|ships from abroad|global store|ships from outside/i.test(all),
    };
  })
"""

# Runs inside a search page: the ads that are not result cards (brand banner, video ads, sponsored carousels).
ADS_JS = r"""
() => {
  const out = [];
  const asinsOf = w => [...new Set([...w.querySelectorAll('[data-asin]')].map(x => x.getAttribute('data-asin'))
    .concat([...w.querySelectorAll('a[href*="/dp/"]')].map(a => ((a.getAttribute('href') || '').match(/\/dp\/([A-Z0-9]{10})/) || [])[1]))
    .filter(a => a && /^[A-Z0-9]{10}$/.test(a)))].slice(0, 12);
  for (const w of document.querySelectorAll('[cel_widget_id], [data-cel-widget]')) {
    const id = w.getAttribute('cel_widget_id') || w.getAttribute('data-cel-widget') || '';
    const kind = /sb-|_sb_|sponsored.?brand|top-slot/i.test(id) ? 'brand_banner' : /VIDEO/i.test(id) ? 'video'
      : /FEATURED_ASINS|THEMATIC|CAROUSEL/i.test(id) ? 'carousel' : null;
    if (!kind || out.some(o => o.el.contains(w))) continue;
    const text = (w.innerText || w.textContent || '').replace(/\s+/g, ' ').trim();
    if (!/sponsored/i.test(text)) continue;
    out.push({el: w, kind, id: id.slice(0, 80), text: text.slice(0, 300), asins: asinsOf(w),
              alts: [...w.querySelectorAll('img[alt]')].map(i => i.getAttribute('alt')).filter(Boolean).slice(0, 8)});
  }
  return out.map(({el, ...o}) => o);
}
"""
SPONSORED_PREFIX = re.compile(r'^\s*Sponsored Ad\s*[–—-]\s*', re.I)
SHOP_BRAND = re.compile(r'\bShop\s+(.{2,40}?)(?=\s+(?:Previous page|Next page|Shop now|Learn more)\b|\s*\||$)')


def parse_ads(raw):
    """The page's ads that are not result cards -> [{'kind': 'brand_banner' | 'video' | 'carousel', 'brand' (the
    banner's "Shop <brand>", else None), 'asins', 'titles'}]."""
    out = []
    for a in raw or []:
        if a.get('kind') not in ('brand_banner', 'video', 'carousel'):
            continue
        alts = [SPONSORED_PREFIX.sub('', str(x)).strip() for x in a.get('alts') or [] if x]
        brand = None
        if a['kind'] == 'brand_banner':
            m = SHOP_BRAND.search(a.get('text') or '')
            brand = (m.group(1).strip() if m else None) or next((x for x in alts if 0 < len(x.split()) <= 3), None)
        out.append({'kind': a['kind'], 'brand': brand, 'asins': [x for x in a.get('asins') or [] if x][:12],
                    'titles': [x for x in dict.fromkeys(alts) if x != brand and len(x.split()) > 2][:8]})
    return out


class Checker:
    """One background browser shared by all checks. All methods run on the engine's event loop."""

    def __init__(self, log=print):
        self.log = log
        self.pw = None
        self.ctx = None
        self.home = None
        self._shows = 0                              # captchas currently waiting for the user
        self.pinned = False                          # the user pressed "Show"
        self.throttle = None                         # set by the app: pacing, budgets and cooldowns per source
        self.state = 'stopped'                       # stopped / starting / ready / error
        self.error = ''
        self.locations = {c: {'ok': None, 'text': ''} for c in MARKETS}
        self._lock = asyncio.Lock()
        self._loc_lock = {c: asyncio.Lock() for c in MARKETS}
        self._loc_any = asyncio.Lock()                       # one location popup at a time

    # ---------- browser ----------
    async def start(self):
        async with self._lock:
            if self.ctx:
                return
            self.state = 'starting'
            try:
                from playwright.async_api import async_playwright
                if not self.pw:
                    self.pw = await async_playwright().start()
                PROFILE_DIR.mkdir(parents=True, exist_ok=True)
                opts = dict(user_data_dir=str(PROFILE_DIR), headless=False, viewport={'width': 1280, 'height': 900},
                            locale='en-US', ignore_default_args=['--enable-automation'],
                            args=['--disable-blink-features=AutomationControlled', '--no-first-run',
                                  '--disable-background-timer-throttling', '--disable-backgrounding-occluded-windows',
                                  '--disable-renderer-backgrounding',
                                  '--no-default-browser-check', '--disable-features=Translate'])
                exe = os.environ.get('PRODUCT_CHECKER_BROWSER')
                tries = [dict(executable_path=exe)] if exe else [dict(channel='chrome'), dict(channel='msedge'), {}]
                last = None
                for extra in tries:
                    try:
                        self.ctx = await self.pw.chromium.launch_persistent_context(**opts, **extra)
                        break
                    except Exception as e:
                        last = e
                if not self.ctx:
                    raise RuntimeError('Could not start Chrome or Edge (%s)' % str(last).splitlines()[0])
                self.ctx.on('close', lambda *_: self._closed())
                await self.ctx.route('**/*', self._route)
                self.home = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()
                await self.hide()
                self.state, self.error = 'ready', ''
                self.log('Background browser ready.')
            except Exception as e:
                self.state, self.error = 'error', str(e)
                raise

    @property
    def visible(self):
        return self.pinned or self._shows > 0

    def _closed(self):
        self.ctx = self.home = None
        self.state = 'stopped'
        self._shows, self.pinned = 0, False

    async def _route(self, route):
        req = route.request
        rt = req.resource_type
        # Skip pictures, video and fonts: pages load much faster. Captcha pictures and the visible window still load.
        if not self.visible and (rt in ('media', 'font') or (rt == 'image' and 'captcha' not in req.url.lower())):
            await route.abort()
        else:
            await route.continue_()

    async def _window(self, state):
        try:
            page = self.home or self.ctx.pages[0]
            cdp = await self.ctx.new_cdp_session(page)
            win = await cdp.send('Browser.getWindowForTarget')
            await cdp.send('Browser.setWindowBounds', {'windowId': win['windowId'], 'bounds': {'windowState': state}})
            await cdp.detach()
        except Exception:
            pass

    async def show(self, page=None, user=True):
        """Bring the checker window up: pinned by the user, or counted for each captcha waiting."""
        await self.start()
        if user:
            self.pinned = True
        else:
            self._shows += 1
        await self._window('normal')
        try:
            await (page or self.home).bring_to_front()
        except Exception:
            pass

    async def hide(self):
        self.pinned = False
        if self.ctx and not self.visible:
            await self._window('minimized')

    async def _release(self):
        """A captcha is done. Minimise again once no other captcha waits and the user did not pin the window."""
        self._shows = max(0, self._shows - 1)
        if self.ctx and not self.visible:
            await self._window('minimized')

    async def close(self):
        try:
            if self.ctx:
                await self.ctx.close()
        except Exception:
            pass
        try:
            if self.pw:
                await self.pw.stop()
        except Exception:
            pass
        self.ctx = self.pw = self.home = None
        self.state = 'stopped'

    # ---------- page helpers ----------
    def slot(self, source):
        """The throttle's slot for one request (or nothing when no throttle is set, e.g. in tests)."""
        return self.throttle.slot(source) if self.throttle else contextlib.nullcontext()

    async def _goto(self, page, url, code):
        """One paced page load. Blocks and captchas cool the marketplace down (see throttle.py)."""
        source = 'amazon:' + code
        async with self.slot(source):
            try:
                status = await self._goto_raw(page, url, code)
            except BlockedError as e:
                if self.throttle and e.status in ('captcha', 'blocked', 'geo_redirect', 'empty_suspect'):
                    self.throttle.report(source, e.status, str(e)[:120])
                raise
        if self.throttle:
            self.throttle.report(source, 'ok')
        return status

    async def _goto_raw(self, page, url, code):
        """Open a page, get past Amazon's interstitials, and raise BlockedError for anything that is not a real page."""
        name = MARKETS[code]['name']
        try:
            resp = await page.goto(url, wait_until='domcontentloaded', timeout=60000)
        except Exception as e:
            msg = str(e)
            if 'timeout' in type(e).__name__.lower() or 'Timeout' in msg[:120]:
                raise BlockedError('timeout', BLOCK_TEXT['timeout'] % name)
            if 'net::ERR_' in msg:
                raise BlockedError('offline', BLOCK_TEXT['offline'] % name)
            raise
        await self._get_past_blocks(page, code)
        if not _same_page(page.url, url) and not SIGNIN.search(urlparse(page.url).path):   # "Continue shopping" can land on the home page instead
            try:
                resp = await page.goto(url, wait_until='domcontentloaded', timeout=60000)
            except Exception as e:                      # noqa: BLE001
                if 'timeout' in type(e).__name__.lower() or 'Timeout' in str(e)[:120]:
                    raise BlockedError('timeout', BLOCK_TEXT['timeout'] % name)
                raise
            await self._get_past_blocks(page, code)
        kind = await self._block_kind(page, code, resp.status if resp else None)
        if kind:
            raise BlockedError(kind, BLOCK_TEXT[kind] % name)
        return resp.status if resp else None

    async def _block_kind(self, page, code, http):
        want = (urlparse(MARKETS[code]['site']).hostname or '').lower()
        got = (urlparse(page.url).hostname or '').lower()
        base = want[4:] if want.startswith('www.') else want
        if got and base and got != base and not got.endswith('.' + base):
            return 'geo_redirect'
        if http == 404:                                  # a page that does not exist (its dog picture is not a block)
            return None
        try:
            info = await page.evaluate(BLOCK_JS)
        except Exception:
            info = {}
        if info.get('dog') or info.get('robot') or (http or 0) >= 500:
            return 'blocked'
        return None

    async def _get_past_blocks(self, page, code):
        """Handle Amazon's "continue shopping" page and captcha. Waits up to 3 minutes for a captcha."""
        deadline = time.time() + 180
        shown = False
        clicks = 0
        while True:
            btn = page.locator('button:has-text("Continue shopping"), input[value*="Continue shopping" i]')
            if clicks < 3 and await btn.count():
                clicks += 1
                try:                                     # wait for the next page, not the one we are on
                    async with page.expect_navigation(wait_until='domcontentloaded', timeout=15000):
                        await btn.first.click()
                except Exception:                        # noqa: BLE001 - no navigation: look again
                    pass
                continue
            captcha = await page.locator('form[action*="validateCaptcha"], #captchacharacters').count()
            if not captcha:
                if shown:
                    await self._release()
                return
            if BACKGROUND.get():                         # never pop the window up for background work
                raise CaptchaRequired('%s asked for a captcha during the automatic refresh.' % MARKETS[code]['name'])
            if not shown:
                self.log('%s: Amazon wants you to type the characters from a picture. The browser window is open, '
                         'please solve it there.' % MARKETS[code]['name'])
                await self.show(page, user=False)
                shown = True
                try:
                    await page.reload()
                except Exception:
                    pass
            if time.time() > deadline:
                await self._release()
                raise CaptchaTimeout('Amazon captcha was not solved in 3 minutes')
            await page.wait_for_timeout(1500)

    async def _location_text(self, page):
        """The 'Deliver to' text in Amazon's header. Both lines: the new UAE header says "Delivering to Dubai" in the
        first line and "Update location" in the second."""
        try:
            parts = await page.evaluate("""() => ['#glow-ingress-line1', '#glow-ingress-line2']
              .map(s => (document.querySelector(s) || {}).innerText || '').map(t => t.trim()).filter(Boolean)""")
            if not parts:
                parts = [await page.locator('#nav-global-location-popover-link').inner_text(timeout=2500)]
            return ' '.join(' '.join(parts).split())
        except Exception:
            return ''

    def _location_ok(self, code, text):
        return any(k in text.lower() for k in MARKETS[code]['expect'])

    async def ensure_location(self, code, page=None, manual=None):
        """Make sure Amazon delivers to the chosen place (postcode 2000, Dubai, 10001). Returns (ok, text).

        Only one location is set at a time, in a tab brought to the front: Amazon's location popups and their
        suggestion lists only work in the focused tab. If the automatic attempt fails and you started the work
        yourself (not the background refresh), the window opens so you can pick the place; we wait up to 2 minutes."""
        await self.start()
        manual = (not BACKGROUND.get()) if manual is None else manual
        async with self._loc_lock[code]:
            if page is not None and self.locations[code]['ok']:      # another check just set it
                await self._goto(page, page.url, code)
                return True, await self._location_text(page)
            own = page is None
            page = page or await self.ctx.new_page()
            m = MARKETS[code]
            try:
                async with self._loc_any:                             # one popup at a time, across marketplaces
                    if own or not page.url.startswith(m['site']):
                        await self._goto(page, m['site'] + '/', code)
                    text = await self._location_text(page)
                    if not self._location_ok(code, text):
                        self.log('%s: setting delivery location to %s...' % (m['name'], m['location']))
                        for attempt in range(2):
                            problem = await self._set_location(page, code)
                            await self._goto(page, page.url, code)
                            text = await self._location_text(page)
                            if self._location_ok(code, text):
                                break
                        if not self._location_ok(code, text) and manual:
                            text = await self._ask_location(page, code, problem)
                    ok = self._location_ok(code, text)
                    self.locations[code] = {'ok': ok, 'text': text}
                    if ok:
                        self.log('%s: delivery location is %s ✓' % (m['name'], m['location']))
                    else:
                        self.log('%s: could not set the delivery location (it shows "%s"%s). Press "Set locations now" '
                                 'in Settings, then choose %s in the window.' % (
                                     m['name'], text or '?', ('; the form said: ' + problem) if problem else '', m['location']))
                    return ok, text
            finally:
                if own:
                    await page.close()

    async def _ask_location(self, page, code, problem=None, wait_s=120):
        """Show the window with Amazon's location popup open and wait until you have picked the place."""
        m = MARKETS[code]
        self.log('%s: please choose %s in the browser window that just opened and press Apply. '
                 'The app waits up to 2 minutes.%s' % (m['name'], m['location'],
                                                      (' (Amazon said: %s)' % problem) if problem else ''))
        await self.show(page, user=False)
        try:
            await self._open_location_popup(page)
            deadline = time.time() + wait_s
            while time.time() < deadline:
                await page.wait_for_timeout(2000)
                try:
                    text = await self._location_text(page)
                except Exception:
                    text = ''
                if self._location_ok(code, text):
                    return text
            return await self._location_text(page)
        finally:
            await self._release()

    async def _open_location_popup(self, page):
        for sel in ('#nav-global-location-popover-link', '#glow-ingress-block', 'text=/Update location|Deliver(ing)? to/i'):
            try:
                el = page.locator(sel).first
                if await el.count():
                    await el.click(timeout=6000)
                    return True
            except Exception:
                continue
        return False

    async def _pick_option(self, page, name, timeout=6000):
        """Click the suggestion whose text is `name` (Amazon's city lists). True if one was clicked."""
        opts = page.locator('[role="option"], [role="listbox"] li, .a-dropdown-item, .a-popover li, '
                            'ul[id*="GLUX" i] li, .autocomplete li, ul li a',
                            has_text=re.compile(r'^\s*%s\s*$' % re.escape(name), re.I))
        try:
            await opts.first.wait_for(state='visible', timeout=timeout)
            await opts.first.click()
            return True
        except Exception:
            return False

    async def _form_problem(self, page):
        try:
            t = await page.evaluate("""() => [...document.querySelectorAll('.a-alert-content, [role="alert"], .a-color-error')]
              .filter(e => e.offsetParent !== null).map(e => (e.innerText || '').trim()).filter(Boolean)[0] || ''""")
            return ' '.join(str(t).split())[:120] or None
        except Exception:
            return None

    async def _set_location(self, page, code):
        """Fill in Amazon's location popup once. Returns the form's error text if it showed one, else None."""
        loc = MARKETS[code]['location']
        try:
            await page.bring_to_front()
        except Exception:
            pass
        if not await self._open_location_popup(page):
            return 'the "Deliver to" link was not found'
        fields = page.locator('#GLUXZipUpdateInput, #GLUXPostalCodeWithCity_PostalCodeInput, #GLUXCityList, '
                              'select[id*="GLUX"], input[placeholder*="city" i], input[aria-label*="city" i], '
                              '[role="combobox"], .a-popover-wrapper')
        try:
            await fields.first.wait_for(timeout=8000)
            await page.wait_for_timeout(400)
        except Exception:
            return 'the location popup did not open'
        try:
            if await page.locator('#GLUXZipUpdateInput').count():                       # USA: ZIP code
                await page.fill('#GLUXZipUpdateInput', loc)
                await page.locator('#GLUXZipUpdate input, #GLUXZipUpdate').first.click()
            elif await page.locator('#GLUXPostalCodeWithCity_PostalCodeInput').count():  # Australia: postcode + suburb
                box = page.locator('#GLUXPostalCodeWithCity_PostalCodeInput').first
                await box.click()
                await box.fill('')
                await box.press_sequentially(loc, delay=60)          # key presses make Amazon load the suburbs
                try:
                    await page.locator('#GLUXPostalCodeWithCity_DropdownList option:not(.a-prompt)').first.wait_for(
                        state='attached', timeout=8000)
                except Exception:
                    pass
                dd = page.locator('#GLUXPostalCodeWithCity_DropdownButton, #GLUXPostalCodeWithCity_CityValue')
                if await dd.count():
                    await dd.first.click()
                    opt = page.locator('a[id^="GLUXPostalCodeWithCity_DropdownList"]', has_text=re.compile('sydney', re.I))
                    if await opt.count():
                        await opt.first.click(timeout=8000)
                    else:
                        await page.locator('a[id^="GLUXPostalCodeWithCity_DropdownList"]').first.click(timeout=8000)
                else:
                    await self._pick_option(page, 'Sydney', 2500)
                await self._apply(page)
            else:                                                                        # UAE: a city
                sel = page.locator('select[id*="GLUX"], select[name*="city" i]')
                box = page.locator('input[placeholder*="city" i], input[aria-label*="city" i], #GLUXCityList input, '
                                   '[role="combobox"]').first
                if await sel.count():                                                    # older form: a list
                    await sel.first.select_option(label=loc)
                elif await box.count():                                                  # new form: type and pick
                    await box.click()
                    await box.fill('')
                    await box.press_sequentially(loc, delay=60)
                    if not await self._pick_option(page, loc):
                        await box.press('ArrowDown')
                        await box.press('Enter')
                else:
                    await page.locator('.a-popover-wrapper >> text=/Select.*city|Choose.*city|City/i').first.click(timeout=4000)
                    await self._pick_option(page, loc, 4000)
                await page.wait_for_timeout(300)
                await self._apply(page)
            await page.wait_for_timeout(1500)
            problem = await self._form_problem(page)
            done = page.locator('#GLUXConfirmClose, .a-popover-footer input[name="glowDoneButton"], '
                                'button[name="glowDoneButton"]')
            if await done.count():
                await done.first.click()
            await page.wait_for_timeout(800)
            return problem
        except Exception as e:
            return 'form not as expected (%s)' % str(e).splitlines()[0][:80]

    async def _apply(self, page):
        btn = page.get_by_role('button', name=re.compile(r'^\s*(Apply|Done|Confirm|Save)\s*$', re.I))
        if await btn.count():
            await btn.first.click(timeout=4000)
            return
        await page.locator('#GLUXPostalCodeWithCityApplyButton input, #GLUXPostalCodeWithCityApplyButton, '
                           '.a-popover-wrapper >> text=/^(Apply|Done|Confirm|Save)$/i').first.click(timeout=4000)

    # ---------- the check ----------
    async def check(self, keyword, code, fast_days=3, pages=1, progress=None, local_days=LOCAL_MAX_DAYS):
        """Search one marketplace. Returns a summary dict with every listing, plus how the page looked:
        status (ok / empty / empty_suspect), Amazon's result count and the parser's health."""
        await self.start()
        m = MARKETS[code]
        page = await self.ctx.new_page()
        rows, seen = [], set()
        location = ''
        info = {}
        ads = None
        today = config.market_today(code)
        try:
            for n in range(1, pages + 1):
                url = search_url(code, keyword, n)
                await self._goto(page, url, code)
                if n == 1:
                    location = await self._location_text(page)
                    if not self._location_ok(code, location) and self.locations[code]['ok'] is not False:
                        ok, location = await self.ensure_location(code, page)
                        await self._goto(page, url, code)
                    else:
                        self.locations[code] = {'ok': self._location_ok(code, location), 'text': location}
                try:
                    await page.wait_for_selector('div[data-component-type="s-search-result"]', timeout=8000)
                except Exception:
                    pass
                raw = await page.evaluate(EXTRACT_JS)
                if n == 1:
                    try:
                        info = await page.evaluate(RESULT_INFO_JS)
                    except Exception:
                        info = {}
                    try:
                        ads = parse_ads(await page.evaluate(ADS_JS))
                    except Exception:
                        ads = None
                for r in raw:
                    if r['asin'] in seen:
                        continue
                    seen.add(r['asin'])
                    rows.append(search_row(r, code, today))
                if progress:
                    progress('%s: page %d read, %d listings' % (m['name'], n, len(rows)))
                if not raw:
                    break
        finally:
            await page.close()
        s = summarize(code, keyword, location, rows, fast_days, local_days)
        total, over = parse_results_info(info.get('text'))
        s.update(results_total=total, results_over=over, parse_health=parse_health(rows), ads=ads)
        if not rows:
            # no listings and no "no results" message: probably a soft block or a changed page, not zero sellers
            s['status'] = 'empty' if info.get('no_results') else 'empty_suspect'
        elif s['parse_health'] is not None and len(rows) >= 8 and s['parse_health'] < 0.5:
            s['layout_warning'] = True
            self.log('%s: the search page looks different from usual; some numbers may be missing.' % m['name'])
        return s


def search_row(r, code, today):
    """One search result as the page gave it (EXTRACT_JS) -> the row kept: numbers parsed, delivery days counted."""
    from engine import badge
    return {'market': code, 'asin': r['asin'], 'title': r['title'][:200],
            'price': parse_price(r['price']), 'rating': parse_price(r['rating']), 'reviews': parse_count(r['reviews']),
            'bought': badge.parse_badge(r.get('badge')) or parse_count(r['bought']),
            'prime': r['prime'], 'sponsored': r['sponsored'], 'delivery': r['delivery'][:240],
            'days': delivery_days(r['delivery'], today), 'intl': bool(r.get('intl')),
            'url': '%s/dp/%s' % (MARKETS[code]['site'], r['asin']), 'image': r['image']}


def with_lang(url, code):
    """Ask amazon.ae and amazon.de for the English page (the parsers read English texts)."""
    lang = LANG.get(code)
    return '%s%slanguage=%s' % (url, '&' if '?' in url else '?', lang) if lang else url


def search_url(code, keyword, page=1):
    """Amazon search address. amazon.ae and amazon.de are asked for English, so delivery texts can be read."""
    m = MARKETS[code]
    return with_lang('%s/s?k=%s%s' % (m['site'], quote_plus(keyword), '&page=%d' % page if page > 1 else ''), code)


def _median(v):
    v = sorted(v)
    return v[len(v) // 2] if v else None


def level_for(fast, organic):
    """opportunity (4 or fewer fast listings), some (up to 10), crowded, or none (no results)."""
    if not organic:
        return 'none'
    return 'opportunity' if (fast or 0) <= 4 else 'some' if fast <= 10 else 'crowded'


def summarize(code, keyword, location, rows, fast_days=3, local_days=LOCAL_MAX_DAYS, checked_at=None):
    """Counts and verdict for one marketplace. Rows are (re)classified with the given day limits."""
    m = MARKETS[code]
    rows = [classify(r, fast_days, local_days) for r in rows]
    organic = [r for r in rows if not r['sponsored']]
    fast = [r for r in organic if r['speed'] == 'fast']
    slow = [r for r in organic if r['speed'] == 'slow']
    local = [r for r in organic if r['origin'] == 'local']
    abroad = [r for r in organic if r['origin'] == 'overseas']
    intl = [r for r in abroad if r['intl']]
    bought = [r['bought'] for r in organic if r['bought']]
    prices = [r['price'] for r in fast if r['price']]
    page_prices = [r['price'] for r in organic if r.get('price')]
    abroad_txt = ' %d of %d ship from overseas.' % (len(abroad), len(organic)) if abroad else ''
    level = level_for(len(fast), len(organic))
    if level == 'none':
        text = 'No results found.'
    elif level == 'opportunity':
        text = 'Only %d listing%s deliver fast.%s Stock sent to Amazon (FBA) would stand out.' % (
            len(fast), '' if len(fast) == 1 else 's', abroad_txt)
    elif level == 'some':
        text = '%d listings deliver fast. Some local competition.%s' % (len(fast), abroad_txt)
    else:
        text = '%d listings deliver fast. Crowded with local stock.%s' % (len(fast), abroad_txt)
    from engine import sellers
    return {
        'per_seller': sellers.per_seller(rows, code),
        'market': code, 'name': m['name'], 'currency': m['currency'], 'keyword': keyword,
        'location': location, 'location_ok': any(k in (location or '').lower() for k in m['expect']),
        'location_wanted': m['location'], 'fast_days': fast_days, 'local_days': local_days,
        'total': len(organic), 'sponsored': len(rows) - len(organic), 'fast': len(fast), 'slow': len(slow),
        'unknown': len(organic) - len(fast) - len(slow),
        'local': len(local), 'local_other': len(local) - len([r for r in local if r['speed'] == 'fast']),
        'overseas': len(abroad), 'overseas_intl': len(intl), 'overseas_slow': len(abroad) - len(intl),
        'origin_unknown': len(organic) - len(local) - len(abroad),
        'local_share': len(local) / len(organic) if organic else None,
        'fast_reviews_median': _median([r['reviews'] or 0 for r in fast]),
        'fast_price_min': min(prices) if prices else None, 'fast_price_max': max(prices) if prices else None,
        'price_med': round(statistics.median(page_prices), 2) if page_prices else None,
        'bought_listings': len(bought), 'bought_top': max(bought) if bought else None,
        'bought_total': sum(bought) if bought else None,
        'level': level, 'verdict': text, 'checked_at': checked_at or time.time(), 'rows': rows,
        'search_url': '%s/s?k=%s' % (m['site'], quote_plus(keyword)),
    }
