"""Tests for engine/alerts.py: every rule fires on crafted data, never on blocked or wrong-location gaps,
the weekly dedupe key, and robustness to junk input (synthetic data only)."""
import json
import math
import random
import re
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from engine import alerts as A
from engine import surge

DAY = 86400
NOW = int(datetime(2026, 10, 7, 12, tzinfo=timezone.utc).timestamp())   # Wed, ISO week 2026-W41
BAD = ('blocked', 'captcha', 'wrong_location', 'geo_redirect', 'error', 'timeout', 'offline')
KEYS = {'rule', 'severity', 'market', 'target', 'title', 'body', 'data', 'dedupe_key'}
KEY_RE = re.compile(r'^[a-z_]+\|[A-Z]*\|[^|]*\|\d{4}-W\d{2}$')


def ts(dt):
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def rules(alerts):
    return [a['rule'] for a in alerts]


def by_rule(alerts, rule):
    return [a for a in alerts if a['rule'] == rule]


def check_shape(alerts):
    """Every alert has the contract keys, a sane severity, a weekly key and survives strict JSON."""
    for a in alerts:
        assert KEYS <= set(a)
        assert a['severity'] in (1, 2, 3)
        assert a['rule'] in A.RULES
        assert a['title'] and a['body']
        assert isinstance(a['data'], dict)
        assert KEY_RE.match(a['dedupe_key']), a['dedupe_key']
        assert a['dedupe_key'] == '%s|%s|%s|%s' % (a['rule'], a['market'], a['target'], A.week_key(a['ts']))
    json.dumps(alerts, allow_nan=False)


def snap(days_ago, status='ok', **kw):
    row = {'ts': NOW - int(days_ago * DAY), 'status': status, 'fast': 10, 'local': 12, 'overseas': 8,
           'organic': 48, 'badged': 20, 'bought_mid_sum': 1000.0, 'price_med': 30.0, 'low_review_n': 4}
    row.update(kw)
    return row


def baseline(n=8, start=9, values=(980, 1010, 1000, 990, 1020, 1005, 995, 1000), **kw):
    """n valid daily snapshots, the newest `start - n + 1` days ago, with varied bought_mid_sum."""
    return [snap(start - i, bought_mid_sum=float(values[i % len(values)]), **kw) for i in range(n)]


# ---- keys, dedupe, alert dicts ----

def test_week_key_iso_format():
    assert A.week_key(NOW) == '2026-W41'
    assert A.week_key(ts(datetime(2026, 1, 1, 12))) == '2026-W01'          # zero-padded week
    assert A.week_key(ts(datetime(2027, 1, 1, 12))) == '2026-W53'          # ISO year, not calendar year
    assert A.week_key(ts(datetime(2024, 12, 30, 1))) == '2025-W01'
    for bad in (None, float('nan'), float('inf'), 'x', True, [1]):
        assert A.week_key(bad) is None


def test_dedupe_key_format_and_week_boundaries():
    k = A.dedupe_key('gap_open', 'us', 'yoga mat', NOW)
    assert k == 'gap_open|US|yoga mat|2026-W41'
    monday = ts(datetime(2026, 10, 5, 0, 0, 1))
    sunday = ts(datetime(2026, 10, 11, 23, 59, 59))
    next_monday = ts(datetime(2026, 10, 12, 0, 0, 1))
    assert A.dedupe_key('gap_open', 'US', 'kw', monday) == A.dedupe_key('gap_open', 'US', 'kw', sunday)
    assert A.dedupe_key('gap_open', 'US', 'kw', next_monday).endswith('|2026-W42')
    assert A.dedupe_key('gap_open', None, None, None) == 'gap_open|||'


def test_make_alert_is_json_safe():
    a = A.make_alert('price_war', 'au', ' kw ', 'T', 'B',
                     {'x': float('nan'), 'y': np.float64(1.234567), 'z': np.int64(3), 'f': np.bool_(True),
                      't': (1, 2), 's': {'b', 'a'}, 'inf': float('-inf'), 'd': date(2026, 1, 2), 'n': None},
                     NOW)
    check_shape([a])
    assert a['market'] == 'AU' and a['target'] == 'kw' and a['severity'] == 2 and a['ts'] == NOW
    d = a['data']
    assert d['x'] is None and d['inf'] is None and d['y'] == 1.2346 and d['z'] == 3 and d['f'] is True
    assert d['t'] == [1, 2] and d['s'] == ['a', 'b'] and d['d'] == '2026-01-02'
    assert A.make_alert('gap_open', 'US', 'k', 't', 'b', None, NOW)['severity'] == 3
    assert A.make_alert('gap_open', 'US', 'k', 't', 'b', None, NOW, severity=1)['severity'] == 1
    assert A.make_alert('gap_open', 'US', 'k', 't', 'b', None, NOW, severity=7)['severity'] == 3


def test_dedupe_keeps_first_per_key():
    a1 = A.make_alert('gap_open', 'US', 'kw', 'first', 'b', {}, NOW)
    a2 = A.make_alert('gap_open', 'US', 'kw', 'second', 'b', {}, NOW + DAY)          # same ISO week
    a3 = A.make_alert('gap_open', 'US', 'kw', 'next week', 'b', {}, NOW + 7 * DAY)
    a4 = A.make_alert('gap_open', 'AU', 'kw', 'other market', 'b', {}, NOW)
    nokey = {'rule': 'x', 'title': 'no key'}
    out = A.dedupe([a1, a2, None, 'junk', a3, a4, nokey, dict(a1, title='third')])
    assert [a['title'] for a in out] == ['first', 'next week', 'other market', 'no key']
    assert A.dedupe(None) == [] and A.dedupe([]) == [] and A.dedupe('abc') == []


# ---- serp_rules: demand ----

def test_demand_up_and_down_fire():
    up = A.serp_rules('US', 'yoga mat', baseline() + [snap(0, bought_mid_sum=1600.0)], NOW)
    check_shape(up)
    assert rules(up) == ['demand_up']
    a = up[0]
    assert a['severity'] == 2 and a['market'] == 'US' and a['target'] == 'yoga mat'
    assert a['dedupe_key'] == 'demand_up|US|yoga mat|2026-W41'
    assert a['data']['z'] >= 3 and a['data']['change'] == pytest.approx(0.6, abs=0.01)
    assert a['data']['n_base'] == 8

    down = A.serp_rules('US', 'yoga mat', baseline() + [snap(0, bought_mid_sum=500.0)], NOW)
    assert rules(down) == ['demand_down']
    assert down[0]['data']['change'] == pytest.approx(-0.5, abs=0.01)


def test_demand_uses_at_most_8_earlier_snapshots():
    old = [snap(30 - i, bought_mid_sum=5000.0) for i in range(5)]          # far older, much higher level
    out = A.serp_rules('US', 'kw', old + baseline() + [snap(0, bought_mid_sum=1600.0)], NOW)
    assert rules(out) == ['demand_up'] and out[0]['data']['baseline_median'] == pytest.approx(1000, abs=10)


def test_demand_needs_four_valid_snapshots_and_a_25pct_change():
    assert A.serp_rules('US', 'kw', baseline(n=3, start=3) + [snap(0, bought_mid_sum=3000.0)], NOW) == []
    tight = baseline(values=(1000, 1001, 999, 1000, 1002, 998, 1000, 1000))
    out = A.serp_rules('US', 'kw', tight + [snap(0, bought_mid_sum=1100.0)], NOW)        # big z, only +10%
    assert 'demand_up' not in rules(out)
    out = A.serp_rules('US', 'kw', baseline() + [snap(0, bought_mid_sum=1240.0)], NOW)   # +24%
    assert 'demand_up' not in rules(out)
    # a wide baseline: +30% is not 3 robust sds away
    wide = baseline(values=(600, 1400, 800, 1200, 1000, 700, 1300, 900))
    assert 'demand_up' not in rules(A.serp_rules('US', 'kw', wide + [snap(0, bought_mid_sum=1300.0)], NOW))


def test_demand_flat_baseline_uses_floor_spread():
    flat = baseline(values=(1000,))
    assert rules(A.serp_rules('US', 'kw', flat + [snap(0, bought_mid_sum=1300.0)], NOW)) == ['demand_up']
    assert A.serp_rules('US', 'kw', flat + [snap(0, bought_mid_sum=1000.0)], NOW) == []
    zero = baseline(values=(0,))
    assert A.serp_rules('US', 'kw', zero + [snap(0, bought_mid_sum=500.0)], NOW) == []


@pytest.mark.parametrize('bad', BAD)
def test_demand_ignores_blocked_rows(bad):
    # the triggering row is a gap: no alert either way
    assert A.serp_rules('US', 'kw', baseline() + [snap(0, bad, bought_mid_sum=5000.0)], NOW) == []
    assert A.serp_rules('US', 'kw', baseline() + [snap(0, bad, bought_mid_sum=0.0)], NOW) == []
    # gaps in the baseline are not zeros: a normal reading after them is not "demand up"
    rows = []
    for i, r in enumerate(baseline(n=6, start=12)):
        rows += [r, snap(12 - i - 0.5, bad, bought_mid_sum=0.0)]
    assert A.serp_rules('US', 'kw', rows + [snap(0, bought_mid_sum=1000.0)], NOW) == []
    # only 3 valid rows in the baseline (the rest are gaps): too few to judge
    rows = baseline(n=3, start=9) + [snap(5 - i, bad, bought_mid_sum=1000.0) for i in range(5)]
    assert A.serp_rules('US', 'kw', rows + [snap(0, bought_mid_sum=3000.0)], NOW) == []


def test_partial_and_empty_rows_are_valid():
    out = A.serp_rules('US', 'kw', baseline(status='partial') + [snap(0, 'partial', bought_mid_sum=1600.0)], NOW)
    assert rules(out) == ['demand_up']
    out = A.serp_rules('US', 'kw', baseline() + [snap(0, 'empty', bought_mid_sum=0.0, fast=0, organic=0)], NOW)
    assert 'demand_down' in rules(out) and 'gap_open' not in rules(out)     # an empty page is not a gap


# ---- serp_rules: gaps ----

def test_gap_open_fires_when_fast_drops_to_4():
    out = A.serp_rules('AU', 'kw', [snap(2, fast=8), snap(1, fast=7), snap(0, fast=3)], NOW)
    check_shape(out)
    assert rules(out) == ['gap_open']
    a = out[0]
    assert a['severity'] == 3 and a['market'] == 'AU'
    assert a['data']['fast'] == 3 and a['data']['fast_before'] == 7 and a['data']['first'] is False


def test_gap_open_first_valid_snapshot():
    out = A.serp_rules('AE', 'kw', [snap(0, fast=2)], NOW)
    assert rules(out) == ['gap_open'] and out[0]['data']['first'] is True
    # earlier rows that were all gaps do not count as "before"
    out = A.serp_rules('AE', 'kw', [snap(3, 'blocked', fast=9), snap(2, 'wrong_location', fast=9), snap(0, fast=2)],
                       NOW)
    assert rules(out) == ['gap_open'] and out[0]['data']['first'] is True


def test_gap_open_not_when_already_open_or_page_empty():
    assert A.serp_rules('US', 'kw', [snap(1, fast=3), snap(0, fast=4)], NOW) == []
    assert A.serp_rules('US', 'kw', [snap(1, fast=9), snap(0, fast=5)], NOW) == []
    assert A.serp_rules('US', 'kw', [snap(1, fast=9), snap(0, fast=0, organic=0)], NOW) == []
    assert A.serp_rules('US', 'kw', [snap(1, fast=9), snap(0, 'empty', fast=0)], NOW) == []


@pytest.mark.parametrize('bad', BAD)
def test_gap_open_ignores_blocked_rows(bad):
    # the current row is a gap (fast 0 because nothing was read): no alert
    assert A.serp_rules('US', 'kw', [snap(1, fast=9), snap(0, bad, fast=0)], NOW) == []
    # a gap between two open readings is not a "before" above 4
    assert A.serp_rules('US', 'kw', [snap(2, fast=3), snap(1, bad, fast=12), snap(0, fast=3)], NOW) == []
    # a gap between a closed and an open reading: the closed one is the "before"
    out = A.serp_rules('US', 'kw', [snap(2, fast=9), snap(1, bad, fast=0), snap(0, fast=3)], NOW)
    assert rules(out) == ['gap_open'] and out[0]['data']['fast_before'] == 9


def test_gap_closing_vs_30_days_earlier():
    rows = [snap(31, fast=2), snap(20, fast=3), snap(10, fast=4), snap(0, fast=6)]
    out = A.serp_rules('US', 'kw', rows, NOW)
    check_shape(out)
    assert rules(out) == ['gap_closing']
    assert out[0]['data']['fast_before'] == 2 and out[0]['data']['days'] == pytest.approx(31)
    rows = [snap(30, fast=3), snap(0, fast=5)]                                # up only 2
    assert A.serp_rules('US', 'kw', rows, NOW) == []
    rows = [snap(45, fast=0), snap(0, fast=9)]                                # too old to compare
    assert A.serp_rules('US', 'kw', rows, NOW) == []
    rows = [snap(12, fast=2), snap(0, fast=7)]                                # shorter history: oldest row
    assert rules(A.serp_rules('US', 'kw', rows, NOW)) == ['gap_closing']


@pytest.mark.parametrize('bad', BAD)
def test_gap_closing_ignores_blocked_rows(bad):
    assert A.serp_rules('US', 'kw', [snap(30, bad, fast=0), snap(0, fast=6)], NOW) == []
    assert A.serp_rules('US', 'kw', [snap(30, fast=2), snap(0, bad, fast=9)], NOW) == []


# ---- serp_rules: competition and price ----

def test_competition_arriving_vs_7_day_median():
    week = [snap(6 - i, low_review_n=v) for i, v in enumerate((3, 4, 3, 5, 4))]
    out = A.serp_rules('US', 'kw', week + [snap(0, low_review_n=10)], NOW)
    check_shape(out)
    assert rules(out) == ['competition_arriving']
    assert out[0]['data']['median_7d'] == 4 and out[0]['data']['low_review_n'] == 10
    assert A.serp_rules('US', 'kw', week + [snap(0, low_review_n=8)], NOW) == []
    # only the last 7 days count
    rows = [snap(12 - i, low_review_n=0) for i in range(4)] + [snap(3 - i, low_review_n=8) for i in range(3)]
    assert A.serp_rules('US', 'kw', rows + [snap(0, low_review_n=10)], NOW) == []
    # nothing in the last 7 days to compare with
    assert A.serp_rules('US', 'kw', [snap(10, low_review_n=0), snap(0, low_review_n=12)], NOW) == []


@pytest.mark.parametrize('bad', BAD)
def test_competition_ignores_blocked_rows(bad):
    week = [snap(6 - i, low_review_n=4) for i in range(5)]
    assert A.serp_rules('US', 'kw', week + [snap(0, bad, low_review_n=30)], NOW) == []
    gaps = [snap(6 - i, bad, low_review_n=0) for i in range(5)]
    assert A.serp_rules('US', 'kw', [snap(5, low_review_n=6)] + gaps + [snap(0, low_review_n=8)], NOW) == []


def test_price_war_vs_30_days_ago():
    rows = [snap(30, price_med=30.0), snap(15, price_med=28.0), snap(0, price_med=24.0)]
    out = A.serp_rules('US', 'kw', rows, NOW)
    check_shape(out)
    assert rules(out) == ['price_war']
    a = out[0]
    assert a['data']['change'] == pytest.approx(-0.2) and a['data']['price_before'] == 30.0
    assert 'US$24.00' in a['body'] and 'US$30.00' in a['body'] and '20%' in a['body']
    assert A.serp_rules('US', 'kw', [snap(30, price_med=30.0), snap(0, price_med=26.0)], NOW) == []
    au = A.serp_rules('AU', 'kw', [snap(30, price_med=50.0), snap(0, price_med=40.0)], NOW)
    assert 'A$40.00' in au[0]['body']
    ae = A.serp_rules('AE', 'kw', [snap(30, price_med=100.0), snap(0, price_med=80.0)], NOW)
    assert 'AED 80.00' in ae[0]['body']
    assert A.serp_rules('US', 'kw', [snap(30, price_med=30.0), snap(0, price_med=0.0)], NOW) == []


@pytest.mark.parametrize('bad', BAD)
def test_price_war_ignores_blocked_rows(bad):
    assert A.serp_rules('US', 'kw', [snap(30, price_med=30.0), snap(0, bad, price_med=5.0)], NOW) == []
    assert A.serp_rules('US', 'kw', [snap(30, bad, price_med=90.0), snap(0, price_med=24.0)], NOW) == []


# ---- serp_rules: freshness and junk ----

def test_serp_needs_fresh_valid_data():
    rows = baseline() + [snap(0, bought_mid_sum=1600.0)]
    assert A.serp_rules('US', 'kw', rows, NOW + 5 * DAY) == []               # newest valid row 5 days old
    assert rules(A.serp_rules('US', 'kw', rows, NOW + 2 * DAY)) == ['demand_up']
    # rows stamped in the future are left out
    assert A.serp_rules('US', 'kw', baseline() + [snap(-2, bought_mid_sum=1600.0)], NOW) == []
    # no now_ts: the newest valid row is "now"
    assert rules(A.serp_rules('US', 'kw', rows, None)) == ['demand_up']


def test_serp_rows_are_sorted_by_ts():
    rows = baseline() + [snap(0, bought_mid_sum=1600.0)]
    random.Random(3).shuffle(rows)
    assert rules(A.serp_rules('US', 'kw', rows, NOW)) == ['demand_up']


def test_serp_junk_input_never_raises():
    for snaps in (None, [], 'abc', 42, [None, 'x', 3, {}], [{'ts': 'soon', 'status': 'ok'}],
                  [{'ts': NOW, 'status': 'ok'}], [{'ts': float('nan'), 'status': 'ok', 'fast': 1}],
                  [snap(0, fast=float('nan'), bought_mid_sum=None, price_med='x', low_review_n=float('inf'))],
                  [snap(1, status=None), snap(0, status=3)]):
        for now in (NOW, None, float('nan'), 'x'):
            out = A.serp_rules('US', 'kw', snaps, now)
            check_shape(out)
    assert A.serp_rules(None, None, [snap(0, fast=1)], NOW)[0]['dedupe_key'] == 'gap_open|||2026-W41'


def test_serp_same_week_dedupes_and_next_week_does_not():
    rows = baseline() + [snap(0, bought_mid_sum=1600.0)]
    a = A.serp_rules('US', 'kw', rows, NOW)
    b = A.serp_rules('US', 'kw', rows + [snap(-1, bought_mid_sum=1700.0)], NOW + DAY)
    assert len(A.dedupe(a + b)) == 1
    later = [dict(r, ts=r['ts'] + 7 * DAY) for r in rows]
    c = A.serp_rules('US', 'kw', later, NOW + 7 * DAY)
    assert c[0]['dedupe_key'] == 'demand_up|US|kw|2026-W42'
    assert len(A.dedupe(a + c)) == 2


# ---- list_rules (crafted features) ----

def feat(**kw):
    f = {'appearances': 3, 'valid_scans': 6, 'persistence': 0.5, 'first_seen_ts': NOW - 2 * DAY, 'streak_days': 1,
         'g': 0.2, 'bsr_slope': -0.05, 'deal_driven': False, 'surge': 40.0, 'deal_hits': 0, 'recent_hits': 1,
         'seen_before_window': True, 'rank_improving': False, 'last_rank': 1500, 'title': 'Widget'}
    f.update(kw)
    return f


def test_new_mover():
    out = A.list_rules('US', {'b0new00001': feat(recent_hits=2, seen_before_window=False)}, [], NOW)
    check_shape(out)
    assert rules(out) == ['new_mover']
    a = out[0]
    assert a['target'] == 'B0NEW00001' and a['severity'] == 2 and a['dedupe_key'].startswith('new_mover|US|B0NEW')
    # deal-driven: still listed but low severity
    out = A.list_rules('US', {'B1': feat(recent_hits=3, seen_before_window=False, deal_driven=True)}, [], NOW)
    assert rules(out) == ['new_mover'] and out[0]['severity'] == 1 and 'deal' in out[0]['body']
    # seen before the window, or only one recent hit: not new
    assert A.list_rules('US', {'B1': feat(recent_hits=3, seen_before_window=True)}, [], NOW) == []
    assert A.list_rules('US', {'B1': feat(recent_hits=1, seen_before_window=False)}, [], NOW) == []


def test_new_mover_falls_back_to_first_seen_ts():
    f = feat(recent_hits=2)
    del f['seen_before_window']
    assert rules(A.list_rules('US', {'B1': dict(f, first_seen_ts=NOW - 2 * DAY)}, [], NOW)) == ['new_mover']
    assert A.list_rules('US', {'B1': dict(f, first_seen_ts=NOW - 9 * DAY)}, [], NOW) == []


def test_persistent_riser():
    out = A.list_rules('AU', {'B1': feat(streak_days=3)}, [], NOW)
    check_shape(out)
    assert rules(out) == ['persistent_riser'] and out[0]['market'] == 'AU'
    assert '3 days in a row' in out[0]['body'] and '#1,500' in out[0]['body']
    assert rules(A.list_rules('AU', {'B1': feat(streak_days=1, rank_improving=True)}, [], NOW)) == \
        ['persistent_riser']
    assert A.list_rules('AU', {'B1': feat(streak_days=5, deal_driven=True)}, [], NOW) == []
    assert A.list_rules('AU', {'B1': feat(streak_days=2)}, [], NOW) == []


def test_list_rules_skip_products_off_every_valid_scan():
    f = feat(appearances=0, streak_days=6, recent_hits=3, seen_before_window=False, rank_improving=True)
    assert A.list_rules('US', {'B1': f}, [], NOW) == []


def test_list_features_as_list_and_cap():
    feats = [dict(feat(recent_hits=2, seen_before_window=False, surge=float(i)), asin='B%04d' % i) for i in range(30)]
    out = A.list_rules('US', feats, [], NOW)
    movers = by_rule(out, 'new_mover')
    assert len(movers) == A.THRESH['list_max_per_rule']
    assert [a['data']['surge'] for a in movers] == sorted((float(i) for i in range(5, 30)), reverse=True)


def test_cluster_surge():
    feats = {'B1': feat(surge=50.0), 'B2': feat(surge=40.0), 'B3': feat(surge=30.0), 'B4': feat(appearances=0)}
    info = [{'key': 'bamboo cutting board', 'asins': ['b1', 'B2', 'B3', 'B4']}]
    out = by_rule(A.list_rules('US', feats, info, NOW), 'cluster_surge')
    check_shape(out)
    assert len(out) == 1
    a = out[0]
    assert a['target'] == 'bamboo cutting board' and a['severity'] == 3
    assert a['data']['surge'] == pytest.approx(100 * (1 - 0.5 * 0.6 * 0.7))
    assert a['data']['n_recent'] == 3 and a['data']['recent'] == ['B1', 'B2', 'B3']
    # two recent members only
    assert by_rule(A.list_rules('US', feats, [{'key': 'k', 'asins': ['B1', 'B2', 'B4']}], NOW), 'cluster_surge') == []
    # weak members
    weak = {a: feat(surge=10.0) for a in ('B1', 'B2', 'B3')}
    assert by_rule(A.list_rules('US', weak, info, NOW), 'cluster_surge') == []
    # a given cluster surge and count win
    assert by_rule(A.list_rules('US', weak, [{'key': 'k', 'asins': ['B1'], 'surge': 70, 'n_recent': 3}], NOW),
                   'cluster_surge')
    assert not by_rule(A.list_rules('US', feats, [dict(info[0], surge=55)], NOW), 'cluster_surge')
    # no key: named by tokens, then by the first ASIN; nothing to name it by: skipped
    out = by_rule(A.list_rules('US', feats, [{'asins': ['B1', 'B2', 'B3'], 'tokens': {'board', 'bamboo'}}], NOW),
                  'cluster_surge')
    assert out[0]['target'] == 'bamboo board'
    out = by_rule(A.list_rules('US', feats, [{'asins': ['B1', 'B2', 'B3']}], NOW), 'cluster_surge')
    assert out[0]['target'] == 'B1'
    assert by_rule(A.list_rules('US', feats, [{'asins': [], 'surge': 90, 'n_recent': 5}], NOW), 'cluster_surge') == []


def list_history(new_status=('ok', 'ok')):
    """Ten daily Movers & Shakers scans; B0OLD0000A on all of them, B0NEW0000B on the last two."""
    scans = []
    for d in range(10):
        status = new_status[d - 8] if d >= 8 else 'ok'
        items = [{'asin': 'B0OLD0000A', 'pos': 5, 'pct': 50, 'rank_now': 900 - 20 * d, 'rank_before': 1000,
                  'price': 20.0, 'reviews': 300, 'title': 'Old steel water bottle', 'deal': False}]
        if d >= 8:
            items.append({'asin': 'B0NEW0000B', 'pos': 3, 'pct': 400, 'rank_now': 500 - 100 * (d - 8),
                          'rank_before': 2500, 'price': 25.0, 'reviews': 4, 'title': 'Collapsible silicone kettle',
                          'deal': False})
        scans.append({'ts': NOW - (9 - d) * DAY - 3600, 'day': None, 'market': 'US', 'kind': 'movers',
                      'slug': 'kitchen', 'n': 50, 'n_median': 50, 'status': status, 'items': items})
    return scans


def test_list_rules_from_surge_features():
    feats = surge.all_features(list_history())
    out = A.list_rules('US', feats, [], NOW)
    check_shape(out)
    movers = by_rule(out, 'new_mover')
    assert [a['target'] for a in movers] == ['B0NEW0000B']
    assert 'B0OLD0000A' in [a['target'] for a in by_rule(out, 'persistent_riser')]


@pytest.mark.parametrize('bad', BAD)
def test_list_rules_ignore_blocked_scans(bad):
    for statuses in ((bad, bad), ('ok', bad), (bad, 'ok')):
        feats = surge.all_features(list_history(statuses))
        out = A.list_rules('US', feats, [], NOW)
        check_shape(out)
        assert 'B0NEW0000B' not in [a['target'] for a in by_rule(out, 'new_mover')]


def test_list_rules_junk_input_never_raises():
    for feats in (None, [], {}, 'x', [None, 1, {'asin': None}], {'B1': None, 'B2': 'x', '': feat()},
                  {'B1': {k: float('nan') for k in feat()}}, {'B1': {'surge': 'high', 'recent_hits': 'two'}}):
        for infos in (None, [], 'x', [None, {}, {'asins': None}, {'asins': 'B1', 'surge': float('nan')}]):
            for now in (NOW, None, float('nan')):
                check_shape(A.list_rules('US', feats, infos, now))


# ---- trend_rules ----

def test_trend_label_change():
    out = A.trend_rules('yoga mat', 'US', {'label': 'Growing'}, {'label': 'Declining', 'why': ['searches down 30%']},
                        NOW)
    check_shape(out)
    assert rules(out) == ['trend_label_change']
    a = out[0]
    assert a['target'] == 'yoga mat' and a['market'] == 'US' and a['severity'] == 2
    assert a['data']['old_base'] == 'Growing' and a['data']['new_base'] == 'Declining'
    assert 'searches down 30%' in a['body']
    # a modifier only, or no change: nothing
    assert A.trend_rules('k', 'US', {'label': 'Growing'}, {'label': 'Growing, seasonal peak Dec'}, NOW) == []
    assert A.trend_rules('k', 'US', {'label': 'Seasonal'}, {'label': 'Seasonal'}, NOW) == []
    # into or out of Insufficient, or no old features: nothing
    assert A.trend_rules('k', 'US', {'label': 'Insufficient'}, {'label': 'Growing'}, NOW) == []
    assert A.trend_rules('k', 'US', {'label': 'Growing'}, {'label': 'Insufficient'}, NOW) == []
    assert A.trend_rules('k', 'US', None, {'label': 'Growing'}, NOW) == []
    # label() output as a (label, why) pair
    out = A.trend_rules('k', '', {'label': ('Evergreen', [])}, {'label': ('Fad', ['peaked 18 months ago'])}, NOW)
    assert rules(out) == ['trend_label_change'] and out[0]['data']['why'] == ['peaked 18 months ago']
    assert out[0]['market'] == '' and 'worldwide' in out[0]['title']


def test_trends_breakout():
    out = A.trend_rules('k', 'AU', {'label': 'Growing'}, {'label': 'Breakout'}, NOW)
    assert sorted(rules(out)) == ['trend_label_change', 'trends_breakout']
    assert by_rule(out, 'trends_breakout')[0]['data']['label_breakout'] is True
    # already Breakout last time: no repeat
    assert A.trend_rules('k', 'AU', {'label': 'Breakout'}, {'label': 'Breakout'}, NOW) == []
    # a new related query marked Breakout
    new = {'label': 'Growing', 'related': [{'query': 'Ice Roller', 'formattedValue': 'Breakout'},
                                           {'query': 'face mask', 'value': 150}]}
    out = A.trend_rules('k', 'AU', {'label': 'Growing'}, new, NOW)
    check_shape(out)
    assert rules(out) == ['trends_breakout'] and out[0]['data']['queries'] == ['ice roller']
    # the same query was already Breakout last time
    assert A.trend_rules('k', 'AU', {'label': 'Growing', 'breakout_queries': ('ice roller',)}, new, NOW) == []
    # other forms: list, flag, numeric value
    assert rules(A.trend_rules('k', 'AU', {}, {'label': 'Growing', 'breakout_queries': ('x', 'y')}, NOW)) == \
        ['trends_breakout']
    assert rules(A.trend_rules('k', 'AU', {}, {'label': 'Growing', 'related_breakout': True}, NOW)) == \
        ['trends_breakout']
    assert rules(A.trend_rules('k', 'AU', {}, {'label': 'Growing', 'rising': [{'query': 'z', 'value': 6000}]},
                               NOW)) == ['trends_breakout']


def test_confirmed_trend():
    new = {'label': 'Emerging', 'cluster_surge': 72, 'autocomplete': True}
    out = A.trend_rules('ice roller', 'US', {'label': 'Emerging'}, new, NOW)
    check_shape(out)
    assert rules(out) == ['confirmed_trend']
    assert out[0]['severity'] == 3 and out[0]['data']['cluster_surge'] == 72
    for lab in ('Breakout', 'Growing', 'Growing, seasonal peak Dec'):
        got = A.trend_rules('k', 'US', {'label': lab}, dict(new, label=lab), NOW)
        assert 'confirmed_trend' in rules(got)
    assert rules(A.trend_rules('k', 'US', None, dict(new, cluster_surge=True), NOW)) == ['confirmed_trend']
    assert A.trend_rules('k', 'US', None, dict(new, autocomplete=False), NOW) == []
    assert A.trend_rules('k', 'US', None, dict(new, cluster_surge=40), NOW) == []
    assert A.trend_rules('k', 'US', None, {'label': 'Emerging', 'autocomplete': True}, NOW) == []
    for lab in ('Seasonal', 'Fad', 'Declining', 'Evergreen', 'Spike (watch)'):
        assert A.trend_rules('k', 'US', None, dict(new, label=lab), NOW) == []


@pytest.mark.parametrize('bad', BAD)
def test_trend_rules_ignore_blocked_samples(bad):
    new = {'label': 'Breakout', 'cluster_surge': 90, 'autocomplete': True, 'breakout_queries': ['q'], 'status': bad}
    assert A.trend_rules('k', 'US', {'label': 'Growing', 'status': 'ok'}, new, NOW) == []
    # a blocked old sample is not the "before": no label change from it
    out = A.trend_rules('k', 'US', {'label': 'Declining', 'status': bad}, {'label': 'Growing', 'status': 'ok'}, NOW)
    assert out == []


def test_trend_rules_junk_input_never_raises():
    for old in (None, 'x', {}, {'label': None}, {'label': 3, 'related': 'x'}, {'label': []}):
        for new in (None, 'x', {}, {'label': float('nan')}, {'label': ('Growing',), 'why': 'one reason'},
                    {'label': 'Breakout', 'related': [None, {'query': None, 'value': 'Breakout'}],
                     'cluster_surge': 'x', 'autocomplete': 'yes'}):
            for now in (NOW, None, 'x'):
                check_shape(A.trend_rules('k', 'US', old, new, now))
                check_shape(A.trend_rules(None, None, old, new, now))


# ---- ops_rules ----

def test_captcha_needed_and_source_paused():
    ctx = {'source_state': {
        'amazon:AU': {'reason': 'captcha', 'cooldown_until': NOW + 3600, 'last_block': NOW - 60,
                      'last_ok': NOW - 7200, 'fail_streak': 1},
        'amazon:US': {'reason': 'timeout', 'cooldown_until': NOW + 7 * 3600},
        'complete:AE': {'reason': 'error', 'cooldown_until': NOW + 1800},
        'google': {'reason': 'rate_limited', 'cooldown_until': NOW + 120},           # a blip: no alert
        'amazon:AE': {'reason': 'captcha', 'last_block': NOW - 7200, 'last_ok': NOW - 60},   # solved since
    }}
    out = A.ops_rules(ctx, NOW)
    check_shape(out)
    cap = by_rule(out, 'captcha_needed')
    assert [(a['market'], a['target']) for a in cap] == [('AU', 'amazon:AU')]
    assert cap[0]['severity'] == 3 and 'Solve now' in cap[0]['body'] and '1 h' in cap[0]['body']
    paused = {a['target']: a for a in by_rule(out, 'source_paused')}
    assert set(paused) == {'amazon:US', 'complete:AE'}
    assert paused['amazon:US']['severity'] == 3 and paused['complete:AE']['severity'] == 2
    assert paused['complete:AE']['market'] == 'AE'
    # a list form and a manual pause
    out = A.ops_rules({'source_state': [{'source': 'google', 'paused': True}]}, NOW)
    assert rules(out) == ['source_paused'] and out[0]['market'] == ''


def test_stale_data_grouped_per_market_and_kind():
    stale = [
        {'kind': 'keyword', 'market': 'US', 'target': 'yoga mat', 'last_ok_ts': NOW - 4 * DAY, 'cadence_h': 24},
        {'kind': 'keyword', 'market': 'US', 'target': 'ice roller', 'last_ok_ts': NOW - 5 * DAY, 'every_s': DAY},
        {'kind': 'keyword', 'market': 'US', 'target': 'fresh', 'last_ok_ts': NOW - DAY, 'cadence_h': 24},
        {'kind': 'asin', 'market': 'AU', 'display': 'Widget', 'stale': True},
        {'kind': 'asin', 'market': 'AU', 'display': 'Gadget', 'stale': False},
    ]
    out = A.ops_rules({'stale': stale}, NOW)
    check_shape(out)
    assert rules(out) == ['stale_data', 'stale_data']
    us = [a for a in out if a['market'] == 'US'][0]
    assert us['data']['n'] == 2 and set(us['data']['targets']) == {'yoga mat', 'ice roller'}
    assert us['data']['oldest_age_days'] == pytest.approx(5) and us['target'] == 'keyword'
    au = [a for a in out if a['market'] == 'AU'][0]
    assert au['data']['n'] == 1 and au['data']['targets'] == ['Widget']


def test_layout_drift_canary():
    health = {'US': {'badges_last_2_days': 0, 'badges_before': 120, 'search_pages_last_2_days': 14},
              'AU': {'list_n_vs_median': 0.5},
              'AE': {'product_rank_missing_share': 0.9, 'product_pages': 10}}
    out = A.ops_rules({'parse_health': health}, NOW)
    check_shape(out)
    got = {(a['market'], a['target']) for a in by_rule(out, 'layout_drift')}
    assert got == {('US', 'search'), ('AU', 'list'), ('AE', 'product')}
    assert all(a['severity'] == 3 for a in out)
    # healthy numbers
    ok = {'US': {'badges_last_2_days': 40, 'badges_before': 120, 'list_n_vs_median': 0.95,
                 'product_rank_missing_share': 0.1, 'product_pages': 20},
          'AU': {'badges_last_2_days': 0, 'badges_before': 0},                    # never had badges
          'AE': {'list_n_vs_median': 0.6, 'product_rank_missing_share': 0.8}}       # on the threshold
    assert A.ops_rules({'parse_health': ok}, NOW) == []
    # other forms: an (n, median) pair or a dict, and a list of per-market rows
    assert by_rule(A.ops_rules({'parse_health': {'US': {'list_n_vs_median': (25, 50)}}}, NOW), 'layout_drift')
    assert by_rule(A.ops_rules({'parse_health': {'US': {'list_n_vs_median': {'n': 40, 'median': 50}}}}, NOW),
                   'layout_drift') == []
    assert by_rule(A.ops_rules({'health': [{'market': 'au', 'list_n_vs_median': 0.1}]}, NOW), 'layout_drift')


def test_layout_drift_not_on_blocked_gaps():
    # no valid search pages in the last 2 days (all blocked): zero badges is a gap, not drift
    health = {'US': {'badges_last_2_days': 0, 'badges_before': 120, 'search_pages_last_2_days': 0}}
    assert A.ops_rules({'parse_health': health}, NOW) == []
    # too few product pages to judge
    health = {'US': {'product_rank_missing_share': 1.0, 'product_pages': 2}}
    assert A.ops_rules({'parse_health': health}, NOW) == []


def test_fees_stale():
    old = (datetime.fromtimestamp(NOW, timezone.utc).date() - timedelta(days=100)).isoformat()
    recent = (datetime.fromtimestamp(NOW, timezone.utc).date() - timedelta(days=30)).isoformat()
    out = A.ops_rules({'fees_as_of': old}, NOW)
    check_shape(out)
    assert rules(out) == ['fees_stale'] and out[0]['data']['age_days'] == 100
    assert A.ops_rules({'fees_as_of': recent}, NOW) == []
    out = A.ops_rules({'fees_as_of': {'US': recent, 'AU': old, 'AE': 'unknown'}}, NOW)
    assert [(a['rule'], a['market']) for a in out] == [('fees_stale', 'AU')]
    assert A.ops_rules({'fees_as_of': '2099-01-01'}, NOW) == []


def test_rerun_xray():
    watched = [{'market': 'AU', 'target': 'yoga mat', 'last_xray_import_ts': NOW - 40 * DAY},
               {'market': 'US', 'target': 'ice roller', 'last_xray_import_ts': NOW - 10 * DAY},
               {'market': 'US', 'target': 'old idea', 'last_xray_import_ts': NOW - 90 * DAY, 'stage': 'Rejected'},
               {'market': 'US', 'target': 'paused', 'last_xray_import_ts': NOW - 90 * DAY, 'enabled': False},
               {'market': 'AE', 'target': 'never imported', 'last_xray_import_ts': None}]
    out = A.ops_rules({'watched': watched}, NOW)
    check_shape(out)
    assert [(a['rule'], a['market'], a['target']) for a in out] == [('rerun_xray', 'AU', 'yoga mat')]
    assert out[0]['severity'] == 1 and out[0]['data']['age_days'] == pytest.approx(40)


def test_ops_rules_junk_input_never_raises():
    assert A.ops_rules(None, NOW) == [] and A.ops_rules({}, NOW) == [] and A.ops_rules('x', NOW) == []
    assert A.ops_rules({'fees_as_of': '2020-01-01'}, None) == []
    junk = {'source_state': {'a': None, 'b': {'cooldown_until': 'x', 'reason': 5}, 'c': {'cooldown_until': NOW * 1e300}},
            'stale': [None, {}, {'kind': None, 'last_ok_ts': 'x', 'cadence_h': float('nan')}],
            'parse_health': {'US': None, 'AU': {'badges_last_2_days': 'x', 'list_n_vs_median': [1, 0]},
                             'AE': {'list_n_vs_median': float('nan'), 'product_rank_missing_share': None}},
            'fees_as_of': ['2020-01-01'], 'watched': 'x'}
    for now in (NOW, float('inf'), -1e20):
        check_shape(A.ops_rules(junk, now))


# ---- order_by_rule ----

def iso(days):
    return (datetime.fromtimestamp(NOW, timezone.utc).date() + timedelta(days=days)).isoformat()


def plan(sea_days, air_days, **kw):
    p = {'status': 'ok', 'peak_month': 12, 'upswing_month': 11,
         'sea': {'order_by': iso(sea_days), 'ship_by': iso(sea_days + 30), 'in_stock_by': iso(sea_days + 80)},
         'air': {'order_by': iso(air_days), 'ship_by': iso(air_days + 30), 'in_stock_by': iso(air_days + 50)}}
    p.update(kw)
    return p


def test_order_by_sea_within_14_days():
    out = A.order_by_rule([{'market': 'US', 'target': 'yoga mat', 'plan': plan(10, 40)}], NOW)
    check_shape(out)
    assert rules(out) == ['order_by']
    a = out[0]
    assert a['severity'] == 3 and a['target'] == 'yoga mat' and a['market'] == 'US'
    assert a['data']['mode'] == 'sea' and a['data']['days_left'] == 10 and a['data']['order_by'] == iso(10)
    assert 'Dec peak' in a['body'] and 'Too late' not in a['body']
    today = A.order_by_rule([{'market': 'US', 'target': 'k', 'plan': plan(0, 30)}], NOW)
    assert 'today' in today[0]['body']
    assert A.order_by_rule([{'market': 'US', 'target': 'k', 'plan': plan(20, 50)}], NOW) == []


def test_order_by_falls_back_to_air():
    out = A.order_by_rule([{'market': 'AU', 'target': 'k', 'plan': plan(-3, 5)}], NOW)
    assert rules(out) == ['order_by'] and out[0]['data']['mode'] == 'air'
    assert out[0]['body'].startswith('Too late for sea freight.')
    assert A.order_by_rule([{'market': 'AU', 'target': 'k', 'plan': plan(-40, -10)}], NOW) == []


def test_order_by_skips_unknown_and_disabled_and_reads_flat_plans():
    assert A.order_by_rule([{'market': 'US', 'target': 'k', 'plan': plan(5, 30, status='unknown')}], NOW) == []
    assert A.order_by_rule([{'market': 'US', 'target': 'k', 'enabled': False, 'plan': plan(5, 30)}], NOW) == []
    flat = A.order_by_rule([{'market': 'AE', 'target': 'k', 'order_by': iso(7), 'mode': 'air'}], NOW)
    assert rules(flat) == ['order_by'] and flat[0]['data']['mode'] == 'air'
    inline = A.order_by_rule([dict(plan(3, 30), market='US', target='k')], NOW)
    assert rules(inline) == ['order_by']


def test_order_by_junk_input_never_raises():
    for items in (None, [], 'x', [None, {}, {'plan': None}, {'plan': {'sea': 'x'}},
                                 {'plan': {'sea': {'order_by': 'not a date'}}}, {'order_by': 12345}]):
        for now in (NOW, None, float('nan')):
            check_shape(A.order_by_rule(items, now))


# ---- all rules together ----

def test_all_alerts_json_safe_and_dedupe_across_rules():
    out = (A.serp_rules('US', 'kw', baseline() + [snap(0, bought_mid_sum=1600.0, fast=2)], NOW)
           + A.list_rules('US', {'B1': feat(streak_days=4)}, [], NOW)
           + A.trend_rules('kw', 'US', {'label': 'Growing'}, {'label': 'Breakout'}, NOW)
           + A.ops_rules({'fees_as_of': '2020-01-01'}, NOW)
           + A.order_by_rule([{'market': 'US', 'target': 'kw', 'plan': plan(1, 20)}], NOW))
    check_shape(out)
    assert set(rules(out)) == {'demand_up', 'gap_open', 'persistent_riser', 'trend_label_change',
                               'trends_breakout', 'fees_stale', 'order_by'}
    assert len(A.dedupe(out + out)) == len(out)
    for a in out:
        assert not any(isinstance(v, float) and math.isnan(v) for v in a['data'].values())
