"""Amazon Selling Partner API (optional): your own seller keys, used where they help instead of reading pages.

- Catalog Items: is an ASIN listed in a marketplace, its brand, title and Amazon's dangerous-goods attributes.
- Product Pricing (getItemOffers): how many offers, FBA or seller-fulfilled, where each ships from, the buy box.
- FBA Inventory and Sales (the Product board): units left and units sold a day.

Keys: one Login with Amazon app (client id and secret) and a refresh token per region (North America for the USA,
Europe for the UAE, Far East for Australia), kept encrypted by vault.py. The access token lives in memory only.
No AWS signing is needed. Each operation keeps Amazon's published rate (requests a second); a 429 waits and tries
again. Errors are plain sentences and never contain a key or token. Without keys every caller reads pages instead.
"""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import config

TOKEN_URL = 'https://api.amazon.com/auth/o2/token'
ENDPOINTS = {'NA': 'https://sellingpartnerapi-na.amazon.com', 'EU': 'https://sellingpartnerapi-eu.amazon.com',
             'FE': 'https://sellingpartnerapi-fe.amazon.com'}
REGION_NAMES = {'NA': 'North America (USA)', 'EU': 'Europe (UAE)', 'FE': 'Far East (Australia)'}
MARKETPLACES = {'US': ('NA', 'ATVPDKIKX0DER'), 'AE': ('EU', 'A2VIGQ35RCS4UG'), 'AU': ('FE', 'A39IBJ37TRP1C6')}
RATES = {                                   # operation: (requests a second, burst)
    'getCatalogItem': (2.0, 2), 'getItemOffers': (0.5, 1), 'getMarketplaceParticipations': (0.016, 15),
    'getInventorySummaries': (2.0, 2), 'getOrderMetrics': (0.5, 15),
}
TIMEOUT = 30
RETRIES = 3
DG_KEYS = ('supplier_declared_dg_hz_regulation', 'hazmat', 'ghs', 'safety_data_sheet_url', 'is_hazmat')


class SpApiError(Exception):
    """The API refused or failed; the message is meant for you and never contains a key or token."""


def _http(method, url, headers, body=None, timeout=TIMEOUT):
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read(5_000_000)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read(200_000)
    except OSError as e:                                # the connection dropped, timed out or could not be made
        raise SpApiError('Amazon could not be reached (%s).' % (str(e).splitlines()[0][:100] or e.__class__.__name__)) from None


def clean_creds(d):
    """Keys typed in Settings -> what is kept: {'client_id', 'client_secret', 'refresh': {'NA', 'EU', 'FE'}}.
    Raises SpApiError when something needed is missing."""
    d = d or {}
    out = {'client_id': str(d.get('client_id') or '').strip(), 'client_secret': str(d.get('client_secret') or '').strip(),
           'refresh': {r: str((d.get('refresh') or {}).get(r) or '').strip() for r in ENDPOINTS}}
    out['refresh'] = {r: t for r, t in out['refresh'].items() if t}
    if not out['client_id'].startswith('amzn1.application-oa2-client.'):
        raise SpApiError('The client ID should start with amzn1.application-oa2-client.')
    if len(out['client_secret']) < 20:
        raise SpApiError('That does not look like the client secret.')
    if not out['refresh']:
        raise SpApiError('Add the refresh token of at least one region.')
    for t in out['refresh'].values():
        if not t.startswith('Atzr|') or len(t) > 2000:
            raise SpApiError('A refresh token should start with Atzr|')
    return out


class SpApi:
    def __init__(self, creds, http=None, clock=time.time, sleep=time.sleep):
        self.creds = creds or {}
        self.http = http or _http
        self.clock, self.sleep = clock, sleep
        self._tokens = {}                                    # region -> (access token, expires at)
        self._next = {}                                      # operation -> earliest next call
        self._lock = threading.Lock()
        self.calls = {}                                      # operation -> calls made (Health)
        self.last_error = None

    # ---------- which marketplaces ----------
    def markets(self):
        return [m for m, (r, _) in MARKETPLACES.items() if (self.creds.get('refresh') or {}).get(r)]

    def has(self, market):
        return market in self.markets()

    # ---------- plumbing ----------
    def _token(self, region):
        tok = self._tokens.get(region)
        if tok and tok[1] > self.clock() + 60:
            return tok[0]
        refresh = (self.creds.get('refresh') or {}).get(region)
        if not refresh:
            raise SpApiError('No SP-API refresh token for %s.' % REGION_NAMES[region])
        body = urllib.parse.urlencode({'grant_type': 'refresh_token', 'refresh_token': refresh,
                                       'client_id': self.creds.get('client_id', ''),
                                       'client_secret': self.creds.get('client_secret', '')}).encode()
        status, _, data = self.http('POST', TOKEN_URL, {'Content-Type': 'application/x-www-form-urlencoded'}, body)
        if status != 200:
            raise SpApiError('Login with Amazon refused the SP-API keys for %s (HTTP %d): check them in Settings.'
                             % (REGION_NAMES[region], status))
        try:
            d = json.loads(data)
            self._tokens[region] = (d['access_token'], self.clock() + int(d.get('expires_in') or 3600))
        except (ValueError, KeyError):
            raise SpApiError('Login with Amazon sent an answer without a token.') from None
        return self._tokens[region][0]

    def _pace(self, op):
        rate, _ = RATES.get(op, (1.0, 1))
        with self._lock:
            now = self.clock()
            wait = self._next.get(op, 0) - now
            self._next[op] = max(now, self._next.get(op, 0)) + 1.0 / rate
        if wait > 0:
            self.sleep(wait)

    def _get(self, market, path, params, op):
        region, _ = MARKETPLACES[market]
        url = ENDPOINTS[region] + path + ('?' + urllib.parse.urlencode(params, doseq=True) if params else '')
        for attempt in range(RETRIES + 1):
            self._pace(op)
            headers = {'x-amz-access-token': self._token(region), 'accept': 'application/json',
                       'user-agent': 'ProductChecker/%s (Language=Python)' % config.VERSION}
            status, h, data = self.http('GET', url, headers)
            self.calls[op] = self.calls.get(op, 0) + 1
            if status == 200:
                try:
                    return json.loads(data or b'{}')
                except ValueError:
                    raise SpApiError('Amazon sent an unreadable %s answer.' % op) from None
            if status == 404:
                return None
            if status in (429, 500, 502, 503, 504) and attempt < RETRIES:
                self.sleep(min(30.0, 2.0 * (2 ** attempt)))
                continue
            if status == 401:
                self._tokens.pop(region, None)
            self.last_error = '%s HTTP %d' % (op, status)
            if status in (401, 403):
                raise SpApiError('Amazon refused %s (HTTP %d): the SP-API app may lack the role it needs, or its keys '
                                 'are for another region.' % (op, status))
            if status == 429:
                raise SpApiError('Amazon asks to slow down (%s): try again later.' % op)
            raise SpApiError('Amazon SP-API %s failed (HTTP %d).' % (op, status))
        raise SpApiError('Amazon SP-API %s failed.' % op)

    # ---------- operations ----------
    def catalog_item(self, market, asin):
        """The catalog entry of an ASIN in a marketplace, or None when it is not listed there."""
        _, mid = MARKETPLACES[market]
        return self._get(market, '/catalog/2022-04-01/items/%s' % urllib.parse.quote(asin),
                         {'marketplaceIds': mid, 'includedData': 'summaries,attributes,salesRanks'}, 'getCatalogItem')

    def item_offers(self, market, asin):
        """getItemOffers payload (new condition): Summary and up to 20 offers. None when Amazon has no such item."""
        _, mid = MARKETPLACES[market]
        d = self._get(market, '/products/pricing/v0/items/%s/offers' % urllib.parse.quote(asin),
                      {'MarketplaceId': mid, 'ItemCondition': 'New'}, 'getItemOffers')
        return (d or {}).get('payload') if d is not None else None

    def participations(self, region):
        """The marketplaces these keys can use in one region (Settings, Test connection)."""
        market = next(m for m, (r, _) in MARKETPLACES.items() if r == region)
        d = self._get(market, '/sellers/v1/marketplaceParticipations', None, 'getMarketplaceParticipations') or {}
        return [((p.get('marketplace') or {}).get('id'), (p.get('marketplace') or {}).get('name'))
                for p in d.get('payload') or []]

    def inventory(self, market, skus):
        """FBA inventory summaries of your SKUs in one marketplace: {sku: {...}} (Product board)."""
        _, mid = MARKETPLACES[market]
        out = {}
        for i in range(0, len(skus), 50):
            d = self._get(market, '/fba/inventory/v1/summaries',
                          {'details': 'true', 'granularityType': 'Marketplace', 'granularityId': mid,
                           'marketplaceIds': mid, 'sellerSkus': ','.join(skus[i:i + 50])}, 'getInventorySummaries') or {}
            for s in (d.get('payload') or {}).get('inventorySummaries') or []:
                out[s.get('sellerSku')] = s
        return out

    def inventory_by_asin(self, market, max_pages=40):
        """All your FBA stock in one marketplace, by ASIN (parse_inventory), every page of it."""
        _, mid = MARKETPLACES[market]
        rows, token = [], None
        for _ in range(max_pages):
            params = {'details': 'true', 'granularityType': 'Marketplace', 'granularityId': mid, 'marketplaceIds': mid}
            if token:
                params['nextToken'] = token
            d = self._get(market, '/fba/inventory/v1/summaries', params, 'getInventorySummaries') or {}
            rows += (d.get('payload') or {}).get('inventorySummaries') or []
            token = (d.get('pagination') or {}).get('nextToken')
            if not token:
                break
        return parse_inventory(rows)

    def order_metrics(self, market, interval, tz, asin=None, sku=None):
        """Units ordered a day (Sales API getOrderMetrics) for one ASIN or SKU; interval from day_interval()."""
        _, mid = MARKETPLACES[market]
        params = {'marketplaceIds': mid, 'interval': interval, 'granularity': 'Day', 'granularityTimeZone': tz}
        if sku:
            params['sku'] = sku
        elif asin:
            params['asin'] = asin
        return (self._get(market, '/sales/v1/orderMetrics', params, 'getOrderMetrics') or {}).get('payload') or []


# ---------- reading the answers (no I/O) ----------
def parse_inventory(summaries):
    """getInventorySummaries rows -> {asin: {'fulfillable' (ready to sell), 'inbound' (on its way to Amazon), 'reserved',
    'total', 'skus'}}; several SKUs of one ASIN add up."""
    out = {}
    for s in summaries or []:
        a = str(s.get('asin') or '').upper()
        if not a:
            continue
        det = s.get('inventoryDetails') or {}
        o = out.setdefault(a, {'fulfillable': 0, 'inbound': 0, 'reserved': 0, 'total': 0, 'skus': []})
        o['fulfillable'] += int(det.get('fulfillableQuantity') or 0)
        o['inbound'] += sum(int(det.get(k) or 0) for k in ('inboundWorkingQuantity', 'inboundShippedQuantity',
                                                          'inboundReceivingQuantity'))
        o['reserved'] += int((det.get('reservedQuantity') or {}).get('totalReservedQuantity') or 0)
        o['total'] += int(s.get('totalQuantity') or 0)
        if s.get('sellerSku'):
            o['skus'].append(s['sellerSku'])
    return out


def parse_order_metrics(payload):
    """getOrderMetrics (granularity Day) -> [('YYYY-MM-DD', units ordered)], the day taken from each interval's start."""
    out = []
    for m in payload or []:
        start = str(m.get('interval') or '')[:10]
        if len(start) == 10 and m.get('unitCount') is not None:
            out.append((start, int(m['unitCount'])))
    return out


def day_interval(start, end, tz_name):
    """'2026-09-14T00:00:00+10:00--2026-10-14T00:00:00+10:00': whole local days from `start` up to `end`."""
    import datetime as dt
    from zoneinfo import ZoneInfo
    z = ZoneInfo(tz_name)
    return '%s--%s' % tuple(dt.datetime(d.year, d.month, d.day, tzinfo=z).isoformat() for d in (start, end))


def _first(v):
    if isinstance(v, list) and v:
        v = v[0]
    if isinstance(v, dict):
        return v.get('value', v.get('aspect'))
    return v


def catalog_summary(item, market):
    """{'listed', 'title', 'brand', 'rank', 'dg'} from a getCatalogItem answer (None = not listed)."""
    if not item:
        return {'listed': False, 'title': None, 'brand': None, 'rank': None, 'dg': {}}
    _, mid = MARKETPLACES.get(market, (None, None))
    summ = next((s for s in item.get('summaries') or [] if s.get('marketplaceId') == mid), None) or \
        ((item.get('summaries') or [None])[0] or {})
    ranks = next((r for r in item.get('salesRanks') or [] if r.get('marketplaceId') == mid), None) or {}
    top = (ranks.get('displayGroupRanks') or ranks.get('classificationRanks') or [{}])[0]
    attrs = item.get('attributes') or {}
    return {'listed': True, 'title': summ.get('itemName'), 'brand': summ.get('brand') or summ.get('brandName'),
            'rank': top.get('rank'), 'dg': {k: _first(attrs[k]) for k in DG_KEYS if k in attrs}}


def load_client(vault_mod=None, **kw):
    """A client from the saved keys, or None when none are saved (or they cannot be read)."""
    import vault
    v = vault_mod or vault
    try:
        creds = v.load('spapi')
    except v.VaultError:
        return None
    return SpApi(creds, **kw) if creds else None
