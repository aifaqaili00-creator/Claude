"""Small secrets kept encrypted for your Windows user on this PC (Windows DPAPI, the same as the Gmail app password):
the Amazon Selling Partner API keys and, later, an AI key. One file per secret in the app folder; the contents are
never written to settings.json or to the log, and nothing here prints them."""
import json
import os
from pathlib import Path

import config
import winsys

NAMES = ('spapi', 'ai')


class VaultError(Exception):
    """A secret could not be saved or read; the message is meant for you (it never contains the secret)."""


def path(name):
    if name not in NAMES:
        raise VaultError('unknown secret')
    return Path(config.APP_DIR) / ('%s.secret' % name)


def has(name):
    return path(name).is_file()


def save(name, data):
    """Encrypt and keep a small JSON-able dict."""
    p = path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp')
    try:
        tmp.write_bytes(winsys.protect(json.dumps(data).encode('utf-8')))
        os.replace(tmp, p)
    except OSError as e:
        raise VaultError('Could not save it (%s).' % e.__class__.__name__) from None


def load(name):
    """The saved dict, or None when nothing is saved. Raises VaultError when this Windows user cannot read it."""
    p = path(name)
    try:
        blob = p.read_bytes()
    except FileNotFoundError:
        return None
    try:
        return json.loads(winsys.unprotect(blob).decode('utf-8'))
    except (OSError, ValueError, UnicodeDecodeError):
        raise VaultError('The saved keys cannot be read by this Windows user: save them again.') from None


def forget(name):
    try:
        path(name).unlink()
    except FileNotFoundError:
        pass
