"""Every id in the page is used once: the scripts find elements by id, and a second element with the same id is never
wired up (Product ideas' market buttons once did nothing because Market intelligence had the same id)."""
import collections
import re
from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / 'ui' / 'index.html').read_text(encoding='utf-8')


def test_every_id_is_unique():
    counts = collections.Counter(re.findall(r'\bid="([^"]+)"', HTML))
    assert {k: n for k, n in counts.items() if n > 1} == {}
