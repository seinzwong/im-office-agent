from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


class CLIRunError(RuntimeError):
    def __init__(self, message: str, returncode: int, stderr: str) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


def strip_secrets(text: str) -> str:
    """脱敏可能出现在 CLI 输出中的长 token 片段（最佳努力）。"""
    t = re.sub(
        r"(bearer|access_token|refresh_token|secret|app_secret)\s*[:=]\s*[\w\-_.]+",
        r"\1=[REDACTED]",
        text,
        flags=re.I,
    )
    return t


@dataclass
class LarkCLIDriver:
    """
    封装 lark-cli 子进程，默认 --format json。
    实际部署前需在机器上 `npm i -g @larksuite/cli` 并 `lark-cli auth login`（或与 Gateway 同机 CI 环境变量）。
    """

    executable: str = "lark-cli"
    json_format_args: tuple[str, ...] = ("--format", "json")
    default_timeout: float = 120.0
    as_user: bool = True  # 优先 user 写 docs/slides

    def _cmd_base(self) -> list[str]:
        c = [self.executable]
        c.extend(self.json_format_args)
        return c

    def run(
        self,
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        ex = self.executable
        name = ex.split("/")[-1]
        if shutil.which(name) is None and not os.path.isfile(ex):
            log.warning("lark-cli 未在 PATH 或路径无效: %s", ex)
        pre: list[str] = [self.executable, *self.json_format_args]
        if (
            self.as_user
            and args
            and "--as" not in args
            and args[0] in ("im", "docs", "slides", "whiteboard", "drive", "api")
        ):
            pre.extend(["--as", "user"])
        full = pre + args
        to = self.default_timeout if timeout is None else timeout
        merged = os.environ.copy()
        if env:
            merged.update(env)
        p = subprocess.run(
            full,
            capture_output=True,
            text=True,
            timeout=to,
            env=merged,
        )
        if p.stderr:
            log.debug("lark-cli stderr: %s", strip_secrets(p.stderr[:2000]))
        if check and p.returncode != 0:
            raise CLIRunError(
                f"lark-cli failed: {' '.join(args[:4])}...",
                p.returncode,
                strip_secrets(p.stderr or ""),
            )
        return p

    def run_json(self, args: list[str], **kwargs: Any) -> Any:
        p = self.run(args, **kwargs)
        if not p.stdout.strip():
            return None
        try:
            return json.loads(p.stdout)
        except json.JSONDecodeError as e:
            log.error("lark-cli JSON 解析失败: %s", strip_secrets(p.stdout[:500]))
            raise CLIRunError("invalid json from lark-cli", 1, str(e)) from e
