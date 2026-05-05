from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from message_structuring.summary_clients.base import SummaryClientRequest
from message_structuring.summary_clients.stub import StubSummaryClient
from message_structuring.summary_clients.teammate_http import TeammateHTTPSummaryClient


def test_stub_client() -> None:
    client = StubSummaryClient()
    req = SummaryClientRequest(
        task_id="task_1",
        message_id="m1",
        topic_id="t1",
        topic_title="login incident",
        normalized_text="login api 500 need rollback",
        plain_text="login api 500 need rollback",
        importance_score=0.8,
        has_deliverable=True,
        deliverable_signals=["deadline", "task_action"],
    )
    resp = client.generate(req)
    assert resp.should_add is True
    assert isinstance(resp.summary_item, dict)
    assert "text" in resp.summary_item

    req_no = SummaryClientRequest(
        task_id="task_1",
        message_id="m2",
        topic_id="t1",
        topic_title="login incident",
        normalized_text="ok",
        plain_text="ok",
        importance_score=0.2,
        has_deliverable=False,
        deliverable_signals=[],
    )
    resp_no = client.generate(req_no)
    assert resp_no.should_add is False


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/summary/generate":
            self.send_response(404)
            self.end_headers()
            return
        body = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8")
        payload = json.loads(body)
        response = {
            "should_add": True,
            "summary_item": {
                "text": f"[{payload.get('topic_title')}] {payload.get('normalized_text')}",
                "topic_id": payload.get("topic_id"),
                "confidence": 0.88,
            },
            "reason": "teammate mock accepted",
        }
        data = json.dumps(response, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


def test_http_client() -> None:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        client = TeammateHTTPSummaryClient(base_url=base_url, api_key="test-key", timeout_seconds=3)
        req = SummaryClientRequest(
            task_id="task_2",
            message_id="m3",
            topic_id="t2",
            topic_title="api migration",
            normalized_text="api migration deadline friday",
            plain_text="api migration deadline friday",
            importance_score=0.7,
            has_deliverable=True,
            deliverable_signals=["deadline"],
        )
        resp = client.generate(req)
        assert resp.should_add is True
        assert resp.summary_item is not None
        assert "api migration" in resp.summary_item["text"]
    finally:
        server.shutdown()
        server.server_close()


def main() -> None:
    test_stub_client()
    test_http_client()
    print("Summary client contract tests passed.")


if __name__ == "__main__":
    main()
