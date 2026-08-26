"""Drive the openvpn3 client.

Unlike openconnect this needs no root: openvpn3 talks to a per-user session
manager over D-Bus, so there is no sudo prompt in this flow. The .ovpn file is
imported once under a namespaced configuration name, then sessions are started
and stopped against that name.

`openvpn3 session-start` exits once the tunnel is up -- the session lives on in
the session manager -- so after a successful start we poll `sessions-list` to
notice the tunnel going away underneath us.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pexpect

from .. import PACKAGE_NAME
from .base import Backend, ConnectionError_, State

P_USER = 0
P_PASS = 1
P_STARTED = 2
P_FAILED = 3
P_EOF = 4
P_TIMEOUT = 5

PATTERNS = [
    r"(Auth User name|User name|[Uu]sername)\s*:\s*$",
    r"(Auth Password|[Pp]assword)\s*:\s*$",
    r"(Connected|Session path:\s*\S+)",
    r"(AUTH_FAILED|Authentication failed|Connection failed|"
    r"Failed to start session|error:)",
    pexpect.EOF,
    pexpect.TIMEOUT,
]

START_TIMEOUT = 120
POLL_INTERVAL = 5


class OpenVpn3Backend(Backend):
    binary = "openvpn3"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._child: pexpect.spawn | None = None

    @property
    def config_name(self) -> str:
        """Namespaced name so we never disturb a hand-imported config."""
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", self.profile.name).strip("-")
        return f"{PACKAGE_NAME}-{safe or self.profile.id}"

    # ------------------------------------------------------------------ setup

    def _ovpn3(self, *args: str, timeout: int = 30) -> "subprocess.CompletedProcess[str]":
        return subprocess.run(
            [self.binary, *args], capture_output=True, text=True, timeout=timeout
        )

    def _ensure_config_imported(self) -> None:
        path = Path(self.profile.config_path).expanduser()
        if not path.is_file():
            raise ConnectionError_(f"Config file not found: {path}")

        listed = self._ovpn3("configs-list")
        if self.config_name in listed.stdout:
            # Re-import when the .ovpn on disk is newer than the stored copy so
            # edits to the file actually take effect.
            if not self._config_is_stale(path):
                return
            self.log(f"{path.name} changed on disk, re-importing")
            self._ovpn3("config-remove", "--config", self.config_name, "--force")

        self.log(f"importing {path} as {self.config_name}")
        result = self._ovpn3(
            "config-import", "--config", str(path),
            "--name", self.config_name, "--persistent",
        )
        if result.returncode != 0:
            raise ConnectionError_(
                f"openvpn3 could not import the config: "
                f"{(result.stderr or result.stdout).strip()}"
            )

    def _config_is_stale(self, path: Path) -> bool:
        """True when the .ovpn file is newer than our last import of it."""
        marker = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
        marker = marker / PACKAGE_NAME / f"{self.config_name}.imported"
        try:
            return path.stat().st_mtime > marker.stat().st_mtime
        except FileNotFoundError:
            return True

    def _mark_imported(self) -> None:
        marker = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
        marker = marker / PACKAGE_NAME
        marker.mkdir(parents=True, exist_ok=True)
        (marker / f"{self.config_name}.imported").touch()

    # -------------------------------------------------------------- main loop

    def _run(self) -> None:
        if not shutil.which(self.binary):
            raise ConnectionError_(
                "openvpn3 is not installed (yay -S openvpn3-git)."
            )

        self._ensure_config_imported()
        self._mark_imported()

        # A session left over from a crash would make session-start fail.
        if self._session_is_up():
            self.log("an existing session for this config is still up, closing it")
            self._ovpn3("session-manage", "--config", self.config_name, "--disconnect")

        self.log(f"$ openvpn3 session-start --config {self.config_name}")
        env = dict(os.environ)
        env["LC_ALL"] = "C"
        self._child = pexpect.spawn(
            self.binary, ["session-start", "--config", self.config_name],
            encoding="utf-8", codec_errors="replace",
            timeout=START_TIMEOUT, env=env, echo=False,
        )
        child = self._child

        while not self._stop.is_set():
            index = child.expect(PATTERNS, timeout=START_TIMEOUT)
            self._log_buffer(child.before)

            if index == P_USER:
                if not self.profile.username:
                    raise ConnectionError_(
                        "This config asks for a username but the profile has none."
                    )
                self.set_state(State.AUTHENTICATING, "sending username")
                self.log(f"[auth] username: {self.profile.username}")
                child.sendline(self.profile.username)

            elif index == P_PASS:
                if not self.creds.password:
                    raise ConnectionError_(
                        "This config asks for a password but none is stored "
                        "for this profile."
                    )
                self.set_state(State.AUTHENTICATING, "sending password")
                self.log("[auth] sending stored password")
                child.sendline(self.creds.password)

            elif index == P_STARTED:
                self._log_buffer(child.after)
                child.expect([pexpect.EOF, pexpect.TIMEOUT], timeout=15)
                self._log_buffer(child.before)
                self.set_state(State.CONNECTED, self.profile.name)
                self._poll_until_gone()
                return

            elif index == P_FAILED:
                self._log_buffer(child.after)
                raise ConnectionError_(
                    "openvpn3 could not bring the session up. See the log."
                )

            elif index == P_EOF:
                # session-start can exit cleanly having already connected.
                if self._session_is_up():
                    self.set_state(State.CONNECTED, self.profile.name)
                    self._poll_until_gone()
                    return
                raise ConnectionError_(
                    "openvpn3 exited without starting a session. See the log."
                )

            elif index == P_TIMEOUT:
                raise ConnectionError_(
                    f"openvpn3 did not connect within {START_TIMEOUT}s."
                )

    def _poll_until_gone(self) -> None:
        """Watch the session manager until the tunnel disappears."""
        while not self._stop.wait(POLL_INTERVAL):
            if not self._session_is_up():
                self.log("the openvpn3 session is no longer listed")
                return

    def _session_is_up(self) -> bool:
        try:
            result = self._ovpn3("sessions-list", timeout=15)
        except (subprocess.SubprocessError, OSError):
            return False
        return self.config_name in result.stdout

    # -------------------------------------------------------------- teardown

    def _teardown(self) -> None:
        if self._child is not None and self._child.isalive():
            self._child.terminate(force=False)

        self.log(f"disconnecting session {self.config_name}")
        result = self._ovpn3(
            "session-manage", "--config", self.config_name, "--disconnect"
        )
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0 and "No sessions" not in output:
            self.log(f"disconnect reported: {output.strip()}")
        self.set_state(State.DISCONNECTED)

    def _log_buffer(self, text: str | None) -> None:
        if not text:
            return
        if self.creds.password and len(self.creds.password) > 2:
            text = text.replace(self.creds.password, "********")
        self.log(text)
