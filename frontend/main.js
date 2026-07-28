/**
 * Electron 主进程入口
 *
 * 负责创建窗口、管理应用生命周期、处理IPC通信
 * Node.js 后端（Fastify）随 Electron 启动，Python Worker 按需启动
 */

const { app, BrowserWindow, ipcMain, Menu, dialog, Tray, nativeImage } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
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

// EPIPE: broken pipe — 在无终端或父进程退出时静默忽略
process.on('uncaughtException', (err) => { if (err.code === 'EPIPE') return; throw err; });

const isDev = process.env.NODE_ENV === 'development' || !app.isPackaged;

// 日志目录：开发模式 → project/logs/，生产模式 → 安装目录 resources/logs/
// （避免写到 %APPDATA% 用户目录导致系统盘爆满；安装目录可写，因 perMachine:false 默认装 %LOCALAPPDATA%）
const logDir = isDev
  ? path.join(__dirname, '..', 'logs')
  : path.join(process.resourcesPath, 'logs');
if (!fs.existsSync(logDir)) fs.mkdirSync(logDir, { recursive: true });

// 在 console 被 override 前保留原始引用
const _origLog = console.log.bind(console);
const _origError = console.error.bind(console);

// 启动时清理超过 7 天的日志文件（electron.log/server.log/worker.log 等无内置 rotation 的日志）
const LOG_RETENTION_DAYS = 7;
function _cleanupOldLogs() {
  try {
    const now = Date.now();
    for (const name of fs.readdirSync(logDir)) {
      // 仅清理日志类文件（.log / loguru 压缩归档 .zip），保留 worker.pid 等
      if (!name.endsWith('.log') && !name.endsWith('.zip')) continue;
      const f = path.join(logDir, name);
      const st = fs.statSync(f);
      if (st.isFile() && (now - st.mtimeMs) > LOG_RETENTION_DAYS * 24 * 3600 * 1000) {
        fs.unlinkSync(f);
        _origLog(`[log-cleanup] removed ${name} (older than ${LOG_RETENTION_DAYS} days)`);
      }
    }
  } catch (e) {
    _origLog(`[log-cleanup] error: ${e && e.message}`);
  }
}
_cleanupOldLogs();

const electronLogPath = path.join(logDir, 'electron.log');
const errorsLogPath = path.join(logDir, 'errors.log');
const _logStream = fs.createWriteStream(electronLogPath, { flags: 'a' });
const _errStream = fs.createWriteStream(errorsLogPath, { flags: 'a' });

function _formatLog(level, ...args) {
  const ts = new Date().toISOString().replace('T', ' ').slice(0, 23);
  const msg = args.map(a => typeof a === 'object' ? JSON.stringify(a) : String(a)).join(' ');
  return `[${ts}] [${level}] [electron] ${msg}\n`;
}

console.log = (...args) => {
  // 检测内容中的实际级别标记
  const firstArg = String(args[0] || '');
  let level = 'INFO';
  if (firstArg.includes(':WARN]') || /\|\s*WARNING\s*\|/.test(firstArg)) level = 'WARN';
  _logStream.write(_formatLog(level, ...args));
  _origLog(...args);
};

console.error = (...args) => {
  const line = _formatLog('ERROR', ...args);
  _logStream.write(line);
  _errStream.write(line);
  _origError(...args);
};

let mainWindow = null;
let serverProcess = null;
let isQuitting = false;
let tray = null;

/**
 * 启动 Node.js 后端（作为子进程，避免 Electron Node.js 版本兼容问题）
 */
async function startBackend() {
    // 生产模式用打包进去的独立 node（与开发环境 node v22 一致），
    // 避免依赖 Electron 自带 node 18.18 跑不动新依赖（Fastify 5 需 diagnostics.tracingChannel）
    //   Win: backend/node/node.exe  |  Mac: backend/node/bin/node
    const nodeExe = isDev
        ? 'node'
        : (process.platform === 'win32'
            ? path.join(process.resourcesPath, 'backend', 'node', 'node.exe')
            : path.join(process.resourcesPath, 'backend', 'node', 'bin', 'node'));
    // 生产模式 server 被 asarUnpack 到 app.asar.unpacked/server/，独立 node.exe 不懂 asar，
    // 必须指向真实文件系统路径（app.asar/server 不存在）
    const serverScript = isDev
        ? path.join(__dirname, 'server', 'index.js')
        : path.join(process.resourcesPath, 'app.asar.unpacked', 'server', 'index.js');

    console.log('[backend] Starting Node.js backend:', nodeExe, serverScript);

    serverProcess = spawn(nodeExe, [serverScript], {
        cwd: isDev ? __dirname : process.resourcesPath,
        // 独立 node.exe 无 process.resourcesPath（仅 Electron 主进程有），
        // 显式注入，供 server/index.js 与 db.js 解析 backend 路径
        env: { ...process.env, NODE_ENV: isDev ? 'development' : 'production', ELECTRON: 'true', HARNESS_LOG_DIR: logDir, ELECTRON_RESOURCES_PATH: process.resourcesPath },
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true,
    });

    serverProcess.stdout.on('data', (data) => {
        console.log('[backend:stdout]', data.toString().trim());
    });

    serverProcess.stderr.on('data', (data) => {
        // 后端子进程的 stderr 不一定是真错误 —— Python loguru 默认写 stderr，包含 INFO/DEBUG/WARNING/ERROR
        // 按内容中的实际级别判断
        const text = data.toString().trim();
        if (!text) return;
        // 包含真实 ERROR 标记才用 console.error，其他按 INFO 处理
        if (/\|\s*ERROR\s*\||\bTraceback\b|\bException\b|\bError:/.test(text)) {
            console.error('[backend:stderr]', text);
        } else if (/\|\s*WARNING\s*\|/.test(text)) {
            console.log('[backend:stderr:WARN]', text);
        } else {
            console.log('[backend:stderr]', text);
        }
    });

    serverProcess.on('exit', (code) => {
        console.log('[backend] Process exited with code:', code);
        serverProcess = null;
    });
}

/**
 * 停止后端
 */
async function stopBackend() {
    if (serverProcess) {
        serverProcess.kill();
        serverProcess = null;
    }
}

/**
 * 创建应用窗口
 */
function createWindow() {
    console.log('[main] Creating window');

    mainWindow = new BrowserWindow({
        width: 960,
        height: 600,
        minWidth: 768,
        minHeight: 480,
        center: true,
        frame: false,
        icon: path.join(__dirname, 'icon.png'),
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            nodeIntegration: false,
            contextIsolation: true,
            webSecurity: false,  // 允许跨域请求（开发环境）
        },
        show: false, // 延迟显示，等待窗口加载完成
    });

    // 加载主页面
    mainWindow.loadFile(path.join(__dirname, 'src', 'renderer', 'index.html'));

    // 绑定窗口状态事件
    bindWindowStateEvents();

    // 开发模式键盘快捷键（frameless 窗口下 Menu 快捷键不生效，用 webContents 拦截）
    if (isDev) {
        mainWindow.webContents.on('before-input-event', (event, input) => {
            if (input.control && input.key.toLowerCase() === 'r') {
                mainWindow.webContents.reload();
                event.preventDefault();
            } else if (input.control && input.shift && input.key.toLowerCase() === 'i') {
                mainWindow.webContents.toggleDevTools();
                event.preventDefault();
            }
        });
    }

    // 窗口准备好后显示
    mainWindow.once('ready-to-show', () => {
        console.log('[main] Window ready to show');
        mainWindow.show();

        // 保留 DevTools 快捷键但隐藏菜单栏
        if (process.env.NODE_ENV === 'development') {
            const devMenu = Menu.buildFromTemplate([
                { label: 'Dev', submenu: [
                    { role: 'toggleDevTools', accelerator: 'Ctrl+Shift+I' },
                    { role: 'reload', accelerator: 'Ctrl+R' },
                ]}
            ]);
            Menu.setApplicationMenu(devMenu);
        } else {
            Menu.setApplicationMenu(null);
        }

        // 开发模式打开开发者工具
        if (process.env.NODE_ENV === 'development') {
            mainWindow.webContents.openDevTools();
        }
    });

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
        // ask：弹窗（必须用异步版 showMessageBox，同步版返回 number 拿不到 checkbox 状态）
        e.preventDefault();
        dialog.showMessageBox(mainWindow, {
            type: 'question',
            title: '关闭窗口',
            message: '关闭后希望怎么做？',
            buttons: ['最小化到托盘', '退出程序'],
            checkboxLabel: '记住选择，以后不再询问',
            checkboxChecked: false,
            defaultId: 0,
        }).then((choice) => {
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
    });

    // 窗口关闭时
    mainWindow.on('closed', () => {
        console.log('[main] Window closed');
        mainWindow = null;
    });

    console.log('[main] Window created');
}

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

/**
 * 轮询后端 + Worker 健康检查，直到就绪
 */
async function waitForServices(maxRetries = 60, intervalMs = 1000) {
    const http = require('http');

    function httpGet(url) {
        return new Promise((resolve, reject) => {
            const req = http.get(url, (res) => {
                let body = '';
                res.on('data', (chunk) => body += chunk);
                res.on('end', () => {
                    if (res.statusCode === 200) resolve(body);
                    else reject(new Error(`Status ${res.statusCode}`));
                });
            });
            req.on('error', reject);
            req.setTimeout(2000, () => { req.destroy(); reject(new Error('timeout')); });
        });
    }

    // Phase 1: 等待 Node.js 后端就绪
    for (let i = 0; i < maxRetries; i++) {
        try {
            await httpGet('http://127.0.0.1:9998/health');
            console.log('[main] Node.js backend is ready');
            break;
        } catch {
            if (i === 0) console.log('[main] Waiting for Node.js backend...');
            await new Promise(r => setTimeout(r, intervalMs));
        }
        if (i === maxRetries - 1) {
            console.error('[main] Node.js backend failed to start within timeout');
            return false;
        }
    }

    // Phase 2: 确保 Python Worker 功能就绪
    // Node.js 后端会自动通过 ensureReady 启动/复用 Worker
    // main.js 只轮询 Worker 的 /ready 端点（Agent 初始化完成后才 ok）
    for (let i = 0; i < 90; i++) {
        try {
            const resp = await httpGet('http://127.0.0.1:9999/ready');
            const data = JSON.parse(resp);
            if (data && data.status === 'ok') {
                console.log('[main] Python Worker is ready');
                return true;
            }
        } catch { /* Worker 端口还没起来，继续等 */ }
        if (i === 0) console.log('[main] Waiting for Python Worker...');
        await new Promise(r => setTimeout(r, 2000));
    }
    console.error('[main] Worker failed to become ready within timeout');
    return false;
}

/**
 * 应用就绪时启动后端并创建窗口
 */
app.on('ready', async () => {
    console.log('[main] App ready, mode:', isDev ? 'development' : 'production');

    createWindow();
    try {
        createTray();
    } catch (e) {
        console.error('[main] createTray failed, tray disabled:', e.message);
    }

    // 异步启动后端，前端通过 IPC 通知 ready
    startBackend().then(() => waitForServices()).then((ready) => {
        if (ready && mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send('backend-ready');
        }
    });
});

/**
 * 所有窗口关闭时退出应用（macOS除外）
 */
app.on('window-all-closed', () => {
    console.log('[main] All windows closed');

    // macOS: 即使所有窗口关闭也保持应用运行
    if (process.platform !== 'darwin') {
        stopBackend();
        app.quit();
    }
});

/**
 * macOS: 点击Dock图标时重新创建窗口
 */
app.on('activate', () => {
    console.log('[main] App activate');

    if (BrowserWindow.getAllWindows().length === 0) {
        createWindow();
    }
});

/**
 * 应用退出前清理后端进程
 */
app.on('before-quit', () => {
    stopBackend();
    if (tray) {
        tray.destroy();
        tray = null;
    }
});

/**
 * IPC 处理器：窗口控制
 */
ipcMain.handle('window-minimize', () => {
    if (mainWindow) mainWindow.minimize();
});

ipcMain.handle('window-maximize', () => {
    if (mainWindow) {
        if (mainWindow.isMaximized()) {
            mainWindow.unmaximize();
        } else {
            mainWindow.maximize();
        }
    }
});

ipcMain.handle('window-close', () => {
    if (mainWindow) mainWindow.close();
});

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

ipcMain.handle('window-is-maximized', () => {
    return mainWindow ? mainWindow.isMaximized() : false;
});

ipcMain.handle('open-log-dir', () => {
    const { shell } = require('electron');
    shell.openPath(logDir);
});

// 窗口状态变化时通知渲染进程
function bindWindowStateEvents() {
    if (!mainWindow) return;
    mainWindow.on('maximize', () => {
        mainWindow.webContents.send('window-state-changed', 'maximized');
    });
    mainWindow.on('unmaximize', () => {
        mainWindow.webContents.send('window-state-changed', 'normal');
    });
}

/**
 * IPC 处理器：ping
 */
ipcMain.handle('ping', async () => {
    console.log('[ipc] Received ping');
    const startTime = Date.now();
    const response = { message: 'pong', timestamp: Date.now() };
    const duration = Date.now() - startTime;
    console.log(`[ipc] Sent pong in ${duration}ms`);
    return response;
});

/**
 * IPC 处理器：获取应用信息
 */
ipcMain.handle('get-app-info', async () => {
    console.log('[ipc] Received get-app-info');

    return {
        name: app.getName(),
        version: app.getVersion(),
        electronVersion: process.versions.electron,
        platform: process.platform,
        arch: process.arch,
    };
});

/**
 * IPC 处理器：确认弹窗（自定义标题）
 */
ipcMain.handle('dialog-confirm', async (event, message) => {
    if (!mainWindow) return false;
    const result = await dialog.showMessageBox(mainWindow, {
        type: 'question',
        title: '灵智投研助手',
        message: message,
        buttons: ['取消', '确定'],
        defaultId: 1,
        cancelId: 0,
        noLink: true,
    });
    return result.response === 1;
});

/**
 * IPC 处理器：选择文件（用于 skill zip 上传）
 */
ipcMain.handle('select-file', async (event, options = {}) => {
    if (!mainWindow) return null;
    const result = await dialog.showOpenDialog(mainWindow, {
        properties: ['openFile'],
        filters: options.filters || [{ name: 'ZIP', extensions: ['zip'] }],
        title: options.title || '选择文件',
    });
    if (result.canceled || result.filePaths.length === 0) return null;
    return result.filePaths[0];
});

/**
 * IPC 处理器：读取文件为 Base64（用于上传到后端）
 */
ipcMain.handle('read-file-base64', async (event, filePath) => {
    const fs = require('fs');
    if (!fs.existsSync(filePath)) return null;
    const buffer = fs.readFileSync(filePath);
    return buffer.toString('base64');
});

/**
 * IPC 处理器：生成 PDF（Chromium 渲染引擎）
 */
ipcMain.handle('generate-pdf', async (event, html) => {
    const { BrowserWindow } = require('electron');

    return new Promise((resolve, reject) => {
        const pdfWindow = new BrowserWindow({
            width: 800,
            height: 600,
            show: false,
            webPreferences: {
                nodeIntegration: false,
                contextIsolation: true,
            },
        });

        // 用 data URL 加载 HTML
        const encodedHtml = encodeURIComponent(html);
        pdfWindow.loadURL(`data:text/html;charset=utf-8,${encodedHtml}`);

        pdfWindow.webContents.on('did-finish-load', async () => {
            try {
                const pdfData = await pdfWindow.webContents.printToPDF({
                    printBackground: true,
                    preferCSSPageSize: true,
                    marginsType: 1, // 使用默认边距
                });
                pdfWindow.close();
                resolve(pdfData.toString('base64'));
            } catch (err) {
                pdfWindow.close();
                reject(err);
            }
        });

        pdfWindow.webContents.on('did-fail-load', (event, errorCode) => {
            pdfWindow.close();
            reject(new Error(`PDF window failed to load: ${errorCode}`));
        });

        // 超时保护
        setTimeout(() => {
            if (!pdfWindow.isDestroyed()) {
                pdfWindow.close();
                reject(new Error('PDF generation timed out'));
            }
        }, 30000);
    });
});

console.log('[main] Main process loaded');
