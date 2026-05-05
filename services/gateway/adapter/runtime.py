from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


def adapter_error(
    code: str,
    message: str,
    details: Any | None = None,
    warnings: list[str] | None = None,
) -> dict:
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"ok": False, "error": error, "warnings": warnings or []}


def load_repo_dotenv() -> dict[str, str]:
    path = Path(__file__).resolve().parents[3] / ".env"
    if not path.is_file():
        return {}
    output: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        output[key.strip()] = value.strip().strip('"').strip("'")
    return output


def env_value(name: str, default: str = "") -> str:
    return os.getenv(name) or load_repo_dotenv().get(name) or default


def lark_cli_path() -> str:
    raw = env_value("LARK_CLI_PATH", "lark-cli.cmd")
    path = Path(raw)
    if path.name.lower() == "lark-cli.cmd":
        exe = path.parent / "node_modules" / "@larksuite" / "cli" / "bin" / "lark-cli.exe"
        if exe.is_file():
            return str(exe)
    return str(path)


def extract_folder_token(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    match = re.search(r"/drive/folder/([^/?#]+)", raw)
    if match:
        return match.group(1)
    return raw


def parse_json_object(raw: str) -> dict:
    try:
        value = json.loads(raw)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def run_lark_cli(args: list[str], *, stdin: str | None = None, timeout: int = 120) -> dict:
    completed = subprocess.run(
        [lark_cli_path(), *args],
        input=stdin,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    data = parse_json_object(completed.stdout)
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "json": data,
    }
