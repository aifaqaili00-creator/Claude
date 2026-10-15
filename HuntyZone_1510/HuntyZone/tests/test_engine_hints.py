"""Launch & strategy hints from research, and when their numbers moved enough to check them again."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import hints  # noqa: E402

RESEARCH = {
    'market': 'AU', 'market_name': 'Australia', 'kw': 'calming dog bed', 'searched': True, 'cur': 'A$',
    'price': 36.0, 'p25': 29.0, 'p75': 45.0,
    'econ': {'Profit': 9.1, 'Margin': 0.281, 'BreakEvenACoS': 0.31},
    'review_barrier': 850, 'fast_local': 3, 'takeaway': 'Mostly Chinese sellers using FBA: 7 of the top 10.',
    'themes': [{'key': 'broke', 'name': 'Broke or cracked', 'n': 9, 'share': 0.35, 'ask': 'Ask for thicker material'},
               {'key': 'small', 'name': 'Too small', 'n': 5, 'share': 0.2, 'ask': 'Offer a larger size'}],
    'phrases': ['calming dog bed', 'calming dog bed large'],
    'plan': {'status': 'ok', 'peak_month': 11, 'sea': {'in_stock_by': '2026-10-01', 'order_by': '2026-07-12'},
             'air': {'order_by': '2026-08-20'}},
    'newcomer': {'v': 265.7, 'lo': 191.7, 'hi': 382.8},
    'trend': {'label': 'Growing, seasonal peak Nov'},
}


def by_key(hs):
    return {h['key']: h for h in hs}


def test_every_hint_comes_from_the_research():
    hs = by_key(hints.make(RESEARCH))
    json.dumps(list(hs.values()), allow_nan=False)
    assert list(hs) == ['price', 'profit', 'reviews', 'competition', 'complaints', 'phrases', 'timing', 'target', 'trend']
    assert hs['price']['text'] == 'Price it between A$29 and A$45: the middle of page 1 is A$36.'
    assert 'A$9.10 a sale (28%)' in hs['profit']['text'] and 'up to 31%' in hs['profit']['text']
    assert hs['reviews']['text'].startswith('The top listings have about 850 reviews')
    assert hs['competition']['text'] == '3 sellers deliver fast from stock in Australia. Mostly Chinese sellers using FBA: 7 of the top 10.'
    assert 'broke or cracked (35%), too small (20%)' in hs['complaints']['text'] and hs['complaints']['data']['top'] == 'broke'
    assert hs['timing']['text'] == 'Demand peaks in Nov: be in stock by 1 Oct (order by 12 Jul by sea or 20 Aug by air).'
    assert hs['target']['data'] == {'v': 266} and 'about 266 a month here (192 to 383)' in hs['target']['text']
    assert hs['trend']['data'] == {'label': 'Growing'} and hs['price']['recheck_days'] == 14 and hs['phrases']['recheck_days'] == 30


def test_unknowns_are_left_out_and_losses_are_said_plainly():
    r = {'market': 'US', 'market_name': 'USA', 'searched': True, 'cur': 'US$', 'price': 20.0,
         'econ': {'Profit': -1.56, 'Margin': -0.078, 'BreakEvenACoS': 0.04}, 'review_barrier': 22,
         'plan': {'status': 'flat'}, 'trend': {'label': 'Insufficient'}}
    hs = by_key(hints.make(r))
    assert list(hs) == ['price', 'profit', 'reviews', 'timing']
    assert hs['price']['text'] == 'The middle of page 1 is US$20.'
    assert hs['profit']['text'].startswith('At US$20 each sale loses about US$1.56')
    assert 'only about 22 reviews' in hs['reviews']['text'] and hs['timing']['text'].startswith('No strong season')


def test_a_market_not_searched_yet_says_so():
    hs = hints.make({'market': 'AE', 'market_name': 'UAE', 'kw': 'dog bed', 'searched': False})
    assert [h['key'] for h in hs] == ['search'] and 'Not searched in UAE yet' in hs[0]['text']


def test_moved_needs_a_real_change():
    assert hints.moved('price', {'price': 36}, {'price': 39.6}) == {'from': 36, 'to': 39.6}
    assert hints.moved('price', {'price': 36}, {'price': 39}) is None
    assert hints.moved('reviews', {'reviews': 800}, {'reviews': 1000}) == {'from': 800, 'to': 1000}
    assert hints.moved('competition', {'fast_local': 3}, {'fast_local': 5}) == {'from': 3, 'to': 5}
    assert hints.moved('competition', {'fast_local': 3}, {'fast_local': 4}) is None
    assert hints.moved('target', {'v': 200}, {'v': 260}) == {'from': 200, 'to': 260}
    assert hints.moved('profit', {'margin': 0.28}, {'margin': 0.22}) == {'from': 0.28, 'to': 0.22}
    assert hints.moved('profit', {'margin': 0.28}, {'margin': 0.25}) is None
    assert hints.moved('complaints', {'top': 'broke'}, {'top': 'small'}) == {'from': 'broke', 'to': 'small'}
    assert hints.moved('trend', {'label': 'Growing'}, {'label': 'Declining'})
    assert hints.moved('timing', {'peak': 11}, {'peak': 11}) is None
    assert hints.moved('phrases', {'phrases': ['a']}, {'phrases': ['b']}) is None
    assert hints.moved('price', {}, {'price': 3}) is None
