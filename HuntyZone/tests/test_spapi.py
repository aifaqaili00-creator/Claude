"""Amazon Selling Partner API client: keys, the token, pacing, retries, readable errors, and reading the answers."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import spapi  # noqa: E402

CREDS = {'client_id': 'amzn1.application-oa2-client.abc123', 'client_secret': 's' * 40,
         'refresh': {'FE': 'Atzr|refresh-for-australia', 'NA': 'Atzr|refresh-for-usa'}}


class Fake:
    """Records requests; answers from a queue of (status, body) per URL part."""
    def __init__(self, answers=None):
        self.calls, self.answers = [], answers or {}

    def __call__(self, method, url, headers, body=None, timeout=None):
        self.calls.append((method, url, headers, body))
        if url == spapi.TOKEN_URL:
            return 200, {}, json.dumps({'access_token': 'Atza|token-%d' % len(self.calls), 'expires_in': 3600}).encode()
        for part, queue in self.answers.items():
            if part in url:
                status, data = queue.pop(0) if len(queue) > 1 else queue[0]
                return status, {}, json.dumps(data).encode()
        return 404, {}, b'{}'


class Clock:
    def __init__(self):
        self.t, self.slept = 1000.0, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(round(s, 2))
        self.t += s


def client(answers=None):
    clock = Clock()
    http = Fake(answers)
    return spapi.SpApi(CREDS, http=http, clock=clock, sleep=clock.sleep), http, clock


def test_clean_creds_checks_what_was_typed():
    good = spapi.clean_creds({**CREDS, 'refresh': {'FE': ' Atzr|x ', 'EU': ''}})
    assert good['refresh'] == {'FE': 'Atzr|x'}
    for bad, msg in (({**CREDS, 'client_id': 'abc'}, 'client ID'), ({**CREDS, 'client_secret': 'short'}, 'client secret'),
                     ({**CREDS, 'refresh': {}}, 'at least one region'), ({**CREDS, 'refresh': {'NA': 'token'}}, 'Atzr|')):
        with pytest.raises(spapi.SpApiError, match=msg.replace('|', r'\|')):
            spapi.clean_creds(bad)


def test_markets_follow_the_regions_with_a_token():
    c, _, _ = client()
    assert c.markets() == ['US', 'AU'] and c.has('AU') and not c.has('AE')


def test_the_token_is_fetched_once_and_sent_with_each_call():
    c, http, clock = client({'/catalog/': [(200, {'asin': 'B0X'})]})
    assert c.catalog_item('AU', 'B0X') == {'asin': 'B0X'}
    clock.t += 10
    c.catalog_item('AU', 'B0X')
    tokens = [x for x in http.calls if x[1] == spapi.TOKEN_URL]
    assert len(tokens) == 1 and b'refresh_token=Atzr%7Crefresh-for-australia' in tokens[0][3]
    api = [x for x in http.calls if x[1] != spapi.TOKEN_URL]
    assert api[0][1].startswith(spapi.ENDPOINTS['FE'] + '/catalog/2022-04-01/items/B0X?marketplaceIds=A39IBJ37TRP1C6')
    assert api[0][2]['x-amz-access-token'] == 'Atza|token-1' and c.calls == {'getCatalogItem': 2}
    clock.t += 4000                                                           # expired: a new token
    c.catalog_item('AU', 'B0X')
    assert len([x for x in http.calls if x[1] == spapi.TOKEN_URL]) == 2


def test_calls_keep_amazons_rate_and_retry_when_asked_to_slow_down():
    c, http, clock = client({'/offers': [(429, {}), (200, {'payload': {'Offers': []}})]})
    assert c.item_offers('AU', 'B0X') == {'Offers': []}
    assert 2.0 in clock.slept                                                 # the 429 backoff
    c.item_offers('AU', 'B0X')
    assert c.calls['getItemOffers'] == 3 and max(clock.slept) >= 2.0         # 0.5 a second


def test_not_listed_is_none_and_errors_are_plain_sentences_without_keys():
    c, _, _ = client({'/catalog/': [(404, {})], '/offers': [(403, {'errors': [{'message': 'Unauthorized'}]})]})
    assert c.catalog_item('AU', 'B0MISSING') is None
    with pytest.raises(spapi.SpApiError) as e:
        c.item_offers('AU', 'B0X')
    assert 'HTTP 403' in str(e.value) and 'Atz' not in str(e.value) and c.last_error == 'getItemOffers HTTP 403'
    with pytest.raises(spapi.SpApiError, match='No SP-API refresh token'):
        c.catalog_item('AE', 'B0X')


def test_a_refused_login_says_so():
    c = spapi.SpApi(CREDS, http=lambda *a, **k: (400, {}, b'{"error":"invalid_grant"}'))
    with pytest.raises(spapi.SpApiError, match='refused the SP-API keys for Far East'):
        c.catalog_item('AU', 'B0X')


def test_catalog_summary_reads_title_brand_rank_and_dangerous_goods():
    item = {'summaries': [{'marketplaceId': 'ATVPDKIKX0DER', 'itemName': 'US name'},
                          {'marketplaceId': 'A39IBJ37TRP1C6', 'itemName': 'Oven Cleaner', 'brand': 'Glisten'}],
            'salesRanks': [{'marketplaceId': 'A39IBJ37TRP1C6', 'displayGroupRanks': [{'rank': 312}]}],
            'attributes': {'supplier_declared_dg_hz_regulation': [{'value': 'ghs'}], 'color': [{'value': 'red'}]}}
    s = spapi.catalog_summary(item, 'AU')
    assert s == {'listed': True, 'title': 'Oven Cleaner', 'brand': 'Glisten', 'rank': 312,
                 'dg': {'supplier_declared_dg_hz_regulation': 'ghs'}}
    assert spapi.catalog_summary(None, 'AU')['listed'] is False


def test_load_client_from_the_vault():
    class V:
        VaultError = type('VaultError', (Exception,), {})
        saved = None

        @classmethod
        def load(cls, name):
            if cls.saved == 'broken':
                raise cls.VaultError('cannot read')
            return cls.saved
    assert spapi.load_client(V) is None
    V.saved = CREDS
    assert spapi.load_client(V).markets() == ['US', 'AU']
    V.saved = 'broken'
    assert spapi.load_client(V) is None


def test_participations_lists_the_marketplaces_of_a_region():
    c, _, _ = client({'/sellers/v1/marketplaceParticipations': [(200, {'payload': [
        {'marketplace': {'id': 'A39IBJ37TRP1C6', 'name': 'Amazon.com.au'}}]})]})
    assert c.participations('FE') == [('A39IBJ37TRP1C6', 'Amazon.com.au')]
