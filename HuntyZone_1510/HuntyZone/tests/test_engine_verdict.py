"""The report in one sentence: the most telling facts first, unknown ones left out."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import verdict  # noqa: E402


def test_one_sentence():
    assert verdict.sentence('AU', label='Worth a look', units=2437, fast_local=3, dominant={'code': 'CN', 'ships': 'fba'},
                            price=44.9, left=25.2, cur='A$') == (
        'Worth a look in Australia: about 2,400 sales a month on page 1, only 3 fast local sellers, mostly Chinese FBA '
        'sellers and about A$25 of the A$45 price left for the product and shipping.')
    assert verdict.sentence('AE', label='Skip', units=95, fast_local=18, amazon=4, price=99, profit=-3.4, cur='AED',
                            trend='Declining, seasonal peak Dec') == (
        'Skip in the UAE: about 95 sales a month on page 1, 18 fast local sellers, Amazon itself sells 4 of the top '
        'listings and a loss of about AED 3 a sale at AED 99.')                    # a fifth fact (the trend) is left out
    assert verdict.sentence('US', label='Strong', units=12345, fast_local=0, price=24, profit=6.6, cur='US$', trend='Growing') == (
        'Strong in the USA: about 12,300 sales a month on page 1, no fast local sellers, about US$7 profit a sale at US$24 '
        'and searches rising.')
    assert verdict.sentence('AU', label='Weak', dominant={'code': 'XK'}) == 'Weak in Australia: mostly sellers based in XK.'
    assert verdict.sentence('AU') == 'Australia.'
    assert verdict.sentence('AU', searched=False) == ('Not searched in Australia yet: press Refresh now to see how well it '
                                                      'sells and who sells it.')
