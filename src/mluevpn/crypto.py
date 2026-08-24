"""Secret encryption for the credential store.

Credentials live in SQLite as Fernet tokens. The Fernet key itself never
touches the database: it is kept in the system keyring (gnome-keyring via
libsecret, already running under Omarchy). If no keyring is reachable -- a
locked session, a TTY-only login -- we fall back to a 0600 key file so the app
still works, and say so loudly in the UI.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken

from .config import KEY_FALLBACK_PATH, ensure_dirs

log = logging.getLogger(__name__)

KEYRING_SERVICE = "mluevpn"
KEYRING_USER = "master-key"


class SecretsUnavailable(RuntimeError):
    """Raised when the master key can be neither loaded nor created."""


@dataclass(frozen=True)
class KeySource:
    """Where the master key came from, so the UI can warn about fallbacks."""

    backend: str  # "keyring" | "file"
    detail: str

    @property
    def is_secure(self) -> bool:
        return self.backend == "keyring"


class SecretBox:
    """Encrypts and decrypts the values stored in the credentials table."""

    def __init__(self) -> None:
        key, self.source = _load_or_create_key()
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, token: bytes) -> str:
        try:
            return self._fernet.decrypt(token).decode("utf-8")
        except InvalidToken as exc:
            # Almost always means the keyring entry was wiped or replaced while
            # the database survived. Nothing is recoverable; the user has to
            # re-enter that credential.
            raise SecretsUnavailable(
                "Stored credential could not be decrypted - the master key has "
                "changed. Re-enter the password for this profile."
            ) from exc


def _load_or_create_key() -> tuple[bytes, KeySource]:
    try:
        import keyring
    except Exception as exc:  # pragma: no cover - keyring is a hard dependency
        log.warning("keyring import failed: %s", exc)
    else:
        try:
            existing = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
            if existing:
                return existing.encode("ascii"), KeySource(
                    "keyring", str(keyring.get_keyring())
                )
            fresh = Fernet.generate_key()
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, fresh.decode("ascii"))
            return fresh, KeySource("keyring", str(keyring.get_keyring()))
        except Exception as exc:
            log.warning("keyring unavailable (%s); falling back to key file", exc)

    return _file_key()


def _file_key() -> tuple[bytes, KeySource]:
    ensure_dirs()
    detail = str(KEY_FALLBACK_PATH)
    if KEY_FALLBACK_PATH.exists():
        if KEY_FALLBACK_PATH.stat().st_mode & 0o077:
            KEY_FALLBACK_PATH.chmod(0o600)
        return KEY_FALLBACK_PATH.read_bytes().strip(), KeySource("file", detail)

    key = Fernet.generate_key()
    # Create with 0600 from the outset rather than write-then-chmod, which
    # would leave a readable window.
    fd = os.open(KEY_FALLBACK_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    return key, KeySource("file", detail)
