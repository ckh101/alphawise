const db = require('../lib/db');
const { getWorker } = require('../lib/pythonWorker');
const schedulerLib = require('../lib/scheduler');

function buildCron(scheduleType, config) {
  if (!config) config = {};
  let { hour = 9, minute = 0 } = config;
  const { weekdays = [1] } = config;

  // 兼容 time: "HH:MM" 格式
  if (config.time && typeof config.time === 'string') {
    const parts = config.time.split(':');
    hour = parseInt(parts[0], 10) || 9;
    minute = parseInt(parts[1], 10) || 0;
  }

  switch (scheduleType) {
    case 'daily':
      return `${minute} ${hour} * * *`;
    case 'weekly': {
      const days = (weekdays || [1]).join(',');
      return `${minute} ${hour} * * ${days}`;
    }
    case 'monthly': {
      const day_from = config.day_from || 1;
      const day_to = config.day_to || 31;
      return `${minute} ${hour} ${day_from}-${day_to} * *`;
    }
    case 'interval': {
      const start = (config.start_time || '09:30').replace(':', '');
      const end = (config.end_time || '11:30').replace(':', '');
      const intervalMin = config.interval_minutes || 30;
      return `INTERVAL:${start}-${end}:${intervalMin}`;
    }
    default:
      return `${minute} ${hour} * * *`;
  }
}

module.exports = async function (fastify) {
  fastify.get('/tasks', async (request) => {
    const { keyword } = request.query;
    const tasks = db.getSchedulerTasks(keyword || undefined);
    return { code: 0, data: { tasks } };
  });

  fastify.post('/tasks', async (request, reply) => {
    const body = request.body;
    const cronExpr = buildCron(body.schedule_type, body.schedule_config);
    const task = db.createSchedulerTask({
      ...body,
      cron_expression: cronExpr,
    });
    if (task.enabled) schedulerLib.refresh(task.id);
    return { code: 0, data: task };
  });

  fastify.get('/tasks/status', async () => {
    return { code: 0, data: {} };
  });

  fastify.get('/tasks/:taskId', async (request, reply) => {
    const task = db.getSchedulerTask(Number(request.params.taskId));
    if (!task) {
      reply.code(404);
      return { code: 404, message: '任务不存在' };
    }
    return { code: 0, data: task };
  });

  fastify.put('/tasks/:taskId', async (request, reply) => {
    const taskId = Number(request.params.taskId);
    const existing = db.getSchedulerTask(taskId);
    if (!existing) {
      reply.code(404);
      return { code: 404, message: '任务不存在' };
    }

    const body = request.body;
    const updateData = { ...body };

    if (body.schedule_type || body.schedule_config) {
      const scheduleType = body.schedule_type || existing.schedule_type;
      let scheduleConfig = body.schedule_config || existing.schedule_config;
      // schedule_config 可能是字符串或对象
      if (typeof scheduleConfig === 'string') {
        try { scheduleConfig = JSON.parse(scheduleConfig); } catch { /* use as-is */ }
      }
      updateData.cron_expression = buildCron(scheduleType, scheduleConfig);
    }

    const task = db.updateSchedulerTask(taskId, updateData);
    schedulerLib.refresh(taskId);
    return { code: 0, data: task };
  });

  fastify.delete('/tasks/:taskId', async (request, reply) => {
    const taskId = Number(request.params.taskId);
    const ok = db.deleteSchedulerTask(taskId);
    schedulerLib.unschedule(taskId);
    if (!ok) {
      reply.code(404);
      return { code: 404, message: '任务不存在' };
    }
    return { code: 0, message: '已删除' };
  });

  fastify.put('/tasks/:taskId/toggle', async (request, reply) => {
    const taskId = Number(request.params.taskId);
    const existing = db.getSchedulerTask(taskId);
    if (!existing) {
      reply.code(404);
      return { code: 404, message: '任务不存在' };
    }
    const task = db.updateSchedulerTask(taskId, { enabled: request.body.enabled });
    schedulerLib.refresh(taskId);
    return { code: 0, data: task };
  });

  // POST /tasks/:taskId/trigger — 手动触发任务
  fastify.post('/tasks/:taskId/trigger', async (request, reply) => {
    const taskId = Number(request.params.taskId);
    const existing = db.getSchedulerTask(taskId);
    if (!existing) {
      reply.code(404);
      return { code: 404, message: '任务不存在' };
    }
    try {
      const result = await getWorker().request('scheduler.trigger', { task_id: taskId });
      if (result.status === 'ok') return { code: 0, data: result.data };
      reply.code(500);
      return { code: 1, message: result.message || '任务触发失败' };
    } catch (e) {
      request.log.error(`scheduler.trigger failed: ${e.message}`);
      reply.code(502);
      return { detail: '任务触发失败，Worker 不可用' };
    }
  });

  // POST /tasks/:taskId/trigger/stream — 流式触发任务
  fastify.post('/tasks/:taskId/trigger/stream', async (request, reply) => {
    const taskId = Number(request.params.taskId);
    const existing = db.getSchedulerTask(taskId);
    if (!existing) {
      reply.code(404);
      return { code: 404, message: '任务不存在' };
    }

    reply.raw.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'X-Accel-Buffering': 'no',
    });

    try {
      const stream = getWorker().requestStream('scheduler.trigger_stream', { task_id: taskId });
      for await (const chunk of stream) {
        const data = typeof chunk === 'string' ? chunk : JSON.stringify(chunk);
        reply.raw.write(`data: ${data}\n\n`);
      }
    } catch (e) {
      const payload = JSON.stringify({ type: 'error', data: { message: `任务触发失败: ${e.message}` } });
      reply.raw.write(`data: ${payload}\n\n`);
    }
    reply.raw.end();
  });
};
