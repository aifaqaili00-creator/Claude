"""Who sells one listing, read from its product page or from Amazon's Selling Partner API, in one shape:

{'seller_id', 'seller', 'country' (where the seller's business is, None when not read), 'amazon' (Amazon itself),
 'fba' (stock in Amazon's warehouse), 'stock' ('local' | 'abroad' | None: where it ships from, seen from the
 marketplace), 'days' (fastest delivery), 'buybox' (the offer a shopper gets by default)}

Amazon itself is recognised by how the page names the seller, or by its seller id (learned from pages: never
hard-coded). Amazon's Global Store ("Amazon US" selling on Amazon Australia) is Amazon with stock 'abroad' and the
store's country. Pure functions.
"""
import re

# "Amazon US", "Amazon.co.uk", "Amazon Germany"... : Amazon's Global Store selling another marketplace's stock
STORE_RX = re.compile(r'^amazon(?:\.com)?[ .]*(us|usa|com|uk|co\.uk|germany|de|japan|jp|france|fr|italy|it|spain|es|canada|ca|'
                      r'india|in|mexico|mx|au|com\.au|australia|ae|uae)$', re.I)
STORE_COUNTRY = {'us': 'US', 'usa': 'US', 'com': 'US', 'uk': 'GB', 'co.uk': 'GB', 'germany': 'DE', 'de': 'DE', 'japan': 'JP',
                 'jp': 'JP', 'france': 'FR', 'fr': 'FR', 'italy': 'IT', 'it': 'IT', 'spain': 'ES', 'es': 'ES', 'canada': 'CA',
                 'ca': 'CA', 'india': 'IN', 'in': 'IN', 'mexico': 'MX', 'mx': 'MX', 'au': 'AU', 'com.au': 'AU',
                 'australia': 'AU', 'ae': 'AE', 'uae': 'AE'}


def store_country(name, market):
    """The country of an Amazon Global Store seller name seen in a marketplace ("Amazon US" on Amazon Australia -> 'US'),
    else None (the local Amazon, or not Amazon at all)."""
    m = STORE_RX.match(' '.join(str(name or '').split()))
    c = STORE_COUNTRY.get(m.group(1).lower()) if m else None
    return c if c and c != (market or '').upper() else None


def stock_of(origin=None, intl=False, fba=None, ships_country=None, market=None):
    """'local', 'abroad' or None. SP-API's ships-from country wins, then Amazon's warehouse (FBA), then the
    delivery text (international or 10+ days = abroad)."""
    if ships_country and market:
        return 'local' if ships_country.upper() == market.upper() else 'abroad'
    if intl or origin == 'overseas':
        return 'abroad'
    if fba or origin == 'local':
        return 'local'
    return None


def from_page(p, market, seller=None):
    """The buy-box offer of one product page reading (product.parse_product, or a product_snapshot row with its
    `intl` flag), with the seller's profile (a seller row) when read. None when the page shows nobody selling it."""
    p = p or {}
    if p.get('available') is False:
        return None
    ful = p.get('fulfillment')
    sid = p.get('seller_id') or p.get('merchant_id')
    name = p.get('buybox_seller')
    if not (ful or sid or name):
        return None
    amazon = ful == 'amazon'
    stock = stock_of(p.get('origin'), p.get('intl'), ful in ('fba', 'amazon'))
    store = store_country(name, market) if amazon else None
    if store:                                           # Amazon's Global Store: Amazon US ships it in
        stock = 'abroad'
    if amazon and stock == 'abroad':
        country = store
    elif amazon:
        country = market
    else:
        country = (seller or {}).get('country')
    who = ('%s (Global Store)' % ' '.join(name.split()) if store else 'Amazon') if amazon else (seller or {}).get('name') or name
    return {'seller_id': None if amazon else sid, 'seller': who,
            'country': country, 'amazon': amazon, 'fba': ful in ('fba', 'amazon'), 'stock': stock,
            'days': p.get('delivery_days'), 'buybox': True}


def _amount(x):
    try:
        return float(x['Amount']) if isinstance(x, dict) and x.get('Amount') is not None else None
    except (TypeError, ValueError):
        return None


def from_spapi(payload, market, amazon_ids=()):
    """Product Pricing getItemOffers payload -> (offers, summary). Amazon itself is the offer whose seller id is one of
    `amazon_ids`. summary: {'n_offers', 'n_fba', 'n_fbm', 'amazon', 'available', 'price' (the buy box, else the lowest
    offer, in the marketplace's currency)}."""
    payload = payload or {}
    ids = {str(i).upper() for i in amazon_ids or ()}
    offers = []
    for o in payload.get('Offers') or []:
        sid = str(o.get('SellerId') or '').upper() or None
        fba = bool(o.get('IsFulfilledByAmazon'))
        ships = ((o.get('ShipsFrom') or {}).get('Country') or '').upper() or None
        amazon = bool(sid and sid in ids)
        offers.append({'seller_id': None if amazon else sid, 'seller': 'Amazon' if amazon else None,
                       'country': market if amazon else None, 'amazon': amazon, 'fba': fba,
                       'stock': stock_of(fba=fba, ships_country=ships, market=market) or ('local' if fba else None),
                       'days': None, 'buybox': bool(o.get('IsBuyBoxWinner'))})
    s = payload.get('Summary') or {}
    by = {'amazon': 0, 'merchant': 0}
    for x in s.get('NumberOfOffers') or []:
        if str(x.get('condition') or 'new').lower() == 'new':
            k = str(x.get('fulfillmentChannel') or '').lower()
            if k in by:
                by[k] += int(x.get('OfferCount') or 0)
    total = s.get('TotalOfferCount')
    total = int(total) if total is not None else len(offers)
    price = next((_amount(b.get('LandedPrice')) or _amount(b.get('ListingPrice')) for b in s.get('BuyBoxPrices') or []
                  if str(b.get('condition') or 'new').lower() == 'new'), None)
    if price is None:
        prices = [p for p in (_amount(o.get('ListingPrice')) for o in payload.get('Offers') or []) if p]
        price = min(prices) if prices else None
    return offers, {'n_offers': total, 'n_fba': by['amazon'], 'n_fbm': by['merchant'],
                    'amazon': any(o['amazon'] for o in offers), 'available': total > 0, 'price': price}


def merge(offers, more):
    """Offers of one listing from two readings: one per seller (the first reading wins, the second fills gaps)."""
    out, seen = [], {}
    for o in list(offers or []) + list(more or []):
        key = 'amazon' if o.get('amazon') else o.get('seller_id') or o.get('seller')
        if key in seen:
            have = seen[key]
            for k, v in o.items():
                if have.get(k) is None and v is not None:
                    have[k] = v
            continue
        seen[key] = dict(o)
        out.append(seen[key])
    return out
