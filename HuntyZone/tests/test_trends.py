"""Google Trends: parsers, the pause ladder, and a real headless browser against the fake Trends server."""
import asyncio
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fakeweb  # noqa: E402
import storage  # noqa: E402
import trends  # noqa: E402

CHROMIUM = '/opt/pw-browsers/chromium'


def test_parsers():
    exp = trends.parse_explore(")]}'\n" + json.dumps({'widgets': [
        {'id': 'TIMESERIES', 'token': 'a', 'request': {'userConfig': {'userType': 'USER_TYPE_LEGIT_USER'}}},
        {'id': 'RELATED_QUERIES_0', 'token': 'b', 'request': {}}]}))
    assert exp['timeseries']['token'] == 'a' and exp['related']['token'] == 'b' and exp['user_type'] == 'USER_TYPE_LEGIT_USER'
    ml = trends.parse_multiline(")]}',\n" + json.dumps({'default': {'timelineData': fakeweb.weekly()[:10]}}))
    assert ml['status'] == 'ok' and ml['resolution'] == 'WEEK' and len(ml['points']) == 10
    lt = trends.parse_multiline(json.dumps({'default': {'timelineData': [
        {'time': '1', 'value': [0], 'formattedValue': ['<1']}, {'time': '604801', 'value': [0], 'hasData': [False]}]}}))
    assert lt['status'] == 'ok' and lt['points'][0][1] == 0.5
    zero = trends.parse_multiline(json.dumps({'default': {'timelineData': [{'time': '1', 'value': [0], 'hasData': [False]}]}}))
    assert zero['status'] == 'low_volume'
    assert trends.parse_multiline(")]}'\n{}")['status'] == 'throttled'
    rel = trends.parse_related(json.dumps({'default': {'rankedList': [
        {'rankedKeyword': [{'query': 'a', 'value': 100}]},
        {'rankedKeyword': [{'query': 'b', 'value': 5000, 'formattedValue': 'Breakout'}]}]}}))
    assert rel == [{'kind': 'top', 'query': 'a', 'value': 100, 'breakout': False},
                   {'kind': 'rising', 'query': 'b', 'value': 5000, 'breakout': True}]


def test_pushback_reasons():
    assert trends.pushback(429, '', '')
    assert trends.pushback(200, 'https://www.google.com/sorry/index', '')
    assert trends.pushback(200, 'https://consent.google.com/m', '')
    assert trends.pushback(200, '', 'Our systems have detected unusual traffic')
    assert trends.pushback(200, 'https://trends.google.com/x', ")]}'{}") is None


def test_breaker_ladder():
    t = [1000.0]
    b = trends.Breaker(now=lambda: t[0])
    assert not b.is_open()
    assert b.trip('x') == 1000 + 3600 and b.is_open()
    t[0] += 3601
    assert not b.is_open()
    assert b.trip('x') == t[0] + 7200
    b.trip('x')
    b.trip('x')
    assert b.trip('x') == t[0] + 24 * 3600                   # stays at the top of the ladder
    b.ok()
    assert not b.is_open() and b.streak == 0
    b2 = trends.Breaker(now=lambda: t[0])
    b2.load(b.state())
    assert b2.streak == 0


def test_compute_labels_and_low_volume():
    pts = [[p['time'], p['value'][0], p.get('isPartial', False)] for p in fakeweb.weekly('growing')]
    pts = [[int(a), b, c] for a, b, c in pts]
    data = trends.compute([{'status': 'ok', 'points': pts, 'resolution': 'WEEK', 'fetched_at': 1}])
    assert data['status'] == 'ok' and data['label'] in ('Growing', 'Emerging', 'Breakout') and data['score'] > 50
    assert data['monthly'] and data['monthly'][0][0] >= '2022-01' or data['monthly'][0][0] >= '2021-10'
    low = trends.compute([{'status': 'low_volume', 'points': [], 'fetched_at': 1}])
    assert low['status'] == 'low_volume' and 'Not enough' in low['why'][0]
    assert trends.compute([{'status': 'throttled'}])['status'] == 'none'


class StubChecker:
    """Just enough of amazon_check.Checker: a headless browser context."""

    def __init__(self):
        self.ctx = self.pw = self.browser = None

    async def start(self):
        if not self.ctx:
            from playwright.async_api import async_playwright
            self.pw = await async_playwright().start()
            self.browser = await self.pw.chromium.launch(executable_path=CHROMIUM if os.path.exists(CHROMIUM) else None)
            self.ctx = await self.browser.new_context()

    async def close(self):
        await self.browser.close()
        await self.pw.stop()


def mode(base, **m):
    req = urllib.request.Request(base + '/__mode', data=json.dumps(m).encode(), method='POST')
    urllib.request.urlopen(req).read()


@pytest.fixture(scope='module')
def web():
    try:
        import playwright  # noqa: F401
    except ImportError:
        pytest.skip('playwright not installed')
    srv, base = fakeweb.start()
    yield base
    srv.shutdown()


def test_browser_lane_against_the_fake_server(web, tmp_path):
    db = storage.DB(tmp_path / 'm.db').open()
    said = []

    async def go():
        chk = StubChecker()
        tr = trends.BrowserTrends(chk, say=said.append, gap=(0, 0), base=web)
        try:
            mode(web, trends='ok')
            data = await trends.refresh(db, tr, 'tower fan', 'US', with_related=True)
            assert data['status'] == 'ok' and data['seasonality']['peak_month'] in (6, 7, 8)
            assert db.read(storage.trends_related, 'tower fan', 'US')[0]['query']
            n_hits = len(fakeweb.HITS)
            again = await trends.refresh(db, tr, 'tower fan', 'US')          # fresh for 7 days: no new request
            assert again['label'] == data['label'] and len(fakeweb.HITS) == n_hits
            mode(web, trends='lowvol')
            low = await trends.refresh(db, tr, 'rare thing', 'AE')
            assert low['status'] == 'low_volume'
            for m in ('429', 'empty', 'scraper', 'sorry'):
                tr.breaker.ok()
                mode(web, trends=m)
                with pytest.raises(trends.TrendsError) as e:
                    await trends.refresh(db, tr, 'some term %s' % m, 'AU')
                assert e.value.status == 'throttled', m
                assert tr.breaker.is_open()
            with pytest.raises(trends.TrendsError) as e:                     # paused: no request at all
                n_hits = len(fakeweb.HITS)
                await trends.refresh(db, tr, 'another', 'AU')
            assert e.value.status == 'paused' and len(fakeweb.HITS) == n_hits
            gaps = db.all("SELECT COUNT(*) AS n FROM trends_sample WHERE status='throttled'")[0]['n']
            assert gaps == 4 and said
        finally:
            await tr.close()
            await chk.close()
    try:
        asyncio.run(go())
    finally:
        db.close()
        mode(web, trends='ok')
