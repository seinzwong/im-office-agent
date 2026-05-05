from __future__ import annotations

from typing import Any


def list_artifacts() -> list[dict[str, Any]]:
    """Return generated artifacts known to the gateway.

    The current target branch has the route but no storage-backed
    implementation. Keep the endpoint stable with an empty in-memory response
    until artifact persistence is wired in.
    """
    return []


__all__ = ["list_artifacts"]
