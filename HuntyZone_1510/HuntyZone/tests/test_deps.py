"""deps.py: start.bat's check that a Python has what the app needs."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import deps  # noqa: E402


def test_requirements_are_read_and_checked_by_version(tmp_path):
    req = tmp_path / 'requirements.txt'
    req.write_text('# what the app needs\npytest>=1.0\npytest>=999  # too new\nno-such-package-xyz>=1\n\n', encoding='utf-8')
    assert deps.requirements(req) == [('pytest', '1.0'), ('pytest', '999'), ('no-such-package-xyz', '1')]
    gone = deps.missing(req)
    assert len(gone) == 2 and gone[0].startswith('pytest>=999 (have ') and gone[1] == 'no-such-package-xyz'


def test_the_apps_own_python_has_what_it_needs():
    assert deps.missing() == [] and deps.main() == 0
