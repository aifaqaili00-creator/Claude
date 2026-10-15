"""engine/discovery.py: whole-phrase Google Trends matching, the five signals, unknowns kept unknown, risks, and the
ranking it gives (a steady product with rising searches above a bigger fad spike)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import discovery as d  # noqa: E402


def feat(score, label='Growing', q=0.8, status='ok', **extra):
    return {'status': status, 'label': label, 'score': score, 'q': q, **extra}


def part(o, key):
    return next(p for p in o['parts'] if p['key'] == key)


STEADY = {'market': 'US', 'surge': 55, 'surge_14d': 52, 'valid_scans': 12, 'appearances': 11, 'streak_days': 6,
          'reviews': 40, 'price': 45.0, 'category': 'Home & Kitchen'}
SPIKE = {'market': 'US', 'surge': 80, 'surge_14d': 45, 'valid_scans': 12, 'appearances': 3, 'reviews': 25000,
         'price': 9.99, 'category': 'Toys'}


# ---- search words -> stored Google Trends terms ----

def test_phrase_variants_are_whole_phrases_longest_first():
    assert d.phrase_variants('Bladeless Tower Fan') == ['bladeless tower fan', 'bladeless tower', 'tower fan']
    assert d.phrase_variants('Hammock') == ['hammock']                          # one word only when it is the phrase
    assert d.phrase_variants('') == []
    assert len(d.phrase_variants('a b c d e f g')[0].split()) == 5            # at most five words
    assert d.suggest_term('Bladeless Tower Fan Quiet Remote') == 'bladeless tower fan'


def test_match_prefers_the_longest_readable_phrase_and_folds_plurals():
    idx = d.trend_index({'tower fan': feat(70), 'bladeless tower fan': feat(None, status='low_volume'), 'fan': feat(90)})
    m = d.match_trend('Bladeless Tower Fans Quiet', idx)
    assert m['term'] == 'tower fan' and m['match'] == 'part'                  # the longer one has too few searches
    assert d.match_trend('Tower Fans', idx)['match'] == 'exact'
    low = d.match_trend('Bladeless Tower Fan Quiet', d.trend_index({'bladeless tower fan': feat(None, status='low_volume')}))
    assert low['term'] == 'bladeless tower fan' and low['feature']['status'] == 'low_volume'


@pytest.mark.parametrize('search', ['Fan Tower Stand', 'Desk Fan', 'Towers Fanatic', 'Tower', ''])
def test_no_loose_matches(search):
    assert d.match_trend(search, d.trend_index({'tower fan': feat(70)})) is None


def test_the_newest_readable_term_wins_a_shared_key():
    idx = d.trend_index({'dog bed': feat(40, computed_at=1), 'dog beds': feat(60, computed_at=2),
                         'dogs bed': feat(None, status='none', computed_at=3)})
    assert d.match_trend('Dog Bed', idx)['term'] == 'dog beds'


# ---- the phrase shoppers type ----

def test_prefixes_are_two_word_starts():
    assert d.phrase_prefixes('Bladeless Tower Fan Quiet Remote') == ['bladeless tower', 'tower fan', 'fan quiet']
    assert d.phrase_prefixes('Tower Fan') == ['tower fan'] and d.phrase_prefixes('Hammock') == ['hammock']
    assert d.phrase_prefixes('') == []


def test_pick_phrase_takes_a_suggestion_whose_words_are_all_in_the_title():
    title = 'Bladeless Tower Fans for Bedroom, Quiet Oscillating Fan with Remote'
    sugg = ['tower fan', 'tower fan with remote control', 'bladeless tower fan', 'tower', 'tower fan cooling']
    assert d.pick_phrase(title, sugg) == 'bladeless tower fan'                # 3 words beat 2; extra words disqualify
    assert d.pick_phrase(title, ['tower fan', 'quiet fan']) == 'tower fan'    # the better ranked of equals
    assert d.pick_phrase(title, ['tower', 'ceiling fan']) is None and d.pick_phrase(title, []) is None
    assert d.autocomplete_knows('tower fan', ['tower fans for bedroom']) is True
    assert d.autocomplete_knows('tower fan', ['fan tower', 'tower']) is False and not d.autocomplete_knows('', ['x'])


def test_the_shopper_phrase_is_matched_first():
    idx = d.trend_index({'tower fan': feat(70), 'bladeless fan': feat(40)})
    m = d.match_trend('Bladeless Fan Tower Quiet', idx, 'tower fan')
    assert m['term'] == 'tower fan' and m['match'] == 'shopper'
    assert d.match_trend('Bladeless Fan Tower Quiet', idx, 'standing fan')['term'] == 'bladeless fan'


def test_variants_are_longer_suggestions_with_all_the_words():
    sugg = ['tower fan', 'tower fans for bedroom', 'tower fan with remote', 'small tower fan', 'tower fan with remote',
            'fan tower', 'tower', 'tower fan for bedroom quiet cooling large room']
    assert d.pick_variants('tower fan', sugg) == ['tower fans for bedroom', 'tower fan with remote', 'small tower fan']
    assert d.pick_variants('Tower Fans', sugg, n=1) == ['tower fans for bedroom'] and d.pick_variants('', sugg) == []


# ---- groups of similar listings ----

def test_a_group_lifts_a_weak_member_but_one_listing_does_not():
    weak = {'surge': 20, 'surge_14d': 20, 'valid_scans': 12, 'appearances': 2, 'persistence': 0.1}
    alone = d.amazon_part(weak)
    pair = d.amazon_part({**weak, 'similar': 1, 'cluster_surge': 90})            # one other listing: no group surge
    group = d.amazon_part({**weak, 'similar': 4, 'cluster_surge': 90, 'group_on': 12, 'group_read': 14})
    assert pair['v'] == alone['v'] < group['v']
    assert 'with 4 similar listings on the lists 12 of 14 days (together 90/100)' in group['why']
    assert group['raw']['similar'] == 4 and group['raw']['group'] == [12, 14]
    lower = d.amazon_part({**weak, 'similar': 4, 'cluster_surge': 10, 'group_on': 0, 'group_read': 14})
    assert lower['v'] == alone['v']                                         # a group never pulls a listing down


# ---- the score ----

def test_a_steady_product_with_rising_searches_beats_a_bigger_fad_spike():
    idx = d.trend_index({'tower fan': feat(78), 'fidget spinner': feat(18, label='Fad')})
    steady = d.score({**STEADY, 'trend': d.match_trend('Bladeless Tower Fan', idx)})
    spike = d.score({**SPIKE, 'trend': d.match_trend('Fidget Spinner Metal', idx)})
    assert SPIKE['surge'] > STEADY['surge'] and steady['score'] > spike['score'] + 30
    assert steady['label'] in ('Strong', 'Worth a look') and spike['label'] == 'Skip'
    assert spike['risk']['flags'] == ['search fad'] and spike['risk']['factor'] == 0.85


def test_unknown_signals_stay_unknown_and_lower_the_confidence():
    o = d.score({'market': 'AU', 'surge': 90, 'surge_14d': 85, 'valid_scans': 10, 'appearances': 10})
    assert part(o, 'A')['known'] and part(o, 'A')['v'] == pytest.approx(0.88) and part(o, 'A')['conf'] == 'high'
    for k in 'SDCE':
        p = part(o, k)
        assert p['known'] is False and p['v'] is None and p['conf'] == 'low' and p['src'] is None
    assert o['known'] == 1 and o['conf'] == 'low' and 'demand unknown' in o['why']
    idx = d.trend_index({'tower fan': feat(78, q=0.9)})
    full = d.score({**STEADY, 'trend': d.match_trend('Tower Fan', idx), 'units': {'v': 900, 'conf': 'high', 'src': 'badge'}})
    assert full['known'] == 5 and full['conf'] == 'medium'                     # price & fees assume the product cost


def test_two_signals_alone_never_read_strong():
    o = d.score({'market': 'US', 'surge': 100, 'surge_14d': 100, 'valid_scans': 9, 'appearances': 9,
                 'trend': d.match_trend('Tower Fan', d.trend_index({'tower fan': feat(100)}))})
    assert o['known'] == 2 and o['score'] >= 70 and o['label'] == 'Worth a look'
    assert "'Strong' needs at least 3 measured signals" in o['why']
    o = d.score({**STEADY, 'surge': 100, 'surge_14d': 100, 'reviews': 0,
                 'trend': d.match_trend('Tower Fan', d.trend_index({'tower fan': feat(100)}))})
    assert o['known'] == 4 and o['label'] == 'Strong'


def test_staying_on_the_lists_beats_a_one_day_jump():
    jump = d.amazon_part({'surge': 36, 'surge_14d': 33, 'persistence': 1 / 7, 'persistence_14d': 1 / 14,
                          'valid_scans': 14, 'appearances': 1})
    steady = d.amazon_part({'surge': 30, 'surge_14d': 28, 'persistence': 0.7, 'persistence_14d': 0.8,
                            'valid_scans': 14, 'appearances': 11})
    new = d.amazon_part({'surge': 70, 'surge_14d': 50, 'persistence': 1.0, 'persistence_14d': 0.5,
                         'valid_scans': 14, 'appearances': 7, 'streak_days': 7})
    assert steady['v'] > jump['v'] + 0.25 and steady['raw']['stay'] == 0.8
    assert new['raw']['stay'] == 1.0 and new['v'] == pytest.approx(0.5 * 0.62 + 0.5)          # this week counts in full
    assert 'on 1 of 14 list scans' in jump['why'] and '7 days running' in new['why']


def test_each_risk_takes_15_percent_and_notes_take_nothing():
    plain = d.score(STEADY)
    deal = d.score({**STEADY, 'deal_driven': True})
    both = d.score({**STEADY, 'deal_driven': True, 'flags': ['electrical', 'price unknown', 'cheap']})
    assert deal['score'] == pytest.approx(plain['score'] * 0.85, abs=0.15)
    assert both['risk']['flags'] == ['electrical', 'deal-driven'] and both['score'] == pytest.approx(plain['score'] * 0.7, abs=0.15)
    seasonal = d.trend_index({'tower fan': feat(70, label='Seasonal', seasonality={'reliable': True, 'peak_month': 12})})
    o = d.score({**STEADY, 'trend': d.match_trend('Tower Fan', seasonal)})
    assert o['risk']['n'] == 0 and o['risk']['notes'] == ['seasonal: peaks in Dec']
    dec = d.score({**STEADY, 'trend': d.match_trend('Tower Fan', d.trend_index({'tower fan': feat(30, label='Declining')}))})
    assert dec['risk']['n'] == 0 and dec['risk']['notes'] == ['searches declining']
    grouped = d.score({**STEADY, 'similar': 2})
    assert grouped['risk']['notes'] == [] and 'with 2 similar listings' in part(grouped, 'A')['why']


def test_too_few_searches_is_unknown_not_bad():
    o = d.score({**STEADY, 'trend': d.match_trend('Tower Fan', d.trend_index({'tower fan': feat(None, status='low_volume')}))})
    s = part(o, 'S')
    assert s['known'] is False and s['raw']['status'] == 'low_volume' and 'too few' in s['why']
    assert o['score'] == d.score(STEADY)['score']


def test_search_confidence_follows_quality_and_match():
    idx = d.trend_index({'tower fan': feat(70, q=0.9), 'dog bed': feat(70, q=0.2),
                         'cat tree': feat(70, label='Insufficient', q=0.9)})
    conf = lambda s: d.search_part(d.match_trend(s, idx))['conf']          # noqa: E731
    assert conf('Tower Fan') == 'high' and conf('Bladeless Tower Fan') == 'medium'
    assert conf('Dog Bed') == 'low' and conf('Cat Tree') == 'low'


# ---- the signals one by one ----

def test_demand_and_competition_scales():
    dm = lambda v: d.demand_part({'v': v, 'conf': 'high', 'src': 'badge'}, 'US')['v']    # noqa: E731
    assert dm(100) == 0.0 and dm(1200) == 1.0 and 0 < dm(400) < 1             # US target 300: 0 at 100, 1 at 1,200
    assert 'badge' in d.demand_part({'v': 400, 'src': 'badge'}, 'US')['why']
    assert d.demand_part(None, 'US')['known'] is False and d.demand_part({'v': None}, 'US')['known'] is False
    cp = lambda r: d.competition_part(r, 'AU')['v']                        # noqa: E731
    assert cp(0) == 1.0 and cp(2300) == 0.0 and cp(50) > cp(500)                  # AU target 230 reviews
    assert d.competition_part(None, 'AU')['known'] is False


def test_price_and_fees():
    cheap = d.economics_part({'price': 8.0, 'category': 'Kitchen'}, 'US')
    mid = d.economics_part({'price': 35.0, 'category': 'Kitchen'}, 'US')
    assert cheap['known'] and mid['known'] and cheap['v'] < mid['v'] and mid['conf'] == 'medium'
    assert 'size unknown' in mid['why'] and mid['raw']['sized'] is False
    sized = d.economics_part({'price': 35.0, 'dims_cm': [20, 15, 5], 'kg': 0.4}, 'US')
    assert sized['raw']['sized'] and 'size unknown' not in sized['why']
    assert d.economics_part({'price': 35.0, 'fba_fee': 4.1}, 'US')['src'] == 'h10'
    assert d.economics_part({'price': None}, 'US')['known'] is False


def test_your_supplier_price_replaces_the_assumed_share():
    base = {'price': 35.0, 'category': 'Kitchen'}
    assumed = d.economics_part(base, 'US')
    mine = d.economics_part({**base, 'cogs_usd': 4.0}, 'US')
    dear = d.economics_part({**base, 'cogs_usd': 12.0}, 'US')
    assert mine['raw']['cost_usd'] == 4.0 and 'your cost US$4.00' in mine['why'] and mine['conf'] == 'medium'
    assert dear['v'] < mine['v'] and dear['raw']['margin'] < mine['raw']['margin']
    sized = d.economics_part({**base, 'cogs_usd': 4.0, 'dims_cm': [20, 15, 5], 'kg': 0.4}, 'US')
    assert sized['conf'] == 'high' and assumed['conf'] == 'medium' and 'cost_usd' not in assumed['raw']


def test_highest_supplier_price_for_a_margin():
    c = {'price': 35.0, 'category': 'Kitchen', 'dims_cm': [20, 15, 5], 'kg': 0.4}
    m20, m30 = d.max_cost(c, 'US', 0.20), d.max_cost(c, 'US', 0.30)
    assert m20 > m30 > 0 and d.max_cost({**c, 'price': 50.0}, 'US', 0.20) > m20
    assert d.economics_part({**c, 'cogs_usd': m20}, 'US')['raw']['margin'] == pytest.approx(0.20, abs=0.01)
    assert d.max_cost({**c, 'price': 6.0}, 'US', 0.20) is None and d.max_cost({'price': None}, 'US', 0.2) is None


def test_one_reading_without_history_is_low_confidence():
    a = d.amazon_part({'pct': 1000})
    assert a['known'] and a['v'] == 1.0 and a['conf'] == 'low' and a['src'] == 'one reading'
    assert d.amazon_part({'new_rank': 1})['v'] == 1.0 and d.amazon_part({'new_rank': 51})['v'] == pytest.approx(0.5)
    assert d.amazon_part({})['known'] is False
    hist = d.amazon_part({'surge': 40, 'surge_14d': 40, 'valid_scans': 1, 'appearances': 1, 'pct': 1000})
    assert hist['v'] == pytest.approx(0.4) and hist['conf'] == 'low' and hist['src'] == 'lists'


@pytest.mark.parametrize('c', [None, {}, 'junk', {'surge': float('nan'), 'surge_14d': float('inf'), 'reviews': -3,
                                                  'price': float('nan'), 'units': {'v': float('nan')}, 'flags': 'x',
                                                  'trend': {'term': 'x', 'feature': {'score': float('nan')}}}])
def test_bad_input_never_raises_and_is_json_safe(c):
    o = d.score(c)
    json.dumps(o, allow_nan=False)
    assert o['of'] == 5 and len(o['parts']) == 5 and o['risk']['n'] == 0
