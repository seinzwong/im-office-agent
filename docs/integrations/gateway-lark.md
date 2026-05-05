# Gateway 与 飞书开放平台 对接

## 自建应用

1. 在[飞书开放平台](https://open.feishu.cn/)（Lark 则为对应国际站）创建企业自建应用。

## 能力开关

- **机器人**（或「消息与群组」中接收消息）
- **网页** / 应用内网页：填写 `PUBLIC_WEB_BASE_URL` 指向的 H5 根地址
- **事件订阅**（`im.message.receive_v1` 等，以控制台为准）  
  - **HTTP 请求地址**：`{GATEWAY_PUBLIC_BASE_URL}/lark/events`（须公网 HTTPS）。  
  - **长连接（WebSocket）**：控制台改为长连接后，在 Gateway `gateway.yaml` 设置 `lark_ws_events_enabled: true`；进程启动时在独立线程内建立 SDK 长连接。**与 HTTP 回调二选一**。多副本部署时同一应用不宜多条并发长连接。
- **权限 scope**：`im:chat:readonly`、`im:message:readonly` 或发消息/读会话所需 scope；**云盘列目录**（`drive:drive:readonly` 等）、**创建云文档**（`docx:document` / `docx:document:create` 等）按 Gateway 当前 OpenAPI 调用申请。

## 加解密

若启用 Encrypt Key：事件体为加密 JSON。Gateway 实现 **Encrypt Key 解密**（MVP 可在未配置时仅支持明文/测试用 challenge）。

## URL 验证

`POST /lark/events` 首包可能为 `{"type":"url_verification","challenge":"..."} `，应 **原样** 返回 `{"challenge":"..."} `。

## OAuth 重定向

在应用「安全设置」中配置**重定向 URL** 为 `OAUTH_REDIRECT_URI`（与 `gateway-bff` 中 `/api/v1/auth/callback` 一致）。

## JSSDK

- 将应用 **AppID** 与「网页」安全域名指向前端公网与 Gateway 可签名域名。  
- 调用 Gateway `POST /api/v1/jssdk/config` 获取 `signature`、`timestamp` 等供前端 `config`。

## 机器人可见回复

- 在收到 `@` 或关键词后，飞书将消息事件经 **HTTP POST** 或 **长连接** 送达 Gateway（与控制台配置一致）。  
- Gateway 完成流程后，通过**开放平台开放接口**以应用身份**回复消息/卡片**（本仓库在 Gateway 内留接口封装位；具体 `tenant_access_token` 获取按官方要求）。

> **注意**：Gateway 使用环境变量中的 **`LARK_APP_ID` / `LARK_APP_SECRET`** 换取 `tenant_access_token`；与飞书网页 OAuth 使用同一应用时，控制台 **scope 并集** 需覆盖上述能力。
