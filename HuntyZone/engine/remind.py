"""Board reminder emails: when each one goes out, whether a late one is still worth sending, the texts, and the
calendar invite for the update meeting. Pure functions: times are unix seconds, days 'YYYY-MM-DD', wall-clock times
in `tz` (a tzinfo) or, without one, in this PC's own time zone."""
import datetime as dt
import html
import re
import time

KINDS = ('start', 'reminder', 'meeting')
INVITE_LEAD = 2 * 3600          # the invite goes out at least this long before the meeting, else the day before
LATE_INVITE = 30 * 60           # closer to the meeting than this, an invite is not sent
DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def _day(x):
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


def hm(s, default=(15, 0)):
    """'15:30' -> (15, 30)."""
    m = re.match(r'^\s*(\d{1,2}):(\d{2})\s*$', str(s or ''))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return default
    return int(m.group(1)), int(m.group(2))


def at(day, hhmm, tz=None):
    """Unix time of a wall-clock time (h, m) on a day, in tz or this PC's time zone (summer time included)."""
    d, (h, m) = _day(day), hhmm
    if tz is not None:
        return int(dt.datetime(d.year, d.month, d.day, h, m, tzinfo=tz).timestamp())
    return int(time.mktime((d.year, d.month, d.day, h, m, 0, 0, 0, -1)))


def local(ts, tz=None):
    return dt.datetime.fromtimestamp(ts, tz) if tz is not None else dt.datetime.fromtimestamp(ts)


def today(now, tz=None):
    return local(now, tz).date()


def nice_day(d):
    d = _day(d)
    return '%s %d %s' % (DAYS[d.weekday()], d.day, MONTHS[d.month - 1])


def utc_offset(ts, tz=None):
    """'UTC+04:00' for the time zone at that moment."""
    off = local(ts, tz).astimezone().utcoffset() if tz is None else local(ts, tz).utcoffset()
    mins = int(off.total_seconds() // 60)
    return 'UTC%s%02d:%02d' % ('+' if mins >= 0 else '-', abs(mins) // 60, abs(mins) % 60)


def schedule(start_day, days, now, send_hour=9, remind_days=3, start_email=True, meet_time='15:00', tz=None):
    """The due day, the meeting start and the emails [(kind, send_at)] for a task of `days` days from start_day:
    a start email now, a reminder remind_days before the due day (the day before for shorter tasks, none for a
    one-day task) and, on the due day, the invite to the update meeting (the day before when the meeting is less
    than two hours after send_hour). Emails whose time has passed are left out."""
    n = max(1, int(days))
    due = _day(start_day) + dt.timedelta(days=n)
    meet = at(due, hm(meet_time), tz)
    mails = [('start', int(now))] if start_email else []
    back = remind_days if n > remind_days else 1 if n >= 2 else None
    if back:
        t = at(due - dt.timedelta(days=back), (send_hour, 0), tz)
        if t > now:
            mails.append(('reminder', t))
    inv = at(due, (send_hour, 0), tz)
    if meet - inv < INVITE_LEAD:
        inv = at(due - dt.timedelta(days=1), (send_hour, 0), tz)
    inv = max(inv, int(now))
    if meet - inv >= LATE_INVITE:
        mails.append(('meeting', inv))
    return {'due_day': due.isoformat(), 'meet_start': meet, 'mails': mails}


def still_useful(kind, due_day, meet_start, now, tz=None):
    """A start email or reminder is still worth sending until the due day is over; an invite until 30 minutes before
    the meeting."""
    if kind == 'meeting':
        return now <= (meet_start or 0) - LATE_INVITE
    return now < at(_day(due_day) + dt.timedelta(days=1), (0, 0), tz)


def join_names(names):
    """First names: 'Sara', 'Sara and Ahmed', 'Sara, Ahmed and Li'."""
    first = [str(n).split()[0] for n in names if str(n or '').strip()]
    if not first:
        return ''
    return first[0] if len(first) == 1 else ', '.join(first[:-1]) + ' and ' + first[-1]


def _blocks(kind, c, tz=None):
    """The email as blocks: ('p', text) and ('ul', [items])."""
    due, title, stage = c['due_day'], c['title'], c['stage']
    owner = c.get('owner') or 'Product Checker'
    who = join_names(c.get('to_names') or [])
    todo = [t for t in c.get('todo') or () if t]
    out = [('p', 'Hi %s,' % who if who else 'Hi,')]
    if kind == 'start':
        days = int(c.get('days') or 0)
        out.append(('p', '%s has started "%s" for %s. Please have it done by %s (%d day%s from today).'
                    % (owner, stage, title, nice_day(due), days, '' if days == 1 else 's')))
        if todo:
            out += [('p', 'What is still needed:'), ('ul', todo)]
    elif kind == 'reminder':
        left = (_day(due) - today(c['now'], tz)).days
        when = 'today' if left <= 0 else 'tomorrow' if left == 1 else 'in %d days' % left
        out.append(('p', 'A reminder: "%s" for %s is due %s, on %s.' % (stage, title, when, nice_day(due))))
        if todo:
            out += [('p', 'Still open:'), ('ul', todo)]
    else:
        start = c['meet_start']
        out.append(('p', '"%s" for %s is due on %s. Let\'s meet for %d minutes on %s at %s (%s) to go through where it '
                    'stands.' % (stage, title, nice_day(due), int(c.get('meet_minutes') or 30),
                                 nice_day(local(start, tz).date()), local(start, tz).strftime('%H:%M'),
                                 utc_offset(start, tz))))
        if c.get('link'):
            out.append(('p', 'Join here: %s' % c['link']))
        out.append(('p', 'Please accept the invitation, or reply with a time that suits you better.'))
        if todo:
            out += [('p', 'Still open:'), ('ul', todo)]
    if c.get('note'):
        out.append(('p', 'Note from %s:\n%s' % (owner, c['note'])))
    if kind == 'start':
        out.append(('p', 'You will get a reminder before the due day and an invitation to a short update meeting.'))
    out.append(('p', 'Thanks,\n%s' % owner))
    return out


def texts(kind, c, tz=None):
    """(subject, plain text, HTML) of one email. c: {'title', 'stage', 'due_day', 'days', 'todo', 'note', 'to_names',
    'owner', 'now', 'meet_start', 'meet_minutes', 'link'}."""
    due, title, stage = c['due_day'], c['title'], c['stage']
    if kind == 'start':
        subject = 'New task: %s for %s, due %s' % (stage, title, nice_day(due))
    elif kind == 'reminder':
        left = (_day(due) - today(c['now'], tz)).days
        when = 'today' if left <= 0 else 'tomorrow' if left == 1 else 'in %d days' % left
        subject = 'Reminder: %s for %s is due %s (%s)' % (stage, title, when, nice_day(due))
    else:
        start = local(c['meet_start'], tz)
        subject = 'Update meeting: %s for %s, %s at %s' % (stage, title, nice_day(start.date()), start.strftime('%H:%M'))
    chunks, body = [], ''
    for b in _blocks(kind, c, tz):
        if b[0] == 'ul':
            chunks[-1] += '\n' + '\n'.join('- ' + i for i in b[1])
            body += '<ul>%s</ul>' % ''.join('<li>%s</li>' % html.escape(i) for i in b[1])
        else:
            chunks.append(b[1])
            body += '<p>%s</p>' % html.escape(b[1]).replace('\n', '<br>')
    page = ('<!doctype html><html><body style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;line-height:1.5;'
            'color:#101828">%s</body></html>' % body)
    return re.sub(r'[\r\n]+', ' ', subject)[:200], '\n\n'.join(chunks) + '\n', page


def _esc(s):
    return (str(s or '').replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,')
            .replace('\r\n', '\\n').replace('\n', '\\n').replace('\r', ''))


def _param(s):
    return '"%s"' % re.sub(r'["\x00-\x1f]', '', str(s or ''))


def _fold(line):
    """Lines of at most 75 bytes, continued with a space (RFC 5545); never splits a character."""
    out, cur, size = [], '', 0
    for ch in line:
        n = len(ch.encode('utf-8'))
        if size + n > (75 if not out else 74):
            out.append(cur)
            cur, size = '', 0
        cur += ch
        size += n
    out.append(cur)
    return '\r\n '.join(out)


def ics(uid, start, minutes, summary, description, organizer, attendees, location=None, seq=0, stamp=None):
    """A calendar invitation (METHOD:REQUEST) for one meeting. organizer (name, email); attendees [(name, email)]."""
    def utc(ts):
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lines = ['BEGIN:VCALENDAR', 'PRODID:-//Product Checker//Board reminders//EN', 'VERSION:2.0', 'CALSCALE:GREGORIAN',
             'METHOD:REQUEST', 'BEGIN:VEVENT', 'UID:' + uid, 'DTSTAMP:' + utc(stamp or time.time()), 'DTSTART:' + utc(start),
             'DTEND:' + utc(start + int(minutes) * 60), 'SEQUENCE:%d' % seq, 'STATUS:CONFIRMED',
             'SUMMARY:' + _esc(summary), 'DESCRIPTION:' + _esc(description),
             'ORGANIZER;CN=%s:mailto:%s' % (_param(organizer[0] or organizer[1]), organizer[1])]
    if location:
        lines.append('LOCATION:' + _esc(location))
    for name, email in attendees:
        lines.append('ATTENDEE;CN=%s;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:%s'
                     % (_param(name or email), email))
    lines += ['END:VEVENT', 'END:VCALENDAR']
    return '\r\n'.join(_fold(x) for x in lines) + '\r\n'
