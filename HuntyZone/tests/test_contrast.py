"""Every text colour in both themes must reach 4.5:1 against the backgrounds it sits on."""
import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / 'ui' / 'css' / 'tokens.css').read_text(encoding='utf-8')
TEXT = ('ink', 'ink-2', 'muted', 'accent')
BACKGROUNDS = ('page', 'surface', 'sunk', 'hover')
PAIRS = [('good-text', 'good-soft'), ('warn-text', 'warn-soft'), ('bad-text', 'bad-soft'), ('weak-text', 'weak-soft'),
         ('accent-ink', 'accent-fill'), ('accent-strong', 'accent-soft'), ('ink', 'accent-soft'),
         ('good-text', 'surface'), ('warn-text', 'surface'), ('bad-text', 'surface')]


def tokens(selector):
    start = CSS.index(selector)
    body = CSS[CSS.index('{', start) + 1:CSS.index('}', start)]
    return dict(re.findall(r'--([\w-]+):\s*(#[0-9a-fA-F]{6})', body))


def luminance(hex_):
    c = [int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def check(theme):
    bad = []
    pairs = [(t, b) for t in TEXT for b in BACKGROUNDS] + PAIRS
    for fg, bg in pairs:
        r = contrast(theme[fg], theme[bg])
        if r < 4.5:
            bad.append('%s on %s = %.2f' % (fg, bg, r))
    return bad


def test_light_theme_contrast():
    assert check(tokens(':root, :root[data-theme="light"]')) == []


def test_dark_theme_contrast():
    assert check(tokens(':root[data-theme="dark"]')) == []


def test_both_themes_define_the_same_colours():
    light, dark = tokens(':root, :root[data-theme="light"]'), tokens(':root[data-theme="dark"]')
    assert set(light) == set(dark)
