"""Email for the product board's reminders. Two ways to send:

- browser (the default): each email opens ready to send in Gmail or Outlook, in the Edge or Chrome profile you pick
  (where you are already signed in), and you press Send. No password is stored.
- smtp: your Gmail (or another SMTP server) with an app password that is kept encrypted for your Windows user. Emails
  then go out by themselves, with the calendar invite attached.

Builds the messages (plain text, HTML, calendar invite) and sends or opens them."""
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

MAX_TO = 20                     # recipients per email, Bcc included
MAX_PER_HOUR = 50
TIMEOUT = 30
BODY_MAX = 6000                 # longer bodies are cut: browsers and webmail limit the length of a link
OUTLOOK_DOMAINS = ('outlook.', 'hotmail.', 'live.', 'msn.com', 'windowslive.com')
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
    """The browser profile emails open in: the one chosen for email, else the one for links. {} if none is known."""
    profiles = cp.list_profiles() if profiles is None else profiles
    for pid in (settings.get('mail_profile'), settings.get('profile')):
        for p in profiles:
            if pid and p['id'] == pid:
                return p
    return {}


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


def compose_url(service, to, subject, text, bcc=None):
    """A link that opens a new email, filled in, in Gmail or Outlook on the web."""
    to_s = ','.join(e for _, e in to or [])
    bcc_s = ','.join(e for _, e in bcc or [])
    body = str(text or '')
    if len(body) > BODY_MAX:
        body = body[:BODY_MAX].rstrip() + '\n\n[... cut: the full text is in Product Checker]'
    subject = re.sub(r'[\r\n]+', ' ', str(subject or ''))[:200]
    if service in ('outlook', 'office'):
        base = ('https://outlook.office.com/mail/deeplink/compose' if service == 'office'
                else 'https://outlook.live.com/mail/0/deeplink/compose')
        q = {'to': to_s, 'subject': subject, 'body': body}
        if bcc_s:
            q['bcc'] = bcc_s
        return base + '?' + urlencode(q, quote_via=quote)
    q = {'view': 'cm', 'fs': '1', 'tf': '1', 'to': to_s, 'su': subject, 'body': body}
    if bcc_s:
        q['bcc'] = bcc_s
    return 'https://mail.google.com/mail/?' + urlencode(q, quote_via=quote)


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


def open_in_browser(settings, to, subject, text, opener=None, profiles=None, now=None):
    """Open the email, filled in, in your browser profile's Gmail or Outlook; you press Send. Returns the link."""
    if not to:
        raise MailError('Nobody to send to.')
    for _, e in to:
        _addr('', e)                                    # a bad address is refused here, as with Gmail
    if len(to) > MAX_TO:
        raise MailError('At most %d people per email.' % MAX_TO)
    _rate(now or time.time())
    p = mail_profile(settings, profiles)
    url = compose_url(web_service(settings, p), to, subject, text)
    try:
        (opener or cp.open_url)(url, p.get('id', ''))
    except OSError as e:
        raise MailError('Could not open the browser: %s' % (str(e) or e.__class__.__name__)) from None
    return url


def deliver(settings, path, to, subject, text, html=None, ics=None, transport=None, opener=None):
    """Send one email: open it ready to send in your browser, or send it from the saved account (you get a copy as
    Bcc when settings say so)."""
    if method(settings) == 'browser':
        return open_in_browser(settings, to, subject, text, opener)
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
