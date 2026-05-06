# Gateway 后端

- **框架**：[FastAPI](https://fastapi.tiangolo.com/)
- **配置**：本目录 **`gateway.yaml`**（由 [`gateway.example.yaml`](gateway.example.yaml) 复制），启动时由 `app/config.py` 读取；键名为 snake_case，与 `Settings` 字段一致。
- **飞书**：`lark_app_id` / `lark_app_secret` 换 **`tenant_access_token`**，经 **OpenAPI** 调用云盘与 docx 等（见 `app/feishu_openapi.py`）。
- **数据**：真源在飞书云盘 **`artifacts_drive_folder_token`** 指定目录；会话依赖 **`session_secret`**。
- **外部 Agents**：在 **`gateway.yaml`** 中配置 `agents_base_url` + `agents_m2m_token`，或 `agents_registry_path` 指向 [`app/agents/agents.example.yaml`](app/agents/agents.example.yaml) 复制后的 `agents.yaml`。与 Agents 的 HTTP 约定见下文 **「Agents 调用协议」**（本仓库以此节为唯一协议说明）。

## 本地开发

```bash
cd services/gateway
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**勿**将含真实密钥的 `gateway.yaml` 提交进 Git。

- 演示与联调见 [../../docs/demo-script.md](../../docs/demo-script.md)、[../../docs/integrations/gateway-agents.md](../../docs/integrations/gateway-agents.md)。

`GET /health` 应 `{"status":"ok"}`。

## 主要路由

- `/api/v1/*`：BFF、开发触发、OAuth
- `/lark/events`：飞书事件回调（URL 验证 `challenge`）

前端见 [../../apps/web](../../apps/web)。

---

## Agents 调用协议

实现方：**外部 Agents 服务**（本目录 `app/agents/client.py` 为客户端）。Gateway 与 Agents **只经 HTTPS JSON**；多实例时由 `gateway.yaml` 的 `agents_registry_path` 注册表按 `action` 选择不同 `base_url`，**请求体结构不变**。

### 端点与鉴权

| 项 | 说明 |
|----|------|
| URL | `POST {base_url}/v1/invoke`；`base_url` 来自 `gateway.yaml` 的 `agents_base_url`，或由注册表按 `action` 解析 |
| Header | `Authorization: Bearer <M2M_TOKEN>`；Token 为 `agents_m2m_token`，或注册表里该实例的 `m2m_token` |
| 超时 | Gateway 客户端当前约 120s；生产可按 action 拆分 |

HTTP 层除 **200** 外，可出现 **401**（鉴权失败）、**429**（限流）、**503**（不可用）；业务失败优先用下文 **200 + `ok: false`** 表达。

### 请求体（InvokeRequest）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `protocol_version` | int | 是 | 当前为 `1` |
| `request_id` | string | 是 | 幂等关联 |
| `idempotency_key` | string | 否 | 幂等键 |
| `trace_id` | string | 否 | 与 Gateway / 飞书日志关联 |
| `action` | string | 是 | 见下表「action 枚举」 |
| `context` | object | 否 | 非密钥上下文，见下「context」 |
| `payload` | object | 是 | 随 `action` 变化，见下「各 action」 |

**context** 常用键（均可选，可扩展）：`tenant_id`、`locale`、`user_open_id`、`task_ref` 等。

**action 枚举**（固定三种）：

- `summary_from_chat`：群聊 → 文档结构
- `deliver_whiteboard`：文档 → 画板 DSL
- `deliver_slides`：文档 → 幻灯片 XML

### 响应体（InvokeResponse）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `protocol_version` | int | 是 | 与请求一致，当前 `1` |
| `request_id` | string | 是 | 回显请求 |
| `ok` | bool | 是 | `false` 时须带 `error` |
| `error` | object | 条件 | `ok` 为 `false` 时必填，见「AgentError」 |
| `result` | object | 条件 | `ok` 为 `true` 时携带，形状随 `action` |

**AgentError**：`{ "code": string, "message": string, "details"?: object }`。

### `summary_from_chat`

**payload**（必填字段）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `chat_id` | string | 会话标识 |
| `message_text_aggregated` | string | 聚合后的聊天文本 |
| `time_hint_text` | string | 用户 @ 时附带的时间窗自然语言，可为空串 |
| `default_window_end_unix` | int | 消息时间锚点 t0（unix 秒） |

**result**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `time_window` | object | `start_unix`、`end_unix`（int，必填）；可选 `label` |
| `doc_title` | string | 文档标题 |
| `doc_content_xml` | string | docx 导向的 XML 正文 |
| `doc_open_url` | string | 可选；若由 Gateway 用飞书 OpenAPI 落盘，可省略，由 Gateway 补打开链接 |

### `deliver_whiteboard` / `deliver_slides`

**payload**（必填字段）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `document_id` | string | 源云文档 token |
| `body_plain` | string | 供生成的纯文本/摘要正文 |

**result**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `whiteboard_dsl` | string | `deliver_whiteboard` 使用；画板 DSL |
| `slide_xml_slides` | string | `deliver_slides` 使用；幻灯片 XML |
| `notes` | string | 可选说明 |

MVP 阶段 `whiteboard_dsl` / `slide_xml_slides` 可为占位串，由 Gateway 或后续流水线写回飞书。

### 错误码与前端文案（建议）

| Agents `error.code` | 用户可见文案 |
|---------------------|--------------|
| `INVALID_TIME_WINDOW` | 无法解析时间范围，已使用默认过去 24 小时。 |
| `UPSTREAM_LLM` | 生成服务繁忙，请重试。 |
| `PAYLOAD_INVALID` | 参数错误。 |

Gateway 内应对内部堆栈做映射，避免直接把 Agents 内部错误透传给 IM。
