"""Email for the product board's reminders. Two ways to send:

- browser (the default): each email opens ready to send in Gmail or Outlook, in the Edge or Chrome profile you pick
  (where you are already signed in), and you press Send. With Gmail, the update meeting opens as a Google Calendar
  event with the guests added: you press Save and Google emails them the invitation. No password is stored.
- smtp: your Gmail (or another SMTP server) with an app password that is kept encrypted for your Windows user. Emails
  then go out by themselves, with the calendar invite attached.

Builds the messages (plain text, HTML, calendar invite) and sends or opens them. With PC_NO_EMAIL set (a development
PC), no email is sent or opened."""
import collections
import email.message
import email.utils
import os
import re
import smtplib
import ssl
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlencode

import chrome_profiles as cp
import config
import winsys
from engine import remind

MAX_TO = 20                     # recipients per email, Bcc included
MAX_PER_HOUR = 50
TIMEOUT = 30
BODY_MAX = 6000                 # longer bodies are cut: browsers and webmail limit the length of a link
URL_MAX = 7000                  # ... and so is the whole link once encoded (Gmail answers "400 Bad Request" above about 8,000)
CUT_NOTE = '\n\n[... cut: the full text is in Product Checker]'
CLIP_NOTE = ('\n\n[... the rest did not fit in an email link. The whole email is on your clipboard: click in this message, '
             'press Ctrl+A, then Ctrl+V, then Send.]')
OUTLOOK_DOMAINS = ('outlook.', 'hotmail.', 'live.', 'msn.com', 'windowslive.com')
CALENDAR_URL = 'https://calendar.google.com/calendar/render'
OFF_SWITCH = 'PC_NO_EMAIL'
_sent = collections.deque()
_lock = threading.Lock()


class MailError(Exception):
    """An email could not be sent; the message is meant for you."""


def _clean_password(p):
    p = str(p or '').strip()
    if re.fullmatch(r'[a-z]{4}( [a-z]{4}){3}', p):           # Gmail shows app passwords as four groups of four
        p = p.replace(' ', '')
    return p


def save_password(path, password):
    p = _clean_password(password)
    if not p or len(p) > 200 or re.search(r'[\r\n]', p):
        raise MailError('That does not look like a password.')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_bytes(winsys.protect(p.encode('utf-8')))
    os.replace(tmp, path)


def has_password(path):
    return Path(path).is_file()


def forget_password(path):
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass


def load_password(path):
    try:
        return winsys.unprotect(Path(path).read_bytes()).decode('utf-8')
    except FileNotFoundError:
        raise MailError('Save your app password in Settings, Email for reminders, first.') from None
    except (OSError, UnicodeDecodeError):
        raise MailError('The saved app password cannot be read by this Windows user: save it again.') from None


def account(settings):
    return {'address': settings.get('mail_address') or '', 'name': settings.get('mail_from_name') or '',
            'host': settings.get('mail_host') or 'smtp.gmail.com', 'port': int(settings.get('mail_port') or 465),
            'security': settings.get('mail_security') or 'ssl'}


def method(settings):
    return 'smtp' if settings.get('mail_method') == 'smtp' else 'browser'


def mail_profile(settings, profiles=None):
    """The browser profile emails open in: the one chosen for email; else (Automatic) the Edge or Chrome profile
    signed in with your email address; else the one for links. {} if none is known."""
    profiles = cp.list_profiles() if profiles is None else profiles
    chosen, links = settings.get('mail_profile'), settings.get('profile')
    addr = (settings.get('mail_address') or '').strip().lower()
    for p in profiles:
        if chosen and p['id'] == chosen:
            return p
    for p in profiles:
        if addr and (p.get('email') or '').strip().lower() == addr:
            return p
    for p in profiles:
        if links and p['id'] == links:
            return p
    return {}


def google_account(settings, profile=None):
    """The Google account Gmail and Calendar should open in: your email address, else the profile's account."""
    for a in (settings.get('mail_address'), (profile or {}).get('email')):
        a = (a or '').strip()
        if config.EMAIL_RX.match(a):
            return a
    return ''


def web_service(settings, profile=None):
    """'gmail', 'outlook' (Outlook.com / Hotmail) or 'office' (work or school Outlook): chosen in Settings, else
    guessed from your address or the browser profile's account."""
    choice = settings.get('mail_web') or 'auto'
    if choice in ('gmail', 'outlook', 'office'):
        return choice
    addr = (settings.get('mail_address') or (profile or {}).get('email') or '').lower()
    dom = addr.rpartition('@')[2]
    if dom and any(dom.startswith(d) or dom == d for d in OUTLOOK_DOMAINS):
        return 'outlook'
    return 'gmail'


class Link(str):
    """A compose or calendar link; .cut is True when the text had to be shortened to fit in it."""
    cut = False


def _link(url, cut):
    out = Link(url)
    out.cut = cut
    return out


BODY_SAFE = '/:'                 # kept as they are in a link's text: web addresses in an email stay short


def _enc(s):
    return len(quote(s, safe=BODY_SAFE))


def _fit(text, room, note=CUT_NOTE):
    """(body, cut): the text, or as much of it as fits in `room` characters once encoded for a link, cut after a whole
    paragraph (a product of the update, a line of the deadlines), with the note saying so."""
    body = str(text or '')
    if len(body) <= BODY_MAX and _enc(body) <= room:
        return body, False
    room -= _enc(note)
    out = ''
    for para in body.split('\n\n'):
        cand = out + '\n\n' + para if out else para
        if len(cand) > BODY_MAX or _enc(cand) > room:
            break
        out = cand
    if not out:                                         # one long paragraph: as many characters as fit
        lo, hi = 0, min(len(body), BODY_MAX)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            lo, hi = (mid, hi) if _enc(body[:mid]) <= room else (lo, mid - 1)
        out = body[:lo]
    return out.rstrip() + note, True


def _cut(text):
    return _fit(text, URL_MAX)[0]


def copy_text(text):
    """Put the text on the clipboard (Windows). True when it worked."""
    import subprocess
    try:
        subprocess.run(['clip'], input=str(text).encode('utf-16'), check=True, timeout=10,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def compose_url(service, to, subject, text, bcc=None, account='', note=CUT_NOTE):
    """A link that opens a new email, filled in, in Gmail or Outlook on the web (Gmail in `account`, when given). The
    whole link stays under URL_MAX characters: a longer text is cut after a whole paragraph, with `note` (link.cut)."""
    to_s = ','.join(e for _, e in to or [])
    bcc_s = ','.join(e for _, e in bcc or [])
    subject = re.sub(r'[\r\n]+', ' ', str(subject or ''))[:200]
    if service in ('outlook', 'office'):
        base = ('https://outlook.office.com/mail/deeplink/compose' if service == 'office'
                else 'https://outlook.live.com/mail/0/deeplink/compose')
        q = {'to': to_s, 'subject': subject}
        if bcc_s:
            q['bcc'] = bcc_s
    else:
        base = 'https://mail.google.com/mail/'
        q = {'view': 'cm', 'fs': '1', 'tf': '1', 'to': to_s, 'su': subject}
        if bcc_s:
            q['bcc'] = bcc_s
        if account:
            q['authuser'] = account
    head = base + '?' + urlencode(q, quote_via=quote)
    body, cut = _fit(text, URL_MAX - len(head) - len('&body='), note)
    return _link(head + '&body=' + quote(body, safe=BODY_SAFE), cut)


def calendar_url(event, guests, account='', note=CUT_NOTE):
    """A link that opens a new Google Calendar event, filled in, with the guests added (Calendar of `account`, when
    given). event: read_ics() of the invite. You press Save and Google emails the guests the invitation."""
    def utc(ts):
        return time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(ts))
    start = int(event['start'])
    end = int(event.get('end') or start + 30 * 60)
    q = {'action': 'TEMPLATE', 'text': re.sub(r'[\r\n]+', ' ', str(event.get('summary') or ''))[:200],
         'dates': '%s/%s' % (utc(start), utc(end))}
    if event.get('location'):
        q['location'] = str(event['location'])[:300]
    if guests:
        q['add'] = ','.join(guests)
    if account:
        q['authuser'] = account
    head = CALENDAR_URL + '?' + urlencode(q, quote_via=quote)
    details, cut = _fit(event.get('description'), URL_MAX - len(head) - len('&details='), note)
    return _link(head + '&details=' + quote(details, safe=BODY_SAFE), cut)


def where(settings, profiles=None):
    """Where browser emails open, in words: 'Gmail in Chrome - Afaq (afaq@gmail.com)'."""
    p = mail_profile(settings, profiles)
    svc = {'gmail': 'Gmail', 'outlook': 'Outlook.com', 'office': 'Outlook (work or school)'}[web_service(settings, p)]
    return '%s in %s' % (svc, p['label'] if p else 'your default browser')


def ready(settings, path):
    """(True, '') when emails can be sent, else (False, what is missing). The browser way needs nothing saved."""
    if method(settings) == 'browser':
        return True, ''
    if not config.EMAIL_RX.match(settings.get('mail_address') or ''):
        return False, 'Add your Gmail address in Settings, Email for reminders.'
    if not has_password(path):
        return False, 'Save your Gmail app password in Settings, Email for reminders.'
    return True, ''


def _addr(name, address):
    e = str(address or '').strip()
    if len(e) > 254 or not config.EMAIL_RX.match(e):
        raise MailError('Not an email address: %s' % e[:80])
    return email.utils.formataddr((re.sub(r'[\r\n"<>]', '', str(name or '')).strip(), e))


def build(acct, to, subject, text, html=None, ics=None, bcc=None):
    """An email from the account to [(name, email)] (Bcc [(name, email)] stays hidden): plain text, HTML and, for a
    meeting, the calendar invite inline and as invite.ics."""
    if not to:
        raise MailError('Nobody to send to.')
    msg = email.message.EmailMessage()
    msg['From'] = _addr(acct.get('name'), acct['address'])
    msg['To'] = ', '.join(_addr(n, e) for n, e in to)
    if bcc:
        msg['Bcc'] = ', '.join(_addr(n, e) for n, e in bcc)
    msg['Reply-To'] = _addr(acct.get('name'), acct['address'])
    msg['Subject'] = re.sub(r'[\r\n]+', ' ', str(subject or ''))[:200]
    msg['Date'] = email.utils.formatdate(localtime=True)
    msg['Message-ID'] = email.utils.make_msgid(domain=acct['address'].split('@')[-1])
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype='html')
    if ics:
        msg.add_alternative(ics, subtype='calendar', params={'method': 'REQUEST'})
        msg.add_attachment(ics.encode('utf-8'), maintype='text', subtype='calendar', filename='invite.ics',
                           params={'method': 'REQUEST'})
    return msg


def _smtp(acct, password, msg):
    ctx = ssl.create_default_context()
    if acct['security'] == 'ssl':
        s = smtplib.SMTP_SSL(acct['host'], acct['port'], context=ctx, timeout=TIMEOUT)
    else:
        s = smtplib.SMTP(acct['host'], acct['port'], timeout=TIMEOUT)
        s.ehlo()
        s.starttls(context=ctx)
        s.ehlo()
    try:
        s.login(acct['address'], password)
        s.send_message(msg)
    finally:
        try:
            s.quit()
        except (smtplib.SMTPException, OSError):
            pass


def send(acct, password, msg, transport=None, now=None):
    """Send a built email; transport(acct, password, msg) replaces SMTP in tests. Raises MailError."""
    now = now or time.time()
    n = len(email.utils.getaddresses([msg.get(h, '') for h in ('To', 'Cc', 'Bcc') if msg.get(h)]))
    if n > MAX_TO:
        raise MailError('At most %d people per email.' % MAX_TO)
    _rate(now)
    try:
        (transport or _smtp)(acct, password, msg)
    except smtplib.SMTPAuthenticationError:
        raise MailError('%s refused the sign-in: check the address and the app password in Settings.'
                        % acct['host']) from None
    except smtplib.SMTPRecipientsRefused as e:
        raise MailError('The email server refused %s.' % ', '.join(e.recipients)) from None
    except (smtplib.SMTPException, OSError) as e:
        raise MailError('Could not send through %s: %s' % (acct['host'], str(e) or e.__class__.__name__)) from None


def _rate(now):
    with _lock:
        while _sent and _sent[0] < now - 3600:
            _sent.popleft()
        if len(_sent) >= MAX_PER_HOUR:
            raise MailError('More than %d emails in an hour: the rest wait an hour.' % MAX_PER_HOUR)
        _sent.append(now)


def open_in_browser(settings, to, subject, text, opener=None, profiles=None, now=None, ics=None, copier=None):
    """Open the email, filled in, in your browser profile's Gmail or Outlook; you press Send. With Gmail, a meeting
    (ics) opens as a Google Calendar event with the guests added instead; you press Save. Returns the link. When the
    text is too long for a link, it is cut there and the whole text goes on the clipboard (link.pasted) to paste over it."""
    if not to:
        raise MailError('Nobody to send to.')
    for _, e in to:
        _addr('', e)                                    # a bad address is refused here, as with Gmail
    if len(to) > MAX_TO:
        raise MailError('At most %d people per email.' % MAX_TO)
    _rate(now or time.time())
    p = mail_profile(settings, profiles)
    service = web_service(settings, p)
    account = google_account(settings, p) if service == 'gmail' else ''
    event = remind.read_ics(ics) if ics and service == 'gmail' else None
    if event and event['start']:
        make = lambda note: calendar_url(event, [e for _, e in to], account, note)  # noqa: E731
        full = event.get('description')
    else:
        make = lambda note: compose_url(service, to, subject, text, account=account, note=note)  # noqa: E731
        full = text
    url = make(CUT_NOTE)
    pasted = bool(url.cut) and (copier or copy_text)(str(full or ''))
    if pasted:
        url = make(CLIP_NOTE)
    url.pasted = pasted
    try:
        (opener or cp.open_url)(url, p.get('id', ''))
    except OSError as e:
        raise MailError('Could not open the browser: %s' % (str(e) or e.__class__.__name__)) from None
    return url


class Done(str):
    """deliver()'s outcome; .pasted is True when the email was too long for its link and the whole text was put on
    the clipboard to paste."""
    pasted = False


def outcome(result):
    """What deliver() did: 'calendar' (a Google Calendar event opened), 'opened' (an email opened) or 'sent'."""
    if isinstance(result, str):
        out = Done('calendar' if result.startswith(CALENDAR_URL) else 'opened')
        out.pasted = bool(getattr(result, 'pasted', False))
        return out
    return Done('sent')


def deliver(settings, path, to, subject, text, html=None, ics=None, transport=None, opener=None):
    """Send one email: open it ready to send in your browser, or send it from the saved account (you get a copy as
    Bcc when settings say so)."""
    if os.environ.get(OFF_SWITCH) and transport is None and opener is None:
        raise MailError('Email is switched off on this PC (%s is set).' % OFF_SWITCH)
    if method(settings) == 'browser':
        return open_in_browser(settings, to, subject, text, opener, ics=ics)
    ok, why = ready(settings, path)
    if not ok:
        raise MailError(why)
    acct = account(settings)
    mine = acct['address'].lower()
    bcc = [(acct['name'], acct['address'])] if settings.get('mail_copy_me', True) and \
        mine not in {str(e).lower() for _, e in to} else None
    msg = build(acct, to, subject, text, html, ics, bcc)
    send(acct, load_password(path), msg, transport)
    return msg
