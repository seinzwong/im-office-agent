# IR Industrial Adapter Demo

这个 demo 用来验证更接近工业级的 **IR → 多端投影** 架构。

核心思想是：

```text
Agent / 用户输入
        ↓
结构化 IR
        ↓
Adapter 投影
        ↓
飞书文档 / 飞书画板 / 飞书 Slides / 本地 PPTX
```

在这个 demo 里，Agent 不直接改飞书文档、画板或 PPTX，而是只改 `data/*.json` 里的 IR。`src/index.mjs` 负责把同一份 IR 投影成多个端侧产物。

---

## 1. Demo 能验证什么？

本 demo 支持从一份 IR 生成：

```text
doc.md
whiteboard.mmd
whiteboard.dsl.json
slides.json
ir.normalized.json
manifest.json
deck.pptx
assets/*.svg
```

其中：

| 输出文件 | 用途 |
|---|---|
| `doc.md` | 面向飞书文档 / Markdown 文档的线性内容 |
| `whiteboard.mmd` | Mermaid 流程图，适合快速写入飞书画板 |
| `whiteboard.dsl.json` | 带设计信息的画板 DSL，包含卡片、背景、圆角、颜色、字体等 |
| `slides.json` | 飞书 Slides 所需的 XML 字符串数组 |
| `deck.pptx` | 本地 PowerPoint 文件，包含主题、布局和 SVG 图片资产 |
| `ir.normalized.json` | 当前被实际渲染的 IR 快照 |
| `manifest.json` | 本轮输出产物清单 |

这个 demo 的重点不是“做一个固定 PPT”，而是验证：

```text
只改 IR，不改 Adapter 代码，也能生成不同风格、不同内容、不同平台的交付物。
```

---

## 2. 推荐目录结构

建议队员统一放在：

```powershell
D:\my_files\competitions\feishu\demos\ir-industrial-adapter-demo
```

进入目录：

```powershell
cd D:\my_files\competitions\feishu\demos\ir-industrial-adapter-demo
```

---

## 3. 环境准备

### 3.1 激活 conda 环境

本项目推荐使用单独的 conda 环境 `feishu`：

```powershell
conda activate feishu
```

检查 Node / npm 是否在该环境中：

```powershell
where.exe node
where.exe npm
node --version
npm --version
```

理想情况下路径应类似：

```text
D:\anaconda3\condaData\envs_dirs\feishu\...
```

### 3.2 设置 npm 国内镜像

国内网络环境建议先设置 npm 镜像：

```powershell
npm config set registry https://registry.npmmirror.com
npm config set fetch-retries 5
npm config set fetch-retry-mintimeout 20000
npm config set fetch-retry-maxtimeout 120000
```

验证：

```powershell
npm view @larksuite/cli version
npm view skills version
```

### 3.3 安装依赖

```powershell
npm install
```

### 3.4 安装并登录 lark-cli

如果本机还没有安装飞书 CLI：

```powershell
npm install -g @larksuite/cli
```

检查：

```powershell
lark-cli --version
```

初始化并登录：

```powershell
lark-cli config init
lark-cli auth login --recommend
lark-cli auth status
```

登录成功后，`lark-cli auth status` 应该能看到：

```text
"tokenStatus": "valid"
```

以及当前登录用户信息。

### 3.5 关于 `skills add`

官方示例里经常会出现：

```powershell
npx --yes skills add larksuite/cli -g
```

这个命令会从 GitHub clone：

```text
https://github.com/larksuite/cli.git
```

国内网络下经常会出现：

```text
Failed to clone repository
Recv failure: Connection was reset
Clone timed out after 60s
```

这不影响本 demo 的核心验证。因为我们真正调用的是已经安装好的：

```text
lark-cli
```

所以如果 `skills add` 失败，可以先跳过，继续跑本地生成和飞书写入实验。

---

## 4. 本地生成实验

### 4.1 生成默认版本

```powershell
npm run all
```

输出目录：

```text
out/default/
```

### 4.2 生成变体版本

```powershell
npm run all:variant
```

输出目录：

```text
out/variant/
```

### 4.3 模拟 Agent 修改 IR

```powershell
npm run agent:patch
```

这一步会生成：

```text
data/ir.agent.generated.json
```

然后用 Agent 生成后的 IR 重新投影：

```powershell
node src/index.mjs all --ir data/ir.agent.generated.json --out out/agent
```

输出目录：

```text
out/agent/
```

### 4.4 一键运行脚本

Windows PowerShell：

```powershell
.\scripts\run-local.ps1
```

Mac / Linux / Git Bash：

```bash
bash scripts/run-local.sh
```

---

## 5. 检查本地产物

运行完成后，检查：

```powershell
dir out\default
dir out\variant
dir out\agent
```

每个目录都应该包含：

```text
assets
deck.pptx
doc.md
ir.normalized.json
manifest.json
slides.json
whiteboard.dsl.json
whiteboard.mmd
```

打开本地 PPTX：

```powershell
start .\out\agent\deck.pptx
```

查看 Markdown：

```powershell
code .\out\agent\doc.md
```

查看 Mermaid：

```powershell
Get-Content -Raw -Encoding UTF8 .\out\agent\whiteboard.mmd
```

查看带设计的白板 DSL：

```powershell
code .\out\agent\whiteboard.dsl.json
```

---

## 6. Agent 到底可以改什么？

在不改 `src/index.mjs` 的前提下，Agent 可以修改 IR 里的这些内容：

```text
meta.title
meta.subtitle
meta.owner
meta.date
meta.audience
theme
assets
blocks
```

当前支持的 block 类型包括：

```text
cover
split
flow
metrics
cards
table
timeline
image
```

也就是说，Agent 可以通过生成不同 IR 实现：

```text
换标题
换副标题
换主题色
换图片资产
换流程图节点
换指标卡片
换风险表
换路线图
换 PPT 页面内容
换白板卡片内容
```

只要 IR 没有超出这些 block 类型，`src/index.mjs` 不需要修改。

### 什么时候必须改 Adapter？

如果要支持新的语义块，例如：

```text
chart
kanban
gantt
mindmap
code
citation
decisionLog
personas
```

就需要扩展：

```text
IR schema
Markdown Adapter
Whiteboard Adapter
Lark Slides Adapter
PPTX Adapter
```

---

## 7. 飞书写入实验总览

本 demo 可以验证三类飞书写入：

```text
doc.md            → 飞书文档
slides.json       → 飞书 Slides
whiteboard.mmd    → 飞书画板，朴素 Mermaid 流程图
whiteboard.dsl.json → 飞书画板，带设计 DSL，需要转 openapi raw
```

注意：

```text
whiteboard.mmd 只会生成默认 Mermaid 流程图，不会有复杂设计。
whiteboard.dsl.json 才包含卡片、背景色、圆角、间距、字体等设计信息。
```

---

## 8. 写入飞书文档

### 8.1 准备 Markdown 内容

```powershell
cd D:\my_files\competitions\feishu\demos\ir-industrial-adapter-demo

$content = Get-Content .\out\agent\doc.md -Raw -Encoding UTF8
```

### 8.2 创建飞书文档

```powershell
lark-cli docs +create `
  --api-version v2 `
  --doc-format markdown `
  --content "$content" `
  --as user | Tee-Object -FilePath .\out\agent\feishu-doc-create.response.json
```

查看响应：

```powershell
Get-Content .\out\agent\feishu-doc-create.response.json -Raw
```

如果 `docs +create` 提示参数不支持，先看当前版本帮助：

```powershell
lark-cli docs +create --help
```

---

## 9. 写入飞书 Slides

### 9.1 准备标题和 slides.json

```powershell
$title = (Get-Content .\data\ir.agent.generated.json -Raw -Encoding UTF8 | ConvertFrom-Json).meta.title
$slides = Get-Content .\out\agent\slides.json -Raw -Encoding UTF8
```

### 9.2 创建飞书 Slides

```powershell
lark-cli slides +create `
  --title "$title" `
  --slides "$slides" `
  --as user | Tee-Object -FilePath .\out\agent\feishu-slides-create.response.json
```

查看响应：

```powershell
Get-Content .\out\agent\feishu-slides-create.response.json -Raw
```

如果提示参数不支持，查看帮助：

```powershell
lark-cli slides +create --help
```

---

## 10. 写入飞书画板：朴素 Mermaid 版本

这一部分验证：

```text
out\agent\whiteboard.mmd → 飞书画板
```

这个版本会比较朴素，因为 Mermaid 只描述流程图，不携带复杂设计。

### 10.1 设置 PowerShell UTF-8

为了避免中文变成 `??` 或乱码，先执行：

```powershell
chcp 65001

[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()
```

### 10.2 准备变量

把 `$DOC` 换成你的飞书文档或 Wiki 页面 URL：

```powershell
$DOC = "https://你的飞书域名/wiki/xxxx"
$MMD = "D:\my_files\competitions\feishu\demos\ir-industrial-adapter-demo\out\agent\whiteboard.mmd"
```

检查 Mermaid 文件：

```powershell
Test-Path $MMD
Get-Content -Raw -Encoding UTF8 $MMD
```

如果看到中文乱码或 Mermaid 语法缺引号，可以先用一个干净的最小 Mermaid 验证闭环：

```powershell
@'
flowchart LR
  im["飞书 IM / 文档评论"]
  agent["Agent 生成 IR Patch"]
  validator["Schema + 业务校验"]
  ir["Versioned IR"]
  adapters["Doc / Board / Slides / PPTX Adapters"]
  publish["人工确认后发布"]

  im --> agent
  agent --> validator
  validator --> ir
  ir --> adapters
  adapters --> publish
'@ | Set-Content -Path $MMD -Encoding UTF8
```

### 10.3 在文档里插入空白画板

```powershell
$content = '<whiteboard type="blank"></whiteboard>'

lark-cli docs +update `
  --api-version v2 `
  --doc "$DOC" `
  --command append `
  --content "$content" `
  --as user | Tee-Object -FilePath .\out\agent\append-whiteboard.response.json
```

注意：我们实测的 `lark-cli docs +update` 不支持 `--format` 参数，所以不要加：

```powershell
--format json
```

### 10.4 从响应里提取 whiteboard block_token

先搜索：

```powershell
Select-String -Path .\out\agent\append-whiteboard.response.json -Pattern "block"
Select-String -Path .\out\agent\append-whiteboard.response.json -Pattern "token"
Select-String -Path .\out\agent\append-whiteboard.response.json -Pattern "whiteboard"
```

你应该能看到类似：

```json
{
  "block_id": "doxcnYaIUPVAXzAmPBY0pm6OJrh",
  "block_token": "LIHcwpeomhjaXEb7NPtcTVRKnAg",
  "block_type": "whiteboard"
}
```

设置变量：

```powershell
$WB = "这里换成 block_token"
```

注意：这个 token 不一定是 `wbcn...` 前缀。我们实测拿到的是普通 `block_token`，也可以被 `lark-cli whiteboard +update` 使用。

### 10.5 Dry-run 测试写入

```powershell
$TOKEN = "agentboard" + (Get-Date -Format "yyyyMMddHHmmss")

Get-Content -Raw -Encoding UTF8 $MMD | lark-cli whiteboard +update `
  --whiteboard-token "$WB" `
  --source - `
  --input_format mermaid `
  --idempotent-token "$TOKEN" `
  --overwrite `
  --dry-run `
  --as user | Tee-Object -FilePath .\out\agent\whiteboard-update.dryrun.json
```

dry-run 成功时，会看到类似：

```text
=== Dry Run ===
will call whiteboard open api to update content
```

并且 body 里有：

```json
"syntax_type": 2,
"parse_mode": 1,
"overwrite": true
```

### 10.6 正式写入 Mermaid 画板

正式写入需要加：

```powershell
--yes
```

否则会报：

```text
unsafe_operation_blocked
high-risk operation requires confirmation
hint: add --yes to confirm
```

正式写入：

```powershell
$TOKEN = "agentboard" + (Get-Date -Format "yyyyMMddHHmmss")

Get-Content -Raw -Encoding UTF8 $MMD | lark-cli whiteboard +update `
  --whiteboard-token "$WB" `
  --source - `
  --input_format mermaid `
  --idempotent-token "$TOKEN" `
  --overwrite `
  --yes `
  --as user | Tee-Object -FilePath .\out\agent\whiteboard-update.response.json
```

刷新飞书文档中的画板即可看到结果。

---

## 11. 写入飞书画板：带设计 DSL 版本

这一部分验证：

```text
out\agent\whiteboard.dsl.json → openapi raw → 飞书画板
```

Mermaid 版本只会显示普通流程图。要验证更精美的白板效果，应使用 `whiteboard.dsl.json`。

### 11.1 DSL 为什么更精美？

`whiteboard.dsl.json` 里包含：

```text
frame
text
fillColor
borderColor
borderRadius
padding
gap
fontSize
textColor
```

因此它可以表达：

```text
背景色
卡片
圆角
边框
标题
副标题
多块内容分区
```

### 11.2 设置变量

```powershell
$DSL = "D:\my_files\competitions\feishu\demos\ir-industrial-adapter-demo\out\agent\whiteboard.dsl.json"
$WB = "这里换成前面拿到的 block_token"
```

检查：

```powershell
Test-Path $DSL
Get-Content -Raw -Encoding UTF8 $DSL | Select-Object -First 1
```

### 11.3 把 DSL 转成 openapi raw 并 dry-run

```powershell
$TOKEN = "agentdsl" + (Get-Date -Format "yyyyMMddHHmmss")

npx -y @larksuite/whiteboard-cli@^0.2.10 -i "$DSL" --to openapi --format json | lark-cli whiteboard +update `
  --whiteboard-token "$WB" `
  --source - `
  --input_format raw `
  --idempotent-token "$TOKEN" `
  --overwrite `
  --dry-run `
  --as user `
  --yes | Tee-Object -FilePath .\out\agent\whiteboard-dsl-update.dryrun.json
```

### 11.4 正式写入 DSL 画板

```powershell
$TOKEN = "agentdsl" + (Get-Date -Format "yyyyMMddHHmmss")

npx -y @larksuite/whiteboard-cli@^0.2.10 -i "$DSL" --to openapi --format json | lark-cli whiteboard +update `
  --whiteboard-token "$WB" `
  --source - `
  --input_format raw `
  --idempotent-token "$TOKEN" `
  --overwrite `
  --as user `
  --yes | Tee-Object -FilePath .\out\agent\whiteboard-dsl-update.response.json
```

如果这里失败，先单独测试 DSL 转换：

```powershell
npx -y @larksuite/whiteboard-cli@^0.2.10 -i "$DSL" --to openapi --format json > .\out\agent\whiteboard.openapi.json
```

如果转换阶段失败，说明当前 demo 的 DSL 与 `@larksuite/whiteboard-cli` 当前支持的 DSL 协议还需要进一步适配。此时先用 Mermaid 路径验证白板写入闭环，后续再扩展 Whiteboard Adapter。

---

## 12. 查询画板内容

写入后可以查询：

```powershell
lark-cli whiteboard +query `
  --whiteboard-token "$WB" `
  --output_as code `
  --as user | Tee-Object -FilePath .\out\agent\whiteboard-query-code.json
```

如果参数不支持，先看帮助：

```powershell
lark-cli whiteboard +query --help
```

---

## 13. 常见问题与解决方式

### 13.1 `npx skills add larksuite/cli -g` 失败

现象：

```text
Failed to clone repository
Recv failure: Connection was reset
Clone timed out after 60s
```

原因：`skills add` 内部会 clone GitHub 仓库，和 npm 镜像无关。

处理：

```text
可以先跳过，不影响 lark-cli 写入实验。
```

### 13.2 `docs +update` 报 `unknown flag: --format`

现象：

```text
Error: unknown flag: --format
```

处理：去掉 `--format json`。当前实测版本的 `docs +update` 支持：

```text
--api-version
--as
--doc
--dry-run
-q / --jq
```

### 13.3 正式写入白板报 `unsafe_operation_blocked`

现象：

```text
high-risk operation requires confirmation
hint: add --yes to confirm
```

处理：正式写入时加：

```powershell
--yes
```

### 13.4 `Select-String wbcn` 找不到 token

不要只搜 `wbcn`。实测响应中可能是：

```json
"block_token": "LIHcwpeomhjaXEb7NPtcTVRKnAg"
```

应该搜索：

```powershell
Select-String -Path .\out\agent\append-whiteboard.response.json -Pattern "block"
Select-String -Path .\out\agent\append-whiteboard.response.json -Pattern "token"
Select-String -Path .\out\agent\append-whiteboard.response.json -Pattern "whiteboard"
```

### 13.5 中文变成 `??` 或乱码

先设置 PowerShell UTF-8：

```powershell
chcp 65001

[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()
```

然后用：

```powershell
Get-Content -Raw -Encoding UTF8 文件路径
Set-Content -Encoding UTF8 文件路径
```

VS Code 保存时确认右下角编码是：

```text
UTF-8
```

### 13.6 写入 Mermaid 后“没有设计”

这是正常现象。

原因：

```text
whiteboard.mmd 是 Mermaid，只包含节点和边。
whiteboard.dsl.json 才包含卡片、颜色、圆角、间距等设计信息。
```

要验证带设计的画板，请走：

```text
whiteboard.dsl.json → @larksuite/whiteboard-cli → openapi raw → lark-cli whiteboard +update
```

### 13.7 Wiki URL 和 docx URL

实测 Wiki URL 也可能可用，但如果 `docs +update` 表现异常，建议在浏览器里打开 Wiki 页面后，复制底层文档的 `docx/...` URL 再试。

---

## 14. 推荐给队员的完整实验顺序

从零开始，建议按这个顺序跑：

```powershell
conda activate feishu

cd D:\my_files\competitions\feishu\demos\ir-industrial-adapter-demo

npm config set registry https://registry.npmmirror.com
npm install

lark-cli --version
lark-cli auth status

npm run all
npm run all:variant
npm run agent:patch
node src/index.mjs all --ir data/ir.agent.generated.json --out out/agent

dir out\agent
start .\out\agent\deck.pptx
```

然后做飞书画板 Mermaid 写入：

```powershell
chcp 65001

[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$DOC = "这里换成你的飞书文档或 Wiki URL"
$MMD = "D:\my_files\competitions\feishu\demos\ir-industrial-adapter-demo\out\agent\whiteboard.mmd"

$content = '<whiteboard type="blank"></whiteboard>'

lark-cli docs +update `
  --api-version v2 `
  --doc "$DOC" `
  --command append `
  --content "$content" `
  --as user | Tee-Object -FilePath .\out\agent\append-whiteboard.response.json

Select-String -Path .\out\agent\append-whiteboard.response.json -Pattern "block"
Select-String -Path .\out\agent\append-whiteboard.response.json -Pattern "token"
Select-String -Path .\out\agent\append-whiteboard.response.json -Pattern "whiteboard"

$WB = "这里换成 block_token"

$TOKEN = "agentboard" + (Get-Date -Format "yyyyMMddHHmmss")

Get-Content -Raw -Encoding UTF8 $MMD | lark-cli whiteboard +update `
  --whiteboard-token "$WB" `
  --source - `
  --input_format mermaid `
  --idempotent-token "$TOKEN" `
  --overwrite `
  --dry-run `
  --as user | Tee-Object -FilePath .\out\agent\whiteboard-update.dryrun.json

$TOKEN = "agentboard" + (Get-Date -Format "yyyyMMddHHmmss")

Get-Content -Raw -Encoding UTF8 $MMD | lark-cli whiteboard +update `
  --whiteboard-token "$WB" `
  --source - `
  --input_format mermaid `
  --idempotent-token "$TOKEN" `
  --overwrite `
  --yes `
  --as user | Tee-Object -FilePath .\out\agent\whiteboard-update.response.json
```

最后做带设计 DSL 写入：

```powershell
$DSL = "D:\my_files\competitions\feishu\demos\ir-industrial-adapter-demo\out\agent\whiteboard.dsl.json"
$TOKEN = "agentdsl" + (Get-Date -Format "yyyyMMddHHmmss")

npx -y @larksuite/whiteboard-cli@^0.2.10 -i "$DSL" --to openapi --format json | lark-cli whiteboard +update `
  --whiteboard-token "$WB" `
  --source - `
  --input_format raw `
  --idempotent-token "$TOKEN" `
  --overwrite `
  --dry-run `
  --as user `
  --yes | Tee-Object -FilePath .\out\agent\whiteboard-dsl-update.dryrun.json

$TOKEN = "agentdsl" + (Get-Date -Format "yyyyMMddHHmmss")

npx -y @larksuite/whiteboard-cli@^0.2.10 -i "$DSL" --to openapi --format json | lark-cli whiteboard +update `
  --whiteboard-token "$WB" `
  --source - `
  --input_format raw `
  --idempotent-token "$TOKEN" `
  --overwrite `
  --as user `
  --yes | Tee-Object -FilePath .\out\agent\whiteboard-dsl-update.response.json
```

---

## 15. 工业化理解

这个 demo 对应工业系统里的几个模块：

| Demo 文件 / 步骤 | 工业系统对应模块 |
|---|---|
| `data/ir.json` | 结构化需求状态 / 项目事实源 |
| `data/ir.variant.json` | 不同客户、不同场景、不同主题的 IR |
| `data/ir.agent.generated.json` | Agent 生成后的候选 IR |
| `src/agent-patch-demo.mjs` | Agent patch 模拟器 |
| `src/index.mjs` | Adapter 编排器 |
| `doc.md` | 文档 Adapter 输出 |
| `whiteboard.mmd` | Mermaid 快速画板 Adapter 输出 |
| `whiteboard.dsl.json` | 设计化画板 Adapter 输出 |
| `slides.json` | 飞书 Slides Adapter 输出 |
| `deck.pptx` | PPTX Adapter 输出 |
| `lark-cli` | 飞书端发布通道 |

真实系统里通常还会增加：

```text
JSON Schema 校验
业务规则校验
Agent JSON Patch
预览页面
人工确认
版本管理
projection revision
飞书端回读
diff / merge
回滚
权限控制
审计日志
```

---

## 16. 一句话结论

这个 demo 证明的是：

```text
Agent 不需要直接操作飞书文档、白板或 PPT。
Agent 只需要维护结构化 IR。
Adapter 可以把同一份 IR 投影成不同平台的交付物。
```

当前 demo 已经可以验证：

```text
本地 PPTX 生成
Markdown 文档生成
Mermaid 画板写入
飞书 whiteboard block_token 提取
whiteboard +update dry-run
whiteboard +update --yes 正式覆盖
Agent 改 IR 后重新生成 out/agent
```

后续比赛开发应围绕：

```text
更严格的 IR schema
更强的 Adapter
更好的飞书端发布
更安全的 Agent patch
更完善的人工确认流程
```

继续扩展。
