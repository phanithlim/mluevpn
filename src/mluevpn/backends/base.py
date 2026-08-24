"""Shared machinery for driving an interactive VPN client.

Both supported clients are console programs that ask questions on a tty, so
every backend follows the same shape: spawn under a pty, answer prompts from
the credential store, then sit reading output until the link drops or the user
disconnects. The work happens on a worker thread; progress is reported through
two callbacks that the UI marshals back onto the GTK main loop.
"""

from __future__ import annotations

import enum
import shlex
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from ..db import Profile


class State(enum.Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    AUTHENTICATING = "authenticating"
    CONNECTED = "connected"
    DISCONNECTING = "disconnecting"
    FAILED = "failed"

    @property
    def is_busy(self) -> bool:
        return self in (State.CONNECTING, State.AUTHENTICATING, State.DISCONNECTING)

    @property
    def is_active(self) -> bool:
        """True when a tunnel exists or is being brought up."""
        return self in (
            State.CONNECTING,
            State.AUTHENTICATING,
            State.CONNECTED,
            State.DISCONNECTING,
        )


class ConnectionError_(RuntimeError):
    """A connection attempt failed for a reason worth showing the user."""


@dataclass
class Credentials:
    """Everything a backend may need to answer prompts with."""

    username: str = ""
    password: str = ""
    sudo_password: str = ""
    servercert: str = ""


# (message) -> None, called from the worker thread.
LogFn = Callable[[str], None]
# (state, detail) -> None, called from the worker thread.
StateFn = Callable[[State, str], None]


class Backend(ABC):
    """Base class for a single profile's connection lifecycle."""

    #: Executable this backend needs on PATH.
    binary: str = ""

    def __init__(
        self,
        profile: "Profile",
        credentials: Credentials,
        on_log: LogFn,
        on_state: StateFn,
    ) -> None:
        self.profile = profile
        self.creds = credentials
        self._on_log = on_log
        self._on_state = on_state
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._state = State.DISCONNECTED
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ state

    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    def set_state(self, state: State, detail: str = "") -> None:
        with self._lock:
            if self._state is state:
                return
            self._state = state
        self._on_state(state, detail)

    def log(self, message: str) -> None:
        for line in message.rstrip().splitlines():
            if line.strip():
                self._on_log(line.rstrip())

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Begin connecting on a worker thread. Returns immediately."""
        if self._thread and self._thread.is_alive():
            raise RuntimeError("This profile is already connecting")
        self._stop.clear()
        self.set_state(State.CONNECTING)
        self._thread = threading.Thread(
            target=self._thread_main,
            name=f"vpn-{self.profile.name}",
            daemon=True,
        )
        self._thread.start()

    def _thread_main(self) -> None:
        try:
            self._run()
        except ConnectionError_ as exc:
            self.log(f"error: {exc}")
            self.set_state(State.FAILED, str(exc))
        except Exception as exc:  # noqa: BLE001 - worker thread must not die silently
            self.log(f"unexpected error: {exc!r}")
            self.set_state(State.FAILED, f"{type(exc).__name__}: {exc}")
        else:
            # _run returning without an explicit failure means the tunnel is
            # down for an ordinary reason (user disconnect, or the server hung
            # up). FAILED is left alone so its detail survives.
            if self.state is not State.FAILED:
                self.set_state(State.DISCONNECTED)

    def stop(self) -> None:
        """Ask for a clean disconnect. Returns immediately."""
        if not self.state.is_active:
            return
        self.set_state(State.DISCONNECTING)
        self._stop.set()
        threading.Thread(target=self._safe_teardown, daemon=True).start()

    def _safe_teardown(self) -> None:
        try:
            self._teardown()
        except Exception as exc:  # noqa: BLE001
            self.log(f"error during disconnect: {exc}")
            self.set_state(State.DISCONNECTED)

    def wait(self, timeout: float | None = None) -> None:
        """Block until the worker thread finishes. Used on app shutdown."""
        if self._thread:
            self._thread.join(timeout)

    # --------------------------------------------------------- for subclasses

    @abstractmethod
    def _run(self) -> None:
        """Connect and stay running until the tunnel ends."""

    @abstractmethod
    def _teardown(self) -> None:
        """Tear the tunnel down in response to stop()."""

    def extra_args(self) -> list[str]:
        """User-supplied extra CLI arguments, parsed shell-style."""
        if not self.profile.extra_args.strip():
            return []
        try:
            return shlex.split(self.profile.extra_args)
        except ValueError as exc:
            raise ConnectionError_(f"Could not parse extra arguments: {exc}") from exc
