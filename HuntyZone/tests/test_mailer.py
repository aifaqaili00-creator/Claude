"""Email for board reminders: the saved app password, the messages and sending (no real server)."""
import smtplib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mailer  # noqa: E402
import winsys  # noqa: E402

ACCT = {'address': 'ali@gmail.com', 'name': 'Ali Afa', 'host': 'smtp.gmail.com', 'port': 465, 'security': 'ssl'}
SETTINGS = {'mail_address': 'ali@gmail.com', 'mail_from_name': 'Ali Afa', 'mail_copy_me': True}


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
    assert mailer.ready({'mail_address': ''}, p) == (False, 'Add your Gmail address in Settings, Email for reminders.')
    ok, why = mailer.ready({'mail_address': 'ali@gmail.com'}, p)
    assert not ok and 'app password' in why
    mailer.save_password(p, 'x' * 16)
    assert mailer.ready({'mail_address': 'ali@gmail.com'}, p) == (True, '')


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
        mailer.deliver({}, p, [('Sara', 'sara@example.com')], 'Hi', 'text', transport=keep)


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
