/**
 * Python Worker 进程管理
 *
 * 管理后台 Python Worker 进程的生命周期：
 * - 开发模式：使用系统 python
 * - 生产模式：使用嵌入式 python
 * - 崩溃自动重启
 * - 按需启动（首次请求时）
 * - 启动时从 DB 注入自定义环境变量
 */

const { spawn, execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const http = require('http');
const WorkerClient = require('./workerClient');

const WORKER_PORT = 9999;

/**
 * 检测端口 :9999 被哪个 PID 占用，返回 PID 或 null
 */
function _findPidOnPort(port) {
  try {
    if (process.platform === 'win32') {
      // netstat -ano | findstr :9999
      const out = execSync(`netstat -ano -p TCP`, { encoding: 'utf8', windowsHide: true });
      const lines = out.split('\n').filter(l => l.includes(`:${port}`) && l.includes('LISTENING'));
      for (const line of lines) {
        const parts = line.trim().split(/\s+/);
        const pid = parts[parts.length - 1];
        if (pid && /^\d+$/.test(pid)) return pid;
      }
      return null;
    } else {
      // lsof -ti :9999 或 ss -ltnp
      try {
        const out = execSync(`lsof -ti :${port}`, { encoding: 'utf8' }).trim();
        if (out) return out.split('\n')[0];
      } catch {}
      try {
        const out = execSync(`ss -ltnp 'sport = :${port}'`, { encoding: 'utf8' });
        const m = out.match(/pid=(\d+)/);
        if (m) return m[1];
      } catch {}
      return null;
    }
  } catch {
    return null;
  }
}

/**
 * 判断 PID 是否是 python 进程（避免误杀）
 */
function _isPythonProcess(pid) {
  if (!pid) return false;
  try {
    if (process.platform === 'win32') {
      const out = execSync(`tasklist /FI "PID eq ${pid}" /FO CSV /NH`, { encoding: 'utf8', windowsHide: true });
      return /python|pythonw/i.test(out);
    } else {
      const out = execSync(`ps -p ${pid} -o comm=`, { encoding: 'utf8' }).trim();
      return /python/i.test(out);
    }
  } catch {
    return false;
  }
}

/**
 * 强制杀进程
 */
function _killPid(pid) {
  if (!pid) return;
  try {
    if (process.platform === 'win32') {
      execSync(`taskkill /F /PID ${pid}`, { stdio: 'ignore', windowsHide: true });
    } else {
      process.kill(parseInt(pid, 10), 'SIGKILL');
    }
  } catch {}
}

/**
 * 等待端口释放，最多 timeoutMs
 */
async function _waitForPortFree(port, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!_findPidOnPort(port)) return true;
    await new Promise(r => setTimeout(r, 200));
  }
  return !_findPidOnPort(port);
}

const isDev = process.env.NODE_ENV === 'development';

// Worker 日志文件
const logDir = process.env.HARNESS_LOG_DIR
  || path.join(__dirname, '..', '..', '..', 'logs');
if (!fs.existsSync(logDir)) fs.mkdirSync(logDir, { recursive: true });
const _workerLogPath = path.join(logDir, 'worker.log');

function _writeWorkerLog(prefix, text) {
  const ts = new Date().toISOString().replace('T', ' ').slice(0, 23);
  try { fs.appendFileSync(_workerLogPath, `[${ts}] [${prefix}] ${text}\n`); } catch {}
}

class PythonWorkerManager {
  constructor() {
    this._process = null;
    this._client = new WorkerClient();
    this._starting = false;
    this._restartCount = 0;
    this._maxRestarts = 5;
    this._crashed = false;
    this._channelsStarted = false;
    this._ensurePromise = null;
    this._restartPromise = null;
  }

  get isRunning() {
    return this._process != null && !this._process.killed;
  }

  /**
   * 从 DB 加载自定义环境变量
   */
  _getCustomEnv() {
    try {
      const db = require('./db');
      return db.getEnvVars();
    } catch {
      return {};
    }
  }

  /**
   * 启动 Worker 进程
   */
  async start() {
    if (this.isRunning || this._starting) return;
    // 端口已被健康 Worker 占用则直接复用，避免并发 spawn 导致 bind 失败重启风暴
    const portPid = _findPidOnPort(WORKER_PORT);
    if (portPid && await this._client.healthCheck()) {
      console.log(`[worker] Port ${WORKER_PORT} already served by PID ${portPid}, reuse`);
      return;
    }
    this._starting = true;

    const backendDir = isDev
      ? path.join(__dirname, '..', '..', '..', 'backend')
      : path.join(process.env.ELECTRON_RESOURCES_PATH, 'backend');

    // 生产模式 Python 解释器路径按平台区分：
    //   Win: 使用 pythonw.exe（无窗口版本），避免 SDK 子进程弹出控制台
    //   Mac: framework + --target site-packages，需 PYTHONPATH 指向
    // dev 模式：优先 .venv，否则系统 python（逻辑不变）
    let pythonPath;
    if (isDev) {
      pythonPath = fs.existsSync(path.join(backendDir, '.venv', 'Scripts', 'python.exe'))
        ? path.join(backendDir, '.venv', 'Scripts', 'python.exe')
        : 'python';
    } else if (process.platform === 'win32') {
      // 使用 pythonw.exe（无窗口版本），避免 SDK 子进程弹出控制台
      pythonPath = path.join(backendDir, 'python', 'pythonw.exe');
    } else {
      // macOS: framework 的 python 软链
      pythonPath = path.join(backendDir, 'python', 'bin', 'python');
    }
    const workerScript = path.join(backendDir, 'worker_main.py');

    // 把 python 目录加到 PATH 前面，确保 SDK Agent 的 shell 命令用正确的 python
    //   dev: .venv/Scripts  |  Win 生产: python/  |  Mac 生产: python/bin
    const venvBin = isDev
      ? path.join(backendDir, '.venv', 'Scripts')
      : (process.platform === 'win32'
          ? path.join(backendDir, 'python')
          : path.join(backendDir, 'python', 'bin'));
    const envPath = venvBin + path.delimiter + (process.env.PATH || '');

    // macOS 生产：--target 装的 site-packages 在 python/lib/python3.14/site-packages，
    // 需 PYTHONPATH 指向（framework 自身的 site-packages 为空）
    const prodEnv = {};
    if (!isDev && process.platform !== 'win32') {
      // Python 版本子目录：python3.x（从 sys.version 不可靠，直接按目录查找）
      const libDir = path.join(backendDir, 'python', 'lib');
      try {
        const versions = fs.readdirSync(libDir).filter(d => d.startsWith('python'));
        if (versions.length > 0) {
          prodEnv.PYTHONPATH = path.join(libDir, versions[0], 'site-packages');
        }
      } catch { /* 目录不存在则忽略，fallback 到 framework 默认 */ }
    }

    // 从 DB 加载自定义环境变量
    const customEnv = this._getCustomEnv();

    console.log('[worker] Starting Python worker:', pythonPath, workerScript);
    if (prodEnv.PYTHONPATH) {
      console.log('[worker] PYTHONPATH:', prodEnv.PYTHONPATH);
    }
    if (Object.keys(customEnv).length > 0) {
      console.log('[worker] Custom env vars:', Object.keys(customEnv).join(', '));
    }

    this._process = spawn(pythonPath, [workerScript], {
      cwd: backendDir,
      env: {
        ...process.env,
        PATH: envPath,
        PYTHONUNBUFFERED: '1',
        PYTHONIOENCODING: 'utf-8',
        PYTHONUTF8: '1',
        ...prodEnv,
        ...customEnv,
      },
      stdio: ['ignore', 'pipe', 'pipe'],
      // detached + unref：让 Worker 脱离 Node.js 父进程的进程树
      // Electron 关闭时 Node.js 父进程被杀，Worker 保持运行（持久化）
      // 后续 Electron 重开时 ensureReady 的 healthCheck 探测复用
      windowsHide: true,
      detached: true,
    });

    // unref：不让 Worker 进程阻止 Node.js 父进程退出
    this._process.unref();

    this._process.stdout.on('data', (data) => {
      const text = data.toString().trim();
      if (!text) return;
      console.log('[worker:stdout]', text);
      _writeWorkerLog('stdout', text);
    });

    this._process.stderr.on('data', (data) => {
      const text = data.toString().trim();
      if (!text) return;
      console.error('[worker:stderr]', text);
      _writeWorkerLog('stderr', text);
    });

    this._process.on('exit', (code) => {
      console.log('[worker] Process exited with code:', code);
      this._process = null;
      this._starting = false;

      // 非主动关闭时自动重启
      if (!this._crashed && code !== 0 && this._restartCount < this._maxRestarts) {
        // 端口已被占用（说明已有 Worker 在跑，例如 bind 失败 10048 的场景）
        // 此时 auto-restart 只会再次 bind 失败，形成重启风暴，必须跳过
        if (_findPidOnPort(WORKER_PORT)) {
          console.log(`[worker] Port ${WORKER_PORT} already in use, skip auto-restart (another worker live)`);
          this._restartCount = 0;
          return;
        }
        this._restartCount++;
        // 重置飞书通道标志：crash 重启后通道实例已丢失，下次 ensureReady 需重新启动
        this._channelsStarted = false;
        console.log(`[worker] Auto-restarting (attempt ${this._restartCount}/${this._maxRestarts})`);
        setTimeout(() => this.start(), 2000);
      }
    });

    this._crashed = false;
    this._starting = false;

    // 后台等待 Worker 就绪后启动飞书通道（crash 重启场景需要）
    this._ensureReadyAndStartChannels().catch(() => {});
  }

  /**
   * 后台等待 Worker 就绪后启动飞书通道（不阻塞调用方）
   */
  async _ensureReadyAndStartChannels() {
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      if (await this._client.healthCheck()) {
        if (!this._channelsStarted) {
          this._channelsStarted = true;
          this._autoStartFeishuChannels();
        }
        return;
      }
      await new Promise((r) => setTimeout(r, 500));
    }
  }

  /**
   * 优雅关闭
   */
  async stop() {
    this._crashed = true;
    if (this._process) {
      this._process.kill('SIGTERM');
      // Windows fallback
      if (process.platform === 'win32') {
        this._process.kill();
      }
      this._process = null;
    }
  }

  /**
   * 重启 Worker（手动触发）：清理僵尸 → kill 当前 → 等端口释放 → start → health check
   * 保证零僵尸
   */
  async restart() {
    // single-flight：并发 restart 复用同一个 Promise，避免重复 spawn
    if (this._restartPromise) return this._restartPromise;
    this._restartPromise = (async () => {
      try {
        console.log('[worker] Manual restart triggered');
        // 1. 清理所有僵尸
        await this._cleanupOrphanWorkers();
        // 2. kill 当前管理的进程（如果有）
        this._crashed = true;
        if (this._process) {
          if (process.platform === 'win32') {
            try { this._process.kill(); } catch {}
          } else {
            try { this._process.kill('SIGTERM'); } catch {}
          }
          this._process = null;
        }
        // 3. 确保端口释放
        await _waitForPortFree(WORKER_PORT, 10000);
        // 4. 重置状态，启动新的
        this._crashed = false;
        this._channelsStarted = false;
        await this.start();
        // 5. 等待就绪
        const deadline = Date.now() + 30_000;
        while (Date.now() < deadline) {
          if (await this._client.healthCheck()) {
            this._restartCount = 0;
            this._channelsStarted = true;
            this._autoStartFeishuChannels();
            return { ok: true };
          }
          await new Promise((r) => setTimeout(r, 500));
        }
        return { ok: false, message: 'Worker failed to become ready within 30s' };
      } finally {
        this._restartPromise = null;
      }
    })();
    return this._restartPromise;
  }

  /**
   * 确保 Worker 就绪：
   * 1. 先探测 :9999 是否已有健康 Worker → 有则直接复用（不 spawn）
   * 2. 没有则清理僵尸 → spawn 新 Worker → 轮询 health check
   */
  async ensureReady() {
    // single-flight：并发 ensureReady 复用同一个 Promise，避免并发 spawn 多个 Worker 争抢端口
    if (this._ensurePromise) return this._ensurePromise;
    this._ensurePromise = (async () => {
      try {
        // 探测已存在的 Worker（可能是上次 Electron 留下的持久化进程）
        if (await this._client.healthCheck()) {
          if (!this._channelsStarted) {
            this._channelsStarted = true;
            this._autoStartFeishuChannels();
          }
          return;
        }

        // 已有本地 spawn 的进程在跑，直接复用
        if (this.isRunning) {
          console.log('[worker:diag] POLL path: local spawned process running, waiting health');
          const deadline = Date.now() + 30_000;
          while (Date.now() < deadline) {
            if (await this._client.healthCheck()) {
              this._restartCount = 0;
              if (!this._channelsStarted) {
                this._channelsStarted = true;
                this._autoStartFeishuChannels();
              }
              return;
            }
            await new Promise((r) => setTimeout(r, 500));
          }
          throw new Error('Worker failed to become ready within 30s');
        }

        // 没有运行中的 Worker：清理僵尸后启动新的
        await this._cleanupOrphanWorkers();
        await this.start();
        const deadline = Date.now() + 30_000;
        while (Date.now() < deadline) {
          if (await this._client.healthCheck()) {
            this._restartCount = 0;
            if (!this._channelsStarted) {
              this._channelsStarted = true;
              this._autoStartFeishuChannels();
            }
            return;
          }
          await new Promise((r) => setTimeout(r, 500));
        }
        throw new Error('Worker failed to become ready within 30s');
      } finally {
        this._ensurePromise = null;
      }
    })();
    return this._ensurePromise;
  }

  /**
   * 自动启动 enabled 的飞书通道
   */
  async _autoStartFeishuChannels() {
    try {
      const db = require('../lib/db');
      const raw = db.getAllSettings('feishu.');
      const channelsStr = raw['feishu.channels'] || '[]';
      const channels = JSON.parse(channelsStr);
      for (const ch of channels) {
        if (ch.enabled === 'true' || ch.enabled === true) {
          this._client.request('feishu.channel_start', { channel_id: ch.id }).catch(() => {});
        }
      }
    } catch {
      // 不阻塞启动
    }
  }

  /**
   * 清理僵尸 Worker：PID 文件残留 + 占用 :9999 的旧 python 进程
   * 确保端口释放后才返回
   */
  async _cleanupOrphanWorkers() {
    // 1. 通过 PID 文件清理
    try {
      // PID 文件路径与 worker_main.py 一致
      const pidPath = path.join(logDir, 'worker.pid');
      if (fs.existsSync(pidPath)) {
        const pid = fs.readFileSync(pidPath, 'utf8').trim();
        if (pid && /^\d+$/.test(pid) && _isPythonProcess(pid)) {
          console.log(`[worker] Cleaning orphan worker PID ${pid} (from pid file)`);
          _killPid(pid);
        }
        try { fs.unlinkSync(pidPath); } catch {}
      }
    } catch {}

    // 2. 通过端口检测清理
    const portPid = _findPidOnPort(WORKER_PORT);
    if (portPid && _isPythonProcess(portPid)) {
      console.log(`[worker] Cleaning orphan worker PID ${portPid} (on port ${WORKER_PORT})`);
      _killPid(portPid);
    }

    // 3. 等待端口释放
    await _waitForPortFree(WORKER_PORT, 5000);
  }

  /**
   * 发送 JSON-RPC 请求
   */
  async request(method, params = {}) {
    await this.ensureReady();
    return this._client.request(method, params);
  }

  /**
   * 发送 JSON-RPC 流式请求（async generator）
   */
  async *requestStream(method, params = {}) {
    await this.ensureReady();
    yield* this._client.requestStream(method, params);
  }
}

// 单例实例
let _instance = null;

function getWorker() {
  if (!_instance) {
    _instance = new PythonWorkerManager();
  }
  return _instance;
}

module.exports = PythonWorkerManager;
module.exports.getWorker = getWorker;
