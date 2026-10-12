"""engine/complaints.py: critical reviews grouped into complaint themes, and the brief for suppliers."""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import reports  # noqa: E402
import storage  # noqa: E402
from engine import complaints as C  # noqa: E402

R = [
    {'asin': 'A1', 'stars': 1, 'title': 'Broke in a week', 'body': 'The handle cracked after two uses. Very disappointed.', 'helpful': 12},
    {'asin': 'A2', 'stars': 2, 'title': 'Cheap', 'body': 'Feels cheap and flimsy. The handle broke too!', 'helpful': 0},
    {'asin': 'A1', 'stars': 3, 'title': 'Too small', 'body': 'Much smaller than the pictures suggest.', 'helpful': 3},
    {'asin': 'A3', 'stars': 1, 'title': 'Terrible', 'body': 'Arrived damaged and the box was torn.', 'helpful': 1},
    {'asin': 'A3', 'stars': 2, 'title': 'Meh', 'body': 'Não gostei do produto.', 'helpful': 0},
]


def test_reviews_group_into_themes_counted_once_per_review():
    out = C.themes(R)
    json.dumps(out, allow_nan=False)
    by = {t['key']: t for t in out['themes']}
    assert out['n'] == 5 and out['other'] == 1                                # the Portuguese review fits no theme
    assert out['themes'][0]['key'] == 'broke' and by['broke']['n'] == 2 and by['broke']['listings'] == 2
    assert by['broke']['share'] == 0.4 and by['broke']['quotes'][0] == 'The handle cracked after two uses.'
    assert by['quality']['n'] == by['small']['n'] == by['damaged']['n'] == 1
    assert [t['key'] for t in out['themes']] == ['broke', 'damaged', 'small', 'quality']   # 1-2 stars and helpful first


def test_the_brief_lists_each_complaint_with_what_to_ask():
    lines = C.brief(C.themes(R), 'tool holder', 3).splitlines()
    assert lines[0] == 'Supplier brief for "tool holder": what buyers of the 3 best-selling listings complain about (5 critical reviews)'
    assert lines[2].startswith('1. Broke or cracked: 2 reviews. Ask for thicker')
    assert lines[3] == '   "The handle cracked after two uses."'
    assert C.brief(C.themes([]), 'x') == '' and C.themes(None) == {'themes': [], 'n': 0, 'other': 0}


@pytest.mark.parametrize('bad', [None, [], ['x', None, {}], [{'stars': 'x', 'helpful': float('nan'), 'body': None}]])
def test_bad_input_never_raises(bad):
    json.dumps(C.themes(bad), allow_nan=False)


def test_reviews_come_with_their_themes_and_a_brief(tmp_path):
    db = storage.DB(tmp_path / 'm.db').open()
    try:
        items = [{'id': 'r1', 'stars': 1, 'title': 'Broke', 'body': 'The lid cracked on day two.', 'helpful': 5},
                 {'id': 'r2', 'stars': 2, 'title': 'Cheap', 'body': 'Flimsy plastic, it broke.', 'helpful': 0}]
        db.write(storage.save_reviews, 'US', 'B0ABCDEFGH', items, 'critical')
        db.write(storage.upsert_asins, 'US', [{'asin': 'B0ABCDEFGH', 'title': 'Glass Food Container Set'}])
        r = reports.reviews_for(db, 'US', asin='B0ABCDEFGH', now=time.time())
        assert r['themes'][0]['key'] == 'broke' and r['themes'][0]['n'] == 2 and r['n_reviews'] == 2
        assert r['brief'].split('\n')[0] == 'Supplier brief for "Glass Food Container Set": what buyers complain about (2 critical reviews)'
    finally:
        db.close()
