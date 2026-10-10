"""After every refresh: turn the new data into alerts, and send one notification for the important ones.

Reads market.db, runs the pure rules in engine/alerts.py and engine/surge.py, stores new alerts (deduplicated
per week) and returns them. Runs in a worker thread, so the browser loop never waits on it.
"""
import logging
import time

import config
import storage
from engine import alerts as rules
from engine import surge

log = logging.getLogger('pc')
LIST_DAYS = 14
SERP_DAYS = 70


def list_alerts(conn, market, now):
    scans = storage.list_scans(conn, market, now - LIST_DAYS * 86400)
    if not scans:
        return [], {}
    feats = surge.all_features(scans, days=7)
    titles = {}
    for sc in scans:
        for it in sc['items']:
            if it.get('title'):
                titles[it['asin']] = it['title']
    recent = [{'asin': a, 'title': titles.get(a, '')} for a, f in feats.items() if f.get('appearances') and titles.get(a)]
    infos = []
    for c in surge.clusters(recent):
        if len(c['asins']) >= 2:
            infos.append({'key': c['key'], 'asins': c['asins'],
                          'surge': surge.cluster_surge([feats[a].get('surge') for a in c['asins'] if a in feats])})
    for a, f in feats.items():
        f.setdefault('title', titles.get(a))
    return rules.list_rules(market, feats, infos, now), feats


def serp_alerts(conn, watches, now):
    out = []
    for w in watches:
        if w['kind'] != 'keyword' or not w.get('enabled', 1):
            continue
        snaps = storage.serp_history(conn, w['market'], w['target'], now - SERP_DAYS * 86400)
        out += rules.serp_rules(w['market'], w['target'], snaps, now)
    return out


def parse_health(conn, now):
    """Canary numbers per market: badges parsed recently vs before, and list sizes vs their median."""
    out = {}
    for m in config.MARKETS:
        def badges(a, b):
            r = conn.execute("SELECT SUM(badged), COUNT(*) FROM serp_snapshot WHERE market=? AND ts>=? AND ts<? "
                             "AND status IN ('ok','partial')", (m, a, b)).fetchone()
            return (r[0] or 0), r[1]
        b2, pages = badges(now - 2 * 86400, now + 1)
        bb, before_pages = badges(now - 30 * 86400, now - 2 * 86400)
        h = {'search_pages_last_2_days': pages}
        if before_pages:
            h.update(badges_last_2_days=b2, badges_before=bb)
        r = conn.execute('SELECT n FROM list_snapshot WHERE market=? AND ts>=? ORDER BY ts', (m, now - 14 * 86400)).fetchall()
        ns = [x[0] for x in r if x[0] is not None]
        if len(ns) >= 6:
            recent = ns[-4:]
            med = sorted(ns)[len(ns) // 2]
            if med:
                h['list_n_vs_median'] = (sum(recent) / len(recent)) / med
        out[m] = h
    return out


def ops_alerts(conn, S, watches, now):
    stale = []
    for t in storage._rows(conn.execute('SELECT * FROM task WHERE enabled=1')):
        if t['kind'] in ('watch_kw', 'lists', 'trends'):
            stale.append({'kind': t['kind'], 'market': t['market'], 'target': t['target'], 'display': t['target'],
                          'last_ok_ts': t['last_run_at'] if t['last_status'] in storage.VALID else None,
                          'every_s': t['every_s'], 'added_at': None})
    xray = []
    for w in watches:
        if w['kind'] != 'keyword':
            continue
        r = conn.execute("SELECT MAX(as_of) FROM import_file WHERE kind='xray' AND market=? AND kw_norm=?",
                         (w['market'], w['target'])).fetchone()
        xray.append({**w, 'last_xray_import_ts': r[0]})
    ctx = {'source_state': S.throttle.state(now) if S and getattr(S, 'throttle', None) else {},
           'stale': [s for s in stale if s['last_ok_ts']], 'parse_health': parse_health(conn, now), 'watched': xray}
    return rules.ops_rules(ctx, now)


def run(S, results=(), now=None):
    """Compute and store alerts. Returns the alerts that are new."""
    now = now or time.time()

    def build(conn):
        watches = storage.watches(conn)
        found = serp_alerts(conn, watches, now)
        for m in config.MARKETS:
            try:
                found += list_alerts(conn, m, now)[0]
            except Exception:                                   # noqa: BLE001
                log.exception('list alerts %s', m)
        for r in results or []:
            d = r.get('data') or {}
            if r.get('kind') == 'trends' and d.get('new'):
                found += rules.trend_rules(r['target'], r['market'], d.get('old'), d['new'], now)
        try:
            found += ops_alerts(conn, S, watches, now)
        except Exception:                                       # noqa: BLE001
            log.exception('ops alerts')
        return rules.dedupe(found)

    found = S.db.read(build)
    keys = [a['dedupe_key'] for a in found]

    def store(conn):
        have = {r[0] for r in conn.execute('SELECT dedupe_key FROM alert WHERE dedupe_key IN (%s)' % ','.join('?' * len(keys)),
                                           keys)} if keys else set()
        fresh = [dict(a, ts=now) for a in found if a['dedupe_key'] not in have]
        storage.add_alerts(conn, fresh)
        return fresh
    return S.db.write(store)


def notify_new(S, fresh):
    """One Windows notification for the new alerts that matter (not while you are looking at the app)."""
    import notify
    msg = notify.digest(fresh, S.settings, time.localtime().tm_hour, S.window_focused())
    if not msg:
        return False
    shown = notify.toast(*msg)
    keys = [a['dedupe_key'] for a in fresh]
    S.db.write(lambda c: c.execute('UPDATE alert SET toasted=1 WHERE dedupe_key IN (%s)' % ','.join('?' * len(keys)), keys))
    return shown
