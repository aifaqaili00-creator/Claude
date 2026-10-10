"""Settings schema, paths, keyword normalising and marketplace-local dates.

Settings are validated and clamped here, so a bad value from the UI can never crash a check.
"""
import datetime as dt
import os
import re
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:                                          # pragma: no cover (Python < 3.9)
    ZoneInfo = None

VERSION = '6.0'
APP_DIR = Path(os.environ.get('PC_APP_DIR') or
               Path(os.environ.get('LOCALAPPDATA') or Path.home() / '.local' / 'share') / 'ProductChecker')
MARKETS = ('US', 'AU', 'AE')
MARKET_TZ = {'US': 'America/New_York', 'AU': 'Australia/Sydney', 'AE': 'Asia/Dubai'}
_FALLBACK_OFFSET = {'US': -5, 'AU': 10, 'AE': 4}             # used only if the time zone database is missing

# key: (type, default, extra) -- extra = (min, max) for numbers, choices for strings
SCHEMA = {
    'profile': (str, '', None),
    'theme': (str, 'light', ('light', 'dark', 'system')),
    'fast_days': (int, 3, (1, 10)),
    'local_days': (int, 9, (2, 21)),
    'pages': (int, 1, (1, 3)),
    'cache_hours': (int, 6, (1, 168)),
    'top': (int, 10, (5, 500)),
    'auto_refresh': (bool, True, None),
    'keep_running': (bool, False, None),
    'start_with_windows': (bool, False, None),
    'pause_on_battery': (bool, True, None),
    'quiet_start': (int, 22, (0, 23)),
    'quiet_end': (int, 7, (0, 23)),
    'toast_min_severity': (int, 2, (1, 3)),
    'trends_enabled': (bool, True, None),
    'import_folder': (str, '', None),
}
DEFAULT_LOCATIONS = {'US': '10001', 'AU': '2000', 'AE': 'Dubai'}


def defaults():
    d = {k: v[1] for k, v in SCHEMA.items()}
    d['locations'] = dict(DEFAULT_LOCATIONS)
    d['categories'] = {m: [] for m in MARKETS}                 # chosen Movers & Shakers slugs per market
    d['registered'] = {'AU': False, 'AE': False}               # registered for GST / VAT
    return d


def _coerce(key, value):
    typ, default, extra = SCHEMA[key]
    if typ is bool:
        if isinstance(value, str):
            return value.strip().lower() in ('1', 'true', 'yes', 'on')
        return bool(value)
    if typ is int:
        try:
            v = int(float(value))
        except (TypeError, ValueError):
            raise ValueError('%s must be a number' % key)
        lo, hi = extra
        return max(lo, min(hi, v))
    v = str(value if value is not None else '').strip()[:300]
    if extra and v not in extra:
        raise ValueError('%s must be one of %s' % (key, ', '.join(extra)))
    return v


def validate(changes, current=None):
    """Apply `changes` onto `current` (or defaults). Returns (settings, errors). Unknown keys are ignored."""
    out = merge(current or {})
    errors = []
    for k, v in (changes or {}).items():
        try:
            if k in SCHEMA:
                out[k] = _coerce(k, v)
            elif k in ('locations', 'categories', 'registered') and isinstance(v, dict):
                out = _merge_sub(out, k, v)
        except ValueError as e:
            errors.append(str(e))
    return out, errors


def merge(saved):
    """Old or partial settings.json -> complete, valid settings (unknown or broken values fall back to defaults)."""
    out = defaults()
    for k, v in (saved or {}).items():
        if k in SCHEMA:
            try:
                out[k] = _coerce(k, v)
            except ValueError:
                pass
        elif k in ('locations', 'categories', 'registered') and isinstance(v, dict):
            out = _merge_sub(out, k, v)
    return out


def _merge_sub(out, key, value):
    out = dict(out)
    for name in ('locations', 'categories', 'registered'):
        out[name] = dict(out[name])
    if key == 'locations':
        for m, loc in value.items():
            if m in MARKETS and str(loc).strip():
                out['locations'][m] = str(loc).strip()[:40]
    elif key == 'categories':
        for m, slugs in value.items():
            if m in MARKETS and isinstance(slugs, list):
                out['categories'][m] = [re.sub(r'[^a-z0-9-]', '', str(x).lower())[:60] for x in slugs][:20]
    elif key == 'registered':
        for m in ('AU', 'AE'):
            if m in value:
                out['registered'][m] = bool(value[m])
    return out


def norm_kw(s):
    """One spelling per keyword for every cache and database key: 'Moving  Bags!' -> 'moving bags'."""
    s = str(s or '').lower()
    s = re.sub(r"[^\w\s&'\-]", ' ', s)
    return ' '.join(s.split())[:120]


def _tz(code):
    if ZoneInfo:
        try:
            return ZoneInfo(MARKET_TZ[code])
        except Exception:
            pass
    return dt.timezone(dt.timedelta(hours=_FALLBACK_OFFSET.get(code, 0)))


def market_now(code, ts=None):
    ts = dt.datetime.now(dt.timezone.utc).timestamp() if ts is None else ts
    return dt.datetime.fromtimestamp(ts, _tz(code))


def market_today(code):
    """Today's date in the marketplace (Amazon delivery dates are in its own calendar)."""
    return market_now(code).date()


def market_day(ts, code):
    return market_now(code, ts).strftime('%Y-%m-%d')


def month_key(ts, code):
    return market_now(code, ts).strftime('%Y-%m')
