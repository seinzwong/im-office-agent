from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import get_settings

SCHEMA_VERSION = "planb.content_ir.sidecar.v1"


def content_ir_store_dir() -> Path:
    settings = get_settings()
    base = Path(str(getattr(settings, "content_ir_store_path", "") or ".artifacts/planb/content_ir"))
    return base


def load_content_ir_for_file_token(file_token: str) -> dict[str, Any] | None:
    path = _path_for_token(file_token)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    content_ir = data.get("content_ir") if isinstance(data, dict) else None
    return content_ir if isinstance(content_ir, dict) else None


def save_content_ir_for_file_token(
    file_token: str,
    content_ir: dict[str, Any],
    *,
    source_title: str = "",
    source_url: str = "",
) -> Path:
    path = _path_for_token(file_token)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    created_at = now
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            created_at = str(existing.get("created_at") or now) if isinstance(existing, dict) else now
        except Exception:
            created_at = now
    payload = {
        "schema_version": SCHEMA_VERSION,
        "file_token": str(file_token or ""),
        "source_title": str(source_title or ""),
        "source_url": str(source_url or ""),
        "content_ir": content_ir if isinstance(content_ir, dict) else {},
        "created_at": created_at,
        "updated_at": now,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _path_for_token(file_token: str) -> Path:
    return content_ir_store_dir() / f"{_safe_token(file_token)}.json"


def _safe_token(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return safe.strip("._")[:120] or "unknown"
