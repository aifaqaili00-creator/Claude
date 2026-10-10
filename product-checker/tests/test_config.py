"""Settings schema, keyword normalising and marketplace-local dates (including clock changes)."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402


def ts(y, m, d, h=0, mi=0):
    return dt.datetime(y, m, d, h, mi, tzinfo=dt.timezone.utc).timestamp()


def test_merge_fills_defaults_and_clamps():
    s = config.merge({'pages': 9, 'cache_hours': 'x', 'top': 1, 'theme': 'neon', 'locations': {'AU': '3000', 'XX': '1'}})
    assert s['pages'] == 3 and s['cache_hours'] == 6 and s['top'] == 5 and s['theme'] == 'light'
    assert s['locations'] == {'US': '10001', 'AU': '3000', 'AE': 'Dubai'}
    assert config.merge(None) == config.defaults()


def test_validate_reports_errors_and_ignores_unknown_keys():
    s, err = config.validate({'theme': 'pink', 'fast_days': 'abc', 'nope': 1})
    assert len(err) == 2 and 'nope' not in s
    s, err = config.validate({'auto_refresh': 'false', 'quiet_start': 30, 'registered': {'AU': 1}})
    assert not err and s['auto_refresh'] is False and s['quiet_start'] == 23 and s['registered']['AU'] is True
    s, err = config.validate({'categories': {'US': ['Kitchen & Dining!', 'garden']}})
    assert s['categories']['US'] == ['kitchendining', 'garden']


def test_validate_does_not_change_the_current_settings():
    cur = config.defaults()
    config.validate({'locations': {'AU': '4000'}}, cur)
    assert cur['locations']['AU'] == '2000'


def test_norm_kw():
    assert config.norm_kw('  Moving   Bags!! (XL) ') == 'moving bags xl'
    assert config.norm_kw("Kid's  Toys & Games") == "kid's toys & games"
    assert config.norm_kw(None) == ''
    assert config.norm_kw('a' * 500) == 'a' * 120


def test_market_day_uses_each_market_calendar():
    t = ts(2026, 10, 9, 20)                                       # 20:00 UTC
    assert config.market_day(t, 'AU') == '2026-10-10'             # Sydney is already the next day
    assert config.market_day(t, 'US') == '2026-10-09'
    assert config.market_day(t, 'AE') == '2026-10-10'             # Dubai UTC+4 -> 00:00


def test_dates_across_clock_changes():
    # Sydney moves to UTC+11 on 4 Oct 2026 (02:00 local); 14:30 UTC on 3 Oct is 00:30 on 4 Oct at UTC+10
    assert config.market_day(ts(2026, 10, 3, 14, 30), 'AU') == '2026-10-04'
    # after the change, 13:30 UTC is already 00:30 the next day
    assert config.market_day(ts(2026, 10, 4, 13, 30), 'AU') == '2026-10-05'
    # Sydney back to UTC+10 on 5 Apr 2026; 13:30 UTC = 23:30 local
    assert config.market_day(ts(2026, 4, 5, 13, 30), 'AU') == '2026-04-05'
    # New York: UTC-4 until 06:00 UTC on 1 Nov 2026, then UTC-5
    assert config.market_day(ts(2026, 11, 1, 3, 30), 'US') == '2026-10-31'       # 23:30 EDT
    assert config.market_day(ts(2026, 11, 2, 4, 30), 'US') == '2026-11-01'       # 23:30 EST (UTC-4 would say the 2nd)
    # and back to UTC-4 at 07:00 UTC on 8 Mar 2026
    assert config.market_day(ts(2026, 3, 8, 4, 30), 'US') == '2026-03-07'        # 23:30 EST
    assert config.market_day(ts(2026, 3, 9, 4, 30), 'US') == '2026-03-09'        # 00:30 EDT (UTC-5 would say the 8th)
    assert config.month_key(ts(2026, 10, 31, 23, 0), 'AU') == '2026-11'
    assert config.month_key(ts(2026, 10, 31, 23, 0), 'US') == '2026-10'
