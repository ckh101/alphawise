/**
 * 股票池路由
 *
 * prefix: /api/v1/stocks
 * - GET /search?query=xxx&limit=10 — 模糊搜索
 * - GET /status — 数量 + 上次同步时间
 * - POST /sync — 触发手动同步
 */

const db = require('../lib/db');
const stockPool = require('../lib/stockPool');

module.exports = async function (fastify) {
  // GET /search — 模糊搜索 code/name
  fastify.get('/search', async (request) => {
    const { query, limit } = request.query;
    const items = db.searchStocks(query, parseInt(limit) || 10);
    return { code: 0, data: items };
  });

  // GET /status — 当前股票池状态
  fastify.get('/status', async () => {
    return {
      code: 0,
      data: {
        count: db.getStocksCount(),
        last_sync_at: db.getStocksLastSyncAt(),
        syncing: stockPool.isSyncing(),
      },
    };
  });

  // POST /sync — 手动触发全量同步
  fastify.post('/sync', async (request, reply) => {
    const result = await stockPool.syncAll();
    if (!result.ok) {
      reply.code(500);
      return { code: 1, message: result.message };
    }
    return { code: 0, data: result };
  });
};
