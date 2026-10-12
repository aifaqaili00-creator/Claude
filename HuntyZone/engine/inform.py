"""The product update email for the people on the Notifier list: every product on the board with the units needed,
the target markets, when sourcing should be done and links to Amazon and Alibaba, plus what changed since the last
update. Pure functions; board.inform_items builds the products:
{'id', 'title', 'image', 'stage', 'order', 'units': [[market, units]], 'sourcing': {'state': 'done'|'due'|'none', 'day'},
 'at_amazon': [[market, 'arrived:YYYY-MM-DD' | 'eta:YYYY-MM-DD']], 'amazon', 'amazon_search', 'alibaba', 'alibaba_search'}."""
import hashlib
import html
import json
from urllib.parse import quote_plus, urlsplit

from engine import board as rules
from engine import remind

ALIBABA_SEARCH = 'https://www.alibaba.com/trade/search?SearchText='
STAGE_COLORS = {'sourcing': '#2f6fd6', 'launch': '#7a5af8', 'reorder': '#0e9384'}
MARK = {'new': ('NEW', '#ecfdf3', '#067647'), 'changed': ('CHANGED', '#fffaeb', '#b54708')}


def amazon_link(site, asin, kw):
    """The product page when the ASIN is known, else a search for the keyword: (url, is_search)."""
    if asin:
        return '%s/dp/%s' % (site, asin), False
    return '%s/s?k=%s' % (site, quote_plus(kw or '')), True


def alibaba_link(url, kw):
    """Your supplier's page when you saved one, else an Alibaba search for the keyword: (url, is_search)."""
    if url:
        return url, False
    return ALIBABA_SEARCH + quote_plus(kw or ''), True


def supplier_name(url):
    host = (urlsplit(url).hostname or '').lower()
    return 'Alibaba' if any(host == d or host.endswith('.' + d) for d in ('alibaba.com', '1688.com')) else 'Supplier'


def _name(mk):
    return rules.MARKET_NAMES.get(mk, mk)


def snapshot(items):
    """What the email says about each product, to compare with the next one. JSON-safe: {product id: {...}}."""
    return {str(p['id']): {'title': p['title'], 'stage': p['stage'], 'order': p['order'],
                           'units': {mk: u for mk, u in p['units']}, 'sourcing': dict(p['sourcing']),
                           'at_amazon': {mk: v for mk, v in p['at_amazon']}, 'amazon': p['amazon'],
                           'alibaba': p['alibaba']} for p in items}


def fingerprint(snap):
    return hashlib.sha1(json.dumps(snap, sort_keys=True).encode()).hexdigest()[:16]


def _units(u):
    return '{:,}'.format(u) if u else 'not set'


def sourcing_text(s, today=None):
    """'Done, Mon 12 Oct', 'Mon 19 Oct', 'Mon 19 Oct (late)' or 'Not set yet'."""
    s = s or {}
    if s.get('state') == 'done':
        return 'Done, %s' % remind.nice_day(s['day']) if rules.day(s.get('day')) else 'Done'
    if s.get('state') == 'due' and rules.day(s.get('day')):
        late = today is not None and rules.day(s['day']) < rules.day(today)
        return remind.nice_day(s['day']) + (' (late)' if late else '')
    return 'Not set yet'


def amazon_text(v):
    """'arrived:2026-10-19' -> 'arrived Mon 19 Oct'; 'eta:2026-12-04' -> 'about Fri 4 Dec'."""
    kind, _, d = str(v or '').partition(':')
    if not rules.day(d):
        return ''
    return ('arrived %s' if kind == 'arrived' else 'about %s') % remind.nice_day(d)


def _amazon_change(mk, v):
    kind, _, d = str(v or '').partition(':')
    if not rules.day(d):
        return None
    if kind == 'arrived':
        return 'arrived at Amazon in %s on %s' % (_name(mk), remind.nice_day(d))
    return 'on sale in %s about %s' % (_name(mk), remind.nice_day(d))


def changes(old, new):
    """What changed between two snapshots: [{'id', 'kind': 'new'|'changed'|'removed', 'title', 'text'}]."""
    old, out = old or {}, []
    for pid, p in new.items():
        o, t = old.get(pid), p['title']
        if not o:
            out.append({'id': pid, 'kind': 'new', 'title': t, 'text': 'New: %s' % t})
            continue
        bits = []
        if o.get('title') != t:
            bits.append('renamed from "%s"' % o.get('title'))
        if o.get('stage') != p['stage']:
            bits.append('moved to %s' % rules.STAGE_NAMES.get(p['stage'], p['stage']))
        ou, nu = o.get('units') or {}, p['units']
        if o.get('order') != p['order']:
            bits.append('%s started for %s' % (p['order'], ', '.join(_name(m) for m in nu) or 'no market yet'))
        else:
            for mk, u in nu.items():
                if mk not in ou:
                    bits.append('%s added (%s units)' % (_name(mk), _units(u)) if u else '%s added' % _name(mk))
                elif ou[mk] != u:
                    bits.append('%s units %s \u2192 %s' % (_name(mk), _units(ou[mk]), _units(u)))
            bits += ['%s taken out' % _name(mk) for mk in ou if mk not in nu]
        if (o.get('sourcing') or {}) != p['sourcing']:
            bits.append('Sourcing ETA %s \u2192 %s' % (sourcing_text(o.get('sourcing')), sourcing_text(p['sourcing'])))
        oa = o.get('at_amazon') or {}
        bits += [x for x in (_amazon_change(mk, v) for mk, v in p['at_amazon'].items() if oa.get(mk) != v) if x]
        if o.get('amazon') != p['amazon']:
            bits.append('new Amazon link')
        if o.get('alibaba') != p['alibaba']:
            bits.append('new Alibaba link')
        if bits:
            out.append({'id': pid, 'kind': 'changed', 'title': t, 'text': '%s: %s' % (t, '; '.join(bits))})
    out += [{'id': pid, 'kind': 'removed', 'title': o.get('title'), 'text': 'Removed: %s' % o.get('title')}
            for pid, o in old.items() if pid not in new]
    return out


def rows(p, today=None):
    """The facts of one product, as the email lists them: [(label, value)]."""
    units = [(mk, u) for mk, u in p['units']]
    set_ = [u for _, u in units if u]
    need = ', '.join('%s %s' % (_name(mk), '{:,}'.format(u) if u else 'not set yet') for mk, u in units)
    if len(set_) > 1:
        need += ' (%s in all)' % '{:,}'.format(sum(set_))
    out = [('Units needed', need or 'Not set yet'),
           ('Target market', ', '.join(_name(mk) for mk, _ in units) or 'Not set yet'),
           ('Sourcing ETA', sourcing_text(p['sourcing'], today))]
    at = ['%s %s' % (_name(mk), amazon_text(v)) for mk, v in p['at_amazon'] if amazon_text(v)]
    if at:
        out.append(('At Amazon', ', '.join(at)))
    out.append(('Stage', '%s \u00b7 %s' % (rules.STAGE_NAMES.get(p['stage'], p['stage']), p['order'])))
    return out


def link_labels(p):
    """('View on Amazon' or 'Search on Amazon', 'Alibaba page' / 'Supplier page' or 'Search on Alibaba')."""
    return ('Search on Amazon' if p['amazon_search'] else 'View on Amazon',
            'Search on Alibaba' if p['alibaba_search'] else '%s page' % supplier_name(p['alibaba']))


def subject(items, changed, today):
    day = remind.nice_day(today)
    if not changed:
        return 'Product update, %s: %d product%s' % (day, len(items), '' if len(items) == 1 else 's')
    if len(changed) == 1:
        c = changed[0]
        what = '%s %s' % (c['title'], {'new': 'is new', 'changed': 'changed', 'removed': 'was removed'}[c['kind']])
    else:
        n = {k: sum(1 for c in changed if c['kind'] == k) for k in ('new', 'changed', 'removed')}
        what = ', '.join('%d %s' % (n[k], k) for k in ('new', 'changed', 'removed') if n[k])
    return ('Product update, %s: %s' % (day, what))[:200]


def texts(items, changed, ctx):
    """(subject, plain text, HTML) of the product update. ctx: {'owner', 'to_names', 'message', 'now', 'auto',
    'since': {'ts', 'sent'} or None}."""
    today = remind.today(ctx['now'])
    owner = ctx.get('owner') or 'Product Checker'
    who = remind.join_names(ctx.get('to_names') or [])
    note = (ctx.get('message') or '').strip()
    marks = {c['id']: c['kind'] for c in changed}
    n = len(items)
    hello = 'Hi %s,' % who if who else 'Hi,'
    what = 'product stands' if n == 1 else '%d products stand' % n
    intro = ('The product board changed, so here is where our %s.' if ctx.get('auto') else 'Here is where our %s.') % what
    since = ctx.get('since') or {}
    head = None
    if changed:
        head = ('What changed since the last update (%s):' if since.get('sent') else 'What changed since %s:') % \
            remind.nice_day(remind.today(since['ts'])) if since.get('ts') else 'What changed:'

    lines = [hello, '', intro]
    if note:
        lines += ['', 'Note from %s:' % owner, note]
    if changed:
        lines += ['', head] + ['- ' + c['text'] for c in changed]
    for i, p in enumerate(items, 1):
        tag = MARK.get(marks.get(str(p['id'])))
        a, b = link_labels(p)
        lines += ['', '%d. %s%s' % (i, p['title'], ' [%s]' % tag[0] if tag else '')]
        lines += ['   %s: %s' % kv for kv in rows(p, today)]
        lines += ['   %s: %s' % (a, p['amazon']), '   %s: %s' % (b, p['alibaba'])]
    lines += ['', 'Thanks,', owner]
    text = '\n'.join(lines) + '\n'

    e = html.escape
    para = 'margin:0 0 12px'
    body = ['<p style="%s">%s</p><p style="%s">%s</p>' % (para, e(hello), para, e(intro))]
    if note:
        body.append('<div style="margin:0 0 14px;padding:12px 14px;background:#eff4ff;border-radius:8px">'
                    '<div style="font-size:12px;color:#475467;margin-bottom:4px">Note from %s</div>%s</div>'
                    % (e(owner), e(note).replace('\n', '<br>')))
    if changed:
        body.append('<div style="margin:0 0 6px;padding:12px 14px;background:#fffaeb;border-left:4px solid #f79009;'
                    'border-radius:8px"><div style="font-weight:600;margin-bottom:6px">%s</div><ul style="margin:0;'
                    'padding-left:18px">%s</ul></div>' % (e(head), ''.join('<li style="margin:2px 0">%s</li>' % e(c['text'])
                                                                         for c in changed)))
    cards = []
    for p in items:
        tag = MARK.get(marks.get(str(p['id'])))
        a, b = link_labels(p)
        pic = ('<td width="64" valign="top" style="padding-right:12px"><img src="%s" width="56" height="56" alt="" '
               'style="display:block;width:56px;height:56px;border-radius:8px;border:1px solid #e4e7ec;object-fit:contain;'
               'background:#fff"></td>' % e(p['image'], quote=True)) if p.get('image') else ''
        badge = ('<span style="display:inline-block;margin-left:8px;padding:1px 8px;border-radius:99px;font-size:11px;'
                 'font-weight:700;letter-spacing:.04em;background:%s;color:%s;vertical-align:middle">%s</span>'
                 % (tag[1], tag[2], tag[0])) if tag else ''
        facts = ''.join('<tr><td style="padding:4px 12px 4px 0;color:#667085;white-space:nowrap;vertical-align:top;'
                        'width:120px">%s</td><td style="padding:4px 0;font-weight:600;color:#101828">%s</td></tr>'
                        % (e(k), e(v)) for k, v in rows(p, today))
        btn = ('<a href="%s" style="display:inline-block;margin:0 8px 6px 0;padding:8px 14px;border-radius:8px;'
               'background:%s;color:#ffffff;text-decoration:none;font-weight:600;font-size:13px">%s</a>')
        cards.append(
            '<tr><td style="padding:0 24px 14px"><table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
            'style="border:1px solid #e4e7ec;border-left:4px solid %s;border-radius:10px"><tr><td style="padding:14px 16px">'
            '<table role="presentation" cellpadding="0" cellspacing="0"><tr>%s<td valign="top">'
            '<div style="font-size:16px;font-weight:700;color:#101828;line-height:1.3">%s%s</div></td></tr></table>'
            '<table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:10px;font-size:14px">%s</table>'
            '<div style="margin-top:12px">%s%s</div></td></tr></table></td></tr>'
            % (STAGE_COLORS.get(p['stage'], '#98a2b3'), pic, e(p['title']), badge, facts,
               btn % (e(p['amazon'], quote=True), '#232f3e', e(a)), btn % (e(p['alibaba'], quote=True), '#c2410c', e(b))))
    page = (
        '<!doctype html><html><body style="margin:0;padding:0;background:#f2f4f7">'
        '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="background:#f2f4f7"><tr>'
        '<td align="center" style="padding:24px 12px"><table role="presentation" width="100%%" cellpadding="0" '
        'cellspacing="0" style="max-width:640px;background:#ffffff;border:1px solid #e4e7ec;border-radius:12px;'
        'font-family:Segoe UI,Arial,sans-serif;font-size:14px;line-height:1.55;color:#101828">'
        '<tr><td style="padding:20px 24px 16px;border-bottom:1px solid #e4e7ec">'
        '<div style="font-size:12px;color:#667085;letter-spacing:.06em;text-transform:uppercase">Product update \u00b7 %s</div>'
        '<div style="font-size:20px;font-weight:700;margin-top:4px">%s</div></td></tr>'
        '<tr><td style="padding:18px 24px 8px">%s</td></tr>%s'
        '<tr><td style="padding:4px 24px 20px">Thanks,<br>%s</td></tr>'
        '<tr><td style="padding:12px 24px;background:#f9fafb;border-top:1px solid #e4e7ec;border-radius:0 0 12px 12px;'
        'font-size:12px;color:#667085">Sent from Product Checker by %s.%s Reply to this email to reach %s.</td></tr>'
        '</table></td></tr></table></body></html>'
        % (e(remind.nice_day(today)), e('%d product%s on the board' % (n, '' if n == 1 else 's')), ''.join(body),
           ''.join(cards), e(owner), e(owner),
           ' You get it when products are added or changed.' if ctx.get('auto') else '', e(owner.split()[0])))
    return subject(items, changed, today), text, page
