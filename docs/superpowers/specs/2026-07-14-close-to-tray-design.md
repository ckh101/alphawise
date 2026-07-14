# 窗口关闭行为与托盘常驻 — 设计文档

## 背景

当前应用窗口关闭按钮（×）直接退出整个应用（`main.js` 的 `window-all-closed` → `app.quit()`）。用户希望支持"最小化到托盘常驻"，让飞书通道、定时任务在窗口关闭后继续运行。

## 需求

1. 点 × 按钮：弹一次对话框，让用户选「最小化到托盘」/「退出程序」，带"记住选择"勾选。
2. 记住选择后，按记忆行为走（不再弹窗）。
3. 用户可随时更改记忆的行为：托盘右键菜单 + 设置页 都能改。
4. 最小化到托盘 = 窗口隐藏，**Node 后端 + Python Worker 继续运行**（飞书通道、定时任务正常工作）。
5. 托盘右键菜单：显示窗口 / 退出程序 / 重置关闭行为（恢复每次询问）。
6. 托盘图标用现有应用图标。

## 非目标（YAGNI）

- 不做托盘消息通知/红点/闪烁。
- 不做开机自启。
- 不为托盘单独做专用小图标。
- macOS 行为不在本次范围（项目目标是 Windows 生产，现有 `window-all-closed` 已对 macOS 做了保留处理，本次不改 macOS 分支）。

## 架构

```
点 × 按钮
  │
  ▼
mainWindow.on('close', e)        ← 拦截，e.preventDefault()
  │
  ▼
读 closeBehavior (ask | minimize | quit)
  ├─ ask       → 弹 dialog（带"记住选择"复选）
  │              ├─ 用户选「最小化」 → hide 窗口（后端继续）
  │              └─ 用户选「退出」   → 设 isQuitting=true → app.quit()
  ├─ minimize  → hide 窗口
  └─ quit      → 设 isQuitting=true → app.quit()

托盘双击        → 显示窗口
托盘右键菜单    → 显示窗口 / 退出 / 重置关闭行为
设置页          → 下拉选「每次询问/最小化/退出」→ IPC 写 config
```

## 组件

### 1. Tray（main.js 新增）
- 图标：`path.join(__dirname, 'icon.png')`（与窗口图标一致的定位方式，开发生产通用）。
- Windows 上用 `.png` 即可（Electron 28 支持）。
- 右键菜单（`Menu.buildFromTemplate`）：
  - 「显示窗口」→ `mainWindow.show() + focus()`
  - 「重置关闭行为」→ 写 `closeBehavior='ask'`
  - 分隔线
  - 「退出」→ 设 `isQuitting=true` → `app.quit()`
- 双击托盘 → 显示窗口（`tray.on('click', ...)` 或 `'double-click'`，Windows 用单击更自然）。
- tooltip：「灵智投研助手」。
- 生命周期：`app.whenReady()` 创建；`app.before-quit` 时 `tray.destroy()`。

### 2. 关闭行为配置（独立 config.json）
- 路径：`path.join(app.getPath('userData'), 'config.json')`，即 `C:\Users\<user>\AppData\Roaming\alphawise\config.json`。
- 与业务 db 解耦，属 Electron 层偏好（Electron 的缓存也在这个目录，config.json 与之并列）。
- 结构：`{ "closeBehavior": "ask" | "minimize" | "quit" }`。
- 默认值：`"ask"`（首次安装后点 × 会弹窗）。
- 读写：主进程封装 `getCloseBehavior()` / `setCloseBehavior(v)`，同步 fs（参考 db.js 风格）。
- 注意：此文件在 C 盘 userData 目录，不随重装丢失（卸载/重装不删 AppData，除非用户主动清）。

### 3. 窗口 close 拦截（改 main.js）
- 新增 `let isQuitting = false` 标志。
- `mainWindow.on('close', (e) => {...})`：
  - 若 `isQuitting` 为 true → 放行（真正退出）。
  - 否则 `e.preventDefault()`，按 `closeBehavior` 分支。
- 弹窗用 `dialog.showMessageBoxSync`（同步，带 buttons）：
  - buttons: `['最小化到托盘', '退出程序']`
  - checkbox label: `「记住选择，以后不再询问」`
  - 返回：`{response, checkboxChecked}`。
  - 用户选最小化 → 若勾记住则写 `closeBehavior='minimize'` → `mainWindow.hide()`。
  - 用户选退出 → 若勾记住则写 `closeBehavior='quit'` → 设 `isQuitting=true` → `mainWindow.close()`（这次会放行）。

### 4. window-all-closed 改造（改 main.js）
- 现状：直接 `stopBackend() + app.quit()`。
- 改为：**有托盘时不再触发 quit**（窗口 hide 不触发 window-all-closed；只有真 close 才触发，而真 close 已被 close 拦截放行 → 走到 app.quit）。
- 实际上最小化是 `hide()`，不触发 `window-all-closed`；真退出走 `app.quit()`，`window-all-closed` 的 `app.quit()` 冗余但无害。保留现有逻辑兜底，不破坏 macOS 分支。

### 5. before-quit（改 main.js）
- 现状：`app.on('before-quit', () => stopBackend())`。
- 不变——真退出时停后端。最小化到托盘不走这里（不 quit）。

### 6. preload.js（新增 IPC 暴露）
- `window.closeBehaviorAPI = { get(), set(v) }`。
- `get` → `ipcRenderer.invoke('close-behavior:get')`。
- `set` → `ipcRenderer.invoke('close-behavior:set', v)`。

### 7. 设置页 UI（前端）
- `index.html`：设置页加一行「关闭按钮行为」+ `<select>` 三选项（每次询问/最小化到托盘/退出程序）。
- `app.js`：加载设置页时读 `closeBehaviorAPI.get()` 填充 select；change 时调 `closeBehaviorAPI.set(v)`。
- `api.js`：不经过 9998 后端，直接用 preload 暴露的 API。

## 数据流

```
关闭按钮 ──close事件──▶ 读 config.json ──▶ hide / quit / 弹窗
                                          │
设置页 select ──IPC──▶ 主进程写 config.json
                                          │
托盘「重置」 ──▶ 主进程写 closeBehavior='ask'
                                          │
托盘「退出」 ──▶ isQuitting=true ──▶ app.quit() ──▶ before-quit stopBackend()
```

## 错误处理

- config.json 读取失败/不存在 → 用默认值 `'ask'`，不报错。
- config.json 写入失败 → console.error，不阻断操作（关闭行为降级为本次询问）。
- Tray 创建失败（如图标缺失）→ catch 并 console.error，不阻断应用启动（关闭行为仍可用 quit 分支）。

## 影响面（精准评估）

| 文件 | 改动 |
|---|---|
| `frontend/main.js` | 加 Tray、close 拦截、config 读写、2 个 IPC handler、`isQuitting` 标志 |
| `frontend/preload.js` | 暴露 `closeBehaviorAPI` |
| `frontend/src/renderer/index.html` | 设置页加「关闭按钮行为」select |
| `frontend/src/renderer/app.js` | 设置页加载/保存 closeBehavior |
| **不改** | 后端、Python、worker、db、server/ |

## 测试 / 验证标准

1. 首次点 × → 弹窗，选「最小化」+ 勾记住 → 窗口隐藏，托盘出现，后端继续（飞书消息能收、定时任务能跑）。
2. 再次点 ×（从托盘恢复后）→ 直接最小化（不弹窗）。
3. 托盘右键「退出」→ 应用完全退出，后端停止。
4. 托盘右键「重置关闭行为」→ 下次点 × 重新弹窗。
5. 设置页改「退出程序」→ 点 × 直接退出。
6. 开发模式 + 生产打包都验证。

## 风险

- **Tray 图标路径在生产失效**：沿用 `path.join(__dirname, 'icon.png')`（与现有窗口图标一致，已验证生产可用），避免重蹈 `process.resourcesPath` 的坑。
- **Electron 28 的 `dialog.showMessageBoxSync` 带 checkbox**：API 支持，需确认返回值结构 `{response, checkboxChecked}`。
- **窗口 hide 后后端是否真继续**：`stopBackend()` 只在 `before-quit`/`window-all-closed(真退出)` 调，hide 不调，后端继续。需实测确认 Worker 进程存活。
