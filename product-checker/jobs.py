"""Background jobs started from the UI (checks, scans, batches).

Finished jobs are kept for 30 minutes (at most 50), and each job's log is capped, so memory stays flat
when the app runs for days. The UI polls /api/job; an expired job answers 410 (Gone).
"""
import logging
import threading
import time
import uuid

log = logging.getLogger('pc')
KEEP_SECONDS = 30 * 60
MAX_JOBS = 50
MAX_LOG = 200


class JobManager:
    def __init__(self, run_coro, say=None, on_user_start=None, on_user_end=None):
        self.run_coro = run_coro                  # submits a coroutine to the browser loop, returns a Future
        self.say = say or (lambda m: None)
        self.on_user_start = on_user_start or (lambda: None)
        self.on_user_end = on_user_end or (lambda: None)
        self.jobs = {}
        self.lock = threading.Lock()

    def start(self, kind, fn):
        """fn(progress) -> coroutine. Returns the job id."""
        self.prune()
        jid = uuid.uuid4().hex[:10]
        job = {'id': jid, 'kind': kind, 'status': 'running', 'log': [], 'result': None, 'error': '',
               'started': time.time(), 'finished': None}
        with self.lock:
            self.jobs[jid] = job

        def progress(m):
            job['log'].append(str(m))
            if len(job['log']) > MAX_LOG:
                del job['log'][:len(job['log']) - MAX_LOG]

        def done(fut):
            try:
                job['result'] = fut.result()
                job['status'] = 'done'
            except Exception as e:                              # noqa: BLE001 - reported to the UI
                log.exception('job %s failed', kind)
                msg = str(e).strip() or e.__class__.__name__
                job['status'], job['error'] = 'error', msg[:1000]
                self.say('%s failed: %s' % (kind.capitalize(), msg.splitlines()[0][:200]))
            finally:
                job['finished'] = time.time()
                self.on_user_end()
        self.on_user_start()
        try:
            self.run_coro(fn(progress)).add_done_callback(done)
        except Exception:
            self.on_user_end()
            raise
        return jid

    def get(self, jid):
        return self.jobs.get(jid)

    def prune(self, now=None):
        now = now or time.time()
        with self.lock:
            done = [j for j in self.jobs.values() if j['finished'] and now - j['finished'] > KEEP_SECONDS]
            for j in done:
                self.jobs.pop(j['id'], None)
            if len(self.jobs) > MAX_JOBS:                       # oldest finished first
                old = sorted((j for j in self.jobs.values() if j['finished']), key=lambda j: j['finished'])
                for j in old[:len(self.jobs) - MAX_JOBS]:
                    self.jobs.pop(j['id'], None)

    def running(self):
        return sum(1 for j in self.jobs.values() if j['status'] == 'running')
