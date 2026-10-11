"""Amazon product pages: Best Sellers Rank, the "bought in past month" badge, price, offers, seller and delivery.

The page script only collects raw text; parse_product() turns it into numbers, so the parsing is tested
without a browser. A product page pairs a BSR with a badge: that pair is one calibration point for the
BSR -> monthly units curve (engine/curve.py). The reviews a page shows are kept too (the critical ones).
"""
import datetime as dt
import hashlib
import math
import re
from urllib.parse import urlparse

import amazon_check as ac
import config
import countries

# Runs inside a product page.
PRODUCT_JS = r"""
() => {
  const q = s => document.querySelector(s);
  const txt = el => el ? (el.textContent || '').replace(/[‎‏]/g, '').replace(/\s+/g, ' ').trim() : '';
  const rows = {};
  document.querySelectorAll('#productDetails_detailBullets_sections1 tr, #productDetails_techSpec_section_1 tr, #prodDetails tr, #technicalSpecifications_section_1 tr')
    .forEach(tr => { const k = txt(tr.querySelector('th')), v = txt(tr.querySelector('td')); if (k && v && !(k in rows)) rows[k] = v; });
  document.querySelectorAll('#detailBullets_feature_div li, #detailBulletsWrapper_feature_div li').forEach(li => {
    const t = txt(li); const m = t.match(/^([^:]{2,60}?)\s*:\s*(.+)$/); if (m && !(m[1] in rows)) rows[m[1]] = m[2];
  });
  const bsrLinks = [...document.querySelectorAll('a[href*="/gp/bestsellers/"]')].map(a => a.getAttribute('href') || '');
  const tab = {};
  document.querySelectorAll('#tabular-buybox .tabular-buybox-text, #tabular-buybox tr').forEach(e => {
    const lab = e.getAttribute('tabular-attribute-name') || txt(e.querySelector('.tabular-buybox-label, td:first-child'));
    const val = txt(e.querySelector('.tabular-buybox-text-message, td:last-child')) || txt(e);
    if (lab) tab[lab] = val;
  });
  const img = q('#landingImage, #imgBlkFront');
  const seller = q('#sellerProfileTriggerId, #merchantInfoFeature_feature_div a[href*="seller="], #tabular-buybox a[href*="seller="], #merchant-info a[href*="seller="]');
  return {
    title: txt(q('#productTitle')),
    price: txt(q('#corePrice_feature_div .a-offscreen, #corePriceDisplay_desktop_feature_div .a-offscreen, #price_inside_buybox, #priceblock_ourprice, .a-price .a-offscreen')),
    rating: (q('#acrPopover') || {}).title || txt(q('#acrPopover .a-icon-alt')),
    reviews: txt(q('#acrCustomerReviewText')),
    badge: txt(q('#social-proofing-faceout-title-tk_bought')),
    offers: txt(q('#aod-ingress-link, #olpLinkWidget_feature_div, #buybox-see-all-buying-choices')),
    delivery: txt(q('#mir-layout-DELIVERY_BLOCK, #deliveryBlockMessage, #ddmDeliveryMessage')),
    sold_by: tab['Sold by'] || txt(q('#merchantInfoFeature_feature_div .offer-display-feature-text-message, #sellerProfileTriggerId, #merchant-info')),
    ships_from: tab['Ships from'] || tab['Dispatches from'] || txt(q('#fulfillerInfoFeature_feature_div .offer-display-feature-text-message')),
    variations: document.querySelectorAll('#twister li[data-asin], #twister-plus-inline-twister li[data-asin], [id^="inline-twister-row"] li').length,
    image: img ? (img.getAttribute('data-old-hires') || img.getAttribute('src') || '') : '',
    seller_link: seller ? seller.getAttribute('href') || '' : '', byline: txt(q('#bylineInfo')),
    rows, bsr_links: bsrLinks.slice(0, 20),
  };
}
"""

# Runs inside a product page or a reviews page: the reviews it shows, and whether a shopper is signed in.
REVIEWS_JS = r"""
() => {
  const txt = e => e ? (e.textContent || '').replace(/\s+/g, ' ').trim() : '';
  const acct = txt(document.querySelector('#nav-link-accountList-nav-line-1'));
  const items = [...document.querySelectorAll('[data-hook="review"]')].slice(0, 40).map(el => {
    const alt = el.querySelector('[data-hook$="review-star-rating"] .a-icon-alt, i[class*="a-star-"] .a-icon-alt');
    const star = el.querySelector('i[class*="a-star-"]');
    const title = el.querySelector('[data-hook="review-title"]');
    const body = el.querySelector('[data-hook="review-collapsed"]') || el.querySelector('[data-hook="review-body"] .cr-original-review-content')
      || el.querySelector('[data-hook="review-body"]');
    return {id: el.id || '', stars: txt(alt) || ((star && star.className.match(/a-star-(?:mini-|medium-)?(\d)\b/)) || [])[1] || '',
            title: title ? txt(title.querySelector('span:not(.a-icon-alt):not(.a-letter-space):last-child')) || txt(title) : '',
            body: txt(body).slice(0, 3000), date: txt(el.querySelector('[data-hook="review-date"]')),
            helpful: txt(el.querySelector('[data-hook="helpful-vote-statement"]')),
            verified: !!el.querySelector('[data-hook^="avp-badge"]'), local: !el.closest('#cm-cr-global-review-list')};
  });
  return {items, signed_in: acct ? !/sign in/i.test(acct) : null};
}
"""
COUNTRY = {'AU': 'australia', 'AE': 'united arab emirates', 'US': 'united states'}     # "Reviewed in Australia on ..."
STARS = re.compile(r'(\d(?:[.,]\d)?)\s*out of\s*5', re.I)
REVIEW_JUNK = re.compile(r'the media could not be loaded\.?|read (?:more|less)\s*$', re.I)


class SignInNeeded(Exception):
    """Amazon shows its reviews pages only to shoppers who are signed in."""


# Runs inside a seller's profile page (/sp?seller=...): its name, "Detailed Seller Information" and feedback.
SELLER_JS = r"""
() => {
  const q = s => document.querySelector(s);
  const text = el => el ? ((el.innerText || '').trim() || (el.textContent || '').replace(/\s+/g, ' ').trim()) : '';
  return {name: text(q('#seller-name, #sellerName-rd, #sellerName')).slice(0, 200),
          detail: text(q('#page-section-detail-seller-info')).slice(0, 2000),
          feedback: text(q('#seller-info-feedback-summary, #feedback-summary-table')).slice(0, 600)};
}
"""
BRAND_KEYS = ('brand', 'brand name', 'manufacturer')


RANK = re.compile(r'#?\s*([\d][\d,.]*)\s+in\s+([^#(\n]+?)(?=\s*\(|\s*#|$)', re.I)
DFA_KEYS = ('date first available', 'date first listed on amazon')
DIM_KEYS = ('package dimensions', 'product dimensions', 'item dimensions l x w x h', 'item package dimensions l x w x h')
WEIGHT_KEYS = ('item weight', 'package weight', 'weight')
UNIT_CM = {'cm': 1.0, 'centimetres': 1.0, 'centimeters': 1.0, 'mm': 0.1, 'millimetres': 0.1, 'millimeters': 0.1,
           'inches': 2.54, 'inch': 2.54, 'in': 2.54, '"': 2.54}
UNIT_KG = {'kg': 1.0, 'kilograms': 1.0, 'g': 0.001, 'grams': 0.001, 'pounds': 0.4536, 'pound': 0.4536, 'lbs': 0.4536,
           'lb': 0.4536, 'ounces': 0.02835, 'ounce': 0.02835, 'oz': 0.02835}


def _row(rows, keys):
    for k, v in (rows or {}).items():
        if k.strip().lower().rstrip(' :') in keys:
            return v
    return None


def parse_ranks(text):
    """'#1,234 in Home & Kitchen (See Top 100) #5 in Moving Bags' -> [(1234, 'Home & Kitchen'), (5, 'Moving Bags')]."""
    out = []
    clean = re.sub(r'\([^)]*\)', ' ', (text or '').replace('‎', '').replace('‏', ''))   # "(See Top 100 in ...)"
    for n, cat in RANK.findall(clean):
        try:
            out.append((int(n.replace(',', '').replace('.', '')), cat.strip(' ,')))
        except ValueError:
            continue
    return out


def root_node(links):
    """The root category slug from the first Best Sellers link: /gp/bestsellers/home-garden/ref=... -> 'home-garden'."""
    for h in links or []:
        m = re.search(r'/gp/bestsellers/([a-z0-9-]+)(?:/(\d+))?', h)
        if m and m.group(1) not in ('ref',):
            return m.group(1)
    return None


def parse_date(text):
    """'March 3, 2023' (US) or '3 March 2023' (AU/AE) -> '2023-03-03'."""
    t = (text or '').strip()
    for fmt in ('%B %d, %Y', '%d %B %Y', '%b %d, %Y', '%d %b %Y', '%d %B, %Y', '%d/%m/%Y'):
        try:
            return dt.datetime.strptime(t, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return None


def parse_dims(text):
    """'30 x 20 x 10 cm; 500 g' -> ([30, 20, 10] in cm, longest first; weight kg or None)."""
    if not text:
        return None, None
    t = text.lower().replace('×', 'x')
    dims = None
    m = re.search(r'([\d.]+)\s*x\s*([\d.]+)\s*x\s*([\d.]+)\s*([a-z"]+)', t)
    if m:
        f = UNIT_CM.get(m.group(4).strip('.'), None)
        if f:
            dims = sorted((round(float(m.group(i)) * f, 2) for i in (1, 2, 3)), reverse=True)
    return dims, parse_weight(t.split(';', 1)[1]) if ';' in t else None


def parse_weight(text):
    m = re.search(r'([\d.,]+)\s*(kilograms|kg|grams|g|pounds|pound|lbs|lb|ounces|ounce|oz)\b', (text or '').lower())
    if not m:
        return None
    try:
        return round(float(m.group(1).replace(',', '')) * UNIT_KG[m.group(2)], 3)
    except ValueError:
        return None


def parse_size_text(text):
    """Size and weight pasted from a supplier page (or anywhere): 'Single package size: 30X20X10 cm, Single gross
    weight: 0.500 kg' or '30*20*10cm 500g' -> ([30, 20, 10] cm, longest first; 0.5 kg). A gross weight wins over a net one."""
    t = (text or '').lower().replace('*', 'x').replace('×', 'x')
    dims, _ = parse_dims(t)
    rest = re.sub(r'[\d.]+\s*x\s*[\d.]+\s*x\s*[\d.]+\s*[a-z"]*', ' ', t)
    gross = re.search(r'(?:gross|\bg\.\s?w\.?)[^\d]{0,25}([\d.,]+\s*[a-z]+)', rest)
    return dims, (parse_weight(gross.group(1)) if gross else None) or parse_weight(rest)


def _helpful(text):
    """'12 people found this helpful' -> 12, 'One person found this helpful' -> 1."""
    t = (text or '').strip().lower()
    if t.startswith('one '):
        return 1
    m = re.search(r'\d[\d,.]*', t)
    return int(re.sub(r'[,.]', '', m.group(0))) if m else 0


def parse_reviews(items, code):
    """Reviews as a page shows them -> [{id, stars, title, body, day, country, local, helpful, verified}].
    `local` = reviewed in this marketplace. Reviews without a star rating are skipped."""
    out, seen = [], set()
    for r in items or []:
        s = str(r.get('stars') or '')
        m = STARS.search(s)
        stars = float(m.group(1).replace(',', '.')) if m else float(s) if s.isdigit() else None
        if not stars:
            continue
        when = re.search(r'reviewed in (.+?) on (.+)$', r.get('date') or '', re.I)
        country = when.group(1).strip() if when else ''
        title = re.sub(r'^\s*\d(?:[.,]\d)?\s*out of\s*5\s*stars\s*', '', r.get('title') or '', flags=re.I).strip()
        body = ' '.join(REVIEW_JUNK.sub(' ', r.get('body') or '').split())[:1500]
        rid = re.search(r'R[0-9A-Z]{8,}', r.get('id') or '')
        rid = rid.group(0) if rid else 'h' + hashlib.sha1((title + '|' + body).encode()).hexdigest()[:15]
        if rid in seen or not (title or body):
            continue
        seen.add(rid)
        out.append({'id': rid, 'stars': stars, 'title': title[:300], 'body': body, 'day': parse_date(when.group(2)) if when else None,
                    'country': country[:60], 'local': COUNTRY[code] in country.lower() if country else bool(r.get('local', True)),
                    'helpful': _helpful(r.get('helpful')), 'verified': bool(r.get('verified'))})
    return out


def clean_seller(text):
    """'Ships from and sold by Amazon AU.' -> 'Amazon AU'; 'Sold by X and Fulfilled by Amazon.' -> 'X'."""
    t = re.sub(r'^(?:ships from and sold by|dispatched from and sold by|sold by)\s+', '', (text or '').strip(), flags=re.I)
    t = re.split(r'\s+and\s+(?:fulfilled|delivered|shipped|dispatched)\s+by\s+', t, flags=re.I)[0]
    return t.strip(' .')[:120] or None


def parse_brand(byline):
    """'Visit the Bedsure Store' or 'Brand: Bedsure' -> 'Bedsure'."""
    t = ' '.join((byline or '').split())
    m = re.match(r'(?:visit the\s+(.+?)\s+store|brand\s*:\s*(.+))$', t, re.I)
    return ((m.group(1) or m.group(2)) if m else t)[:80] or None


def fulfillment(sold_by, ships_from):
    """'amazon' (Amazon sells it), 'fba' (a seller's stock in Amazon's warehouse), 'fbm' (the seller ships it) or None."""
    s, f = (sold_by or '').lower().strip(), (ships_from or '').lower().strip()
    if re.match(r'(?:(?:ships from|dispatched from) and )?(?:sold by )?amazon\b', s):
        return 'amazon'
    if not f:
        return 'fba' if 'fulfilled by amazon' in s else None
    return 'fba' if re.match(r'amazon\b', f) else 'fbm'


def parse_seller(raw):
    """A seller's profile page -> {name, business_name, country, rating_pct, ratings, status}. The country is the last
    line of the business address under "Detailed Seller Information" (None when Amazon does not show one)."""
    detail = raw.get('detail') or ''
    business = re.search(r'business name\s*:\s*([^\n]+)', detail, re.I)
    address = re.search(r'business address\s*:\s*(.+)', detail, re.I | re.S)
    country = None
    if address:
        lines = [x.strip() for x in address.group(1).splitlines() if x.strip()]
        country = countries.code_of(lines[-1]) if lines else None
        if not country:                                 # all on one line: only a code right at the end counts
            tail = re.search(r'(?:^|[\s\d,])([A-Z]{2})\s*$', address.group(1).strip())
            country = countries.code_of(tail.group(1)) if tail else None
    fb = raw.get('feedback') or ''
    pct = re.search(r'(\d{1,3})\s*%\s*positive', fb, re.I)
    n = re.search(r'(\d[\d,.]*)\s+ratings?\b', fb, re.I)
    name = ' '.join((raw.get('name') or '').split())[:120] or None
    return {'name': name, 'business_name': ' '.join(business.group(1).split())[:160] if business else None, 'country': country,
            'rating_pct': float(pct.group(1)) if pct else None, 'ratings': int(re.sub(r'[,.]', '', n.group(1))) if n else None,
            'status': 'ok' if name or detail else 'empty'}


def parse_product(raw, code, today=None):
    """Raw page text -> one product snapshot (numbers only; missing things are None)."""
    from engine import badge
    rows = raw.get('rows') or {}
    bsr_text = _row(rows, ('best sellers rank', 'best seller rank', 'amazon best sellers rank')) or ''
    ranks = parse_ranks(bsr_text)
    dims, w = parse_dims(_row(rows, DIM_KEYS))
    weight = parse_weight(_row(rows, WEIGHT_KEYS)) or w
    delivery = raw.get('delivery') or ''
    days = ac.delivery_days(delivery, today or config.market_today(code))
    sold_by, ships_from = (raw.get('sold_by') or '')[:120], (raw.get('ships_from') or '')[:120]
    cls = ac.classify({'days': days, 'delivery': delivery, 'prime': 'amazon' in ships_from.lower()})
    offers = re.search(r'\((\d[\d,]*)\)|(\d[\d,]*)\s+(?:new|offers|options)', raw.get('offers') or '', re.I)
    seller = re.search(r'[?&]seller=([A-Za-z0-9]{6,20})', raw.get('seller_link') or '')
    return {
        'title': (raw.get('title') or '')[:300], 'image': raw.get('image') or '',
        'price': ac.parse_price(raw.get('price')), 'rating': ac.parse_price(raw.get('rating')),
        'reviews': ac.parse_count(raw.get('reviews')),
        'badge_low': badge.parse_badge(raw.get('badge')),
        'root_rank': ranks[0][0] if ranks else None, 'root_name': ranks[0][1] if ranks else None,
        'root_node': root_node(raw.get('bsr_links')), 'sub_ranks': [{'rank': r, 'name': n} for r, n in ranks[1:]],
        'offers': int((offers.group(1) or offers.group(2)).replace(',', '')) if offers else None,
        'buybox_seller': clean_seller(sold_by), 'ships_from': ships_from or None,
        'sold_by_amazon': 1 if 'amazon' in sold_by.lower() else 0,
        'seller_id': seller.group(1).upper() if seller else None, 'fulfillment': fulfillment(sold_by, ships_from),
        'brand': parse_brand(raw.get('byline')) or ((_row(rows, BRAND_KEYS) or '')[:80] or None),
        'n_variations': raw.get('variations') or 0,
        'delivery_days': days, 'origin': cls['origin'],
        'dfa_day': parse_date(_row(rows, DFA_KEYS)),
        'pkg_cm': dims, 'pkg_kg': weight,
        'review_items': parse_reviews(raw['review_items'], code) if 'review_items' in raw else None,
        'signed_in': raw.get('signed_in'),
    }


def page_status(p):
    """ok when the page had a title and a rank; partial without a rank (layout canary); empty_suspect otherwise."""
    if not p['title']:
        return 'empty_suspect'
    return 'ok' if p['root_rank'] else 'partial'


async def read_product(checker, asin, code, reviews=False):
    """Open /dp/ASIN in the background browser and read it. With `reviews`, wait for the reviews section too (it can
    load late), so the reviews it shows are kept."""
    await checker.start()
    page = await checker.ctx.new_page()
    try:
        await checker._goto(page, '%s/dp/%s%s' % (ac.MARKETS[code]['site'], asin, '?language=en_AE' if code == 'AE' else ''),
                            code)
        try:
            await page.wait_for_selector('#productTitle', timeout=6000)
        except Exception:
            pass
        if reviews:
            try:
                await page.evaluate("() => { const r = document.querySelector('#reviewsMedley, #customerReviews'); "
                                    "if (r) r.scrollIntoView(); else window.scrollTo(0, document.body.scrollHeight); }")
                await page.wait_for_selector('[data-hook="review"]', timeout=4000)
            except Exception:
                pass
        raw = await page.evaluate(PRODUCT_JS)
        rv = await page.evaluate(REVIEWS_JS)
        raw['review_items'], raw['signed_in'] = rv['items'], rv['signed_in']
    finally:
        await page.close()
    p = parse_product(raw, code)
    p['status'] = page_status(p)
    return p


def critical_url(code, asin):
    """The listing's 1-3 star reviews on Amazon, most helpful first."""
    return '%s/product-reviews/%s/?filterByStar=critical&sortBy=helpful&reviewerType=all_reviews%s' % (
        ac.MARKETS[code]['site'], asin, '&language=en_AE' if code == 'AE' else '')


async def read_critical_reviews(checker, asin, code):
    """The most helpful 1-3 star reviews from the listing's reviews page (10 of them). Raises SignInNeeded when
    Amazon sends the browser to its sign-in page instead."""
    await checker.start()
    page = await checker.ctx.new_page()
    try:
        await checker._goto(page, critical_url(code, asin), code)
        if ac.SIGNIN.search(urlparse(page.url).path):
            raise SignInNeeded('Amazon %s shows reviews pages only to shoppers who are signed in' % ac.MARKETS[code]['name'])
        try:
            await page.wait_for_selector('[data-hook="review"]', timeout=5000)
        except Exception:
            pass
        rv = await page.evaluate(REVIEWS_JS)
    finally:
        await page.close()
    return parse_reviews(rv['items'], code)


def seller_url(code, seller_id):
    """A seller's profile page on Amazon (store name, business address, feedback)."""
    return '%s/sp?seller=%s%s' % (ac.MARKETS[code]['site'], seller_id, '&language=en_AE' if code == 'AE' else '')


async def read_seller(checker, seller_id, code):
    """Open a seller's profile page in the background browser and read it."""
    await checker.start()
    page = await checker.ctx.new_page()
    try:
        await checker._goto(page, seller_url(code, seller_id), code)
        try:
            await page.wait_for_selector('#seller-name, #page-section-detail-seller-info', timeout=6000)
        except Exception:
            pass
        raw = await page.evaluate(SELLER_JS)
    finally:
        await page.close()
    return parse_seller(raw)


def calib_point(market, asin, p, ts):
    """A product page with both a BSR and a badge = one interval observation of the sales curve."""
    from engine import badge
    if not p.get('root_rank') or not p.get('badge_low'):
        return None
    lo, hi = badge.bucket(p['badge_low'], market)
    return {'market': market, 'node': p.get('root_node') or '', 'ts': int(ts), 'ln_bsr': math.log(p['root_rank']),
            'low': lo, 'high': hi, 'kind': 'badge', 'asin': asin}
