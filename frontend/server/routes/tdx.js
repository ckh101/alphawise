/**
 * TDX 数据代理路由
 *
 * prefix: /api/v1/tdx
 * 全部代理到 Python Worker (JSON-RPC)。
 */

const { getWorker } = require('../lib/pythonWorker');

module.exports = async function (fastify) {
  // GET /quote — 实时行情
  fastify.get('/quote', async (request, reply) => {
    const { symbols } = request.query;
    if (!symbols) {
      reply.code(400);
      return { detail: '参数 symbols 必填' };
    }
    try {
      const result = await getWorker().request('tdx.quote', { symbols });
      if (result.status === 'ok') return { code: 0, data: result.data };
      reply.code(500);
      return { code: 1, message: result.message || '行情获取失败' };
    } catch (e) {
      request.log.error(`tdx.quote failed: ${e.message}`);
      reply.code(502);
      return { detail: '行情数据获取失败，请稍后重试' };
    }
  });

  // GET /kline — K线数据
  fastify.get('/kline', async (request, reply) => {
    try {
      const result = await getWorker().request('tdx.kline', request.query);
      if (result.status === 'ok') return { code: 0, data: result.data };
      reply.code(500);
      return { code: 1, message: result.message || 'K线获取失败' };
    } catch (e) {
      request.log.error(`tdx.kline failed: ${e.message}`);
      reply.code(502);
      return { detail: 'K线数据获取失败，请稍后重试' };
    }
  });

  // GET /search-by-name — 根据名称/代码片段查询股票（从本地 stocks 表查询）
  fastify.get('/search-by-name', async (request, reply) => {
    const { query } = request.query;
    if (!query) return { code: 0, data: [] };
    try {
      const db = require('../lib/db');
      const rows = db.searchStocks(query, 10);
      return { code: 0, data: rows };
    } catch (e) {
      request.log.error(`tdx.search_by_name failed: ${e.message}`);
      return { code: 0, data: [] };
    }
  });

  // GET /stock-info — 个股信息
  fastify.get('/stock-info', async (request, reply) => {
    const { symbol } = request.query;
    if (!symbol) {
      reply.code(400);
      return { detail: '参数 symbol 必填' };
    }
    try {
      const result = await getWorker().request('tdx.stock_info', { symbol });
      if (result.status === 'ok') return { code: 0, data: result.data };
      reply.code(500);
      return { code: 1, message: result.message || '个股信息获取失败' };
    } catch (e) {
      request.log.error(`tdx.stock_info failed: ${e.message}`);
      reply.code(502);
      return { detail: '个股信息获取失败，请稍后重试' };
    }
  });

  // GET /health — TDX 连接状态
  fastify.get('/health', async (request, reply) => {
    try {
      const result = await getWorker().request('tdx.health');
      if (result.status === 'ok') return { code: 0, data: result };
      return { code: 1, message: result.message || 'TDX 连接失败' };
    } catch (e) {
      request.log.error(`tdx.health failed: ${e.message}`);
      reply.code(502);
      return { detail: 'TDX 连接检查失败' };
    }
  });
};
