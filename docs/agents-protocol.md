# Agents 统一交互协议

实现方：外部 Agents 服务（本仓库仅 `services/gateway` 的 **客户端** 与本规范 OpenAPI 对齐）。

- **端点**：`POST {AGENTS_BASE_URL}/v1/invoke`
- **鉴权**：`Authorization: Bearer <AGENTS_M2M_TOKEN>`

## 请求信封

| 字段 | 说明 |
|------|------|
| `protocol_version` | 当前为 `1` |
| `request_id` | 幂等关联 |
| `idempotency_key` | 可选 |
| `trace_id` | 与 Gateway/飞书日志关联 |
| `action` | `summary_from_chat` \| `deliver_whiteboard` \| `deliver_slides` |
| `context` | 非密钥上下文 |
| `payload` | 随 action 变化 |

## `action` 与 `payload/result` 对应

- **summary_from_chat**：`payload` 为聊天聚合文本、chat_id、时间窗自然语言、默认 t0。`result` 需含 `time_window`（start/end unix）、`doc_title`、`doc_content_xml`；若产物由 Gateway 用飞书 OpenAPI 落盘，可将 `doc_open_url` 留空由 Gateway 写库后补录。
- **deliver_whiteboard** / **deliver_slides**：`payload` 为 `document_id` 与 `body_plain`；`result` 为 `whiteboard_dsl` 或 `slide_xml_slides`（MVP 可为占位串，由 Gateway 后续写飞书或存对象存储时消费）。

## 错误

`ok: false` 时返回 `error: { code, message, details? }`；与 Gateway 的「错误码 → 用户文案」表映射见 [gateway-agents.md](integrations/gateway-agents.md)。

## 规范文件

见 [../specs/agents-protocol/openapi.yaml](../specs/agents-protocol/openapi.yaml)，单一评审入口。
