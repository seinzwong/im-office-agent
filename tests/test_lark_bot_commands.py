from __future__ import annotations

import tempfile
import unittest
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
                return_value={"result": {"ir": _ir()}},
            ), patch.object(summary_from_event, "publish_ir", return_value=_publish_ok()) as publish, patch.object(
                summary_from_event, "_safe_reply"
            ):
                result = summary_from_event.run_summary_command("oc", "om", "ou_user", 1_700_000_000)
        self.assertTrue(result["ok"])
        self.assertEqual(publish.call_args.args[1]["user_access_token"], "access-token")


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
