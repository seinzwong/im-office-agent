from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from services.gateway.app.feishu_openapi import _message_plain_text
from services.gateway.app.config import Settings
from services.gateway.app import oauth_tokens
from services.gateway.app.pipelines import summary_from_event
from services.gateway.app.routes import lark_events
from services.gateway.app.routes import api_v1
from services.gateway.adapter.feishu_doc_adapter import (
    FeishuDocClient,
    feishu_doc_blocks_to_openapi_children,
    ir_to_feishu_doc_blocks,
)
from services.gateway.adapter.feishu_board_adapter import ir_to_feishu_board_draft
from services.gateway.adapter.feishu_board_adapter import board_ir_to_whiteboard_dsl
from services.gateway.adapter.feishu_board_adapter import publish_ir_to_feishu_board
from services.gateway.adapter.ir_schema import ensure_ir_defaults, validate_ir
from services.gateway.adapter.ppt_adapter import publish_ir_to_ppt, slide_draft_to_ppt_draft
from services.gateway.app import content_ir_store
from services.gateway import planb_e2e
from services.agent.agents import repair_board_ir, repair_slide_draft, validate_board_ir, validate_slide_draft


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(lark_events.router)
    return TestClient(app)


def _event(text: str, *, mentions: list[dict] | None = None) -> dict:
    return {
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user"}},
            "message": {
                "chat_id": "oc_chat",
                "message_id": "om_msg",
                "content": f'{{"text": "{text}"}}',
                "mentions": mentions if mentions is not None else [{"id": "bot"}],
            },
        }
    }


class LarkBotCommandTests(unittest.TestCase):
    def test_summary_command_defaults_and_overrides(self) -> None:
        self.assertEqual(lark_events.parse_summary_command("/summary"), (60, 200))
        self.assertEqual(lark_events.parse_summary_command("/summary 30"), (30, 200))
        self.assertEqual(lark_events.parse_summary_command("/summary 30 100"), (30, 100))
        self.assertEqual(lark_events.parse_summary_command("/summary 30 999"), (30, 200))

    def test_challenge_returns_verification_body(self) -> None:
        response = _client().post("/lark/events", json={"challenge": "abc"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"challenge": "abc"})

    def test_help_replies_without_running_summary(self) -> None:
        with patch.object(lark_events, "_reply_later") as reply, patch.object(
            lark_events, "run_summary_command"
        ) as summary:
            response = _client().post("/lark/events", json=_event("/help"))
        self.assertEqual(response.status_code, 200)
        reply.assert_called_once()
        self.assertIn("/summary", reply.call_args.args[1])
        summary.assert_not_called()

    def test_summary_runs_background_task(self) -> None:
        with patch.object(lark_events, "run_summary_command") as summary:
            response = _client().post("/lark/events", json=_event("/summary 30 100"))
        self.assertEqual(response.status_code, 200)
        summary.assert_called_once()
        self.assertEqual(summary.call_args.args[0], "oc_chat")
        self.assertEqual(summary.call_args.args[4], 30)
        self.assertEqual(summary.call_args.args[5], 100)

    def test_plain_non_mentioned_message_is_ignored(self) -> None:
        with patch.object(lark_events, "_reply_later") as reply, patch.object(
            lark_events, "run_summary_command"
        ) as summary:
            response = _client().post("/lark/events", json=_event("hello", mentions=[]))
        self.assertEqual(response.status_code, 200)
        reply.assert_not_called()
        summary.assert_not_called()

    def test_unknown_slash_command_replies(self) -> None:
        with patch.object(lark_events, "_reply_later") as reply:
            response = _client().post("/lark/events", json=_event("/later"))
        self.assertEqual(response.status_code, 200)
        reply.assert_called_once()
        self.assertIn("该功能还没开发好", reply.call_args.args[1])


class FeishuMessageParsingTests(unittest.TestCase):
    def test_text_content_is_readable(self) -> None:
        message = {"body": {"content": '{"text":"hello @bot"}'}}
        self.assertEqual(_message_plain_text(message), "hello @bot")

    def test_post_content_is_flattened(self) -> None:
        message = {
            "body": {
                "content": '{"post":{"zh_cn":{"content":[[{"tag":"text","text":"hello "},{"tag":"at","user_name":"Alice"}]]}}}'
            }
        }
        self.assertEqual(_message_plain_text(message), "hello Alice")

    def test_non_text_content_is_skipped(self) -> None:
        message = {"body": {"content": '{"image_key":"img"}'}}
        self.assertEqual(_message_plain_text(message), "")


class OAuthTokenTests(unittest.TestCase):
    def test_login_url_contains_signed_state_and_user_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _settings(tmp)
            app = FastAPI()
            app.add_middleware(SessionMiddleware, secret_key="test-secret")
            app.include_router(api_v1.router)
            app.dependency_overrides[api_v1.get_settings] = lambda: settings
            response = TestClient(app).get("/api/v1/auth/login?user_id=ou_user&reason=summary", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        location = response.headers["location"]
        self.assertIn("scope=", location)
        self.assertIn("offline_access", location)
        self.assertIn("docx%3Adocument%3Areadonly", location)
        self.assertIn("docx%3Adocument%3Acreate", location)
        self.assertIn("state=", location)

    def test_callback_saves_user_token_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _settings(tmp)
            state = oauth_tokens.encode_oauth_state(settings, {"user_id": "ou_event", "reason": "summary"})
            app = FastAPI()
            app.add_middleware(SessionMiddleware, secret_key="test-secret")
            app.include_router(api_v1.router)
            app.dependency_overrides[api_v1.get_settings] = lambda: settings
            with patch.object(api_v1.httpx, "post", return_value=_FakeResponse({"data": _token_payload()})):
                response = TestClient(app).get(
                    f"/api/v1/auth/callback?code=code&state={state}",
                    follow_redirects=False,
                )
            token = oauth_tokens.get_valid_user_access_token(settings, "ou_event")
        self.assertEqual(response.status_code, 307)
        self.assertEqual(token, "access-token")

    def test_expired_token_refreshes_and_updates_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _settings(tmp)
            oauth_tokens.save_user_token(
                settings,
                {
                    **_token_payload(),
                    "access_token": "old-token",
                    "expires_in": -10,
                },
                expected_user_id="ou_event",
            )
            with patch.object(
                oauth_tokens.httpx,
                "post",
                return_value=_FakeResponse({"data": {**_token_payload(), "access_token": "new-token"}}),
            ):
                token = oauth_tokens.get_valid_user_access_token(settings, "ou_event")
        self.assertEqual(token, "new-token")


class SummaryOAuthFlowTests(unittest.TestCase):
    def test_summary_without_user_token_replies_auth_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _settings(tmp, static_user_token="")
            with patch.object(summary_from_event, "get_settings", return_value=settings), patch.object(
                summary_from_event, "_safe_reply"
            ) as reply, patch.object(summary_from_event, "list_chat_messages") as list_messages, patch.object(
                summary_from_event, "_invoke_summary_ir"
            ) as invoke:
                result = summary_from_event.run_summary_command("oc", "om", "ou_user", 1_700_000_000)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "USER_AUTH_REQUIRED")
        self.assertIn("/api/v1/auth/login?state=", reply.call_args.args[2])
        list_messages.assert_not_called()
        invoke.assert_not_called()

    def test_summary_uses_oauth_user_token_for_publish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _settings(tmp, static_user_token="")
            oauth_tokens.save_user_token(settings, _token_payload(), expected_user_id="ou_user")
            with patch.object(summary_from_event, "get_settings", return_value=settings), patch.object(
                summary_from_event, "list_chat_messages", return_value=[{"message_id": "m", "sender": "u", "timestamp": "t", "text": "hi"}]
            ), patch.object(
                summary_from_event,
                "_invoke_summary_ir",
                return_value={"ok": True, "result": {"ir": _ir()}},
            ), patch.object(summary_from_event, "publish_ir", return_value=_publish_ok()) as publish, patch.object(
                summary_from_event, "_safe_reply"
            ):
                result = summary_from_event.run_summary_command("oc", "om", "ou_user", 1_700_000_000)
        self.assertTrue(result["ok"])
        self.assertEqual(publish.call_args.args[1]["user_access_token"], "access-token")

    def test_summary_reauths_when_saved_token_lacks_doc_scope(self) -> None:
        settings = _settings(".artifacts/test-auth", static_user_token="")
        publish_error = {
            "ok": False,
            "publish_result": {
                "doc": {
                    "ok": False,
                    "error": {
                        "code": "FEISHU_DOC_API_FAILED",
                        "message": "Feishu HTTP 400: {'code': 99991679, 'msg': 'required docx:document:readonly'}",
                    },
                }
            },
        }
        with patch.object(summary_from_event, "get_settings", return_value=settings), patch.object(
            summary_from_event, "get_valid_user_access_token", return_value="access-token"
        ), patch.object(
            summary_from_event, "list_chat_messages", return_value=[{"message_id": "m", "sender": "u", "timestamp": "t", "text": "hi"}]
        ), patch.object(
            summary_from_event,
            "_invoke_summary_ir",
            return_value={"ok": True, "result": {"ir": _ir()}},
        ), patch.object(summary_from_event, "publish_ir", return_value=publish_error), patch.object(
            summary_from_event, "_safe_reply"
        ) as reply:
            result = summary_from_event.run_summary_command("oc", "om", "ou_user", 1_700_000_000)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "USER_AUTH_REQUIRED")
        self.assertIn("/api/v1/auth/login?state=", reply.call_args.args[2])


class FeishuDocAdapterTests(unittest.TestCase):
    def test_unified_ir_fields_validate_and_render(self) -> None:
        ir = ensure_ir_defaults(
            {
                "schemaVersion": "0.2.0",
                "meta": {"title": "t"},
                "blocks": [
                    {
                        "id": "actions",
                        "kind": "table",
                        "title": "Action table",
                        "description": "Owner-aligned next steps.",
                        "intent": "actions",
                        "columns": ["Task", "Owner", "Due", "Status"],
                        "rows": [["Review", "Alice", "Today", "Open"]],
                        "caption": "Use 待确认 for missing owners.",
                        "sourceRefs": ["m1 10:00"],
                    },
                    {
                        "id": "cards",
                        "kind": "cards",
                        "title": "Decisions",
                        "intent": "decisions",
                        "cards": [{"title": "Go", "body": "Ship v1", "meta": ["owner: Bob", "status: confirmed"]}],
                    },
                    {
                        "id": "timeline",
                        "kind": "timeline",
                        "title": "Plan",
                        "events": [{"date": "Today", "title": "Review", "body": "Check scope", "owner": "Alice", "status": "open"}],
                    },
                ],
            }
        )
        self.assertEqual(validate_ir(ir), [])
        doc_blocks = ir_to_feishu_doc_blocks(ir)
        self.assertIn({"type": "quote", "text": "Owner-aligned next steps."}, doc_blocks)
        self.assertIn({"type": "quote", "text": "来源：m1 10:00"}, doc_blocks)
        self.assertTrue(any(block.get("type") == "table" and block.get("rows", [])[0] == ["Task", "Owner", "Due", "Status"] for block in doc_blocks))
        children = feishu_doc_blocks_to_openapi_children(doc_blocks)
        table_children = [child for child in children if child.get("block_type") == 31]
        self.assertEqual(table_children[0]["_table_rows"][1], ["Review", "Alice", "Today", "Open"])

    def test_table_rows_are_normalized_to_columns(self) -> None:
        ir = ensure_ir_defaults(
            {
                "schemaVersion": "0.2.0",
                "meta": {"title": "t"},
                "blocks": [
                    {
                        "id": "actions",
                        "kind": "table",
                        "title": "Action table",
                        "columns": ["Task", "Owner", "Due"],
                        "rows": [["Review", "Alice"], ["Ship", "Bob", "Tomorrow", "ignored"]],
                    }
                ],
            }
        )
        self.assertEqual(ir["blocks"][0]["rows"], [["Review", "Alice", "待确认"], ["Ship", "Bob", "Tomorrow"]])
        self.assertEqual(validate_ir(ir), [])

    def test_structured_intents_must_use_table(self) -> None:
        ir = ensure_ir_defaults(
            {
                "schemaVersion": "0.2.0",
                "meta": {"title": "t"},
                "blocks": [
                    {
                        "id": "actions",
                        "kind": "cards",
                        "intent": "actions",
                        "title": "Actions",
                        "cards": [{"title": "Review", "body": "Check scope"}],
                    }
                ],
            }
        )
        self.assertIn("intent actions must use kind table", "\n".join(validate_ir(ir)))

    def test_card_meta_and_sources_render_concisely(self) -> None:
        ir = ensure_ir_defaults(
            {
                "schemaVersion": "0.2.0",
                "meta": {"title": "t"},
                "blocks": [
                    {
                        "id": "cards",
                        "kind": "cards",
                        "title": "Cards",
                        "sourceRefs": ["message:om_x100b509affb384a8c44c00f34309780", "https://www.feishu.cn/docx/abc"],
                        "cards": [
                            {
                                "title": "Validate link",
                                "body": "Confirm access.",
                                "meta": [
                                    "owner: 待确认",
                                    "status: 进行中",
                                    "link: https://www.feishu.cn/docx/abc",
                                    "source: message:om_x100b509affb384a8c44c00f34309780",
                                ],
                            }
                        ],
                    }
                ],
            }
        )
        doc_blocks = ir_to_feishu_doc_blocks(ir)
        text_blocks = [block.get("text") for block in doc_blocks]
        self.assertIn("status: 进行中", text_blocks)
        self.assertNotIn("owner: 待确认", text_blocks)
        self.assertNotIn("link: https://www.feishu.cn/docx/abc", text_blocks)
        self.assertIn("来源：消息 | 文档链接", text_blocks)

    def test_page_block_id_falls_back_when_read_scope_missing(self) -> None:
        client = FeishuDocClient("app", "secret", user_access_token="user-token")
        response = _FakeResponse(
            {
                "code": 99991679,
                "error": {
                    "permission_violations": [
                        {"type": "action_privilege_required", "subject": "docx:document"},
                        {"type": "action_privilege_required", "subject": "docx:document:readonly"},
                    ]
                },
            },
            status_code=400,
        )
        with patch("services.gateway.adapter.feishu_doc_adapter.httpx.get", return_value=response):
            self.assertEqual(client.page_block_id("doc_token"), "doc_token")

    def test_table_create_falls_back_to_readable_markdown_when_descendant_fails(self) -> None:
        client = FeishuDocClient("app", "secret", user_access_token="user-token")
        table_child = {
            "block_type": 31,
            "table": {"property": {"row_size": 2, "column_size": 2}},
            "_table_rows": [["Task", "Owner"], ["Review", "Alice"]],
        }
        posts = []

        def fake_post(url: str, **kwargs):
            posts.append({"url": url, "json": kwargs.get("json")})
            if url.endswith("/descendant"):
                return _FakeResponse({"code": 1, "msg": "unsupported"}, status_code=400)
            return _FakeResponse({"code": 0, "data": {"ok": True}})

        with patch.object(client, "page_block_id", return_value="page"), patch(
            "services.gateway.adapter.feishu_doc_adapter.httpx.post",
            side_effect=fake_post,
        ):
            result = client.create_blocks("doc", [table_child])

        self.assertEqual(result["results"][0]["ok"], True)
        self.assertIn("| Task | Owner |", posts[-1]["json"]["children"][0]["code"]["elements"][0]["text_run"]["content"])


class PptSchemaAndAdapterTests(unittest.TestCase):
    def test_slide_draft_rejects_unknown_fields_and_layout_mismatch(self) -> None:
        draft = _slide_draft()
        draft["slides"][0]["layout"] = "cards"
        draft["slides"][0]["content"] = {"cards": [{"title": "A", "body": "B"}], "items": []}
        errors = validate_slide_draft(draft)
        self.assertIn("slides[0].content.items is not supported for layout cards.", errors)

        draft = _slide_draft()
        draft["slides"][0]["layout"] = "cards"
        draft["slides"][0]["content"] = {"steps": [{"title": "A", "body": "B"}]}
        errors = validate_slide_draft(draft)
        self.assertIn("slides[0].content.steps is not supported for layout cards.", errors)
        self.assertIn("slides[0].content does not match layout schema for cards.", errors)

    def test_slide_draft_repair_breaks_three_consecutive_tables(self) -> None:
        draft = _slide_draft()
        table_slide = {
            "id": "table",
            "title": "Table",
            "layout": "table",
            "content": {"columns": ["事项", "负责人"], "rows": [["确认范围", "Alice"]]},
            "visual": {"highlightIndex": -1},
            "speaker_notes": "",
        }
        draft["slides"] = [
            {**table_slide, "id": "table_1"},
            {**table_slide, "id": "table_2"},
            {**table_slide, "id": "table_3"},
        ]

        repaired, warnings = repair_slide_draft(draft)

        self.assertEqual([slide["layout"] for slide in repaired["slides"]], ["table", "table", "cards"])
        self.assertTrue(any("avoid 3 consecutive table" in warning for warning in warnings))
        self.assertEqual(validate_slide_draft(repaired), [])

    def test_ppt_adapter_rejects_xml_renderer_and_does_not_fallback(self) -> None:
        result = publish_ir_to_ppt(_ir(), {"slide_draft": _slide_draft(), "renderer": "xml", "dry_run": True})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "UNSUPPORTED_PPT_RENDERER")

        with patch("services.gateway.adapter.ppt_adapter._render_pptx_with_node", return_value={"ok": False, "stderr": "boom"}):
            result = publish_ir_to_ppt(_ir(), {"slide_draft": _slide_draft(), "dry_run": True})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "PPTX_RENDER_FAILED")

    def test_llm_file_name_is_used_for_board_and_ppt(self) -> None:
        ir = ensure_ir_defaults({**_ir(), "meta": {"title": "Visible Title", "file_name": "LLM File Name"}})
        self.assertEqual(ir_to_feishu_board_draft(ir)["metadata"]["title"], "LLM File Name")

        draft = _slide_draft()
        draft["file_name"] = "PPT LLM Name"
        self.assertEqual(slide_draft_to_ppt_draft(draft)["file_name"], "PPT LLM Name")

    def test_content_ir_sidecar_round_trip_and_planb_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(content_ir_store, "content_ir_store_dir", return_value=Path(tmp)):
                saved = content_ir_store.save_content_ir_for_file_token("doc/token:1", {"title": "IR"}, source_title="Doc")
                self.assertTrue(saved.is_file())
                self.assertEqual(content_ir_store.load_content_ir_for_file_token("doc/token:1"), {"title": "IR"})

        request = {
            "task": {"title": "Deck", "deliverables": ["ppt"]},
            "messages": [{"text": "Build slides", "sender": "u"}],
            "options": {"target_outputs": ["ppt"], "content_ir": {"title": "Stored IR"}},
        }
        with patch.object(planb_e2e, "generate_content_ir_from_messages") as generate_ir, patch.object(
            planb_e2e, "generate_slide_draft_from_content_ir", return_value={"ok": True, "slide_draft": _slide_draft()}
        ) as generate_slides, patch.object(planb_e2e, "publish_ir", return_value={"ok": True, "publish_result": {"ppt": {"ok": True}}}):
            result = planb_e2e.run_planb_e2e(request)
        self.assertTrue(result["ok"])
        generate_ir.assert_not_called()
        generate_slides.assert_called_once()

    def test_planb_generates_board_ir_for_board_target(self) -> None:
        request = {
            "task": {"title": "Board", "deliverables": ["board"]},
            "messages": [{"text": "Build board", "sender": "u"}],
            "options": {"target_outputs": ["board"]},
        }
        board_ir = {
            "title": "Board",
            "file_name": "Board",
            "subtitle": "S",
            "theme": {"accent": "#2F6BFF", "accent2": "#7C3AED", "background": "#F8FAFC", "surface": "#FFFFFF", "text": "#0F172A", "muted": "#64748B"},
            "sections": [{"id": "s1", "kind": "overview", "title": "Overview", "description": "", "accent": "", "items": ["a"], "nodes": [], "edges": [], "metrics": [], "columns": [], "rows": [], "events": []}],
        }
        with patch.object(planb_e2e, "generate_content_ir_from_messages", return_value={"ok": True, "content_ir": {"title": "C"}}), patch.object(
            planb_e2e, "generate_board_ir_from_content_ir", return_value={"ok": True, "board_ir": board_ir}
        ) as generate_board, patch.object(planb_e2e, "publish_ir", return_value={"ok": True, "publish_result": {"board": {"ok": True}}}) as publish:
            result = planb_e2e.run_planb_e2e(request)
        self.assertTrue(result["ok"])
        generate_board.assert_called_once()
        self.assertEqual(publish.call_args.args[2]["board_ir"]["file_name"], "Board")

    def test_planb_reuses_board_ir_from_options(self) -> None:
        board_ir = {
            "title": "Board",
            "file_name": "Board",
            "subtitle": "S",
            "theme": {"accent": "#2F6BFF", "accent2": "#7C3AED", "background": "#F8FAFC", "surface": "#FFFFFF", "text": "#0F172A", "muted": "#64748B"},
            "sections": [{"id": "s1", "kind": "overview", "title": "Overview", "description": "", "accent": "", "items": ["a"], "nodes": [], "edges": [], "metrics": [], "columns": [], "rows": [], "events": []}],
        }
        request = {
            "task": {"title": "Board", "deliverables": ["board"]},
            "messages": [{"text": "Build board", "sender": "u"}],
            "options": {"target_outputs": ["board"], "board_ir": board_ir},
        }
        with patch.object(planb_e2e, "generate_board_ir_from_content_ir") as generate_board, patch.object(
            planb_e2e, "generate_content_ir_from_messages", return_value={"ok": True, "content_ir": {"title": "C"}}
        ), patch.object(planb_e2e, "publish_ir", return_value={"ok": True, "publish_result": {"board": {"ok": True}}}):
            result = planb_e2e.run_planb_e2e(request)
        self.assertTrue(result["ok"])
        generate_board.assert_not_called()

    def test_board_ir_repair_fills_empty_items_sections(self) -> None:
        board_ir = {
            "title": "Board",
            "file_name": "Board",
            "subtitle": "S",
            "theme": {"accent": "#2F6BFF", "accent2": "#7C3AED", "background": "#F8FAFC", "surface": "#FFFFFF", "text": "#0F172A", "muted": "#64748B"},
            "sections": [
                {"id": "s1", "kind": "overview", "title": "Overview", "description": "Context", "accent": "", "items": [], "nodes": [], "edges": [], "metrics": [], "columns": [], "rows": [], "events": []},
                {"id": "s2", "kind": "cards", "title": "Actions", "description": "", "accent": "", "items": [], "nodes": [], "edges": [], "metrics": [], "columns": [], "rows": [], "events": []},
                {"id": "s3", "kind": "summary", "title": "Next", "description": "", "accent": "", "items": [], "nodes": [], "edges": [], "metrics": [], "columns": [], "rows": [], "events": []},
            ],
        }
        content_ir = {
            "background": "Current onboarding is slow.",
            "solution_modules": [{"title": "Checklist", "description": "Create a 7-day checklist"}],
            "expected_outcomes": ["Reduce onboarding to one day"],
        }

        repaired, warnings = repair_board_ir(board_ir, content_ir)

        self.assertEqual(validate_board_ir(repaired), [])
        self.assertTrue(all(section["items"] for section in repaired["sections"]))
        self.assertTrue(any("Repaired board sections[0]" in warning for warning in warnings))

    def test_board_adapter_repairs_empty_items_sections(self) -> None:
        board_ir = {
            "title": "Board",
            "file_name": "Board",
            "subtitle": "S",
            "theme": {"accent": "#2F6BFF", "accent2": "#7C3AED", "background": "#F8FAFC", "surface": "#FFFFFF", "text": "#0F172A", "muted": "#64748B"},
            "sections": [{"id": "s1", "kind": "overview", "title": "Overview", "description": "Context", "accent": "", "items": [], "nodes": [], "edges": [], "metrics": [], "columns": [], "rows": [], "events": []}],
        }

        result = publish_ir_to_feishu_board(_ir(), {"dry_run": True, "board_ir": board_ir})

        self.assertTrue(result["ok"])
        self.assertIn("Context", result.get("whiteboard_dsl") or "")
        self.assertTrue(result["warnings"])

    def test_board_adapter_create_uses_openapi_board_token(self) -> None:
        board_ir = {
            "title": "Board",
            "file_name": "Board",
            "subtitle": "S",
            "theme": {"accent": "#2F6BFF", "accent2": "#7C3AED", "background": "#F8FAFC", "surface": "#FFFFFF", "text": "#0F172A", "muted": "#64748B"},
            "sections": [{"id": "s1", "kind": "overview", "title": "Overview", "description": "", "accent": "", "items": ["Context"], "nodes": [], "edges": [], "metrics": [], "columns": [], "rows": [], "events": []}],
        }
        posts = []

        def fake_post(url: str, **kwargs):
            posts.append({"url": url, "json": kwargs.get("json")})
            if url.endswith("/docx/v1/documents"):
                return _FakeResponse({"code": 0, "data": {"document": {"document_id": "doc_token"}}})
            if url.endswith("/children"):
                return _FakeResponse({"code": 0, "data": {"children": [{"board": {"token": "board_token"}}]}})
            if "/open-apis/board/v1/whiteboards/board_token/nodes" in url:
                return _FakeResponse({"code": 0, "data": {"nodes": []}})
            return _FakeResponse({"code": 999, "msg": "unexpected"}, status_code=400)

        def fake_get(url: str, **kwargs):
            if url.endswith("/blocks"):
                return _FakeResponse({"code": 0, "data": {"items": [{"block_id": "page", "block_type": 1}]}})
            return _FakeResponse({"code": 999, "msg": "unexpected"}, status_code=400)

        with patch("services.gateway.adapter.feishu_board_adapter.httpx.post", side_effect=fake_post), patch(
            "services.gateway.adapter.feishu_board_adapter.httpx.get", side_effect=fake_get
        ):
            result = publish_ir_to_feishu_board(
                _ir(),
                {"dry_run": False, "folder_token": "folder", "board_ir": board_ir, "user_access_token": "user-token"},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["document_id"], "doc_token")
        self.assertEqual(result["whiteboard_token"], "board_token")
        self.assertNotIn("WHITEBOARD_TOKEN_MISSING", " ".join(result.get("warnings") or []))
        self.assertEqual(posts[1]["json"]["children"][0]["block_type"], 43)
        self.assertIn("nodes", posts[2]["json"])
        self.assertNotIn("plant_uml_code", posts[2]["json"])
        self.assertTrue(posts[2]["json"]["nodes"])
        self.assertIn("text", posts[2]["json"]["nodes"][0]["text"])
        self.assertNotIn("content", posts[2]["json"]["nodes"][0]["text"])
        self.assertGreaterEqual(len(posts[2]["json"]["nodes"]), 4)
        background_nodes = [node for node in posts[2]["json"]["nodes"] if str(node.get("id", "")).endswith("_bg")]
        self.assertTrue(background_nodes)
        self.assertEqual(background_nodes[0]["style"]["border_color"], "#D8E0EA")
        self.assertEqual(background_nodes[0]["style"]["border_width"], "extra_narrow")
        section_text_nodes = [node for node in posts[2]["json"]["nodes"] if str(node.get("id", "")).startswith("s0_")]
        self.assertGreaterEqual(len(section_text_nodes), 2)
        self.assertFalse(any("Overview\nContext" == (node.get("text") or {}).get("text") for node in posts[2]["json"]["nodes"]))
        body_nodes = [node for node in posts[2]["json"]["nodes"] if str(node.get("id", "")).startswith("s0_body_")]
        self.assertEqual(len(body_nodes), 1)
        self.assertEqual(body_nodes[0]["width"], 552)
        self.assertGreaterEqual(body_nodes[0]["height"], 31)
        title_nodes = [node for node in posts[2]["json"]["nodes"] if str(node.get("id", "")).endswith("_title")]
        self.assertTrue(title_nodes)
        self.assertGreaterEqual(title_nodes[0]["height"], 37)

    def test_board_dsl_uses_fixed_readable_layout(self) -> None:
        long_text = "这是一个很长的中文白板内容，用来确认渲染器会进行换行，而不是把整段内容压缩成一个横向极长的节点导致字几乎看不见。"
        board_ir = {
            "title": "Board",
            "file_name": "Board",
            "subtitle": "S",
            "theme": {"accent": "#2F6BFF", "accent2": "#7C3AED", "background": "#F8FAFC", "surface": "#FFFFFF", "text": "#0F172A", "muted": "#64748B"},
            "sections": [
                {"id": "s1", "kind": "overview", "title": "Overview", "description": long_text, "accent": "", "items": [long_text], "nodes": [], "edges": [], "metrics": [], "columns": [], "rows": [], "events": []},
                {"id": "s2", "kind": "table", "title": "Table", "description": "", "accent": "", "items": [], "nodes": [], "edges": [], "metrics": [], "columns": ["模块", "输入", "输出"], "rows": [[long_text, long_text, long_text]], "events": []},
                {"id": "s3", "kind": "timeline", "title": "Timeline", "description": "", "accent": "", "items": [], "nodes": [], "edges": [], "metrics": [], "columns": [], "rows": [], "events": [{"date": "T0", "title": "Kickoff", "body": long_text}]},
            ],
        }

        payload = json.loads(board_ir_to_whiteboard_dsl(board_ir))
        nodes = payload["nodes"]
        section_nodes = [node for node in nodes if node.get("type") == "frame"]

        self.assertTrue(section_nodes)
        for node in section_nodes:
            self.assertIsInstance(node.get("x"), int)
            self.assertIsInstance(node.get("y"), int)
            self.assertIsInstance(node.get("width"), int)
            self.assertIsInstance(node.get("height"), int)
            self.assertNotIn("fill-container", json.dumps(node, ensure_ascii=False))
            self.assertNotIn("fit-content", json.dumps(node, ensure_ascii=False))
            for child in node.get("children") or []:
                if child.get("type") == "text":
                    for line in str(child.get("text") or "").splitlines():
                        self.assertLessEqual(len(line), 55)
        overview_body_nodes = [child for child in section_nodes[0].get("children") or [] if str(child.get("id", "")).startswith("s0_body_")]
        self.assertLessEqual(len(overview_body_nodes), 5)
        self.assertTrue(any("\n" in str(child.get("text") or "") for child in overview_body_nodes))
        self.assertTrue(all(child.get("width") == 552 for child in overview_body_nodes))
        two_line_nodes = [child for child in overview_body_nodes if "\n" in str(child.get("text") or "")]
        self.assertTrue(all(child.get("height", 0) >= 52 for child in two_line_nodes))
        desc_nodes = [child for child in section_nodes[0].get("children") or [] if str(child.get("id", "")).endswith("_desc")]
        self.assertTrue(desc_nodes)
        self.assertNotEqual(desc_nodes[0].get("height"), 44)
        title_nodes = [child for child in section_nodes[0].get("children") or [] if str(child.get("id", "")).endswith("_title")]
        self.assertTrue(title_nodes)
        self.assertGreaterEqual(title_nodes[0].get("height", 0), 37)

    def test_board_body_keeps_truncated_item_when_space_is_tight(self) -> None:
        board_ir = {
            "title": "Board",
            "file_name": "Board",
            "subtitle": "S",
            "theme": {"accent": "#2F6BFF", "accent2": "#7C3AED", "background": "#F8FAFC", "surface": "#FFFFFF", "text": "#0F172A", "muted": "#64748B"},
            "sections": [
                {
                    "id": "s1",
                    "kind": "overview",
                    "title": "Overview",
                    "description": "Long description forces less body room. " * 8,
                    "accent": "",
                    "items": [
                        "First item has enough content to wrap into two visible lines for height testing.",
                        "Second item should remain as a one-line truncated node when space is tight.",
                        "Third item may be dropped if there is no remaining space.",
                    ],
                    "nodes": [],
                    "edges": [],
                    "metrics": [],
                    "columns": [],
                    "rows": [],
                    "events": [],
                }
            ],
        }

        payload = json.loads(board_ir_to_whiteboard_dsl(board_ir))
        section = next(node for node in payload["nodes"] if node.get("type") == "frame")
        body_nodes = [child for child in section.get("children") or [] if str(child.get("id", "")).startswith("s0_body_")]

        self.assertTrue(body_nodes)
        self.assertTrue(any(str(child.get("text") or "").endswith("...") for child in body_nodes))
        self.assertTrue(all(child.get("height", 0) >= 31 for child in body_nodes))

    def test_board_adapter_dry_run_keeps_only_supported_timeline_fields(self) -> None:
        board_ir = {
            "title": "Board",
            "file_name": "Board",
            "subtitle": "S",
            "theme": {"accent": "#2F6BFF", "accent2": "#7C3AED", "background": "#F8FAFC", "surface": "#FFFFFF", "text": "#0F172A", "muted": "#64748B"},
            "sections": [
                {
                    "id": "t1",
                    "kind": "timeline",
                    "title": "Timeline",
                    "description": "",
                    "accent": "",
                    "items": [],
                    "nodes": [],
                    "edges": [],
                    "metrics": [],
                    "columns": [],
                    "rows": [],
                    "events": [{"date": "W1", "title": "Kickoff", "body": "Do", "owner": "Alice", "status": "open"}],
                }
            ],
        }
        result = publish_ir_to_feishu_board(_ir(), {"dry_run": True, "board_ir": board_ir})
        self.assertTrue(result["ok"])
        dsl_text = result.get("whiteboard_dsl") or ""
        self.assertNotIn("owner", dsl_text)
        self.assertNotIn("status", dsl_text)


def _settings(tmp: str, static_user_token: str = "") -> Settings:
    return Settings(
        gateway_public_base_url="https://gateway.example",
        public_web_base_url="https://web.example",
        oauth_redirect_uri="https://gateway.example/api/v1/auth/callback",
        session_secret="test-secret",
        lark_app_id="cli_test",
        lark_app_secret="app-secret",
        artifacts_drive_folder_token="fld",
        feishu_user_access_token=static_user_token,
        oauth_token_store_path=f"{tmp}/tokens.json",
    )


def _token_payload() -> dict:
    return {
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "expires_in": 3600,
        "open_id": "ou_open",
        "user_id": "ou_user",
        "union_id": "on_union",
    }


def _ir() -> dict:
    return {
        "schemaVersion": "0.2.0",
        "meta": {"title": "t"},
        "blocks": [{"id": "cover", "kind": "cover", "title": "t"}],
    }


def _slide_draft() -> dict:
    return {
        "title": "Deck",
        "file_name": "Deck File",
        "subtitle": "Subtitle",
        "theme": {"fontFace": "Microsoft YaHei"},
        "slides": [
            {
                "id": "s1",
                "title": "Summary",
                "layout": "summary",
                "content": {"outcomes": ["Done"], "next_steps": ["Review"]},
                "visual": {"highlightIndex": -1},
                "speaker_notes": "",
            }
        ],
    }


def _publish_ok() -> dict:
    return {"ok": True, "publish_result": {"doc": {"ok": True, "document_id": "doc", "url": "https://doc"}}}


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


if __name__ == "__main__":
    unittest.main()
