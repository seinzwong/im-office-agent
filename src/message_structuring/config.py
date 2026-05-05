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
    store_backend: str = "memory"
    redis_url: str = "redis://localhost:6379/0"
    redis_key_prefix: str = "msl"
    redis_buffer_ttl_seconds: int = 300
    redis_max_buffer_messages: int = 1000
    importance_backend: str = "rule"
    importance_bert_model_path: str = "models/importance_bert"
    importance_bert_base_model: str = "hfl/chinese-macbert-base"
    importance_bert_device: str = "auto"
    importance_hybrid_bert_weight: float = 0.6
    importance_max_length: int = 128


def load_config_from_env() -> MessageStructuringConfig:
    mode = os.getenv("SUMMARY_CLIENT_MODE", "stub").strip().lower()
    if mode not in {"stub", "http"}:
        mode = "stub"

    timeout_raw = os.getenv("TEAMMATE_SUMMARY_TIMEOUT_SECONDS", "8")
    threshold_raw = os.getenv("SUMMARY_IMPORTANCE_THRESHOLD", "0.45")
    store_backend = os.getenv("STORE_BACKEND", "memory").strip().lower()
    if store_backend not in {"memory", "redis"}:
        store_backend = "memory"
    redis_buffer_ttl_raw = os.getenv("REDIS_BUFFER_TTL_SECONDS", "300")
    redis_max_buffer_raw = os.getenv("REDIS_MAX_BUFFER_MESSAGES", "1000")
    importance_backend = os.getenv("IMPORTANCE_BACKEND", "rule").strip().lower()
    if importance_backend not in {"rule", "bert", "hybrid"}:
        importance_backend = "rule"
    importance_hybrid_weight_raw = os.getenv("IMPORTANCE_HYBRID_BERT_WEIGHT", "0.6")
    importance_max_length_raw = os.getenv("IMPORTANCE_MAX_LENGTH", "128")
    importance_device = os.getenv("IMPORTANCE_BERT_DEVICE", "auto").strip().lower()
    if importance_device not in {"auto", "cpu", "cuda"}:
        importance_device = "auto"
    try:
        timeout_value = float(timeout_raw)
    except ValueError:
        timeout_value = 8.0
    try:
        threshold_value = float(threshold_raw)
    except ValueError:
        threshold_value = 0.45
    try:
        redis_buffer_ttl_value = int(redis_buffer_ttl_raw)
    except ValueError:
        redis_buffer_ttl_value = 300
    try:
        redis_max_buffer_value = int(redis_max_buffer_raw)
    except ValueError:
        redis_max_buffer_value = 1000
    try:
        importance_hybrid_weight = float(importance_hybrid_weight_raw)
    except ValueError:
        importance_hybrid_weight = 0.6
    try:
        importance_max_length = int(importance_max_length_raw)
    except ValueError:
        importance_max_length = 128

    threshold_value = max(0.0, min(threshold_value, 1.0))
    importance_hybrid_weight = max(0.0, min(importance_hybrid_weight, 1.0))
    importance_max_length = max(8, importance_max_length)

    return MessageStructuringConfig(
        summary_client_mode=mode,
        teammate_summary_base_url=os.getenv("TEAMMATE_SUMMARY_BASE_URL", "").strip(),
        teammate_summary_api_key=os.getenv("TEAMMATE_SUMMARY_API_KEY", "").strip(),
        teammate_summary_timeout_seconds=max(0.5, timeout_value),
        summary_importance_threshold=threshold_value,
        store_backend=store_backend,
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0").strip(),
        redis_key_prefix=os.getenv("REDIS_KEY_PREFIX", "msl").strip() or "msl",
        redis_buffer_ttl_seconds=max(1, redis_buffer_ttl_value),
        redis_max_buffer_messages=max(1, redis_max_buffer_value),
        importance_backend=importance_backend,
        importance_bert_model_path=os.getenv("IMPORTANCE_BERT_MODEL_PATH", "models/importance_bert").strip(),
        importance_bert_base_model=os.getenv("IMPORTANCE_BERT_BASE_MODEL", "hfl/chinese-macbert-base").strip(),
        importance_bert_device=importance_device,
        importance_hybrid_bert_weight=importance_hybrid_weight,
        importance_max_length=importance_max_length,
    )
