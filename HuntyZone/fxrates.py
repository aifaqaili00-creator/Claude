"""Exchange rates between US$, A$, AED and EUR (your price range, supplier costs and the converter).

Source: the European Central Bank's daily reference rates (free, published every working day around 16:00 CET).
The UAE dirham is pegged to the US dollar at 3.6725. Rates are fetched at most once a day, kept in fx.json, and
the app falls back to the last saved rates, then to fixed defaults, so it never stops working offline.
"""
import json
import logging
import os
import re
import threading
import time
import urllib.request

import config

log = logging.getLogger('pc')
ECB_URL = 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml'
AED_PER_USD = 3.6725
MAX_AGE = 20 * 3600
RETRY_AFTER = 30 * 60                                   # after a failed fetch (offline), wait before trying again
FALLBACK = {'as_of': None, 'source': 'fixed', 'per_usd': {'USD': 1.0, 'EUR': 0.92, 'AUD': 1.55, 'AED': AED_PER_USD}}
CURRENCY = {'US': 'USD', 'AU': 'AUD', 'AE': 'AED'}
CURRENCIES = config.CURRENCIES
SYMBOL = {'USD': 'US$', 'EUR': '€', 'AUD': 'A$', 'AED': 'AED '}

_lock = threading.Lock()
_rates = None
_last_try = 0.0


def cache_file():
    return config.APP_DIR / 'fx.json'


def parse_ecb(xml):
    """ECB daily XML -> {'as_of', 'source', 'per_usd': {USD, EUR, AUD, AED}}. Raises ValueError if unusable."""
    day = re.search(r"time=['\"](\d{4}-\d{2}-\d{2})['\"]", xml)
    per_eur = {c: float(r) for c, r in re.findall(r"currency=['\"]([A-Z]{3})['\"]\s+rate=['\"]([\d.]+)['\"]", xml)}
    usd, aud = per_eur.get('USD'), per_eur.get('AUD')
    if not usd or not aud:
        raise ValueError('ECB rates without USD or AUD')
    return {'as_of': day.group(1) if day else None, 'source': 'ECB',
            'per_usd': {'USD': 1.0, 'EUR': round(1 / usd, 6), 'AUD': round(aud / usd, 6), 'AED': AED_PER_USD}}


def _fetch(timeout=10):
    req = urllib.request.Request(ECB_URL, headers={'User-Agent': 'ProductChecker/' + config.VERSION})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(200_000).decode('utf-8', 'replace')


def _load():
    try:
        d = json.loads(cache_file().read_text(encoding='utf-8'))
        if all(d['per_usd'].get(c) for c in ('USD', 'EUR', 'AUD', 'AED')):
            return d
    except Exception:                                   # noqa: BLE001 - no or broken cache
        pass
    return None


def _apply(d):
    """Keep the profit maths (engine/profit.fx) on the same rates."""
    global _rates
    _rates = d
    try:
        from engine import fees
        fees.DEFAULTS.setdefault('fx_per_usd', {}).update({k: v for k, v in d['per_usd'].items() if k != 'EUR'})
    except Exception:                                   # noqa: BLE001
        pass
    return d


def refresh(force=False, fetch=None, now=None):
    """Fetch new rates if the saved ones are older than about a day. Never raises; returns the rates in use."""
    global _last_try
    now = now or time.time()
    with _lock:
        saved = _load()
        if saved and not force and now - saved.get('fetched_at', 0) < MAX_AGE:
            return _apply(saved)
        _last_try = now
        try:
            d = parse_ecb((fetch or _fetch)())
            d['fetched_at'] = now
            try:
                cache_file().parent.mkdir(parents=True, exist_ok=True)
                cache_file().write_text(json.dumps(d), encoding='utf-8')
            except OSError:
                pass
            return _apply(d)
        except Exception as e:                          # noqa: BLE001 - offline: keep what we have
            log.info('exchange rates not updated: %s', e)
            return _apply(saved or dict(FALLBACK))


def rates():
    """The rates in use (loads the saved file the first time, without going online)."""
    if _rates is None:
        with _lock:
            if _rates is None:
                _apply(_load() or dict(FALLBACK))
    return _rates


def convert(amount, from_cur, to_cur):
    per = rates()['per_usd']
    return amount / per[from_cur] * per[to_cur]


def nice(x):
    """Round a converted price the way a shopper reads it."""
    return round(x) if x >= 10 else round(x, 1)


def ranges(settings):
    """Your price range in each marketplace's own money: {'US': (lo, hi), 'AU': ..., 'AE': ...}."""
    lo, hi = float(settings.get('price_min', 20)), float(settings.get('price_max', 50))
    lo, hi = min(lo, hi), max(lo, hi)
    cur = settings.get('price_currency', 'USD')
    return {m: (nice(convert(lo, cur, c)), nice(convert(hi, cur, c))) for m, c in CURRENCY.items()}


def summary(settings):
    r = rates()
    return {'as_of': r.get('as_of'), 'source': r.get('source'), 'fetched_at': r.get('fetched_at'),
            'per_usd': {c: r['per_usd'][c] for c in CURRENCIES}, 'currencies': list(CURRENCIES),
            'ranges': ranges(settings), 'currency': settings.get('price_currency', 'USD')}


_busy = threading.Event()


def due(now=None):
    """True when the rates are older than about a day and the last attempt was not a moment ago."""
    now = now or time.time()
    r = rates()
    fresh = r.get('fetched_at') and now - r['fetched_at'] < MAX_AGE
    return not fresh and now - _last_try >= RETRY_AFTER


def refresh_soon(force=False):
    """Refresh in a background thread if the rates are due (or force). Returns at once.
    Called on every status poll, so after a failure it waits RETRY_AFTER instead of asking again every 2 s."""
    if os.environ.get('PC_FX_OFFLINE') or _busy.is_set() or not (force or due()):
        return False
    _busy.set()

    def run():
        try:
            refresh(force=force)
        finally:
            _busy.clear()
    threading.Thread(target=run, name='fx', daemon=True).start()
    return True
