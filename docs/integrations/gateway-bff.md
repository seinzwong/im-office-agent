# Gateway 与 前端（BFF）对接

- **基址**：`GATEWAY_PUBLIC_BASE_URL`（生产为 HTTPS 公网域名，供内嵌 H5 与 `redirect_uri` 使用）
- **鉴权**：MVP 使用 **HttpOnly Cookie 会话**（`session` cookie）；OAuth 换票成功后写入。开发环境可开 `Access-Control-Allow-Credentials` 与 `localhost` 前端联调。
- **数据**：**总结文档/产物列表不存本地库**，`GET /api/v1/artifacts` 对飞书云盘 **配置的目标目录**（`ARTIFACTS_DRIVE_FOLDER_TOKEN`）拉取子文件列表。

## 主要端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/auth/login` | 302 至飞书授权（需配置 `LARK_APP_ID`） |
| GET | `/api/v1/auth/callback` | OAuth 回调，换票、建 session |
| GET | `/api/v1/me` | 当前用户摘要 |
| GET | `/api/v1/artifacts` | 云空间目标目录下文件（`{ "artifacts": [...] }`） |
| POST | `/api/v1/deliver` | 见请求体；返回 `202` + 提示文案，**不**含 `job_id`；不持久化任务状态 |
| POST | `/api/v1/jssdk/config` | 内嵌 H5 签名（body: `{ "url" }`） |
| GET | `/health` | 健康检查 |

## 错误体

```json
{ "code": "STRING", "message": "人类可读" }
```

## 交付请求体

```json
{
  "file_tokens": ["<飞书 file_token>"],
  "deliverables": { "whiteboard": true, "slides": true }
}
```

> OpenAPI 片段见 Gateway 的 `/openapi.json` 或由 FastAPI 自动产出。
