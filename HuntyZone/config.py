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

VERSION = '6.4'
APP_DIR = Path(os.environ.get('PC_APP_DIR') or
               Path(os.environ.get('LOCALAPPDATA') or Path.home() / '.local' / 'share') / 'ProductChecker')
MARKETS = ('US', 'AU', 'AE')
MARKET_TZ = {'US': 'America/New_York', 'AU': 'Australia/Sydney', 'AE': 'Asia/Dubai'}
_FALLBACK_OFFSET = {'US': -5, 'AU': 10, 'AE': 4}             # used only if the time zone database is missing
CURRENCIES = ('USD', 'AUD', 'AED', 'EUR')                   # prices and supplier costs can be typed in these

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
    'auto_qualify': (bool, True, None),                 # the automatic refresh reads what the radar's top products miss
    'keep_running': (bool, False, None),
    'start_with_windows': (bool, False, None),
    'pause_on_battery': (bool, True, None),
    'quiet_start': (int, 22, (0, 23)),
    'quiet_end': (int, 7, (0, 23)),
    'toast_min_severity': (int, 2, (1, 3)),
    'trends_enabled': (bool, True, None),
    'import_folder': (str, '', None),
    'price_filter': (bool, True, None),                 # show only ideas selling within your price range
    'price_min': (float, 20.0, (1, 20000)),
    'price_max': (float, 50.0, (1, 20000)),
    'price_currency': (str, 'USD', CURRENCIES),         # your currency: the range is typed in it, other prices show ≈ in it
    'cost_currency': (str, 'USD', CURRENCIES),          # what your supplier quotes in (profit calculator, Profit?)
    'exclude_tech': (bool, True, None),                 # no phones, laptops or their accessories
    'target_margin_pct': (int, 20, (5, 60)),            # the margin the highest supplier price leaves you
    'board_low_pct': (int, 30, (5, 90)),                # board stock turns red at this share of the last shipment
    'board_notify': (bool, True, None),                 # Windows notification when stock turns red or a re-order is due
    'board_inform_auto': (bool, True, None),            # email the Notifiers a product update when products are added or changed
    'board_inform_wait': (int, 15, (5, 240)),           # ... once nothing changed for this many minutes
    'mail_address': (str, '', None),                    # board reminders are sent from this address (Gmail)
    'mail_from_name': (str, '', None),
    'mail_host': (str, 'smtp.gmail.com', None),
    'mail_port': (int, 465, (1, 65535)),
    'mail_security': (str, 'ssl', ('ssl', 'starttls')),
    'mail_send_hour': (int, 9, (0, 23)),                # reminders and invites go out at this hour, your time
    'mail_remind_days': (int, 3, (1, 14)),              # the reminder goes this many days before the due date
    'mail_meet_time': (str, '15:00', None),             # the update meeting on the due date
    'mail_meet_minutes': (int, 30, (15, 180)),
    'mail_meet_link': (str, '', None),                  # optional Zoom / Meet / Teams link for the invite
    'mail_copy_me': (bool, True, None),
    'mail_start_email': (bool, True, None),             # email the people as soon as you set a deadline
}
EMAIL_RX = re.compile(r'^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$')
# free-text settings that must look right: (pattern, what it should be)
CHECKS = {
    'mail_address': (EMAIL_RX, 'an email address'),
    'mail_host': (re.compile(r'^[A-Za-z0-9.\-]{1,253}$'), 'a server name like smtp.gmail.com'),
    'mail_meet_time': (re.compile(r'^(?:[01]\d|2[0-3]):[0-5]\d$'), 'a time like 15:00'),
    'mail_meet_link': (re.compile(r'^https://[^\s<>"\']{3,300}$'), 'a link starting with https://'),
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
    if typ is float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise ValueError('%s must be a number' % key)
        if v != v:                                          # NaN
            raise ValueError('%s must be a number' % key)
        lo, hi = extra
        return round(max(lo, min(hi, v)), 2)
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
    if key == 'mail_from_name':
        v = re.sub(r'[\r\n<>"]', '', v)[:80]
    if v and key in CHECKS and not (len(v) <= 254 and CHECKS[key][0].match(v)):
        raise ValueError('%s must be %s' % (key, CHECKS[key][1]))
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
