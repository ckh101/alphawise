/**
 * 飞书通道路由
 *
 * prefix: /api/v1/channel/feishu
 * CRUD 在 Node.js（数据库），启停代理到 Worker。
 */

const { getWorker } = require('../lib/pythonWorker');

// 通知 Worker 通道配置已变更
async function _notifyWorkerChannelsChanged() {
  try {
    await getWorker().request('feishu.channel_reload', {});
  } catch { /* Worker 可能未就绪 */ }
}

module.exports = async function (fastify) {
  const db = require('../lib/db');

  // -----------------------------------------------------------------------
  // GET /status — 飞书连接状态
  // -----------------------------------------------------------------------
  fastify.get('/status', async (request, reply) => {
    try {
      const result = await getWorker().request('feishu.status');
      if (result.status === 'ok') return { code: 0, data: result.data };
      return { code: 0, data: { running: false, channels_running: [], any_running: false } };
    } catch (e) {
      request.log.error(`feishu.status failed: ${e.message}`);
      return { code: 0, data: { running: false, channels_running: [], any_running: false } };
    }
  });

  // -----------------------------------------------------------------------
  // GET /config — 读取飞书配置
  // -----------------------------------------------------------------------
  fastify.get('/config', async () => {
    const raw = db.getAllSettings('feishu.');
    // 去掉 feishu. 前缀，前端期望 {enabled, app_id, ...}
    const data = {};
    for (const [key, value] of Object.entries(raw)) {
      const shortKey = key.replace(/^feishu\./, '');
      if (shortKey !== 'channels') { // channels 是独立的通道列表，不属于基础配置
        data[shortKey] = value;
      }
    }
    return { code: 0, data };
  });

  // -----------------------------------------------------------------------
  // PUT /config — 保存飞书配置
  // -----------------------------------------------------------------------
  fastify.put('/config', async (request, reply) => {
    const { settings } = request.body;
    if (!settings) {
      reply.code(400);
      return { detail: '缺少 settings 字段' };
    }
    try {
      await db.updateSettings(settings);
      return { code: 0, message: '飞书配置已保存' };
    } catch (e) {
      request.log.error(`feishu config save failed: ${e.message}`);
      reply.code(500);
      return { detail: '配置保存失败' };
    }
  });

  // -----------------------------------------------------------------------
  // POST /start — 启动飞书
  // -----------------------------------------------------------------------
  fastify.post('/start', async (request, reply) => {
    try {
      const result = await getWorker().request('feishu.start');
      if (result.status === 'ok') return { code: 0, message: result.message || '飞书已启动' };
      reply.code(500);
      return { code: 1, message: result.message || '飞书启动失败' };
    } catch (e) {
      request.log.error(`feishu.start failed: ${e.message}`);
      reply.code(502);
      return { detail: '飞书启动失败' };
    }
  });

  // -----------------------------------------------------------------------
  // POST /stop — 停止飞书
  // -----------------------------------------------------------------------
  fastify.post('/stop', async (request, reply) => {
    try {
      const result = await getWorker().request('feishu.stop');
      if (result.status === 'ok') return { code: 0, message: result.message || '飞书已停止' };
      reply.code(500);
      return { code: 1, message: result.message || '飞书停止失败' };
    } catch (e) {
      request.log.error(`feishu.stop failed: ${e.message}`);
      reply.code(502);
      return { detail: '飞书停止失败' };
    }
  });

  // -----------------------------------------------------------------------
  // GET /channels — 列出所有通道
  // -----------------------------------------------------------------------
  fastify.get('/channels', async () => {
    const channels = getChannelsList();
    // 从 Worker 获取运行中的通道 ID 列表
    try {
      const status = await getWorker().request('feishu.status');
      const runningIds = status?.data?.channels_running || [];
      for (const ch of channels) {
        ch.running = runningIds.includes(ch.id);
      }
    } catch {
      for (const ch of channels) {
        ch.running = false;
      }
    }
    return { code: 0, data: { channels } };
  });

  // -----------------------------------------------------------------------
  // POST /channels — 添加通道
  // -----------------------------------------------------------------------
  fastify.post('/channels', async (request, reply) => {
    const { name, webhook_url, receive_id } = request.body;
    if (!name || !webhook_url) {
      reply.code(400);
      return { detail: 'name 和 webhook_url 必填' };
    }
    try {
      const channels = getChannelsList();
      const channel = {
        id: `ch_${Date.now()}`,
        name,
        webhook_url,
        receive_id: receive_id || '',
        enabled: true,
        created_at: new Date().toISOString(),
      };
      channels.push(channel);
      saveChannelsList(channels);
      // 通知 Worker 重载通道配置
      _notifyWorkerChannelsChanged().catch(() => {});
      return { code: 0, data: channel };
    } catch (e) {
      request.log.error(`channel add failed: ${e.message}`);
      reply.code(500);
      return { detail: '通道添加失败' };
    }
  });

  // -----------------------------------------------------------------------
  // PUT /channels/:channelId — 更新通道
  // -----------------------------------------------------------------------
  fastify.put('/channels/:channelId', async (request, reply) => {
    const { channelId } = request.params;
    const channels = getChannelsList();
    const idx = channels.findIndex(c => c.id === channelId);
    if (idx < 0) {
      reply.code(404);
      return { detail: '通道不存在' };
    }

    const { name, webhook_url, receive_id, enabled, push_targets } = request.body;
    if (name !== undefined) channels[idx].name = name;
    if (webhook_url !== undefined) channels[idx].webhook_url = webhook_url;
    if (receive_id !== undefined) channels[idx].receive_id = receive_id;
    if (enabled !== undefined) channels[idx].enabled = enabled;
    if (push_targets !== undefined) channels[idx].push_targets = push_targets;

    saveChannelsList(channels);
    // 通知 Worker 重载通道配置（名称变更等）
    _notifyWorkerChannelsChanged().catch(() => {});
    return { code: 0, data: channels[idx] };
  });

  // -----------------------------------------------------------------------
  // DELETE /channels/:channelId — 删除通道
  // -----------------------------------------------------------------------
  fastify.delete('/channels/:channelId', async (request, reply) => {
    const { channelId } = request.params;
    const channels = getChannelsList();
    const idx = channels.findIndex(c => c.id === channelId);
    if (idx < 0) {
      reply.code(404);
      return { detail: '通道不存在' };
    }
    channels.splice(idx, 1);
    saveChannelsList(channels);
    // 通知 Worker 重载通道配置
    _notifyWorkerChannelsChanged().catch(() => {});
    return { code: 0, message: '通道已删除' };
  });

  // -----------------------------------------------------------------------
  // POST /channels/:channelId/start — 启动通道
  // -----------------------------------------------------------------------
  fastify.post('/channels/:channelId/start', async (request, reply) => {
    const { channelId } = request.params;
    try {
      const result = await getWorker().request('feishu.channel_start', { channel_id: channelId });
      if (result.status === 'ok') return { code: 0, message: result.message };
      reply.code(500);
      return { code: 1, message: result.message || '通道启动失败' };
    } catch (e) {
      request.log.error(`channel start failed: ${e.message}`);
      reply.code(502);
      return { detail: '通道启动失败' };
    }
  });

  // -----------------------------------------------------------------------
  // POST /channels/:channelId/stop — 停止通道
  // -----------------------------------------------------------------------
  fastify.post('/channels/:channelId/stop', async (request, reply) => {
    const { channelId } = request.params;
    try {
      const result = await getWorker().request('feishu.channel_stop', { channel_id: channelId });
      if (result.status === 'ok') return { code: 0, message: result.message };
      reply.code(500);
      return { code: 1, message: result.message || '通道停止失败' };
    } catch (e) {
      request.log.error(`channel stop failed: ${e.message}`);
      reply.code(502);
      return { detail: '通道停止失败' };
    }
  });

  // -----------------------------------------------------------------------
  // POST /send-message — 通过飞书通道发送消息
  // -----------------------------------------------------------------------
  fastify.post('/send-message', async (request, reply) => {
    const { channel_id, text } = request.body || {};
    if (!channel_id || !text) {
      reply.code(400);
      return { detail: 'channel_id 和 text 必填' };
    }

    // 优先从通道配置的 push_targets 获取推送目标
    const channels = getChannelsList();
    const ch = channels.find(c => c.id === channel_id);
    const pushTargets = ch?.push_targets || [];

    if (pushTargets.length === 0) {
      return { code: 1, message: '该通道未配置推送目标，请在通道设置中添加推送目标' };
    }

    // 向所有推送目标发送
    let sentCount = 0;
    for (const target of pushTargets) {
      try {
        const result = await getWorker().request('feishu.send_message', {
          receive_id: target.receive_id,
          receive_id_type: target.receive_id_type || 'chat_id',
          text,
          channel_id,
        });
        if (result.status === 'ok') sentCount++;
      } catch (e) {
        request.log.error(`send-message to ${target.receive_id} failed: ${e.message}`);
      }
    }

    if (sentCount > 0) return { code: 0, message: `消息已发送至 ${sentCount} 个目标` };
    reply.code(500);
    return { code: 1, message: '消息发送失败' };
  });

  // -----------------------------------------------------------------------
  // GET /events — SSE 事件流
  // -----------------------------------------------------------------------
  const sseClients = new Set();

  fastify.get('/events', (request, reply) => {
    reply.raw.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
    });
    reply.raw.write(': connected\n\n');

    sseClients.add(reply.raw);

    const heartbeat = setInterval(() => {
      if (reply.raw.writableEnded) {
        clearInterval(heartbeat);
        return;
      }
      reply.raw.write(': heartbeat\n\n');
    }, 15000);

    request.raw.on('close', () => {
      clearInterval(heartbeat);
      sseClients.delete(reply.raw);
    });
  });

  // -----------------------------------------------------------------------
  // POST /internal/event — Worker 回调飞书事件
  // -----------------------------------------------------------------------
  fastify.post('/internal/event', async (request, reply) => {
    const event = request.body;
    const data = JSON.stringify(event);
    for (const client of sseClients) {
      try {
        if (!client.writableEnded) {
          client.write(`data: ${data}\n\n`);
        }
      } catch { /* ignore */ }
    }
    return { ok: true };
  });
};

// ---------------------------------------------------------------------------
// 通道列表持久化（存储在 settings 表中）
// ---------------------------------------------------------------------------

function getChannelsList() {
  const db = require('../lib/db');
  const raw = db.getSetting('feishu.channels');
  if (!raw) return [];
  try {
    return JSON.parse(raw);
  } catch {
    return [];
  }
}

function saveChannelsList(channels) {
  const db = require('../lib/db');
  db.setSetting('feishu.channels', JSON.stringify(channels));
}
