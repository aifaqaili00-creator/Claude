"""Product ideas within your price range, without phones and laptops."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import file_rank as fr  # noqa: E402
import ideas  # noqa: E402


def item(asin, title, price, kind='movers', pct=200):
    return {'asin': asin, 'title': title, 'image': '', 'market': 'AU', 'url': '', 'category': 'Home', 'kind': kind,
            'slug': 'home', 'deal': False, 'rank': 1, 'pct': pct, 'rank_now': None, 'rank_before': None,
            'rating': 4.5, 'reviews': 40, 'price': price}


@pytest.mark.parametrize('title', ['iPhone 15 Case Clear', 'USB-C Charger 65W', 'USB C Cable 2m', 'Laptop Stand for Desk',
                                   'Phone Holder for Car', 'Wireless Mouse Silent', 'Tablet Stand Adjustable',
                                   'Screen Protector Galaxy S24', 'Bluetooth Earbuds', 'Monitor Stand Riser'])
def test_phones_laptops_and_their_accessories_count_as_tech(title):
    assert fr.is_tech(title)


@pytest.mark.parametrize('title', ['Mouse Trap Humane', 'Paper Notebook A5 Lined', 'Toilet Paper Holder Stand',
                                   'Bird Bath Feeder Freestanding', 'Dog Bark Deterrent Ultrasonic', 'Gaming Chair',
                                   'Cable Ties Reusable', 'Router Table for Woodworking', 'Pet Insect Repellent Spray'])
def test_everyday_products_are_not_tech(title):
    assert not fr.is_tech(title)


def test_price_range_and_tech_filter():
    items = [item('B01', 'Toilet Paper Holder Stand Black', 35.0), item('B02', 'Bird Bath Feeder Metal', 52.0),
             item('B03', 'Cheap Sink Strainer', 9.0), item('B04', 'Expensive Garden Bench', 180.0),
             item('B05', 'iPhone 15 Case Clear', 40.0), item('B06', 'Bamboo Bath Caddy Tray', None),
             item('B07', 'Laptop Stand for Desk', 45.0)]
    stats = {}
    out = ideas.score_ideas(items, 'AU', True, (30, 76), True, stats)
    asins = [i['asin'] for i in out]
    assert asins[:2] == ['B01', 'B02'] and asins[-1] == 'B06'               # unknown price last
    assert stats == {'out_of_range': 2, 'tech': 2, 'no_price': 1}
    assert 'price unknown' in out[-1]['flags']
    kept = {i['asin'] for i in ideas.score_ideas(items, 'AU', False, (30, 76), False)}
    assert {'B05', 'B07'} <= kept                                           # tech kept when the box is off


def test_without_a_range_the_old_targets_still_score():
    out = ideas.score_ideas([item('B01', 'Toilet Paper Holder Stand', 35.0), item('B02', 'Sink Strainer', 9.0)], 'AU')
    assert {i['asin'] for i in out} == {'B01', 'B02'} and 'cheap' in out[-1]['flags']
