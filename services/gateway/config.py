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
    ("FEISHU_USER_ACCESS_TOKEN", "feishu_user_access_token"),
    ("OAUTH_USER_SCOPES", "oauth_user_scopes"),
    ("OAUTH_TOKEN_STORE_PATH", "oauth_token_store_path"),
    ("LARK_CLI_PATH", "lark_cli_path"),
    ("CORS_ORIGINS", "cors_origins"),
)


def _gateway_dir() -> Path:
    return Path(__file__).resolve().parent


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, str) and not value.strip():
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
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = value[1:-1].replace('\\"', '"')
        elif value.startswith("'") and value.endswith("'") and len(value) >= 2:
            value = value[1:-1]
        out[key] = value
    return out


def _coerce_field(field: str, raw: str) -> Any:
    if field == "dev_skip_lark":
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return raw


def _merge_env_into(data: dict[str, Any]) -> dict[str, Any]:
    """Merge .env and process environment values into YAML settings."""
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
        log.info("settings: filled empty YAML values from .env files")

    for env_key, field in _ENV_TO_FIELD:
        raw = os.environ.get(env_key)
        if raw is None or not str(raw).strip():
            continue
        out[field] = _coerce_field(field, str(raw).strip())

    return out


def _load_yaml_dict() -> dict[str, Any]:
    """Load services/gateway/gateway.yaml, falling back to gateway.example.yaml."""
    gateway_dir = _gateway_dir()
    candidates = [
        (gateway_dir / "gateway.yaml", False),
        (gateway_dir / "gateway.example.yaml", True),
    ]
    for path, is_example in candidates:
        if not path.is_file():
            continue
        if is_example:
            log.info("settings: using %s because gateway.yaml was not found", path.name)
        with path.open(encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise ValueError(f"{path.name} root must be a mapping")
        return raw
    log.warning("settings: gateway.yaml and gateway.example.yaml were not found")
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
        description="Session cookie signing secret.",
    )

    lark_app_id: str = ""
    lark_app_secret: str = ""
    lark_event_encrypt_key: str = ""
    lark_verification_token: str = ""
    lark_base_url: str = "https://open.feishu.cn"
    feishu_user_access_token: str = ""
    oauth_user_scopes: str = ""
    oauth_token_store_path: str = ".artifacts/auth/user_tokens.json"

    artifacts_drive_folder_token: str = ""
    dev_skip_lark: bool = False

    agents_base_url: str = ""
    agents_m2m_token: str = "dev-m2m-secret"
    agents_registry_path: str = ""

    lark_cli_path: str = "lark-cli"

    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"

    @property
    def cors_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings.model_validate(_load_settings_dict())


def clear_settings_cache() -> None:
    get_settings.cache_clear()
