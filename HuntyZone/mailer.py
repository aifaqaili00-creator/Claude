"""Email for the product board's reminders: your Gmail (or another SMTP server) with an app password that is kept
encrypted for your Windows user. Builds the messages (plain text, HTML, calendar invite) and sends them."""
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

import config
import winsys

MAX_TO = 20                     # recipients per email, Bcc included
MAX_PER_HOUR = 50
TIMEOUT = 30
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


def ready(settings, path):
    """(True, '') when an address and an app password are saved, else (False, what is missing)."""
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
    with _lock:
        while _sent and _sent[0] < now - 3600:
            _sent.popleft()
        if len(_sent) >= MAX_PER_HOUR:
            raise MailError('More than %d emails in an hour: the rest wait an hour.' % MAX_PER_HOUR)
        _sent.append(now)
    try:
        (transport or _smtp)(acct, password, msg)
    except smtplib.SMTPAuthenticationError:
        raise MailError('%s refused the sign-in: check the address and the app password in Settings.'
                        % acct['host']) from None
    except smtplib.SMTPRecipientsRefused as e:
        raise MailError('The email server refused %s.' % ', '.join(e.recipients)) from None
    except (smtplib.SMTPException, OSError) as e:
        raise MailError('Could not send through %s: %s' % (acct['host'], str(e) or e.__class__.__name__)) from None


def deliver(settings, path, to, subject, text, html=None, ics=None, transport=None):
    """Build and send one email from the saved account. You get a copy (Bcc) when settings say so."""
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
