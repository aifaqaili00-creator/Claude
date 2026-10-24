"""The product board: your products from idea to re-order. Cards, their orders and shipments, stock counts,
launch hints from the app's research, notes, deadlines and the reminder emails those send. Functions that take
`conn` run inside db.read / db.write; the ones that take `db` open their own. Rules live in engine.board,
engine.hints and engine.remind."""
import datetime as dt
import json
import math
import re
import threading
import time

import amazon_check as ac
import config
import fxrates
import reports
import storage
from engine import board as rules
from engine import hints as hintmod
from engine import inform
from engine import launch as lch
from engine import planner, profit, remind
from engine import fees
from engine.trendfeat import MONTH_NAMES as MONTH_ABBR

COLORS = ('blue', 'violet', 'teal', 'green', 'amber', 'orange', 'red', 'pink', 'gray')
IMAGE_RX = re.compile(r'^https://(?:[a-z0-9-]+\.)*(?:media-amazon|ssl-images-amazon)\.com/[^\s"\'<>]{1,400}$')
ASIN_RX = re.compile(r'^[A-Z0-9]{10}$')
LINK_RX = re.compile(r'^https://[a-z0-9-]+(?:\.[a-z0-9-]+)+(?::\d{1,5})?(?:[/?#][^\s"\'<>]*)?$', re.I)
STATUSES = ('todo', 'done', 'adjusted', 'skip')
WATCH_ORDER = ('idea', 'researching', 'sourcing', 'ordered', 'launched')
STEPS = ('deposit', 'produced', 'shipped', 'arrived')
KIND_NAMES = {'start': 'New task email', 'reminder': 'Reminder', 'meeting': 'Meeting invite'}
MERGE_S = 600                    # edits of one kind within 10 minutes share one history line
RETRY_S = (15 * 60, 60 * 60)     # a failed email is tried again after these, then reported on Home
MAX_TRIES = 3
INFORM_KEY = 'board_inform'      # ui_state: the last product update {'at', 'sent_at', 'snapshot', 'to', 'auto'}
INFORM_WAIT_KEY = 'board_inform_wait'   # ui_state: changes waiting for the automatic update {'fp', 'since', 'tries'...}
_rev = {'n': 0}
_send_lock = threading.Lock()


class BoardError(ValueError):
    """Bad input; the message is meant for you."""


def bump():
    _rev['n'] += 1


def rev():
    return _rev['n']


def _per_usd():
    return fxrates.rates()['per_usd']


# ---------- input ----------
def _number(v, lo, hi, what, whole=False):
    if v is None or v == '':
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise BoardError('%s must be a number.' % what) from None
    if not math.isfinite(x) or x < lo or x > hi:
        raise BoardError('%s must be between %s and %s.' % (what, '{:,}'.format(lo), '{:,}'.format(hi)))
    return int(round(x)) if whole else round(x, 4)


def _when(v, what='Date'):
    if v is None or v == '':
        return None
    d = rules.day(v)
    if not d or not re.match(r'^\d{4}-\d{2}-\d{2}$', str(v)):
        raise BoardError('%s must be a date.' % what)
    return d.isoformat()


def _text(v, n):
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(v or '')).strip()[:n]


def _cur(v):
    if v not in config.CURRENCIES:
        raise BoardError('Currency must be one of %s.' % ', '.join(config.CURRENCIES))
    return v


def _market(v):
    if v not in config.MARKETS:
        raise BoardError('Which market: US, AU or AE?')
    return v


# ---------- rows ----------
def _card(conn, card_id):
    r = conn.execute('SELECT * FROM board_card WHERE id=?', (card_id,)).fetchone()
    if not r:
        raise BoardError('That product is not on the board.')
    c = dict(r)
    c['data'] = json.loads(c.pop('data_json') or '{}')
    for k in ('milestones', 'targets', 'live'):
        c['data'].setdefault(k, {})
    if 'approval' not in c['data']:                     # on the board before the Approval step: counts as approved
        c['data']['approval'] = {'items': []}
        c['data']['milestones'].setdefault('approved', c.get('created_at') or 1)
    if c['stage'] not in ('idea', 'precheck'):          # on the board before the Idea step: counts as done
        c['data']['milestones'].setdefault('idea_done', c.get('created_at') or 1)
    return c


def _save(conn, card, now):
    conn.execute('UPDATE board_card SET data_json=?, stage=?, pos=?, updated_at=? WHERE id=?',
                 (json.dumps(card['data']), card['stage'], card['pos'], now, card['id']))


def _orders(conn, card_id):
    orders = [dict(r) for r in conn.execute('SELECT * FROM board_order WHERE card_id=? ORDER BY n', (card_id,))]
    by = {o['id']: o for o in orders}
    for o in orders:
        o['lines'] = []
    if by:
        for r in conn.execute('SELECT * FROM board_line WHERE order_id IN (%s)' % ','.join('?' * len(by)), list(by)):
            ln = dict(r)
            ln['steps'] = json.loads(ln.pop('steps_json') or '{}')
            by[ln['order_id']]['lines'].append(ln)
    for o in orders:
        o['lines'].sort(key=lambda x: config.MARKETS.index(x['market']) if x['market'] in config.MARKETS else 9)
    return orders


def _order(conn, card_id, n):
    o = next((x for x in _orders(conn, card_id) if x['n'] == n), None)
    if not o:
        raise BoardError('Order #%s is not on this product.' % n)
    return o


def _hints(conn, card_id):
    out = []
    for r in conn.execute('SELECT * FROM board_hint WHERE card_id=? ORDER BY custom, id', (card_id,)):
        h = dict(r)
        h['data'] = json.loads(h.pop('data_json') or '{}')
        h['moved'] = json.loads(h.pop('moved_json') or 'null')
        out.append(h)
    return out


def _counts(conn, line_id):
    return [(r[0], r[1]) for r in conn.execute('SELECT ts, units_left FROM board_stock WHERE line_id=? ORDER BY ts, id',
                                               (line_id,))]


def _event(conn, card_id, kind, text, now, data=None, merge=False):
    if merge:
        last = conn.execute('SELECT id, kind, ts FROM board_event WHERE card_id=? ORDER BY ts DESC, id DESC LIMIT 1',
                            (card_id,)).fetchone()
        if last and last['kind'] == kind and now - last['ts'] < MERGE_S:
            conn.execute('UPDATE board_event SET ts=?, text=? WHERE id=?', (now, text, last['id']))
            return
    conn.execute('INSERT INTO board_event(card_id, ts, kind, text, data_json) VALUES (?,?,?,?,?)',
                 (card_id, now, kind, text, json.dumps(data) if data else None))


def _watch(conn, kw, market, stage, now):
    """Watch the product's keyword in a market (so its research stays fresh) and move the watch's stage forward."""
    wid = storage.add_watch(conn, 'keyword', market, kw, None, 24, now)
    cur = conn.execute('SELECT stage FROM watch WHERE id=?', (wid,)).fetchone()[0]
    if cur not in WATCH_ORDER or WATCH_ORDER.index(stage) > WATCH_ORDER.index(cur):
        storage.update_watch(conn, wid, stage=stage)


def _markets(orders, card):
    """The markets of the first order, or the card's own market."""
    return [ln['market'] for ln in orders[0]['lines']] if orders and orders[0]['lines'] else [card['market']]


def _name(mk):
    return rules.MARKET_NAMES.get(mk, mk)


# ---------- cards ----------
def add_card(conn, d, settings, now=None):
    """Put a product on the board as an Idea: once it has its Amazon ASIN and Alibaba link it moves on to Approval by
    itself (given both, it starts there). Also order #1 for its own market (your saved supplier price filled in) and a
    watch on its keyword. The same product twice returns the card already there."""
    now = int(now or time.time())
    market = _market(d.get('market'))
    title = _text(d.get('title'), 160)
    if not title:
        raise BoardError('Give the product a name.')
    asin = str(d.get('asin') or '').strip().upper()
    asin = asin if ASIN_RX.match(asin) else None
    alibaba = str(d.get('alibaba') or '').strip()
    if alibaba and (len(alibaba) > 500 or not LINK_RX.match(alibaba)):
        raise BoardError('Paste the whole Alibaba link, starting with https://')
    kw = config.norm_kw(d.get('kw') or '')[:120] or config.norm_kw(' '.join(title.split()[:6]))[:120]
    if not kw:
        raise BoardError('Which search words does it sell under?')
    image = str(d.get('image') or '').strip()
    image = image if IMAGE_RX.match(image) else None
    same = conn.execute('SELECT id FROM board_card WHERE archived_at IS NULL AND market=? AND %s' %
                        ('asin=?' if asin else 'asin IS NULL AND kw=?'), (market, asin or kw)).fetchone()
    if same:
        return {'id': same[0], 'exists': True}
    other = next((dict(r) for r in conn.execute('SELECT * FROM board_card WHERE archived_at IS NULL ORDER BY id')
                  if same_product(dict(r), {'kw': kw, 'asin': asin})), None)
    if other:                                            # the same product from another market: one card for both
        _add_market(conn, other, market, now)
        if image and not other['image']:
            conn.execute('UPDATE board_card SET image=? WHERE id=?', (image, other['id']))
        return {'id': other['id'], 'exists': True, 'joined': market}
    pos = conn.execute("SELECT COALESCE(MAX(pos), 0) + 1 FROM board_card WHERE stage='idea'").fetchone()[0]
    data = {'milestones': {}, 'targets': {}, 'live': {},
            'approval': {'items': [{'id': i + 1, 'text': t, 'done': None} for i, t in enumerate(rules.APPROVAL_CHECKS)]}}
    if alibaba:
        data['links'] = {'alibaba': alibaba}
    cid = conn.execute('INSERT INTO board_card(title, market, asin, kw, image, color, stage, pos, data_json, created_at, '
                       'updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                       (title, market, asin, kw, image, 'blue', 'idea', pos, json.dumps(data), now, now)).lastrowid
    cost_cur = settings.get('cost_currency') or 'USD'
    unit = None
    if asin:
        mine = (storage.prefs(conn, market, [asin]).get(asin) or {}).get('cogs_usd')
        unit = round(fxrates.convert(mine, 'USD', cost_cur), 2) if mine else None
    oid = conn.execute('INSERT INTO board_order(card_id, n, unit_cost, cost_cur, created_at) VALUES (?,?,?,?,?)',
                       (cid, 1, unit, cost_cur, now)).lastrowid
    conn.execute("INSERT INTO board_line(order_id, market, ship_cur, steps_json) VALUES (?,?,'USD','{}')", (oid, market))
    _watch(conn, kw, market, 'idea', now)
    _event(conn, cid, 'created', 'Added to the board as an idea', now)
    return {'id': cid, 'exists': False, 'moved': _idea_if_done(conn, cid, now)['moved']}


def same_product(a, b):
    """Two cards are one product (sold in more than one market): the same search words, and not two different ASINs
    (two ASINs under one phrase are two products, e.g. two firelighter brands). The same ASIN under other words is left
    alone: it is more often a pasted ASIN than the same product."""
    if a.get('asin') and b.get('asin') and a['asin'] != b['asin']:
        return False
    return bool(a.get('kw')) and a.get('kw') == b.get('kw')


def _add_market(conn, card, market, now):
    """Sell a card's product in one more market: a line for it on the first order and a watch on its keyword there."""
    o = conn.execute('SELECT id FROM board_order WHERE card_id=? ORDER BY n LIMIT 1', (card['id'],)).fetchone()
    if not o:
        o = [conn.execute('INSERT INTO board_order(card_id, n, cost_cur, created_at) VALUES (?,1,?,?)',
                          (card['id'], 'USD', now)).lastrowid]
    if conn.execute('SELECT 1 FROM board_line WHERE order_id=? AND market=?', (o[0], market)).fetchone():
        return False
    conn.execute("INSERT INTO board_line(order_id, market, ship_cur, steps_json) VALUES (?,?,'USD','{}')", (o[0], market))
    _watch(conn, card['kw'], market, 'idea', now)
    _event(conn, card['id'], 'created', '%s added: the same product, one card for every market' % _name(market), now)
    return True


def _empty_line(conn, ln):
    return (ln['units'] is None and ln['ship_cost'] is None and ln['arrived_units'] is None
            and not ln.get('steps')
            and not conn.execute('SELECT 1 FROM board_stock WHERE line_id=?', (ln['id'],)).fetchone())


def _drop_line(conn, line_id):
    conn.execute('DELETE FROM board_stock WHERE line_id=?', (line_id,))
    conn.execute('DELETE FROM board_line WHERE id=?', (line_id,))


def _merge_data(keep, other):
    """The other card's details that the kept card does not have yet (links, targets, tags, approval ticks, ...)."""
    for k, v in other.items():
        if k not in keep:
            keep[k] = v
        elif k == 'tags':
            keep[k] = keep[k] + [t for t in v if t not in keep[k]]
        elif k == 'strategy':
            top = max([x['id'] for x in keep[k]] or [0])
            keep[k] = keep[k] + [{**x, 'id': top + i + 1} for i, x in enumerate(v)]
        elif k == 'approval':
            mine = {i['text']: i for i in keep[k].get('items', [])}
            for i in v.get('items', []):
                if i['text'] not in mine:
                    keep[k]['items'].append({**i, 'id': max([x['id'] for x in keep[k]['items']] or [0]) + 1})
                elif i.get('done') and not mine[i['text']].get('done'):
                    mine[i['text']].update(done=i['done'], **({'by': i['by']} if 'by' in i else {}))
        elif isinstance(v, dict) and isinstance(keep[k], dict):
            for kk, vv in v.items():
                keep[k].setdefault(kk, vv)


def merge_cards(conn, keep_id, other_id, now=None):
    """Put card other_id into keep_id and remove it: its markets, orders, history, hints, deadlines, sales and alerts move
    over. Where both cards planned the same market differently, the kept card's plan stays and the other one is written
    into the history, so nothing is lost without a trace."""
    now = int(now or time.time())
    keep, other = _card(conn, keep_id), _card(conn, other_id)
    dropped = []
    mine = {o['n']: o for o in _orders(conn, keep_id)}
    for o in _orders(conn, other_id):
        k = mine.get(o['n'])
        if not k:
            conn.execute('UPDATE board_order SET card_id=? WHERE id=?', (keep_id, o['id']))
            continue
        if k['unit_cost'] is None and o['unit_cost'] is not None:
            conn.execute('UPDATE board_order SET unit_cost=?, cost_cur=? WHERE id=?', (o['unit_cost'], o['cost_cur'], k['id']))
        elif o['unit_cost'] is not None and o['unit_cost'] != k['unit_cost']:
            dropped.append('order #%d unit price %s %s' % (o['n'], o['unit_cost'], o['cost_cur']))
        if k['ready_at'] is None and o['ready_at'] is not None:
            conn.execute('UPDATE board_order SET ready_at=? WHERE id=?', (o['ready_at'], k['id']))
        theirs = {ln['market']: ln for ln in k['lines']}
        for ln in o['lines']:
            have = theirs.get(ln['market'])
            if have and not _empty_line(conn, have):
                if not _empty_line(conn, ln):
                    dropped.append('order #%d %s: %s units, shipping %s %s' % (o['n'], _name(ln['market']), ln['units'],
                                                                               ln['ship_cost'], ln['ship_cur']))
                _drop_line(conn, ln['id'])
                continue
            if have:
                _drop_line(conn, have['id'])
            conn.execute('UPDATE board_line SET order_id=? WHERE id=?', (k['id'], ln['id']))
        conn.execute('DELETE FROM board_order WHERE id=?', (o['id'],))
    live = {r[0] for r in conn.execute('SELECT stage FROM board_task WHERE card_id=? AND cancelled_at IS NULL', (keep_id,))}
    for t in conn.execute('SELECT id, stage FROM board_task WHERE card_id=? AND cancelled_at IS NULL', (other_id,)).fetchall():
        if t['stage'] in live:                           # the kept card's deadline for that step stays the one in force
            _cancel(conn, t['id'], now, 'merged into PB-%d' % keep_id)
    conn.execute('UPDATE board_task SET card_id=? WHERE card_id=?', (keep_id, other_id))
    conn.execute('UPDATE OR IGNORE board_hint SET card_id=? WHERE card_id=?', (keep_id, other_id))
    conn.execute('DELETE FROM board_hint WHERE card_id=?', (other_id,))
    conn.execute('INSERT OR IGNORE INTO board_sales(card_id, market, day, units, ts) '
                 'SELECT ?, market, day, units, ts FROM board_sales WHERE card_id=?', (keep_id, other_id))
    conn.execute('DELETE FROM board_sales WHERE card_id=?', (other_id,))
    conn.execute('UPDATE board_event SET card_id=? WHERE card_id=?', (keep_id, other_id))
    for r in conn.execute("SELECT id, data_json FROM alert WHERE target='board'").fetchall():
        d = json.loads(r[1] or '{}')
        if d.get('card_id') == other_id:
            conn.execute('UPDATE alert SET data_json=? WHERE id=?', (json.dumps({**d, 'card_id': keep_id}), r[0]))
    raw = json.loads(conn.execute('SELECT data_json FROM board_card WHERE id=?', (other_id,)).fetchone()[0] or '{}')
    raw.pop('milestones', None)                          # the kept card's progress stays its own
    _merge_data(keep['data'], raw)
    _save(conn, keep, now)
    if other['image'] and not keep['image']:
        conn.execute('UPDATE board_card SET image=? WHERE id=?', (other['image'], keep_id))
    _event(conn, keep_id, 'created', 'PB-%d (%s) merged into this card: one card for every market%s' % (
        other_id, _name(other['market']), '. Not kept, as this card had its own: ' + '; '.join(dropped) if dropped else ''),
        now, {'merged': other_id, 'dropped': dropped} if dropped else {'merged': other_id})
    conn.execute('DELETE FROM board_card WHERE id=?', (other_id,))
    return {'id': keep_id, 'merged': other_id, 'dropped': dropped}


def merge_duplicates(conn, now=None):
    """One card per product: cards of the same product in different markets (added before cards could hold several
    markets) are merged into the one furthest along, else the one with an ASIN, the most history, the oldest."""
    now = int(now or time.time())
    cards = [dict(r) for r in conn.execute('SELECT id, kw, asin, stage, market FROM board_card WHERE archived_at IS NULL '
                                           'ORDER BY id')]
    groups = []
    for c in cards:
        g = next((g for g in groups if any(same_product(c, x) for x in g) and
                  len({x['asin'] for x in g + [c] if x['asin']}) <= 1), None)
        g.append(c) if g else groups.append([c])
    out = []
    for g in groups:
        if len(g) < 2:
            continue
        events = {c['id']: conn.execute('SELECT COUNT(*) FROM board_event WHERE card_id=?', (c['id'],)).fetchone()[0]
                  for c in g}
        stage = lambda c: rules.STAGES.index(c['stage']) if c['stage'] in rules.STAGES else 0
        keep = max(g, key=lambda c: (stage(c), bool(c['asin']), events[c['id']], -c['id']))
        for c in g:
            if c['id'] != keep['id']:
                out.append(merge_cards(conn, keep['id'], c['id'], now))
                _add_market(conn, _card(conn, keep['id']), c['market'], now)
    if out:
        bump()
    return out


def update_card(conn, card_id, d, settings, now=None):
    """Change a card: title, color, kw (search words), asin, alibaba (your supplier's link), targets {market: units a
    month}, live {market: day}."""
    now = int(now or time.time())
    c = _card(conn, card_id)
    watched = False
    if 'title' in d:
        t = _text(d['title'], 160)
        if not t:
            raise BoardError('Give the product a name.')
        conn.execute('UPDATE board_card SET title=? WHERE id=?', (t, card_id))
    if 'color' in d:
        if d['color'] not in COLORS:
            raise BoardError('Unknown colour.')
        conn.execute('UPDATE board_card SET color=? WHERE id=?', (d['color'], card_id))
    if 'asin' in d:
        asin = str(d['asin'] or '').strip().upper()
        if asin and not ASIN_RX.match(asin):
            raise BoardError('An ASIN is 10 letters and digits, like B0C1234567.')
        if (asin or None) != c['asin']:
            conn.execute('UPDATE board_card SET asin=? WHERE id=?', (asin or None, card_id))
            _event(conn, card_id, 'edit', 'ASIN is now %s' % asin if asin else 'ASIN removed', now)
    if 'alibaba' in d:
        url = str(d['alibaba'] or '').strip()
        if url and (len(url) > 500 or not LINK_RX.match(url)):
            raise BoardError('Paste the whole link, starting with https://')
        links = c['data'].setdefault('links', {})
        if url != links.get('alibaba', ''):
            if url:
                links['alibaba'] = url
            else:
                links.pop('alibaba', None)
            _event(conn, card_id, 'edit', 'Alibaba link saved' if url else 'Alibaba link removed', now)
    if 'kw' in d:
        kw = config.norm_kw(d['kw'] or '')[:120]
        if not kw:
            raise BoardError('Which search words does it sell under?')
        if kw != c['kw']:
            conn.execute('UPDATE board_card SET kw=? WHERE id=?', (kw, card_id))
            for mk in _markets(_orders(conn, card_id), c):
                _watch(conn, kw, mk, 'sourcing', now)
            watched = True
            _event(conn, card_id, 'edit', 'Search words are now "%s"' % kw, now)
    for key, what in (('targets', 'Sales target'), ('live', 'Live day')):
        for mk, v in (d.get(key) or {}).items():
            _market(mk)
            val = _number(v, 0, 1_000_000, what, whole=True) if key == 'targets' else _when(v, what)
            if val is None:
                c['data'][key].pop(mk, None)
            else:
                c['data'][key][mk] = val
            if key == 'live' and val:
                _event(conn, card_id, 'live', 'Live on Amazon %s since %s' % (_name(mk), remind.nice_day(val)), now)
    if 'tags' in d:
        tags = rules.clean_tags(d['tags'] if isinstance(d['tags'], list) else str(d['tags'] or '').split(','))
        if tags != c['data'].get('tags', []):
            c['data']['tags'] = tags
            _event(conn, card_id, 'edit', 'Tags: %s' % ', '.join(tags) if tags else 'Tags removed', now, merge=True)
    fresh = _card(conn, card_id)
    fresh['data'] = c['data']
    _save(conn, fresh, now)
    idea, out = _idea_if_done(conn, card_id, now), _settle(conn, card_id, settings, now)
    return {'id': card_id, 'watched': watched, 'moved': idea['moved'], 'trophies': idea['trophies'] + out['trophies']}


def move_card(conn, card_id, stage, pos=None, now=None):
    now = int(now or time.time())
    c = _card(conn, card_id)
    if stage not in rules.STAGES:
        raise BoardError('Unknown stage.')
    if stage not in rules.unlocked(c['data']['milestones']):
        raise BoardError('Finish %s first.' % {'approval': 'Idea: add the Amazon ASIN and the Alibaba link',
                                                'sourcing': 'Approval: tick every check', 'launch': 'Sourcing & PO'}
                         .get(stage, 'Launching strategy'))
    p = _number(pos, -1e9, 1e9, 'Position')
    if p is None:
        p = conn.execute('SELECT COALESCE(MAX(pos), 0) + 1 FROM board_card WHERE stage=?', (stage,)).fetchone()[0]
    if stage != c['stage']:
        _event(conn, card_id, 'moved', 'Moved to %s' % rules.STAGE_NAMES[stage], now)
    c.update(stage=stage, pos=p)
    _save(conn, c, now)
    return {'id': card_id}


def archive_card(conn, card_id, archived=True, now=None):
    """Remove a product from the board (or put it back). Its scheduled emails are cancelled; putting it back restores
    the deadlines that removing it cancelled, with their emails still to come."""
    now = int(now or time.time())
    c = _card(conn, card_id)
    conn.execute('UPDATE board_card SET archived_at=? WHERE id=?', (now if archived else None, card_id))
    if archived:
        conn.execute("UPDATE board_mail SET status='cancelled', error='product removed from the board' WHERE status='scheduled' "
                     'AND task_id IN (SELECT id FROM board_task WHERE card_id=?)', (card_id,))
        conn.execute('UPDATE board_task SET cancelled_at=? WHERE card_id=? AND done_at IS NULL AND cancelled_at IS NULL',
                     (now, card_id))
    elif c['archived_at']:
        for (tid,) in conn.execute('SELECT id FROM board_task WHERE card_id=? AND cancelled_at=?',
                                   (card_id, c['archived_at'])).fetchall():
            conn.execute('UPDATE board_task SET cancelled_at=NULL WHERE id=?', (tid,))
            conn.execute("UPDATE board_mail SET status='scheduled', error=NULL WHERE task_id=? AND status='cancelled' "
                         "AND error='product removed from the board' AND send_at>?", (tid, now))
    _event(conn, card_id, 'archived' if archived else 'restored',
           'Removed from the board' if archived else 'Put back on the board', now)
    return {'id': card_id}


def delete_card(conn, card_id):
    """Delete a product for good: its orders, shipments, stock counts, hints, notes, deadlines, emails and Home alerts.
    The watch on its keyword stays (it may have been yours before)."""
    _card(conn, card_id)
    orders = 'SELECT id FROM board_order WHERE card_id=?'
    conn.execute('DELETE FROM board_stock WHERE line_id IN (SELECT id FROM board_line WHERE order_id IN (%s))' % orders,
                 (card_id,))
    conn.execute('DELETE FROM board_line WHERE order_id IN (%s)' % orders, (card_id,))
    conn.execute('DELETE FROM board_mail WHERE task_id IN (SELECT id FROM board_task WHERE card_id=?)', (card_id,))
    for table in ('board_order', 'board_task', 'board_hint', 'board_event'):
        conn.execute('DELETE FROM %s WHERE card_id=?' % table, (card_id,))
    alerts = [(r[0],) for r in conn.execute("SELECT id, data_json FROM alert WHERE target='board'")
              if json.loads(r[1] or '{}').get('card_id') == card_id]
    conn.executemany('DELETE FROM alert WHERE id=?', alerts)
    conn.execute('DELETE FROM board_card WHERE id=?', (card_id,))
    return {'id': card_id}


def add_note(conn, card_id, text, now=None):
    now = int(now or time.time())
    _card(conn, card_id)
    t = _text(text, 2000)
    if not t:
        raise BoardError('Write a note first.')
    _event(conn, card_id, 'note', t, now)
    return {'id': card_id}


def remove_note(conn, event_id):
    """Delete one of your notes (the history the app writes stays)."""
    r = conn.execute("SELECT card_id FROM board_event WHERE id=? AND kind='note'", (event_id,)).fetchone()
    if not r:
        raise BoardError('That note is not on the board.')
    conn.execute('DELETE FROM board_event WHERE id=?', (event_id,))
    return {'id': r[0]}


# ---------- launching strategy ----------
STRATEGY_MAX = 50


def _strategy(c, sid):
    s = next((x for x in c['data'].get('strategy') or [] if x['id'] == sid), None)
    if not s:
        raise BoardError('That strategy is not on this product.')
    return s


def _strategy_text(text):
    t = _text(text, 4000)
    if not t:
        raise BoardError('Write the strategy first.')
    return t


def _from(by):
    return ' from ' + by if by else ''


def add_strategy(conn, card_id, by, text, now=None):
    """A launch strategy someone gave you, on the card's Launching strategy tab: who it came from (optional) and what
    it says."""
    now = int(now or time.time())
    c = _card(conn, card_id)
    t, who = _strategy_text(text), _text(by, 80)
    have = c['data'].setdefault('strategy', [])
    if len(have) >= STRATEGY_MAX:
        raise BoardError('At most %d strategies on one product.' % STRATEGY_MAX)
    have.append({'id': max([x['id'] for x in have] or [0]) + 1, 'by': who, 'text': t, 'at': now})
    _save(conn, c, now)
    _event(conn, card_id, 'strategy', 'Launch strategy%s added' % _from(who), now)
    return {'id': card_id}


def edit_strategy(conn, card_id, sid, by, text, now=None):
    now = int(now or time.time())
    c = _card(conn, card_id)
    s = _strategy(c, sid)
    s.update(by=_text(by, 80), text=_strategy_text(text), edited_at=now)
    _save(conn, c, now)
    _event(conn, card_id, 'strategy', 'Launch strategy%s changed' % _from(s['by']), now, merge=True)
    return {'id': card_id}


def remove_strategy(conn, card_id, sid, now=None):
    now = int(now or time.time())
    c = _card(conn, card_id)
    s = _strategy(c, sid)
    c['data']['strategy'] = [x for x in c['data']['strategy'] if x['id'] != sid]
    _save(conn, c, now)
    _event(conn, card_id, 'strategy', 'Launch strategy%s removed' % _from(s['by']), now)
    return {'id': card_id}


# ---------- orders and shipments ----------
def set_order(conn, card_id, n, d, settings, now=None):
    """Price per unit and its currency for order #n."""
    now = int(now or time.time())
    o = _order(conn, card_id, int(n))
    unit = _number(d['unit_cost'], 0, 10_000_000, 'Price per unit') if 'unit_cost' in d else o['unit_cost']
    cur = _cur(d['cost_cur']) if 'cost_cur' in d else o['cost_cur']
    conn.execute('UPDATE board_order SET unit_cost=?, cost_cur=? WHERE id=?', (unit, cur, o['id']))
    _event(conn, card_id, 'po', 'Order #%d changed' % o['n'], now, merge=True)
    return {'id': card_id, **_settle(conn, card_id, settings, now)}


def set_line(conn, card_id, n, market, d, settings, now=None):
    """Add or change one market of order #n: units, ship_cost (total for the shipment), ship_cur, ship_mode (sea or
    air), steps {deposit, produced, shipped, arrived: day} and arrived_units."""
    now = int(now or time.time())
    c, o, mk = _card(conn, card_id), _order(conn, card_id, int(n)), _market(market)
    ln = next((x for x in o['lines'] if x['market'] == mk), None)
    watched = False
    if not ln:
        conn.execute("INSERT INTO board_line(order_id, market, ship_cur, steps_json) VALUES (?,?,'USD','{}')", (o['id'], mk))
        ln = {'units': None, 'ship_cost': None, 'ship_cur': 'USD', 'ship_mode': None, 'steps': {}, 'arrived_units': None}
        _watch(conn, c['kw'], mk, 'sourcing', now)
        watched = True
    vals = {'units': _number(d['units'], 1, 1_000_000, 'Units', whole=True) if 'units' in d else ln['units'],
            'ship_cost': _number(d['ship_cost'], 0, 10_000_000, 'Shipping cost') if 'ship_cost' in d else ln['ship_cost'],
            'ship_cur': _cur(d['ship_cur']) if 'ship_cur' in d else ln['ship_cur'],
            'arrived_units': (_number(d['arrived_units'], 1, 1_000_000, 'Units that arrived', whole=True)
                              if 'arrived_units' in d else ln['arrived_units'])}
    mode = d['ship_mode'] if 'ship_mode' in d else ln['ship_mode']
    if mode not in (None, '') + rules.MODES:
        raise BoardError('Shipment type must be sea or air.')
    steps = dict(ln['steps'])
    for k, v in (d.get('steps') or {}).items():
        if k not in STEPS:
            raise BoardError('Unknown shipment step.')
        steps[k] = _when(v, 'The %s date' % k)
    conn.execute('UPDATE board_line SET units=?, ship_cost=?, ship_cur=?, ship_mode=?, steps_json=?, arrived_units=? '
                 'WHERE order_id=? AND market=?', (vals['units'], vals['ship_cost'], vals['ship_cur'], mode or None,
                                                   json.dumps({k: v for k, v in steps.items() if v}),
                                                   vals['arrived_units'], o['id'], mk))
    if steps.get('arrived') and not ln['steps'].get('arrived'):
        _event(conn, card_id, 'arrived', 'Order #%d arrived in %s' % (o['n'], _name(mk)), now)
    else:
        _event(conn, card_id, 'po', 'Order #%d changed' % o['n'], now, merge=True)
    return {'id': card_id, 'watched': watched, **_settle(conn, card_id, settings, now)}


def remove_line(conn, card_id, n, market, settings, now=None):
    now = int(now or time.time())
    o = _order(conn, card_id, int(n))
    ln = next((x for x in o['lines'] if x['market'] == market), None)
    if ln:
        conn.execute('DELETE FROM board_stock WHERE line_id=?', (ln['id'],))
        conn.execute('DELETE FROM board_line WHERE id=?', (ln['id'],))
        _event(conn, card_id, 'po', 'Order #%d no longer goes to %s' % (o['n'], _name(market)), now)
    return {'id': card_id, **_settle(conn, card_id, settings, now)}


def start_reorder(conn, card_id, market, settings, now=None):
    """A new order for one market: price per unit, shipment type and currencies from the last order that went there;
    you fill in how many and what shipping costs this time."""
    now = int(now or time.time())
    c, mk = _card(conn, card_id), _market(market)
    if not c['data']['milestones'].get('launched'):
        raise BoardError('Re-orders open once the product is launched.')
    orders = _orders(conn, card_id)
    last = next((o for o in reversed(orders) if any(x['market'] == mk for x in o['lines'])), orders[-1])
    prev = next((x for x in last['lines'] if x['market'] == mk), {})
    n = orders[-1]['n'] + 1
    oid = conn.execute('INSERT INTO board_order(card_id, n, unit_cost, cost_cur, created_at) VALUES (?,?,?,?,?)',
                       (card_id, n, last['unit_cost'], last['cost_cur'], now)).lastrowid
    conn.execute("INSERT INTO board_line(order_id, market, ship_cur, ship_mode, steps_json) VALUES (?,?,?,?,'{}')",
                 (oid, mk, prev.get('ship_cur') or 'USD', prev.get('ship_mode')))
    _event(conn, card_id, 'reorder', 'Re-order #%d started for %s' % (n, _name(mk)), now)
    return {'id': card_id, 'n': n, **_settle(conn, card_id, settings, now)}


def _current_line(conn, orders, mk):
    """The shipment selling now in a market: the newest one that arrived or was counted, else the first."""
    lines = [(o, ln) for o in orders for ln in o['lines'] if ln['market'] == mk]
    for o, ln in reversed(lines):
        if ln['steps'].get('arrived') or _counts(conn, ln['id']):
            return o, ln
    return lines[0] if lines else (None, None)


def _stock(conn, card, orders, mk, settings, today):
    o, ln = _current_line(conn, orders, mk)
    if not ln:
        return None
    rows = conn.execute('SELECT ts, units_left, id, source FROM board_stock WHERE line_id=? ORDER BY ts, id',
                        (ln['id'],)).fetchall()
    counts = [(r[0], r[1]) for r in rows]
    sent = ln['arrived_units'] or ln['units']
    left = counts[-1][1] if counts else None
    counted = dt.date.fromtimestamp(counts[-1][0]).isoformat() if counts else None
    sales = [(r[0], r[1]) for r in conn.execute('SELECT day, units FROM board_sales WHERE card_id=? AND market=? '
                                               'ORDER BY day', (card['id'], mk))]
    by_sales = rules.sales_rate(sales, today)
    rate = by_sales if by_sales is not None else rules.sell_rate(counts, sent, ln['steps'].get('arrived'))
    lead = planner.lead_days(mk, ln['ship_mode'] or 'sea')
    fc = rules.forecast(left, rate, lead, counted)
    st = rules.stock_status(sent, left, settings.get('board_low_pct', 30), fc['reorder_by'], today)
    target = card['data']['targets'].get(mk)
    live = next((r[0] for r in reversed(rows) if r[3] == 'spapi'), None)
    return {'market': mk, 'line_id': ln['id'], 'n': o['n'], 'sent': sent, 'arrived': ln['steps'].get('arrived'),
            'counts': [list(r) for r in rows[-12:]], 'left': left, 'counted': counted, 'rate': rate, 'lead_days': lead,
            'rate_src': 'Amazon orders' if by_sales is not None else 'your counts' if rate is not None else None,
            'live_at': live, 'sales': [list(x) for x in sales[-30:]],
            **fc, **st, 'target': target, 'target_progress': rules.target_progress(rate, target)}


def add_count(conn, line_id, units_left, settings, now=None, source=None):
    """How many units are left of a shipment (typed by you, or source 'spapi' from Amazon). Returns new Home alerts:
    stock turned red, or the re-order day came."""
    now = int(now or time.time())
    r = conn.execute('SELECT l.*, o.card_id, o.n FROM board_line l JOIN board_order o ON o.id=l.order_id WHERE l.id=?',
                     (line_id,)).fetchone()
    if not r:
        raise BoardError('That shipment is not on the board.')
    left = _number(units_left, 0, 1_000_000, 'Units left', whole=True)
    if left is None:
        raise BoardError('How many units are left?')
    conn.execute('INSERT INTO board_stock(line_id, ts, units_left, source) VALUES (?,?,?,?)', (line_id, now, left, source))
    c = _card(conn, r['card_id'])
    s = _stock(conn, c, _orders(conn, c['id']), r['market'], settings, dt.date.fromtimestamp(now))
    _event(conn, c['id'], 'stock', '%s: %s left%s%s' % (_name(r['market']), '{:,}'.format(left),
                                                        ' (%d%%)' % round(s['pct']) if s and s['pct'] is not None else '',
                                                        ', from Amazon' if source == 'spapi' else ''), now, merge=True)
    return {'id': c['id'], 'alerts': _stock_alerts(conn, c, s, now) if s else []}


# ---------- live stock and sales from Amazon (SP-API) ----------
LIVE_REPEAT_S = 12 * 3600          # the same count from Amazon is not stored again within 12 hours
SALES_DAYS = 35


def live_count(conn, card_id, market, units, settings, now=None):
    """Amazon's FBA stock of a card's product in one market, as a count on the shipment selling now. None when the
    card has no shipment there or the count did not change in the last 12 hours."""
    now = int(now or time.time())
    c = _card(conn, card_id)
    o, ln = _current_line(conn, _orders(conn, card_id), market)
    if not ln:
        return None
    last = conn.execute('SELECT ts, units_left FROM board_stock WHERE line_id=? ORDER BY ts DESC, id DESC LIMIT 1',
                        (ln['id'],)).fetchone()
    if last and last[1] == units and now - last[0] < LIVE_REPEAT_S:
        return None
    return add_count(conn, ln['id'], units, settings, now, 'spapi') | {'card': c['title']}


def save_sales(conn, card_id, market, days, now=None):
    """Amazon's units ordered a day [('YYYY-MM-DD', units)] for a card in one market (a day read again replaces it)."""
    now = int(now or time.time())
    for d, u in days or ():
        conn.execute('INSERT OR REPLACE INTO board_sales(card_id, market, day, units, ts) VALUES (?,?,?,?,?)',
                     (card_id, market, d, int(u), now))
    return len(days or ())


def sync_live(db, client, settings, now=None, card_id=None):
    """Fill the Re-order stage from the SP-API: units left (FBA stock that is ready to sell) and the daily sales of every
    card with an ASIN, in each market its keys cover. Blocking (the SP-API is plain HTTP): run it in a thread.
    Returns {'cards', 'counts', 'sales_days', 'alerts', 'errors'}."""
    import spapi
    now = int(now or time.time())
    out = {'cards': 0, 'counts': 0, 'sales_days': 0, 'alerts': [], 'errors': []}
    if client is None:
        return out
    cards = db.read(lambda conn: [dict(r) for r in conn.execute(
        'SELECT id, asin, market FROM board_card WHERE archived_at IS NULL AND asin IS NOT NULL' +
        (' AND id=?' if card_id else ''), (card_id,) if card_id else ())])
    if not cards:
        return out
    by_mk = {}
    for c in cards:
        mks = db.read(lambda conn, i=c['id']: [ln['market'] for o in _orders(conn, i) for ln in o['lines']]) or [c['market']]
        for mk in dict.fromkeys(mks):
            if client.has(mk):
                by_mk.setdefault(mk, []).append(c)
    for mk, cs in by_mk.items():
        try:
            stock = client.inventory_by_asin(mk)
        except spapi.SpApiError as e:
            out['errors'].append('%s stock: %s' % (_name(mk), e))
            stock = {}
        from zoneinfo import ZoneInfo
        today = remind.today(now, ZoneInfo(config.MARKET_TZ[mk]))
        interval = spapi.day_interval(today - dt.timedelta(days=SALES_DAYS), today, config.MARKET_TZ[mk])
        for c in cs:
            out['cards'] += 1
            inv = stock.get(c['asin'])
            if inv is not None:
                r = db.write(live_count, c['id'], mk, inv['fulfillable'], settings, now)
                if r:
                    out['counts'] += 1
                    out['alerts'] += r['alerts']
            try:
                days = spapi.parse_order_metrics(client.order_metrics(mk, interval, config.MARKET_TZ[mk], asin=c['asin']))
                out['sales_days'] += db.write(save_sales, c['id'], mk, days, now)
            except spapi.SpApiError as e:
                out['errors'].append('%s sales: %s' % (_name(mk), e))
    if out['counts'] or out['sales_days']:
        bump()
    return out


def remove_count(conn, count_id, now=None):
    """Delete a stock count typed by mistake, so it no longer bends the selling pace."""
    now = int(now or time.time())
    r = conn.execute('SELECT s.ts, s.units_left, l.market, o.card_id FROM board_stock s JOIN board_line l ON l.id=s.line_id '
                     'JOIN board_order o ON o.id=l.order_id WHERE s.id=?', (count_id,)).fetchone()
    if not r:
        raise BoardError('That count is not on the board.')
    conn.execute('DELETE FROM board_stock WHERE id=?', (count_id,))
    _event(conn, r['card_id'], 'stock', '%s: the count of %s on %s was deleted' % (
        _name(r['market']), '{:,}'.format(r['units_left']), remind.nice_day(dt.date.fromtimestamp(r['ts']).isoformat())), now)
    return {'id': r['card_id']}


def _stock_alerts(conn, c, s, now):
    """Low stock and re-order alerts for one shipment, each at most once."""
    found = []
    where = 'in %s' % _name(s['market'])
    if s['level'] == 'red':
        found.append({'rule': 'board_stock_low', 'severity': 3, 'market': s['market'], 'target': 'board',
                      'title': 'Low stock: %s %s' % (c['title'][:60], where),
                      'body': '%s of %s left (%d%%).%s' % ('{:,}'.format(s['left']), '{:,}'.format(s['sent']), round(s['pct']),
                                                           ' Re-order by %s.' % remind.nice_day(s['reorder_by'])
                                                           if s['reorder_by'] else ''),
                      'data': {'card_id': c['id']}, 'dedupe_key': 'board_stock|%d' % s['line_id'], 'ts': now})
    if s['order_now']:
        found.append({'rule': 'board_reorder', 'severity': 3, 'market': s['market'], 'target': 'board',
                      'title': 'Re-order now: %s %s' % (c['title'][:60], where),
                      'body': 'At the current pace the stock runs out on %s; a new shipment takes about %d days.'
                              % (remind.nice_day(s['sellout']), s['lead_days']),
                      'data': {'card_id': c['id']}, 'dedupe_key': 'board_reorder|%d' % s['line_id'], 'ts': now})
    return [a for a in found if storage.add_alerts(conn, [a])]


# ---------- approval ----------
def _approval(c):
    return c['data'].setdefault('approval', {'items': []})


def set_check(conn, card_id, item_id, done, settings=None, now=None):
    """Tick or untick one approval check. When every check is ticked the product is approved: a trophy, and it moves on
    to Sourcing & PO. After approval the checklist stays as it was."""
    now = int(now or time.time())
    c = _card(conn, card_id)
    if c['data']['milestones'].get('approved'):
        raise BoardError('This product is already approved.')
    item = next((i for i in _approval(c)['items'] if i['id'] == item_id), None)
    if not item:
        raise BoardError('That check is not on this product.')
    item['done'] = now if done else None
    _save(conn, c, now)
    return {'id': card_id, **_approve_if_done(conn, card_id, now)}


def add_check(conn, card_id, text, now=None):
    now = int(now or time.time())
    c = _card(conn, card_id)
    if c['data']['milestones'].get('approved'):
        raise BoardError('This product is already approved.')
    text = _text(text, 160)
    if not text:
        raise BoardError('Write what should be checked.')
    items = _approval(c)['items']
    if len(items) >= 20:
        raise BoardError('At most 20 checks.')
    items.append({'id': max([i['id'] for i in items] or [0]) + 1, 'text': text, 'done': None})
    _save(conn, c, now)
    return {'id': card_id}


def remove_check(conn, card_id, item_id, now=None):
    now = int(now or time.time())
    c = _card(conn, card_id)
    if c['data']['milestones'].get('approved'):
        raise BoardError('This product is already approved.')
    a = _approval(c)
    if len(a['items']) <= 1:
        raise BoardError('Keep at least one check.')
    a['items'] = [i for i in a['items'] if i['id'] != item_id]
    _save(conn, c, now)
    return {'id': card_id, **_approve_if_done(conn, card_id, now)}


def _approve_if_done(conn, card_id, now):
    c = _card(conn, card_id)
    ms = c['data']['milestones']
    if ms.get('approved') or not ms.get('idea_done') or not rules.approval_status(_approval(c)['items'])['complete']:
        return {'trophies': []}
    ms['approved'] = now
    _finish(conn, card_id, 'approval', now)
    if c['stage'] in ('precheck', 'approval'):          # every check ticked: the pre-check is passed too
        c['stage'] = 'sourcing'
        c['pos'] = conn.execute("SELECT COALESCE(MAX(pos), 0) + 1 FROM board_card WHERE stage='sourcing'").fetchone()[0]
    _save(conn, c, now)
    _event(conn, card_id, 'trophy', 'Approved', now)
    for mk in {c['market']} | {ln['market'] for o in _orders(conn, card_id) for ln in o['lines']}:
        _watch(conn, c['kw'], mk, 'sourcing', now)
    return {'trophies': ['Approved']}


# ---------- milestones ----------
def _idea_if_done(conn, card_id, now):
    """An idea with its Amazon ASIN and Alibaba link is done: it moves on to Pre-check (you move it to Approval when
    the pre-check is done), and straight on to Sourcing when every approval check was ticked already. Returns
    {'moved': the stage it moved to or None, 'trophies'}."""
    c = _card(conn, card_id)
    ms = c['data']['milestones']
    if ms.get('idea_done') or not rules.idea_status(c['asin'], (c['data'].get('links') or {}).get('alibaba'))['complete']:
        return {'moved': None, 'trophies': []}
    ms['idea_done'] = now
    _finish(conn, card_id, 'idea', now)
    moved = None
    if c['stage'] == 'idea':
        c['stage'], moved = 'precheck', 'precheck'
        c['pos'] = conn.execute("SELECT COALESCE(MAX(pos), 0) + 1 FROM board_card WHERE stage='precheck'").fetchone()[0]
        _event(conn, card_id, 'moved', 'Moved to Pre-check: the Amazon ASIN and the Alibaba link are in', now)
    _save(conn, c, now)
    _watch(conn, c['kw'], c['market'], 'researching', now)
    won = _approve_if_done(conn, card_id, now)['trophies']
    return {'moved': 'sourcing' if won and _card(conn, card_id)['stage'] == 'sourcing' else moved, 'trophies': won}


def _finish(conn, card_id, stage, now):
    """A stage is done: its deadline is met and its scheduled emails are cancelled."""
    for t in conn.execute('SELECT id FROM board_task WHERE card_id=? AND stage=? AND done_at IS NULL AND cancelled_at IS NULL',
                          (card_id, stage)).fetchall():
        conn.execute('UPDATE board_task SET done_at=? WHERE id=?', (now, t[0]))
        conn.execute("UPDATE board_mail SET status='cancelled', error='the stage is done' WHERE task_id=? AND status='scheduled'",
                     (t[0],))


def _settle(conn, card_id, settings, now):
    """Hand out the trophies a card has earned and move it on: Order ready opens Launching strategy, Launched opens
    Re-order, every re-order that is complete counts. Returns {'trophies': [names]}."""
    c, per_usd = _card(conn, card_id), _per_usd()
    ms, won = c['data']['milestones'], []
    for o in _orders(conn, card_id):
        if o['ready_at'] or not rules.sourcing_status(o, o['lines'], per_usd)['complete']:
            continue
        conn.execute('UPDATE board_order SET ready_at=? WHERE id=?', (now, o['id']))
        if o['n'] == 1:
            ms['order_ready'] = now
            won.append('Order ready')
            _finish(conn, card_id, 'sourcing', now)
            for ln in o['lines']:
                _watch(conn, c['kw'], ln['market'], 'ordered', now)
            if c['stage'] == 'sourcing':
                c['stage'] = 'launch'
        else:
            ms['reorders'] = int(ms.get('reorders') or 0) + 1
            won.append('Re-order #%d ready' % o['n'])
            _finish(conn, card_id, 'reorder', now)
    if ms.get('order_ready') and not ms.get('launched') and \
            rules.launch_status(_hints(conn, card_id), c['data']['live'])['complete']:
        ms['launched'] = now
        won.append('Launched')
        _finish(conn, card_id, 'launch', now)
        for mk in c['data']['live']:
            _watch(conn, c['kw'], mk, 'launched', now)
        if c['stage'] in ('sourcing', 'launch'):
            c['stage'] = 'reorder'
    for w in won:
        _event(conn, card_id, 'trophy', w, now)
    if won:
        _save(conn, c, now)
    return {'trophies': won}


# ---------- hints ----------
def research(conn, card, mk, settings, landed, now):
    """What the app knows about the product's keyword in one market, for engine.hints (complaint themes are added
    by the caller). landed: engine.board.line_cost of this market's shipment."""
    n = reports.niche_inputs(conn, mk, card['kw'], settings)
    base = {'market': mk, 'market_name': _name(mk), 'kw': card['kw'], 'cur': ac.MARKETS[mk]['currency']}
    if not n:
        return {**base, 'searched': False}
    prices = sorted(r['price'] for r in n['summary']['rows'] if not r.get('sponsored') and r.get('price'))
    q = (lambda f: prices[min(len(prices) - 1, int(f * len(prices)))]) if prices else (lambda f: None)
    unit, ship = (landed or {}).get('unit_usd'), (landed or {}).get('ship_unit_usd')
    econ = None
    if n['price'] and unit:
        size = reports.size_suggestion(conn, mk, card['kw'])
        inp = {'market': mk, 'price': n['price'], 'cogs_usd': unit, 'category': '',
               'registered': bool((settings.get('registered') or {}).get(mk)), 'date': config.market_today(mk).isoformat()}
        if ship is not None:
            inp['freight_per_unit_usd'] = ship
        if size.get('dims_cm'):
            inp['dims_cm'] = size['dims_cm']
        if size.get('kg'):
            inp['kg'] = size['kg']
        econ = profit.unit_economics(inp)
    who = reports._who_sells(conn, mk, card['kw'], now)
    tf = n['trend'] or {}
    season = tf.get('seasonality') or {}
    if season.get('si') and season.get('reliable'):
        plan = planner.plan(season.get('si'), season.get('strength'), season.get('reliable'), season.get('peak_month'),
                            config.market_today(mk).isoformat(), market=mk)
    else:
        plan = {'status': 'flat'} if tf.get('label') and tf.get('label') != 'Insufficient' else None
    phrases = (storage.suggestions(conn, mk, [card['kw']]).get(card['kw']) or {}).get('keywords') or []
    return {**base, 'searched': True, 'checked_at': n['summary'].get('checked_at'), 'price': n['price'],
            'p25': q(.25), 'p75': q(.75), 'econ': econ, 'review_barrier': n['metrics'].get('review_barrier'),
            'fast_local': n['metrics'].get('fast_local'), 'takeaway': who.get('takeaway') if who.get('searched') else None,
            'phrases': phrases[:5], 'plan': plan, 'trend': {'label': tf.get('label')} if tf.get('label') else None,
            'newcomer': lch.newcomer_units((n['summary'].get('per_seller') or {}).get('per_seller'),
                                           n['metrics'].get('review_barrier'), mk)}


def refresh_hints(db, card_id, settings, now=None):
    """Rebuild a card's hints from the newest research. Your status and notes stay; a hint whose numbers moved goes
    back to To check."""
    now = int(now or time.time())
    card, orders = db.read(_card, card_id), db.read(_orders, card_id)
    markets = _markets(orders, card)
    themes = {mk: reports.reviews_for(db, mk, kw=card['kw']).get('themes') or [] for mk in markets}
    per_usd = _per_usd()

    def build(conn):
        st = rules.sourcing_status(orders[0], orders[0]['lines'], per_usd) if orders else {'markets': {}}
        out = {}
        for mk in markets:
            r = research(conn, card, mk, settings, st['markets'].get(mk), now)
            r['themes'] = themes[mk]
            out[mk] = hintmod.make(r)
        return out
    return db.write(_store_hints, card_id, db.read(build), settings, now)


def _store_hints(conn, card_id, fresh, settings, now):
    have = {(h['market'], h['key']): h for h in _hints(conn, card_id) if not h['custom']}
    keep = set()
    for mk, hs in fresh.items():
        for h in hs:
            keep.add((mk, h['key']))
            old = have.get((mk, h['key']))
            if not old:
                conn.execute('INSERT INTO board_hint(card_id, market, key, cat, text, data_json, recheck_days, updated_at) '
                             'VALUES (?,?,?,?,?,?,?,?)', (card_id, mk, h['key'], h['cat'], h['text'], json.dumps(h['data']),
                                                          h['recheck_days'], now))
                continue
            mv = hintmod.moved(h['key'], old['data'], h['data'])
            back = mv and old['status'] in rules.HANDLED
            conn.execute('UPDATE board_hint SET cat=?, text=?, data_json=?, recheck_days=?, updated_at=?, status=?, '
                         'moved_json=? WHERE id=?',
                         (h['cat'], h['text'], json.dumps(h['data']), h['recheck_days'], now,
                          'todo' if back else old['status'],
                          json.dumps(dict(mv, at=now)) if back else json.dumps(old['moved']) if old['moved'] else None,
                          old['id']))
    for k, h in have.items():
        if k not in keep:
            conn.execute('DELETE FROM board_hint WHERE id=?', (h['id'],))
    return {'id': card_id, **_settle(conn, card_id, settings, now)}


def set_hint(conn, hint_id, status, note, settings, now=None):
    now = int(now or time.time())
    h = conn.execute('SELECT * FROM board_hint WHERE id=?', (hint_id,)).fetchone()
    if not h:
        raise BoardError('That hint is gone: refresh the card.')
    if status not in STATUSES:
        raise BoardError('Unknown status.')
    note = _text(note, 400) if note is not None else h['note']
    if status == 'adjusted' and not note:
        raise BoardError('Say what you do differently.')
    conn.execute('UPDATE board_hint SET status=?, note=?, checked_at=?, moved_json=? WHERE id=?',
                 (status, note, now if status != 'todo' else None, h['moved_json'] if status == 'todo' else None, hint_id))
    _event(conn, h['card_id'], 'hint', 'Launch checklist updated', now, merge=True)
    return {'id': h['card_id'], **_settle(conn, h['card_id'], settings, now)}


def add_hint(conn, card_id, market, text, now=None):
    now = int(now or time.time())
    _card(conn, card_id)
    t = _text(text, 400)
    if not t:
        raise BoardError('Write the hint first.')
    conn.execute('INSERT INTO board_hint(card_id, market, key, cat, text, data_json, custom, updated_at) '
                 "VALUES (?,?,?,?,?,'{}',1,?)", (card_id, _market(market), 'own-%d-%d' % (now, time.perf_counter_ns() % 10**6),
                                                'own', t, now))
    return {'id': card_id}


def remove_hint(conn, hint_id):
    h = conn.execute('SELECT card_id, custom FROM board_hint WHERE id=?', (hint_id,)).fetchone()
    if not h or not h['custom']:
        raise BoardError('Only your own hints can be removed.')
    conn.execute('DELETE FROM board_hint WHERE id=?', (hint_id,))
    return {'id': h['card_id']}


# ---------- people to notify ----------
def notifiers(conn):
    return [dict(r) for r in conn.execute('SELECT id, name, email, added_at FROM board_notifier WHERE removed_at IS NULL '
                                          'ORDER BY name COLLATE NOCASE, email')]


def add_notifier(conn, name, email, now=None):
    now = int(now or time.time())
    e = str(email or '').strip()
    if len(e) > 254 or not config.EMAIL_RX.match(e):
        raise BoardError('That is not an email address.')
    n = re.sub(r'[\r\n<>"]', '', str(name or '')).strip()[:80] or e.split('@')[0]
    conn.execute('INSERT INTO board_notifier(name, email, added_at) VALUES (?,?,?) ON CONFLICT(email) DO UPDATE SET '
                 'name=excluded.name, removed_at=NULL', (n, e, now))
    return notifiers(conn)


def remove_notifier(conn, notifier_id, now=None):
    conn.execute('UPDATE board_notifier SET removed_at=? WHERE id=?', (int(now or time.time()), notifier_id))
    return notifiers(conn)


# ---------- deadlines and their emails ----------
def set_task(conn, card_id, stage, d, settings, now=None, tz=None):
    """A deadline for one stage of a card: days to complete (or a due day), who to email (ids from the notifier list),
    a note and the update meeting's time. Replaces the stage's earlier deadline and its unsent emails."""
    now = int(now or time.time())
    c = _card(conn, card_id)
    if stage not in rules.STAGES:
        raise BoardError('Unknown stage.')
    start = remind.today(now, tz)
    days = _number(d.get('days'), 1, 365, 'Days to complete', whole=True)
    due = _when(d.get('due_day'), 'Due day')
    if days is None and due:
        days = (rules.day(due) - start).days
        if days < 1:
            raise BoardError('The due day must be after today.')
    if not days:
        raise BoardError('How many days to complete it?')
    active = {n['id']: n for n in notifiers(conn)}
    notify = [int(x) for x in d.get('notify') or [] if str(x).isdigit() and int(x) in active]
    meet_time = d.get('meet_time') or settings.get('mail_meet_time') or '15:00'
    if not re.match(r'^(?:[01]\d|2[0-3]):[0-5]\d$', str(meet_time)):
        raise BoardError('Meeting time must look like 15:00.')
    minutes = _number(d.get('meet_minutes') or settings.get('mail_meet_minutes') or 30, 15, 180, 'Meeting length', whole=True)
    note = _text(d.get('note'), 1000)
    for t in conn.execute('SELECT id FROM board_task WHERE card_id=? AND stage=? AND done_at IS NULL AND cancelled_at IS NULL',
                          (card_id, stage)).fetchall():
        _cancel(conn, t[0], now, 'replaced by a new deadline')
    sch = remind.schedule(start, days, now, settings.get('mail_send_hour', 9), settings.get('mail_remind_days', 3),
                          settings.get('mail_start_email', True), meet_time, tz)
    tid = conn.execute('INSERT INTO board_task(card_id, stage, start_day, days, due_day, note, notify_json, meet_time, '
                       'meet_minutes, meet_start, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                       (card_id, stage, start.isoformat(), days, sch['due_day'], note, json.dumps(notify), meet_time, minutes,
                        sch['meet_start'], now)).lastrowid
    for kind, ts in sch['mails'] if notify else []:
        conn.execute('INSERT INTO board_mail(task_id, kind, send_at) VALUES (?,?,?)', (tid, kind, ts))
    who = [active[i]['name'] for i in notify]
    _event(conn, card_id, 'task', '%s due %s%s' % (rules.STAGE_NAMES[stage], remind.nice_day(sch['due_day']),
                                                   '; emails to ' + remind.join_names(who) if who else ''), now)
    return {'id': card_id, 'task_id': tid, 'due_day': sch['due_day'], 'mails': len(sch['mails']) if notify else 0}


def _cancel(conn, task_id, now, why):
    conn.execute('UPDATE board_task SET cancelled_at=? WHERE id=?', (now, task_id))
    conn.execute("UPDATE board_mail SET status='cancelled', error=? WHERE task_id=? AND status='scheduled'", (why, task_id))


def cancel_task(conn, task_id, now=None):
    now = int(now or time.time())
    t = conn.execute('SELECT card_id, stage FROM board_task WHERE id=?', (task_id,)).fetchone()
    if not t:
        raise BoardError('That deadline is gone.')
    _cancel(conn, task_id, now, 'deadline removed')
    _event(conn, t['card_id'], 'task', 'Deadline for %s removed' % rules.STAGE_NAMES[t['stage']], now)
    return {'id': t['card_id']}


def move_task(conn, task_id, due_day, settings, now=None, tz=None):
    """A deadline's new due day (dragged on the timeline): the same people, note and meeting; its reminder and meeting
    emails not sent yet move with it, and a 'new task' email that already went is not sent again."""
    now = int(now or time.time())
    t = conn.execute('SELECT * FROM board_task WHERE id=? AND done_at IS NULL AND cancelled_at IS NULL', (task_id,)).fetchone()
    if not t:
        raise BoardError('That deadline is done or gone.')
    due = rules.day(_when(due_day, 'Due day'))
    if not due or due <= remind.today(now, tz):
        raise BoardError('The due day must be after today.')
    days = (due - rules.day(t['start_day'])).days
    if days < 1:
        raise BoardError('The due day must be after the day the deadline was set.')
    sch = remind.schedule(t['start_day'], days, now, settings.get('mail_send_hour', 9), settings.get('mail_remind_days', 3),
                          False, t['meet_time'], tz)
    conn.execute("UPDATE board_mail SET status='cancelled', error='the due day moved' WHERE task_id=? AND status='scheduled' "
                 "AND kind!='start'", (task_id,))
    if json.loads(t['notify_json'] or '[]'):
        for kind, ts in sch['mails']:
            conn.execute('INSERT INTO board_mail(task_id, kind, send_at) VALUES (?,?,?)', (task_id, kind, ts))
    conn.execute('UPDATE board_task SET days=?, due_day=?, meet_start=? WHERE id=?', (days, sch['due_day'], sch['meet_start'], task_id))
    _event(conn, t['card_id'], 'task', '%s now due %s' % (rules.STAGE_NAMES[t['stage']], remind.nice_day(sch['due_day'])), now)
    return {'id': t['card_id'], 'due_day': sch['due_day']}


def send_now(conn, task_id, now=None):
    """'Send reminder now': the scheduled reminder goes out at the next send (a new one if none is waiting)."""
    now = int(now or time.time())
    t = conn.execute('SELECT * FROM board_task WHERE id=? AND done_at IS NULL AND cancelled_at IS NULL', (task_id,)).fetchone()
    if not t:
        raise BoardError('That deadline is done or gone.')
    if not json.loads(t['notify_json'] or '[]'):
        raise BoardError('Pick who to email first.')
    m = conn.execute("SELECT id FROM board_mail WHERE task_id=? AND kind='reminder' AND status='scheduled'", (task_id,)).fetchone()
    if m:
        conn.execute('UPDATE board_mail SET send_at=? WHERE id=?', (now, m[0]))
    else:
        conn.execute("INSERT INTO board_mail(task_id, kind, send_at) VALUES (?, 'reminder', ?)", (task_id, now))
    return {'id': t['card_id']}


def _todo(conn, card, stage, settings, per_usd, now):
    """What is still open in a stage, in plain lines for the emails."""
    if stage in ('idea', 'precheck'):
        return rules.idea_status(card['asin'], (card['data'].get('links') or {}).get('alibaba'))['missing']
    orders = _orders(conn, card['id'])
    if stage == 'sourcing':
        return rules.sourcing_status(orders[0], orders[0]['lines'], per_usd)['missing'] if orders else []
    if stage == 'launch':
        hs = _hints(conn, card['id'])
        open_ = ['%s: %s' % (_name(h['market']), h['text']) for h in hs if h['status'] not in rules.HANDLED][:6]
        return open_ + [m for m in rules.launch_status(hs, card['data']['live'])['missing'] if 'live' in m]
    out = []
    for o in orders[1:]:
        if not o['ready_at']:
            out += ['re-order #%d: %s' % (o['n'], m) for m in rules.sourcing_status(o, o['lines'], per_usd)['missing']]
    today = dt.date.fromtimestamp(now)
    for mk in _markets(orders, card):
        s = _stock(conn, card, orders, mk, settings, today)
        if s and s['left'] is not None and (s['level'] == 'red' or s['order_now']):
            out.append('%s: %s of %s left (%d%%)%s' % (_name(mk), '{:,}'.format(s['left']), '{:,}'.format(s['sent']),
                                                       round(s['pct']), ', re-order by %s' % remind.nice_day(s['reorder_by'])
                                                       if s['reorder_by'] else ''))
    return out


def mail_context(conn, mail_id, settings, now, per_usd, tz=None):
    """Everything one email needs, built from the board as it is now. {'cancel': why} when it should not go."""
    m = conn.execute('SELECT * FROM board_mail WHERE id=?', (mail_id,)).fetchone()
    t = conn.execute('SELECT * FROM board_task WHERE id=?', (m['task_id'],)).fetchone() if m else None
    if not t or t['done_at'] or t['cancelled_at']:
        return {'cancel': 'the deadline is done or gone'}
    c = _card(conn, t['card_id'])
    if c['archived_at']:
        return {'cancel': 'product removed from the board'}
    people = {n['id']: n for n in notifiers(conn)}
    to = [(people[i]['name'], people[i]['email']) for i in json.loads(t['notify_json'] or '[]') if i in people]
    owner = _owner(settings)
    ctx = {'title': c['title'], 'stage': rules.STAGE_NAMES[t['stage']], 'due_day': t['due_day'], 'days': t['days'],
           'todo': _todo(conn, c, t['stage'], settings, per_usd, now), 'note': t['note'], 'to_names': [n for n, _ in to],
           'owner': owner, 'now': now, 'meet_start': t['meet_start'], 'meet_minutes': t['meet_minutes'],
           'link': settings.get('mail_meet_link') or None}
    subject, text, page = remind.texts(m['kind'], ctx, tz)
    ics = None
    if m['kind'] == 'meeting':
        ics = remind.ics('pc-board-task-%d@product-checker' % t['id'], t['meet_start'], t['meet_minutes'], subject, text,
                         (owner, settings.get('mail_address') or ''), to, ctx['link'], stamp=now)
    return {'mail': dict(m), 'card_id': c['id'], 'kind': m['kind'], 'to': to, 'subject': subject, 'text': text,
            'html': page, 'ics': ics, 'due_day': t['due_day'], 'meet_start': t['meet_start']}


def preview(conn, task_id, settings, now=None, tz=None):
    """The emails of a deadline as they would read now: [{'kind', 'send_at', 'status', 'subject', 'text'}]."""
    now = int(now or time.time())
    per_usd = _per_usd()
    out = []
    for m in conn.execute('SELECT id, kind, send_at, status FROM board_mail WHERE task_id=? ORDER BY send_at, id', (task_id,)):
        ctx = mail_context(conn, m['id'], settings, now, per_usd, tz)
        out.append({'kind': m['kind'], 'send_at': m['send_at'], 'status': m['status'],
                    'subject': ctx.get('subject'), 'text': ctx.get('text') or ctx.get('cancel')})
    return out


def _mail_done(conn, mail_id, status, now, error=None):
    conn.execute('UPDATE board_mail SET status=?, error=? WHERE id=?', (status, error, mail_id))


def _mail_sent(conn, ctx, now, how=None):
    """how: what the sender did, 'sent', 'opened' (ready to send in your browser) or 'calendar' (a Google Calendar
    event to save); None counts as sent."""
    m = ctx['mail']
    opened = how in ('opened', 'calendar')
    conn.execute('UPDATE board_mail SET status=?, sent_at=?, attempts=attempts+1, error=NULL, to_json=?, subject=? '
                 'WHERE id=?', ('opened' if opened else 'sent', now, json.dumps([e for _, e in ctx['to']]),
                                ctx['subject'], m['id']))
    who = remind.join_names([n for n, _ in ctx['to']])
    text = ('%s opened in Google Calendar: press Save to invite %s' if how == 'calendar' else
            '%s opened in your browser: press Send to email %s' if opened else '%s emailed to %s')
    _event(conn, ctx['card_id'], 'mail', text % (KIND_NAMES[m['kind']], who), now)


def _mail_failed(conn, ctx, error, now):
    """A failed email is tried again twice; the last failure is reported on Home. Returns new alerts."""
    m = ctx['mail']
    tries = m['attempts'] + 1
    if tries < MAX_TRIES:
        conn.execute('UPDATE board_mail SET attempts=?, error=?, send_at=? WHERE id=?',
                     (tries, error, now + RETRY_S[tries - 1], m['id']))
        return []
    conn.execute("UPDATE board_mail SET status='failed', attempts=?, error=? WHERE id=?", (tries, error, m['id']))
    _event(conn, ctx['card_id'], 'mail', '%s could not be sent: %s' % (KIND_NAMES[m['kind']], error), now)
    a = {'rule': 'board_mail_failed', 'severity': 2, 'market': None, 'target': 'board',
         'title': '%s not sent: %s' % (KIND_NAMES[m['kind']], ctx['subject'][:80]), 'body': error,
         'data': {'card_id': ctx['card_id']}, 'dedupe_key': 'board_mail|%d' % m['id'], 'ts': now}
    return [a] if storage.add_alerts(conn, [a]) else []


def _mail_missed(conn, ctx, now):
    m = ctx['mail']
    conn.execute("UPDATE board_mail SET status='missed', error='too late to send' WHERE id=?", (m['id'],))
    _event(conn, ctx['card_id'], 'mail', '%s was not sent: the PC was off when it was due' % KIND_NAMES[m['kind']], now)
    a = {'rule': 'board_mail_missed', 'severity': 2, 'market': None, 'target': 'board',
         'title': '%s missed: %s' % (KIND_NAMES[m['kind']], ctx['subject'][:80]),
         'body': 'Product Checker was not running when it was due, and now it is too late to send.',
         'data': {'card_id': ctx['card_id']}, 'dedupe_key': 'board_mail|%d' % m['id'], 'ts': now}
    return [a] if storage.add_alerts(conn, [a]) else []


def _all_stock_alerts(conn, settings, now):
    """Re-order days that came while nobody counted (a forecast ages): new alerts, each at most once."""
    out, today = [], dt.date.fromtimestamp(now)
    for (cid,) in conn.execute('SELECT id FROM board_card WHERE archived_at IS NULL').fetchall():
        c = _card(conn, cid)
        if not c['data']['milestones'].get('order_ready'):
            continue
        orders = _orders(conn, cid)
        for mk in _markets(orders, c):
            s = _stock(conn, c, orders, mk, settings, today)
            if s and s['left'] is not None:
                out += _stock_alerts(conn, c, s, now)
    return out


def tick(db, settings, send, now=None, tz=None, inform_ok=True):
    """Send the reminder emails that are due, raise the stock alerts whose day came and send the automatic product
    update once its waiting time is over (inform_ok: the Gmail is set up). send(to, subject, text, html, ics) raises on
    failure (mailer.deliver) and may return what it did ('sent', 'opened' or 'calendar', see _mail_sent). Returns
    {'sent', 'failed', 'missed', 'alerts' (new on Home), 'next' (when to look again for the product update, or None)}."""
    out = {'sent': 0, 'failed': 0, 'missed': 0, 'alerts': [], 'next': None}
    if not _send_lock.acquire(blocking=False):
        return out
    try:
        now = int(now or time.time())
        per_usd = _per_usd()
        due = db.read(lambda c: [r[0] for r in c.execute("SELECT id FROM board_mail WHERE status='scheduled' AND send_at<=? "
                                                         'ORDER BY send_at, id', (now,))])
        for mid in due:
            ctx = db.read(mail_context, mid, settings, now, per_usd, tz)
            if ctx.get('cancel'):
                db.write(_mail_done, mid, 'cancelled', now, ctx['cancel'])
            elif not remind.still_useful(ctx['kind'], ctx['due_day'], ctx['meet_start'], now, tz):
                out['missed'] += 1
                out['alerts'] += db.write(_mail_missed, ctx, now)
            elif not ctx['to']:
                db.write(_mail_done, mid, 'cancelled', now, 'nobody to email')
            else:
                try:
                    how = send(ctx['to'], ctx['subject'], ctx['text'], ctx['html'], ctx['ics'])
                except Exception as e:                       # noqa: BLE001 - reported on the card and on Home
                    out['failed'] += 1
                    out['alerts'] += db.write(_mail_failed, ctx, str(e)[:300] or e.__class__.__name__, now)
                else:
                    out['sent'] += 1
                    db.write(_mail_sent, ctx, now, how)
        out['alerts'] += db.write(_all_stock_alerts, settings, now)
        out['next'], more = _inform_auto(db, settings, send, now, inform_ok)
        out['alerts'] += more
        if due or out['alerts']:
            bump()
        return out
    finally:
        _send_lock.release()


def pending_mail(db):
    return db.read(lambda c: c.execute("SELECT COUNT(*) FROM board_mail WHERE status='scheduled'").fetchone()[0])


# ---------- the product update email (Inform attendees) ----------
def _owner(settings):
    return settings.get('mail_from_name') or (settings.get('mail_address') or '').split('@')[0] or 'Product Checker'


def _inform_item(conn, c):
    """One product as the product update shows it: its newest order's units and markets, when its sourcing is due
    (the deadline of Sourcing & PO, or Re-order for a re-order) and links to Amazon and Alibaba."""
    orders = _orders(conn, c['id'])
    o = orders[-1] if orders else None
    lines = o['lines'] if o else []
    if o and o['ready_at']:
        src = {'state': 'done', 'day': dt.date.fromtimestamp(o['ready_at']).isoformat()}
    else:
        t = _tasks(conn, c['id']).get('sourcing' if not o or o['n'] == 1 else 'reorder')
        src = {'state': 'due', 'day': t['due_day']} if t and not t['done_at'] else {'state': 'none', 'day': None}
    at = []
    for ln in lines:
        eta = _eta(ln)
        if rules.day(ln['steps'].get('arrived')):
            at.append([ln['market'], 'arrived:' + rules.day(ln['steps']['arrived']).isoformat()])
        elif eta:
            at.append([ln['market'], 'eta:' + eta])
    amazon, a_search = inform.amazon_link(ac.MARKETS.get(c['market'], ac.MARKETS['US'])['site'], c['asin'], c['kw'])
    alibaba, b_search = inform.alibaba_link((c['data'].get('links') or {}).get('alibaba'), c['kw'])
    return {'id': c['id'], 'title': c['title'], 'image': c['image'], 'stage': c['stage'], 'tags': c['data'].get('tags') or [],
            'order': 'First order' if not o or o['n'] == 1 else 'Re-order #%d' % o['n'],
            'units': [[ln['market'], ln['units']] for ln in lines], 'sourcing': src, 'at_amazon': at,
            'amazon': amazon, 'amazon_search': a_search, 'alibaba': alibaba, 'alibaba_search': b_search}


def inform_items(conn):
    """Every product on the board for the product update, in the email's stage order (Sourcing & PO, Pre-check, ...)."""
    cards = [_card(conn, r[0]) for r in conn.execute('SELECT id FROM board_card WHERE archived_at IS NULL').fetchall()]
    cards.sort(key=lambda c: (inform.email_rank(c['stage']), c['pos'] or 0, c['id']))
    return [_inform_item(conn, c) for c in cards]


def _inform_mail(conn, settings, to_ids, message, now, auto=False):
    """The product update as it would go now: to everyone on the Notifier list (or the ids given), with what changed
    since the last one."""
    people = notifiers(conn)
    pick = people if to_ids is None else [p for p in people if p['id'] in set(to_ids)]
    last = storage.get_state(conn, INFORM_KEY) or {}
    items = inform_items(conn)
    snap = inform.snapshot(items)
    changed = inform.changes(last['snapshot'], snap) if last.get('snapshot') is not None else []
    since = {'ts': last['sent_at'], 'sent': True} if last.get('sent_at') else {'ts': last['at'], 'sent': False} \
        if last.get('at') else None
    subject, text, page = inform.texts(items, changed, {'owner': _owner(settings), 'to_names': [p['name'] for p in pick],
                                                        'message': message, 'now': now, 'auto': auto, 'since': since})
    return {'to': [(p['name'], p['email']) for p in pick], 'people': people, 'items': items, 'snapshot': snap,
            'changes': changed, 'subject': subject, 'text': text, 'html': page, 'last': last}


def _inform_next(wait, settings):
    return max(wait['since'] + 60 * int(settings.get('board_inform_wait', 15)), wait.get('next_try') or 0)


def inform_view(db, settings, to_ids=None, message='', now=None):
    """The product update as it would go now, when the last one went, and when the automatic one goes if changes
    are waiting."""
    now = int(now or time.time())
    m = db.read(_inform_mail, settings, to_ids, _text(message, 1000), now)
    wait = db.read(storage.get_state, INFORM_WAIT_KEY)
    last, auto = m['last'], bool(settings.get('board_inform_auto', True))
    return {'subject': m['subject'], 'text': m['text'], 'html': m['html'], 'changes': m['changes'], 'count': len(m['items']),
            'to': [n for n, _ in m['to']], 'notifiers': m['people'],
            'last': {'sent_at': last.get('sent_at'), 'to': last.get('to') or [], 'auto': bool(last.get('auto')),
                     'opened': bool(last.get('opened'))},
            'auto': auto, 'wait_min': int(settings.get('board_inform_wait', 15)),
            'next_at': _inform_next(wait, settings) if auto and wait and not wait.get('gave_up') else None,
            'error': (wait or {}).get('error')}


def _inform_sent(conn, m, now, auto, how=None):
    opened = how in ('opened', 'calendar')
    storage.set_state(conn, INFORM_KEY, {'at': now, 'sent_at': now, 'snapshot': m['snapshot'], 'auto': auto,
                                         'to': [n for n, _ in m['to']], 'subject': m['subject'], 'opened': opened})
    storage.set_state(conn, INFORM_WAIT_KEY, None)
    who = remind.join_names([n for n, _ in m['to']])
    text = ('Product update opened in your browser: press Send to email %s' if opened else
            'Product update emailed to %s') % who
    for c in m['changes']:
        if c['kind'] != 'removed':
            _event(conn, int(c['id']), 'mail', text, now)


def _inform_send(db, settings, send, to_ids, message, now, auto=False):
    m = db.read(_inform_mail, settings, to_ids, _text(message, 1000), now, auto)
    if not m['to']:
        raise BoardError('Pick who to email.' if m['people'] else 'Add people to your Notifier list first.')
    if not m['items']:
        raise BoardError('There are no products on the board yet.')
    how = send(m['to'], m['subject'], m['text'], m['html'], None)
    db.write(_inform_sent, m, now, auto, how)
    bump()
    return {'to': [n for n, _ in m['to']], 'subject': m['subject'], 'changes': len(m['changes']),
            'opened': how in ('opened', 'calendar'), 'pasted': bool(getattr(how, 'pasted', False))}


def inform_send(db, settings, send, to_ids=None, message='', now=None):
    """'Inform attendees': email the product update now. send(to, subject, text, html, ics) raises on failure."""
    if not _send_lock.acquire(timeout=120):
        raise BoardError('Emails are going out right now; try again in a moment.')
    try:
        return _inform_send(db, settings, send, to_ids, message, int(now or time.time()))
    finally:
        _send_lock.release()


def _inform_auto(db, settings, send, now, can_send):
    """The automatic product update: when products were added or changed and then nothing changed for
    board_inform_wait minutes, everyone on the Notifier list gets it. The first time, the board as it is becomes the
    starting point. A failed send is tried again twice, then reported on Home. Returns (next look, new alerts)."""
    if not settings.get('board_inform_auto', True):
        return None, []
    last = db.read(storage.get_state, INFORM_KEY)
    snap = inform.snapshot(db.read(inform_items))
    if last is None:
        db.write(storage.set_state, INFORM_KEY, {'at': now, 'sent_at': None, 'snapshot': snap, 'to': []})
        return None, []
    wait = db.read(storage.get_state, INFORM_WAIT_KEY)
    if not inform.changes(last.get('snapshot'), snap):
        if wait:
            db.write(storage.set_state, INFORM_WAIT_KEY, None)
            bump()
        return None, []
    fp = inform.fingerprint(snap)
    if not wait or wait.get('fp') != fp:
        wait = {'fp': fp, 'since': now, 'tries': 0}
        db.write(storage.set_state, INFORM_WAIT_KEY, wait)
        bump()
    if wait.get('gave_up'):
        return None, []
    due = _inform_next(wait, settings)
    if now < due:
        return due, []
    if not can_send or not db.read(notifiers):
        return None, []
    try:
        _inform_send(db, settings, send, None, '', now, auto=True)
    except BoardError:
        return None, []
    except Exception as e:                                       # noqa: BLE001 - tried again, then reported on Home
        tries = wait.get('tries', 0) + 1
        err = str(e)[:300] or e.__class__.__name__
        wait.update(tries=tries, error=err, gave_up=tries >= MAX_TRIES,
                    next_try=now + RETRY_S[min(tries, len(RETRY_S)) - 1])
        db.write(storage.set_state, INFORM_WAIT_KEY, wait)
        bump()
        if tries < MAX_TRIES:
            return wait['next_try'], []
        a = {'rule': 'board_inform_failed', 'severity': 2, 'market': None, 'target': 'board',
             'title': 'The product update email was not sent', 'body': err, 'data': {}, 'dedupe_key': 'board_inform|' + fp,
             'ts': now}
        return None, [a] if db.write(storage.add_alerts, [a]) else []
    return None, []


def inform_waiting(db, settings):
    """Is an automatic product update waiting to go out (so the app should keep running)?"""
    if not settings.get('board_inform_auto', True):
        return False
    wait = db.read(storage.get_state, INFORM_WAIT_KEY)
    return bool(wait and not wait.get('gave_up') and db.read(notifiers))


# ---------- the deadlines email (Inform deadlines) ----------
DEADLINES_KEY = 'board_deadlines'    # ui_state: the last deadlines email {'sent_at', 'to', 'subject', 'opened'}


def deadline_items(conn):
    """Every open deadline on the board, the soonest first, and the products without one: (items, titles)."""
    items, none = [], []
    for r in conn.execute('SELECT id, title, market, data_json FROM board_card WHERE archived_at IS NULL ORDER BY pos, id').fetchall():
        open_ = [t for t in _tasks(conn, r['id']).values() if not t['done_at']]
        tags = json.loads(r['data_json'] or '{}').get('tags') or []
        items += [{'id': r['id'], 'title': r['title'], 'market': r['market'], 'stage': t['stage'], 'due_day': t['due_day'],
                   'note': t['note'], 'tags': tags} for t in open_]
        if not open_:
            none.append(r['title'])
    items.sort(key=lambda i: (i['due_day'], i['title'].lower()))
    return items, none


def _deadlines_mail(conn, settings, to_ids, message, now):
    people = notifiers(conn)
    pick = people if to_ids is None else [p for p in people if p['id'] in set(to_ids)]
    items, none = deadline_items(conn)
    subject, text, page = inform.deadline_texts(items, {'owner': _owner(settings), 'to_names': [p['name'] for p in pick],
                                                        'message': message, 'now': now, 'none': none})
    return {'to': [(p['name'], p['email']) for p in pick], 'people': people, 'items': items, 'none': none,
            'subject': subject, 'text': text, 'html': page, 'last': storage.get_state(conn, DEADLINES_KEY) or {}}


def deadlines_view(db, settings, to_ids=None, message='', now=None):
    """The deadlines email as it would go now, and when the last one went."""
    now = int(now or time.time())
    m = db.read(_deadlines_mail, settings, to_ids, _text(message, 1000), now)
    today, last = remind.today(now), m['last']
    return {'subject': m['subject'], 'text': m['text'], 'html': m['html'], 'count': len(m['items']),
            'late': sum(1 for i in m['items'] if rules.day(i['due_day']) < today), 'none': m['none'],
            'to': [n for n, _ in m['to']], 'notifiers': m['people'],
            'last': {'sent_at': last.get('sent_at'), 'to': last.get('to') or [], 'opened': bool(last.get('opened'))}}


def _deadlines_sent(conn, m, now, opened):
    storage.set_state(conn, DEADLINES_KEY, {'sent_at': now, 'to': [n for n, _ in m['to']], 'subject': m['subject'],
                                            'opened': opened})
    who = remind.join_names([n for n, _ in m['to']])
    text = ('Deadlines email opened in your browser: press Send to email %s' if opened else 'Deadlines emailed to %s') % who
    for cid in dict.fromkeys(i['id'] for i in m['items']):
        _event(conn, cid, 'mail', text, now)


def deadlines_send(db, settings, send, to_ids=None, message='', now=None):
    """'Inform deadlines': email every product's open deadline now, next to the product update and the reminder
    emails each deadline sends. send(to, subject, text, html, ics) raises on failure."""
    if not _send_lock.acquire(timeout=120):
        raise BoardError('Emails are going out right now; try again in a moment.')
    try:
        now = int(now or time.time())
        m = db.read(_deadlines_mail, settings, to_ids, _text(message, 1000), now)
        if not m['to']:
            raise BoardError('Pick who to email.' if m['people'] else 'Add people to your Notifier list first.')
        if not m['items']:
            raise BoardError('No product has a deadline yet: open a product and press Set a deadline.')
        how = send(m['to'], m['subject'], m['text'], m['html'], None)
        opened = how in ('opened', 'calendar')
        db.write(_deadlines_sent, m, now, opened)
        bump()
        return {'to': [n for n, _ in m['to']], 'subject': m['subject'], 'count': len(m['items']), 'opened': opened,
                'pasted': bool(getattr(how, 'pasted', False))}
    finally:
        _send_lock.release()


# ---------- reading the board ----------
def _tasks(conn, card_id):
    out = {}
    for t in conn.execute('SELECT * FROM board_task WHERE card_id=? AND cancelled_at IS NULL ORDER BY created_at, id',
                          (card_id,)):
        t = dict(t)
        t['notify'] = json.loads(t.pop('notify_json') or '[]')
        t['mails'] = [dict(m) for m in conn.execute('SELECT id, kind, send_at, status, sent_at, error, attempts FROM board_mail '
                                                    'WHERE task_id=? ORDER BY send_at, id', (t['id'],))]
        out[t['stage']] = t                                      # the newest deadline of each stage
    return out


def _summary(conn, c, settings, per_usd, now):
    orders, hs = _orders(conn, c['id']), _hints(conn, c['id'])
    today = dt.date.fromtimestamp(now)
    ms = c['data']['milestones']
    idea = rules.idea_status(c['asin'], (c['data'].get('links') or {}).get('alibaba'))
    first = rules.sourcing_status(orders[0], orders[0]['lines'], per_usd) if orders else None
    ls = rules.launch_status(hs, c['data']['live'])
    markets = []
    stocks = []
    for mk in _markets(orders, c):
        s = _stock(conn, c, orders, mk, settings, today) if ms.get('order_ready') else None
        units = next((ln['units'] for ln in orders[0]['lines'] if ln['market'] == mk), None) if orders else None
        markets.append({'market': mk, 'units': units, 'stock': {k: s[k] for k in ('pct', 'level', 'order_now', 'left',
                                                                                     'reorder_by')} if s else None})
        if s:
            stocks.append(s)
    tasks = _tasks(conn, c['id'])
    task = tasks.get(c['stage'])
    nxt = min((m for m in (task or {}).get('mails', []) if m['status'] == 'scheduled'), key=lambda m: m['send_at'],
              default=None)
    etas = [e for e in (_eta(ln) for o in orders for ln in o['lines']) if e]
    due = rules.due_state(task['due_day'], bool(task['done_at']), today) if task else None
    dates = [{'kind': 'reorder', 'day': s['reorder_by'], 'market': s['market']} for s in stocks if s['reorder_by']]
    if task and not task['done_at']:
        dates.append({'kind': 'due', 'day': task['due_day']})
    dates += [{'kind': 'eta', 'day': e} for e in etas]
    dates += [{'kind': 'order_by', 'day': x['day'], 'market': x['market'], 'mode': x['mode']}
              for x in _season_order(conn, c, orders, today)]
    progress = {'idea': 1.0 if ms.get('idea_done') else idea['progress'],
                'approval': 1.0 if ms.get('approved') else rules.approval_status(c['data']['approval']['items'])['progress'],
                'sourcing': 1.0 if ms.get('order_ready') else first['progress'] if first else 0.0,
                'launch': 1.0 if ms.get('launched') else ls['progress'] if ms.get('order_ready') else 0.0,
                'reorder': min(1.0, int(ms.get('reorders') or 0) / 1.0) if ms.get('launched') else 0.0}
    late = due == 'late' or any(s['order_now'] for s in stocks)
    mini = next((s for s in stocks if s['counts'] or s['sales']), None)
    step = rules.next_step(ms, idea, rules.approval_status(c['data']['approval']['items']),
                           first or rules.sourcing_status({}, [], per_usd), ls, stocks,
                           [{'n': o['n'], 'missing': rules.sourcing_status(o, o['lines'], per_usd)['missing']}
                            for o in orders[1:] if not o['ready_at']])
    return {'id': c['id'], 'key': 'PB-%d' % c['id'], 'title': c['title'], 'market': c['market'], 'asin': c['asin'],
            'kw': c['kw'], 'image': c['image'], 'color': c['color'], 'stage': c['stage'], 'pos': c['pos'],
            'created_at': c['created_at'], 'updated_at': c['updated_at'], 'archived_at': c['archived_at'],
            'milestones': ms, 'unlocked': rules.unlocked(ms), 'markets': markets, 'idea': idea,
            'invested_usd': round(sum(rules.sourcing_status(o, o['lines'], per_usd)['known_usd'] for o in orders), 2),
            'approval': {**rules.approval_status(c['data']['approval']['items']), 'items': c['data']['approval']['items']},
            'progress': progress, 'ring': rules.ring(progress, late), 'late': late,
            'countdown': rules.countdown(dates, today), 'tags': c['data'].get('tags', []),
            'links': {'alibaba': (c['data'].get('links') or {}).get('alibaba')},
            'mini': {'market': mini['market'], 'counts': [[x[0], x[1]] for x in mini['counts']], 'sent': mini['sent'],
                     'low_pct': settings.get('board_low_pct', 30), 'sales': mini['sales'],
                     'target': mini['target'], 'live_at': mini['live_at']} if mini else None,
            'edit': {'n': orders[0]['n'] if orders else 1,
                     'unit_cost': orders[0]['unit_cost'] if orders else None, 'cost_cur': orders[0]['cost_cur'] if orders else None,
                     'units': {ln['market']: ln['units'] for ln in orders[0]['lines']} if orders else {},
                     'live': c['data']['live'], 'due_day': task['due_day'] if task and not task['done_at'] else None,
                     'task_id': task['id'] if task and not task['done_at'] else None},
            'hints': {'done': ls['done'], 'total': ls['total'], 'moved': sum(1 for h in hs if h['moved'])},
            'missing': first['missing'] if first and not ms.get('order_ready') else [], 'next': step,
            'task': {'id': task['id'], 'due_day': task['due_day'], 'due': due,
                     'next_mail': {'kind': nxt['kind'], 'send_at': nxt['send_at']} if nxt else None} if task else None}


def state(db, settings, now=None):
    """The whole board: cards (not removed) with their progress, the notifier list and a few totals."""
    now = int(now or time.time())
    per_usd = _per_usd()

    def build(conn):
        cards = [_summary(conn, _card(conn, r[0]), settings, per_usd, now) for r in
                 conn.execute('SELECT id FROM board_card WHERE archived_at IS NULL ORDER BY pos, id').fetchall()]
        off = [dict(r) for r in conn.execute('SELECT id, title, market, archived_at FROM board_card WHERE archived_at IS '
                                             'NOT NULL ORDER BY archived_at DESC LIMIT 50')]
        trophies = conn.execute("SELECT COUNT(*) FROM board_event e JOIN board_card c ON c.id=e.card_id WHERE "
                                "e.kind='trophy' AND c.archived_at IS NULL").fetchone()[0]
        recent = [dict(r) for r in conn.execute(
            'SELECT e.card_id, e.ts, e.kind, e.text, c.title FROM board_event e JOIN board_card c ON c.id=e.card_id '
            'WHERE c.archived_at IS NULL ORDER BY e.ts DESC, e.id DESC LIMIT 8')]
        last, wait = storage.get_state(conn, INFORM_KEY) or {}, storage.get_state(conn, INFORM_WAIT_KEY)
        waiting = wait and not wait.get('gave_up') and settings.get('board_inform_auto', True)
        return {'cards': cards, 'archived': off, 'notifiers': notifiers(conn), 'trophies': trophies, 'recent': recent,
                'invested_usd': round(sum(c['invested_usd'] for c in cards), 2),
                'low': sum(1 for c in cards for m in c['markets'] if (m['stock'] or {}).get('level') == 'red'),
                'late': sum(1 for c in cards if (c['task'] or {}).get('due') == 'late'), 'rev': rev(),
                'inform': {'sent_at': last.get('sent_at'), 'to': last.get('to') or [], 'opened': bool(last.get('opened')),
                           'next_at': _inform_next(wait, settings) if waiting else None}}
    return db.read(build)


def timeline(db, settings, now=None):
    """Every date on the board for the timeline: deadlines, shipment steps, expected arrival at Amazon, go-live days and
    re-order days (engine.board.timeline_events)."""
    now = int(now or time.time())

    def build(conn):
        today = dt.date.fromtimestamp(now)
        items = []
        for (cid,) in conn.execute('SELECT id FROM board_card WHERE archived_at IS NULL ORDER BY pos, id').fetchall():
            c = _card(conn, cid)
            orders = _orders(conn, cid)
            tasks = [{'id': t['id'], 'stage': s, 'due_day': t['due_day'], 'start_day': t['start_day'], 'done': bool(t['done_at'])}
                     for s, t in _tasks(conn, cid).items()]
            lines = [{'n': o['n'], 'market': ln['market'], 'steps': ln['steps'], 'eta': _eta(ln)} for o in orders for ln in o['lines']]
            reorder = []
            if c['data']['milestones'].get('order_ready'):
                for mk in _markets(orders, c):
                    s = _stock(conn, c, orders, mk, settings, today)
                    if s and s['reorder_by']:
                        reorder.append({'market': mk, 'day': s['reorder_by']})
            items.append({'id': cid, 'title': c['title'], 'color': c['color'], 'stage': c['stage'], 'tasks': tasks,
                          'lines': lines, 'live': c['data']['live'], 'reorder': reorder,
                          'order_by': _season_order(conn, c, orders, today)})
        return {'events': rules.timeline_events(items, today), 'today': today.isoformat()}
    return db.read(build)


def _season_order(conn, c, orders, today):
    """For a product not ordered yet: the last day to order for its next seasonal peak in each market, by sea (by air
    when sea is too late), from the launch planner and the keyword's Google Trends season. [] when the season is not
    reliable or not read, or once a deposit is paid."""
    if c['data']['milestones'].get('order_ready') or not c['kw'] or \
            any(ln['steps'].get('deposit') for o in orders for ln in o['lines']):
        return []
    out = []
    for mk in _markets(orders, c):
        s = (storage.trends_feature(conn, c['kw'], mk) or {}).get('seasonality') or {}
        if not s.get('reliable'):
            continue
        p = planner.plan(s.get('si'), s.get('strength'), True, s.get('peak_month'), today.isoformat(), market=mk)
        mode = {'ok': 'sea', 'next_year': 'sea', 'too_late': 'air'}.get(p['status'])
        if mode and (p.get(mode) or {}).get('order_by'):
            out.append({'market': mk, 'day': p[mode]['order_by'], 'mode': mode,
                        'peak': MONTH_ABBR[p['peak_month'] - 1] if p.get('peak_month') else None})
    return out


FILTERS_KEY = 'board_filters'


def saved_filters(conn):
    return storage.get_state(conn, FILTERS_KEY, []) or []


def save_filter(conn, f=None, remove=None):
    """Keep a named filter of the board (text, market, stage, overdue, tag), or remove one by name. Up to 12."""
    have = [x for x in saved_filters(conn) if x.get('name') != remove]
    if f:
        name = _text(f.get('name'), 40)
        if not name:
            raise BoardError('Give the filter a name.')
        keep = {'name': name, 'q': _text(f.get('q'), 80) or '', 'mk': f.get('mk') if f.get('mk') in config.MARKETS else '',
                'stage': f.get('stage') if f.get('stage') in rules.STAGES else '', 'overdue': bool(f.get('overdue')),
                'tag': _text(f.get('tag'), 24) or ''}
        have = [x for x in have if x.get('name') != name][:11] + [keep]
    storage.set_state(conn, FILTERS_KEY, have)
    return have


# ---------- what the research says about the approval checks ----------
CHECK_RULES = {0: 'demand_floor', 1: 'fast_stock', 2: 'margin_floor', 4: 'restricted'}     # APPROVAL_CHECKS index: rule


def approval_research(db, card, settings, per_usd, now=None):
    """What the stored research says about each standard approval check while the product waits for approval:
    {item id: {'state': 'pass', 'fail' or 'unknown', 'why'}}. Checks you wrote yourself get nothing, nor do the ones
    whose Go/No-go rule is switched off. The supplier price is the order's, else the one the keyword report
    remembers. Nothing is fetched."""
    ids = {rules.APPROVAL_CHECKS.index(i['text']): i['id'] for i in card['approval']['items']
           if i['text'] in rules.APPROVAL_CHECKS}
    if card['milestones'].get('approved') or not ids:
        return {}
    mk, kw, ed = card['market'], config.norm_kw(card['kw']), card['edit']

    def read(conn):
        w = conn.execute("SELECT cogs FROM watch WHERE kind='keyword' AND market=? AND target=? AND cogs > 0",
                         (mk, kw)).fetchone()
        return (w[0] if w else None), reports.size_suggestion(conn, mk, kw), reports.decide_rules(conn)
    saved, size, on = db.read(read)
    cost = (rules.to_usd(ed['unit_cost'], ed['cost_cur'] or 'USD', per_usd) if ed['unit_cost'] else None) or saved
    d = reports.decision(db, mk, kw, settings, cogs_usd=cost, dims=size['dims_cm'], kg=size['kg'], now=now)
    found = {r['key']: r for r in d['decision']['rules']}
    first = {'state': 'unknown', 'why': 'Search "%s" in %s first: the research fills this in.' % (kw, _name(mk))}
    price = 'Type the supplier price in the keyword report\'s profit calculator and tick Remember the cost.'
    out = {}
    for n, key in CHECK_RULES.items():
        if n in ids and on[key]['on']:
            r = found.get(key)
            out[ids[n]] = first if not d['facts'] or not r else \
                {'state': r['state'], 'why': price if key == 'margin_floor' and r['state'] == 'unknown' else r['why']}
    if 3 in ids:
        out[ids[3]] = {'state': 'unknown', 'why': 'Add the Alibaba link on the Idea tab.'} if not card['links']['alibaba'] \
            else {'state': 'pass', 'why': 'Alibaba link added; the supplier price is about US$%.2f a unit.' % cost} if cost \
            else {'state': 'unknown', 'why': 'Alibaba link added. ' + price}
    if 5 in ids:
        tier = fees.size_tier(mk, size['dims_cm'], size['kg']) if size['dims_cm'] and size['kg'] else None
        if not size['searched']:
            out[ids[5]] = first
        elif not tier or not tier['storage']:
            out[ids[5]] = {'state': 'unknown', 'why': 'The package size is not known yet: the keyword report reads it from '
                                                      'the top listings.'}
        else:
            box = '%s cm, %g kg' % (' x '.join('%g' % x for x in size['dims_cm']), size['kg'])
            big = tier['storage'] != 'standard'
            out[ids[5]] = {'state': 'fail' if big else 'pass', 'why': 'The usual package is %s: %s%s.' % (
                box, tier['label'].lower(), ', so higher FBA fees' if big else ' size')}
    return out


def card_detail(db, card_id, settings, now=None):
    """One card with everything the card panel shows."""
    now = int(now or time.time())
    per_usd = _per_usd()

    def build(conn):
        c = _card(conn, card_id)
        orders, hs = _orders(conn, card_id), _hints(conn, card_id)
        today = dt.date.fromtimestamp(now)
        for o in orders:
            o['status'] = rules.sourcing_status(o, o['lines'], per_usd)
            for ln in o['lines']:
                ln['cost'] = o['status']['markets'].get(ln['market'])
                ln['eta'] = _eta(ln)
        for h in hs:
            h['stale'] = bool(h['status'] in ('done', 'adjusted') and h['checked_at'] and h['recheck_days'] and
                              now - h['checked_at'] > h['recheck_days'] * 86400)
        markets = _markets(orders, c)
        first = orders[0]['status'] if orders else {'markets': {}}
        money = {}
        for mk in markets:
            landed = first['markets'].get(mk) or {}
            r = research(conn, c, mk, settings, landed, now)
            e = r.get('econ')
            sim = lch.simulate(landed.get('units') or 0, e, r.get('newcomer')) if e and landed.get('units') else None
            money[mk] = {'searched': r['searched'], 'price': r.get('price'), 'cur': r['cur'], 'econ': {
                k: e.get(k) for k in ('Profit', 'Margin', 'ROI', 'BreakEvenACoS', 'Landed', 'FBA', 'Referral', 'Duty',
                                      'ImportTax', 'tier_label')} if e else None, 'sim': sim, 'newcomer': r.get('newcomer')}
        item = _inform_item(conn, c)
        labels = inform.link_labels(item)
        team = {'rows': inform.rows(item, today), 'amazon': item['amazon'], 'alibaba': item['alibaba'],
                'amazon_label': labels[0], 'alibaba_label': labels[1]}
        return {'card': _summary(conn, c, settings, per_usd, now), 'data': c['data'], 'orders': orders, 'hints': hs,
                'launch': rules.launch_status(hs, c['data']['live']), 'markets': markets, 'money': money, 'team': team,
                'stock': [s for s in (_stock(conn, c, orders, mk, settings, today) for mk in markets) if s],
                'tasks': _tasks(conn, card_id), 'notifiers': notifiers(conn),
                'strategy': list(reversed(c['data'].get('strategy') or [])),
                'events': [dict(r) for r in conn.execute('SELECT id, ts, kind, text FROM board_event WHERE card_id=? '
                                                         'ORDER BY ts DESC, id DESC LIMIT 80', (card_id,))]}
    out = db.read(build)
    out['research'] = approval_research(db, out['card'], settings, per_usd, now)
    return out


def _eta(ln):
    """When a shipment should be on sale at Amazon, from its last step and the default lead times."""
    t = planner.times(ln['market'], ln['ship_mode'] or 'sea')
    st = ln['steps']
    for step, rest in (('arrived', 0), ('shipped', t['transit'] + t['check_in']),
                       ('produced', t['buffer'] + t['transit'] + t['check_in']),
                       ('deposit', t['production'] + t['buffer'] + t['transit'] + t['check_in'])):
        d = rules.day(st.get(step))
        if d:
            return None if step == 'arrived' else (d + dt.timedelta(days=rest)).isoformat()
    return None


def refresh_for(db, settings, results, now=None):
    """After automatic refreshes: rebuild the hints of the cards whose keyword was just searched or trended."""
    kws = {(r.get('market'), config.norm_kw(r.get('target') or '')) for r in results or ()
           if r.get('kind') in ('watch_kw', 'trends')}
    if not kws:
        return 0
    ids = db.read(lambda c: [r[0] for r in c.execute('SELECT id, market, kw FROM board_card WHERE archived_at IS NULL')
                             if any(k == r[2] for _, k in kws)])
    for cid in ids:
        refresh_hints(db, cid, settings, now)
    if ids:
        bump()
    return len(ids)


def summary_text(db, settings, now=None):
    """The weekly summary to paste into WhatsApp or an email."""
    now = int(now or time.time())
    st = state(db, settings, now)
    today = dt.date.fromtimestamp(now)
    lines = ['Product board, %s' % remind.nice_day(today), '']
    for stage in rules.STAGES:
        cards = [c for c in st['cards'] if c['stage'] == stage]
        lines.append('%s (%d)' % (rules.STAGE_NAMES[stage], len(cards)))
        for c in cards:
            bits = []
            if stage == 'idea':
                bits.append('ready' if c['milestones'].get('idea_done') else 'still needs ' + ' and '.join(c['idea']['missing']))
            elif stage == 'approval':
                a = c['approval']
                bits.append('%d of %d checks ticked' % (a['done'], a['total']))
            elif stage == 'sourcing':
                bits.append('ready' if c['milestones'].get('order_ready') else
                            'still needs ' + ', '.join(c['missing'][:3]) + (' and more' if len(c['missing']) > 3 else ''))
            elif stage == 'launch':
                bits.append('%d of %d hints checked' % (c['hints']['done'], c['hints']['total']))
            for m in c['markets']:
                s = m['stock'] or {}
                if s.get('pct') is not None:
                    bits.append('%s %d%% left%s' % (_name(m['market']), round(s['pct']),
                                                    ' (re-order now)' if s.get('order_now') else
                                                    ' (re-order by %s)' % remind.nice_day(s['reorder_by'])
                                                    if s.get('reorder_by') else ''))
            t = c['task']
            if t:
                bits.append(('LATE, was due %s' if t['due'] == 'late' else 'due %s') % remind.nice_day(t['due_day']))
            lines.append('- %s (%s): %s' % (c['title'], _name(c['market']), '; '.join(bits) or 'just added'))
        lines.append('')
    week = now - 7 * 86400
    won = db.read(lambda c: [dict(r) for r in c.execute(
        "SELECT e.text, c.title FROM board_event e JOIN board_card c ON c.id=e.card_id WHERE e.kind='trophy' AND e.ts>=? "
        'ORDER BY e.ts', (week,))])
    if won:
        lines.append('Trophies this week: ' + '; '.join('%s (%s)' % (w['text'], w['title']) for w in won))
    lines.append('Invested so far: US$ {:,.0f}'.format(st['invested_usd']))
    return '\n'.join(lines).strip() + '\n'
