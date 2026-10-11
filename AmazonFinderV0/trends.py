"""Google Trends: how much people search for a term, week by week, in the USA, Australia and the UAE.

Requests run inside one trends.google.com tab of the background browser (in-page fetch, the same calls the
Trends website makes), one at a time with a 6-10 second gap. If Google pushes back (HTTP 429, the /sorry/ page,
"unusual traffic", an empty timeline, a consent page or a "scraper" user type) a circuit breaker pauses all
Trends requests for 1 h, then 2 h, 4 h and 24 h; the app keeps an "Open in Google Trends" link instead.
A term with too few searches is data ("low volume"), not an error.

The numbers are an index (0-100, relative to the busiest week in the window), so they show the shape of demand,
not the number of searches.
"""
import asyncio
import contextlib
import json
import logging
import os
import random
import time
from urllib.parse import quote

import config
import storage

log = logging.getLogger('pc')

BASE = os.environ.get('PC_TRENDS_BASE', 'https://trends.google.com').rstrip('/')
HL = 'en-US'
SERIES_TF = 'today 5-y'
RELATED_TF = 'today 12-m'
SERIES_TTL = 7 * 86400                      # a 5-year series is fetched again after a week
GAP = tuple(float(x) for x in os.environ.get('PC_TRENDS_GAP', '6,10').split(','))[:2]   # seconds between requests
LADDER = [3600, 2 * 3600, 4 * 3600, 24 * 3600]
MAX_SAMPLES = 5                             # newest samples averaged into one series
SEASON_MIN_SWING = 0.15                     # a seasonal pattern must move at least 15% between its best and worst month
GEOS = {'US': 'US', 'AU': 'AU', 'AE': 'AE'}

# Runs inside the trends.google.com tab.
FETCH_JS = """async u => {
  const r = await fetch(u, {credentials: 'include'});
  return {s: r.status, u: r.url, t: (await r.text()).slice(0, 2000000)};
}"""


class TrendsError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


# ---------- pure helpers: addresses and parsers ----------
def explore_url(term, geo, timeframe=SERIES_TF, base=None):
    req = {'comparisonItem': [{'keyword': term, 'geo': geo, 'time': timeframe}], 'category': 0, 'property': ''}
    return '%s/trends/api/explore?hl=%s&tz=0&req=%s' % (base or BASE, HL, quote(json.dumps(req, separators=(',', ':'))))


def widget_url(kind, widget, base=None):
    path = {'multiline': 'widgetdata/multiline', 'related': 'widgetdata/relatedsearches'}[kind]
    return '%s/trends/api/%s?hl=%s&tz=0&req=%s&token=%s' % (
        base or BASE, path, HL, quote(json.dumps(widget['request'], separators=(',', ':'))), quote(widget['token']))


def open_url(term, geo):
    """The Trends website for a term, for the "Open in Google Trends" fallback."""
    return 'https://trends.google.com/trends/explore?date=today%%205-y&geo=%s&q=%s' % (geo, quote(term))


def strip_prefix(text):
    """Google prefixes JSON with )]}' to stop it being run as a script."""
    text = text or ''
    starts = [i for i in (text.find('{'), text.find('[')) if i >= 0]
    return text[min(starts):] if starts else ''


def pushback(status, url, text):
    """The reason Google is refusing us, or None when the answer is usable."""
    if status == 429:
        return 'rate limited (HTTP 429)'
    if '/sorry/' in (url or ''):
        return 'Google asked to confirm you are not a robot'
    if 'consent.google' in (url or ''):
        return 'Google is showing a cookie consent page'
    low = (text or '')[:5000].lower()
    if 'unusual traffic' in low:
        return 'Google saw unusual traffic'
    if status and status >= 400:
        return 'HTTP %d' % status
    return None


def parse_explore(text):
    """-> {'timeseries': widget or None, 'related': widget or None, 'user_type': str}"""
    data = json.loads(strip_prefix(text) or '{}')
    out = {'timeseries': None, 'related': None, 'user_type': None}
    for w in data.get('widgets') or []:
        wid = str(w.get('id') or '')
        if wid.startswith('TIMESERIES') and not out['timeseries']:
            out['timeseries'] = w
        elif wid.startswith('RELATED_QUERIES') and not out['related']:
            out['related'] = w
        ut = (w.get('request') or {}).get('userConfig', {}).get('userType')
        out['user_type'] = out['user_type'] or ut
    return out


def parse_multiline(text):
    """-> {'status': ok | low_volume | throttled, 'points': [[ts, value, partial]], 'resolution': str|None}"""
    data = json.loads(strip_prefix(text) or '{}')
    timeline = ((data.get('default') or {}).get('timelineData')) or []
    if not timeline:
        return {'status': 'throttled', 'points': [], 'resolution': None, 'reason': 'empty timeline'}
    points, any_data = [], False
    for p in timeline:
        try:
            t = int(p.get('time'))
        except (TypeError, ValueError):
            continue
        val = (p.get('value') or [0])[0]
        shown = (p.get('formattedValue') or [''])[0]
        if shown == '<1':
            val = 0.5
        has = (p.get('hasData') or [True])[0]
        any_data = any_data or (bool(has) and (val or 0) > 0)
        points.append([t, float(val or 0), bool(p.get('isPartial'))])
    res = None
    if len(points) >= 2:
        step = sorted(b[0] - a[0] for a, b in zip(points, points[1:]))[len(points) // 2 - 1] / 86400
        res = 'DAY' if step < 2 else 'WEEK' if step < 20 else 'MONTH'
    return {'status': 'ok' if any_data else 'low_volume', 'points': points, 'resolution': res}


def parse_related(text):
    """-> [{'kind': 'top'|'rising', 'query', 'value', 'breakout'}]"""
    data = json.loads(strip_prefix(text) or '{}')
    lists = (data.get('default') or {}).get('rankedList') or []
    out = []
    for kind, block in zip(('top', 'rising'), lists):
        for k in block.get('rankedKeyword') or []:
            shown = str((k.get('formattedValue') or ''))
            out.append({'kind': kind, 'query': str(k.get('query') or '')[:120], 'value': k.get('value'),
                        'breakout': shown.lower() == 'breakout'})
    return out


# ---------- circuit breaker ----------
class Breaker:
    """Pauses all Trends requests after Google pushes back: 1 h, 2 h, 4 h, then 24 h. A success resets it."""

    def __init__(self, now=time.time):
        self.now = now
        self.until = 0
        self.streak = 0
        self.reason = ''

    def is_open(self):
        return self.now() < self.until

    def trip(self, reason):
        self.until = self.now() + LADDER[min(self.streak, len(LADDER) - 1)]
        self.streak += 1
        self.reason = reason
        return self.until

    def ok(self):
        self.streak, self.until, self.reason = 0, 0, ''

    def state(self):
        return {'paused': self.is_open(), 'until': self.until if self.is_open() else None, 'reason': self.reason,
                'streak': self.streak}

    def load(self, d):
        if d:
            self.until, self.streak, self.reason = d.get('until') or 0, d.get('streak') or 0, d.get('reason') or ''


# ---------- the browser lane ----------
class BrowserTrends:
    def __init__(self, checker, say=None, gap=GAP, base=None):
        self.checker = checker
        self.say = say or (lambda m: None)
        self.gap = gap
        self.base = base or BASE
        self.breaker = Breaker()
        self.throttle = None                                    # set by the app: Google's daily budget lives there
        self.page = None
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def _tab(self):
        await self.checker.start()
        if self.page is None or self.page.is_closed():
            self.page = await self.checker.ctx.new_page()
            await self.page.goto('%s/trends/explore?geo=US&hl=%s' % (self.base, HL), wait_until='domcontentloaded',
                                 timeout=45000)
            await self._consent()
        return self.page

    async def _consent(self):
        """Europe: Google first shows a cookie page. Choose "Reject all" (no sign-in, nothing else)."""
        if 'consent.' not in self.page.url:
            return
        for label in ('Reject all', 'Alle ablehnen', 'Tout refuser', 'Rechazar todo'):
            btn = self.page.locator('button:has-text("%s")' % label)
            if await btn.count():
                await btn.first.click()
                await self.page.wait_for_load_state('domcontentloaded')
                return

    async def _fetch(self, url):
        if self.breaker.is_open():
            raise TrendsError('paused', 'Google Trends is paused until %s (%s).' % (
                time.strftime('%H:%M', time.localtime(self.breaker.until)), self.breaker.reason))
        async with self._lock, (self.throttle.slot('google') if self.throttle else contextlib.nullcontext()):
            if not self.throttle:                                # one request at a time, with a polite gap
                wait = self._last + random.uniform(*self.gap) - time.time()
                if wait > 0:
                    await asyncio.sleep(wait)
            try:
                page = await self._tab()
                r = await page.evaluate(FETCH_JS, url)
            except TrendsError:
                raise
            except Exception as e:                               # noqa: BLE001
                self.page = None
                raise TrendsError('error', 'Google Trends could not be reached (%s)' % str(e).splitlines()[0][:100])
            finally:
                self._last = time.time()
        reason = pushback(r.get('s'), r.get('u'), r.get('t'))
        if reason:
            self._trip(reason)
        return r.get('t') or ''

    def _trip(self, reason):
        until = self.breaker.trip(reason)
        self.say('Google Trends paused until %s: %s.' % (time.strftime('%H:%M', time.localtime(until)), reason))
        raise TrendsError('throttled', reason)

    async def series(self, term, geo, timeframe=SERIES_TF):
        """-> {'status', 'points', 'resolution', 'user_type'}. Raises TrendsError when Google refuses."""
        exp = parse_explore(await self._fetch(explore_url(term, geo, timeframe, self.base)))
        if exp['user_type'] == 'USER_TYPE_SCRAPER':
            self._trip('Google flagged the requests as automated')
        if not exp['timeseries']:
            self._trip('no timeline in the answer')
        out = parse_multiline(await self._fetch(widget_url('multiline', exp['timeseries'], self.base)))
        if out['status'] == 'throttled':
            self._trip(out.get('reason') or 'empty timeline')
        self.breaker.ok()
        out['user_type'] = exp['user_type']
        out['related_widget'] = exp['related']
        return out

    async def related(self, term, geo, timeframe=RELATED_TF):
        exp = parse_explore(await self._fetch(explore_url(term, geo, timeframe, self.base)))
        if not exp['related']:
            return []
        items = parse_related(await self._fetch(widget_url('related', exp['related'], self.base)))
        self.breaker.ok()
        return items

    async def close(self):
        try:
            if self.page and not self.page.is_closed():
                await self.page.close()
        except Exception:                                        # noqa: BLE001
            pass
        self.page = None


# ---------- samples -> features ----------
def compute(samples):
    """Stored samples -> one feature record: monthly consensus, features, label with reasons, score, seasonality."""
    from engine import trendfeat as tf
    usable = [s for s in samples if s.get('status') in ('ok', 'low_volume')][-MAX_SAMPLES:]
    if not usable:
        return {'status': 'none', 'label': None, 'score': None, 'why': [], 'monthly': []}
    if all(s['status'] == 'low_volume' for s in usable):
        return {'status': 'low_volume', 'label': 'Insufficient', 'score': None,
                'why': ['Not enough Google searches for this term in this country'], 'monthly': [],
                'n_samples': len(usable), 'as_of': usable[-1].get('fetched_at')}
    monthly = [tf.to_monthly(s['points'], s.get('resolution')) for s in usable if s['status'] == 'ok']
    cons, _cv, q = tf.consensus(monthly)
    f = tf.features(cons)
    label, why = tf.label(f)
    season = tf.seasonality(f)
    swing = max(season['si']) - min(season['si']) if season.get('si') else 0
    season['swing'] = round(swing, 4)
    if swing < SEASON_MIN_SWING:                 # a smooth trend has a tidy but meaningless "pattern"
        season['reliable'] = False
    keep = {k: f.get(k) for k in ('YoY', 'YoY_recent', 'CAGR', 'm12', 'short_mom', 'cur_vs_peak', 'months_since_peak',
                                  'spikiness', 'zero_share', 'peak_month', 'launch_month', 'n_months', 'mean_last12')}
    return {'status': 'ok', 'label': label, 'why': why, 'score': tf.trend_score(f, q), 'q': q,
            'features': keep, 'seasonality': season, 'n_samples': len(monthly),
            'monthly': [[i.strftime('%Y-%m'), round(float(v), 2)] for i, v in cons.items() if v == v],
            'as_of': usable[-1].get('fetched_at')}


async def refresh(db, trends, term, geo, force=False, with_related=False, now=None):
    """Fetch the term's 5-year series when the stored one is older than a week, then recompute its features.
    Returns the feature record. Google refusing is stored as a 'throttled' sample (a gap) and re-raised."""
    now = now or time.time()
    term = config.norm_kw(term)
    have = db.read(storage.trends_samples, term, geo, SERIES_TF, now - SERIES_TTL)
    fresh = [s for s in have if s['status'] in ('ok', 'low_volume')]
    if force or not fresh:
        try:
            got = await trends.series(term, geo)
        except TrendsError as e:
            if e.status == 'throttled':
                await db.awrite(storage.save_trends_sample, term, geo, SERIES_TF, 'throttled', None, None, None, now)
            raise
        await db.awrite(storage.save_trends_sample, term, geo, SERIES_TF, got['status'], got['points'],
                        got['resolution'], got.get('user_type'), now)
        if with_related and got['status'] == 'ok':
            try:
                items = await trends.related(term, geo)
                await db.awrite(storage.save_trends_related, term, geo, RELATED_TF, items, now)
            except TrendsError:
                pass
    samples = db.read(storage.trends_samples, term, geo, SERIES_TF)
    data = compute(samples)
    await db.awrite(storage.save_trends_feature, term, geo, data, now)
    return data
