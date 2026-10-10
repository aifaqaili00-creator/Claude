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
    top = [i['asin'] for i in r['items'][:3]]
    assert set(top) == {'FAN0', 'FAN1', 'FAN2'}
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
