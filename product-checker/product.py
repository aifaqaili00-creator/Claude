"""Amazon product pages: Best Sellers Rank, the "bought in past month" badge, price, offers, seller and delivery.

The page script only collects raw text; parse_product() turns it into numbers, so the parsing is tested
without a browser. A product page pairs a BSR with a badge: that pair is one calibration point for the
BSR -> monthly units curve (engine/curve.py).
"""
import datetime as dt
import math
import re

import amazon_check as ac
import config

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
    rows, bsr_links: bsrLinks.slice(0, 20),
  };
}
"""

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
    return {
        'title': (raw.get('title') or '')[:300], 'image': raw.get('image') or '',
        'price': ac.parse_price(raw.get('price')), 'rating': ac.parse_price(raw.get('rating')),
        'reviews': ac.parse_count(raw.get('reviews')),
        'badge_low': badge.parse_badge(raw.get('badge')),
        'root_rank': ranks[0][0] if ranks else None, 'root_name': ranks[0][1] if ranks else None,
        'root_node': root_node(raw.get('bsr_links')), 'sub_ranks': [{'rank': r, 'name': n} for r, n in ranks[1:]],
        'offers': int((offers.group(1) or offers.group(2)).replace(',', '')) if offers else None,
        'buybox_seller': sold_by or None, 'ships_from': ships_from or None,
        'sold_by_amazon': 1 if 'amazon' in sold_by.lower() else 0,
        'n_variations': raw.get('variations') or 0,
        'delivery_days': days, 'origin': cls['origin'],
        'dfa_day': parse_date(_row(rows, DFA_KEYS)),
        'pkg_cm': dims, 'pkg_kg': weight,
    }


def page_status(p):
    """ok when the page had a title and a rank; partial without a rank (layout canary); empty_suspect otherwise."""
    if not p['title']:
        return 'empty_suspect'
    return 'ok' if p['root_rank'] else 'partial'


async def read_product(checker, asin, code):
    """Open /dp/ASIN in the background browser and read it."""
    await checker.start()
    page = await checker.ctx.new_page()
    try:
        await checker._goto(page, '%s/dp/%s%s' % (ac.MARKETS[code]['site'], asin, '?language=en_AE' if code == 'AE' else ''),
                            code)
        try:
            await page.wait_for_selector('#productTitle', timeout=6000)
        except Exception:
            pass
        raw = await page.evaluate(PRODUCT_JS)
    finally:
        await page.close()
    p = parse_product(raw, code)
    p['status'] = page_status(p)
    return p


def calib_point(market, asin, p, ts):
    """A product page with both a BSR and a badge = one interval observation of the sales curve."""
    from engine import badge
    if not p.get('root_rank') or not p.get('badge_low'):
        return None
    lo, hi = badge.bucket(p['badge_low'], market)
    return {'market': market, 'node': p.get('root_node') or '', 'ts': int(ts), 'ln_bsr': math.log(p['root_rank']),
            'low': lo, 'high': hi, 'kind': 'badge', 'asin': asin}
