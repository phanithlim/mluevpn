"""VPN backends: one module per external tool we drive."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import Backend, ConnectionError_, Credentials, State
from .openconnect import OpenConnectBackend
from .openvpn3 import OpenVpn3Backend

if TYPE_CHECKING:
    from ..db import Profile

__all__ = [
    "Backend",
    "ConnectionError_",
    "Credentials",
    "State",
    "OpenConnectBackend",
    "OpenVpn3Backend",
    "for_profile",
]

_BACKENDS = {
    "openconnect": OpenConnectBackend,
    "openvpn3": OpenVpn3Backend,
}


def for_profile(profile: "Profile", *args: Any, **kwargs: Any) -> Backend:
    """Build the backend matching a profile's kind."""
    try:
        cls = _BACKENDS[profile.kind]
    except KeyError:
        raise ValueError(f"No backend for profile kind {profile.kind!r}") from None
    return cls(profile, *args, **kwargs)
