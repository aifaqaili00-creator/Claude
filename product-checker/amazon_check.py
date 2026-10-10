"""Search Amazon AU / UAE / US and count how many listings deliver fast.

Fast delivery (within a few days) means the stock is already in that country: Amazon itself,
FBA or a local seller. Slow delivery (1-3 weeks) means it ships from overseas.

The checks run in their own browser profile (Chrome blocks automation of your everyday profile),
kept minimised in the background. It shows itself only when Amazon asks for a captcha.
"""
import asyncio
import contextvars
import datetime as dt
import os
import re
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
}
for _code, _site in (('AU', 'PC_SITE_AU'), ('AE', 'PC_SITE_AE'), ('US', 'PC_SITE_US')):   # used by tests
    if os.environ.get(_site):
        MARKETS[_code]['site'] = os.environ[_site].rstrip('/')

MONTHS = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}
MON = r'(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b'
DAY_MON = re.compile(r'\b(\d{1,2})\s+' + MON, re.I)            # 10 Oct (AU, UAE)
MON_DAY = re.compile(r'\b' + MON + r'\.?\s+(\d{1,2})\b', re.I)  # Oct 10 (US)
RANGE = re.compile(r'\b(\d{1,2})\s*[-–]\s*\d{1,2}\s+' + MON, re.I)   # 15 - 16 Oct: the first day has no month

# Text that means the item ships from another country (Amazon Global Store, "ships from abroad"...)
INTERNATIONAL = re.compile(r'international delivery|international items|ships from abroad|global store|'
                           r'ships from outside|imported from|from overseas|international shipping', re.I)
LOCAL_MAX_DAYS = 9          # slower than this (and no "international" text) = probably shipped from abroad

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
    if re.search(r'\btoday\b|\bovernight\b|\bwithin \d+ hours?\b', t):
        days.append(0)
    if 'tomorrow' in t:
        days.append(1)
    found = [(int(d), m) for d, m in DAY_MON.findall(t)] + [(int(d), m) for m, d in MON_DAY.findall(t)] + \
            [(int(d), m) for d, m in RANGE.findall(t)]
    for day, mon in found:
        month = MONTHS[mon[:3]]
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
    m = re.search(r'(\d[\d,]*\.?\d*)', (text or '').replace('\xa0', ' '))
    try:
        return float(m.group(1).replace(',', '')) if m else None
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


class Checker:
    """One background browser shared by all checks. All methods run on the engine's event loop."""

    def __init__(self, log=print):
        self.log = log
        self.pw = None
        self.ctx = None
        self.home = None
        self._shows = 0                              # captchas currently waiting for the user
        self.pinned = False                          # the user pressed "Show"
        self.state = 'stopped'                       # stopped / starting / ready / error
        self.error = ''
        self.locations = {c: {'ok': None, 'text': ''} for c in MARKETS}
        self._lock = asyncio.Lock()
        self._loc_lock = {c: asyncio.Lock() for c in MARKETS}

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
    async def _goto(self, page, url, code):
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
                await btn.first.click()
                await page.wait_for_load_state('domcontentloaded')
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
        try:
            return (await page.locator('#glow-ingress-line2').inner_text(timeout=2500)).strip()
        except Exception:
            return ''

    def _location_ok(self, code, text):
        return any(k in text.lower() for k in MARKETS[code]['expect'])

    async def ensure_location(self, code, page=None):
        """Make sure Amazon delivers to the chosen place (postcode 2000, Dubai, 10001). Returns (ok, text)."""
        await self.start()
        async with self._loc_lock[code]:
            if page is not None and self.locations[code]['ok']:      # another check just set it
                await self._goto(page, page.url, code)
                return True, await self._location_text(page)
            own = page is None
            page = page or await self.ctx.new_page()
            try:
                m = MARKETS[code]
                if own or not page.url.startswith(m['site']):
                    await self._goto(page, m['site'] + '/', code)
                text = await self._location_text(page)
                if not self._location_ok(code, text):
                    self.log('%s: setting delivery location to %s...' % (m['name'], m['location']))
                    await self._set_location(page, code)
                    await self._goto(page, page.url, code)
                    text = await self._location_text(page)
                ok = self._location_ok(code, text)
                self.locations[code] = {'ok': ok, 'text': text}
                if not ok:
                    self.log('%s: could not set the delivery location automatically (it shows "%s"). '
                             'Use "Show browser" and set it to %s once.' % (m['name'], text or '?', m['location']))
                return ok, text
            finally:
                if own:
                    await page.close()

    async def _set_location(self, page, code):
        loc = MARKETS[code]['location']
        try:
            await page.locator('#nav-global-location-popover-link, #glow-ingress-block').first.click(timeout=8000)
        except Exception:
            return
        pop = page.locator('#GLUXZipUpdateInput, #GLUXPostalCodeWithCity_PostalCodeInput, '
                           '#GLUXCityList, [id^="GLUXCity"], .a-popover-wrapper')
        try:
            await pop.first.wait_for(timeout=8000)
        except Exception:
            return
        try:
            if await page.locator('#GLUXZipUpdateInput').count():                       # USA style
                await page.fill('#GLUXZipUpdateInput', loc)
                await page.locator('#GLUXZipUpdate input, #GLUXZipUpdate').first.click()
            elif await page.locator('#GLUXPostalCodeWithCity_PostalCodeInput').count():  # Australia style
                await page.fill('#GLUXPostalCodeWithCity_PostalCodeInput', loc)
                await page.wait_for_timeout(1200)
                dd = page.locator('#GLUXPostalCodeWithCity_DropdownButton, #GLUXPostalCodeWithCity_CityValue')
                if await dd.count():
                    await dd.first.click()
                    opt = page.locator('a[id^="GLUXPostalCodeWithCity_DropdownList"]', has_text=re.compile('sydney', re.I))
                    if await opt.count():
                        await opt.first.click()
                    else:
                        await page.locator('a[id^="GLUXPostalCodeWithCity_DropdownList"]').first.click()
                await page.locator('#GLUXPostalCodeWithCityApplyButton, #GLUXPostalCodeWithCityApplyButton input, '
                                   'span:has-text("Apply") input').first.click()
            else:                                                                        # UAE style: pick a city
                sel = page.locator('select[id*="GLUX"], select[name*="city" i]')
                if await sel.count():
                    await sel.first.select_option(label=loc)
                else:
                    await page.locator('.a-popover-wrapper >> text=/Select.*city|Choose.*city|City/i').first.click(timeout=4000)
                    await page.locator('.a-popover-wrapper a, .a-popover-wrapper li, [role="option"]',
                                       has_text=re.compile(r'^\s*%s\s*$' % loc, re.I)).first.click(timeout=4000)
                await page.locator('.a-popover-wrapper >> text=/^(Apply|Done|Confirm|Save)$/i').first.click(timeout=4000)
            await page.wait_for_timeout(1500)
            done = page.locator('#GLUXConfirmClose, .a-popover-footer input[name="glowDoneButton"], '
                                'button[name="glowDoneButton"]')
            if await done.count():
                await done.first.click()
            await page.wait_for_timeout(1000)
        except Exception as e:
            self.log('%s: location form not as expected (%s)' % (MARKETS[code]['name'], str(e).splitlines()[0][:80]))

    # ---------- the check ----------
    async def check(self, keyword, code, fast_days=3, pages=1, progress=None, local_days=LOCAL_MAX_DAYS):
        """Search one marketplace. Returns a summary dict with every listing, plus how the page looked:
        status (ok / empty / empty_suspect), Amazon's result count and the parser's health."""
        from engine import badge
        await self.start()
        m = MARKETS[code]
        page = await self.ctx.new_page()
        rows, seen = [], set()
        location = ''
        info = {}
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
                for r in raw:
                    if r['asin'] in seen:
                        continue
                    seen.add(r['asin'])
                    rows.append({
                        'market': code, 'asin': r['asin'], 'title': r['title'][:200],
                        'price': parse_price(r['price']), 'rating': parse_price(r['rating']),
                        'reviews': parse_count(r['reviews']),
                        'bought': badge.parse_badge(r.get('badge')) or parse_count(r['bought']),
                        'prime': r['prime'], 'sponsored': r['sponsored'], 'delivery': r['delivery'][:240],
                        'days': delivery_days(r['delivery'], today), 'intl': bool(r.get('intl')),
                        'url': '%s/dp/%s' % (m['site'], r['asin']), 'image': r['image'],
                    })
                if progress:
                    progress('%s: page %d read, %d listings' % (m['name'], n, len(rows)))
                if not raw:
                    break
        finally:
            await page.close()
        s = summarize(code, keyword, location, rows, fast_days, local_days)
        total, over = parse_results_info(info.get('text'))
        s.update(results_total=total, results_over=over, parse_health=parse_health(rows))
        if not rows:
            # no listings and no "no results" message: probably a soft block or a changed page, not zero sellers
            s['status'] = 'empty' if info.get('no_results') else 'empty_suspect'
        elif s['parse_health'] is not None and len(rows) >= 8 and s['parse_health'] < 0.5:
            s['layout_warning'] = True
            self.log('%s: the search page looks different from usual; some numbers may be missing.' % m['name'])
        return s


def search_url(code, keyword, page=1):
    """Amazon search address. amazon.ae is asked for English, so delivery texts can be read."""
    m = MARKETS[code]
    return '%s/s?k=%s%s%s' % (m['site'], quote_plus(keyword), '&page=%d' % page if page > 1 else '',
                              '&language=en_AE' if code == 'AE' else '')


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
    return {
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
        'bought_listings': len(bought), 'bought_top': max(bought) if bought else None,
        'bought_total': sum(bought) if bought else None,
        'level': level, 'verdict': text, 'checked_at': checked_at or time.time(), 'rows': rows,
        'search_url': '%s/s?k=%s' % (m['site'], quote_plus(keyword)),
    }
