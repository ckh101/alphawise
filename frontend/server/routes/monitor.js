const db = require('../lib/db');

const TOOL_DISPLAY_NAMES = {
  get_stock_info: '基本信息',
  get_realtime_quote: '实时行情',
  get_kline_data: 'K线数据',
  technical_analysis: '技术分析',
  fundamental_analysis: '基本面分析',
  risk_assessment: '风险评估',
  web_search: '联网搜索',
  investment_advice: '投资建议',
  run_backtest: '策略回测',
};

const MODE_DISPLAY_NAMES = {
  report: '投研报告',
  chat: '智能对话',
  search_summary: '资讯搜索',
};

module.exports = async function (fastify) {
  fastify.get('/stats', async () => {
    const data = db.getMonitorStats();
    data.top_tools = data.top_tools.map(t => ({
      ...t,
      display_name: TOOL_DISPLAY_NAMES[t.name] || t.name,
    }));
    return { code: 0, data };
  });

  fastify.get('/sessions', async (request) => {
    const { page = 1, page_size = 20, status, stock_symbol } = request.query;
    const data = db.listSessions({
      page: Number(page),
      pageSize: Number(page_size),
      status: status || undefined,
      stockSymbol: stock_symbol || undefined,
    });
    data.items = data.items.map(item => ({
      ...item,
      response_mode_display: MODE_DISPLAY_NAMES[item.response_mode] || item.response_mode,
    }));
    return { code: 0, data };
  });

  fastify.get('/sessions/:sessionId', async (request, reply) => {
    const data = db.getSessionDetail(request.params.sessionId);
    if (!data) {
      reply.code(404);
      return { code: 404, message: 'Session not found' };
    }
    data.response_mode_display = MODE_DISPLAY_NAMES[data.response_mode] || data.response_mode;
    return { code: 0, data };
  });

  fastify.delete('/sessions/:sessionId', async (request, reply) => {
    const ok = db.deleteSession(request.params.sessionId);
    if (!ok) {
      reply.code(404);
      return { code: 404, message: 'Session not found' };
    }
    return { code: 0, message: '已删除' };
  });

  fastify.post('/sessions/batch-delete', async (request) => {
    const { ids } = request.body;
    if (!ids || ids.length === 0) {
      return { code: 0, message: '无记录需要删除' };
    }
    const deleted = db.batchDeleteSessions(ids);
    return { code: 0, message: `已删除 ${deleted} 条记录`, data: { deleted } };
  });

  fastify.get('/conversations', async () => {
    const items = db.listConversations();
    return { code: 0, data: { items, total: items.length } };
  });

  fastify.get('/conversations/:conversationId', async (request, reply) => {
    const data = db.getConversationMessages(request.params.conversationId);
    if (!data) {
      reply.code(404);
      return { code: 404, message: '会话不存在' };
    }
    return { code: 0, data };
  });

  fastify.delete('/conversations/:conversationId', async (request) => {
    const deleted = db.deleteConversation(request.params.conversationId);
    return { code: 0, message: `已删除 ${deleted} 条记录`, data: { deleted } };
  });

  fastify.delete('/conversations/clear/all', async () => {
    const deleted = db.clearAllConversations();
    return { code: 0, message: `已清空 ${deleted} 条记录`, data: { deleted } };
  });
};
