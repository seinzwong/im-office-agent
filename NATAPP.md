# NATAPP 内网穿透启动手册

本文档固化当前项目接入 NATAPP 的流程。目标是：重启电脑、重启 NATAPP，或 NATAPP 免费域名变化后，可以快速恢复公网访问。

## 当前端口拓扑

NATAPP 只转发一个本地端口，因此本项目让公网入口先进入前端，再由 Vite 代理 API 到后端：

```text
公网用户 / 飞书回调
        |
        v
https://im-office-agent.nat100.top
        |
        v
NATAPP -> http://127.0.0.1:8000
        |
        +-- 前端 Vite: http://127.0.0.1:8000
        |
        +-- /api /lark /health 代理到后端
            http://127.0.0.1:8001
```

当前公网地址：

```text
https://im-office-agent.nat100.top
```

如果 NATAPP 域名变了，把文档里的 `im-office-agent.nat100.top` 换成新的域名，并按下方“域名变化时要改的配置”处理。

## 一次性配置

前端本地配置：[apps/web/config.yaml](apps/web/config.yaml)

```yaml
dev:
  host: "127.0.0.1"
  port: 8000
  gateway_proxy_target: "http://127.0.0.1:8001"
```

后端公网配置：[services/gateway/gateway.yaml](services/gateway/gateway.yaml)

```yaml
gateway_public_base_url: https://im-office-agent.nat100.top
public_web_base_url: https://im-office-agent.nat100.top
oauth_redirect_uri: https://im-office-agent.nat100.top/api/v1/auth/callback
cors_origins: http://127.0.0.1:8000,http://localhost:8000,https://im-office-agent.nat100.top
```

Vite 允许 NATAPP Host：[apps/web/vite.config.ts](apps/web/vite.config.ts)

```ts
allowedHosts: ["im-office-agent.nat100.top"],
```

如果公网访问返回 `403 Forbidden`，通常就是这里没有允许当前 NATAPP 域名。

## 使用的 Python 环境

后端使用 Conda 环境 `summary-selector`：

```text
C:\Anaconda3\envs\summary-selector\python.exe
```

确认环境：

```powershell
C:\Anaconda3\envs\summary-selector\python.exe --version
C:\Anaconda3\envs\summary-selector\python.exe -m pip check
```

首次缺依赖时安装：

```powershell
cd C:\Users\Seinz\Desktop\Lark-ai-project\fan-NATAPP\services\gateway
C:\Anaconda3\envs\summary-selector\python.exe -m pip install -r requirements.txt
```

如果只缺飞书 SDK：

```powershell
C:\Anaconda3\envs\summary-selector\python.exe -m pip install "lark-oapi>=1.5.0,<2"
```

## 前端依赖

首次或 `node_modules` 丢失时：

```powershell
cd C:\Users\Seinz\Desktop\Lark-ai-project\fan-NATAPP\apps\web
npm.cmd install --cache .\.npm-cache
```

验证前端构建：

```powershell
npm.cmd run build
```

PowerShell 里如果直接执行 `npm` 报 `npm.ps1` 执行策略错误，用 `npm.cmd`。

## 启动顺序

### 1. 启动 NATAPP
natapp.exe -authtoken=你的authtoken
确保 NATAPP 显示：

```text
Tunnel Status    Online
Forwarding       http://你的域名.natappfree.cc -> http://127.0.0.1:8000
```

当前示例：

```text
Forwarding https://im-office-agent.nat100.top -> http://127.0.0.1:8000
```

### 2. 启动后端

新开 PowerShell：

```powershell
cd C:\Users\Seinz\Desktop\Lark-ai-project\fan-NATAPP\services\gateway
C:\Anaconda3\envs\summary-selector\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

看到下面信息表示后端启动成功：

```text
Uvicorn running on http://127.0.0.1:8001
```

### 3. 启动前端

再新开 PowerShell：

```powershell
cd C:\Users\Seinz\Desktop\Lark-ai-project\fan-NATAPP\apps\web
npm.cmd run dev
```

看到下面信息表示前端启动成功：

```text
Local: http://127.0.0.1:8000/
```

## 后台启动方式

如果不想开多个终端，可以在仓库根目录执行：

```powershell
cd C:\Users\Seinz\Desktop\Lark-ai-project\fan-NATAPP
New-Item -ItemType Directory -Force logs

Start-Process -FilePath "C:\Anaconda3\envs\summary-selector\python.exe" `
  -ArgumentList @("-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8001") `
  -WorkingDirectory "C:\Users\Seinz\Desktop\Lark-ai-project\fan-NATAPP\services\gateway" `
  -RedirectStandardOutput "C:\Users\Seinz\Desktop\Lark-ai-project\fan-NATAPP\logs\gateway.out.log" `
  -RedirectStandardError "C:\Users\Seinz\Desktop\Lark-ai-project\fan-NATAPP\logs\gateway.err.log" `
  -WindowStyle Hidden

Start-Process -FilePath "npm.cmd" `
  -ArgumentList @("run","dev") `
  -WorkingDirectory "C:\Users\Seinz\Desktop\Lark-ai-project\fan-NATAPP\apps\web" `
  -RedirectStandardOutput "C:\Users\Seinz\Desktop\Lark-ai-project\fan-NATAPP\logs\web.out.log" `
  -RedirectStandardError "C:\Users\Seinz\Desktop\Lark-ai-project\fan-NATAPP\logs\web.err.log" `
  -WindowStyle Hidden
```

日志位置：

```text
logs/gateway.err.log
logs/gateway.out.log
logs/web.err.log
logs/web.out.log
```

## 验证命令

本地后端：

```powershell
Invoke-RestMethod http://127.0.0.1:8001/health
```

本地前端代理到后端：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

公网前端：

```powershell
Invoke-WebRequest https://im-office-agent.nat100.top -UseBasicParsing
```

公网代理到后端：

```powershell
Invoke-RestMethod https://im-office-agent.nat100.top/health
```

成功时 `/health` 返回：

```json
{"status":"ok"}
```

## 域名变化时要改的配置

NATAPP 二级域名通常是固定的；如果后续换了域名，假设新域名是：

```text
https://new-domain.nat100.top
```

需要改三处。

1. [services/gateway/gateway.yaml](services/gateway/gateway.yaml)

```yaml
gateway_public_base_url: https://new-domain.nat100.top
public_web_base_url: https://new-domain.nat100.top
oauth_redirect_uri: https://new-domain.nat100.top/api/v1/auth/callback
cors_origins: http://127.0.0.1:8000,http://localhost:8000,https://new-domain.nat100.top
```

2. [apps/web/vite.config.ts](apps/web/vite.config.ts)

```ts
allowedHosts: ["new-domain.nat100.top"],
```

3. 飞书开放平台配置

如果使用 HTTP 回调，把事件订阅请求地址改为：

```text
https://new-domain.nat100.top/lark/events
```

如果使用网页应用 OAuth，把回调地址改为：

```text
https://new-domain.nat100.top/api/v1/auth/callback
```

改完后重启前端和后端。

## 常见问题

### 公网访问返回 403

大概率是 Vite 拦截了 NATAPP 的 Host。

检查 [apps/web/vite.config.ts](apps/web/vite.config.ts)：

```ts
allowedHosts: ["当前NATAPP域名"],
```

注意这里只写域名，不写 `http://`。

### `http://127.0.0.1:8000/health` 返回 500

通常是后端 `8001` 没启动，或前端代理目标不对。

先测：

```powershell
Invoke-RestMethod http://127.0.0.1:8001/health
```

再检查 [apps/web/config.yaml](apps/web/config.yaml)：

```yaml
gateway_proxy_target: "http://127.0.0.1:8001"
```

### PowerShell 不能执行 npm

如果看到 `npm.ps1` 被执行策略禁止，使用：

```powershell
npm.cmd run dev
npm.cmd install --cache .\.npm-cache
```

### npm install 报 EACCES 或 Exit handler never called

通常是网络或 npm cache 问题。使用项目内 cache 重试：

```powershell
cd C:\Users\Seinz\Desktop\Lark-ai-project\fan-NATAPP\apps\web
npm.cmd install --cache .\.npm-cache
```

### 后端缺 `lark_oapi`

安装到 `summary-selector` 环境：

```powershell
C:\Anaconda3\envs\summary-selector\python.exe -m pip install "lark-oapi>=1.5.0,<2"
```

### 端口被占用

查看端口：

```powershell
Get-NetTCPConnection -LocalPort 8000,8001 -ErrorAction SilentlyContinue |
  Select-Object LocalAddress,LocalPort,State,OwningProcess
```

查看进程：

```powershell
Get-Process -Id <OwningProcess>
```

确认是本项目旧进程后再停止：

```powershell
Stop-Process -Id <进程ID>
```

## 快速恢复清单

每次重启后按这个顺序：

1. 启动 NATAPP，确认转发到 `127.0.0.1:8000`。
2. 如果 NATAPP 域名变了，更新 `gateway.yaml` 和 `vite.config.ts`。
3. 启动后端 `summary-selector` 环境，监听 `8001`。
4. 启动前端 Vite，监听 `8000`。
5. 验证 `http://127.0.0.1:8000/health`。
6. 验证 `http://当前NATAPP域名/health`。
