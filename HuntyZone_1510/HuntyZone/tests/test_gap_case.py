"""B01EIG6A4Q on Amazon Australia (Sydney 2000), recorded live on 14 October 2026 (tests/fixtures/gap_B01EIG6A4Q).

Expected before recording: a lone exporter. What Amazon showed: six offers on the listing, the buy box with
MangaNaturals AUS from stock in Amazon's Australian warehouse (delivery today), and page 1 of "stain remover clothes"
full of local listings. The app must say what the page says: Crowded, not a gap, with the chemical warning."""
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import amazon_check as ac  # noqa: E402
import product  # noqa: E402
from engine import gaps, offers  # noqa: E402

REC = json.loads((Path(__file__).parent / 'fixtures' / 'gap_B01EIG6A4Q' / 'capture.json').read_text('utf-8'))
TODAY = dt.date.fromisoformat(REC['today'])


def parsed():
    p = product.parse_product(REC['product']['raw'], 'AU', TODAY)
    s = product.parse_seller(REC['seller']['raw'])
    rows = [ac.classify(ac.search_row(r, 'AU', TODAY)) for r in REC['search']['rows']]
    return p, s, rows


def test_the_recording_was_made_in_sydney():
    assert 'sydney 2000' in REC['product']['location'].lower() and 'sydney 2000' in REC['search']['location'].lower()


def test_the_product_page_as_amazon_showed_it():
    p, s, _ = parsed()
    assert p['title'].startswith("Miss Mouth's HATE STAINS CO Stain Remover") and p['brand'] == "Miss Mouth's"
    assert p['offers'] == 6 and p['available'] is True and p['fulfillment'] == 'fba' and p['origin'] == 'local'
    assert p['seller_id'] == 'AJCNOSVUDJN51' and p['delivery_days'] == 0
    assert s['name'] == 'MangaNaturals AUS' and s['country'] is None           # no business address on its page
    o = offers.from_page(p, 'AU', s)
    assert o['stock'] == 'local' and o['fba'] and not o['amazon'] and o['seller'] == 'MangaNaturals AUS'


def test_the_phrase_shoppers_type_has_no_brand():
    p, _, _ = parsed()
    phrase, how = gaps.generic_phrase(p['title'], p['brand'], [k for v in REC['suggestions'].values() for k in v])
    assert (phrase, how) == ('stain remover clothes', 'suggestion') == (REC['search']['phrase'], 'suggestion')


def test_it_is_crowded_in_australia_with_a_chemical_warning():
    p, s, rows = parsed()
    listing = {'status': 'ok', 'available': p['available'], 'n_offers': p['offers'], 'offers': [offers.from_page(p, 'AU', s)]}
    g = gaps.classify('AU', listing, {'status': 'ok', 'phrase': REC['search']['phrase'], 'rows': rows}, ['B01EIG6A4Q'])
    assert g['type'] == 'crowded' and not g['gap'] and g['same_local'] >= 5 and g['local_rivals'] >= 20
    flags = gaps.flags(p['title'], p['bullets'], p['item_form'], p['safety'])
    assert flags[0]['label'] == 'chemical / dangerous goods'
    assert 'stain remover' in flags[0]['why'] and 'item form: spray' in flags[0]['why']
