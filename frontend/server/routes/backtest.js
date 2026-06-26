/**
 * 策略回测代理路由
 *
 * prefix: /api/v1/backtest
 * 全部代理到 Python Worker (JSON-RPC)。
 */

const { getWorker } = require('../lib/pythonWorker');

module.exports = async function (fastify) {
  // GET /strategies — 可用策略列表
  fastify.get('/strategies', async (request, reply) => {
    try {
      const result = await getWorker().request('backtest.strategies');
      if (result.status === 'ok') return { code: 0, data: result.data };
      reply.code(500);
      return { code: 1, message: result.message || '策略列表获取失败' };
    } catch (e) {
      request.log.error(`backtest.strategies failed: ${e.message}`);
      reply.code(502);
      return { detail: '策略列表获取失败，请稍后重试' };
    }
  });

  // GET /params — 策略参数
  fastify.get('/params', async (request, reply) => {
    try {
      const result = await getWorker().request('backtest.params', request.query);
      if (result.status === 'ok') return { code: 0, data: result.data };
      reply.code(500);
      return { code: 1, message: result.message || '策略参数获取失败' };
    } catch (e) {
      request.log.error(`backtest.params failed: ${e.message}`);
      reply.code(502);
      return { detail: '策略参数获取失败，请稍后重试' };
    }
  });

  // POST /run — 执行回测
  fastify.post('/run', async (request, reply) => {
    try {
      const result = await getWorker().request('backtest.run', request.body);
      if (result.status === 'ok') return { code: 0, data: result.data };
      reply.code(500);
      return { code: 1, message: result.message || '回测执行失败' };
    } catch (e) {
      request.log.error(`backtest.run failed: ${e.message}`);
      reply.code(502);
      return { detail: '回测执行失败，请稍后重试' };
    }
  });
};
