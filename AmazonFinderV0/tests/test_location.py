"""Delivery locations: the header read from both lines, one location popup at a time, and the manual fallback only
for work you started yourself."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import amazon_check as ac  # noqa: E402


class Page:
    def __init__(self, header, url=''):
        self.header, self.url = header, url

    async def evaluate(self, js, arg=None):
        return [x for x in self.header if x]

    async def close(self):
        pass

    async def wait_for_timeout(self, ms):
        await asyncio.sleep(0)


class Ctx:
    def __init__(self, pages):
        self.pages = pages

    async def new_page(self):
        return self.pages.pop(0)


def checker(pages):
    c = ac.Checker(log=lambda m: c.logs.append(m))
    c.logs = []
    c.ctx = Ctx(pages)

    async def start():
        pass

    async def goto(page, url, code):
        page.url = url
    c.start, c._goto = start, goto
    return c


def test_header_text_uses_both_lines():
    c = checker([])
    t = asyncio.run(c._location_text(Page(['Delivering to Dubai', 'Update location'])))
    assert t == 'Delivering to Dubai Update location' and c._location_ok('AE', t)
    assert not c._location_ok('AE', asyncio.run(c._location_text(Page(['Deliver to', 'Abu Dhabi']))))


def test_only_one_location_popup_at_a_time():
    pages = [Page(['Deliver to', 'nowhere']) for _ in range(3)]
    c = checker(list(pages))
    live, peak = [0], [0]

    async def set_location(page, code):
        live[0] += 1
        peak[0] = max(peak[0], live[0])
        await asyncio.sleep(0.02)
        page.header = ['Deliver to', ac.MARKETS[code]['location']]
        live[0] -= 1
        return None
    c._set_location = set_location

    async def go():
        return await asyncio.gather(*(c.ensure_location(m, manual=False) for m in ac.MARKETS))
    res = asyncio.run(go())
    assert all(ok for ok, _ in res) and peak[0] == 1
    assert sum('✓' in m for m in c.logs) == 3


def test_manual_fallback_only_for_your_own_work():
    asked = []

    def make():
        c = checker([Page(['Deliver to', 'Abu Dhabi'])])

        async def set_location(page, code):
            return 'Please enter a valid city'

        async def ask(page, code, problem=None):
            asked.append(problem)
            page.header = ['Delivering to Dubai', 'Update location']       # you picked Dubai in the window
            return await c._location_text(page)
        c._set_location, c._ask_location = set_location, ask
        return c
    c = make()
    ok, text = asyncio.run(c.ensure_location('AE', manual=True))
    assert ok and asked == ['Please enter a valid city'] and 'Dubai' in text

    c = make()

    async def background():
        token = ac.BACKGROUND.set(True)
        try:
            return await c.ensure_location('AE')
        finally:
            ac.BACKGROUND.reset(token)
    ok, _ = asyncio.run(background())
    assert not ok and len(asked) == 1                                       # never pops up in the background
    assert 'the form said: Please enter a valid city' in c.logs[-1]


def test_waiting_for_you_gives_up_after_the_limit():
    c = checker([])
    page = Page(['Deliver to', 'Abu Dhabi'])
    shown = []

    async def show(p=None, user=True):
        shown.append('show')

    async def release():
        shown.append('release')

    async def opened(p):
        return True
    c.show, c._release, c._open_location_popup = show, release, opened
    text = asyncio.run(c._ask_location(page, 'AE', wait_s=0.01))
    assert not c._location_ok('AE', text) and shown == ['show', 'release']
    assert 'choose Dubai' in c.logs[0]
