"""The product update email for the people on the Notifier list: every product on the board with the units needed,
the target markets, when sourcing should be done and links to Amazon and Alibaba, plus what changed since the last
update. Pure functions; board.inform_items builds the products:
{'id', 'title', 'image', 'stage', 'order', 'units': [[market, units]], 'sourcing': {'state': 'done'|'due'|'none', 'day'},
 'at_amazon': [[market, 'arrived:YYYY-MM-DD' | 'eta:YYYY-MM-DD']], 'amazon', 'amazon_search', 'alibaba', 'alibaba_search'}."""
import hashlib
import html
import json
import re
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

from engine import board as rules
from engine import remind

ALIBABA_SEARCH = 'https://www.alibaba.com/trade/search?SearchText='
TITLE_MAX = 70                  # a longer product name is shortened in the plain-text email
# what tracks the click, not what the page shows: dropped from links (Alibaba's spm and priceId, Amazon's ref...)
TRACKING = {'spm', 'priceid', 'has4tab', 'tab', 'ref', 'ref_', 'psc', 'th', 'tag', 'qid', 'sr', 'crid', 'sprefix', 'scm',
            'pvid', 'smid', 'linkcode', 'dib', 'dib_tag', 'content-id', '_encoding', 'algo_pvid', 'algo_exp_id',
            'trafficchannel', 'cm_sw_r'}
STAGE_COLORS = {'idea': '#64748b', 'precheck': '#0891b2', 'approval': '#d97706', 'sourcing': '#2f6fd6', 'launch': '#7a5af8', 'reorder': '#0e9384'}
# each stage's own colours in the emails, so a section is easy to find: a solid heading bar (white text on it reaches
# 4.5:1) and a light tint behind its products
STAGE_BAND = {'idea': '#475569', 'precheck': '#0e7490', 'approval': '#b45309', 'sourcing': '#1d4ed8', 'launch': '#6d28d9',
              'reorder': '#0f766e'}
STAGE_TINT = {'idea': '#f1f3f6', 'precheck': '#e6f6fa', 'approval': '#fef3e2', 'sourcing': '#eaf1fd', 'launch': '#f1edfe',
              'reorder': '#e6f5f3'}
LABEL = '#475467'               # fact labels: dark enough on every tint
# both board emails list the products under their stage, in this order: Sourcing & PO, Approval, Pre-check, Idea (then
# Launching strategy and Re-order)
EMAIL_ORDER = ('sourcing', 'approval', 'precheck', 'idea', 'launch', 'reorder')
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


def short_link(url):
    """The same page without the tracking after "?" (Alibaba's spm and priceId, Amazon's ref, utm_...): an Alibaba
    product link goes from about 250 to about 110 characters. Searches keep their words."""
    try:
        s = urlsplit(str(url or ''))
    except ValueError:
        return url
    if not s.scheme or not s.netloc:
        return url
    keep = [(k, v) for k, v in parse_qsl(s.query) if k.lower() not in TRACKING
            and not k.lower().startswith(('utm_', 'pd_rd', 'pf_rd'))]
    path = s.path
    if 'amazon.' in s.netloc:
        m = re.match(r'(.*?/dp/[A-Z0-9]{10})', path)
        path = m.group(1) if m else path
    return urlunsplit((s.scheme, s.netloc, path, urlencode(keep, quote_via=quote_plus), ''))


def short_title(t):
    t = ' '.join(str(t or '').split())
    return t if len(t) <= TITLE_MAX else t[:TITLE_MAX - 3].rstrip(' ,.-') + '...'


def supplier_name(url):
    host = (urlsplit(url).hostname or '').lower()
    return 'Alibaba' if any(host == d or host.endswith('.' + d) for d in ('alibaba.com', '1688.com')) else 'Supplier'


def _name(mk):
    return rules.MARKET_NAMES.get(mk, mk)


def email_rank(stage):
    return EMAIL_ORDER.index(stage) if stage in EMAIL_ORDER else len(EMAIL_ORDER)


def groups(items):
    """The products under their stage headings, in EMAIL_ORDER: [(stage, [items])], empty stages left out."""
    out = {}
    for p in sorted(items, key=lambda p: email_rank(p['stage'])):      # stable: each stage keeps the board's order
        out.setdefault(p['stage'], []).append(p)
    return list(out.items())


def hashtags(tags):
    """'#Golden #verified' from a card's tags."""
    return ' '.join('#' + t for t in tags or [] if t)


def stage_name(stage):
    return rules.STAGE_NAMES.get(stage, stage)


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


def facts_line(p, today=None):
    """The facts of one product in one line for the plain-text email: 'Australia 25, UAE 25 (50 in all) · sourcing
    by Mon 19 Oct · at Amazon: USA about Fri 4 Dec · Idea'."""
    units = [(mk, u) for mk, u in p['units']]
    set_ = [u for _, u in units if u]
    need = ', '.join('%s %s' % (_name(mk), '{:,}'.format(u) if u else '(units not set)') for mk, u in units)
    if len(set_) > 1:
        need += ' (%s in all)' % '{:,}'.format(sum(set_))
    s = p['sourcing'] or {}
    if s.get('state') == 'done':
        src = 'sourcing done' + (' %s' % remind.nice_day(s['day']) if rules.day(s.get('day')) else '')
    elif s.get('state') == 'due' and rules.day(s.get('day')):
        late = today is not None and rules.day(s['day']) < rules.day(today)
        src = ('sourcing late (was due %s)' if late else 'sourcing by %s') % remind.nice_day(s['day'])
    else:
        src = 'no sourcing date yet'
    bits = [need or 'no market yet', src]
    at = ['%s %s' % (_name(mk), amazon_text(v)) for mk, v in p['at_amazon'] if amazon_text(v)]
    if at:
        bits.append('at Amazon: ' + ', '.join(at))
    stage = rules.STAGE_NAMES.get(p['stage'], p['stage'])
    bits.append(stage if p['order'] == 'First order' else '%s (%s)' % (stage, p['order']))
    return ' \u00b7 '.join(bits)


def links_line(p):
    """'Amazon https://... · Alibaba https://...': short links Gmail and Outlook turn into links by themselves."""
    a = 'Amazon search' if p['amazon_search'] else 'Amazon'
    b = 'Alibaba search' if p['alibaba_search'] else supplier_name(p['alibaba'])
    return '%s %s \u00b7 %s %s' % (a, short_link(p['amazon']), b, short_link(p['alibaba']))


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
    i = 0
    for stage, group in groups(items):                  # brief: it must fit in an email link (mailer.URL_MAX)
        lines += ['', '== %s (%d) ==' % (stage_name(stage).upper(), len(group))]
        for p in group:
            i += 1
            tag, tags = MARK.get(marks.get(str(p['id']))), hashtags(p.get('tags'))
            lines += ['', '%d. %s%s%s' % (i, short_title(p['title']), ' [%s]' % tag[0] if tag else '', '  ' + tags if tags else ''),
                      '   ' + facts_line(p, today), '   ' + links_line(p)]
    lines += ['', 'Thanks,', owner]
    text = '\n'.join(lines) + '\n'

    e = html.escape
    para = 'margin:0 0 12px'
    body = ['<p style="%s">%s</p><p style="%s">%s</p>' % (para, e(hello), para, e(intro))]
    if note:
        body.append(_note(owner, note))
    if changed:
        body.append('<div style="margin:0 0 6px;padding:12px 14px;background:#fffaeb;border-left:4px solid #f79009;'
                    'border-radius:8px"><div style="font-weight:600;margin-bottom:6px">%s</div><ul style="margin:0;'
                    'padding-left:18px">%s</ul></div>' % (e(head), ''.join('<li style="margin:2px 0">%s</li>' % e(c['text'])
                                                                         for c in changed)))
    cards = []
    for stage, group in groups(items):
        band = STAGE_BAND.get(stage, '#475467')
        cards.append('<tr><td style="padding:14px 24px 10px"><table role="presentation" width="100%%" cellpadding="0" '
                     'cellspacing="0"><tr><td bgcolor="%s" style="background:%s;border-radius:8px;padding:9px 14px;color:#ffffff;'
                     'font-size:14px;font-weight:700;letter-spacing:.05em;text-transform:uppercase">%s '
                     '<span style="font-weight:600">· %d product%s</span></td></tr></table></td></tr>'
                     % (band, band, e(stage_name(stage)), len(group), '' if len(group) == 1 else 's'))
        cards += [_card_html(p, marks, today) for p in group]
    page = _page('Product update \u00b7 ' + remind.nice_day(today), '%d product%s on the board' % (n, '' if n == 1 else 's'),
                 ''.join(body), ''.join(cards), owner,
                 ' You get it when products are added or changed.' if ctx.get('auto') else '')
    return subject(items, changed, today), text, page


def _card_html(p, marks, today):
    """One product of the product update as an HTML table row: picture, name, NEW/CHANGED badge, hashtags, facts and
    the Amazon and Alibaba buttons."""
    e = html.escape
    tag = MARK.get(marks.get(str(p['id'])))
    a, b = link_labels(p)
    pic = ('<td width="64" valign="top" style="padding-right:12px"><img src="%s" width="56" height="56" alt="" '
           'style="display:block;width:56px;height:56px;border-radius:8px;border:1px solid #e4e7ec;object-fit:contain;'
           'background:#fff"></td>' % e(p['image'], quote=True)) if p.get('image') else ''
    badge = ('<span style="display:inline-block;margin-left:8px;padding:1px 8px;border-radius:99px;font-size:11px;'
             'font-weight:700;letter-spacing:.04em;background:%s;color:%s;vertical-align:middle">%s</span>'
             % (tag[1], tag[2], tag[0])) if tag else ''
    tags = ''.join('<span style="display:inline-block;margin:4px 6px 0 0;padding:1px 8px;border-radius:99px;font-size:12px;'
                   'font-weight:600;background:#ffffff;border:1px solid #b2ccff;color:#004eeb">#%s</span>' % e(t) for t in p.get('tags') or [] if t)
    facts = ''.join('<tr><td style="padding:4px 12px 4px 0;color:%s;white-space:nowrap;vertical-align:top;'
                    'width:120px">%s</td><td style="padding:4px 0;font-weight:600;color:#101828">%s</td></tr>'
                    % (LABEL, e(k), e(v)) for k, v in rows(p, today))
    btn = ('<a href="%s" style="display:inline-block;margin:0 8px 6px 0;padding:8px 14px;border-radius:8px;'
           'background:%s;color:#ffffff;text-decoration:none;font-weight:600;font-size:13px">%s</a>')
    return ('<tr><td style="padding:0 24px 14px"><table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
            'bgcolor="%s" style="background:%s;border:1px solid #e4e7ec;border-left:5px solid %s;border-radius:10px">'
            '<tr><td style="padding:14px 16px">'
            '<table role="presentation" cellpadding="0" cellspacing="0"><tr>%s<td valign="top">'
            '<div style="font-size:16px;font-weight:700;color:#101828;line-height:1.3">%s%s</div>%s</td></tr></table>'
            '<table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:10px;font-size:14px">%s</table>'
            '<div style="margin-top:12px">%s%s</div></td></tr></table></td></tr>'
            % (STAGE_TINT.get(p['stage'], '#ffffff'), STAGE_TINT.get(p['stage'], '#ffffff'),
               STAGE_BAND.get(p['stage'], '#98a2b3'), pic, e(p['title']), badge,
               '<div>%s</div>' % tags if tags else '', facts,
               btn % (e(short_link(p['amazon']), quote=True), '#232f3e', e(a)),
               btn % (e(short_link(p['alibaba']), quote=True), '#c2410c', e(b))))


def _note(owner, note):
    e = html.escape
    return ('<div style="margin:0 0 14px;padding:12px 14px;background:#eff4ff;border-radius:8px">'
            '<div style="font-size:12px;color:#475467;margin-bottom:4px">Note from %s</div>%s</div>'
            % (e(owner), e(note).replace('\n', '<br>')))


def _page(kicker, title, body, rows, owner, extra=''):
    """The email page: a header (kicker over title), the body, more table rows (already HTML), the sign-off, a footer."""
    e = html.escape
    return (
        '<!doctype html><html><body style="margin:0;padding:0;background:#f2f4f7">'
        '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="background:#f2f4f7"><tr>'
        '<td align="center" style="padding:24px 12px"><table role="presentation" width="100%%" cellpadding="0" '
        'cellspacing="0" style="max-width:640px;background:#ffffff;border:1px solid #e4e7ec;border-radius:12px;'
        'font-family:Segoe UI,Arial,sans-serif;font-size:14px;line-height:1.55;color:#101828">'
        '<tr><td style="padding:20px 24px 16px;border-bottom:1px solid #e4e7ec">'
        '<div style="font-size:12px;color:#667085;letter-spacing:.06em;text-transform:uppercase">%s</div>'
        '<div style="font-size:20px;font-weight:700;margin-top:4px">%s</div></td></tr>'
        '<tr><td style="padding:18px 24px 8px">%s</td></tr>%s'
        '<tr><td style="padding:4px 24px 20px">Thanks,<br>%s</td></tr>'
        '<tr><td style="padding:12px 24px;background:#f9fafb;border-top:1px solid #e4e7ec;border-radius:0 0 12px 12px;'
        'font-size:12px;color:#667085">Sent from Product Checker by %s.%s Reply to this email to reach %s.</td></tr>'
        '</table></td></tr></table></body></html>'
        % (e(kicker), e(title), body, rows, e(owner), e(owner), extra, e(owner.split()[0])))


# ---------- the deadlines email (Inform deadlines) ----------
def due_text(day, today):
    """'due Mon 19 Oct (in 4 days)', 'due today, Mon 12 Oct' or 'was due Mon 12 Oct (3 days late)'."""
    n = (rules.day(day) - rules.day(today)).days
    if n < 0:
        return 'was due %s (%d day%s late)' % (remind.nice_day(day), -n, '' if n == -1 else 's')
    if n == 0:
        return 'due today, %s' % remind.nice_day(day)
    return 'due %s (in %d day%s)' % (remind.nice_day(day), n, '' if n == 1 else 's')


def deadline_texts(items, ctx):
    """(subject, plain text, HTML) of the deadlines email: every open deadline on the board under its stage (in
    EMAIL_ORDER), the soonest first in each. items: [{'title', 'market', 'stage', 'due_day', 'note', 'tags'}] sorted by
    due day; ctx: {'owner', 'to_names',
    'message', 'now', 'none': [products without a deadline]}."""
    today = remind.today(ctx['now'])
    owner = ctx.get('owner') or 'Product Checker'
    who = remind.join_names(ctx.get('to_names') or [])
    note = (ctx.get('message') or '').strip()
    none = ctx.get('none') or []
    late = sum(1 for i in items if rules.day(i['due_day']) < today)
    day = remind.nice_day(today)
    parts = (['%d late' % late] if late else []) + (['%d coming up' % (len(items) - late)] if len(items) > late else [])
    subject_ = 'Deadlines, %s: %s' % (day, ', '.join(parts) or 'none set')
    hello = 'Hi %s,' % who if who else 'Hi,'
    intro = 'Here are the deadlines on our product board by stage, the soonest first in each.' if items else \
        'No product on our board has a deadline yet.'
    stage = lambda i: rules.STAGE_NAMES.get(i['stage'], i['stage'])          # noqa: E731

    lines = [hello, '', intro]
    if note:
        lines += ['', 'Note from %s:' % owner, note]
    n = 0
    for st, group in groups(items):
        lines += ['', '== %s (%d) ==' % (stage_name(st).upper(), len(group))]
        for i in group:
            n += 1
            tags = hashtags(i.get('tags'))
            lines.append('%d. %s (%s)%s: %s, %s' % (n, i['title'], _name(i['market']), ' ' + tags if tags else '', stage(i),
                                                   due_text(i['due_day'], today)))
            if i.get('note'):
                lines.append('   Note: %s' % i['note'])
    if none:
        lines += ['', 'No deadline yet: %s.' % ', '.join(none)]
    lines += ['', 'Thanks,', owner]
    text = '\n'.join(lines) + '\n'

    e = html.escape
    para = 'margin:0 0 12px'
    body = '<p style="%s">%s</p><p style="%s">%s</p>' % (para, e(hello), para, e(intro)) + (_note(owner, note) if note else '')
    cell = 'padding:10px 12px;border-top:1px solid #e4e7ec;vertical-align:top'
    row = lambda i: (                                                        # noqa: E731
        '<tr bgcolor="%s" style="background:%s"><td style="%s;border-left:5px solid %s"><div style="font-weight:600">%s</div>'
        '<div style="font-size:12px;color:%s">%s%s%s</div></td>'
        '<td style="%s;white-space:nowrap"><span style="display:inline-block;padding:2px 9px;border-radius:99px;'
        'background:%s;color:#ffffff;font-size:12px;font-weight:700">%s</span></td><td style="%s;%s">%s</td></tr>'
        % (STAGE_TINT.get(i['stage'], '#ffffff'), STAGE_TINT.get(i['stage'], '#ffffff'), cell,
           STAGE_BAND.get(i['stage'], '#98a2b3'), e(i['title']), LABEL, e(_name(i['market'])),
           ' \u00b7 <span style="color:#004eeb;font-weight:600">%s</span>' % e(hashtags(i['tags'])) if i.get('tags') else '',
           ' \u00b7 ' + e(i['note']) if i.get('note') else '',
           cell, STAGE_BAND.get(i['stage'], '#475467'), e(stage(i)), cell,
           'color:#b42318;font-weight:600' if rules.day(i['due_day']) < today else 'color:#101828',
           e(due_text(i['due_day'], today))))
    trs = ''
    for st, group in groups(items):
        band = STAGE_BAND.get(st, '#475467')
        trs += ('<tr><td colspan="3" bgcolor="%s" style="background:%s;padding:8px 12px;color:#ffffff;font-size:13px;'
                'font-weight:700;letter-spacing:.05em;text-transform:uppercase">%s <span style="font-weight:600">'
                '\u00b7 %d deadline%s</span></td></tr>' % (band, band, e(stage_name(st)), len(group),
                                                          '' if len(group) == 1 else 's'))
        trs += ''.join(row(i) for i in group)
    head = ''.join('<th align="left" style="padding:8px 12px;font-size:12px;color:#667085;font-weight:600">%s</th>' % x
                   for x in ('Product', 'Stage', 'Deadline'))
    rows = ('<tr><td style="padding:0 24px 14px"><table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
            'style="border:1px solid #e4e7ec;border-radius:10px;font-size:14px"><tr style="background:#f9fafb">%s</tr>%s'
            '</table></td></tr>' % (head, trs)) if trs else ''
    if none:
        rows += '<tr><td style="padding:0 24px 14px;color:#475467">No deadline yet: %s.</td></tr>' % e(', '.join(none))
    title = '%d deadline%s on the board' % (len(items), '' if len(items) == 1 else 's')
    return subject_, text, _page('Deadlines \u00b7 ' + day, title, body, rows, owner)
