"""Scheduler: due order, staggering, catch-up, timeouts, cooldown short-circuit, quotas, pauses, the loop."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import amazon_check as ac  # noqa: E402
import scheduler as sc  # noqa: E402
import storage  # noqa: E402
import throttle as th  # noqa: E402

T0 = 1_791_590_400.0 + 10 * 3600          # 2026-10-10 10:00 UTC


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.t += s
        await asyncio.sleep(0)


@pytest.fixture
def db(tmp_path):
    d = storage.DB(tmp_path / 'm.db').open()
    yield d
    d.close()


def spec(kind, market, target, every=86400, priority=5):
    return {'kind': kind, 'market': market, 'target': target, 'every_s': every, 'priority': priority}


def make(db, handlers, **kw):
    clock = kw.pop('clock', Clock())
    return sc.Scheduler(db, handlers, clock=clock, sleep=clock.sleep, **kw), clock


def test_stagger_is_deterministic_and_spread():
    a = sc.stagger('watch_kw', 'AU', 'moving bags', 86400)
    assert a == sc.stagger('watch_kw', 'AU', 'moving bags', 86400) and 0 <= a < 86400
    offs = {sc.stagger('watch_kw', 'AU', 'kw %d' % i, 3600) for i in range(50)}
    assert len(offs) > 40


def test_sync_tasks_inserts_updates_and_disables(db):
    s, clock = make(db, {})
    s.sync_tasks([spec('watch_kw', 'AU', 'a'), spec('watch_kw', 'US', 'b')])
    rows = s.tasks()
    assert len(rows) == 2 and all(clock() <= r['next_run_at'] < clock() + 3600 for r in rows)
    s.sync_tasks([spec('watch_kw', 'AU', 'a', every=3600, priority=1), spec('lists', 'US', 'all')])
    rows = {(r['kind'], r['target']): r for r in s.tasks(enabled_only=False)}
    assert rows[('watch_kw', 'a')]['every_s'] == 3600 and rows[('watch_kw', 'a')]['priority'] == 1
    assert rows[('watch_kw', 'b')]['enabled'] == 0 and rows[('lists', 'all')]['enabled'] == 1


def test_due_order_priority_then_staleness(db):
    s, clock = make(db, {})
    s.sync_tasks([spec('watch_kw', 'AU', 'old'), spec('watch_kw', 'AU', 'new'), spec('lists', 'US', 'all', priority=1)])
    db.write(lambda c: c.execute("UPDATE task SET next_run_at=? WHERE target='old'", (clock() - 20 * 3600,)))
    db.write(lambda c: c.execute("UPDATE task SET next_run_at=? WHERE target='new'", (clock() - 3600,)))
    db.write(lambda c: c.execute("UPDATE task SET next_run_at=? WHERE kind='lists'", (clock() - 60,)))
    assert [t['target'] for t in s.due(clock())] == ['all', 'old', 'new']


def test_catch_up_after_sleep_runs_stalest_first_capped(db, monkeypatch):
    ran = []

    async def h(task):
        ran.append(task['target'])
        return {'status': 'ok'}
    s, clock = make(db, {'watch_kw': h})
    s.sync_tasks([spec('watch_kw', 'AU', 'k%02d' % i) for i in range(50)])
    for i in range(50):
        db.write(lambda c, i=i: c.execute("UPDATE task SET next_run_at=? WHERE target=?", (clock() - (i + 1) * 3600, 'k%02d' % i)))
    monkeypatch.setattr(sc, 'MAX_UNITS_PER_TICK', 10)
    out = asyncio.run(s.tick())
    assert out['ran'] == 10 and ran == ['k%02d' % i for i in range(49, 39, -1)]
    nxt = db.one("SELECT next_run_at FROM task WHERE target='k49'")['next_run_at']
    assert nxt >= clock() + 86400 - 1


def test_timeouts_and_errors(db, monkeypatch):
    async def slow(task):
        await asyncio.sleep(1)

    async def bad(task):
        raise ValueError('boom')
    monkeypatch.setitem(sc.UNIT_TIMEOUT, 'watch_kw', 0.01)
    s, clock = make(db, {'watch_kw': slow, 'trends': bad})
    s.sync_tasks([spec('watch_kw', 'AU', 'x'), spec('trends', 'AU', 'y', every=7 * 86400)])
    db.write(lambda c: c.execute('UPDATE task SET next_run_at=0'))
    out = asyncio.run(s.tick())
    assert out['failed'] == 2
    rows = {r['kind']: r for r in s.tasks()}
    assert rows['watch_kw']['last_status'] == 'timeout' and rows['watch_kw']['fail_streak'] == 1
    assert rows['trends']['last_status'] == 'error' and rows['trends']['next_run_at'] == int(clock() + 3600)
    assert db.one("SELECT status FROM run")['status'] == 'error'


def test_cooldown_short_circuits_that_source_only(db):
    calls = []

    async def h(task):
        calls.append((task['market'], task['target']))
        if task['market'] == 'AU':
            raise th.Cooldown('amazon:AU', T0 + 1800, 'captcha')
        return {'status': 'ok'}
    s, clock = make(db, {'watch_kw': h})
    s.sync_tasks([spec('watch_kw', 'AU', 'a1'), spec('watch_kw', 'AU', 'a2'), spec('watch_kw', 'US', 'u1')])
    db.write(lambda c: c.execute('UPDATE task SET next_run_at=0'))
    out = asyncio.run(s.tick())
    assert sum(1 for m, _ in calls if m == 'AU') == 1 and ('US', 'u1') in calls
    assert out['skipped'] == 2
    au = [r for r in s.tasks() if r['market'] == 'AU']
    assert all(r['next_run_at'] == int(T0 + 1800) and r['last_status'] == 'cooldown' for r in au)


def test_quota_reschedules_to_the_next_day(db):
    async def h(task):
        raise th.QuotaExceeded('amazon:US', 'watch_kw')
    s, clock = make(db, {'watch_kw': h})
    s.sync_tasks([spec('watch_kw', 'US', 'q')])
    db.write(lambda c: c.execute('UPDATE task SET next_run_at=0'))
    asyncio.run(s.tick())
    r = s.tasks()[0]
    assert r['last_status'] == 'skipped_quota' and sc.next_utc_midnight(T0) <= r['next_run_at'] < sc.next_utc_midnight(T0) + 3600


def test_captcha_reports_to_the_throttle_and_flags_are_set(db):
    seen = {}

    async def h(task):
        seen['bg'], seen['task'] = ac.BACKGROUND.get(), th.TASK.get()
        raise ac.CaptchaRequired('captcha in background')
    clock = Clock()
    thr = th.Throttle(th.MemoryStore(), clock=clock)
    s, _ = make(db, {'watch_kw': h}, throttle=thr, clock=clock)
    s.sync_tasks([spec('watch_kw', 'AU', 'c')])
    db.write(lambda c: c.execute('UPDATE task SET next_run_at=0'))
    out = asyncio.run(s.tick())
    assert out['blocked'] == 1 and seen == {'bg': True, 'task': 'watch_kw'}
    assert thr.store.get('amazon:AU')['cooldown_until'] == int(T0 + 1800)
    assert ac.BACKGROUND.get() is False and th.TASK.get() == 'user'


def test_pauses(db):
    ran = []

    async def h(task):
        ran.append(1)
        return {'status': 'ok'}
    reason = ['on battery']
    s, clock = make(db, {'watch_kw': h}, conditions=lambda: reason[0])
    s.sync_tasks([spec('watch_kw', 'AU', 'p')])
    db.write(lambda c: c.execute('UPDATE task SET next_run_at=0'))
    assert asyncio.run(s.tick()) is None and s.paused == 'on battery' and not ran
    reason[0] = None
    s.pause(3600)
    assert asyncio.run(s.tick()) is None and s.status()['paused'] == 'paused by you'
    s.resume()
    assert asyncio.run(s.tick())['ok'] == 1 and ran


def test_on_run_done_gets_the_run_and_results(db):
    got = {}

    async def h(task):
        return {'status': 'ok', 'note': 'fine'}

    async def done(run_id, results):
        got['run'], got['results'] = run_id, results
    s, _ = make(db, {'watch_kw': h}, on_run_done=done)
    s.sync_tasks([spec('watch_kw', 'AU', 'r')])
    db.write(lambda c: c.execute('UPDATE task SET next_run_at=0'))
    out = asyncio.run(s.tick())
    assert got['run'] == out['run_id'] and got['results'][0]['target'] == 'r'


def test_run_forever_marks_interrupted_runs_wakes_on_poke_and_stops(db):
    ran = []

    async def h(task):
        ran.append(task['target'])
        return {'status': 'ok'}
    db.write(storage.start_run, 'refresh', 'schedule')                 # left over from a crash

    async def go():
        clock = Clock()
        s, _ = make(db, {'watch_kw': h}, clock=clock)

        async def slow_sleep(sec):                                     # a real wait the poke must cut short
            await asyncio.sleep(5)
        s.sleep = slow_sleep
        s.sync_tasks([spec('watch_kw', 'AU', 'w')])
        db.write(lambda c: c.execute('UPDATE task SET next_run_at=0'))
        loop_task = asyncio.create_task(s.run_forever())
        await asyncio.sleep(0.05)
        s.poke()
        for _ in range(100):
            if ran:
                break
            await asyncio.sleep(0.01)
        s.stop()
        await asyncio.wait_for(loop_task, 1)
    asyncio.run(go())
    assert ran == ['w']
    assert db.one("SELECT status FROM run WHERE id=1")['status'] == 'interrupted'


def test_status_and_run_all_now(db):
    s, clock = make(db, {})
    s.sync_tasks([spec('watch_kw', 'AU', 'a'), spec('watch_kw', 'AU', 'b')])
    st = s.status()
    assert st['tasks_total'] == 2 and st['next_due'] >= clock() and not st['running']
    s.run_all_now()
    assert s.status()['due_now'] == 2
