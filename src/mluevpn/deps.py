"""Detect the external programs MLUEVPN drives, and whether they are current.

The app is a front-end: without `openconnect` and `openvpn3` there is nothing
to drive. Rather than letting a connection fail with a confusing error, probe
for them up front and say what is missing and how to install it.

Update detection compares the installed version against pacman's *local* sync
database, which is only as fresh as the last `pacman -Sy`. We deliberately do
not refresh it -- that needs root and modifies system state -- so a result of
"up to date" means "up to date as of your last sync".

Packages from the AUR have no repo entry to compare against, so no update check
is possible for them. That is reported as INSTALLED, not as a warning: the
program is there and works; only the "is it current?" question is unanswerable.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field

# Detection states, ordered worst-first for summarising a whole list.
MISSING = "missing"
OUTDATED = "outdated"
#: Present and working, but no repo entry to compare against, so "is there an
#: update?" is unanswerable. AUR packages land here. It is *not* a problem.
INSTALLED = "installed"
#: Nothing has been probed yet (used for the credential-key row).
UNKNOWN = "unknown"
OK = "ok"

_SEVERITY = {MISSING: 0, OUTDATED: 1, UNKNOWN: 2, INSTALLED: 3, OK: 4}


@dataclass(frozen=True)
class Dependency:
    key: str
    name: str
    purpose: str
    binary: str
    version_args: tuple[str, ...]
    install_command: str
    required: bool
    #: AUR packages have no repo version, so "update available" is unanswerable.
    from_aur: bool = False


DEPENDENCIES: tuple[Dependency, ...] = (
    Dependency(
        key="openconnect",
        name="OpenConnect",
        purpose="Connects to GlobalProtect, AnyConnect, Fortinet and Pulse servers",
        binary="openconnect",
        version_args=("--version",),
        install_command="sudo pacman -S openconnect",
        required=True,
    ),
    Dependency(
        key="sudo",
        name="sudo",
        purpose="OpenConnect has to run as root to create the tunnel device",
        binary="sudo",
        version_args=("--version",),
        install_command="sudo pacman -S sudo",
        required=True,
    ),
    Dependency(
        key="openvpn3",
        name="OpenVPN 3",
        purpose="Connects using .ovpn configuration files",
        binary="openvpn3",
        version_args=("version",),
        install_command="yay -S openvpn3-git",
        required=True,
        from_aur=True,
    ),
)


@dataclass
class DepStatus:
    dependency: Dependency
    found: bool = False
    path: str = ""
    version: str = ""
    package: str = ""
    installed_version: str = ""
    repo_version: str = ""
    state: str = MISSING
    notes: list[str] = field(default_factory=list)

    @property
    def update_available(self) -> bool:
        return self.state == OUTDATED

    def summary(self) -> str:
        """One line for the row subtitle."""
        if not self.found:
            return f"Not installed — {self.dependency.install_command}"

        bits = []
        if self.package and self.installed_version:
            bits.append(f"{self.package} {self.installed_version}")
        elif self.version:
            bits.append(self.version)
        else:
            bits.append("installed")

        if self.state == OUTDATED:
            bits.append(f"update available: {self.repo_version}")
        elif self.state == INSTALLED:
            # Say why there is no version comparison, so a blank verdict is not
            # read as "something is wrong with this one".
            bits.append(
                "installed from the AUR — check updates with `yay -Sua`"
                if self.dependency.from_aur
                else "not from a repo — no update check"
            )
        return "  ·  ".join(bits)


def _run(args: list[str], timeout: int = 8) -> str:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout
        )
    except (subprocess.SubprocessError, OSError):
        return ""
    return (result.stdout or "") + (result.stderr or "")


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _probe_version(dep: Dependency, path: str) -> str:
    raw = _first_line(_run([path, *dep.version_args]))
    if not raw:
        return ""
    # "OpenConnect version v9.21" reads better trimmed to the number; the
    # openvpn3 banner is more informative left whole.
    match = re.search(r"version\s+v?([0-9][\w.\-]*)", raw, re.IGNORECASE)
    return f"v{match.group(1)}" if match else raw


def _owning_package(path: str) -> tuple[str, str]:
    """Ask pacman which package owns a binary. Returns (name, version)."""
    out = _run(["pacman", "-Qo", "--", path])
    # "/usr/bin/openconnect is owned by openconnect 1:9.21-1"
    match = re.search(r"is owned by (\S+) (\S+)", out)
    return (match.group(1), match.group(2)) if match else ("", "")


def _repo_version(package: str) -> str:
    out = _run(["pacman", "-Si", "--", package])
    for line in out.splitlines():
        if line.startswith("Version"):
            _, _, value = line.partition(":")
            return value.strip()
    return ""


def _is_older(installed: str, candidate: str) -> bool:
    """True when `installed` sorts before `candidate` per pacman's own rules."""
    out = _run(["vercmp", installed, candidate]).strip()
    try:
        return int(out) < 0
    except ValueError:
        return False


def check(dep: Dependency) -> DepStatus:
    status = DepStatus(dependency=dep)

    path = shutil.which(dep.binary)
    if not path:
        status.state = MISSING
        return status

    status.found = True
    status.path = path
    status.version = _probe_version(dep, path)

    package, installed = _owning_package(path)
    status.package = package
    status.installed_version = installed

    if not package:
        # Not managed by pacman -- a manual build, or something in ~/.local.
        status.state = INSTALLED
        status.notes.append("Not installed through pacman; cannot check for updates.")
        return status

    repo = _repo_version(package)
    if not repo:
        # No repo entry: an AUR package, or a repo that is no longer configured.
        # The program is there and usable either way, so this is INSTALLED and
        # not a warning -- only the update comparison is missing.
        status.state = INSTALLED
        status.notes.append(
            "Installed from the AUR — pacman cannot tell you if it is current. "
            "Use your AUR helper (for example `yay -Sua`)."
            if dep.from_aur
            else "No repository entry found for this package."
        )
        return status

    status.repo_version = repo
    status.state = OUTDATED if _is_older(installed, repo) else OK
    return status


def check_all() -> list[DepStatus]:
    """Probe every dependency. Safe to call from a worker thread."""
    return [check(dep) for dep in DEPENDENCIES]


def worst_state(statuses: list[DepStatus], required_only: bool = False) -> str:
    """The most severe state across a set, for summarising in a banner."""
    considered = [
        s for s in statuses if s.dependency.required or not required_only
    ]
    if not considered:
        return OK
    return min((s.state for s in considered), key=lambda s: _SEVERITY[s])


def missing_required(statuses: list[DepStatus]) -> list[DepStatus]:
    return [s for s in statuses if s.dependency.required and not s.found]
