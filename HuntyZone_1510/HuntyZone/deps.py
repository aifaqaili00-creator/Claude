"""Does this Python have what Product Checker needs? start.bat asks each Python it could use (the .venv folder, then
your own Python). Exit code 0 when this Python is new enough and every package in requirements.txt is installed at
the version asked for and loads; otherwise 1, with what is missing."""
import importlib
import importlib.metadata as md
import re
import sys
from pathlib import Path

REQUIREMENTS = Path(__file__).with_name('requirements.txt')
MIN_PYTHON = (3, 10)


def _version(text):
    return tuple(int(x) for x in re.findall(r'\d+', text)[:3])


def requirements(path=REQUIREMENTS):
    """[(name, lowest version or '')] from lines like 'pandas>=2.0'."""
    out = []
    for line in path.read_text(encoding='utf-8').splitlines():
        name, _, low = line.split('#')[0].strip().partition('>=')
        if name.strip():
            out.append((name.strip(), low.strip()))
    return out


def missing(path=REQUIREMENTS):
    """What this Python lacks, e.g. ['pandas', 'numpy>=1.24 (have 1.21.0)']."""
    out = []
    for name, low in requirements(path):
        try:
            have = md.version(name)
        except md.PackageNotFoundError:
            out.append(name)
            continue
        if low and _version(have) < _version(low):
            out.append('%s>=%s (have %s)' % (name, low, have))
    return out


def main():
    if sys.version_info < MIN_PYTHON:
        print('Product Checker needs Python %d.%d or newer; this is %d.%d.' % (MIN_PYTHON + sys.version_info[:2]))
        return 1
    gone = missing()
    if not gone:
        for name, _ in requirements():
            try:
                importlib.import_module(name)
            except Exception as e:                       # installed, but broken or half installed
                gone.append('%s (%s)' % (name, e))
    if gone:
        print('Missing: ' + ', '.join(gone))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
