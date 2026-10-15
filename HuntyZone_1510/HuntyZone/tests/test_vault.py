"""Small secrets kept encrypted for this Windows user (vault.py)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import vault  # noqa: E402
import winsys  # noqa: E402


@pytest.fixture
def box(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'APP_DIR', tmp_path)
    monkeypatch.setattr(winsys, 'protect', lambda b: b'enc:' + b[::-1])
    monkeypatch.setattr(winsys, 'unprotect', lambda b: b[4:][::-1] if b.startswith(b'enc:') else (_ for _ in ()).throw(OSError()))
    return tmp_path


def test_save_load_forget(box):
    assert not vault.has('spapi') and vault.load('spapi') is None
    vault.save('spapi', {'client_id': 'x', 'refresh': {'FE': 'Atzr|t'}})
    assert vault.has('spapi') and vault.load('spapi') == {'client_id': 'x', 'refresh': {'FE': 'Atzr|t'}}
    assert b'Atzr' not in (box / 'spapi.secret').read_bytes()[:4] and not (box / 'spapi.tmp').exists()
    vault.forget('spapi')
    vault.forget('spapi')                                                       # twice is fine
    assert not vault.has('spapi')


def test_an_unreadable_secret_says_so_without_its_contents(box):
    (box / 'ai.secret').write_bytes(b'garbage')
    with pytest.raises(vault.VaultError, match='cannot be read by this Windows user'):
        vault.load('ai')


def test_only_known_secrets(box):
    with pytest.raises(vault.VaultError):
        vault.save('../settings', {})
