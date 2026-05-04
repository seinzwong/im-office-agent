# Gateway 后端

- **框架**：[FastAPI](https://fastapi.tiangolo.com/)
- **飞书**：`LARK_APP_ID` / `LARK_APP_SECRET` 换 **`tenant_access_token`**，经 **OpenAPI** 调用 `GET /open-apis/drive/v1/files` 列目录、`POST /open-apis/docx/v1/documents` 等在配置的云盘目录落库（见 `app/feishu_openapi.py`）。需在开放平台开通云盘与 docx 等对应权限并发版。
- **数据**：业务/产物不存 **SQLite/本地表**；真源在飞书云盘 **由 `ARTIFACTS_DRIVE_FOLDER_TOKEN` 指定目录**。会话仅依赖 Cookie 签名 `SESSION_SECRET`。
- **外部 Agents**：单实例使用 `AGENTS_BASE_URL` + `AGENTS_M2M_TOKEN`；多实例使用 **`AGENTS_REGISTRY_PATH`** 指向 YAML（示例见 [`config/agents.example.yaml`](config/agents.example.yaml)），启动时加载并按 `action` 路由。

## 本地开发

**单实例：**

```bash
cd services/gateway
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export DEV_SKIP_LARK=true
export AGENTS_BASE_URL=<你的 Agents 基址>
export AGENTS_M2M_TOKEN=<与 Agents 约定一致>
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**多实例（注册表）：**

```bash
export DEV_SKIP_LARK=true
export AGENTS_REGISTRY_PATH=config/agents.yaml
export AGENTS_M2M_TOKEN=<默认 M2M，或与 YAML 中各 agent 的 m2m_token 配合>
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

将 `config/agents.example.yaml` 复制为 `config/agents.yaml` 并改成真实 `base_url`。**勿**将生产密钥提交进 Git。

- 环境变量说明见 [../../docs/demo-script.md](../../docs/demo-script.md) 与 [../../docs/integrations/gateway-agents.md](../../docs/integrations/gateway-agents.md)。

`GET /health` 应 `{"status":"ok"}`。

## 主要路由

- `/api/v1/*`：BFF、开发触发、OAuth
- `/lark/events`：飞书事件回调（URL 验证 `challenge`）

前端见 [../../apps/web](../../apps/web)。
