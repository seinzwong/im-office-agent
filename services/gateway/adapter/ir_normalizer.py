from __future__ import annotations

from copy import deepcopy
from typing import Any

from .ir_schema import ensure_ir_defaults, validate_ir


def normalize_agent_ir_output(agent_output: dict, current_ir: dict | None = None) -> dict:
    if not isinstance(agent_output, dict):
        return _error("INVALID_AGENT_OUTPUT", "Agent output must be an object.")
    if _looks_like_ir(agent_output):
        return ensure_ir_defaults(agent_output)
    if agent_output.get("ok") is True and isinstance(agent_output.get("ir"), dict):
        return ensure_ir_defaults(agent_output["ir"])

    has_patch = isinstance(agent_output.get("patch"), list)
    proposed = agent_output.get("proposed_ir_if_no_current_ir")
    if has_patch or isinstance(proposed, dict):
        base = current_ir if isinstance(current_ir, dict) else proposed
        if not isinstance(base, dict):
            return _error("INVALID_AGENT_OUTPUT", "Patch output requires current_ir or proposed_ir_if_no_current_ir.")
        if has_patch:
            patched = apply_json_patch(base, agent_output["patch"])
            if isinstance(patched, dict) and patched.get("ok") is False:
                return patched
            return ensure_ir_defaults(patched)
        return ensure_ir_defaults(base)

    return _error("INVALID_AGENT_OUTPUT", "Unsupported Agent IR output shape.")


def apply_json_patch(base_ir: dict, patch: list[dict]) -> dict:
    if not isinstance(base_ir, dict):
        return _error("INVALID_JSON_PATCH", "Patch base must be an object.")
    if not isinstance(patch, list):
        return _error("INVALID_JSON_PATCH", "Patch must be an array.")
    target = deepcopy(base_ir)
    try:
        for op_index, operation in enumerate(patch):
            if not isinstance(operation, dict):
                return _error("INVALID_JSON_PATCH", f"patch[{op_index}] must be an object.")
            op = operation.get("op")
            path = operation.get("path")
            if op not in {"add", "replace", "remove"} or not isinstance(path, str):
                return _error("INVALID_JSON_PATCH", f"patch[{op_index}] has invalid op or path.")
            parent, key = _resolve_parent(target, path, create_missing=op == "add")
            if op == "remove":
                _remove(parent, key)
            elif op == "replace":
                _replace(parent, key, deepcopy(operation.get("value")))
            else:
                _add(parent, key, deepcopy(operation.get("value")))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        return _error("INVALID_JSON_PATCH", str(exc))
    return target


def _looks_like_ir(value: dict[str, Any]) -> bool:
    return (
        value.get("schemaVersion") == "0.2.0"
        and isinstance(value.get("meta"), dict)
        and isinstance(value.get("blocks"), list)
    )


def _resolve_parent(document: Any, path: str, *, create_missing: bool) -> tuple[Any, str]:
    if not path.startswith("/"):
        raise ValueError(f"Invalid JSON Pointer path: {path}")
    parts = [_unescape(part) for part in path.split("/")[1:]]
    if not parts:
        raise ValueError("Cannot patch document root.")
    cursor = document
    for part in parts[:-1]:
        if isinstance(cursor, list):
            cursor = cursor[int(part)]
        elif isinstance(cursor, dict):
            if part not in cursor:
                if create_missing:
                    cursor[part] = {}
                else:
                    raise KeyError(f"Missing path segment: {part}")
            cursor = cursor[part]
        else:
            raise TypeError(f"Cannot traverse path segment: {part}")
    return cursor, parts[-1]


def _add(parent: Any, key: str, value: Any) -> None:
    if isinstance(parent, list):
        if key == "-":
            parent.append(value)
            return
        parent.insert(int(key), value)
        return
    if isinstance(parent, dict):
        parent[key] = value
        return
    raise TypeError("Patch parent must be object or array.")


def _replace(parent: Any, key: str, value: Any) -> None:
    if isinstance(parent, list):
        parent[int(key)] = value
        return
    if isinstance(parent, dict):
        if key not in parent:
            raise KeyError(f"Cannot replace missing key: {key}")
        parent[key] = value
        return
    raise TypeError("Patch parent must be object or array.")


def _remove(parent: Any, key: str) -> None:
    if isinstance(parent, list):
        del parent[int(key)]
        return
    if isinstance(parent, dict):
        if key not in parent:
            raise KeyError(f"Cannot remove missing key: {key}")
        del parent[key]
        return
    raise TypeError("Patch parent must be object or array.")


def _unescape(value: str) -> str:
    return value.replace("~1", "/").replace("~0", "~")


def _error(code: str, message: str, details: Any | None = None) -> dict:
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"ok": False, "error": error, "warnings": []}
