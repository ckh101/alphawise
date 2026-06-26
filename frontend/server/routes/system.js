/**
 * 系统管理路由
 *
 * prefix: /api/v1/system
 * Worker 进程状态查询 + 手动重启
 */

const { getWorker } = require('../lib/pythonWorker');

module.exports = async function (fastify) {
  // GET /worker/status — Worker 运行状态
  fastify.get('/worker/status', async () => {
    const worker = getWorker();
    return {
      code: 0,
      data: {
        running: worker.isRunning,
        // 注意：isRunning 只反映本 Node 进程 spawn 的子进程，
        // 持久化的 Worker（非本进程 spawn）需要通过 health check 判断
      },
    };
  });

  // POST /worker/restart — 手动重启 Worker（零僵尸保证）
  fastify.post('/worker/restart', async (request, reply) => {
    try {
      const worker = getWorker();
      const result = await worker.restart();
      if (result.ok) {
        return { code: 0, message: 'Worker 重启成功' };
      }
      reply.code(500);
      return { code: 500, message: result.message || 'Worker 重启失败' };
    } catch (e) {
      reply.code(500);
      return { code: 500, message: 'Worker 重启失败: ' + e.message };
    }
  });
};
