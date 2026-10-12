"""Reminder emails: their send times, late ones, the texts and the calendar invite."""
import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import remind  # noqa: E402

TZ = ZoneInfo('Asia/Dubai')                       # UTC+4 all year


def at(day, h, m=0):
    return remind.at(day, (h, m), TZ)


NOW = at('2026-10-12', 10)


def test_a_seven_day_task_emails_now_three_days_before_and_on_the_due_day():
    s = remind.schedule('2026-10-12', 7, NOW, 9, 3, True, '15:00', TZ)
    assert s['due_day'] == '2026-10-19' and s['meet_start'] == at('2026-10-19', 15)
    assert s['mails'] == [('start', NOW), ('reminder', at('2026-10-16', 9)), ('meeting', at('2026-10-19', 9))]
    assert dt.datetime.fromtimestamp(s['meet_start'], dt.timezone.utc).hour == 11


def test_short_tasks_late_settings_and_early_meetings():
    assert remind.schedule('2026-10-12', 3, NOW, 9, 3, False, '15:00', TZ)['mails'] == [
        ('reminder', at('2026-10-14', 9)), ('meeting', at('2026-10-15', 9))]
    assert [k for k, _ in remind.schedule('2026-10-12', 1, NOW, 9, 3, False, '15:00', TZ)['mails']] == ['meeting']
    early = remind.schedule('2026-10-12', 7, NOW, 9, 3, False, '10:00', TZ)          # meeting an hour after 9:00
    assert early['mails'][-1] == ('meeting', at('2026-10-18', 9))
    late = remind.schedule('2026-10-12', 4, at('2026-10-13', 12), 9, 3, False, '15:00', TZ)
    assert [k for k, _ in late['mails']] == ['meeting']                              # the reminder time has passed
    same_day = remind.schedule('2026-10-12', 1, at('2026-10-13', 14, 45), 9, 3, False, '15:00', TZ)
    assert same_day['mails'] == []                                                    # too close to the meeting


def test_late_emails_are_sent_only_while_they_still_help():
    meet = at('2026-10-19', 15)
    assert remind.still_useful('reminder', '2026-10-19', meet, at('2026-10-19', 23, 59), TZ)
    assert not remind.still_useful('reminder', '2026-10-19', meet, at('2026-10-20', 0, 1), TZ)
    assert remind.still_useful('meeting', '2026-10-19', meet, at('2026-10-19', 14, 30), TZ)
    assert not remind.still_useful('meeting', '2026-10-19', meet, at('2026-10-19', 14, 31), TZ)


CTX = {'title': 'Calming Dog Bed', 'stage': 'Sourcing & PO', 'due_day': '2026-10-19', 'days': 7, 'owner': 'Ali Afa',
       'todo': ['the shipping cost to Australia', 'units for UAE'], 'note': 'Ask for the new sample', 'now': NOW,
       'to_names': ['Sara Khan', 'Ahmed'], 'meet_start': at('2026-10-19', 15), 'meet_minutes': 30,
       'link': 'https://meet.google.com/abc-defg-hij'}


def test_the_three_emails_read_well():
    subject, text, page = remind.texts('start', CTX, TZ)
    assert subject == 'New task: Sourcing & PO for Calming Dog Bed, due Mon 19 Oct'
    assert text.startswith('Hi Sara and Ahmed,\n\nAli Afa has started "Sourcing & PO" for Calming Dog Bed. Please have it '
                           'done by Mon 19 Oct (7 days from today).')
    assert 'What is still needed:\n- the shipping cost to Australia\n- units for UAE' in text
    assert 'Note from Ali Afa:\nAsk for the new sample' in text and text.endswith('Thanks,\nAli Afa\n')
    assert '<li>units for UAE</li>' in page and '<br>' in page
    subject, text, _ = remind.texts('reminder', CTX, TZ)
    assert subject == 'Reminder: Sourcing & PO for Calming Dog Bed is due in 7 days (Mon 19 Oct)'
    assert remind.texts('reminder', dict(CTX, now=at('2026-10-18', 9)), TZ)[0].startswith('Reminder: Sourcing & PO for '
                                                                                          'Calming Dog Bed is due tomorrow')
    subject, text, _ = remind.texts('meeting', CTX, TZ)
    assert subject == 'Update meeting: Sourcing & PO for Calming Dog Bed, Mon 19 Oct at 15:00'
    assert 'on Mon 19 Oct at 15:00 (UTC+04:00)' in text and 'Join here: https://meet.google.com/abc-defg-hij' in text
    evil = remind.texts('start', dict(CTX, title='<b>x</b>\r\nBcc: a@b.c'), TZ)
    assert '\r' not in evil[0] and '\n' not in evil[0] and '&lt;b&gt;' in evil[2]


def test_the_invite_is_a_valid_calendar_request():
    s = remind.ics('pc-board-task-7@product-checker', at('2026-10-19', 15), 30, 'Update; meeting, now',
                   'Line one\nLine two ' + 'x' * 120, ('Ali "A"', 'ali@gmail.com'),
                   [('Sara Khan', 'sara@example.com'), ('', 'ahmed@example.com')], 'https://meet.google.com/x', 1,
                   stamp=NOW)
    lines = s.split('\r\n')
    assert s.endswith('\r\n') and lines[0] == 'BEGIN:VCALENDAR' and 'METHOD:REQUEST' in lines
    assert 'DTSTART:20261019T110000Z' in lines and 'DTEND:20261019T113000Z' in lines and 'SEQUENCE:1' in lines
    assert 'SUMMARY:Update\\; meeting\\, now' in lines
    assert all(len(x.encode()) <= 75 for x in lines)
    flat = s.replace('\r\n ', '')
    assert 'DESCRIPTION:Line one\\nLine two ' + 'x' * 120 in flat
    assert 'ORGANIZER;CN="Ali A":mailto:ali@gmail.com' in flat
    assert 'ATTENDEE;CN="Sara Khan";ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:sara@example.com' in flat
    assert 'CN="ahmed@example.com"' in flat and 'LOCATION:https://meet.google.com/x' in flat


def test_names_and_days():
    assert remind.join_names(['Sara Khan']) == 'Sara'
    assert remind.join_names(['Sara', 'Ahmed', 'Li Wei']) == 'Sara, Ahmed and Li'
    assert remind.join_names([]) == '' and remind.nice_day('2026-10-19') == 'Mon 19 Oct'
    assert remind.hm('7:05') == (7, 5) and remind.hm('25:00') == (15, 0) and remind.hm(None) == (15, 0)
