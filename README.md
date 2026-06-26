# alphawise · 灵智投研助手

> 面向个人投资者与小型投研团队的智能投研 Agent 桌面客户端。自然语言对话即可完成股票分析、行情查询、资讯搜索、选股、策略回测,结果可一键推送飞书。

Electron + Node.js (Fastify) + Python Worker (JSON-RPC) + Claude Agent SDK + PyTDX。

---

## 目录

- [快速开始(普通用户)](#快速开始普通用户)
- [配置 AI 与数据源](#配置-ai-与数据源)
- [使用说明](#使用说明)
- [自行构建(开发者)](#自行构建开发者)
- [项目架构](#项目架构)
- [配置文件说明](#配置文件说明)
- [常见问题](#常见问题)
- [技术栈](#技术栈)

---

## 快速开始(普通用户)

### 1. 下载安装包

到 [Releases 页面](../../releases) 下载最新的 Windows 安装包:

- **`灵智投研助手-Setup-0.1.0.exe`** — NSIS 安装器,双击安装,自动创建桌面快捷方式

> macOS 版本需自行构建(见[自行构建](#自行构建开发者))。

### 2. 安装与启动

1. 双击安装包,按提示完成安装(可选择安装目录)
2. 从开始菜单或桌面快捷方式启动「灵智投研助手」
3. 首次启动会自动初始化后端 + 数据同步(约 30–60 秒),窗口显示「正在启动服务」

### 3. 首次配置

启动后,**必须先配置 AI 的 API Key** 才能正常对话(否则会提示「LLM 未配置」):

1. 点左上角设置图标(或侧边栏设置入口)
2. 填入 GLM / Claude 的 API Key 与模型(详见[配置 AI 与数据源](#配置-ai-与数据源))
3. 保存后即可开始对话

---

## 配置 AI 与数据源

所有配置在应用内「设置」页完成,保存在本地数据库,无需改代码。

### AI 模型(必填其一)

| 提供方 | 需要的字段 | 获取方式 |
|--------|-----------|---------|
| **智谱 GLM**(推荐,国内访问快) | API Key、Base URL、模型名(如 `glm-4.7`) | https://open.bigmodel.cn/ |
| **Claude**(通过 Agent SDK) | Anthropic API Key / Base URL | https://console.anthropic.com/ |

> Claude Agent SDK 走 `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` 环境变量,可在设置里配置。

### 行情数据

- **通达信(PyTDX)**:开箱即用,默认连公共服务器。建议本机安装通达信客户端以获得更稳定快速的数据(应用会自动检测本地通达信进程)。

### 飞书推送(可选)

配置飞书机器人后,Agent 分析结果可推送到指定飞书群/个人。需要:

- 飞书自建应用的 **App ID**、**App Secret**
- **Verification Token**、**Encrypt Key**(事件订阅用)
- 启用「机器人」能力,订阅消息事件

### 妙想数据(可选,增强选股/资讯)

如需「妙想选股」「资讯搜索」等增强能力,在设置里填 Iwencai API Key。

---

## 使用说明

### 对话

在输入框直接提问,Agent 自主决定调用哪些工具。例如:

- `你是谁` — 闲聊,不调工具
- `全方位分析卫宁健康` — 自动识别股票 → 拉行情/财报/K线/资讯 → 生成投研报告
- `贵州茅台今天的行情` — 单步查实时行情
- `换手率 3%–8%、涨幅 –1%–3%、股价 <30 元的股票` — 条件选股
- `回测均线交叉策略 on 600519` — 策略回测

### 下载报告

深度分析结果会生成 Markdown 报告,消息下方有 **「下载报告」** 按钮,可导出 PDF。

### 定时任务

在「定时任务」页可配置:

- 定时资讯推送、早盘总结、自动选股等
- 按周几 + 时间设定(工作日 9:15 等)
- 结果自动推送到飞书

> ⚠️ 定时任务依赖应用处于运行状态(后端常驻)。请保持应用开启,或后续接入系统级计划任务。

### 自选股与监控

- 添加自选股,实时刷新行情
- 多会话管理,保留上下文

---

## 自行构建(开发者)

### 前置要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| Node.js | **v22.x** | 后端运行时 + native binding 编译基准。必须 v22(应用打包了 node v22.14.0) |
| Python | 3.10+ | 开发环境用(.venv);构建时由打包脚本自动下载嵌入式 Python,无需本机配 |
| Git | 任意 | |

### 开发模式

```bash
git clone <repo-url>
cd alphawise

# 后端 Python 环境
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt

# 前端
cd ../frontend
npm install
npm rebuild better-sqlite3   # 首次/升级后,编译 native binding(必须 v22 node)
npm run dev                  # 自动生成图标 + 启动 Electron + 后端 + Worker
```

### 打包安装包

```bash
# Windows (产出 dist/灵智投研助手-Setup-0.1.0.exe)
cd frontend
npm run build:win

# macOS (产出 dist/alphawise-0.1.0.dmg)
npm run build:mac
```

构建脚本会自动:
1. 下载嵌入式 Python + Node v22 到 `build-backend/`
2. 安装生产依赖
3. 生成图标(`resources/icon.{ico,icns}`)
4. electron-builder 打包(NSIS installer / DMG)

> 完整构建机制与约束见 [`docs/构建方案与操作文档.md`](docs/构建方案与操作文档.md)。

---

## 项目架构

```
┌─────────────────────────────────────────────────────────────┐
│  Electron 主进程 (frontend/main.js)                          │
│  ├─ 渲染进程 (frontend/src/renderer/)  ← UI(vanilla HTML/CSS/JS)│
│  └─ spawn → Node.js 后端 (frontend/server/, Fastify :9998)   │
│              ├─ SQLite (better-sqlite3, 自选股/会话/设置/任务) │
│              ├─ node-cron 定时调度                           │
│              └─ spawn → Python Worker (backend/, :9999)      │
│                            ├─ Agent 编排 (Claude SDK + ReAct) │
│                            ├─ Skills (TDX / GLM / 搜索 / 回测)│
│                            ├─ 飞书通道 (WebSocket 推送)       │
│                            └─ APScheduler                    │
└─────────────────────────────────────────────────────────────┘
```

**数据流**:渲染进程 → Axios → Fastify(:9998)→ `pythonWorker.request()` → Python Worker(:9999)→ Skills/Agent。

### 关键目录

| 目录 | 作用 |
|------|------|
| `frontend/main.js` | Electron 主进程,窗口 + 后端生命周期 |
| `frontend/server/` | Fastify API 层 + Worker 管理 + DB + 调度 |
| `frontend/src/renderer/` | UI(vanilla,无框架) |
| `backend/harness/` | Agent 编排、Skills、服务(GLM/飞书/记忆) |
| `backend/skills/builtin/` | 内置技能(TDX/GLM/搜索/回测) |
| `backend/.claude/skills/` | 妙想技能(选股/资讯等,需 Iwencai key) |
| `backend/worker_main.py` | Python Worker 入口 |

> Python Worker 是 **detached 持久化进程**,Electron 关闭后仍存活,重开时复用(秒级启动)。

---

## 配置文件说明

| 文件 | 作用 | 是否含密钥 |
|------|------|-----------|
| `backend/.env.example` | 环境变量模板 | 否(占位符) |
| `backend/resources/config/config.dev.yaml` | 开发环境配置 | 否(密钥留空) |
| `backend/resources/config/config.prod.yaml` | 生产环境配置 | 否 |

**真实 API Key / 飞书密钥不进代码**,全部存在本地 SQLite 的 `settings` 表(应用内设置页配置)。仓库内无任何真实凭据。

---

## 常见问题

### 启动后卡在「正在启动服务」

后端或 Worker 启动较慢(首次需同步股票池 5 万只,约 30–60 秒)。若超过 2 分钟未进入主界面:

- 检查日志:`%APPDATA%/alphawise/logs/electron.log`(Windows)
- 常见原因:AI 未配置、端口被占用、native 模块加载失败

### 提示「LLM 未配置」

先到设置页配置 GLM 或 Claude 的 API Key。

### 定时任务没执行

定时任务依赖应用保持运行。确认应用在任务时间点处于开启状态。任务用本地时区(node-cron Asia/Shanghai)。

### 构建失败

参见 [`docs/构建方案与操作文档.md`](docs/构建方案与操作文档.md) 的「常见失败排查」表。

---

## 技术栈

| 层 | 技术 |
|----|------|
| 桌面壳 | Electron 28 |
| UI | Vanilla HTML5 + CSS(Agent-Native 设计令牌,无框架) |
| API 层 | Node.js + Fastify 5 |
| 数据库 | SQLite (better-sqlite3) |
| 引擎层 | Python + FastAPI Worker(JSON-RPC over HTTP) |
| AI | Claude Agent SDK、智谱 GLM |
| 行情 | PyTDX(通达信) |
| 图表 | ECharts |
| 推送 | 飞书 OpenAPI |

---

## License

MIT
