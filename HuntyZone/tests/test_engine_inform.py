"""The product update email: links, the facts of each product, what changed between two updates, and the texts."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import inform, remind  # noqa: E402

NOW = remind.at('2026-10-12', (10, 0))


def item(pid=1, title='Calming Dog Bed', **kw):
    p = {'id': pid, 'title': title, 'image': None, 'stage': 'sourcing', 'order': 'First order',
         'units': [['US', 300], ['AU', 200]], 'sourcing': {'state': 'due', 'day': '2026-10-19'},
         'at_amazon': [['US', 'eta:2026-12-04']], 'amazon': 'https://www.amazon.com/s?k=calming+dog+bed',
         'amazon_search': True, 'alibaba': 'https://www.alibaba.com/trade/search?SearchText=calming+dog+bed',
         'alibaba_search': True}
    p.update(kw)
    return p


def test_links_go_to_the_product_or_to_a_search():
    assert inform.amazon_link('https://www.amazon.com.au', 'B0C1234567', 'x') == ('https://www.amazon.com.au/dp/B0C1234567', False)
    assert inform.amazon_link('https://www.amazon.com', None, 'dog bed & mat') == ('https://www.amazon.com/s?k=dog+bed+%26+mat', True)
    assert inform.alibaba_link(None, 'dog bed') == ('https://www.alibaba.com/trade/search?SearchText=dog+bed', True)
    assert inform.alibaba_link('https://x.1688.com/offer/1.html', 'dog bed') == ('https://x.1688.com/offer/1.html', False)
    assert inform.supplier_name('https://www.alibaba.com/product-detail/x.html') == 'Alibaba'
    assert inform.supplier_name('https://notalibaba.com/x') == 'Supplier'
    assert inform.link_labels(item()) == ('Search on Amazon', 'Search on Alibaba')
    assert inform.link_labels(item(amazon_search=False, alibaba_search=False)) == ('View on Amazon', 'Alibaba page')


def test_the_facts_of_a_product():
    rows = dict(inform.rows(item(), '2026-10-12'))
    assert rows == {'Units needed': 'USA 300, Australia 200 (500 in all)', 'Target market': 'USA, Australia',
                    'Sourcing ETA': 'Mon 19 Oct', 'At Amazon': 'USA about Fri 4 Dec',
                    'Stage': 'Sourcing & PO \u00b7 First order'}
    late = dict(inform.rows(item(units=[['US', None]], at_amazon=[], sourcing={'state': 'due', 'day': '2026-10-09'}),
                            '2026-10-12'))
    assert late['Units needed'] == 'USA not set yet' and late['Sourcing ETA'] == 'Fri 9 Oct (late)' and 'At Amazon' not in late
    done = dict(inform.rows(item(sourcing={'state': 'done', 'day': '2026-10-12'}, at_amazon=[['US', 'arrived:2026-10-20']])))
    assert done['Sourcing ETA'] == 'Done, Mon 12 Oct' and done['At Amazon'] == 'USA arrived Tue 20 Oct'
    none = dict(inform.rows(item(units=[], sourcing={'state': 'none', 'day': None})))
    assert none['Units needed'] == 'Not set yet' and none['Target market'] == 'Not set yet' and none['Sourcing ETA'] == 'Not set yet'


def test_what_changed_between_two_updates():
    old = inform.snapshot([item(), item(2, 'Lick Mat', units=[['US', 100]])])
    assert inform.changes(old, old) == [] and inform.changes(old, json.loads(json.dumps(old))) == []
    new = inform.snapshot([
        item(units=[['US', 500], ['AE', 50]], sourcing={'state': 'due', 'day': '2026-10-23'}, stage='launch',
             at_amazon=[['US', 'arrived:2026-10-20']], alibaba='https://www.alibaba.com/product-detail/1.html'),
        item(3, 'Sauna Hat')])
    ch = inform.changes(old, new)
    assert [c['kind'] for c in ch] == ['changed', 'new', 'removed']
    assert ch[0]['text'] == ('Calming Dog Bed: moved to Launch & strategy; USA units 300 \u2192 500; UAE added (50 units); '
                             'Australia taken out; Sourcing ETA Mon 19 Oct \u2192 Fri 23 Oct; arrived at Amazon in USA on '
                             'Tue 20 Oct; new Alibaba link')
    assert ch[1]['text'] == 'New: Sauna Hat' and ch[2]['text'] == 'Removed: Lick Mat' and ch[2]['id'] == '2'
    reorder = inform.snapshot([item(order='Re-order #2', units=[['US', None]]), item(2, 'Lick Mat', units=[['US', 100]])])
    assert [c['text'] for c in inform.changes(old, reorder)] == ['Calming Dog Bed: Re-order #2 started for USA']
    assert inform.fingerprint(old) == inform.fingerprint(json.loads(json.dumps(old))) != inform.fingerprint(new)


def test_the_email_lists_every_product_and_what_changed():
    items = [item(), item(2, '<b>Lick</b> Mat', image='https://m.media-amazon.com/images/I/x.jpg')]
    ch = [{'id': '2', 'kind': 'new', 'title': '<b>Lick</b> Mat', 'text': 'New: <b>Lick</b> Mat'}]
    subject, text, page = inform.texts(items, ch, {'owner': 'Ali Afa', 'to_names': ['Sara Khan', 'Ahmed Ali'],
                                                  'message': 'Quotes by Friday, please.', 'now': NOW,
                                                  'since': {'ts': NOW - 86400, 'sent': True}})
    assert subject == 'Product update, Mon 12 Oct: <b>Lick</b> Mat is new'
    assert text.startswith('Hi Sara and Ahmed,\n\nHere is where our 2 products stand.\n\nNote from Ali Afa:\n'
                           'Quotes by Friday, please.\n\nWhat changed since the last update (Sun 11 Oct):\n- New: <b>Lick</b> Mat')
    assert ('1. Calming Dog Bed\n   Units needed: USA 300, Australia 200 (500 in all)\n   Target market: USA, Australia\n'
            '   Sourcing ETA: Mon 19 Oct\n   At Amazon: USA about Fri 4 Dec') in text
    assert '2. <b>Lick</b> Mat [NEW]' in text and text.endswith('Thanks,\nAli Afa\n')
    assert 'Search on Alibaba: https://www.alibaba.com/trade/search?SearchText=calming+dog+bed' in text
    assert '<b>Lick</b>' not in page and '&lt;b&gt;Lick&lt;/b&gt; Mat' in page and '>NEW</span>' in page
    assert 'href="https://www.amazon.com/s?k=calming+dog+bed"' in page and 'src="https://m.media-amazon.com/images/I/x.jpg"' in page
    assert 'Quotes by Friday, please.' in page and '2 products on the board' in page and 'Reply to this email to reach Ali.' in page
    subject, text, page = inform.texts([item()], [], {'owner': 'Ali', 'now': NOW, 'auto': True})
    assert subject == 'Product update, Mon 12 Oct: 1 product'
    assert text.startswith('Hi,\n\nThe product board changed, so here is where our product stands.\n\n1. Calming Dog Bed\n')
    assert 'You get it when products are added or changed.' in page
    many = [{'kind': k, 'title': 't', 'id': str(i), 'text': 'x'} for i, k in enumerate(['new', 'changed', 'changed', 'removed'])]
    assert inform.subject([item()], many, '2026-10-12') == 'Product update, Mon 12 Oct: 1 new, 2 changed, 1 removed'
