from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def run_deliver_artifacts(
    file_tokens: list[str],
    want_whiteboard: bool = True,
    want_slides: bool = False,
) -> None:
    """Background delivery hook used by the existing gateway route.

    The target branch had route wiring but an empty module. This no-op keeps
    development mode safe instead of failing imports while external artifact
    delivery is not configured.
    """
    log.info(
        "deliver artifacts requested: files=%s whiteboard=%s slides=%s",
        file_tokens,
        want_whiteboard,
        want_slides,
    )


__all__ = ["run_deliver_artifacts"]
