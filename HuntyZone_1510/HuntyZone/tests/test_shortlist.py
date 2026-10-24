"""Shortlist (shortlist.py): a product found goes on the board with its numbers: the listing read, the most to pay a supplier
and keep 20%, the profit at a saved supplier price; a second click adds the numbers to the card already there."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import amazon_check as ac  # noqa: E402
import board  # noqa: E402
import product  # noqa: E402
import shortlist  # noqa: E402
import storage  # noqa: E402
from test_gapfinder import FakeS  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = storage.DB(tmp_path / 'm.db').open()
    S = FakeS(db, [], [])
    S.synced = 0
    S.sync_tasks = lambda: setattr(S, 'synced', S.synced + 1)
    pages = {'B0ROPE1234': {'status': 'ok', 'title': 'Kinetic Towing Rope 20 ft', 'price': 129.0, 'available': True,
                        'pkg_cm': [30, 25, 12], 'pkg_kg': 2.4, 'rating': 4.4, 'reviews': 37, 'root_rank': 1800,
                        'root_name': 'Automotive', 'fulfillment': 'fba', 'seller_id': 'S1', 'buybox_seller': 'Rope Co',
                        'offers': 3, 'brand': 'KING-PRO'}}

    async def read_product(checker, asin, market, reviews=False):
        got = pages.get(asin)
        if isinstance(got, Exception):
            raise got
        p = product.parse_product({}, market)
        p.update(got or {'status': 'missing'})
        return p
    monkeypatch.setattr(product, 'read_product', read_product)
    yield S, pages
    db.close()


def test_a_product_goes_on_the_board_with_its_numbers(env):
    S, _ = env
    item = {'market': 'AE', 'asin': 'B0ROPE1234', 'title': 'Kinetic Towing Rope 20 ft', 'kw': 'towing rope for jet ski',
            'source': 'Market Gaps (Towing Ropes)'}
    out = asyncio.run(shortlist.run(S, item))
    assert not out['exists'] and out['summary'].startswith('Shortlisted as PB-%d. Shortlisted from Market Gaps (Towing Ropes): ' % out['card'])
    s = out['summary']
    assert 'AED 129.00 on Amazon UAE' in s and '2.40 kg' in s and '30 × 25 × 12 cm' in s and '3 sellers' in s and '4.4★ (37 reviews)' in s
    assert out['pay_usd'] and 'pay your supplier at most US$%.2f' % out['pay_usd'] in s and 'for this size' in s
    card = S.db.one('SELECT * FROM board_card WHERE id=?', (out['card'],))
    assert card['asin'] == 'B0ROPE1234' and card['market'] == 'AE' and card['kw'] == 'towing rope for jet ski'
    notes = S.db.all("SELECT text FROM board_event WHERE card_id=? AND kind='note'", (out['card'],))
    assert len(notes) == 1 and notes[0]['text'].startswith('Shortlisted from Market Gaps')
    assert S.synced == 1 and S.db.one("SELECT 1 AS ok FROM watch WHERE target='towing rope for jet ski' AND market='AE'")
    S.db.write(storage.set_pref, 'AE', 'B0ROPE1234', cogs_usd=6.0)                 # your supplier price, saved with Profit?
    again = asyncio.run(shortlist.run(S, item))
    assert again['exists'] and again['card'] == out['card'] and again['summary'].startswith('Already on your board as PB-')
    assert 'With your supplier price US$6.00: about AED' in again['summary'] and again['profit'] is not None
    assert len(S.db.all("SELECT 1 FROM board_event WHERE card_id=? AND kind='note'", (out['card'],))) == 2


def test_a_listing_that_cannot_be_read_still_goes_on_the_board(env):
    S, pages = env
    pages['B0GONE1234'] = ac.CaptchaRequired('captcha')
    out = asyncio.run(shortlist.run(S, {'market': 'AU', 'asin': 'B0GONE1234', 'title': 'Foot Rest Cushion'}))
    assert 'the listing could not be read' in out['summary'] and out['pay_usd'] is None
    assert S.db.one('SELECT title FROM board_card WHERE id=?', (out['card'],))['title'] == 'Foot Rest Cushion'


def test_the_note_without_room_for_a_margin():
    facts = {'price': 3.0, 'dims_cm': None, 'kg': None}
    nums = shortlist.numbers(facts, 'AU', {})
    assert nums['pay_usd'] in (None, 0.0)
    text = shortlist.note_text(facts, nums, 'AU', '')
    assert text.startswith('Shortlisted: A$3.00 on Amazon Australia.') and 'nothing is left' in text or 'at most US$0.00' in text
    assert board.add_card                                                       # the board is what it writes to
