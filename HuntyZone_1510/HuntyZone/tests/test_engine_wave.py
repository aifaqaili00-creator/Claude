"""engine/wave.py: when a rise in US searches reaches Australia or the UAE."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import wave as W  # noqa: E402


def series(start, values):
    return [[W.add_months(start, i), v] for i, v in enumerate(values)]


FLAT = [10] * 48
RISE = [10, 10, 20, 30, 45, 60, 70, 80, 85, 90, 95, 100]
US = series('2022-01', FLAT + RISE)                                     # rises from May 2026
AU = series('2022-01', FLAT + [10, 10, 10] + RISE[:9])                  # the same rise three months later
QUIET = series('2022-01', FLAT + [10] * 12)


def test_onset_is_where_the_current_rise_started():
    assert W.onset(US) == '2026-05' and W.onset(AU) == '2026-08'
    assert W.onset(QUIET) is None                                       # not rising
    assert W.onset(series('2022-01', [50] * 30 + [100] * 30)) is None   # an old step up is not a current rise
    assert W.onset(series('2022-01', RISE)) is None and W.onset(None) is None   # too short to date
    assert W.lag_months(US, AU) == 3 and W.lag_months(US, QUIET) is None


def test_the_usual_lag_needs_three_products():
    assert W.typical_lag([3, 2, 4, 30, None, 'x']) == {'months': 3, 'lo': 2, 'hi': 4, 'n': 3}   # 30 months is noise
    assert W.typical_lag([3, 4]) is None and W.typical_lag(None) is None


def test_forecast_says_here_coming_due_or_early():
    names = ('US', 'Australia')
    lag = {'months': 3, 'lo': 2, 'hi': 4, 'n': 5}
    here = W.forecast(US, AU, names=names)
    assert here['status'] == 'here' and here['why'] == 'searches in Australia have been rising since Aug 2026'
    known = W.forecast(US, QUIET, {'known_now': True, 'first_known': '2026-09-12'}, names=names)
    assert known['status'] == 'here' and known['why'] == 'Amazon suggests it to shoppers in Australia since 2026-09-12'
    coming = W.forecast(US, QUIET, {'known_now': False}, lag, '2026-06-15', names)
    assert coming['status'] == 'coming' and coming['eta'] == ['2026-07', '2026-09'] and 'expected Jul 2026 to Sep 2026' in coming['why']
    assert W.forecast(US, QUIET, None, lag, '2026-12-01', names)['status'] == 'due'
    assert W.forecast(US, QUIET, None, None, '2026-06-15', names)['status'] == 'early'
    assert W.forecast(QUIET, QUIET, None, lag, '2026-06-15', names)['status'] == 'unknown'
    json.dumps(coming, allow_nan=False)
