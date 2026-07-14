# 窗口关闭行为与托盘常驻 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 点窗口关闭按钮支持"最小化到托盘常驻/退出程序"，首次询问并记住选择，托盘和设置页都能改。

**Architecture:** 在 main.js 拦截窗口 `close` 事件，按 userData/config.json 里的 `closeBehavior`（ask/minimize/quit）决定 hide/quit/弹窗。新增 Tray 实例（窗口隐藏时后端继续跑）。设置页通过新增 IPC 读写 closeBehavior。

**Tech Stack:** Electron 28（Tray、dialog、ipcMain）、Node fs、vanilla JS 前端。

## Global Constraints

- 不动后端、Python、worker、db、server/ —— 只改 Electron 层（main.js / preload.js / 前端三件套）。
- 托盘图标定位用 `path.join(__dirname, 'icon.png')`（与现有窗口图标一致，开发生产通用）。
- config.json 路径用 `path.join(app.getPath('userData'), 'config.json')`，不硬编码。
- 中文菜单文案：显示窗口 / 重置关闭行为 / 退出。
- 遵循现有代码风格（main.js 单双引号混用、4 空格缩进；preload.js 用 contextBridge.exposeInMainWorld）。
- 完成后 cache busting：index.html 里给改动的 css/js `?v=N` 加 1。

参考设计文档：`docs/superpowers/specs/2026-07-14-close-to-tray-design.md`

---

## File Structure

| 文件 | 职责 | 改动类型 |
|---|---|---|
| `frontend/main.js` | 主进程：close 拦截、Tray、config 读写、IPC handler | 修改 |
| `frontend/preload.js` | 暴露 `closeBehaviorAPI` 给渲染进程 | 修改 |
| `frontend/src/renderer/index.html` | 设置页加"关闭按钮行为"select | 修改 |
| `frontend/src/renderer/app.js` | 设置页加载/保存 closeBehavior | 修改 |

---

## Task 1: 主进程 config 读写 + close 拦截

**Files:**
- Modify: `frontend/main.js`（顶部 require 区 + 新增 config 模块 + close 事件）

**Interfaces:**
- Produces: `getCloseBehavior()` → `'ask'|'minimize'|'quit'`；`setCloseBehavior(v)`；`isQuitting` 全局标志。后续 Task 2/3 依赖这些。

- [ ] **Step 1: 在 main.js 顶部 require 区下方加 config 读写模块**

定位 `main.js` 第 8 行 `const { app, BrowserWindow, ... } = require('electron');` 之后，加：

```js
const fs = require('fs');

// === 关闭行为配置（userData/config.json）===
// closeBehavior: 'ask' | 'minimize' | 'quit'
const CONFIG_PATH = () => path.join(app.getPath('userData'), 'config.json');

function getCloseBehavior() {
    try {
        if (fs.existsSync(CONFIG_PATH())) {
            const cfg = JSON.parse(fs.readFileSync(CONFIG_PATH(), 'utf8'));
            if (['ask', 'minimize', 'quit'].includes(cfg.closeBehavior)) {
                return cfg.closeBehavior;
            }
        }
    } catch (e) {
        console.error('[main] read closeBehavior failed:', e.message);
    }
    return 'ask';
}

function setCloseBehavior(v) {
    try {
        let cfg = {};
        if (fs.existsSync(CONFIG_PATH())) {
            cfg = JSON.parse(fs.readFileSync(CONFIG_PATH(), 'utf8'));
        }
        cfg.closeBehavior = v;
        fs.writeFileSync(CONFIG_PATH(), JSON.stringify(cfg, null, 2), 'utf8');
    } catch (e) {
        console.error('[main] write closeBehavior failed:', e.message);
    }
}
```

注意：`path` 已在文件顶部 require（确认第 1-7 行有 `const path = require('path');`，若无需补）。

- [ ] **Step 2: 加 isQuitting 全局标志**

在 `let mainWindow;`（约第 145 行附近，createWindow 之前）加：

```js
let isQuitting = false;
```

- [ ] **Step 3: 在 createWindow 里加 close 事件拦截**

找到 `main.js:211` 现有的 `mainWindow.on('closed', ...)`，在它**之前**插入 close 拦截：

```js
    // 关闭按钮拦截：按 closeBehavior 决定隐藏/退出/询问
    mainWindow.on('close', (e) => {
        if (isQuitting) return;  // 真退出放行
        const behavior = getCloseBehavior();
        if (behavior === 'minimize') {
            e.preventDefault();
            mainWindow.hide();
            return;
        }
        if (behavior === 'quit') {
            isQuitting = true;
            return;  // 放行，触发 closed → app.quit
        }
        // ask：弹窗
        e.preventDefault();
        const choice = dialog.showMessageBoxSync(mainWindow, {
            type: 'question',
            title: '关闭窗口',
            message: '关闭后希望怎么做？',
            buttons: ['最小化到托盘', '退出程序'],
            checkboxLabel: '记住选择，以后不再询问',
            checkboxChecked: false,
            defaultId: 0,
        });
        const remember = choice.checkboxChecked;
        if (choice.response === 1) {
            // 退出
            if (remember) setCloseBehavior('quit');
            isQuitting = true;
            mainWindow.close();  // 这次会放行（isQuitting=true）
        } else {
            // 最小化
            if (remember) setCloseBehavior('minimize');
            mainWindow.hide();
        }
    });
```



- [ ] **Step 4: 确认 `dialog` 已在顶部 require**

`main.js:8` 的解构里要有 `dialog`（前面 grep 确认过已有：`const { app, BrowserWindow, ipcMain, Menu, dialog } = require('electron');`）。无需改动。

- [ ] **Step 5: 启动开发模式验证（手动）**

```bash
cd frontend && npm run dev
```
Expected: 应用启动正常。点 × → 弹窗，选"最小化"+勾记住 → 窗口隐藏。再从任务栏/Dock 恢复窗口（此时还没托盘，只能从系统任务管理器或重启恢复——验证逻辑即可）。再点 × → 直接最小化（不弹窗，因为记住了）。

- [ ] **Step 6: Commit**

```bash
git add frontend/main.js
git commit -m "feat: 拦截窗口close事件，支持最小化到托盘/退出询问"
```

---

## Task 2: Tray 托盘实例

**Files:**
- Modify: `frontend/main.js`（顶部 require 加 Tray + nativeImage，app.ready 时创建，before-quit 时销毁）

**Interfaces:**
- Consumes: Task 1 的 `isQuitting`、`setCloseBehavior`
- Produces: `tray` 全局实例。

- [ ] **Step 1: 顶部 require 加 Tray 和 nativeImage**

修改 `main.js:8`：

```js
const { app, BrowserWindow, ipcMain, Menu, dialog, Tray, nativeImage } = require('electron');
```

- [ ] **Step 2: 加 tray 全局变量**

在 `let isQuitting = false;`（Task 1 Step 2 加的）旁加：

```js
let tray = null;
```

- [ ] **Step 3: 加 createTray 函数（在 createWindow 函数之后定义）**

```js
function createTray() {
    const iconPath = path.join(__dirname, 'icon.png');
    const icon = nativeImage.createFromPath(iconPath);
    tray = new Tray(icon);
    tray.setToolTip('灵智投研助手');

    const contextMenu = Menu.buildFromTemplate([
        {
            label: '显示窗口',
            click: () => {
                if (mainWindow) {
                    mainWindow.show();
                    mainWindow.focus();
                }
            },
        },
        {
            label: '重置关闭行为',
            click: () => {
                setCloseBehavior('ask');
                dialog.showMessageBoxSync({
                    type: 'info',
                    title: '已重置',
                    message: '已重置为每次询问。下次点关闭按钮会重新弹出选择。',
                });
            },
        },
        { type: 'separator' },
        {
            label: '退出',
            click: () => {
                isQuitting = true;
                app.quit();
            },
        },
    ]);
    tray.setContextMenu(contextMenu);

    // 单击托盘显示窗口（Windows 习惯）
    tray.on('click', () => {
        if (mainWindow) {
            if (mainWindow.isVisible()) {
                mainWindow.focus();
            } else {
                mainWindow.show();
                mainWindow.focus();
            }
        }
    });
}
```

- [ ] **Step 4: 在 app.ready 里调 createTray**

找到 `main.js:278` 附近的 `app.on('ready', ...)`（或 `app.whenReady()`），在 `createWindow();`（281 行）之后加：

```js
    createTray();
```

- [ ] **Step 5: before-quit 时销毁 tray**

找到 `main.js:318` 的 `app.on('before-quit', ...)`，在 `stopBackend();` 之后加：

```js
    if (tray) {
        tray.destroy();
        tray = null;
    }
```

- [ ] **Step 6: 启动验证**

```bash
cd frontend && npm run dev
```
Expected: 系统托盘出现应用图标。右键托盘 → 看到"显示窗口/重置关闭行为/退出"三项菜单。点"显示窗口"→ 窗口显示。点"退出"→ 应用完全退出（托盘消失）。点"重置关闭行为"→ 提示框，下次点 × 重新弹窗。

- [ ] **Step 7: Commit**

```bash
git add frontend/main.js
git commit -m "feat: 添加系统托盘（显示窗口/重置关闭行为/退出）"
```

---

## Task 3: IPC handler + preload 暴露 closeBehaviorAPI

**Files:**
- Modify: `frontend/main.js`（加 2 个 ipcMain.handle）
- Modify: `frontend/preload.js`（暴露 closeBehaviorAPI）

**Interfaces:**
- Consumes: Task 1 的 `getCloseBehavior`/`setCloseBehavior`
- Produces: `window.electronAPI.getCloseBehavior()` / `window.electronAPI.setCloseBehavior(v)`（preload 风格沿用 electronAPI，不新建 API 对象）

- [ ] **Step 1: main.js 加 2 个 IPC handler**

在 `main.js:339` 附近的 `ipcMain.handle('window-close', ...)` 之后加：

```js
ipcMain.handle('close-behavior:get', () => {
    return getCloseBehavior();
});

ipcMain.handle('close-behavior:set', (event, value) => {
    if (['ask', 'minimize', 'quit'].includes(value)) {
        setCloseBehavior(value);
        return true;
    }
    return false;
});
```

- [ ] **Step 2: preload.js 暴露 closeBehaviorAPI**

在 `preload.js` 的 `electronAPI` 对象里（`openLogDir` 之前或之后，约 95 行）加：

```js
    /**
     * 关闭按钮行为（ask/minimize/quit）
     */
    getCloseBehavior: () => ipcRenderer.invoke('close-behavior:get'),
    setCloseBehavior: (v) => ipcRenderer.invoke('close-behavior:set', v),
```

- [ ] **Step 3: 验证 IPC 通**

```bash
cd frontend && npm run dev
```
启动后，在渲染进程 DevTools Console 执行：
```js
await window.electronAPI.getCloseBehavior()
```
Expected: 返回 `'ask'`（首次）或之前记住的值。
```js
await window.electronAPI.setCloseBehavior('minimize')
```
Expected: 返回 `true`。再 `getCloseBehavior()` 应返回 `'minimize'`。

- [ ] **Step 4: Commit**

```bash
git add frontend/main.js frontend/preload.js
git commit -m "feat: 暴露 closeBehavior 读写 IPC（供设置页使用）"
```

---

## Task 4: 设置页 UI（关闭按钮行为选项）

**Files:**
- Modify: `frontend/src/renderer/index.html`（设置页加 select）
- Modify: `frontend/src/renderer/app.js`（加载/保存逻辑）

**Interfaces:**
- Consumes: Task 3 的 `window.electronAPI.getCloseBehavior`/`setCloseBehavior`

- [ ] **Step 1: 在 index.html 设置页加一个 settings-group**

定位 `app.js:1505` 对应的 HTML（"后端服务" group 之前），插入新 group。先在 `index.html` 里找到"环境变量"或"后端服务" group 的位置（搜索 `后端服务` 或 `restartWorkerBtn`），在它**之前**插入：

```html
            <div class="settings-group">
                <div class="settings-group-title">关闭按钮行为</div>
                <div style="font-size:11px;color:var(--text-tertiary);margin-bottom:8px">点窗口关闭按钮（×）时的行为。选"每次询问"会重新弹出选择对话框。</div>
                <select id="closeBehaviorSelect" class="settings-input" style="width:100%;padding:6px;background:var(--bg-secondary);color:var(--text-primary);border:1px solid var(--border);border-radius:4px">
                    <option value="ask">每次询问</option>
                    <option value="minimize">最小化到托盘</option>
                    <option value="quit">退出程序</option>
                </select>
            </div>
```

- [ ] **Step 2: 在 app.js 找设置页初始化位置**

搜索 `app.js` 里设置页打开时的初始化逻辑（如加载环境变量、加载通道列表的地方，约 1659 行 `_renderEnvVars` 附近，或设置页 show 的回调）。找到设置页**显示/渲染时**会调用的函数（通常是某个 `_renderSettings` 或设置页 tab 切换回调）。

在该初始化函数里加（如果没有明确入口，加一个新函数 `_initCloseBehavior` 并在设置页打开时调用）：

```js
async function _initCloseBehavior() {
    const select = document.getElementById('closeBehaviorSelect');
    if (!select) return;
    try {
        const behavior = await window.electronAPI.getCloseBehavior();
        select.value = behavior || 'ask';
    } catch (e) {
        console.warn('getCloseBehavior failed', e);
    }
    select.addEventListener('change', async () => {
        try {
            await window.electronAPI.setCloseBehavior(select.value);
            showToast('关闭行为已更新', 'success');
        } catch (e) {
            showToast('保存失败: ' + e.message, 'error');
        }
    });
}
```

注意：每次调用 `_initCloseBehavior` 都 addEventListener 会重复绑定。**改为只绑一次**（用标志或检查 `select.dataset.bound`）：

```js
async function _initCloseBehavior() {
    const select = document.getElementById('closeBehaviorSelect');
    if (!select) return;
    try {
        const behavior = await window.electronAPI.getCloseBehavior();
        select.value = behavior || 'ask';
    } catch (e) {
        console.warn('getCloseBehavior failed', e);
    }
    if (!select.dataset.bound) {
        select.addEventListener('change', async () => {
            try {
                await window.electronAPI.setCloseBehavior(select.value);
                showToast('关闭行为已更新', 'success');
            } catch (e) {
                showToast('保存失败: ' + e.message, 'error');
            }
        });
        select.dataset.bound = '1';
    }
}
```

- [ ] **Step 3: 在设置页打开的入口调用 `_initCloseBehavior`**

找设置页打开的入口（搜索现有 `_renderEnvVars`、`_loadEnvVars` 或设置 nav 的 click handler，在它们旁边调 `_initCloseBehavior()`）。如果找不到明确入口，在渲染设置页 HTML 之后立即调用。

- [ ] **Step 4: cache busting**

`index.html` 末尾给 `app.js?v=N` 和 `main.css?v=N` 的 N 加 1。

- [ ] **Step 5: 验证**

```bash
cd frontend && npm run dev
```
Expected: 打开设置页，看到"关闭按钮行为"下拉框。改选"最小化到托盘"→ 提示"关闭行为已更新"。点 × → 直接最小化（不弹窗）。回设置页改"每次询问"→ 下次点 × 重新弹窗。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/renderer/index.html frontend/src/renderer/app.js
git commit -m "feat: 设置页加'关闭按钮行为'选项"
```

---

## Task 5: 端到端验证 + 生产打包验证

**Files:** 无新改动，纯验证。

- [ ] **Step 1: 开发模式端到端走查**

`npm run dev`，按顺序验证：
1. 首次点 × → 弹窗，选"最小化"+勾记住 → 窗口隐藏，托盘在。
2. 托盘单击 → 窗口恢复。
3. 再点 × → 直接最小化（记住了，不弹窗）。
4. 托盘右键"重置关闭行为" → 提示框。
5. 点 × → 重新弹窗，选"退出"+勾记住 → 应用退出。
6. 重新启动，点 × → 直接退出（记住了 quit）。
7. 设置页改"最小化" → 点 × → 最小化。
8. **后端存活验证**：最小化后，从飞书发消息或等定时任务触发，确认 worker 还在工作（看 `logs/worker.log`）。

- [ ] **Step 2: 生产打包**

```bash
cd frontend && npm run build:win
```
Expected: 打包成功（注意之前 file locked 问题，先删旧 exe）。

- [ ] **Step 3: 生产安装验证**

装一次，重复 Step 1 的验证项。重点确认：
- 托盘图标正常显示（`path.join(__dirname, 'icon.png')` 在 asar 内可用）。
- config.json 写到 `%APPDATA%/alphawise/config.json`。

- [ ] **Step 4: Commit（如有验证发现的小修）**

```bash
git add -A
git commit -m "fix: 端到端验证修复"
```

---

## Self-Review 已完成

- ✅ Spec 覆盖：close 拦截（T1）、Tray（T2）、IPC+preload（T3）、设置页 UI（T4）、端到端（T5）—— spec 所有需求映射到任务。
- ✅ 无占位符。
- ✅ 类型/命名一致：`getCloseBehavior`/`setCloseBehavior`/`isQuitting`/`closeBehaviorAPI` 全程一致。
