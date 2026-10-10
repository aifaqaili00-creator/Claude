"""market.db: migrations, snapshots, recounting after the purge, history.json import, retention, backups."""
import datetime as dt
import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import amazon_check as ac  # noqa: E402
import storage  # noqa: E402

DAY = 86400
TODAY = dt.date(2026, 10, 9)


def row(asin, delivery, price=20.0, reviews=10, bought=None, prime=False, sponsored=False, intl=False):
    return {'market': 'AU', 'asin': asin, 'title': 'Title ' + asin, 'price': price, 'rating': 4.5, 'reviews': reviews,
            'bought': bought, 'prime': prime, 'sponsored': sponsored, 'delivery': delivery,
            'days': ac.delivery_days(delivery, TODAY), 'intl': intl, 'url': '', 'image': 'http://img/' + asin}


ROWS = [
    row('A1', 'FREE delivery Tomorrow, 10 Oct', bought=1000, reviews=900, price=30),
    row('A2', 'FREE delivery Sat, 11 Oct', bought=100, reviews=40),
    row('A3', 'FREE International delivery 21 Oct - 5 Nov', bought=50, reviews=5),        # intl only from the text
    row('A4', 'FREE delivery on your first order', reviews=0),                              # first-order, no date
    row('A5', 'Get it 28 Oct - 12 Nov', reviews=2000, price=12),                            # slow, abroad
    row('A6', 'FREE delivery Sat, 11 Oct', sponsored=True, bought=200),
    row('A7', '', reviews=3),                                                                # no date shown
]


def summary(rows=ROWS, kw='Moving  Bags', location='Sydney 2000', at=None):
    s = ac.summarize('AU', kw, location, [dict(r) for r in rows], 3, 9, at or time.time())
    return s


@pytest.fixture
def db(tmp_path):
    d = storage.DB(tmp_path / 'market.db').open()
    yield d
    d.close()


def test_fresh_database_has_the_schema(db):
    v = db.one('PRAGMA user_version')
    assert list(v.values())[0] == storage.SCHEMA_VERSION
    names = {r['name'] for r in db.all("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'serp_snapshot', 'serp_item', 'trends_sample', 'h10_row', 'alert', 'watch', 'curve'} <= names
    assert db.one('PRAGMA journal_mode')['journal_mode'] == 'wal'


def test_reopening_keeps_data_and_newer_schema_is_refused(tmp_path):
    d = storage.DB(tmp_path / 'm.db').open()
    d.write(storage.save_serp, summary())
    d.close()
    d = storage.DB(tmp_path / 'm.db').open()
    assert d.one('SELECT COUNT(*) AS n FROM serp_snapshot')['n'] == 1
    d.close()
    con = sqlite3.connect(str(tmp_path / 'm.db'))
    con.execute('PRAGMA user_version=99')
    con.commit()
    con.close()
    with pytest.raises(RuntimeError):
        storage.DB(tmp_path / 'm.db').open()


def test_older_schema_is_backed_up_before_migrating(tmp_path, monkeypatch):
    d = storage.DB(tmp_path / 'm.db').open()
    d.close()
    monkeypatch.setitem(storage.MIGRATIONS, 2, 'CREATE TABLE extra(x INT)')
    monkeypatch.setattr(storage, 'SCHEMA_VERSION', 2)
    d = storage.DB(tmp_path / 'm.db').open()
    assert list((tmp_path / 'backup').glob('pre-migrate-v1-*.db'))
    assert d.one("SELECT name FROM sqlite_master WHERE name='extra'")
    d.close()


def test_snapshot_round_trip_recounts_the_same(db):
    s = summary()
    sid = db.write(storage.save_serp, s, 'user', None, 1, '2000')
    snap = db.one('SELECT * FROM serp_snapshot WHERE id=?', (sid,))
    assert snap['status'] == 'ok' and snap['kw_norm'] == 'moving bags' and snap['organic'] == s['total']
    assert snap['local'] == s['local'] and snap['overseas'] == s['overseas'] and snap['fast'] == s['fast']
    back = db.read(storage.latest_serp, 'AU', 'moving bags', 1, '2000')
    again = ac.summarize('AU', back['keyword'], back['location'], back['rows'], 3, 9, back['checked_at'])
    for k in ('total', 'fast', 'slow', 'local', 'local_other', 'overseas', 'overseas_intl', 'origin_unknown', 'level'):
        assert again[k] == s[k], k
    assert back['keyword'] == 'Moving Bags' and back['rows'][0]['image'] == 'http://img/A1'


def test_recount_is_stable_after_delivery_text_is_purged(db):
    s = summary(at=time.time() - 20 * DAY)
    db.write(storage.save_serp, s, 'user', None, 1, '2000')
    out = db.write(storage.retention)
    assert out['delivery_cleared'] == len(ROWS) - 1                      # the empty text was never stored
    back = db.read(storage.latest_serp, 'AU', 'moving bags')
    assert all(not r['delivery'] for r in back['rows'])
    again = ac.summarize('AU', back['keyword'], back['location'], back['rows'], 3, 9)
    for k in ('fast', 'local', 'overseas', 'overseas_intl', 'origin_unknown'):
        assert again[k] == s[k], k
    a3 = next(r for r in again['rows'] if r['asin'] == 'A3')
    a4 = next(r for r in again['rows'] if r['asin'] == 'A4')
    assert a3['origin'] == 'overseas' and a3['intl'] and a4['origin'] == 'local'


def test_badge_and_price_aggregates(db):
    sid = db.write(storage.save_serp, summary())
    snap = db.one('SELECT * FROM serp_snapshot WHERE id=?', (sid,))
    assert snap['badged'] == 3                                            # the sponsored 200+ is not counted
    assert snap['bought_low_sum'] == 1150
    assert 1150 < snap['bought_mid_sum'] < snap['bought_high_sum']
    assert snap['bought_top'] == 1000 and snap['price_med'] == 20.0
    assert snap['low_review_n'] == 4 and snap['revenue_mid'] > 0


def test_wrong_location_and_errors_are_gaps(db):
    db.write(storage.save_serp, summary(location='Melbourne 3000'), 'user', None, 1, '2000')
    snap = db.one('SELECT status FROM serp_snapshot')
    assert snap['status'] == 'wrong_location'
    assert db.read(storage.latest_serp, 'AU', 'moving bags') is None
    assert db.read(storage.latest_serp, 'AU', 'moving bags', statuses=storage.VALID + ('wrong_location',))
    db.write(storage.save_serp, {'market': 'AU', 'keyword': 'moving bags', 'error': 'Captcha', 'status': 'captcha'})
    assert db.one("SELECT status FROM serp_snapshot WHERE error IS NOT NULL")['status'] == 'captcha'
    assert storage.serp_status({'total': 0, 'location_ok': True}) == 'empty'


def test_cache_respects_location_pages_and_age(db):
    db.write(storage.save_serp, summary(at=time.time() - 3600), 'user', None, 1, '2000')
    assert db.read(storage.latest_serp, 'AU', 'Moving bags!', 1, '2000', 7200)
    assert db.read(storage.latest_serp, 'AU', 'moving bags', 1, '3000', 7200) is None      # postcode changed
    assert db.read(storage.latest_serp, 'AU', 'moving bags', 2, '2000', 7200) is None      # more pages wanted
    assert db.read(storage.latest_serp, 'AU', 'moving bags', 1, '2000', 1800) is None      # too old


def test_history_json_import_is_idempotent(db, tmp_path):
    entries = [{'id': 'x1', 'keyword': 'sauna hat', 'at': 1_790_000_000, 'pages': 1,
                'results': [summary(kw='sauna hat', at=1_790_000_000)]},
               {'id': 'x0', 'keyword': 'moving bags', 'at': 1_780_000_000,
                'results': [summary(at=1_780_000_000), {**summary(at=1_780_000_000), 'market': 'US'}]}]
    hist = tmp_path / 'history.json'
    hist.write_text(json.dumps(entries))
    assert storage.import_history_file(db, hist) == 3
    assert not hist.exists() and (tmp_path / 'history.v5.bak.json').exists()
    hist.write_text(json.dumps(entries))                                  # the same file again
    assert storage.import_history_file(db, hist) == 0
    checks = db.read(storage.recent_checks)
    assert [c['keyword'] for c in checks] == ['sauna hat', 'Moving Bags']
    assert len(checks[1]['snapshots']) == 2
    runs = db.read(storage.run_snapshots, checks[1]['id'])
    assert {r['market'] for r in runs} == {'AU', 'US'} and runs[0]['rows']


def test_broken_history_json_is_left_alone(db, tmp_path):
    hist = tmp_path / 'history.json'
    hist.write_text('{not json')
    assert storage.import_history_file(db, hist) == 0 and hist.exists()


def test_retention_keeps_one_old_snapshot_per_month(db):
    old = time.time() - 200 * DAY
    for i in range(3):
        db.write(storage.save_serp, summary(at=old + i * 3600))
    db.write(storage.save_serp, summary())
    out = db.write(storage.retention)
    assert out['serp_items_dropped'] == 2 * len(ROWS)
    left = db.all('SELECT snapshot_id, COUNT(*) AS n FROM serp_item GROUP BY snapshot_id')
    assert len(left) == 2


def test_concurrent_writers_and_readers(db):
    errors = []

    def writer(n):
        try:
            for _ in range(5):
                db.write(storage.save_serp, summary(kw='kw %d' % n))
        except Exception as e:                       # noqa: BLE001
            errors.append(e)

    def reader():
        try:
            for _ in range(20):
                db.all('SELECT COUNT(*) FROM serp_snapshot')
        except Exception as e:                       # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)] + [threading.Thread(target=reader) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert db.one('SELECT COUNT(*) AS n FROM serp_snapshot')['n'] == 20


def test_a_failing_write_rolls_back(db):
    def bad(conn):
        storage.save_serp(conn, summary())
        raise ValueError('boom')
    with pytest.raises(ValueError):
        db.write(bad)
    assert db.one('SELECT COUNT(*) AS n FROM serp_snapshot')['n'] == 0
    db.write(storage.save_serp, summary())                                   # the writer still works
    assert db.one('SELECT COUNT(*) AS n FROM serp_snapshot')['n'] == 1


def test_awrite_from_an_event_loop(db):
    import asyncio
    sid = asyncio.run(db.awrite(storage.save_serp, summary()))
    assert sid == 1


def test_backups_rotate_and_restore(tmp_path):
    d = storage.DB(tmp_path / 'm.db').open()
    d.write(storage.save_serp, summary())
    base = time.mktime((2026, 10, 1, 12, 0, 0, 0, 0, 0))
    for i in range(4):
        d.routine_backups(now=base + i * DAY * 8)
    assert len(list((tmp_path / 'backup').glob('daily-*.db'))) == 2
    assert len(list((tmp_path / 'backup').glob('weekly-*.db'))) == 2
    assert d.quick_check()
    d.close()
    (tmp_path / 'm.db').write_bytes(b'garbage')
    used = storage.restore_latest_backup(tmp_path / 'm.db')
    assert used is not None and list(tmp_path.glob('m.db.damaged-*'))
    d = storage.DB(tmp_path / 'm.db').open()
    assert d.one('SELECT COUNT(*) AS n FROM serp_snapshot')['n'] == 1
    d.close()


def test_alerts_are_deduplicated(db):
    a = {'rule': 'gap_open', 'market': 'AU', 'target': 'moving bags', 'title': 'Gap', 'dedupe_key': 'gap_open|AU|x|2026-W41',
         'data': {'n': 1}}
    assert db.write(storage.add_alerts, [a, a]) == 1
    rows = db.read(storage.list_alerts)
    assert rows[0]['data'] == {'n': 1}
    db.write(storage.mark_alerts, [rows[0]['id']], 'dismissed_at')
    assert db.read(storage.list_alerts) == []
    with pytest.raises(ValueError):
        db.write(storage.mark_alerts, [1], 'title')


def test_watchlist(db):
    wid = db.write(storage.add_watch, 'keyword', 'AU', 'Moving Bags!')
    assert db.write(storage.add_watch, 'keyword', 'AU', 'moving bags') == wid
    db.write(storage.update_watch, wid, stage='sourcing', notes='ask 3 suppliers', bogus=1)
    w = db.read(storage.watches)[0]
    assert w['stage'] == 'sourcing' and w['target'] == 'moving bags' and w['notes'] == 'ask 3 suppliers'
    with pytest.raises(ValueError):
        db.write(storage.update_watch, wid, stage='flying')


def test_imports_are_deduplicated_by_file_hash(db):
    rows = [{'asin': 'B01', 'title': 'x', 'price': 30, 'sales_asin': 100, 'revenue': 3000, 'bsr': 1200}]
    iid = db.write(storage.save_import, 'xray', 'AU', 'moving bags', 'a.csv', 'abc', rows, time.time() - 5 * DAY,
                   [{'keyword': 'moving bags', 'volume': 5400}])
    assert iid and db.write(storage.save_import, 'xray', 'AU', 'moving bags', 'a.csv', 'abc', rows, time.time()) is None
    assert db.read(storage.h10_rows, iid)[0]['sales_asin'] == 100
    assert db.read(storage.kw_volume, 'AU', 'Moving Bags')[0]['volume'] == 5400


def test_runs_cut_off_by_a_crash_are_marked(db):
    db.write(storage.start_run, 'refresh', 'schedule')
    assert db.write(storage.interrupt_stale_runs) == 1
    assert db.one('SELECT status FROM run')['status'] == 'interrupted'


def test_trends_samples_and_features(db):
    db.write(storage.save_trends_sample, 'Tower Fan', 'US', 'today 5-y', 'ok', [[1, 50, False]], 'WEEK')
    db.write(storage.save_trends_sample, 'tower fan', 'US', 'today 5-y', 'throttled')
    s = db.read(storage.trends_samples, 'tower fan', 'US')
    assert [x['status'] for x in s] == ['ok', 'throttled'] and s[0]['points'] == [[1, 50, False]]
    db.write(storage.save_trends_feature, 'tower fan', 'US', {'label': 'Growing', 'score': 71.5})
    assert db.read(storage.trends_feature, 'tower fan', 'US')['label'] == 'Growing'
    db.write(storage.save_trends_related, 'tower fan', 'US', 'today 12-m',
             [{'kind': 'rising', 'query': 'bladeless tower fan', 'value': 250, 'breakout': False}])
    assert db.read(storage.trends_related, 'tower fan', 'US')[0]['query'] == 'bladeless tower fan'


def test_closed_database_refuses_writes(tmp_path):
    d = storage.DB(tmp_path / 'm.db').open()
    d.close()
    with pytest.raises(RuntimeError):
        d.write(storage.save_serp, summary())
