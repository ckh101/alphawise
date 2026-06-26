# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

智能投研 Agent 桌面客户端。Electron + Node.js (Fastify) + Python Worker (JSON-RPC) + GLM-4.7 + PyTDX。
用户通过自然语言对话完成股票分析、行情查询、新闻搜索、策略回测。

**第一性原则**
- 新增任何新功能，不得影响已有正常功能
- 修复bug，不得影响已有正常功能
- 严格遵守以上两点，100%保证零影响

**四条行为准则**

1. 编码前先思考
不要假设。不要隐藏困惑。明确权衡。

- 明确陈述假设，不确定就询问
- 多种解释时呈现出来，不要默默选择
- 有更简单的方法就说出来，必要时提出反对意见
- 不清楚的地方停下来，指出困惑，询问

2. 简洁优先
解决问题的最少代码。不要臆测。

- 不添加超出要求的功能
- 不为一次性代码创建抽象
- 不添加未被要求的"灵活性"或"可配置性"
- 不为不可能发生的情况编写错误处理
- 如果写了200行而本来可以用50行，请重写

问自己： "资深工程师会说这过于复杂吗？"

3. 精准修改
只动必须动的部分。只清理自己造成的混乱。

- 不要"改进"相邻的代码、注释或格式
- 不要重构没有问题的部分
- 遵循现有风格，即使自己会做得不同
- 无关的死代码可以提及，但不要删除

测试标准： 每一行更改都应直接追溯到用户的请求。

4. 目标驱动执行
定义成功标准。循环验证直至通过。

将任务转化为可验证的目标：
- "添加验证" → "为无效输入编写测试，然后使其通过"
- "修复bug" → "编写重现该问题的测试，然后使其通过"
- "重构X" → "确保测试在重构前后都通过"

## Commands

```bash
# 启动 Electron 应用（自动启动 Node.js 后端 + Python Worker）
cd frontend && npm run dev

# 单独启动 Node.js 后端 (port 9998)
cd frontend && node server/index.js

# 单独启动 Python Worker
cd backend && .venv/Scripts/python.exe worker_main.py

# Lint
cd backend && ruff check .
cd frontend && npm run lint

# Test
cd backend && pytest
cd backend && pytest -k "test_name"    # 运行单个测试
cd backend && pytest --cov             # 带覆盖率

# Build (Windows/Mac installer)
cd frontend && npm run build:win       # Windows NSIS installer
cd frontend && npm run build:mac       # Mac DMG
```

## Architecture

### API Server — Node.js Fastify (`frontend/server/`)

Fastify HTTP server started as a child process by Electron `main.js`. Listens on `127.0.0.1:9998`.

**Routes** (`frontend/server/routes/`): health, settings, monitor, agent, scheduler, tdx, backtest, channel, stocks, watchlist, system (worker restart/status).

**Python Worker** (`frontend/server/lib/pythonWorker.js`):
- Manages a Python child process (`backend/worker_main.py`) that provides **HTTP JSON-RPC** services on `127.0.0.1:9999`
- `WorkerClient` (`workerClient.js`) handles HTTP transport (GET /ready, POST /rpc, POST /rpc/stream)
- **Persistent lifecycle**: Worker is detached from Node.js parent (`detached:true` + `unref()`), survives Electron restarts
- `ensureReady()` probes `:9999/ready` first to reuse existing persistent Worker; only spawns on cold start
- `_cleanupOrphanWorkers()` kills stale processes via PID file + port detection before spawning new ones
- `restart()` provides manual restart with cleanup-kill-wait-spawn-healthCheck cycle, protected by single-flight lock
- All AI, TDX, and backtest logic flows through `pythonWorker.request(method, params)`
- Startup wait: `main.js` `waitForServices()` polls `:9999/ready` (Agent `initialize_agent()` must finish before ok)

**Worker RPC Layer** (`backend/worker/rpc.py`):
- JSON-RPC dispatcher that deserializes requests and routes them to handler functions
- Handlers: `agent_handler.py`, `tdx_handler.py`, `backtest_handler.py`, `feishu_handler.py`, `scheduler_handler.py`, `pdf_handler.py`
- `worker_main.py` writes PID file to `<logDir>/worker.pid` on startup, removes on shutdown
- `/ready` endpoint returns `{"status":"ok"}` only after `initialize_agent()` completes; `/health` only checks liveness

### Python Backend (`backend/`)

Python worker process, entry point `worker_main.py`. Provides JSON-RPC methods:

**Agent System** — three orchestrators:
- `claude_orchestrator.py` — Primary orchestrator using Claude Agent SDK (`claude-agent-sdk`), handles complex multi-step analysis
- `react_orchestrator.py` — ReAct pattern: LLM intent recognition → tool planning → execution → report generation
- `orchestrator.py` — Base orchestrator class with shared logic
- `registry.py` — Auto-discovers skills from `skills/builtin/` and `skills/custom/`

**Skill System** — each skill is a directory with `SKILL.md` + `skill.py`:
- `skills/builtin/tdx-*` — PyTDX data (quotes, K-lines, stock info)
- `skills/builtin/glm-*` — GLM AI (chat, analysis)
- `skills/builtin/browser-search` — Playwright headless Bing scraper
- `skills/builtin/web-search` — Zhipu AI search API
- `skills/builtin/strategy-backtest` — TA-Lib based backtesting

Skills are auto-registered at startup. To add a new skill: create directory under `skills/builtin/`, add `SKILL.md` and `skill.py` with a class exposing `connect()` and relevant methods. Register in `react_orchestrator.py` `_register_tools_from_skill()`.

**Config** — `resources/config/config.dev.yaml`, env via `HARNESS_ENV`.

**Services** (`backend/harness/services/`): `claude_client.py` (Claude API), `glm_client.py` (GLM API), `feishu_client.py` (飞书 API), `trade_calendar.py` (交易日历 via baostock), `scheduler.py` (定时任务 via APScheduler), `memory.py` (对话记忆).

**Database** — SQLite at `frontend/server/data/harness.db` (managed by Node.js `db.js`). Includes `stocks` table (full stock list synced daily at 03:00 via `stockPool.js`), `watchlist_items`, `settings`, `sessions`.

**FastAPI Server** (`backend/harness/api/main.py`) — Separate HTTP API on port 8000 with routers for agent, backtest, channel, glm, monitor, scheduler, settings, tdx, watchlist.

### Frontend (`frontend/`)

Electron 28 frameless window. Single-page app, no build bundler, vanilla HTML/CSS/JS.

**Files:**
- `main.js` — Electron main process, IPC handlers, backend lifecycle
- `preload.js` — Context bridge exposing `window.*API` objects
- `src/renderer/index.html` — DOM structure: Header → App Body (Left Rail + Session Panel + Main + Right Drawer) → Bottom Bar
- `src/renderer/styles/main.css` — All styles (~860 lines), design tokens in `:root`, **no** Tailwind/glass-morphism/gradients
- `src/renderer/app.js` — Application logic, DOM manipulation, channel dropdown, session management (~2500 lines)
- `src/renderer/services/api.js` — Axios client, base URL `http://127.0.0.1:9998`

**Design system — Agent-Native:**
- Dark theme: backgrounds `#0d0d12` / `#131318` / `#19191f`, text `#e8e4dd` / `#8a8780` / `#5c5954`
- Gold `#c89640` used sparingly as accent only (brand dot, active states, send btn, focus rings)
- Zero `backdrop-filter`, zero `box-shadow` glow, zero `linear-gradient`
- All financial data: `font-family: var(--font-mono)` + `font-variant-numeric: tabular-nums`
- Small border-radius: 3/5/6px
- Cache busting via query params in `index.html` (`?v=N`) — bump after any CSS/JS change

### Key Data Flow

```
Renderer (HTML/JS) → Axios → Fastify (9998) → pythonWorker.request() → Python JSON-RPC Worker
```

User message → `/api/v1/agent/react/analyze` → Node.js route handler → `pythonWorker.request('agent.react.analyze', ...)` → `ReactOrchestrator.execute()`:
1. Extract stock symbols (regex + TDX name lookup)
2. LLM intent analysis → returns domain, symbols, tool_plan, response_mode
3. Execute tools per plan (stock info → quote → K-line → technical → fundamental → risk → web search)
4. Generate final response (report / chat / search_summary)

For SSE streaming: `/api/v1/agent/react/analyze/stream` → `pythonWorker.requestStream()` → async generator over JSON-RPC stream.

## Engineering Rules

5 mandatory rules from the design doc (`docs/superpowers/specs/`):

1. **No mock/hardcoded data** — all implementations must use real data sources
2. **Isolated changes** — regression test after every modification
3. **Mandatory logging** — log function entry/exit, branches, exceptions, external calls
4. **Code quality** — PEP 8 / Airbnb style, type annotations, single responsibility
5. **Config-driven** — no magic numbers, use config files

## Important Details

- TDX industry/province codes use their own encoding (NOT standard GB/T codes). Mapping tables are in `tdx-stock-info/skill.py`.
- **TDX connection singleton**: `tdx_handler.py` maintains a global `Quotes` connection via `_get_quotes()` — all handlers (quote/kline/stock_info/fetch_all_stocks) share it, no per-request reconnect.
- **Stock name lookup**: Name is resolved from SQLite `stocks` table (populated by `stockPool.js`, synced daily at 03:00). Python handlers no longer call `quotes.stocks()` for name lookup. Node.js watchlist route fills name from `db.getStockBySymbol()`.
- Browser search chain: Bing CN (`cn.bing.com`) → Bing International (`www.bing.com`). DuckDuckGo/Google/Sogou are blocked in current network.
- The orchestrator's `_phase_web_search` uses `asyncio.to_thread` to call sync Playwright from async context.
- SQLite database at `backend/data/harness.db`. DateTime fields stored as strings, use `func.strftime` for date aggregation.
- Frontend has no bundler — JS is loaded directly via `<script>` tags. Update cache version in `index.html` after CSS/JS changes.
- **Channel dropdown**: Custom `<div>`-based dropdown with search filter in header. Channels are loaded dynamically via `_loadChannelSelector()` → `window._updateChannelList()`. No `<select>` element.
- **Left rail icons**: 48px column with SVG icons. Active state shows gold left-edge indicator bar (`::before` pseudo-element). Click handler in app.js toggles `.active` class.
- **Session panel**: Toggle collapsed/expanded via `#railChat` click → `#sessionSidebar.classList.toggle('collapsed')`.
- Legacy `<select id="feishuChannelSelector">` may still be referenced in app.js for backward compat — kept hidden, synced via `_onChannelChange()`.
- **Feishu multi-channel dedup**: `_on_message()` in `feishu_client.py` checks `msg.mentions[].name` against `self.channel_name`. Messages @mentioning a different bot are skipped (Feishu pushes group messages to all bots). Mention names (`@_user_1`) in display text are replaced with display names for the frontend.
