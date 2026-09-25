"""Drive `sudo openconnect` through its interactive prompts.

The sequence this handles is the one you get from a bare
`sudo openconnect --protocol=gp <host>` against a server with an untrusted
certificate:

    [sudo] password for you:                              -> sudo password
    Enter 'yes' to accept, 'no' to abort; ...             -> yes
    Username:                                             -> username
    Password:                                             -> password

The certificate question only appears until we have pinned the server. The
first successful connection captures the `pin-sha256:` fingerprint openconnect
suggests, stores it, and passes it as --servercert from then on -- which both
removes a prompt and turns a blind "yes" into a real identity check.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess

from typing import Any, Callable

import pexpect

from .base import Backend, ConnectionError_, State

# Ordered by priority: pexpect resolves ties by list position, and the sudo
# prompt would otherwise also match the generic password pattern.
P_SUDO = 0
P_SUDO_RETRY = 1
P_CERT = 2
P_USER = 3
P_PASS = 4
P_GATEWAY = 5
P_CONNECTED = 6
P_FAILED = 7
P_EOF = 8
P_TIMEOUT = 9

PATTERNS = [
    r"\[sudo\] password for [^:]+:",
    r"Sorry, try again\.",
    r"Enter 'yes' to accept, 'no' to abort; anything else to view:",
    r"[Uu]sername:\s*$",
    r"[Pp]assword:\s*$",
    r"GATEWAY:.*\]:",
    # openconnect >= 9 announces the tunnel with "Configured as <ip>, with SSL
    # connected and DTLS ..."; older builds said "Connected as <ip>". Match both,
    # or the tunnel comes up and we sit in AUTHENTICATING until the timeout.
    r"(Configured as [^\r\n]+|Connected as [^\r\n]+|"
    r"ESP session established|Connected \S+ as )",
    r"(Login failed|Authentication failed|failed to obtain|"
    r"Failed to complete authentication|Permission denied)",
    pexpect.EOF,
    pexpect.TIMEOUT,
]

PIN_RE = re.compile(r"(pin-sha256:[A-Za-z0-9+/=]+)")

# openconnect rejects the connection itself when --servercert does not match,
# and the only symptom further down the stream is a generic authentication
# failure -- which would otherwise send the user after their password.
MISMATCH_RE = re.compile(
    r"None of the \d+ fingerprint\(s\) specified via --servercert match"
)

AUTH_TIMEOUT = 120


class OpenConnectBackend(Backend):
    binary = "openconnect"

    def __init__(
        self,
        *args: Any,
        on_servercert: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        # Called with the pin string when we learn the server's fingerprint.
        self._on_servercert = on_servercert
        # Set when the server presents a certificate we did not pin.
        self._cert_mismatch = False
        self._child: pexpect.spawn | None = None
        self._vpn_pid: int | None = None

    # ------------------------------------------------------------------ setup

    def _build_command(self) -> list[str]:
        p = self.profile
        if not p.host.strip():
            raise ConnectionError_("This profile has no host set.")
        if not shutil.which(self.binary):
            raise ConnectionError_(
                "openconnect is not installed (pacman -S openconnect)."
            )
        if not shutil.which("sudo"):
            raise ConnectionError_("sudo is not available.")

        cmd = ["sudo", "openconnect", f"--protocol={p.protocol or 'gp'}"]
        if p.username.strip():
            cmd.append(f"--user={p.username.strip()}")
        if p.authgroup.strip():
            cmd.append(f"--authgroup={p.authgroup.strip()}")
        if self.creds.servercert:
            cmd.append(f"--servercert={self.creds.servercert}")
        cmd += self.extra_args()
        cmd.append(p.host.strip())
        return cmd

    # -------------------------------------------------------------- main loop

    def _run(self) -> None:
        cmd = self._build_command()
        # Redact nothing here: the command line carries no secrets, only flags.
        self.log(f"$ {' '.join(cmd)}")

        env = dict(os.environ)
        # Force predictable, parseable prompts regardless of locale.
        env["LC_ALL"] = "C"
        # Stop sudo from trying to pop a graphical askpass helper; we answer on
        # the pty ourselves.
        env.pop("SUDO_ASKPASS", None)

        self._child = pexpect.spawn(
            cmd[0], cmd[1:], encoding="utf-8", codec_errors="replace",
            timeout=AUTH_TIMEOUT, env=env, echo=False,
        )
        child = self._child
        sudo_attempts = 0

        while not self._stop.is_set():
            index = child.expect(PATTERNS, timeout=AUTH_TIMEOUT)
            self._log_buffer(child.before)

            if index == P_SUDO:
                sudo_attempts += 1
                if sudo_attempts > 1:
                    raise ConnectionError_(
                        "sudo rejected the stored password. Update it in "
                        "Settings > Sudo password."
                    )
                if not self.creds.sudo_password:
                    raise ConnectionError_(
                        "sudo wants a password but none is stored. Add it in "
                        "Settings > Sudo password."
                    )
                self.set_state(State.AUTHENTICATING, "authorising with sudo")
                self.log("[sudo] sending stored password")
                child.sendline(self.creds.sudo_password)

            elif index == P_SUDO_RETRY:
                raise ConnectionError_(
                    "sudo rejected the stored password. Update it in "
                    "Settings > Sudo password."
                )

            elif index == P_CERT:
                self._capture_pin(child.before)
                self.log("[cert] server certificate not trusted, accepting")
                child.sendline("yes")

            elif index == P_USER:
                if not self.profile.username:
                    raise ConnectionError_(
                        "The server asked for a username but the profile has none."
                    )
                self.set_state(State.AUTHENTICATING, "sending username")
                self.log(f"[auth] username: {self.profile.username}")
                child.sendline(self.profile.username)

            elif index == P_PASS:
                if not self.creds.password:
                    raise ConnectionError_(
                        "The server asked for a password but none is stored "
                        "for this profile."
                    )
                self.set_state(State.AUTHENTICATING, "sending password")
                self.log("[auth] sending stored password")
                child.sendline(self.creds.password)

            elif index == P_GATEWAY:
                # Accept whatever gateway openconnect defaults to.
                self.log("[auth] accepting default gateway")
                child.sendline("")

            elif index == P_CONNECTED:
                self._log_buffer(child.after)
                self._vpn_pid = self._find_vpn_pid()
                self.set_state(State.CONNECTED, self.profile.host)
                self._stream_until_exit()
                return

            elif index == P_FAILED:
                self._log_buffer(child.after)
                if self._cert_mismatch:
                    raise ConnectionError_(
                        "The server's certificate has changed since it was "
                        "pinned for this profile. If the server was legitimately "
                        "reissued, use \u201cForget server certificate\u201d in the "
                        "profile menu and reconnect to trust the new one."
                    )
                raise ConnectionError_(
                    "Authentication was rejected by the server. Check the "
                    "username and password saved for this profile."
                )

            elif index == P_EOF:
                raise ConnectionError_(
                    "openconnect exited before the tunnel came up. See the log."
                )

            elif index == P_TIMEOUT:
                raise ConnectionError_(
                    f"No response from {self.profile.host} after "
                    f"{AUTH_TIMEOUT}s. Is the host reachable?"
                )

    def _stream_until_exit(self) -> None:
        """Relay openconnect's output until it exits or stop() is called."""
        child = self._child
        assert child is not None
        while not self._stop.is_set():
            try:
                # Short timeout so the stop flag is noticed promptly.
                child.expect([r"[\r\n]+", pexpect.EOF], timeout=1)
            except pexpect.TIMEOUT:
                continue
            except (pexpect.EOF, OSError):
                break
            self._log_buffer(child.before)
            if not child.isalive():
                break
        self.log("openconnect has exited")

    # -------------------------------------------------------------- teardown

    def _teardown(self) -> None:
        child = self._child
        if child is None or not child.isalive():
            self.set_state(State.DISCONNECTED)
            return

        pid = self._vpn_pid or self._find_vpn_pid()
        if pid:
            self.log(f"sending SIGINT to openconnect (pid {pid}) for a clean exit")
            # openconnect runs as root, so the kill needs root too. sudo's
            # timestamp is still warm from the connect, but feed the password
            # on stdin anyway so this works after the cache expires.
            self._sudo_kill(pid, signal.SIGINT)
        else:
            self.log("could not locate the openconnect process; terminating sudo")
            child.terminate(force=False)

        try:
            child.expect(pexpect.EOF, timeout=15)
        except (pexpect.TIMEOUT, pexpect.EOF, OSError):
            pass
        if child.isalive():
            self.log("openconnect did not exit in time; forcing it down")
            if pid:
                self._sudo_kill(pid, signal.SIGKILL)
            child.terminate(force=True)

        self._vpn_pid = None
        self.set_state(State.DISCONNECTED)

    def _sudo_kill(self, pid: int, sig: signal.Signals) -> None:
        try:
            subprocess.run(
                ["sudo", "-S", "-p", "", "kill", f"-{sig.name[3:]}", str(pid)],
                input=(self.creds.sudo_password or "") + "\n",
                text=True, capture_output=True, timeout=20,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            self.log(f"kill failed: {exc}")

    def _find_vpn_pid(self) -> int | None:
        """Find the openconnect process under the sudo child we spawned."""
        child = self._child
        if child is None:
            return None
        try:
            out = subprocess.run(
                ["pgrep", "-P", str(child.pid), "-x", "openconnect"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except (subprocess.SubprocessError, OSError):
            return None
        if out:
            return int(out.splitlines()[0])
        # sudo can exec the target directly instead of forking, in which case
        # the pid we already hold is openconnect itself.
        return child.pid

    # --------------------------------------------------------------- helpers

    def _capture_pin(self, text: str | None) -> None:
        match = PIN_RE.search(text or "")
        if not match:
            return
        pin = match.group(1)
        self.log(f"[cert] pinning server certificate {pin[:24]}...")
        if self._on_servercert:
            self._on_servercert(pin)

    def _log_buffer(self, text: str | None) -> None:
        if not text:
            return
        if MISMATCH_RE.search(text):
            self._cert_mismatch = True
        # Belt and braces: a stored password should never reach the log, even
        # if a future openconnect echoes what it was given.
        for secret in (self.creds.password, self.creds.sudo_password):
            if secret and len(secret) > 2:
                text = text.replace(secret, "********")
        self.log(text)
