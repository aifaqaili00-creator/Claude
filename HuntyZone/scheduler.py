"""Background refresh: watched keywords, Movers & Shakers lists, Google Trends and imports, on a schedule.

It wakes once a minute by the wall clock (so it catches up after the PC sleeps), runs whatever is due, oldest
first, one unit at a time, and lets the throttle decide pacing and daily budgets. Background units run with
amazon_check.BACKGROUND set, so a captcha never pops a window up: the source cools down and an alert says so.
The app supplies one async handler per task kind.
"""
import asyncio
import logging
import time
import zlib

import amazon_check as ac
import storage
import throttle as th

log = logging.getLogger('pc')

TICK = 60
EVERY = {'lists:US': 12 * 3600, 'lists:AU': 24 * 3600, 'lists:AE': 24 * 3600, 'watch_kw': 24 * 3600,
         'trends': 7 * 86400, 'suggest': 24 * 3600, 'products': 24 * 3600, 'import_scan': 3600,
         'maintenance': 24 * 3600}
UNIT_TIMEOUT = {'lists': 900, 'watch_kw': 300, 'trends': 180, 'suggest': 240, 'products': 180, 'import_scan': 300,
                'maintenance': 600}
DEFAULT_TIMEOUT = 300
MAX_UNITS_PER_TICK = 40
SOURCE_OF = {'lists': 'amazon', 'watch_kw': 'amazon', 'products': 'amazon', 'trends': 'google', 'suggest': 'complete'}


def stagger(kind, market, target, every_s):
    """A fixed offset per task, so first runs spread over the period instead of all starting at once."""
    return zlib.crc32(('%s|%s|%s' % (kind, market or '', target or '')).encode()) % max(1, int(every_s))


def source_for(task):
    base = SOURCE_OF.get(task['kind'])
    if base == 'google':
        return 'google'
    if base and task.get('market'):
        return '%s:%s' % (base, task['market'])
    return None


def next_utc_midnight(now):
    return (int(now) // 86400 + 1) * 86400


class Scheduler:
    def __init__(self, db, handlers, throttle=None, clock=time.time, sleep=asyncio.sleep, conditions=None,
                 on_run_done=None, say=None):
        self.db, self.handlers, self.throttle = db, handlers, throttle
        self.clock, self.sleep = clock, sleep
        self.conditions = conditions or (lambda: None)
        self.on_run_done = on_run_done
        self.say = say or (lambda m: None)
        self.paused = None
        self.pause_until = 0
        self.last_run = None
        self.current = None
        self.running = False
        self._stop = False
        self._loop = None
        self._wake = None

    # ---------- tasks ----------
    def sync_tasks(self, specs, now=None):
        """Make the task table match the wanted work: add new tasks, update, and disable ones no longer wanted."""
        now = int(now if now is not None else self.clock())
        kinds = {s['kind'] for s in specs}

        def fn(conn):
            wanted = set()
            for s in specs:
                key = (s['kind'], s.get('market') or '', s.get('target') or '')
                wanted.add(key)
                every = int(s['every_s'])
                first = now + stagger(*key, every) % min(every, 3600)
                conn.execute('INSERT INTO task(kind, market, target, every_s, next_run_at, priority, enabled) '
                             'VALUES (?,?,?,?,?,?,1) ON CONFLICT(kind, market, target) DO UPDATE SET '
                             'every_s=excluded.every_s, priority=excluded.priority, enabled=1',
                             (*key, every, first, s.get('priority', 5)))
            for r in conn.execute('SELECT id, kind, market, target FROM task WHERE enabled=1').fetchall():
                if r[1] in kinds and (r[1], r[2], r[3]) not in wanted:
                    conn.execute('UPDATE task SET enabled=0 WHERE id=?', (r[0],))
        self.db.write(fn)

    def tasks(self, enabled_only=True):
        return self.db.all('SELECT * FROM task' + (' WHERE enabled=1' if enabled_only else '') + ' ORDER BY id')

    def due(self, now):
        rows = self.db.all('SELECT * FROM task WHERE enabled=1 AND next_run_at<=?', (int(now),))
        rows.sort(key=lambda t: (t['priority'] if t['priority'] is not None else 5,
                                 -(now - t['next_run_at']) / max(1, t['every_s'] or 1), t['id']))
        return rows[:MAX_UNITS_PER_TICK]

    def _update(self, task, status, next_at, ok, now):
        def fn(conn):
            conn.execute('UPDATE task SET last_run_at=?, last_status=?, next_run_at=?, fail_streak=? WHERE id=?',
                         (int(now), status, int(next_at), 0 if ok else (task['fail_streak'] or 0) + 1, task['id']))
        self.db.write(fn)

    # ---------- one unit ----------
    async def _unit(self, task):
        handler = self.handlers.get(task['kind'])
        if not handler:
            return {'status': 'error', 'note': 'no handler for %s' % task['kind']}

        async def run():
            ac.BACKGROUND.set(True)
            th.TASK.set(task['kind'])
            return await asyncio.wait_for(handler(task), UNIT_TIMEOUT.get(task['kind'], DEFAULT_TIMEOUT))
        # a task runs in its own copy of the context, so these flags stay inside this unit (Python 3.10 too)
        return await asyncio.ensure_future(run())

    # ---------- one tick ----------
    async def tick(self, now=None):
        now = now if now is not None else self.clock()
        if self.pause_until and now < self.pause_until:
            self.paused = 'paused by you'
            return None
        self.pause_until = 0
        reason = self.conditions()
        self.paused = reason
        if reason:
            return None
        due = self.due(now)
        if not due:
            return None
        self.running = True
        run_id = self.db.write(storage.start_run, 'refresh', 'schedule')
        summary = {'run_id': run_id, 'ran': 0, 'ok': 0, 'failed': 0, 'blocked': 0, 'skipped': 0}
        results, cooling = [], {}
        try:
            for task in due:
                src = source_for(task)
                t_now = self.clock()
                if src and src in cooling:                            # that source said stop: do not hammer it
                    self._update(task, 'cooldown', cooling[src], False, t_now)
                    summary['skipped'] += 1
                    continue
                self.current = {k: task[k] for k in ('kind', 'market', 'target')}
                every = task['every_s'] or 86400
                try:
                    out = await self._unit(task) or {}
                    status = out.get('status', 'ok')
                    ok = status in storage.VALID
                    self._update(task, status, self.clock() + every, ok, t_now)
                    summary['ran'] += 1
                    summary['ok' if ok else 'failed'] += 1
                    results.append({**self.current, 'status': status, 'note': out.get('note'), 'data': out.get('data')})
                except th.QuotaExceeded:
                    self._update(task, 'skipped_quota', next_utc_midnight(t_now) + stagger(
                        task['kind'], task['market'], task['target'], 3600), True, t_now)
                    summary['skipped'] += 1
                except th.Cooldown as e:
                    cooling[src or e.source] = e.until
                    self._update(task, 'cooldown', e.until, False, t_now)
                    summary['skipped'] += 1
                except ac.BlockedError as e:
                    if self.throttle is not None and task.get('market') and src and src.startswith('amazon'):
                        until = self.throttle.report(src, e.status, str(e)[:120])
                        if until:
                            cooling[src] = until
                    self._update(task, e.status, self.clock() + min(every, 2 * 3600), False, t_now)
                    summary['ran'] += 1
                    summary['blocked'] += 1
                    results.append({**self.current, 'status': e.status, 'note': str(e)[:200]})
                except asyncio.TimeoutError:
                    self._update(task, 'timeout', self.clock() + min(every, 3600), False, t_now)
                    summary['ran'] += 1
                    summary['failed'] += 1
                    results.append({**self.current, 'status': 'timeout'})
                except Exception as e:                                # noqa: BLE001 - one unit never stops the run
                    log.exception('scheduled %s failed', task['kind'])
                    backoff = min(every, 3600) * min(4, 1 + (task['fail_streak'] or 0))
                    self._update(task, 'error', self.clock() + backoff, False, t_now)
                    summary['ran'] += 1
                    summary['failed'] += 1
                    results.append({**self.current, 'status': 'error', 'note': str(e)[:200]})
            status = 'done' if summary['failed'] == 0 and summary['blocked'] == 0 else 'partial' if summary['ok'] else 'error'
            if not summary['ran']:
                status = 'skipped'
            self.db.write(storage.finish_run, run_id, status, summary['ok'], summary['failed'], summary['blocked'])
            if self.on_run_done and summary['ran']:
                try:
                    await self.on_run_done(run_id, results)
                except Exception:                                     # noqa: BLE001
                    log.exception('after-run analysis failed')
            self.last_run = {'ts': self.clock(), **summary}
            return summary
        finally:
            self.current = None
            self.running = False

    # ---------- the loop ----------
    async def run_forever(self):
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        try:
            self.db.write(storage.interrupt_stale_runs)
        except Exception:                                             # noqa: BLE001
            log.exception('could not mark interrupted runs')
        while not self._stop:
            now = self.clock()
            wait = TICK - (now % TICK) + 0.5                          # next wall-clock minute
            sleeper = asyncio.ensure_future(self.sleep(wait))
            waker = asyncio.ensure_future(self._wake.wait())
            await asyncio.wait({sleeper, waker}, return_when=asyncio.FIRST_COMPLETED)
            for f in (sleeper, waker):
                if not f.done():
                    f.cancel()
            self._wake.clear()
            if self._stop:
                break
            try:
                await self.tick()
            except Exception:                                         # noqa: BLE001
                log.exception('scheduler tick failed')

    def poke(self):
        """Run due work now (thread-safe)."""
        if self._loop is not None and self._wake is not None:
            self._loop.call_soon_threadsafe(self._wake.set)

    def run_all_now(self):
        """Make every enabled task due and wake up (the user's "Refresh everything now")."""
        now = int(self.clock())
        self.db.write(lambda c: c.execute('UPDATE task SET next_run_at=? WHERE enabled=1', (now,)))
        self.pause_until = 0
        self.poke()

    def stop(self):
        self._stop = True
        self.poke()

    def pause(self, seconds, reason='paused by you'):
        self.pause_until = self.clock() + seconds
        self.paused = reason

    def resume(self):
        self.pause_until = 0
        self.paused = None
        self.poke()

    def status(self):
        r = self.db.one('SELECT MIN(next_run_at) AS nxt, COUNT(*) AS n, SUM(CASE WHEN next_run_at<=? THEN 1 ELSE 0 END) AS due '
                        'FROM task WHERE enabled=1', (int(self.clock()),))
        return {'running': self.running, 'paused': self.paused,
                'pause_until': self.pause_until if self.pause_until > self.clock() else None,
                'last_run': self.last_run, 'next_due': r['nxt'] if r else None, 'due_now': (r or {}).get('due') or 0,
                'tasks_total': (r or {}).get('n') or 0, 'current': self.current}
