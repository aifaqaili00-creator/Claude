"""Tests for engine/surge.py. Run with:  python -m pytest -q tests/test_engine_surge.py"""
import json
import math
import random
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import surge as sg  # noqa: E402

DAY = 86400
T0 = 1_760_000_000              # a fixed unix time (Oct 2025), scans are T0 + d days
SPEC_KEYS = {'appearances', 'valid_scans', 'persistence', 'first_seen_ts', 'streak_days', 'g', 'bsr_slope',
             'deal_driven', 'surge'}


def json_safe(x):
    """Raises if x holds NaN / inf or anything json can't write."""
    return json.dumps(x, allow_nan=False)


def item(asin, rank_now=None, rank_before=None, price=20.0, deal=False, title='Orthopedic Dog Bed', pos=1):
    return {'asin': asin, 'pos': pos, 'pct': None, 'rank_now': rank_now, 'rank_before': rank_before,
            'price': price, 'reviews': 10, 'title': title, 'deal': deal}


def filler(k, n):
    """n other products so the scan looks like a full list."""
    return [item('F%s%04d' % (k, i), rank_now=1000 + i, rank_before=1100 + i, title='Other thing %d' % i, pos=i + 2)
            for i in range(n)]


def scan(d, items, status='ok', n=None, n_median=50, slug='kitchen', kind='movers', market='US', hour=0):
    ts = T0 + d * DAY + hour * 3600
    return {'ts': ts, 'day': None, 'market': market, 'kind': kind, 'slug': slug,
            'n': len(items) if n is None else n, 'n_median': n_median, 'status': status, 'items': items}


def full(d, target_items, **kw):
    """A 50-item scan on day d holding target_items plus filler."""
    its = list(target_items)
    return scan(d, its + filler(kw.get('slug', 'kitchen')[:1], 50 - len(its)), **kw)


# ---- shape and missing data ----

def test_thresh_is_a_dict():
    assert isinstance(sg.THRESH, dict)
    assert sg.THRESH['valid_n_share'] == 0.8 and sg.THRESH['deal_price_ratio'] == 0.8
    assert sg.THRESH['cluster_jaccard'] == 0.6


@pytest.mark.parametrize('history', [None, [], 'junk', 5, [None, 'x', {}], [{'ts': None, 'items': None}],
                                     [{'ts': float('nan'), 'status': 'ok', 'items': [item('A1')]}]])
def test_empty_or_bad_history(history):
    f = sg.asin_features(history, 'A1')
    assert SPEC_KEYS <= set(f)
    assert f['appearances'] == 0 and f['valid_scans'] == 0 and f['persistence'] is None
    assert f['first_seen_ts'] is None and f['streak_days'] == 0 and f['g'] is None and f['bsr_slope'] is None
    assert f['deal_driven'] is False and f['surge'] == 0.0
    json_safe(f)
    assert sg.rank_history(history, 'A1') == []
    assert sg.all_features(history) == {}


def test_unknown_or_blank_asin():
    h = [full(d, [item('A1', 500, 600)]) for d in range(7)]
    for a in ('ZZZ', None, ''):
        f = sg.asin_features(h, a)
        assert f['appearances'] == 0 and f['surge'] == 0.0 and f['persistence'] is None
    assert sg.rank_history(h, None) == []


def test_asin_match_ignores_case_and_spaces():
    h = [full(d, [item('B0ABC', 500, 600)]) for d in range(5)]
    assert sg.asin_features(h, ' b0abc ')['appearances'] == 5


# ---- persistence and valid scans ----

def test_persistence_excludes_partial_scans():
    h = []
    for d in range(7):
        if d in (2, 4):                               # partial: only 20 of the usual 50 read
            h.append(scan(d, [item('A1', 500, 600)] + filler('k', 19), status='partial'))
        else:
            h.append(full(d, [item('A1', 500, 600)]))
    f = sg.asin_features(h, 'A1')
    assert f['valid_scans'] == 5
    assert f['appearances'] == 5
    assert f['persistence'] == 1.0


def test_absence_on_partial_scan_is_not_evidence():
    # the product is missing from the two partial scans only: persistence stays 1
    h = [scan(d, filler('k', 20), status='partial') if d in (2, 4) else full(d, [item('A1', 500, 600)])
         for d in range(7)]
    assert sg.asin_features(h, 'A1')['persistence'] == 1.0


def test_absence_on_full_scan_counts():
    h = [full(d, [item('A1', 500, 600)]) if d in (0, 2, 4) else full(d, []) for d in range(5)]
    f = sg.asin_features(h, 'A1')
    assert f['valid_scans'] == 5 and f['appearances'] == 3 and f['persistence'] == 0.6


def test_short_ok_scan_is_not_valid_but_partial_full_length_is():
    h = [full(0, [item('A1')]),
         scan(1, [item('A1')] + filler('k', 38), status='ok'),          # n=39 < 40: not valid
         scan(2, [item('A1')] + filler('k', 39), status='partial'),     # n=40 = 80%: valid
         full(3, [])]
    f = sg.asin_features(h, 'A1')
    assert f['valid_scans'] == 3 and f['appearances'] == 2
    assert f['persistence'] == pytest.approx(2 / 3, abs=1e-3)


@pytest.mark.parametrize('status', ['blocked', 'captcha', 'wrong_location', 'error', None, ''])
def test_bad_status_scans_are_ignored(status):
    h = [full(0, [item('A1', 900, 1000)], status=status), full(1, [item('A1', 500, 600)]), full(2, [])]
    f = sg.asin_features(h, 'A1')
    assert f['valid_scans'] == 2 and f['appearances'] == 1 and f['persistence'] == 0.5
    assert f['first_seen_ts'] == T0 + DAY                 # the bad scan doesn't count as seen
    assert sg.rank_history(h, 'A1') == [(T0 + DAY, 500)]


def test_status_is_case_insensitive():
    h = [full(0, [item('A1')], status='OK'), full(1, [item('A1')], status=' Partial ')]
    assert sg.asin_features(h, 'A1')['valid_scans'] == 2


def test_missing_n_median_uses_the_list_median():
    h = [full(d, [item('A1')], n_median=None) for d in range(4)]
    h.append(scan(4, [item('A1')] + filler('k', 9), n_median=None))   # 10 items vs median 50 -> not valid
    f = sg.asin_features(h, 'A1')
    assert f['valid_scans'] == 4 and f['appearances'] == 4


def test_missing_n_uses_item_count():
    h = [full(d, [item('A1')]) for d in range(3)]
    h[1]['n'] = None
    assert sg.asin_features(h, 'A1')['valid_scans'] == 3
    h[1]['items'] = h[1]['items'][:10]                     # n None, only 10 items -> short scan
    assert sg.asin_features(h, 'A1')['valid_scans'] == 2


def test_only_lists_the_product_was_on_count():
    h = [full(d, [item('A1', 500, 600)]) for d in range(4)]
    h += [full(d, [], slug='garden') for d in range(4)]          # another category, never had A1
    h += [full(d, [], kind='movers', slug='kitchen', market='AU') for d in range(4)]
    f = sg.asin_features(h, 'A1')
    assert f['valid_scans'] == 4 and f['persistence'] == 1.0


def test_both_lists_count_as_scans():
    h = []
    for d in range(4):
        h.append(full(d, [item('A1', 500, 600)], kind='movers'))
        h.append(full(d, [item('A1')] if d >= 2 else [], kind='new', hour=1))
    f = sg.asin_features(h, 'A1')
    assert f['valid_scans'] == 8 and f['appearances'] == 6 and f['persistence'] == 0.75


def test_duplicate_rows_in_one_scan_count_once():
    h = [scan(0, [item('A1'), item('A1')] + filler('k', 48))]
    assert sg.asin_features(h, 'A1')['appearances'] == 1


def test_window_by_days():
    # 14 daily scans, the product only in the first 7 days
    h = [full(d, [item('A1', 500, 600)] if d < 7 else []) for d in range(14)]
    f7 = sg.asin_features(h, 'A1', days=7)
    assert f7['valid_scans'] == 7 and f7['appearances'] == 0 and f7['persistence'] == 0.0
    assert f7['seen_before_window'] is True and f7['first_seen_ts'] == T0
    assert f7['surge'] == 0.0
    f14 = sg.asin_features(h, 'A1', days=14)
    assert f14['valid_scans'] == 14 and f14['appearances'] == 7 and f14['persistence'] == 0.5
    assert f14['seen_before_window'] is False
    # a bad days value falls back to 7
    assert sg.asin_features(h, 'A1', days=None)['valid_scans'] == 7
    assert sg.asin_features(h, 'A1', days=-3)['valid_scans'] == 7


def test_history_order_does_not_matter():
    h = [full(d, [item('A1', 1000 - 100 * d, 1100 - 100 * d)] if d % 3 else []) for d in range(8)]
    a = sg.asin_features(h, 'A1')
    b = sg.asin_features(list(reversed(h)), 'A1')
    random.Random(1).shuffle(h)
    c = sg.asin_features(h, 'A1')
    assert a == b == c


# ---- rank gain, slope and the surge score ----

def test_velocity_g_and_full_surge():
    # rank 10x better than the day before, and rank_now halves every week
    h = [full(d, [item('A1', 1000 * 0.5 ** (d / 7), 10000 * 0.5 ** (d / 7))]) for d in range(7)]
    f = sg.asin_features(h, 'A1')
    assert f['g'] == pytest.approx(math.log(10), abs=1e-3)
    assert f['bsr_slope'] == pytest.approx(-math.log(2) / 7, abs=1e-4)
    assert f['persistence'] == 1.0
    assert f['surge'] == pytest.approx(100.0, abs=0.2)


def test_surge_formula_by_hand():
    # present on 2 of 4 valid scans, rank_before/rank_now = sqrt(10), flat rank -> 100*(.4*.5 + .3*.5 + 0) = 35
    r = 1000
    h = [full(0, [item('A1', r, r * math.sqrt(10))]), full(1, []),
         full(2, [item('A1', r, r * math.sqrt(10))]), full(3, [])]
    f = sg.asin_features(h, 'A1')
    assert f['persistence'] == 0.5
    assert f['g'] == pytest.approx(math.log(10) / 2, abs=1e-4)
    assert f['bsr_slope'] is None                        # 2 readings: no slope
    assert f['surge'] == pytest.approx(35.0, abs=0.1)
    assert sg.surge_score(0.5, math.log(10) / 2, 0.0) == pytest.approx(35.0, abs=0.1)
    assert sg.surge_score(None, None, None) == 0.0
    assert sg.surge_score(2.0, 99.0, -99.0) == 100.0      # every part is clipped to 1
    assert sg.surge_score(0.0, -5.0, 5.0) == 0.0          # falling ranks add nothing


def test_falling_product_scores_low():
    h = [full(d, [item('A1', 1000 * 2 ** (d / 7), 900 * 2 ** (d / 7))]) for d in range(7)]
    f = sg.asin_features(h, 'A1')
    assert f['g'] < 0 and f['bsr_slope'] > 0
    assert f['surge'] == pytest.approx(40.0, abs=0.1)      # persistence only
    assert f['rank_improving'] is False


def test_surge_monotonic_in_climb_speed():
    def surge_for(rate):
        h = [full(d, [item('A1', 5000 * math.exp(-rate * d), 5000 * math.exp(-rate * (d - 1)))]) for d in range(7)]
        return sg.asin_features(h, 'A1')['surge']
    vals = [surge_for(r) for r in (0.0, 0.02, 0.05, 0.08)]
    assert all(a < b for a, b in zip(vals, vals[1:]))


def test_new_releases_without_ranks():
    h = [full(d, [item('A1')], kind='new') for d in range(5)]
    f = sg.asin_features(h, 'A1')
    assert f['g'] is None and f['bsr_slope'] is None and f['last_rank'] is None
    assert f['persistence'] == 1.0 and f['surge'] == 40.0
    json_safe(f)


def test_slope_uses_one_category():
    # ranks in 'kitchen' climb; one stray reading in 'home' (a different rank scale) must not mix in
    h = [full(d, [item('A1', 1000 - 100 * d, 1100 - 100 * d)]) for d in range(5)]
    h.append(full(5, [item('A1', 90000, 95000)], slug='home'))
    f = sg.asin_features(h, 'A1')
    assert f['bsr_slope'] < 0
    expected = np.polyfit(np.arange(5.0), np.log([1000, 900, 800, 700, 600]), 1)[0]
    assert f['bsr_slope'] == pytest.approx(expected, abs=1e-4)
    assert f['last_rank'] == 600
    assert [r for _, r in sg.rank_history(h, 'A1')] == [1000, 900, 800, 700, 600]


def test_bad_rank_values_are_skipped():
    h = [full(0, [item('A1', float('nan'), 600)]), full(1, [item('A1', 0, -5)]),
         full(2, [item('A1', 'x', None)]), full(3, [item('A1', 400, 500)])]
    f = sg.asin_features(h, 'A1')
    assert f['g'] == pytest.approx(math.log(500 / 400), abs=1e-4)
    assert f['bsr_slope'] is None
    json_safe(f)
    assert sg.rank_history(h, 'A1') == [(T0 + 3 * DAY, 400)]


def test_rank_improving():
    h = [full(d, [item('A1', r, r + 50)]) for d, r in enumerate([900, 950, 800, 700, 600])]
    assert sg.asin_features(h, 'A1')['rank_improving'] is True
    h = [full(d, [item('A1', r, r + 50)]) for d, r in enumerate([900, 800, 700, 750])]
    assert sg.asin_features(h, 'A1')['rank_improving'] is False


# ---- deal-driven appearances ----

def test_deal_flag_excluded_from_persistence():
    h = [full(d, [item('A1', 500, 600, deal=(d < 4))]) for d in range(6)]
    f = sg.asin_features(h, 'A1')
    assert f['appearances'] == 6 and f['deal_hits'] == 4
    assert f['persistence'] == pytest.approx(2 / 6, abs=1e-3)
    assert f['deal_driven'] is True


def test_one_deal_day_is_not_deal_driven():
    h = [full(d, [item('A1', 500, 600, deal=(d == 3))]) for d in range(7)]
    f = sg.asin_features(h, 'A1')
    assert f['deal_driven'] is False and f['deal_hits'] == 1
    assert f['persistence'] == pytest.approx(6 / 7, abs=1e-3)


def test_price_cut_counts_as_deal():
    # 20 -> 15 is a 25% cut (below 80% of 20 = 16); 20 -> 17 is not
    h = [full(0, [item('A1', 500, 600, price=20.0)]), full(1, [item('A1', 300, 600, price=15.0)])]
    f = sg.asin_features(h, 'A1')
    assert f['deal_hits'] == 1 and f['persistence'] == 0.5 and f['deal_driven'] is True
    assert f['g'] == pytest.approx(math.log(600 / 500), abs=1e-4)   # the deal day's jump is left out
    h[1]['items'][0]['price'] = 17.0
    f = sg.asin_features(h, 'A1')
    assert f['deal_hits'] == 0 and f['persistence'] == 1.0 and f['deal_driven'] is False


def test_long_price_cut_stays_deal_driven():
    # the reference is the last normal price, so a cut that lasts several days keeps counting as a deal
    prices = [20, 20, 14, 14, 14, 14]
    h = [full(d, [item('A1', 500, 600, price=p)]) for d, p in enumerate(prices)]
    f = sg.asin_features(h, 'A1')
    assert f['deal_hits'] == 4 and f['deal_driven'] is True


def test_price_back_up_is_normal_again():
    prices = [20, 14, 20, 20, None]
    h = [full(d, [item('A1', 500, 600, price=p)]) for d, p in enumerate(prices)]
    f = sg.asin_features(h, 'A1')
    assert f['deal_hits'] == 1 and f['deal_driven'] is False


def test_price_cut_uses_earlier_price_seen_outside_window():
    h = [full(0, [item('A1', 500, 600, price=30.0)])] + [full(d, []) for d in range(1, 9)]
    h.append(full(9, [item('A1', 400, 500, price=20.0)]))
    f = sg.asin_features(h, 'A1', days=7)
    assert f['appearances'] == 1 and f['deal_hits'] == 1 and f['deal_driven'] is True


# ---- first seen, streak, recent hits ----

def test_first_seen_and_streak():
    h = [full(d, [item('A1', 500, 600)] if d >= 3 else []) for d in range(8)]
    f = sg.asin_features(h, 'A1')
    assert f['first_seen_ts'] == T0 + 3 * DAY
    assert f['streak_days'] == 5
    assert f['recent_hits'] == 3
    assert f['seen_before_window'] is False


def test_streak_breaks_on_a_missing_day():
    h = [full(d, [] if d == 6 else [item('A1', 500, 600)]) for d in range(8)]
    f = sg.asin_features(h, 'A1')
    assert f['streak_days'] == 1
    assert f['recent_hits'] == 2


def test_streak_ends_today_or_is_zero():
    h = [full(d, [item('A1')] if d < 5 else []) for d in range(6)]
    assert sg.asin_features(h, 'A1')['streak_days'] == 0


def test_streak_skips_days_without_valid_scans():
    # day 4 has only a partial scan, day 5 no scan at all: neither breaks the streak
    h = [full(d, [item('A1')]) for d in (0, 1, 2, 3, 6, 7)]
    h.append(scan(4, filler('k', 10), status='partial'))
    f = sg.asin_features(h, 'A1')
    assert f['streak_days'] == 6


def test_streak_two_scans_a_day_needs_one_hit():
    h = []
    for d in range(4):
        h.append(full(d, [item('A1')], hour=1))
        h.append(full(d, [], hour=13))
    assert sg.asin_features(h, 'A1')['streak_days'] == 4


def test_streak_uses_stored_day():
    h = [full(d, [item('A1')]) for d in range(3)]
    for s, day in zip(h, ('2025-10-01', '2025-10-02', '2025-10-02')):
        s['day'] = day
    assert sg.asin_features(h, 'A1')['streak_days'] == 2


def test_deal_day_breaks_streak():
    h = [full(d, [item('A1', 500, 600, deal=(d == 5))]) for d in range(8)]
    assert sg.asin_features(h, 'A1')['streak_days'] == 2


def test_new_mover_inputs():
    # first seen in the last two scans only
    h = [full(d, [item('A1', 300, 3000)] if d >= 5 else []) for d in range(7)]
    f = sg.asin_features(h, 'A1')
    assert f['recent_hits'] == 2 and f['seen_before_window'] is False and f['appearances'] == 2


# ---- all_features and rank_history ----

def test_all_features_matches_single_calls():
    rnd = random.Random(7)
    h = []
    for d in range(10):
        for slug in ('kitchen', 'garden'):
            its = [item('%s%02d' % (slug[0].upper(), i), rnd.randint(100, 5000), rnd.randint(100, 9000),
                        price=rnd.choice([10, 20, 30]), deal=rnd.random() < 0.1, pos=i + 1)
                   for i in range(30) if rnd.random() < 0.7]
            h.append(scan(d, its, n=50 if d != 4 else 20, slug=slug, status='ok' if d != 6 else 'blocked'))
    every = sg.all_features(h, days=7)
    assert len(every) > 30
    for a, f in every.items():
        assert f == sg.asin_features(h, a, days=7)
        assert 0.0 <= f['surge'] <= 100.0
    json_safe(every)


def test_rank_history_sorted_and_deduped():
    h = [full(d, [item('A1', 1000 - d, 1100)]) for d in (3, 1, 2)]
    h.append(full(2, [item('A1', 998, 1100)], kind='new'))           # same ts, other list, same category
    rh = sg.rank_history(h, 'A1')
    assert rh == [(T0 + DAY, 999), (T0 + 2 * DAY, 998), (T0 + 3 * DAY, 997)]
    assert all(isinstance(t, int) and isinstance(r, int) for t, r in rh)
    json_safe(rh)


def test_rank_history_includes_deal_days():
    h = [full(d, [item('A1', 500 - d, 600, deal=(d == 1))]) for d in range(3)]
    assert [r for _, r in sg.rank_history(h, 'A1')] == [500, 499, 498]


# ---- clusters ----

def titles(*pairs):
    return [{'asin': a, 'title': t} for a, t in pairs]


def test_clusters_group_similar_titles():
    items = titles(('A1', 'Orthopedic Dog Bed for Large Dogs, Washable Cover'),
                   ('A2', 'Orthopedic Dog Bed - Memory Foam'),
                   ('A3', 'Orthopedic Dog Bed Dogs | Waterproof'),
                   ('B1', 'Silicone Baking Mat Set'),
                   ('B2', '[2 Pack] Silicone Baking Mat'),
                   ('C1', 'Stainless Steel Garlic Press'))
    cl = sg.clusters(items)
    by = {a: c for c in cl for a in c['asins']}
    assert by['A1'] is by['A2'] is by['A3']
    assert by['B1'] is by['B2']
    assert by['C1'] is not by['A1'] and by['C1'] is not by['B1']
    assert [len(c['asins']) for c in cl] == [3, 2, 1]
    assert cl[0]['key'] == 'bed dog dogs orthopedic'          # words in at least half the members
    assert cl[0]['tokens'] == ['bed', 'dog', 'dogs', 'orthopedic']
    assert cl[1]['key'] == 'baking mat silicone'
    assert sorted(a for c in cl for a in c['asins']) == ['A1', 'A2', 'A3', 'B1', 'B2', 'C1']
    json_safe(cl)


def test_clusters_jaccard_threshold_edge():
    # {a b c d} vs {a b c e}: 3 shared of 5 -> exactly 0.6, merges at 0.6 but not at 0.61
    items = titles(('X1', 'Alpha Bravo Charlie Delta'), ('X2', 'Alpha Bravo Charlie Echo'))
    assert len(sg.clusters(items, threshold=0.6)) == 1
    assert len(sg.clusters(items, threshold=0.61)) == 2
    assert len(sg.clusters(items, threshold=1.0)) == 2
    # {a b} vs {a c}: 1/3 -> separate at the default
    assert len(sg.clusters(titles(('Y1', 'Alpha Bravo'), ('Y2', 'Alpha Charlie')))) == 2


def test_clusters_are_transitive():
    # A~B and B~C (0.6 each) but A and C share only 2 of 6 words: union-find still joins all three
    items = titles(('A', 'Alpha Bravo Charlie Delta'), ('B', 'Alpha Bravo Charlie Echo'),
                   ('C', 'Alpha Bravo Echo Foxtrot'))
    cl = sg.clusters(items)
    assert len(cl) == 1 and cl[0]['asins'] == ['A', 'B', 'C']
    assert cl[0]['key'] == 'alpha bravo charlie echo'


def test_clusters_bad_items():
    items = [None, 'x', {'asin': None, 'title': 'Dog Bed'}, {'asin': 'E1', 'title': ''},
             {'asin': 'E2'}, {'asin': 'E3', 'title': None}, {'asin': 'D1', 'title': 'Dog Bed'},
             {'asin': 'd1', 'title': 'Dog Bed Large'}]
    cl = sg.clusters(items)
    assert sorted(a for c in cl for a in c['asins']) == ['D1', 'E1', 'E2', 'E3']
    empties = [c for c in cl if c['asins'][0].startswith('E')]
    assert all(len(c['asins']) == 1 and c['tokens'] == [] and c['key'] == c['asins'][0] for c in empties)
    assert sg.clusters(None) == [] and sg.clusters([]) == [] and sg.clusters('abc') == []
    json_safe(cl)


def test_clusters_bad_threshold_uses_default():
    items = titles(('X1', 'Alpha Bravo Charlie Delta'), ('X2', 'Alpha Bravo Charlie Echo'))
    assert len(sg.clusters(items, threshold=None)) == 1
    assert len(sg.clusters(items, threshold=float('nan'))) == 1


def _naive_clusters(items, t):
    toks = {}
    for it in items:
        toks.setdefault(it['asin'], set()).update(sg._tokens(it['title']))
    keys = list(toks)
    parent = {k: k for k in keys}

    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            A, B = toks[a], toks[b]
            if A and B and len(A & B) / len(A | B) >= t:
                parent[find(a)] = find(b)
    groups = {}
    for k in keys:
        groups.setdefault(find(k), set()).add(k)
    return sorted(sorted(g) for g in groups.values())


@pytest.mark.parametrize('t', [0.3, 0.5, 0.6, 0.75, 1.0])
def test_clusters_match_brute_force(t):
    rnd = random.Random(int(t * 100))
    words = ['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo', 'Foxtrot', 'Golf', 'Hotel', 'India', 'Juliet']
    items = [{'asin': 'Q%03d' % i, 'title': ' '.join(rnd.sample(words, rnd.randint(1, 5)))} for i in range(150)]
    got = sorted(sorted(c['asins']) for c in sg.clusters(items, threshold=t))
    assert got == _naive_clusters(items, t)


# ---- cluster surge ----

def test_cluster_surge_noisy_or():
    assert sg.cluster_surge([50, 50]) == 75.0
    assert sg.cluster_surge([60]) == 60.0
    assert sg.cluster_surge([30, 30, 30]) == pytest.approx(100 * (1 - 0.7 ** 3), abs=0.05)
    assert sg.cluster_surge([100, 10]) == 100.0
    assert sg.cluster_surge([]) == 0.0
    assert sg.cluster_surge(None) == 0.0
    assert sg.cluster_surge([None, float('nan'), 'x', 50]) == 50.0
    assert sg.cluster_surge([150, -20]) == 100.0
    assert sg.cluster_surge([-20]) == 0.0
    assert sg.cluster_surge(np.array([20.0, 20.0])) == 36.0
    assert sg.cluster_surge(s for s in (50, 50)) == 75.0


def test_cluster_surge_grows_with_members():
    vals = [sg.cluster_surge([25] * k) for k in range(1, 6)]
    assert all(a < b for a, b in zip(vals, vals[1:])) and vals[-1] < 100


# ---- JSON safety and speed ----

def test_outputs_are_json_safe_with_nan_inputs():
    h = [full(d, [item('A1', float('nan'), float('inf'), price=float('nan'))]) for d in range(5)]
    h.append({'ts': T0 + 6 * DAY, 'status': 'ok', 'n': float('nan'), 'n_median': float('nan'),
              'items': [{'asin': 'A1', 'rank_now': 300, 'rank_before': None, 'price': None, 'deal': None}]})
    f = sg.asin_features(h, 'A1')
    json_safe(f)
    json_safe(sg.all_features(h))
    json_safe(sg.rank_history(h, 'A1'))
    assert f['first_seen_ts'] == T0 and isinstance(f['first_seen_ts'], int)


def test_speed_two_weeks_of_lists():
    rnd = random.Random(3)
    pool = ['P%04d' % i for i in range(1500)]
    h = []
    for d in range(14):
        for k in range(20):
            its = [item(a, rnd.randint(50, 50000), rnd.randint(50, 80000), title='Thing %s widget' % a, pos=i + 1)
                   for i, a in enumerate(rnd.sample(pool, 50))]
            h.append(scan(d, its, slug='cat%d' % k, hour=k % 24))
    t = time.perf_counter()
    feats = sg.all_features(h)
    cl = sg.clusters([{'asin': a, 'title': f['title']} for a, f in feats.items()])
    assert time.perf_counter() - t < 3.0
    assert len(feats) == 1500 and len(cl) >= 1
