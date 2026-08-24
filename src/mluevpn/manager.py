"""Connection manager: owns the running backends and marshals their events.

Backends run on worker threads and know nothing about GTK. This layer is the
seam: it loads credentials out of the store, starts the right backend, and
hands every callback back to the GTK main loop via GLib.idle_add so the UI
only ever touches widgets from the main thread.
"""

from __future__ import annotations

import logging
from typing import Callable

from gi.repository import GLib

from . import backends, db
from .backends import Credentials, State
from .crypto import SecretsUnavailable
from .db import Profile, Store

log = logging.getLogger(__name__)


class VpnManager:
    """Tracks one backend per profile id."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self._backends: dict[int, backends.Backend] = {}
        #: (profile_id, state, detail) -> None, on the GTK main thread.
        self.on_state: Callable[[int, State, str], None] = lambda *_: None
        #: (profile_id, line) -> None, on the GTK main thread.
        self.on_log: Callable[[int, str], None] = lambda *_: None

    # ------------------------------------------------------------------ query

    def state_of(self, profile_id: int | None) -> State:
        backend = self._backends.get(profile_id) if profile_id else None
        return backend.state if backend else State.DISCONNECTED

    def any_active(self) -> bool:
        return any(b.state.is_active for b in self._backends.values())

    def active_profile_ids(self) -> list[int]:
        return [pid for pid, b in self._backends.items() if b.state.is_active]

    # ------------------------------------------------------------- lifecycle

    def connect(self, profile: Profile) -> None:
        """Start a connection. Raises ValueError if it cannot even be attempted."""
        if profile.id is None:
            raise ValueError("Save the profile before connecting.")
        if self.state_of(profile.id).is_active:
            return

        creds = self._credentials_for(profile)
        kwargs = {}
        if profile.kind == db.KIND_OPENCONNECT:
            kwargs["on_servercert"] = self._servercert_handler(profile.id)

        backend = backends.for_profile(
            profile,
            creds,
            self._log_handler(profile.id),
            self._state_handler(profile.id),
            **kwargs,
        )
        self._backends[profile.id] = backend
        self.store.add_history(profile.id, "connect", profile.target)
        backend.start()

    def disconnect(self, profile: Profile) -> None:
        backend = self._backends.get(profile.id)
        if backend is None:
            return
        self.store.add_history(profile.id, "disconnect", profile.target)
        backend.stop()

    def disconnect_all(self, wait: float = 20.0) -> None:
        """Used on shutdown so we never leave an orphaned tunnel behind."""
        active = [b for b in self._backends.values() if b.state.is_active]
        for backend in active:
            backend.stop()
        for backend in active:
            backend.wait(timeout=wait)

    def forget(self, profile_id: int) -> None:
        self._backends.pop(profile_id, None)

    # --------------------------------------------------------------- internals

    def _credentials_for(self, profile: Profile) -> Credentials:
        try:
            password = self.store.get_secret(profile.id, db.CRED_PASSWORD) or ""
            servercert = self.store.get_secret(profile.id, db.CRED_SERVERCERT) or ""
            sudo = ""
            if profile.kind == db.KIND_OPENCONNECT:
                sudo = self.store.get_app_secret(db.SUDO_PASSWORD) or ""
        except SecretsUnavailable as exc:
            raise ValueError(str(exc)) from exc
        return Credentials(
            username=profile.username,
            password=password,
            sudo_password=sudo,
            servercert=servercert,
        )

    def _state_handler(self, profile_id: int) -> Callable[[State, str], None]:
        def handler(state: State, detail: str) -> None:
            GLib.idle_add(self._dispatch_state, profile_id, state, detail)

        return handler

    def _dispatch_state(self, profile_id: int, state: State, detail: str) -> bool:
        if state in (State.CONNECTED, State.FAILED, State.DISCONNECTED):
            self.store.add_history(profile_id, state.value, detail)
        self.on_state(profile_id, state, detail)
        return GLib.SOURCE_REMOVE

    def _log_handler(self, profile_id: int) -> Callable[[str], None]:
        def handler(line: str) -> None:
            GLib.idle_add(self._dispatch_log, profile_id, line)

        return handler

    def _dispatch_log(self, profile_id: int, line: str) -> bool:
        self.on_log(profile_id, line)
        return GLib.SOURCE_REMOVE

    def _servercert_handler(self, profile_id: int) -> Callable[[str], None]:
        def handler(pin: str) -> None:
            GLib.idle_add(self._store_servercert, profile_id, pin)

        return handler

    def _store_servercert(self, profile_id: int, pin: str) -> bool:
        try:
            self.store.set_secret(profile_id, db.CRED_SERVERCERT, pin)
        except Exception:  # noqa: BLE001
            log.exception("could not store server certificate pin")
        return GLib.SOURCE_REMOVE
