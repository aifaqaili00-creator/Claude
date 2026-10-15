"""Phase 2 glue: task specs, after-run alerts (deduped, gaps ignored), radar, watchlist, home and health payloads."""
import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import analysis  # noqa: E402
import collectors  # noqa: E402
import config  # noqa: E402
import reports  # noqa: E402
import scheduler  # noqa: E402
import storage  # noqa: E402
import throttle as th  # noqa: E402
import trends  # noqa: E402
from test_storage import ROWS, row, summary  # noqa: E402

NOW = time.time()
DAY = 86400


class FakeS:
    def __init__(self, db):
        self.db = db
        self.settings = config.defaults()
        self.throttle = th.Throttle(th.Store(db))
        self.trends = trends.BrowserTrends(None)
        self.scheduler = scheduler.Scheduler(db, {})
        self.checker = type('C', (), {'state': 'ready', 'error': ''})()
        self.focused = False

    def window_focused(self):
        return self.focused


@pytest.fixture
def S(tmp_path):
    d = storage.DB(tmp_path / 'm.db').open()
    yield FakeS(d)
    d.close()


def test_task_specs_follow_the_watchlist_and_settings():
    s = config.defaults()
    w = [{'kind': 'keyword', 'market': 'AU', 'target': 'moving bags', 'cadence_h': 24, 'enabled': 1},
         {'kind': 'seed', 'market': 'US', 'target': 'tower fan', 'enabled': 1},
         {'kind': 'keyword', 'market': 'US', 'target': 'off', 'enabled': 0}]
    specs = collectors.task_specs(s, w)
    kinds = sorted((x['kind'], x['market']) for x in specs)
    assert ('watch_kw', 'AU') in kinds and ('trends', 'AU') in kinds and ('suggest', 'US') in kinds
    assert sum(1 for k in kinds if k[0] == 'lists') == 3 and ('watch_kw', 'US') not in kinds
    assert collectors.task_specs({**s, 'auto_refresh': False}, w) == []
    assert not any(x['kind'] == 'trends' for x in collectors.task_specs({**s, 'trends_enabled': False}, w))
    assert sum(1 for k in kinds if k[0] == 'qualify') == 3
    assert not any(x['kind'] == 'qualify' for x in collectors.task_specs({**s, 'auto_qualify': False}, w))


def gap_history(S, market='AU', kw='moving bags'):
    """Ten valid daily checks with 8 fast sellers, then one with only 2 (a gap opens), plus a captcha gap."""
    many = [dict(r) for r in ROWS]
    fast = [row('F%d' % i, 'FREE delivery Tomorrow, 10 Oct', bought=100) for i in range(8)]
    for i in range(10):
        S.db.write(storage.save_serp, summary(rows=many + fast, kw=kw, at=NOW - (11 - i) * DAY), 'schedule', None, 1, '2000')
    S.db.write(storage.save_serp, summary(rows=many, kw=kw, at=NOW - 3600), 'schedule', None, 1, '2000')
    S.db.write(storage.save_serp, {'market': market, 'keyword': kw, 'status': 'captcha', 'error': 'x', 'checked_at': NOW - 60})
    S.db.write(storage.add_watch, 'keyword', market, kw)


def test_after_run_alerts_are_stored_once(S):
    gap_history(S)
    fresh = analysis.run(S, [], now=NOW)
    rules = {a['rule'] for a in fresh}
    assert 'gap_open' in rules
    assert analysis.run(S, [], now=NOW) == []                       # same week: nothing new
    stored = S.db.read(storage.list_alerts)
    assert len(stored) == len(fresh)


def test_blocked_checks_alone_raise_no_market_alerts(S):
    for i in range(5):
        S.db.write(storage.save_serp, {'market': 'AU', 'keyword': 'x', 'status': 'blocked', 'error': 'dog',
                                       'checked_at': NOW - i * DAY})
    S.db.write(storage.add_watch, 'keyword', 'AU', 'x')
    fresh = analysis.run(S, [], now=NOW)
    assert not [a for a in fresh if a['rule'] in ('gap_open', 'demand_up', 'demand_down', 'gap_closing')]


def test_toast_digest_respects_focus(S, monkeypatch):
    import notify
    sent = []
    monkeypatch.setattr(notify, 'toast', lambda t, b, u=None: sent.append((t, b)) or True)
    monkeypatch.setattr(time, 'localtime', lambda *a: type('T', (), {'tm_hour': 12})())
    a = [{'title': 'Gap opened', 'severity': 3, 'dedupe_key': 'k1', 'rule': 'gap_open'}]
    S.db.write(storage.add_alerts, a)
    S.focused = True
    assert not analysis.notify_new(S, a) and not sent
    S.focused = False
    assert analysis.notify_new(S, a) and sent[0][0] == '1 new market alert'
    assert S.db.one('SELECT toasted FROM alert')['toasted'] == 1


def list_history(S, market='US'):
    """Seven daily scans: a fan climbs and stays, a deal product appears once, others churn."""
    for d in range(7):
        items = [{'asin': 'FAN%d' % k, 'title': 'Bladeless Tower Fan Quiet %d' % k, 'rank_now': 500 - d * 60 - k,
                  'rank_before': 2000, 'pct': 300, 'price': 59.0, 'reviews': 40} for k in range(3)]
        items += [{'asin': 'X%d_%d' % (d, k), 'title': 'Random thing %d %d' % (d, k), 'rank_now': 900, 'rank_before': 1000,
                   'pct': 10, 'price': 20.0} for k in range(40)]
        if d == 6:
            items.append({'asin': 'DEAL', 'title': 'Phone Case Deal', 'deal': True, 'rank_now': 50, 'rank_before': 5000,
                          'pct': 9000, 'price': 5.0})
        S.db.write(storage.save_list, market, 'movers', 'home-garden', 'Home & Kitchen', items, 'ok', None, NOW - (6 - d) * DAY)


def test_radar_ranks_persistent_climbers_and_groups_them(S):
    list_history(S)
    r = reports.radar(S.db, 'US', now=NOW)
    json.dumps(r, allow_nan=False)
    assert r['scans'] == 7 and r['items']
    fans = {'FAN0', 'FAN1', 'FAN2'}
    top = r['items'][0]                                                     # one row for the group, the others inside
    assert top['asin'] in fans and {m['asin'] for m in top['members']} == fans - {top['asin']} and top['similar'] == 2
    assert not fans - {top['asin']} & {i['asin'] for i in r['items']}
    assert r['items'][0]['label'] in ('Rising', 'New on the lists') and len(r['items'][0]['spark']) >= 3
    deal = next(i for i in reports.radar(S.db, 'US', now=NOW, limit=1000)['items'] if i['asin'] == 'DEAL')
    assert deal['label'] == 'Deal-driven' and deal['deal']
    assert any(set(c['asins']) >= {'FAN0', 'FAN1', 'FAN2'} for c in r['clusters'])
    assert reports.radar(S.db, 'AE', now=NOW)['items'] == []


def test_list_alerts_fire_for_the_climbers(S):
    list_history(S)
    fresh = analysis.run(S, [], now=NOW)
    assert any(a['rule'] in ('persistent_riser', 'cluster_surge', 'new_mover') for a in fresh)


def test_watchlist_home_and_health(S):
    gap_history(S)
    analysis.run(S, [], now=NOW)
    wl = reports.watchlist(S.db, S.settings, now=NOW)
    json.dumps(wl, allow_nan=False)
    w = wl[0]
    assert w['fast'] == 2 and w['fast_change'] == -8 and len(w['spark']) == 11 and w['last_status'] == 'captcha'
    h = reports.home(S.db, S, now=NOW)
    json.dumps(h, allow_nan=False)
    assert h['watch_n'] == 1 and h['gaps'][0]['kw'] == 'moving bags' and h['alerts']
    assert h['checklist']['watching'] and not h['checklist']['imported']
    he = reports.health(S.db, S, now=NOW)
    json.dumps(he, allow_nan=False)
    assert 'amazon:AU' in he['sources'] and he['counts']['serp_snapshot'] == 12


def test_search_trend_lanes(S):
    S.db.write(storage.save_trends_feature, 'tower fan', 'US', {'label': 'Growing, seasonal peak Jul', 'score': 72})
    S.db.write(storage.save_trends_feature, 'fidget spinner', 'US', {'label': 'Fad', 'score': 12})
    lanes = reports.search_trends(S.db)
    assert lanes['Rising'][0]['term'] == 'tower fan' and lanes['Fading'][0]['term'] == 'fidget spinner'


def test_collector_watch_kw_saves_gaps_and_reraises(S):
    import amazon_check as ac
    saved = []

    class Checker:
        async def check(self, *a, **k):
            raise ac.CaptchaRequired('captcha')

    async def save_check(s, run_id, pages, source='user'):
        saved.append(s)
    S.checker = Checker()
    S.days = lambda: (3, 9, 1)
    S.save_check = save_check
    c = collectors.Collectors(S)
    with pytest.raises(ac.CaptchaRequired):
        asyncio.run(c.watch_kw({'kind': 'watch_kw', 'market': 'AU', 'target': 'moving bags'}))
    assert saved[0]['status'] == 'captcha'


def test_radar_keeps_your_price_range_and_drops_phones(S):
    list_history(S)
    r = reports.radar(S.db, 'US', now=NOW, limit=1000, price_range=(20, 50), exclude_tech=True)
    shown = lambda r: {i['asin'] for i in r['items']} | {m['asin'] for i in r['items'] for m in i['members']}  # noqa: E731
    assert not shown(r) & {'FAN0', 'FAN1', 'FAN2', 'DEAL'}                 # fans at 59 are outside, the phone case is tech
    assert r['filtered']['out_of_range'] == 3 and r['filtered']['tech'] == 1 and r['price_range'] == (20, 50)
    wide = reports.radar(S.db, 'US', now=NOW, limit=1000, price_range=(20, 60))
    assert {'FAN0', 'DEAL'} - shown(wide) == {'DEAL'}                       # DEAL at 5 is below the range


# ---- qualifying: what the radar's top products miss ----

def fake_sources(S, monkeypatch, blocked=None, page=None, trends_error=None):
    """Amazon suggestions, Google Trends, searches and product pages that record each call. blocked {market: error}."""
    import amazon_check as ac
    import ideas
    import product
    calls = []

    async def complete(checker, code, prefix):
        calls.append(('complete', code, prefix))
        return ['tower fan', '', 'tower fans for bedroom'] if code == 'US' else [prefix + ' quiet']

    async def refresh_trends(term, geo, progress=None, force=False, related=True):
        calls.append(('trends', geo, term, related))
        if trends_error:
            raise trends_error
        return {'status': 'ok'}

    class Checker:
        async def check(self, kw, code, *a):
            calls.append(('search', code, kw))
            if code in (blocked or {}):
                raise blocked[code]
            return ac.summarize(code, kw, ac.MARKETS[code]['expect'][0], [dict(r) for r in ROWS], 3, 9, time.time())

    async def read_product(checker, asin, market):
        calls.append(('page', market, asin))
        if page:
            raise page
        return {'status': 'ok', 'title': 'Bladeless Tower Fan Quiet', 'reviews': 40}

    async def save_check(s, run_id, pages, source='user'):
        S.db.write(storage.save_serp, s, source, run_id, pages, None)

    async def refit_curve(market):
        calls.append(('refit', market))
    monkeypatch.setattr(ideas, 'complete', complete)
    monkeypatch.setattr(product, 'read_product', read_product)
    S.checker, S.refresh_trends, S.save_check, S.refit_curve = Checker(), refresh_trends, save_check, refit_curve
    S.days = lambda: (3, 9, 1)
    return calls


def test_qualify_reads_each_missing_piece_once(S, monkeypatch):
    list_history(S)
    calls = fake_sources(S, monkeypatch)
    c = collectors.Collectors(S)
    out = asyncio.run(c.qualify_products('US', ['FAN0', 'FAN1', 'NOPE000000'], checks=1, pages=1))
    assert [x for x in calls if x[:2] == ('complete', 'US')] == [('complete', 'US', p) for p in
                                                                 ('bladeless tower', 'tower fan', 'fan quiet')]
    assert [x for x in calls if x[0] == 'trends'] == [('trends', 'US', 'tower fan', True), ('trends', 'AU', 'tower fan', False),
                                                      ('trends', 'AE', 'tower fan', False)]   # one phrase for both fans
    assert {x[1:] for x in calls if x[0] == 'complete' and x[1] != 'US'} == {('AU', 'tower fan'), ('AE', 'tower fan')}
    assert {x[1:] for x in calls if x[0] == 'search'} == {('AU', 'tower fan'), ('AE', 'tower fan')}
    assert [x for x in calls if x[0] in ('page', 'refit')] == [('page', 'US', 'FAN0'), ('refit', 'US')]
    assert out['counts'] == {'suggestions': 3, 'trends': 1, 'abroad': 2, 'readiness': 2, 'searches': 2, 'pages': 1}
    assert out['status'] == 'ok' and out['note'].startswith('2 products')
    calls.clear()
    asyncio.run(c.qualify_products('US', ['FAN0', 'FAN1'], checks=1, pages=1))
    assert [x[0] for x in calls] == ['trends'] * 3 + ['page', 'refit'] and calls[3] == ('page', 'US', 'FAN1')   # the rest is fresh
    fan = reports.radar(S.db, 'US', now=time.time())['items'][0]
    assert fan['search'] == 'tower fan' and fan['phrase_src'] == 'amazon' and fan['gap']['AU']['score'] is not None


def test_qualify_asks_again_weekly_until_the_next_market_knows_the_phrase(S, monkeypatch):
    list_history(S)
    calls = fake_sources(S, monkeypatch)
    old = time.time() - 8 * DAY
    for p in ('bladeless tower', 'tower fan', 'fan quiet'):
        S.db.write(storage.save_suggest, 'US', p, [{'keyword': 'tower fan', 'hits': 1, 'best': 1}], 1, 0, 'ok', None, old)
    S.db.write(storage.save_suggest, 'AU', 'tower fan', [{'keyword': 'tower fans', 'hits': 1, 'best': 1}], 1, 0, 'ok', None, old)
    S.db.write(storage.save_suggest, 'AE', 'tower fan', [], 1, 0, 'empty', None, old)
    asyncio.run(collectors.Collectors(S).qualify_products('US', ['FAN0'], pages=0))
    assert [x for x in calls if x[0] == 'complete'] == [('complete', 'AE', 'tower fan')]    # Australia knows it already


def test_qualify_stops_only_the_step_that_hit_a_limit(S, monkeypatch):
    import amazon_check as ac
    list_history(S)
    calls = fake_sources(S, monkeypatch, blocked={'AU': ac.CaptchaRequired('captcha')},
                         trends_error=th.QuotaExceeded('google', 'qualify'))
    out = asyncio.run(collectors.Collectors(S).qualify_products('US', ['FAN0'], checks=1))
    assert ('search', 'AE', 'tower fan') in calls and out['counts']['searches'] == 1 and out['counts']['pages'] == 1
    assert out['status'] == 'partial' and 'Google Trends' in out['note'] and 'Amazon Australia' in out['note']
    with pytest.raises(th.Cooldown):                                        # the captcha counts against Australia
        S.throttle.check('amazon:AU', 'qualify')
    S.throttle.check('amazon:US', 'qualify')
    assert S.db.one("SELECT status FROM serp_snapshot WHERE market='AU' ORDER BY id DESC LIMIT 1")['status'] == 'captcha'


def test_a_blocked_product_page_ends_the_automatic_run_only(S, monkeypatch):
    import amazon_check as ac
    list_history(S)
    fake_sources(S, monkeypatch, page=ac.CaptchaRequired('captcha'))
    c = collectors.Collectors(S)
    out = asyncio.run(c.qualify_products('US', ['FAN0']))                  # your own press: the rest is kept, said
    assert out['status'] == 'partial' and 'product pages' in out['note'] and out['counts']['suggestions'] == 3

    async def background():
        ac.BACKGROUND.set(True)
        return await c.qualify_products('US', ['FAN1'])
    with pytest.raises(ac.CaptchaRequired):
        asyncio.run(background())


def test_find_the_underserved_version_searches_longer_phrases_abroad(S, monkeypatch):
    import amazon_check as ac
    import ideas
    calls = []

    async def suggest(checker, code, seed, progress=None):
        calls.append(('suggest', code, seed))
        kws = ['tower fan', 'tower fan with remote', 'small tower fan', 'tower fans for bedroom', 'fan tower', 'tower fan quiet']
        return {'market': code, 'seed': seed, 'n_calls': 26, 'n_failed': 0,
                'keywords': [{'keyword': k, 'hits': 6 - n, 'best': 1} for n, k in enumerate(kws)]}

    async def check_batch(items, progress, refresh=False):
        for it in items:
            calls.append(('search', it['market'], it['search']))
            rows = ROWS[:2] if it['search'] == 'small tower fan' else ROWS
            S.db.write(storage.save_serp, ac.summarize(it['market'], it['search'], ac.MARKETS[it['market']]['expect'][0],
                                                       [dict(r) for r in rows], 3, 9, time.time()), 'user', None, 1, None)
        return {}
    monkeypatch.setattr(ideas, 'suggest', suggest)
    S.check_batch = check_batch
    out = asyncio.run(collectors.Collectors(S).underserved('US', 'Tower Fan', {'trend_score': 70, 'cluster_surge': 60}))
    json.dumps(out, allow_nan=False)
    searches = [c for c in calls if c[0] == 'search']
    assert calls[0] == ('suggest', 'US', 'tower fan') and len(searches) == 8 and {c[1] for c in searches} == {'AU', 'AE'}
    assert {c[2] for c in searches} == {'tower fan with remote', 'small tower fan', 'tower fans for bedroom', 'tower fan quiet'}
    v = out['variants']
    assert len(v) == 4 and all(x['gap']['AU'] and x['gap']['AE'] for x in v) and v[0]['best'] >= v[-1]['best']
    assert out['parent'] == {'AU': None, 'AE': None} and v[0]['hits'] is not None
    assert S.db.read(storage.suggestions, 'US', ['tower fan'])['tower fan']['keywords'][0] == 'tower fan'


def test_the_scheduled_qualify_takes_the_radar_candidates(S, monkeypatch):
    list_history(S)
    seen = []

    async def qualify_products(market, asins, progress=None, checks=0, pages=12, phrases=None):
        seen.append((market, asins, checks, pages))
        return {'status': 'ok', 'note': ''}
    c = collectors.Collectors(S)
    monkeypatch.setattr(c, 'qualify_products', qualify_products)
    asyncio.run(c.qualify({'kind': 'qualify', 'market': 'US', 'target': 'radar'}))
    (market, asins, checks, pages), = seen
    assert market == 'US' and asins and not set(asins) & {'FAN0', 'FAN1', 'FAN2', 'DEAL'}   # electrical and deals: left out
    assert (checks, pages) == (collectors.QUALIFY_CHECKS, collectors.QUALIFY_PAGES)
    assert asyncio.run(c.qualify({'kind': 'qualify', 'market': 'AE', 'target': 'radar'}))['note'] == 'nothing to qualify'
    assert len(seen) == 1 and c.handlers()['qualify'] == c.qualify
    assert scheduler.source_for({'kind': 'qualify', 'market': 'AU'}) == 'amazon:AU'
