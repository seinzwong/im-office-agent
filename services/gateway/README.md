# Gateway 后端

- **框架**：[FastAPI](https://fastapi.tiangolo.com/)
- **飞书**：`LARK_APP_ID` / `LARK_APP_SECRET` 换 **`tenant_access_token`**，经 **OpenAPI** 调用 `GET /open-apis/drive/v1/files` 列目录、`POST /open-apis/docx/v1/documents` 等在配置的云盘目录落库（见 `app/feishu_openapi.py`）。需在开放平台开通云盘与 docx 等对应权限并发版。
- **数据**：业务/产物不存 **SQLite/本地表**；真源在飞书云盘 **由 `ARTIFACTS_DRIVE_FOLDER_TOKEN` 指定目录**。会话仅依赖 Cookie 签名 `SESSION_SECRET`。

## 本地开发

```bash
cd services/gateway
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export DEV_SKIP_LARK=true
export AGENTS_BASE_URL=http://127.0.0.1:18080
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- 先启动 [Mock Agents](../../mocks/agents/main.py)（`uvicorn` 端口 18080），或指向真实 `AGENTS_BASE_URL`。
- 环境变量见 [../../docs/demo-script.md](../../docs/demo-script.md)。

`GET /health` 应 `{"status":"ok"}`。

## 主要路由

- `/api/v1/*`：BFF、开发触发、OAuth
- `/lark/events`：飞书事件回调（URL 验证 `challenge`）

前端见 [../../apps/web](../../apps/web)。
