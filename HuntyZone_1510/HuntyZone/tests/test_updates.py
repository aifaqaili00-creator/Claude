"""Update notice (updates.py): the newest published version on GitHub Releases, newer than the one running, with its
ProductChecker.exe; drafts, pre-releases and other tags are left out; offline is not an error on screen."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import updates  # noqa: E402

EXE = {'name': 'ProductChecker.exe', 'browser_download_url': 'https://github.com/o/r/releases/download/v8.1/ProductChecker.exe'}
RELEASES = [
    {'tag_name': 'v8.1', 'body': 'Shortlist button.\n\nFaster refresh.', 'assets': [EXE], 'html_url': 'https://github.com/o/r/releases/tag/v8.1'},
    {'tag_name': 'v9.0', 'draft': True, 'assets': [EXE]},                        # not published yet
    {'tag_name': 'productchecker-v8.2-beta', 'prerelease': True},
    {'tag_name': 'scout-data-1', 'assets': []},                                  # another kind of release in the repo
    {'tag_name': 'v7.10', 'assets': [], 'html_url': 'https://github.com/o/r/releases/tag/v7.10'},
]


def test_the_newest_published_version_newer_than_this_one():
    n = updates.newest(RELEASES, '7.9')
    assert n['version'] == '8.1' and n['download'].endswith('/v8.1/ProductChecker.exe') and n['exe']
    assert n['notes'] == 'Shortlist button. Faster refresh.'
    assert updates.newest(RELEASES, '8.1') is None and updates.newest(RELEASES, '9.0') is None
    assert updates.vkey('7.10') > updates.vkey('7.9')                            # numbers, not text
    only_page = updates.newest([RELEASES[4]], '7.9')
    assert only_page['version'] == '7.10' and only_page['download'] == RELEASES[4]['html_url'] and not only_page['exe']
    assert updates.newest([{'tag_name': 'ProductChecker-v10.0', 'assets': [EXE]}], '7.9')['version'] == '10.0'


def test_check_is_quiet_when_off_or_offline():
    s = {'update_check': True, 'update_repo': 'o/r'}
    newer = [{'tag_name': 'v999.0', 'assets': [EXE]}]                              # newer than whatever version runs
    assert updates.check(s, fetcher=lambda repo: newer, now=1)['latest']['version'] == '999.0'
    def offline(repo):
        raise OSError('no network')
    st = updates.check(s, fetcher=offline, now=2)
    assert st['error'] == 'no network' and st['checked_at'] == 2
    assert updates.check({'update_check': False, 'update_repo': 'o/r'})['latest'] is None
    assert updates.check({'update_check': True, 'update_repo': 'not a repo'})['latest'] is None
