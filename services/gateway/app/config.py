from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger(__name__)

# 与历史 .env 变量名一致：YAML 中某键为空时，可从仓库根 / gateway 目录 .env 或进程环境补齐。
_ENV_TO_FIELD: tuple[tuple[str, str], ...] = (
    ("GATEWAY_PUBLIC_BASE_URL", "gateway_public_base_url"),
    ("PUBLIC_WEB_BASE_URL", "public_web_base_url"),
    ("OAUTH_REDIRECT_URI", "oauth_redirect_uri"),
    ("SESSION_SECRET", "session_secret"),
    ("ARTIFACTS_DRIVE_FOLDER_TOKEN", "artifacts_drive_folder_token"),
    ("DEV_SKIP_LARK", "dev_skip_lark"),
    ("AGENTS_BASE_URL", "agents_base_url"),
    ("AGENTS_M2M_TOKEN", "agents_m2m_token"),
    ("AGENTS_REGISTRY_PATH", "agents_registry_path"),
    ("LARK_APP_ID", "lark_app_id"),
    ("LARK_APP_SECRET", "lark_app_secret"),
    ("LARK_EVENT_ENCRYPT_KEY", "lark_event_encrypt_key"),
    ("LARK_VERIFICATION_TOKEN", "lark_verification_token"),
    ("LARK_BASE_URL", "lark_base_url"),
    ("LARK_WS_EVENTS_ENABLED", "lark_ws_events_enabled"),
    ("LARK_CLI_PATH", "lark_cli_path"),
    ("CORS_ORIGINS", "cors_origins"),
)


def _gateway_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _is_empty(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, bool):
        return False
    if isinstance(v, str) and not v.strip():
        return True
    return False


def _parse_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val.startswith('"') and val.endswith('"') and len(val) >= 2:
            val = val[1:-1].replace('\\"', '"')
        elif val.startswith("'") and val.endswith("'") and len(val) >= 2:
            val = val[1:-1]
        out[key] = val
    return out


def _coerce_field(field: str, raw: str) -> Any:
    if field == "dev_skip_lark":
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if field == "lark_ws_events_enabled":
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return raw


def _merge_env_into(data: dict[str, Any]) -> dict[str, Any]:
    """YAML 为主；空键用仓库根 .env → gateway/.env 补齐；最后进程环境变量覆盖非空项。"""
    out: dict[str, Any] = dict(data)
    filled_from_file = False

    for dotenv_path in (_repo_root() / ".env", _gateway_dir() / ".env"):
        parsed = _parse_dotenv(dotenv_path)
        for env_key, field in _ENV_TO_FIELD:
            if env_key not in parsed:
                continue
            raw = parsed[env_key]
            if _is_empty(raw):
                continue
            if _is_empty(out.get(field)):
                out[field] = _coerce_field(field, raw)
                filled_from_file = True

    if filled_from_file:
        log.info("配置：已从 .env 文件补齐 YAML 中的空项（仓库根或 services/gateway）")

    for env_key, field in _ENV_TO_FIELD:
        raw = os.environ.get(env_key)
        if raw is None or not str(raw).strip():
            continue
        out[field] = _coerce_field(field, str(raw).strip())

    return out


def _load_yaml_dict() -> dict[str, Any]:
    """从 services/gateway 目录读取 gateway.yaml，否则回退 gateway.example.yaml。"""
    d = _gateway_dir()
    candidates = [
        (d / "gateway.yaml", False),
        (d / "gateway.example.yaml", True),
    ]
    for path, is_example in candidates:
        if not path.is_file():
            continue
        if is_example:
            log.info(
                "配置：使用 %s（可复制为 gateway.yaml 覆盖）",
                path.name,
            )
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise ValueError(f"{path.name} 根节点必须为 mapping")
        return raw
    log.warning(
        "配置：未找到 gateway.yaml / gateway.example.yaml，使用代码内默认值",
    )
    return {}


def _load_settings_dict() -> dict[str, Any]:
    return _merge_env_into(_load_yaml_dict())


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    gateway_public_base_url: str = "http://127.0.0.1:8000"
    public_web_base_url: str = "http://127.0.0.1:5173"
    oauth_redirect_uri: str = "http://127.0.0.1:8000/api/v1/auth/callback"
    session_secret: str = Field(
        "dev-session-secret",
        min_length=8,
        description="itsdangerous / cookie 签名，生产必换",
    )

    lark_app_id: str = ""
    lark_app_secret: str = ""
    lark_event_encrypt_key: str = ""
    lark_verification_token: str = ""
    lark_base_url: str = "https://open.feishu.cn"
    lark_ws_events_enabled: bool = False

    artifacts_drive_folder_token: str = ""
    dev_skip_lark: bool = False

    agents_base_url: str = ""
    agents_m2m_token: str = "dev-m2m-secret"
    agents_registry_path: str = ""

    lark_cli_path: str = "lark-cli"

    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    data = _load_settings_dict()
    return Settings.model_validate(data)


def clear_settings_cache() -> None:
    """测试或热重载时清空缓存。"""
    get_settings.cache_clear()
