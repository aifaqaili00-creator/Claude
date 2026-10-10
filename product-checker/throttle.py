"""Politeness and daily budgets for every outside source (Amazon per market, Google, Amazon suggestions).

- Each source has a concurrency limit and a random gap between request starts.
- After a captcha, block or throttle, the source cools down: 30 min / 1 h / 2 h / 6 h for captchas,
  longer for blocks. The streak resets after a success. Cooldowns survive restarts (table source_state).
- Background tasks have daily quotas per source; the user's own checks are never limited (they are the reserve).
- The Gate makes background work wait while a user job runs, so the user always comes first.
"""
import asyncio
import contextlib
import contextvars
import os
import random
import threading
import time

SOURCES = {
    'amazon:US': {'concurrency': 2, 'gap': (2.0, 5.0), 'daily': 200},
    'amazon:AU': {'concurrency': 2, 'gap': (2.0, 5.0), 'daily': 120},
    'amazon:AE': {'concurrency': 2, 'gap': (2.0, 5.0), 'daily': 100},
    'google': {'concurrency': 1, 'gap': (6.0, 10.0), 'daily': 120},
    'complete:US': {'concurrency': 1, 'gap': (1.0, 1.5), 'daily': 600},
    'complete:AU': {'concurrency': 1, 'gap': (1.0, 1.5), 'daily': 600},
    'complete:AE': {'concurrency': 1, 'gap': (1.0, 1.5), 'daily': 600},
}
QUOTAS = {
    'amazon:US': {'lists': 32, 'watch_kw': 30, 'products': 60, 'qualify': 30},
    'amazon:AU': {'lists': 16, 'watch_kw': 30, 'products': 30, 'qualify': 20},
    'amazon:AE': {'lists': 16, 'watch_kw': 30, 'products': 20, 'qualify': 15},
    'google': {'trends': 60, 'qualify': 30, 'related': 20},
}
LADDERS = {
    'captcha': [1800, 3600, 7200, 21600],
    'blocked': [3600, 7200, 14400, 43200],
    'empty_suspect': [3600, 7200, 14400, 43200],
    'geo_redirect': [3600, 7200, 14400, 43200],
    'wrong_location': [3600, 7200, 14400, 43200],
    'throttled': [3600, 7200, 14400, 86400],
}
DEFAULT_SOURCE = {'concurrency': 1, 'gap': (1.0, 2.0), 'daily': None}
GAP_SCALE = float(os.environ.get('PC_GAP_SCALE', '1'))         # tests only: shrink the gaps
TASK = contextvars.ContextVar('pc_task', default='user')      # which quota a request counts against


class ThrottleError(Exception):
    pass


class Cooldown(ThrottleError):
    def __init__(self, source, until, reason=''):
        super().__init__('%s is paused until %s (%s)' % (source, time.strftime('%H:%M', time.localtime(until)),
                                                          reason or 'cooling down'))
        self.source, self.until, self.reason = source, until, reason


class QuotaExceeded(ThrottleError):
    def __init__(self, source, task):
        super().__init__('Today\'s %s budget for %s is used up' % (task, source))
        self.source, self.task = source, task


def utc_day(ts):
    return time.strftime('%Y-%m-%d', time.gmtime(ts))


# ---------- where the state lives ----------
STATE_DEFAULTS = {'cooldown_until': 0, 'fail_streak': 0, 'budget_factor': 1.0, 'last_ok': None, 'last_block': None,
                  'reason': None}


class MemoryStore:
    def __init__(self):
        self.states, self.budget = {}, {}

    def get(self, source):
        return {**STATE_DEFAULTS, **self.states.get(source, {})}

    def put(self, source, **fields):
        self.states.setdefault(source, {}).update(fields)

    def used(self, day, source, task):
        return self.budget.get((day, source, task), {}).get('used', 0)

    def add(self, day, source, task, used=0, skipped=0):
        b = self.budget.setdefault((day, source, task), {'used': 0, 'skipped': 0})
        b['used'] += used
        b['skipped'] += skipped

    def day_rows(self, day):
        return [{'day': d, 'source': s, 'task': t, **v} for (d, s, t), v in self.budget.items() if d == day]


class Store:
    """The same interface over market.db (tables source_state and budget)."""

    def __init__(self, db):
        self.db = db

    def get(self, source):
        r = self.db.one('SELECT * FROM source_state WHERE source=?', (source,))
        return {**STATE_DEFAULTS, **{k: v for k, v in (r or {}).items() if v is not None and k != 'source'}}

    def put(self, source, **fields):
        cols = list(fields)

        def fn(conn):
            conn.execute('INSERT INTO source_state(source) VALUES (?) ON CONFLICT(source) DO NOTHING', (source,))
            if cols:
                conn.execute('UPDATE source_state SET %s WHERE source=?' % ', '.join('%s=?' % c for c in cols),
                             [fields[c] for c in cols] + [source])
        self.db.write(fn)

    def used(self, day, source, task):
        r = self.db.one('SELECT used FROM budget WHERE day=? AND source=? AND task=?', (day, source, task))
        return r['used'] if r else 0

    def add(self, day, source, task, used=0, skipped=0):
        self.db.write(lambda c: c.execute(
            'INSERT INTO budget(day, source, task, used, skipped) VALUES (?,?,?,?,?) ON CONFLICT(day, source, task) '
            'DO UPDATE SET used=used+excluded.used, skipped=skipped+excluded.skipped', (day, source, task, used, skipped)))

    def day_rows(self, day):
        return self.db.all('SELECT * FROM budget WHERE day=?', (day,))


# ---------- user first ----------
class Gate:
    """Counts running user jobs. Background work waits until there are none. Safe from any thread."""

    def __init__(self, loop=None):
        self.loop = loop
        self._n = 0
        self._lock = threading.Lock()
        self._clear = None

    def _event(self):
        if self._clear is None:
            self._clear = asyncio.Event()
            if self._n == 0:
                self._clear.set()
        return self._clear

    def _sync(self):
        ev = self._clear
        if ev is None:
            return
        if self._n == 0:
            ev.set()
        else:
            ev.clear()

    def _changed(self):
        if self.loop is not None and self.loop.is_running():
            self.loop.call_soon_threadsafe(self._sync)
        else:
            self._sync()

    def user_start(self):
        with self._lock:
            self._n += 1
        self._changed()

    def user_end(self):
        with self._lock:
            self._n = max(0, self._n - 1)
        self._changed()

    @property
    def active(self):
        return self._n

    async def wait_clear(self, timeout=None):
        ev = self._event()
        if self._n == 0:
            ev.set()
            return True
        if timeout is None:
            await ev.wait()
            return True
        try:
            await asyncio.wait_for(ev.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False


# ---------- the throttle ----------
class Throttle:
    def __init__(self, store=None, gate=None, clock=time.time, sleep=asyncio.sleep, rand=random.uniform):
        self.store = store or MemoryStore()
        self.gate = gate
        self.clock, self.sleep, self.rand = clock, sleep, rand
        self._sems = {}
        self._next_start = {}
        self._gap_locks = {}
        self._last_ok = {}

    def _conf(self, source):
        return SOURCES.get(source, DEFAULT_SOURCE)

    def _sem(self, source):
        if source not in self._sems:
            self._sems[source] = asyncio.Semaphore(self._conf(source)['concurrency'])
            self._gap_locks[source] = asyncio.Lock()
        return self._sems[source]

    def remaining(self, source, task):
        q = QUOTAS.get(source, {}).get(task)
        if q is None or task == 'user':
            return None
        factor = self.store.get(source).get('budget_factor') or 1.0
        return max(0, int(q * factor) - self.store.used(utc_day(self.clock()), source, task))

    def check(self, source, task='user'):
        """Raise Cooldown or QuotaExceeded now, without waiting or counting anything."""
        st = self.store.get(source)
        now = self.clock()
        if (st.get('cooldown_until') or 0) > now:
            raise Cooldown(source, st['cooldown_until'], st.get('reason') or '')
        if task != 'user':
            left = self.remaining(source, task)
            if left is not None and left <= 0:
                self.store.add(utc_day(now), source, task, skipped=1)
                raise QuotaExceeded(source, task)

    @contextlib.asynccontextmanager
    async def slot(self, source, task=None):
        task = task or TASK.get()
        if task != 'user' and self.gate is not None:
            await self.gate.wait_clear()
        self.check(source, task)
        sem = self._sem(source)
        async with sem:
            async with self._gap_locks[source]:              # one start at a time per source, spaced out
                wait = self._next_start.get(source, 0) - self.clock()
                if wait > 0:
                    await self.sleep(wait)
                start = self.clock()
                lo, hi = self._conf(source)['gap']
                self._next_start[source] = start + self.rand(lo, hi) * GAP_SCALE
            try:
                yield
            finally:
                self.store.add(utc_day(self.clock()), source, task, used=1)

    def report(self, source, outcome, detail=''):
        now = self.clock()
        if outcome == 'ok':
            self._last_ok[source] = int(now)                 # kept in memory: no database write per page
            st = self.store.get(source)
            if st.get('fail_streak'):
                self.store.put(source, fail_streak=0, last_ok=int(now), reason=None)
            return 0
        ladder = LADDERS.get(outcome)
        if not ladder:
            return 0
        st = self.store.get(source)
        streak = int(st.get('fail_streak') or 0)
        until = int(now + ladder[min(streak, len(ladder) - 1)])
        self.store.put(source, cooldown_until=until, fail_streak=streak + 1, last_block=int(now),
                       reason=(outcome + (': ' + detail if detail else ''))[:200])
        return until

    def clear(self, source):
        self.store.put(source, cooldown_until=0, reason=None)

    def state(self, now=None):
        now = now or self.clock()
        day = utc_day(now)
        rows = self.store.day_rows(day)
        out = {}
        for source, conf in SOURCES.items():
            st = self.store.get(source)
            tasks = {}
            for r in rows:
                if r['source'] == source:
                    tasks[r['task']] = {'used': r['used'], 'skipped': r['skipped'],
                                        'quota': QUOTAS.get(source, {}).get(r['task'])}
            for t, q in QUOTAS.get(source, {}).items():
                tasks.setdefault(t, {'used': 0, 'skipped': 0, 'quota': q})
            cool = st.get('cooldown_until') or 0
            out[source] = {'cooldown_until': cool if cool > now else None, 'reason': st.get('reason') if cool > now else None,
                           'fail_streak': st.get('fail_streak') or 0,
                           'last_ok': max(st.get('last_ok') or 0, self._last_ok.get(source, 0)) or None,
                           'last_block': st.get('last_block'), 'daily': conf['daily'],
                           'used_today': sum(t['used'] for t in tasks.values()), 'tasks': tasks}
        return out
