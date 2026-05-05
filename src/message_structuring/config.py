from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class MessageStructuringConfig:
    summary_client_mode: str = "stub"
    teammate_summary_base_url: str = ""
    teammate_summary_api_key: str = ""
    teammate_summary_timeout_seconds: float = 8.0
    summary_importance_threshold: float = 0.45


def load_config_from_env() -> MessageStructuringConfig:
    mode = os.getenv("SUMMARY_CLIENT_MODE", "stub").strip().lower()
    if mode not in {"stub", "http"}:
        mode = "stub"

    timeout_raw = os.getenv("TEAMMATE_SUMMARY_TIMEOUT_SECONDS", "8")
    threshold_raw = os.getenv("SUMMARY_IMPORTANCE_THRESHOLD", "0.45")
    try:
        timeout_value = float(timeout_raw)
    except ValueError:
        timeout_value = 8.0
    try:
        threshold_value = float(threshold_raw)
    except ValueError:
        threshold_value = 0.45

    threshold_value = max(0.0, min(threshold_value, 1.0))

    return MessageStructuringConfig(
        summary_client_mode=mode,
        teammate_summary_base_url=os.getenv("TEAMMATE_SUMMARY_BASE_URL", "").strip(),
        teammate_summary_api_key=os.getenv("TEAMMATE_SUMMARY_API_KEY", "").strip(),
        teammate_summary_timeout_seconds=max(0.5, timeout_value),
        summary_importance_threshold=threshold_value,
    )
