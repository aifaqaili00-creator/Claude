"""Email for board reminders: the saved app password, the messages and sending (no real server)."""
import smtplib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mailer  # noqa: E402
import winsys  # noqa: E402

ACCT = {'address': 'ali@gmail.com', 'name': 'Ali Afa', 'host': 'smtp.gmail.com', 'port': 465, 'security': 'ssl'}
SETTINGS = {'mail_method': 'smtp', 'mail_address': 'ali@gmail.com', 'mail_from_name': 'Ali Afa', 'mail_copy_me': True}


@pytest.fixture
def fake_dpapi(monkeypatch):
    monkeypatch.setattr(winsys, 'protect', lambda b: b'X' + b[::-1])
    monkeypatch.setattr(winsys, 'unprotect', lambda b: b[1:][::-1])
    mailer._sent.clear()


def test_the_app_password_is_kept_encrypted(tmp_path, fake_dpapi):
    p = tmp_path / 'mail.secret'
    assert not mailer.has_password(p)
    with pytest.raises(mailer.MailError, match='Save your app password'):
        mailer.load_password(p)
    mailer.save_password(p, ' abcd efgh ijkl mnop ')                  # as Gmail shows it
    assert b'abcdefghijklmnop' not in p.read_bytes() and mailer.load_password(p) == 'abcdefghijklmnop'
    mailer.save_password(p, 'My pass word')
    assert mailer.load_password(p) == 'My pass word'
    for bad in ('', '   ', 'a\nb'):
        with pytest.raises(mailer.MailError):
            mailer.save_password(p, bad)
    mailer.forget_password(p)
    mailer.forget_password(p)
    assert not mailer.has_password(p)


def test_ready_says_what_is_missing(tmp_path, fake_dpapi):
    p = tmp_path / 'mail.secret'
    assert mailer.ready({'mail_method': 'smtp', 'mail_address': ''}, p) == (False, 'Add your Gmail address in Settings, Email for reminders.')
    ok, why = mailer.ready({'mail_method': 'smtp', 'mail_address': 'ali@gmail.com'}, p)
    assert not ok and 'app password' in why
    mailer.save_password(p, 'x' * 16)
    assert mailer.ready({'mail_method': 'smtp', 'mail_address': 'ali@gmail.com'}, p) == (True, '')


def test_messages_carry_text_html_and_the_invite():
    m = mailer.build(ACCT, [('Sara Khan', 'sara@example.com')], 'Update\r\nBcc: x@y.z', 'plain', '<p>html</p>',
                     'BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n', [('Ali Afa', 'ali@gmail.com')])
    assert m['From'] == 'Ali Afa <ali@gmail.com>' and m['To'] == 'Sara Khan <sara@example.com>'
    assert m['Bcc'] == 'Ali Afa <ali@gmail.com>' and m['Reply-To'] == 'Ali Afa <ali@gmail.com>'
    assert m['Subject'] == 'Update Bcc: x@y.z' and m['Message-ID'].endswith('@gmail.com>')
    assert [p.get_content_type() for p in m.walk()] == ['multipart/mixed', 'multipart/alternative', 'text/plain',
                                                        'text/html', 'text/calendar', 'text/calendar']
    cal = [p for p in m.walk() if p.get_content_type() == 'text/calendar']
    assert all(p.get_param('method') == 'REQUEST' for p in cal) and cal[1].get_filename() == 'invite.ics'
    plain = mailer.build(ACCT, [('', 'sara@example.com')], 's', 'only text')
    assert plain.get_content_type() == 'text/plain' and plain['Bcc'] is None and plain['To'] == 'sara@example.com'
    with pytest.raises(mailer.MailError):
        mailer.build(ACCT, [('x', 'sara@example.com\r\nBcc: evil@example.com')], 's', 't')
    with pytest.raises(mailer.MailError):
        mailer.build(ACCT, [], 's', 't')


def test_deliver_sends_you_a_copy(tmp_path, fake_dpapi):
    p = tmp_path / 'mail.secret'
    mailer.save_password(p, 'abcdefghijklmnop')
    got = []
    keep = lambda a, pw, m: got.append((a['host'], pw, m))          # noqa: E731
    mailer.deliver(SETTINGS, p, [('Sara', 'sara@example.com')], 'Hi', 'text', transport=keep)
    assert got[0][:2] == ('smtp.gmail.com', 'abcdefghijklmnop') and got[0][2]['Bcc'] == 'Ali Afa <ali@gmail.com>'
    mailer.deliver({**SETTINGS, 'mail_copy_me': False}, p, [('Sara', 'sara@example.com')], 'Hi', 'text', transport=keep)
    mailer.deliver(SETTINGS, p, [('Me', 'ALI@gmail.com')], 'Test', 'text', transport=keep)
    assert got[1][2]['Bcc'] is None and got[2][2]['Bcc'] is None
    with pytest.raises(mailer.MailError, match='Gmail address'):
        mailer.deliver({'mail_method': 'smtp'}, p, [('Sara', 'sara@example.com')], 'Hi', 'text', transport=keep)


def test_send_errors_are_plain_and_limited(monkeypatch):
    mailer._sent.clear()
    msg = mailer.build(ACCT, [('S', 'sara@example.com')], 's', 't')

    def refuse(a, p, m):
        raise smtplib.SMTPAuthenticationError(535, b'bad credentials')

    def down(a, p, m):
        raise OSError('timed out')
    with pytest.raises(mailer.MailError, match='refused the sign-in'):
        mailer.send(ACCT, 'pw', msg, refuse)
    with pytest.raises(mailer.MailError, match='Could not send through smtp.gmail.com: timed out'):
        mailer.send(ACCT, 'pw', msg, down)
    monkeypatch.setattr(mailer, 'MAX_PER_HOUR', 2)
    mailer._sent.clear()
    ok = lambda a, p, m: None                                         # noqa: E731
    mailer.send(ACCT, 'pw', msg, ok, now=1000)
    mailer.send(ACCT, 'pw', msg, ok, now=1001)
    with pytest.raises(mailer.MailError, match='in an hour'):
        mailer.send(ACCT, 'pw', msg, ok, now=1002)
    mailer.send(ACCT, 'pw', msg, ok, now=4700)
    many = mailer.build(ACCT, [('', 'p%d@example.com' % i) for i in range(21)], 's', 't')
    with pytest.raises(mailer.MailError, match='At most 20'):
        mailer.send(ACCT, 'pw', many, ok)


# ---------- the browser way: the email opens in your signed-in Gmail or Outlook ----------
PROFILES = [{'id': 'edge:Default', 'browser': 'edge', 'dir': 'Default', 'name': 'Personal', 'email': 'me@outlook.com',
             'label': 'Edge - Personal (me@outlook.com)'},
            {'id': 'chrome:Profile 2', 'browser': 'chrome', 'dir': 'Profile 2', 'name': 'Afaq', 'email': 'afaq@gmail.com',
             'label': 'Chrome - Afaq (afaq@gmail.com)'}]


def test_browser_is_the_default_and_needs_no_password(tmp_path):
    assert mailer.method({}) == 'browser' and mailer.ready({}, tmp_path / 'none') == (True, '')
    assert mailer.method({'mail_method': 'smtp'}) == 'smtp' and not mailer.ready({'mail_method': 'smtp'}, tmp_path / 'x')[0]


def test_the_profile_and_the_website_follow_your_choice():
    s = {'mail_profile': 'chrome:Profile 2', 'profile': 'edge:Default'}
    p = mailer.mail_profile(s, PROFILES)
    assert p['name'] == 'Afaq' and mailer.web_service(s, p) == 'gmail'
    s = {'mail_profile': '', 'profile': 'edge:Default'}                      # same as the profile for links
    p = mailer.mail_profile(s, PROFILES)
    assert p['id'] == 'edge:Default' and mailer.web_service(s, p) == 'outlook'
    assert mailer.web_service({'mail_address': 'x@hotmail.co.uk'}) == 'outlook'
    assert mailer.web_service({'mail_address': 'x@company.com', 'mail_web': 'office'}) == 'office'
    assert mailer.where(s, PROFILES) == 'Outlook.com in Edge - Personal (me@outlook.com)'


def test_compose_links_carry_the_email():
    from urllib.parse import parse_qs, urlparse
    to = [('Sara', 'sara@x.com'), ('', 'li@y.com')]
    g = urlparse(mailer.compose_url('gmail', to, 'Deadline: Sourcing\nsoon', 'Hi Sara & Li,\n50% done?'))
    q = parse_qs(g.query)
    assert g.netloc == 'mail.google.com' and q['view'] == ['cm'] and q['to'] == ['sara@x.com,li@y.com']
    assert q['su'] == ['Deadline: Sourcing soon'] and q['body'] == ['Hi Sara & Li,\n50% done?']
    o = urlparse(mailer.compose_url('outlook', to, 'S', 'B'))
    assert o.netloc == 'outlook.live.com' and parse_qs(o.query)['to'] == ['sara@x.com,li@y.com']
    assert urlparse(mailer.compose_url('office', to, 'S', 'B')).netloc == 'outlook.office.com'
    long = mailer.compose_url('gmail', to, 'S', 'x' * 20000)
    assert len(long) < 9000 and 'cut' in parse_qs(urlparse(long).query)['body'][0]


def test_deliver_opens_the_email_in_the_chosen_profile(monkeypatch):
    mailer._sent.clear()
    monkeypatch.setattr(mailer.cp, 'list_profiles', lambda: PROFILES)
    opened = []
    s = {'mail_profile': 'chrome:Profile 2'}
    url = mailer.deliver(s, None, [('Sara', 'sara@x.com')], 'Hello', 'Text', '<p>html</p>', 'ICS',
                         opener=lambda u, pid: opened.append((u, pid)))
    assert opened == [(url, 'chrome:Profile 2')] and url.startswith('https://mail.google.com/')
    with pytest.raises(mailer.MailError, match='Not an email address'):
        mailer.deliver(s, None, [('', 'not-an-address')], 'S', 'T', opener=lambda u, pid: None)
    with pytest.raises(mailer.MailError, match='Nobody'):
        mailer.deliver(s, None, [], 'S', 'T', opener=lambda u, pid: None)
