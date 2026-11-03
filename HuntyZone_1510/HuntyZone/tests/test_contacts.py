"""Brand contacts (contacts.py): who is suggested, what you type is checked and kept, details read from Amazon never
overwrite yours, and Email opens a filled-in draft (you press Send)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import contacts  # noqa: E402
import product  # noqa: E402
import storage  # noqa: E402

DETAIL = ('Business Name: Shenzhen Comfy Trading Co., Ltd.\nBusiness Address:\nRoom 101, Building 5\nLonghua District\n'
          'Shenzhen\nGuangdong\n518000\nCN\nPhone number: +86 755 1234 5678\nEmail: sales@comfy-trading.cn\n'
          'Customer Services Address:\nsame as above')


def test_the_business_details_on_a_seller_page():
    c = product.parse_contact({'name': 'Comfy Store', 'detail': DETAIL})
    assert c['business_name'] == 'Shenzhen Comfy Trading Co., Ltd.' and c['country'] == 'CN'
    assert c['address'] == 'Room 101, Building 5, Longhua District, Shenzhen, Guangdong, 518000, CN'
    assert c['phone'] == '+86 755 1234 5678' and c['email'] == 'sales@comfy-trading.cn'
    bare = product.parse_contact({'detail': 'Business Name: Artiss Pty Ltd\nBusiness Address:\n12 Smith St\nSydney NSW 2000\nAU'})
    assert bare['phone'] is None and bare['email'] is None and bare['country'] == 'AU'
    assert product.parse_contact({}) == {'business_name': None, 'address': None, 'country': None, 'phone': None, 'email': None,
                                         'detail': None}


def test_what_you_type_is_checked():
    assert contacts.clean({'email': ' ', 'website': '', 'status': 'replied', 'notes': ' met at  the fair '}) == \
        {'email': None, 'website': None, 'status': 'replied', 'notes': 'met at the fair'}
    for bad, word in (({'email': 'not-an-email'}, 'email'), ({'website': 'www.x.com'}, 'https'), ({'status': 'maybe'}, 'status')):
        with pytest.raises(ValueError, match=word):
            contacts.clean(bad)


def test_details_read_from_amazon_never_overwrite_yours(tmp_path):
    db = storage.DB(tmp_path / 'm.db').open()
    try:
        contacts.save(db, 'Comfy', {'email': 'me@comfy.com', 'notes': 'call after 5'})
        db.write(storage.save_contact, 'comfy', {'email': 'sales@comfy-trading.cn', 'phone': '+86 1', 'company': 'Comfy Ltd'},
                 None, False)
        c = db.read(storage.contacts)['comfy']
        assert c['email'] == 'me@comfy.com' and c['phone'] == '+86 1' and c['company'] == 'Comfy Ltd'
        assert c['status'] == 'to_contact' and c['notes'] == 'call after 5' and c['brand'] == 'Comfy'
        with pytest.raises(ValueError, match='brand'):
            contacts.save(db, '', {})
    finally:
        db.close()


def test_email_opens_a_filled_in_draft_and_marks_it_contacted(tmp_path):
    db = storage.DB(tmp_path / 'm.db').open()
    try:
        settings = {'mail_from_name': 'Ali Afa', 'mail_address': 'ali@gmail.com', 'mail_web': 'gmail'}
        with pytest.raises(ValueError, match='email address'):
            contacts.email(db, settings, 'Comfy', 'AU', opener=lambda *a: None)
        contacts.save(db, 'Comfy', {'email': 'sales@comfy.com'})
        opened = []
        out = contacts.email(db, settings, 'Comfy', 'AU', opener=lambda url, profile='': opened.append(str(url)), now=1000)
        assert out['opened'] and len(opened) == 1 and 'sales%40comfy.com' in opened[0] and 'Comfy' in opened[0]
        assert 'Australia' in opened[0] and 'Ali' in opened[0]
        c = db.read(storage.contacts)['comfy']
        assert c['status'] == 'contacted' and c['contacted_at'] == 1000
        t = contacts.save_template(db, 'Hello {brand}', 'Text for {market} from {me}')
        assert contacts.template(db) == t and contacts.fill(t['body'], 'Comfy', 'AE', settings) == 'Text for UAE from Ali Afa'
    finally:
        db.close()


def test_who_is_suggested():
    B = {'shipped': {'sources': [{'seller': 'Amazon US', 'brands': [{'brand': 'Comfy', 'revenue': 9000, 'famous': False},
                                                                    {'brand': 'Nike', 'revenue': 99999, 'famous': True}],
                                  'products': [{'asin': 'B0LEGREST1', 'brand': 'Comfy'}]}]},
         'missing': [{'brand': 'Owala', 'us_revenue': 2500000}, {'brand': 'Comfy', 'us_revenue': 1}],
         'ss_finds': [{'asin': 'B0SMALL001', 'brand': 'Tiny', 'units': 450, 'sellers': 2, 'risk_why': None},
                      {'asin': 'B0COLA0001', 'brand': 'Cola Co', 'units': 9000, 'sellers': 1, 'risk_why': 'food & drink'},
                      {'asin': 'B0GEN00001', 'brand': 'Generic', 'units': 900, 'sellers': 1, 'risk_why': None}]}
    out = contacts.suggestions(B, 'AU')
    assert [s['brand'] for s in out] == ['Comfy', 'Owala', 'Tiny']          # famous, risky and generic left out, once each
    assert out[0]['asin'] == 'B0LEGREST1' and 'Amazon US' in out[0]['reason'] and 'Australia' in out[1]['reason']
