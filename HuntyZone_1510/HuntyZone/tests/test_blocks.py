"""Amazon block pages become statuses (gaps), background work never pops the window up, and the window
shows / hides correctly when several captchas overlap."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import amazon_check as ac  # noqa: E402
import storage  # noqa: E402


class Loc:
    def __init__(self, n):
        self.n = n

    async def count(self):
        return self.n

    @property
    def first(self):
        return self

    async def click(self, **kw):
        pass


class Page:
    def __init__(self, url, captcha=0, info=None, solve_after=None):
        self.url, self.captcha, self.info, self.waits = url, captcha, info or {}, 0
        self.solve_after = solve_after

    def locator(self, sel):
        return Loc(self.captcha if 'aptcha' in sel else 0)

    async def evaluate(self, js):
        return self.info

    async def wait_for_timeout(self, ms):
        self.waits += 1
        if self.solve_after is not None and self.waits >= self.solve_after:
            self.captcha = 0

    async def wait_for_load_state(self, *a):
        pass

    async def reload(self):
        pass

    async def bring_to_front(self):
        pass


def checker():
    c = ac.Checker(log=lambda m: None)
    c.ctx = object()
    c.windows = []

    async def window(state):
        c.windows.append(state)

    async def start():
        pass
    c._window = window
    c.start = start
    return c


def run(coro):
    return asyncio.run(coro)


def test_result_count_parsing():
    assert ac.parse_results_info('1-48 of over 2,000 results for "moving bags"') == (2000, True)
    assert ac.parse_results_info('1-16 of 312 results for "sauna hat"') == (312, False)
    assert ac.parse_results_info('48 results for "x"') == (48, False)
    assert ac.parse_results_info('') == (None, None)
    assert ac.parse_results_info(None) == (None, None)


def test_parse_health():
    good = {'title': 't', 'price': 1.0}
    bad = {'title': '', 'price': None, 'delivery': ''}
    assert ac.parse_health([good] * 3 + [bad]) == 0.75
    assert ac.parse_health([]) is None


def test_block_kinds():
    c = checker()
    assert run(c._block_kind(Page('https://www.amazon.com.au/s?k=x'), 'AU', 200)) is None
    assert run(c._block_kind(Page('https://amazon.com.au/s?k=x'), 'AU', 200)) is None
    assert run(c._block_kind(Page('https://www.amazon.com/s?k=x'), 'AU', 200)) == 'geo_redirect'
    assert run(c._block_kind(Page('https://www.amazon.ae/s?k=x', info={'dog': True}), 'AE', 503)) == 'blocked'
    assert run(c._block_kind(Page('https://www.amazon.ae/s?k=x', info={'robot': True}), 'AE', 200)) == 'blocked'
    assert run(c._block_kind(Page('https://www.amazon.com/s?k=x'), 'US', 503)) == 'blocked'


def test_background_captcha_never_shows_the_window():
    c = checker()

    async def go():
        ac.BACKGROUND.set(True)
        await c._get_past_blocks(Page('https://www.amazon.com.au/', captcha=1), 'AU')
    with pytest.raises(ac.CaptchaRequired) as e:
        run(go())
    assert e.value.status == 'captcha' and c.windows == [] and not c.visible


def test_user_captcha_shows_then_hides():
    c = checker()
    run(c._get_past_blocks(Page('https://www.amazon.com.au/', captcha=1, solve_after=2), 'AU'))
    assert c.windows == ['normal', 'minimized'] and not c.visible


def test_overlapping_captchas_keep_the_window_up_until_both_are_solved():
    c = checker()

    async def go():
        await c.show(user=False)
        await c.show(user=False)
        await c._release()
        assert c.visible
        await c._release()
        assert not c.visible
        await c.show()                        # the user pins it
        await c.show(user=False)
        await c._release()
        assert c.visible and c.pinned
        await c.hide()
        assert not c.visible
    run(go())


def test_http_background_flag_is_off_by_default():
    assert ac.BACKGROUND.get() is False


def test_ae_searches_ask_for_english():
    assert ac.search_url('AE', 'tower fan').endswith('language=en_AE')
    assert 'language' not in ac.search_url('US', 'tower fan')
    assert '&page=2' in ac.search_url('AU', 'x', 2)


def test_block_statuses_are_gaps_in_storage():
    for st in ('captcha', 'blocked', 'geo_redirect', 'empty_suspect', 'timeout', 'offline'):
        assert st in storage.STATUSES and st not in storage.VALID
        assert storage.serp_status({'status': st, 'error': 'x'}) == st
