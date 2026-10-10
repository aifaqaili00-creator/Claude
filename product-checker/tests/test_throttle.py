"""Throttle: gaps, concurrency, cooldown ladder, quotas, persistence and the user-first gate."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import storage  # noqa: E402
import throttle as th  # noqa: E402

DAY0 = 1_791_590_400.0          # 2026-10-10 00:00 UTC


class Clock:
    def __init__(self, t=DAY0 + 3600):
        self.t = t
        self.sleeps = []

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.sleeps.append(s)
        self.t += s
        await asyncio.sleep(0)


def make(store=None, gate=None, clock=None):
    clock = clock or Clock()
    return th.Throttle(store or th.MemoryStore(), gate, clock, clock.sleep, rand=lambda a, b: (a + b) / 2), clock


def test_gaps_between_starts():
    t, clock = make()

    async def go():
        starts = []
        for _ in range(3):
            async with t.slot('amazon:AU'):
                starts.append(clock())
        return starts
    s = asyncio.run(go())
    assert s[1] - s[0] == pytest.approx(3.5) and s[2] - s[1] == pytest.approx(3.5)


def test_concurrency_cap():
    t, clock = make()
    inside, peak = [0], [0]

    async def one():
        async with t.slot('amazon:US'):
            inside[0] += 1
            peak[0] = max(peak[0], inside[0])
            await clock.sleep(10)
            inside[0] -= 1

    async def go():
        await asyncio.gather(one(), one(), one())
    asyncio.run(go())
    assert peak[0] == 2


def test_cooldown_ladder_and_reset():
    t, clock = make()
    expected = th.LADDERS['captcha']
    for i in range(5):
        until = t.report('amazon:AU', 'captcha')
        assert until == int(clock() + expected[min(i, 3)])
    with pytest.raises(th.Cooldown):
        t.check('amazon:AU', 'user')
    t.report('amazon:AU', 'ok')
    assert t.store.get('amazon:AU')['fail_streak'] == 0
    clock.t += 30000
    t.check('amazon:AU', 'user')                     # cooldown over
    assert t.report('amazon:AU', 'captcha') == int(clock() + expected[0])
    assert t.report('amazon:AU', 'something odd') == 0


def test_cooldown_blocks_user_and_background():
    t, clock = make()
    t.report('google', 'throttled')

    async def go(task):
        async with t.slot('google', task):
            pass
    for task in ('user', 'trends'):
        with pytest.raises(th.Cooldown):
            asyncio.run(go(task))
    t.clear('google')
    asyncio.run(go('trends'))


def test_quota_and_skips_and_new_day():
    t, clock = make()

    async def go(task):
        async with t.slot('amazon:US', task):
            pass
    for _ in range(30):
        asyncio.run(go('watch_kw'))
    with pytest.raises(th.QuotaExceeded):
        asyncio.run(go('watch_kw'))
    for _ in range(5):
        asyncio.run(go('user'))                       # never limited
    day = th.utc_day(clock())
    rows = {r['task']: r for r in t.store.day_rows(day)}
    assert rows['watch_kw']['used'] == 30 and rows['watch_kw']['skipped'] == 1 and rows['user']['used'] == 5
    assert t.remaining('amazon:US', 'watch_kw') == 0 and t.remaining('amazon:US', 'user') is None
    clock.t = DAY0 + 86400 + 60                       # next UTC day
    asyncio.run(go('watch_kw'))


def test_budget_factor_halves_the_quota():
    t, clock = make()
    t.store.put('amazon:AU', budget_factor=0.5)
    assert t.remaining('amazon:AU', 'lists') == 8


def test_state_survives_a_restart(tmp_path):
    db = storage.DB(tmp_path / 'm.db').open()
    try:
        t, clock = make(th.Store(db))
        t.report('amazon:AE', 'blocked', 'dog page')

        async def go():
            async with t.slot('amazon:US', 'lists'):
                pass
        asyncio.run(go())
        t2, _ = make(th.Store(db), clock=clock)
        with pytest.raises(th.Cooldown) as e:
            t2.check('amazon:AE')
        assert 'dog page' in e.value.reason
        st = t2.state()
        assert st['amazon:US']['tasks']['lists']['used'] == 1 and st['amazon:AE']['cooldown_until']
        assert st['amazon:AE']['fail_streak'] == 1
    finally:
        db.close()


def test_gate_makes_background_wait_for_the_user():
    async def go():
        gate = th.Gate(asyncio.get_running_loop())
        t, clock = make(gate=gate)
        order = []
        gate.user_start()

        async def background():
            async with t.slot('amazon:AU', 'lists'):
                order.append('background')

        task = asyncio.create_task(background())
        await asyncio.sleep(0.01)
        assert order == [] and gate.active == 1
        order.append('user done')
        gate.user_end()
        await asyncio.wait_for(task, 1)
        return order
    assert asyncio.run(go()) == ['user done', 'background']


def test_gate_from_another_thread():
    import threading

    async def go():
        gate = th.Gate(asyncio.get_running_loop())
        gate.user_start()
        assert not await gate.wait_clear(timeout=0.01)
        threading.Thread(target=gate.user_end).start()
        assert await gate.wait_clear(timeout=1)
    asyncio.run(go())


def test_user_slots_do_not_wait_for_the_gate():
    async def go():
        gate = th.Gate(asyncio.get_running_loop())
        gate.user_start()
        t, _ = make(gate=gate)
        async with t.slot('amazon:AU', 'user'):
            return True
    assert asyncio.run(go())


def test_task_contextvar_default_and_override():
    assert th.TASK.get() == 'user'
    t, clock = make()

    async def go():
        th.TASK.set('watch_kw')
        async with t.slot('amazon:AU'):
            pass
    asyncio.run(go())
    assert t.store.used(th.utc_day(clock()), 'amazon:AU', 'watch_kw') == 1
    assert th.TASK.get() == 'user'


def test_state_shape():
    t, _ = make()
    st = t.state()
    assert set(st) == set(th.SOURCES)
    assert st['amazon:US']['tasks']['lists'] == {'used': 0, 'skipped': 0, 'quota': 32}
    assert st['google']['daily'] == 120 and st['google']['cooldown_until'] is None
