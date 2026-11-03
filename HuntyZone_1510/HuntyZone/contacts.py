"""Brand contacts (Market Gaps > Contacts): the brands worth writing to about stocking them in Australia or the UAE, and
what you know about each.

Who is suggested (marketgaps.baseline):
- brands shipped in from abroad: most of their sales here go through Amazon's Global Store or an import reseller, so
  shoppers wait for delivery and nobody stocks them locally (famous brands are left out: only their owner sells them new);
- big US brands that are not on this Amazon at all (Brands missing here);
- the brands behind SmartScout's products with proven demand and few sellers.

What is known about a brand comes from two places only: Amazon's own seller pages (the business name, address and,
for some sellers, a phone and an email under "Detailed Seller Information") and what you type. Nothing is collected
from websites; Find website opens a search for you to look yourself. Email opens a filled-in draft in your Gmail or
Outlook (mailer.open_in_browser): you read it and press Send.
"""
import asyncio
import logging
import re
import time

import amazon_check as ac
import config
import mailer
import product
import storage
from engine import marketgaps as mg

log = logging.getLogger('pc')
STATUSES = ('to_contact', 'contacted', 'replied', 'not_interested')
STATUS_LABEL = {'to_contact': 'To contact', 'contacted': 'Contacted', 'replied': 'Replied', 'not_interested': 'Not interested'}
TEMPLATE_KEY = 'contact_template'              # ui_state: {'subject', 'body'} (too long for a setting)
TEMPLATE = {
    'subject': 'Stocking {brand} on Amazon {market}',
    'body': 'Hello {brand} team,\n\n'
            'I sell on Amazon {market}. Shoppers there buy {brand}, but most orders are shipped from abroad, so they wait for '
            'delivery. I would like to stock your products locally (Amazon FBA in {market}) as an authorised reseller or '
            'distributor.\n\n'
            'Could you tell me who looks after wholesale or distribution, and your terms for a first order?\n\n'
            'Kind regards,\n{me}\n\n'
            'If you would rather not hear from me, just reply and I will not write again.'}
READ_MAX = 10                                  # brands a Read details reads per press (two pages each at most)
SUGGEST = 300                                  # suggestions shown per market
URL_RX = re.compile(r'^https://[^\s<>"\']{3,300}$')


def key(brand):
    return mg.brand_key(brand)


def market_name(market):
    return (ac.MARKETS.get(market) or {}).get('name', market)


def template(db):
    t = db.read(storage.get_state, TEMPLATE_KEY, None) or {}
    return {'subject': t.get('subject') or TEMPLATE['subject'], 'body': t.get('body') or TEMPLATE['body']}


def save_template(db, subject, body):
    subject, body = ' '.join(str(subject or '').split())[:200], str(body or '').strip()[:4000]
    if not subject or not body:
        raise ValueError('Give the email a subject and a text.')
    db.write(storage.set_state, TEMPLATE_KEY, {'subject': subject, 'body': body})
    return {'subject': subject, 'body': body}


def suggestions(B, market):
    """[{brand, key, reason, revenue, seller, asin, storefront_url, kind}] from a market's baseline, best reasons first."""
    out, seen = [], set()

    def add(brand, reason, kind, revenue=None, seller=None, asin=None, storefront=None):
        k = key(brand)
        if not k or k in mg.GENERIC or k in seen:
            return
        seen.add(k)
        out.append({'brand': brand, 'key': k, 'reason': reason, 'kind': kind, 'revenue': revenue, 'seller': seller,
                    'asin': asin, 'storefront_url': storefront})
    shipped = (B.get('shipped') or {}).get('sources') or []
    asins = {}
    for src in shipped:
        for p in src.get('products') or ():
            if p.get('brand'):
                asins.setdefault(key(p['brand']), p['asin'])
    for p in B.get('ss_finds') or ():
        if p.get('brand'):
            asins.setdefault(key(p['brand']), p['asin'])
    for src in shipped:
        for b in src.get('brands') or ():
            if not b.get('famous'):
                add(b['brand'], 'Shipped in: most sales here go through %s (from abroad)' % src['seller'], 'shipped',
                    b.get('revenue'), src['seller'], asins.get(key(b['brand'])), b.get('storefront_url'))
    for b in B.get('missing') or ():
        add(b['brand'], 'Sells US$%s a month in the USA, not on Amazon %s' % (f"{round(b.get('us_revenue') or 0):,}",
                                                                              market_name(market)),
            'missing', b.get('us_revenue'))
    for p in B.get('ss_finds') or ():
        if p.get('brand') and not p.get('risk_why'):
            add(p['brand'], 'SmartScout: about %s sold a month, %d seller%s on the listing'
                % (f"{p['units']:,}", p['sellers'], '' if p['sellers'] == 1 else 's'), 'product', None, None, p['asin'])
    return out[:SUGGEST]


def table(db, settings, market):
    """Market Gaps > Contacts: your saved contacts first (any market), then the suggestions of this market."""
    import marketgaps
    B = marketgaps.baseline(db, market)
    saved = db.read(storage.contacts)
    sug = suggestions(B, market)
    by = {s['key']: s for s in sug}
    rows = []
    for k, c in sorted(saved.items(), key=lambda kv: -(kv[1].get('updated_at') or 0)):
        rows.append({**(by.get(k) or {'brand': c.get('brand'), 'key': k, 'reason': None, 'kind': 'saved'}), 'saved': c})
    rows += [{**s, 'saved': None} for s in sug if s['key'] not in saved]
    return {'market': market, 'rows': rows, 'statuses': [{'status': s, 'label': STATUS_LABEL[s]} for s in STATUSES],
            'template': template(db), 'site': ac.MARKETS[market]['site'],
            'counts': {s: sum(1 for c in saved.values() if c.get('status') == s) for s in STATUSES}}


def clean(d):
    """The fields of a contact you typed, checked. Raises ValueError with a plain message."""
    out = {}
    for k in ('company', 'phone', 'address', 'country', 'notes', 'markets', 'brand', 'seller_id', 'seller_market'):
        if k in d:
            out[k] = ' '.join(str(d[k] or '').split())[:600 if k == 'notes' else 200] or None
    if 'email' in d:
        e = str(d['email'] or '').strip()
        if e and not config.EMAIL_RX.match(e):
            raise ValueError('That email address does not look right.')
        out['email'] = e or None
    for k in ('website', 'storefront_url'):
        if k in d:
            u = str(d[k] or '').strip()
            if u and not URL_RX.match(u):
                raise ValueError('Paste the whole link, starting with https://')
            out[k] = u or None
    if 'status' in d:
        if d['status'] not in STATUSES:
            raise ValueError('Unknown status.')
        out['status'] = d['status']
    return out


def save(db, brand, fields):
    k = key(brand)
    if not k:
        raise ValueError('Which brand?')
    f = clean(fields)
    f.setdefault('brand', ' '.join(str(brand).split())[:120])
    return db.write(storage.save_contact, k, f)


async def read_details(S, market, items, progress=None):
    """For each brand (items: [{brand, asin, seller_id}]): its own seller on Amazon (the seller of one of its listings,
    unless you saved one) and that seller's business details. Amazon itself and its Global Store are skipped: their
    pages say nothing about the brand. Returns {'read', 'found', 'notes'}."""
    say = progress or (lambda m: None)
    found, read, notes = 0, 0, []
    for i, it in enumerate(items[:READ_MAX], 1):
        brand = it.get('brand') or ''
        say('Reading %s (%d of %d)' % (brand, i, min(len(items), READ_MAX)))
        sid = (it.get('seller_id') or '').strip().upper()
        try:
            if not sid and it.get('asin'):
                p = await product.read_product(S.checker, it['asin'], market)
                read += 1
                if p.get('sold_by_amazon') or p.get('fulfillment') == 'amazon':
                    notes.append('%s: Amazon sells it, so no seller to contact' % brand)
                    continue
                sid = (p.get('seller_id') or p.get('merchant_id') or '').strip().upper()
            if not sid:
                notes.append('%s: no seller found on Amazon' % brand)
                continue
            prof, contact = await product.read_seller_page(S.checker, sid, market)
            read += 1
        except (ac.BlockedError,) as e:
            notes.append('Amazon paused reading (%s): try again later' % (str(e) or e.__class__.__name__)[:60])
            break
        except Exception as e:                                       # noqa: BLE001 - one brand never ends the job
            log.warning('contact %s %s failed: %s', market, brand, e)
            notes.append('%s: could not read (%s)' % (brand, str(e)[:60]))
            continue
        await S.db.awrite(storage.save_seller, market, sid, prof)
        await S.db.awrite(storage.save_seller_detail, market, sid, contact)
        fields = {k: contact.get(k) for k in ('address', 'country', 'phone', 'email') if contact.get(k)}
        if contact.get('business_name'):
            fields['company'] = contact['business_name']
        fields.update(seller_id=sid, seller_market=market, source='amazon seller page', brand=brand)
        await S.db.awrite(storage.save_contact, key(brand), fields, None, False)
        found += 1 if (contact.get('email') or contact.get('phone') or contact.get('address')) else 0
        await asyncio.sleep(0)
    return {'read': read, 'found': found, 'notes': notes}


def fill(text, brand, market, settings):
    me = settings.get('mail_from_name') or settings.get('mail_address') or ''
    return (text.replace('{brand}', brand or '').replace('{market}', market_name(market))
            .replace('{me}', me).strip())


def email(db, settings, brand, market, opener=None, now=None):
    """Open a filled-in email to a brand's saved address in your Gmail or Outlook (you press Send); the contact becomes
    'contacted'. Raises mailer.MailError / ValueError with a plain message."""
    k = key(brand)
    c = db.read(storage.contacts).get(k) or {}
    if not c.get('email'):
        raise ValueError('Add an email address for %s first (Edit).' % brand)
    t = template(db)
    url = mailer.open_in_browser(settings, [(c.get('company') or brand, c['email'])], fill(t['subject'], brand, market, settings),
                                 fill(t['body'], brand, market, settings), opener=opener, now=now)
    db.write(storage.save_contact, k, {'status': 'contacted' if c.get('status') in (None, 'to_contact') else c['status'],
                                       'contacted_at': int(now or time.time())})
    return {'opened': True, 'pasted': bool(getattr(url, 'pasted', False))}
