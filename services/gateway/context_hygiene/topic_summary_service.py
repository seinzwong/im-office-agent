from __future__ import annotations

import time
from typing import Any


def run_summary_for_chat(
    chat_id: str,
    time_hint: str,
    t0_unix: int,
    aggregate_text: str,
    source: str,
) -> dict[str, Any]:
    """Return a development summary artifact shape for the existing dev route."""
    stamp = t0_unix or int(time.time())
    title = f"Summary {chat_id} {stamp}"
    return {
        "ok": True,
        "title": title,
        "file_token": f"dev-summary-{stamp}",
        "open_url": "about:blank",
        "chat_id": chat_id,
        "time_hint": time_hint,
        "source": source,
        "preview": aggregate_text[:240],
    }


__all__ = ["run_summary_for_chat"]
