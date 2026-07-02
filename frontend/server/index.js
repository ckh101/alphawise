/**
 * Node.js 后端服务（Fastify）
 *
 * 随 Electron 启动，毫秒级就绪。
 * 端口 9998，与原 Python 后端完全相同的 API 路径和响应格式。
 */

const path = require('path');
const fs = require('fs');

// 判断环境
const isDev = process.env.NODE_ENV === 'development';

// 配置文件路径
// 生产环境由独立 node.exe 启动（无 process.resourcesPath，仅 Electron 主进程有），
// 故读 main.js 注入的 ELECTRON_RESOURCES_PATH 来解析真实 backend 目录。
const backendDir = isDev
  ? path.join(__dirname, '..', '..', 'backend')
  : path.join(process.env.ELECTRON_RESOURCES_PATH || process.resourcesPath || path.join(__dirname, '..', '..', 'backend'), 'backend');

const configDir = path.join(backendDir, 'resources', 'config');

// 日志目录：从环境变量（Electron 子进程注入）或 fallback 到项目 logs/
const logDir = process.env.HARNESS_LOG_DIR
  || path.join(__dirname, '..', '..', 'logs');
if (!fs.existsSync(logDir)) fs.mkdirSync(logDir, { recursive: true });

const serverLogPath = path.resolve(logDir, 'server.log');

async function createServer() {
  // 确保 server.log 文件存在（pino/file 需要）
  if (!fs.existsSync(serverLogPath)) fs.writeFileSync(serverLogPath, '');

  const pinoTargets = isDev
    ? [
        { target: 'pino-pretty', options: { colorize: true }, level: 'debug' },
        { target: 'pino/file', options: { destination: serverLogPath }, level: 'info' },
      ]
    : [
        { target: 'pino/file', options: { destination: serverLogPath }, level: 'info' },
      ];

  const fastify = require('fastify')({
    logger: {
      level: isDev ? 'debug' : 'info',
      transport: { targets: pinoTargets },
    },
    bodyLimit: 50 * 1024 * 1024, // 50MB
  });

  // CORS
  await fastify.register(require('@fastify/cors'), { origin: true });

  // 注册路由
  fastify.register(require('./routes/health'), { backendDir, configDir });
  fastify.register(require('./routes/settings'), { prefix: '/api/v1/settings', backendDir });
  fastify.register(require('./routes/monitor'), { prefix: '/api/v1/agent/monitor' });
  fastify.register(require('./routes/agent'), { prefix: '/api/v1/agent' });
  fastify.register(require('./routes/scheduler'), { prefix: '/api/v1/scheduler' });
  fastify.register(require('./routes/tdx'), { prefix: '/api/v1/tdx' });
  fastify.register(require('./routes/backtest'), { prefix: '/api/v1/backtest' });
  fastify.register(require('./routes/channel'), { prefix: '/api/v1/channel/feishu' });
  fastify.register(require('./routes/watchlist'), { prefix: '/api/v1/watchlist' });
  fastify.register(require('./routes/stocks'), { prefix: '/api/v1/stocks' });
  fastify.register(require('./routes/system'), { prefix: '/api/v1/system' });

  return fastify;
}

let _server = null;

async function startServer() {
  if (_server) return _server;

  _server = await createServer();

  await _server.listen({ port: 9998, host: '127.0.0.1' });

  console.log('[server] Fastify listening on http://127.0.0.1:9998');

  // 先确保表结构已创建（scheduler/stockPool 依赖这些表）
  const db = require('./lib/db');
  db.initDb();

  // 启动定时任务调度
  const scheduler = require('./lib/scheduler');
  scheduler.startAll();

  // 启动股票池同步（表为空时后台全量同步 + 每日定时更新）
  const stockPool = require('./lib/stockPool');
  stockPool.startCron();
  // 延迟 5 秒后检查（等待 Python Worker 就绪）
  setTimeout(() => stockPool.bootstrapIfEmpty(), 5000);

  return _server;
}

async function stopServer() {
  if (_server) {
    await _server.close();
    _server = null;
    console.log('[server] Fastify stopped');
  }
}

module.exports = { startServer, stopServer, createServer };

// Standalone mode: node server/index.js
if (require.main === module) {
  startServer().catch((err) => {
    console.error('[server] Failed to start:', err.message);
    process.exit(1);
  });
}
