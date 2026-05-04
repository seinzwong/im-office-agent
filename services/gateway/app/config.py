from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_file_tuple() -> tuple[str, ...]:
    """仓库根或 gateway 目录的 .env；后者覆盖前者。避免从 services/gateway 启动时读不到根目录 .env。"""
    here = Path(__file__).resolve()
    gateway_dir = here.parents[1]
    repo_root = here.parents[3]
    ordered: list[Path] = []
    for p in (repo_root / ".env", gateway_dir / ".env"):
        if p.is_file():
            ordered.append(p)
    return tuple(str(p) for p in ordered) if ordered else (".env",)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_file_tuple(),
        extra="ignore",
        case_sensitive=False,
    )

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
    lark_base_url: str = "https://open.feishu.cn"  # Lark 国际版见文档改 open.larksuite.com

    # 云空间/文档库目标目录 folder_token；生产应配置。仅开发可在 DEV_SKIP_LARK=true 时留空（列表为占位、总结不落盘）。
    artifacts_drive_folder_token: str = ""
    dev_skip_lark: bool = False  # True 时跳过飞书 OpenAPI 与真实落盘

    # 未设置 AGENTS_REGISTRY_PATH 时：所有 action 共用这一对。
    agents_base_url: str = ""  # 如 https://agents.internal.example
    agents_m2m_token: str = "dev-m2m-secret"
    # 指向 YAML 注册表（相对路径相对 services/gateway 目录）；设置后按 routing 分流各 action。
    agents_registry_path: str = ""

    lark_cli_path: str = "lark-cli"  # 保留字段；Gateway 云盘/文档已改走 OpenAPI，不再调用 CLI

    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
