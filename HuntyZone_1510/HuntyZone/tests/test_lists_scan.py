"""Reading Amazon's category menu and the Movers & Shakers / New Releases lists: link formats, fallbacks, never caching
an empty menu, one bad list not ending a scan, progress lines, and job messages."""
import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import amazon_check as ac  # noqa: E402
import collectors  # noqa: E402
import ideas  # noqa: E402
import jobs  # noqa: E402
from test_phase2 import S  # noqa: E402,F401


# ---------- the category menu ----------
def test_menu_links_in_every_format():
    links = [
        {'href': '/gp/movers-and-shakers/ref=zg_bsms_tab', 'name': 'Movers & Shakers'},           # the tab itself
        {'href': '/gp/movers-and-shakers/home/ref=zg_bsms_nav_home_0', 'name': 'Home'},
        {'href': 'https://www.amazon.com.au/gp/movers-and-shakers/kitchen/ref=zg_bsms_nav_kitchen_0', 'name': 'Kitchen & Dining'},
        {'href': '/gp/movers-and-shakers/sporting-goods?ref_=Oct_d_omwf_S', 'name': 'Sports, Fitness & Outdoors'},
        {'href': '/gp/movers-and-shakers/pet-supplies/4851626051', 'name': 'Pet Supplies'},
        {'href': '/gp/movers-and-shakers/pet-supplies/4851626051/ref=x', 'name': 'Cat Toys'},       # a sub-category
        {'href': '/gp/movers-and-shakers/garden/ref=zg_bsms_garden_sm', 'name': 'See More'},
        {'href': '/gp/movers-and-shakers/garden/ref=zg_bsms_nav_garden_0', 'name': 'Garden'},
        {'href': '/gp/movers-and-shakers/toys', 'name': 'Toys & Games'},
        {'href': '/gp/movers-and-shakers/12345', 'name': 'A node only'},
        {'href': '/dp/B0AU000001', 'name': 'a product'},
    ]
    cats = ideas.parse_categories(links)
    assert [c['slug'] for c in cats] == ['home', 'kitchen', 'sporting-goods', 'pet-supplies', 'garden', 'toys']
    by = {c['slug']: c for c in cats}
    assert by['pet-supplies']['name'] == 'Pet Supplies' and by['garden']['name'] == 'Garden'
    assert by['toys']['risky'] and not by['home']['risky']


def test_see_more_only_gets_a_name_from_the_slug():
    cats = ideas.parse_categories([{'href': '/gp/new-releases/office-products/ref=zg_bsnr_sm', 'name': 'See more'}])
    assert cats == [{'slug': 'office-products', 'name': 'Office Products', 'risky': False}]


class FakePage:
    def __init__(self, site, menus):
        self.site, self.menus, self.url = site, menus, ''

    async def wait_for_function(self, *a, **k):
        pass

    async def evaluate(self, js, arg=None):
        return self.menus.get(self.url[len(self.site):], [])

    async def content(self):
        return '<html>menu missing</html>'

    async def screenshot(self, path):
        Path(path).write_bytes(b'png')

    async def close(self):
        pass


class FakeChecker:
    def __init__(self, menus, code='AU', fail=None):
        self.site = ac.MARKETS[code]['site']
        self.menus, self.fail, self.logs, self.visited = menus, fail, [], []
        self.ctx = self

    async def start(self):
        pass

    async def new_page(self):
        self.page = FakePage(self.site, self.menus)
        return self.page

    async def _goto(self, page, url, code):
        self.visited.append(url[len(self.site):])
        if self.fail:
            raise self.fail
        page.url = url

    def log(self, m):
        self.logs.append(m)


def test_menu_falls_back_to_best_sellers_then_to_built_in_categories(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, 'APP_DIR', tmp_path)
    menu = [{'href': '/gp/bestsellers/%s/ref=zg_bs_nav_%s_0' % (s, s), 'name': n} for s, n in (
        ('amazon-renewed', 'Amazon Renewed'), ('appliances', 'Appliances'), ('kitchen', 'Kitchen & Dining'),
        ('coins', 'Collectible Coins'), ('pet-supplies', 'Pet Supplies'), ('garden', 'Garden'))]
    ch = FakeChecker({'/gp/bestsellers': menu})
    got = asyncio.run(ideas.categories(ch, 'AU'))
    assert [c['slug'] for c in got] == ['amazon-renewed', 'appliances', 'kitchen', 'coins', 'pet-supplies', 'garden']
    assert ch.visited == ['/gp/bestsellers']                               # Best Sellers first: it has the menu
    assert [c['slug'] for c in ideas.friendly_first([c for c in got if not c['risky']])] == ['kitchen', 'garden', 'pet-supplies']

    stray = [{'href': '/gp/new-releases/coins/ref=x', 'name': ''}, {'href': '/gp/new-releases/hpc/ref=x', 'name': ''}]
    ch = FakeChecker({'/gp/movers-and-shakers': stray})                    # an emptied page: stray links are not a menu
    assert all(c.get('default') for c in asyncio.run(ideas.categories(ch, 'AU')))
    assert ch.visited == ['/gp/bestsellers', '/gp/movers-and-shakers']
    assert (tmp_path / 'debug' / 'categories_AU.html').exists()            # kept for a closer look
    assert not ch.logs                                                     # the log file only: no message on screen

    ch = FakeChecker({}, 'AE')
    cats = asyncio.run(ideas.categories(ch, 'AE'))
    assert cats and all(c['default'] for c in cats) and 'home' in [c['slug'] for c in cats]


def test_a_blocked_menu_page_says_so():
    ch = FakeChecker({}, fail=ac.CaptchaTimeout('Amazon captcha was not solved in 3 minutes'))
    with pytest.raises(ac.BlockedError, match='captcha'):
        asyncio.run(ideas.categories(ch, 'AU'))


def test_same_page_ignores_ref_tails_but_not_redirects():
    f = ac._same_page
    assert f('https://www.amazon.com.au/gp/movers-and-shakers/ref=zg_x', 'https://www.amazon.com.au/gp/movers-and-shakers')
    assert f('https://www.amazon.com/Name/dp/B0X?th=1', 'https://www.amazon.com/dp/B0X')
    assert not f('https://www.amazon.com.au/', 'https://www.amazon.com.au/gp/movers-and-shakers/home')
    assert not f('https://www.amazon.com/gp/movers-and-shakers', 'https://www.amazon.ae/gp/movers-and-shakers')


# ---------- the list scan ----------
def _items(n, slug):
    return [{'asin': 'B0%s%05d' % (slug[:3].upper(), i), 'title': 't', 'image': '', 'market': 'AU', 'url': '',
             'category': slug, 'kind': 'movers', 'slug': slug, 'deal': False, 'rank': i + 1, 'pct': 10,
             'rank_now': None, 'rank_before': None, 'rating': None, 'reviews': None, 'price': None} for i in range(n)]


def _scan_setup(S, monkeypatch, cats, read):
    calls = {'cats': 0, 'active': 0, 'peak': 0}

    async def categories(checker, code):
        calls['cats'] += 1
        return cats() if callable(cats) else cats

    async def read_list(checker, code, kind, slug, name):
        calls['active'] += 1
        calls['peak'] = max(calls['peak'], calls['active'])
        await asyncio.sleep(0.01)
        calls['active'] -= 1
        return read(kind, slug)
    monkeypatch.setattr(ideas, 'categories', categories)
    monkeypatch.setattr(ideas, 'read_list', read_list)

    class Checker:
        async def start(self):
            pass
    S.checker = Checker()
    return collectors.Collectors(S), calls


def test_an_empty_menu_is_never_cached(S, monkeypatch):
    answers = [[], [{'slug': 'home', 'name': 'Home', 'risky': False}]]
    c, calls = _scan_setup(S, monkeypatch, lambda: answers.pop(0), lambda k, s: _items(40, s))
    with pytest.raises(RuntimeError, match='No categories found on Amazon Australia'):
        asyncio.run(c.lists({'market': 'AU'}))
    out = asyncio.run(c.lists({'market': 'AU'}))
    assert calls['cats'] == 2 and out['status'] == 'ok'
    asyncio.run(c.lists({'market': 'AU'}))
    assert calls['cats'] == 2                                               # a good menu is kept


def test_one_bad_list_does_not_end_the_scan_and_progress_is_reported(S, monkeypatch):
    cats = [{'slug': s, 'name': s.title(), 'risky': False} for s in ('home', 'kitchen', 'garden')]

    def read(kind, slug):
        if slug == 'kitchen' and kind == 'new':
            raise RuntimeError('page crashed')
        return _items(40, slug)
    c, calls = _scan_setup(S, monkeypatch, cats, read)
    lines = []
    out = asyncio.run(c.lists({'market': 'AU'}, lines.append))
    assert out['status'] == 'ok' and '5 lists read' in out['note'] and '1 failed' in out['note']
    assert lines[0].startswith('Australia: opening the browser')
    assert any('list 6 of 6' in l for l in lines) and any('left)' in l for l in lines)
    assert calls['peak'] == 2                                               # your own scan: two at a time
    assert S.db.one('SELECT COUNT(*) AS n FROM list_snapshot')['n'] == 5


def test_background_scan_reads_one_list_at_a_time(S, monkeypatch):
    cats = [{'slug': s, 'name': s, 'risky': False} for s in ('home', 'kitchen')]
    c, calls = _scan_setup(S, monkeypatch, cats, lambda k, s: _items(40, s))
    asyncio.run(c.lists({'market': 'AU'}))
    assert calls['peak'] == 1


def test_a_captcha_stops_the_scan_but_keeps_what_was_read(S, monkeypatch):
    cats = [{'slug': s, 'name': s, 'risky': False} for s in ('home', 'kitchen', 'garden')]

    def read(kind, slug):
        if slug == 'garden':
            raise ac.CaptchaTimeout('Amazon captcha was not solved in 3 minutes')
        return _items(40, slug)
    c, _ = _scan_setup(S, monkeypatch, cats, read)
    out = asyncio.run(c.lists({'market': 'AU'}))
    assert out['status'] == 'ok' and 'stopped early' in out['note'] and 'captcha' in out['note']

    c, _ = _scan_setup(S, monkeypatch, cats, lambda k, s: (_ for _ in ()).throw(ac.CaptchaTimeout('captcha')))
    with pytest.raises(ac.CaptchaTimeout):                                  # nothing read at all: the run failed
        asyncio.run(c.lists({'market': 'AU'}))


def test_built_in_categories_that_amazon_lacks_are_dropped(S, monkeypatch):
    cats = [{'slug': s, 'name': s, 'risky': False, 'default': True} for s in ('home', 'sports', 'sporting')]

    def read(kind, slug):
        if slug == 'sports':
            raise ideas.ListMissing('no list')
        return _items(40, slug)
    c, _ = _scan_setup(S, monkeypatch, cats, read)
    out = asyncio.run(c.lists({'market': 'AE'}))
    assert 'built-in' in out['note']
    assert [x['slug'] for x in c.cats['AE']['cats']] == ['home', 'sporting']


# ---------- job messages ----------
def test_jobs_start_with_a_line_and_hear_general_messages():
    loop = asyncio.new_event_loop()
    import threading
    threading.Thread(target=loop.run_forever, daemon=True).start()
    try:
        m = jobs.JobManager(lambda c: asyncio.run_coroutine_threadsafe(c, loop))
        gate = asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop)
        gate.result(2)
        release = threading.Event()

        async def work(progress):
            await asyncio.get_running_loop().run_in_executor(None, release.wait)
            return 1
        jid = m.start('radar', work)
        assert m.get(jid)['log'] == ['Waiting for the browser…']
        m.broadcast('Australia: please solve the captcha in the browser window')
        assert m.get(jid)['log'][-1].startswith('Australia: please solve')
        release.set()
        for _ in range(100):
            if m.get(jid)['status'] == 'done':
                break
            time.sleep(0.01)
        m.broadcast('later message')
        assert m.get(jid)['log'][-1] != 'later message'                    # finished jobs are left alone
    finally:
        loop.call_soon_threadsafe(loop.stop)


def test_your_own_scan_that_reads_nothing_says_why(S, monkeypatch):
    cats = [{'slug': 'home', 'name': 'Home', 'risky': False, 'default': True}]
    c, _ = _scan_setup(S, monkeypatch, cats, lambda k, s: (_ for _ in ()).throw(ideas.ListMissing('no list')))
    with pytest.raises(RuntimeError, match='Could not read any .* UAE .*debug'):
        asyncio.run(c.lists({'market': 'AE'}, lambda m: None))
    assert asyncio.run(c.lists({'market': 'AE'}))['status'] == 'empty'      # the automatic refresh just notes it
