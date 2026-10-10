"""Exchange rates: the ECB file, the dirham peg, conversion, the saved copy, offline fallback and price ranges."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import fxrates  # noqa: E402

XML = (Path(__file__).parent / 'fixtures' / 'ecb_daily.xml').read_text()


@pytest.fixture(autouse=True)
def app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'APP_DIR', tmp_path)
    monkeypatch.setattr(fxrates, '_rates', None)
    yield tmp_path


def test_ecb_file_gives_rates_per_us_dollar():
    d = fxrates.parse_ecb(XML)
    assert d['as_of'] == '2026-10-09' and d['source'] == 'ECB'
    assert d['per_usd']['AUD'] == pytest.approx(1.6416 / 1.08)
    assert d['per_usd']['EUR'] == pytest.approx(1 / 1.08)
    assert d['per_usd']['AED'] == 3.6725                                    # fixed to the dollar
    with pytest.raises(ValueError):
        fxrates.parse_ecb('<Cube currency="JPY" rate="160"/>')


def test_refresh_saves_and_reuses_for_a_day(app_dir):
    calls = []

    def fetch():
        calls.append(1)
        return XML
    fxrates.refresh(fetch=fetch, now=1000)
    assert json.loads((app_dir / 'fx.json').read_text())['as_of'] == '2026-10-09'
    fxrates.refresh(fetch=fetch, now=1000 + 3600)
    assert len(calls) == 1                                                  # fresh enough: not fetched again
    fxrates.refresh(fetch=fetch, now=1000 + 2 * 86400)
    assert len(calls) == 2


def test_offline_keeps_the_saved_rates_then_the_fixed_ones(app_dir):
    def broken():
        raise OSError('no internet')
    d = fxrates.refresh(fetch=broken, now=5)
    assert d['source'] == 'fixed' and d['per_usd']['AED'] == 3.6725
    fxrates.refresh(fetch=lambda: XML, now=10)
    d = fxrates.refresh(fetch=broken, force=True, now=20)
    assert d['source'] == 'ECB'                                             # the saved copy, not the guesses


def test_conversion_and_ranges():
    fxrates.refresh(fetch=lambda: XML, now=1)
    assert fxrates.convert(20, 'USD', 'AUD') == pytest.approx(30.4, abs=0.01)
    assert fxrates.convert(10, 'EUR', 'AUD') == pytest.approx(16.416, abs=0.01)
    r = fxrates.ranges(config.defaults())                                   # 20-50 US$
    assert r == {'US': (20, 50), 'AU': (30, 76), 'AE': (73, 184)}
    r = fxrates.ranges({**config.defaults(), 'price_currency': 'EUR', 'price_min': 50, 'price_max': 20})
    assert r['US'] == (22, 54) and r['AU'][0] < r['AU'][1]                  # swapped ends are put right


def test_profit_maths_follows_the_new_rates():
    from engine import fees, profit
    fxrates.refresh(fetch=lambda: XML, now=1)
    assert profit.fx('AU') == pytest.approx(1.52, abs=0.001)
    assert fees.DEFAULTS['fx_per_usd']['AED'] == 3.6725


def test_settings_accept_the_price_range():
    s, err = config.validate({'price_min': '25.5', 'price_max': 9999, 'price_currency': 'EUR', 'exclude_tech': 'false'})
    assert not err and s['price_min'] == 25.5 and s['price_max'] == 5000 and s['price_currency'] == 'EUR'
    assert s['exclude_tech'] is False
    s, err = config.validate({'price_currency': 'GBP', 'price_min': 'cheap'})
    assert len(err) == 2
