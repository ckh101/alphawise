/**
 * 自选股代理路由
 *
 * prefix: /api/v1/watchlist
 * CRUD 直接操作 SQLite，detail 通过 Python Worker 获取 TDX 数据。
 */

const db = require('../lib/db');
const { getWorker } = require('../lib/pythonWorker');

module.exports = async function (fastify) {
  // GET /items — 列出所有自选股
  fastify.get('/items', async () => {
    const items = db.getWatchlistItems();
    return { code: 0, data: { items } };
  });

  // POST /items — 添加自选股
  fastify.post('/items', async (request, reply) => {
    const body = request.body;
    if (!body || !body.symbol) {
      reply.code(400);
      return { detail: 'symbol 必填' };
    }
    const item = db.addWatchlistItem(body);
    return { code: 0, data: item };
  });

  // DELETE /items/:itemId — 删除自选股
  fastify.delete('/items/:itemId', async (request, reply) => {
    const ok = db.deleteWatchlistItem(Number(request.params.itemId));
    if (!ok) {
      reply.code(404);
      return { code: 404, message: '不存在' };
    }
    return { code: 0, message: '已删除' };
  });

  // PUT /items/:itemId — 更新自选股
  fastify.put('/items/:itemId', async (request, reply) => {
    const id = Number(request.params.itemId);
    const existing = db.getWatchlistItem(id);
    if (!existing) {
      reply.code(404);
      return { code: 404, message: '不存在' };
    }
    const item = db.updateWatchlistItem(id, request.body);
    return { code: 0, data: item };
  });

  // GET /items/:itemId/detail — 复合接口：quote + stock_info + kline
  fastify.get('/items/:itemId/detail', async (request, reply) => {
    const id = Number(request.params.itemId);
    const item = db.getWatchlistItem(id);
    if (!item) {
      reply.code(404);
      return { code: 404, message: '不存在' };
    }

    const symbol = item.symbol;
    // 优先从 stocks 表查 name（毫秒级），自选股表 name 作为 fallback
    const stockRow = db.getStockBySymbol(symbol);
    const stockName = (stockRow && stockRow.name) || item.name || '';
    const result = { symbol, name: stockName, quote: null, info: null, klines: {} };

    // 并行请求 Python Worker
    try {
      const [quoteResp, infoResp] = await Promise.all([
        getWorker().request('tdx.quote', { symbols: symbol }).catch(() => null),
        getWorker().request('tdx.stock_info', { symbol }).catch(() => null),
      ]);
      if (quoteResp?.status === 'ok') {
        const q = Array.isArray(quoteResp.data) ? quoteResp.data[0] : quoteResp.data;
        if (q) q.name = stockName;  // Python 端不补 name，这里统一覆盖
        result.quote = q || null;
      }
      if (infoResp?.status === 'ok' && infoResp.data) {
        if (!infoResp.data.name) infoResp.data.name = stockName;
        result.info = infoResp.data;
      }
    } catch (_) { /* ignore */ }

    // 如果 info 失败，用 stocks 表兜底（至少返回 name）
    if (!result.info && stockRow) {
      result.info = { name: stockName, symbol };
    }

    // K线（日/周/月）
    const periods = ['daily', 'weekly', 'monthly'];
    const today = new Date();
    const sixMonthsAgo = new Date(today.getTime() - 180 * 86400000);
    const fmtDate = d => d.toISOString().slice(0, 10);
    const klinePromises = periods.map(p =>
      getWorker().request('tdx.kline', {
        symbol, period: p, start_date: fmtDate(sixMonthsAgo),
      }).then(r => ({ period: p, data: r.status === 'ok' ? r.data : [] }))
        .catch(() => ({ period: p, data: [] }))
    );
    const klineResults = await Promise.all(klinePromises);
    for (const kr of klineResults) {
      result.klines[kr.period] = kr.data;
    }

    return { code: 0, data: result };
  });
};
