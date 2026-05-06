from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "feishu_onboarding_events_25.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / ".artifacts" / "pptx" / "output"
OUTPUT_STEM = "onboarding-fixture"


DEFAULT_MESSAGES: list[dict[str, str]] = [
    {
        "message_id": "onboarding_001",
        "sender": "Alice",
        "timestamp": "2026-05-05T09:00:00+08:00",
        "text": "老板希望下周看到公司软件 onboarding 变慢问题的优化方案，重点要解释为什么新客户首周启动慢。",
    },
    {
        "message_id": "onboarding_002",
        "sender": "Bob",
        "timestamp": "2026-05-05T09:04:00+08:00",
        "text": "当前平均 onboarding 时长是 3 天，首登完成率 62%，权限配置平均耗时 45 分钟，客户经理反馈模板选择主要靠经验。",
    },
    {
        "message_id": "onboarding_003",
        "sender": "Carol",
        "timestamp": "2026-05-05T09:12:00+08:00",
        "text": "主要痛点：权限配置繁琐、角色继承关系复杂、初始化模板缺少推荐、导入数据错误反馈不清楚。",
    },
    {
        "message_id": "onboarding_004",
        "sender": "David",
        "timestamp": "2026-05-05T09:18:00+08:00",
        "text": "建议方案：建立行业模板库，增加七天成功检查清单，把权限模板、数据导入和首登向导串起来。",
    },
    {
        "message_id": "onboarding_005",
        "sender": "Alice",
        "timestamp": "2026-05-05T09:25:00+08:00",
        "text": "PPT 面向管理层，需要包含现状指标、主要风险、两阶段排期、负责人分工和预期收益。",
    },
    {
        "message_id": "onboarding_006",
        "sender": "Bob",
        "timestamp": "2026-05-05T09:31:00+08:00",
        "text": "第一阶段 1-2 周做角色模板与向导原型，产品负责流程，工程负责权限模板，实施负责客户准备清单。",
    },
    {
        "message_id": "onboarding_007",
        "sender": "Carol",
        "timestamp": "2026-05-05T09:39:00+08:00",
        "text": "第二阶段 3-6 周做数据埋点和七天检查，目标是把平均 onboarding 时长降到 1 天以内，首登完成率提升到 85%。",
    },
    {
        "message_id": "onboarding_008",
        "sender": "David",
        "timestamp": "2026-05-05T09:45:00+08:00",
        "text": "风险包括权限继承规则复杂、推荐模板不准确、指标口径不统一。兜底方案是保留人工确认页和审计日志。",
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the onboarding fixture to a local PPTX.")
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE), help="Conversation fixture JSON path.")
    parser.add_argument("--slide-draft", default="", help="Existing SlideDraft JSON path. Skips LLM generation.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for request, draft, renderer input, and PPTX outputs.")
    parser.add_argument("--keep-timestamp", action="store_true", help="Also keep the timestamped PPTX produced by the renderer.")
    args = parser.parse_args()

    _ensure_repo_on_path()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        if args.slide_draft:
            slide_draft = _read_json(Path(args.slide_draft))
            request = _build_request(_ensure_fixture(Path(args.fixture)))
            result = _render_slide_draft(slide_draft, output_dir, keep_timestamp=args.keep_timestamp)
        else:
            fixture = _ensure_fixture(Path(args.fixture))
            request = _build_request(fixture)
            result = _run_planb(request, output_dir)
            slide_draft = result.get("slide_draft") or {}
            if not isinstance(slide_draft, dict) or not slide_draft.get("slides"):
                raise RuntimeError("PlanB did not produce slide_draft.slides. Check Agent LLM configuration and response.")
            result = _render_slide_draft(slide_draft, output_dir, keep_timestamp=args.keep_timestamp, result=result)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Tip: pass --slide-draft .artifacts/pptx/output/onboarding-fixture.slide_draft.json to render without LLM.", file=sys.stderr)
        return 1

    _write_json(output_dir / f"{OUTPUT_STEM}.request.json", request)
    _write_json(output_dir / f"{OUTPUT_STEM}.slide_draft.json", slide_draft)
    _write_json(output_dir / f"{OUTPUT_STEM}.renderer_input.json", result["ppt_draft"])
    _write_json(output_dir / f"{OUTPUT_STEM}.result.json", result)
    print(json.dumps({"ok": True, "pptx_path": result["pptx_path"], "output_dir": str(output_dir)}, ensure_ascii=False, indent=2))
    return 0


def _ensure_repo_on_path() -> None:
    root = str(REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _ensure_fixture(path: Path) -> list[dict[str, str]]:
    if path.is_file():
        data = _read_json(path)
        if isinstance(data, dict):
            data = data.get("messages") or data.get("events") or []
        if not isinstance(data, list):
            raise RuntimeError(f"Fixture must be a list or object with messages: {path}")
        return [_normalize_fixture_message(item, index) for index, item in enumerate(data)]
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, DEFAULT_MESSAGES)
    return list(DEFAULT_MESSAGES)


def _build_request(messages: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "task": {
            "task_id": "onboarding_fixture_ppt",
            "title": "公司软件Onboarding优化方案",
            "goal": "输出公司软件 onboarding 变慢问题的优化方案，面向管理层展示现状、原因、方案、风险和排期。",
            "audience": "老板/管理层",
            "deliverables": ["ppt"],
        },
        "messages": messages,
        "publish_context": {},
        "options": {
            "target_outputs": ["ppt"],
            "dry_run": True,
            "language": "zh-CN",
            "ppt": {"renderer": "pptx", "dry_run": True},
        },
    }


def _run_planb(request: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    from services.gateway.planb_e2e import run_planb_e2e

    _write_json(output_dir / f"{OUTPUT_STEM}.request.json", request)
    result = run_planb_e2e(request)
    if result.get("ok") is not True:
        raise RuntimeError(f"PlanB failed at {result.get('stage')}: {result.get('error') or result}")
    return result


def _render_slide_draft(
    slide_draft: dict[str, Any],
    output_dir: Path,
    *,
    keep_timestamp: bool,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from services.gateway.adapter.ppt_adapter import publish_ir_to_ppt

    ir = {
        "schemaVersion": "0.2.0",
        "meta": {"title": slide_draft.get("title") or "公司软件Onboarding优化方案"},
        "blocks": [{"id": "cover", "kind": "cover", "title": slide_draft.get("title") or "PPT"}],
    }
    publish_result = publish_ir_to_ppt(
        ir,
        {
            "slide_draft": slide_draft,
            "dry_run": True,
            "renderer": "pptx",
            "asset_root": str(REPO_ROOT),
        },
    )
    if publish_result.get("ok") is not True or not publish_result.get("pptx_path"):
        raise RuntimeError(f"PPTX render failed: {publish_result.get('error') or publish_result}")
    source = Path(str(publish_result["pptx_path"]))
    target = output_dir / f"{OUTPUT_STEM}.pptx"
    shutil.copyfile(source, target)
    if keep_timestamp:
        shutil.copyfile(source, output_dir / source.name)
    merged = {**(result or {}), "publish_result": {"ppt": publish_result}, "ppt_draft": publish_result.get("ppt_draft") or {}}
    merged["pptx_path"] = str(target)
    return merged


def _normalize_fixture_message(item: Any, index: int) -> dict[str, str]:
    if not isinstance(item, dict):
        return {
            "message_id": f"onboarding_{index + 1:03d}",
            "sender": "unknown",
            "timestamp": "",
            "text": str(item),
        }
    return {
        "message_id": str(item.get("message_id") or item.get("id") or f"onboarding_{index + 1:03d}"),
        "sender": str(item.get("sender") or item.get("sender_name") or item.get("user_name") or "unknown"),
        "timestamp": str(item.get("timestamp") or item.get("time") or item.get("create_time") or ""),
        "text": str(item.get("text") or item.get("content") or item.get("plain_text") or ""),
    }


def _read_json(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    return json.loads(text)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
