/**
 * API服务模块
 * 封装后端API调用
 */

// 使用全局axios（通过CDN加载）
const axios = window.axios;

// API基础URL
const API_BASE_URL = 'http://127.0.0.1:9998';

// 创建axios实例
const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 180000,  // 180秒，GLM深度分析+数据获取可能需要较长时间
  headers: {
    'Content-Type': 'application/json'
  }
});

// 请求拦截器
apiClient.interceptors.request.use(
  (config) => {
    console.log(`[API] ${config.method?.toUpperCase()} ${config.url}`);
    return config;
  },
  (error) => {
    console.error('[API] Request error:', error);
    return Promise.reject(error);
  }
);

// 响应拦截器
apiClient.interceptors.response.use(
  (response) => {
    console.log(`[API] Response:`, response.data);
    return response.data;
  },
  (error) => {
    console.error('[API] Response error:', error);
    return Promise.reject(error);
  }
);

// 后端未就绪时自动重试（最多 60 秒）
async function waitForBackend(fn, maxRetries = 30) {
  for (let i = 0; i < maxRetries; i++) {
    try {
      return await fn();
    } catch (e) {
      if (e.code === 'ERR_NETWORK' || e.code === 'ECONNREFUSED' || e.message?.includes('Network Error')) {
        if (i === 0) console.log('[API] Backend not ready, waiting...');
        await new Promise(r => setTimeout(r, 2000));
        continue;
      }
      throw e;
    }
  }
  throw new Error('Backend unavailable after waiting');
}

/**
 * 通达信数据API
 */
const tdxAPI = {
  /**
   * 获取实时行情
   * @param {string} symbols - 股票代码列表，逗号分隔
   */
  async getQuote(symbols) {
    return await apiClient.get('/api/v1/tdx/quote', {
      params: { symbols }
    });
  },

  /**
   * 获取K线数据
   * @param {string} symbol - 股票代码
   * @param {string} period - K线周期
   * @param {string} startDate - 开始日期
   * @param {string} endDate - 结束日期
   */
  async getKline(symbol, period, startDate = null, endDate = null) {
    const params = { symbol, period };
    if (startDate) params.start_date = startDate;
    if (endDate) params.end_date = endDate;

    return await apiClient.get('/api/v1/tdx/kline', { params });
  },

  /**
   * 健康检查
   */
  async healthCheck() {
    return await apiClient.get('/api/v1/tdx/health');
  },

  /**
   * 获取股票详细信息
   * @param {string} symbol - 股票代码
   */
  async getStockInfo(symbol) {
    return await apiClient.get('/api/v1/tdx/stock-info', { params: { symbol } });
  },

  /**
   * 根据名称/代码片段查询股票（自动联想）
   * @param {string} query - 名称或代码
   */
  async searchByName(query) {
    return await apiClient.get('/api/v1/tdx/search-by-name', { params: { query } });
  }
};

/**
 * GLM服务API
 */
const glmAPI = {
  /**
   * GLM对话
   * @param {string} message - 用户消息
   * @param {string} sessionId - 会话ID
   */
  async chat(message, sessionId = null) {
    return await apiClient.post('/api/v1/glm/chat', null, {
      params: { message, session_id: sessionId }
    });
  },

  /**
   * 股票分析
   * @param {string} symbol - 股票代码
   * @param {string} analysisType - 分析类型
   */
  async analyze(symbol, analysisType = 'comprehensive') {
    return await apiClient.post('/api/v1/glm/analyze', null, {
      params: { symbol, analysis_type: analysisType }
    });
  },

  /**
   * Agent执行（带工具调用）
   * @param {string} prompt - 任务描述
   * @param {string} taskType - 任务类型
   * @param {string} sessionId - 会话ID
   */
  async execute(prompt, taskType = 'chat', sessionId = null) {
    const body = {
      prompt,
      task_type: taskType
    };
    if (sessionId) {
      body.session_id = sessionId;
    }
    return await apiClient.post('/api/v1/agent/claude/execute', body);
  },

  /**
   * ReAct端到端投研分析（新一代智能Agent）
   * 一句话输入，自动完成所有分析！
   * @param {string} prompt - 任务描述，如"分析600519"或"贵州茅台怎么样"
   * @param {string} sessionId - 会话ID（可选）
   */
  async reactAnalyze(prompt, sessionId = null) {
    const body = {
      prompt,
      task_type: 'analysis'
    };
    if (sessionId) {
      body.session_id = sessionId;
    }
    return await apiClient.post('/api/v1/agent/react/analyze', body);
  },

  /**
   * 上传文件并解析内容
   * @param {File} file - 文件对象
   * @returns {Promise} 解析结果 { file_id, filename, content_text, char_count }
   */
  async uploadFile(file) {
    const formData = new FormData();
    formData.append('file', file);
    const resp = await fetch(API_BASE_URL + '/api/v1/agent/upload', {
      method: 'POST',
      body: formData
    });
    return resp.json();
  },

  /**
   * ReAct流式分析（SSE）
   * 实时推送分析进度，完成后推送最终结果
   * @param {string} prompt - 任务描述
   * @param {string} sessionId - 会话ID（可选）
   * @param {Object|null} fileContext - 文件上下文 { filename, content_text }
   * @param {Function} onProgress - 进度回调 (progressEvent) => void
   * @param {Function} onResult - 结果回调 (resultData) => void
   * @param {Function} onError - 错误回调 (errorData) => void
   * @param {Function} [onToken] - 报告流式 token 回调 (tokenStr) => void，用于打字机效果
   */
  reactAnalyzeStream(prompt, sessionId = null, fileContext = null, onProgress, onResult, onError, signal = null, onToken = null) {
    const body = {
      prompt,
      task_type: 'analysis'
    };
    if (sessionId) {
      body.session_id = sessionId;
    }
    if (fileContext) {
      body.file_context = fileContext;
    }

    console.log('[SSE] Sending body:', JSON.stringify(body).substring(0, 200));
    fetch(API_BASE_URL + '/api/v1/agent/react/analyze/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    }).then(response => {
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      function read() {
        reader.read().then(({ done, value }) => {
          if (done) return;
          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const event = JSON.parse(line.slice(6));
                if (event.type === 'progress') {
                  const data = event.data || {};
                  if (data.status === 'streaming' && typeof data.token === 'string') {
                    // 报告逐 token 增量，走打字机渲染
                    if (onToken) onToken(data.token);
                  } else if (onProgress) {
                    onProgress(data);
                  }
                } else if (event.type === 'result' && onResult) {
                  onResult(event.data);
                } else if (event.type === 'error' && onError) {
                  onError(event.data);
                }
              } catch (e) {
                console.warn('[SSE] Parse error:', e);
              }
            }
          }
          read();
        }).catch(err => {
          console.error('[SSE] Read error:', err);
          if (onError) onError({ message: err.message });
        });
      }
      read();
    }).catch(err => {
      console.error('[SSE] Fetch error:', err);
      if (onError) onError({ message: err.message });
    });
  },

  /**
   * 获取ReAct Agent状态
   */
  async getReactStatus() {
    return await apiClient.get('/api/v1/agent/react/status');
  },

  /**
   * 生成 PDF 报告
   * @param {Object} params - { markdown, stock_symbol, stock_name, generated_at }
   */
  async generatePDF(params) {
    return await apiClient.post('/api/v1/agent/pdf/generate', params);
  }
};

/**
 * 系统API
 */
const systemAPI = {
  /**
   * 健康检查
   */
  async healthCheck() {
    return await apiClient.get('/health');
  },

  /**
   * 应用信息
   */
  async getAppInfo() {
    return await apiClient.get('/api/v1/info');
  },

  /**
   * 重启 Worker 进程
   */
  async restartWorker() {
    return await apiClient.post('/api/v1/system/worker/restart', {}, { timeout: 60000 });
  },

  /**
   * Worker 运行状态
   */
  async getWorkerStatus() {
    return await apiClient.get('/api/v1/system/worker/status');
  }
};

/**
 * 策略回测API
 */
const backtestAPI = {
  /**
   * 列出可用策略
   */
  async listStrategies() {
    return await apiClient.get('/api/v1/backtest/strategies');
  },

  /**
   * 获取策略参数
   * @param {string} strategy - 策略名称
   */
  async getParams(strategy) {
    return await apiClient.get('/api/v1/backtest/params', { params: { strategy } });
  },

  /**
   * 执行回测
   * @param {Object} params - 回测参数
   */
  async runBacktest({ symbol, strategy, params, startDate, endDate, initialCash }) {
    const body = {
      symbol,
      strategy,
      params: params || null,
      start_date: startDate || null,
      end_date: endDate || null,
      initial_cash: initialCash || 100000
    };
    return await apiClient.post('/api/v1/backtest/run', body);
  }
};

/**
 * 配置管理API
 */
const settingsAPI = {
  async getSettings() {
    return await apiClient.get('/api/v1/settings');
  },

  async updateSettings(settings) {
    return await apiClient.put('/api/v1/settings', { settings });
  },

  async testConnection() {
    return await apiClient.post('/api/v1/settings/test-connection');
  },

  async getLlmStatus() {
    return await apiClient.get('/api/v1/settings/llm-status');
  },

  // 多厂商 LLM Provider 管理
  async getLlmProviders() {
    return await apiClient.get('/api/v1/settings/llm-providers');
  },

  async updateLlmProviders(providers) {
    return await apiClient.put('/api/v1/settings/llm-providers', { providers });
  },

  async setActiveLlmProvider(providerId) {
    return await apiClient.put('/api/v1/settings/llm-active', { provider_id: providerId });
  },

  // 环境变量管理
  async getEnvVars() {
    return await apiClient.get('/api/v1/settings/env-vars');
  },

  async updateEnvVars(envVars) {
    return await apiClient.put('/api/v1/settings/env-vars', { env_vars: envVars });
  },

  // SDK Skills 管理
  async getSkills() {
    return await apiClient.get('/api/v1/settings/skills');
  },

  async uploadSkill(filePath) {
    const base64 = await window.electronAPI.readFileBase64(filePath);
    if (!base64) throw new Error('无法读取文件');
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    const blob = new Blob([bytes], { type: 'application/zip' });
    const formData = new FormData();
    formData.append('file', blob, filePath.split(/[\\/]/).pop());
    return await apiClient.post('/api/v1/settings/skills/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 60000,
    });
  },

  async deleteSkill(name) {
    return await apiClient.delete(`/api/v1/settings/skills/${encodeURIComponent(name)}`);
  },

  async updateSkillStatus(skills) {
    return await apiClient.put('/api/v1/settings/skills/status', { skills });
  },

  // MCP 配置
  async getMcpConfigs() {
    return await apiClient.get('/api/v1/settings/mcp');
  },

  async updateMcpConfigs(configs) {
    return await apiClient.put('/api/v1/settings/mcp', { configs });
  }
};

// 导出为全局对象（用于非模块环境）
window.tdxAPI = tdxAPI;
window.glmAPI = glmAPI;
window.systemAPI = systemAPI;
window.backtestAPI = backtestAPI;
window.settingsAPI = settingsAPI;
window.apiClient = apiClient;

// 监控 API
const monitorAPI = {
    async getStats() {
        return await apiClient.get('/api/v1/agent/monitor/stats');
    },
    async getSessions(page = 1, pageSize = 20, filters = {}) {
        const params = { page, page_size: pageSize };
        if (filters.status) params.status = filters.status;
        if (filters.stock_symbol) params.stock_symbol = filters.stock_symbol;
        return await apiClient.get('/api/v1/agent/monitor/sessions', { params });
    },
    async getSessionDetail(sessionId) {
        return await apiClient.get(`/api/v1/agent/monitor/sessions/${sessionId}`);
    },
    async deleteSession(sessionId) {
        return await apiClient.delete(`/api/v1/agent/monitor/sessions/${sessionId}`);
    },
    async batchDelete(ids) {
        return await apiClient.post('/api/v1/agent/monitor/sessions/batch-delete', { ids });
    }
};
window.monitorAPI = monitorAPI;

// 飞书 Channel API
const channelAPI = {
  async getFeishuStatus() {
    return await apiClient.get('/api/v1/channel/feishu/status');
  },
  async getFeishuConfig() {
    return await apiClient.get('/api/v1/channel/feishu/config');
  },
  async saveFeishuConfig(data) {
    return await apiClient.put('/api/v1/channel/feishu/config', data);
  },
  async startFeishu() {
    return await apiClient.post('/api/v1/channel/feishu/start', {});
  },
  async stopFeishu() {
    return await apiClient.post('/api/v1/channel/feishu/stop', {});
  },

  /**
   * 订阅飞书对话事件（SSE）
   * @param {Function} onEvent - 事件回调 (event) => void
   * @param {Function} onError - 错误回调 (error) => void
   * @returns {AbortController} 用于取消订阅
   */
  subscribeFeishuEvents(onEvent, onError, channelId = null) {
    const controller = new AbortController();
    const url = channelId
      ? `${API_BASE_URL}/api/v1/channel/feishu/events?channel_id=${encodeURIComponent(channelId)}`
      : `${API_BASE_URL}/api/v1/channel/feishu/events`;
    fetch(url, {
      signal: controller.signal
    }).then(response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      function read() {
        reader.read().then(({ done, value }) => {
          if (done) return;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const event = JSON.parse(line.slice(6));
                if (onEvent) onEvent(event);
              } catch (e) {}
            }
          }
          read();
        }).catch(err => {
          if (err.name !== 'AbortError' && onError) onError(err);
        });
      }
      read();
    }).catch(err => {
      if (err.name !== 'AbortError' && onError) onError(err);
    });
    return controller;
  },

  // 多通道管理
  async listChannels() {
    return await apiClient.get('/api/v1/channel/feishu/channels');
  },
  async addChannel(data) {
    return await apiClient.post('/api/v1/channel/feishu/channels', data);
  },
  async updateChannel(channelId, data) {
    return await apiClient.put(`/api/v1/channel/feishu/channels/${channelId}`, data);
  },
  async deleteChannel(channelId) {
    return await apiClient.delete(`/api/v1/channel/feishu/channels/${channelId}`);
  },
  async startChannel(channelId) {
    return await apiClient.post(`/api/v1/channel/feishu/channels/${channelId}/start`, {});
  },
  async stopChannel(channelId) {
    return await apiClient.post(`/api/v1/channel/feishu/channels/${channelId}/stop`, {});
  },
  async sendMessageToFeishu({ receive_id, receive_id_type, text, channel_id }) {
    return await apiClient.post('/api/v1/channel/feishu/send-message', { receive_id, receive_id_type, text, channel_id });
  }
};
window.channelAPI = channelAPI;

// 会话管理 API
const conversationAPI = {
  async listConversations() {
    return await apiClient.get('/api/v1/agent/monitor/conversations');
  },
  async deleteConversation(conversationId) {
    return await apiClient.delete(`/api/v1/agent/monitor/conversations/${encodeURIComponent(conversationId)}`);
  },
  async getMessages(conversationId) {
    return await apiClient.get(`/api/v1/agent/monitor/conversations/${encodeURIComponent(conversationId)}`);
  },
  async clearAll() {
    return await apiClient.delete('/api/v1/agent/monitor/conversations/clear/all');
  }
};
window.conversationAPI = conversationAPI;

// 定时任务 API
const schedulerAPI = {
  async listTasks(keyword) {
    const params = {};
    if (keyword) params.keyword = keyword;
    return await apiClient.get('/api/v1/scheduler/tasks', { params });
  },
  async createTask(data) {
    return await apiClient.post('/api/v1/scheduler/tasks', data);
  },
  async getTask(taskId) {
    return await apiClient.get(`/api/v1/scheduler/tasks/${taskId}`);
  },
  async updateTask(taskId, data) {
    return await apiClient.put(`/api/v1/scheduler/tasks/${taskId}`, data);
  },
  async deleteTask(taskId) {
    return await apiClient.delete(`/api/v1/scheduler/tasks/${taskId}`);
  },
  async triggerTask(taskId) {
    return await apiClient.post(`/api/v1/scheduler/tasks/${taskId}/trigger`, {}, { timeout: 600000 });
  },

  /**
   * 触发任务（SSE 流式进度）
   * @param {number} taskId
   * @param {Function} onProgress - (event) => void  {type, step, detail}
   * @param {Function} onResult - (data) => void
   * @param {Function} onError - (data) => void
   */
  triggerTaskStream(taskId, onProgress, onResult, onError) {
    fetch(API_BASE_URL + `/api/v1/scheduler/tasks/${taskId}/trigger/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    }).then(response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      function read() {
        reader.read().then(({ done, value }) => {
          if (done) return;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const event = JSON.parse(line.slice(6));
                if (event.type === 'progress' && onProgress) {
                  onProgress(event);
                } else if (event.type === 'result' && onResult) {
                  onResult(event.data);
                } else if (event.type === 'error' && onError) {
                  onError(event.data);
                }
              } catch (e) {}
            }
          }
          read();
        }).catch(err => {
          if (onError) onError({ message: err.message });
        });
      }
      read();
    }).catch(err => {
      if (onError) onError({ message: err.message });
    });
  },
  async toggleTask(taskId, enabled) {
    return await apiClient.put(`/api/v1/scheduler/tasks/${taskId}/toggle`, { enabled });
  },
  async getTasksStatus() {
    return await apiClient.get('/api/v1/scheduler/tasks/status');
  }
};
window.schedulerAPI = schedulerAPI;

// 自选股 API
const watchlistAPI = {
  async listItems() {
    return await apiClient.get('/api/v1/watchlist/items');
  },
  async addItem(data) {
    return await apiClient.post('/api/v1/watchlist/items', data);
  },
  async removeItem(id) {
    return await apiClient.delete(`/api/v1/watchlist/items/${id}`);
  },
  async updateItem(id, data) {
    return await apiClient.put(`/api/v1/watchlist/items/${id}`, data);
  },
  async getItemDetail(id) {
    return await apiClient.get(`/api/v1/watchlist/items/${id}/detail`);
  }
};
window.watchlistAPI = watchlistAPI;

// 股票池 API
const stocksAPI = {
  async search(query, limit = 10) {
    return await apiClient.get('/api/v1/stocks/search', { params: { query, limit } });
  },
  async getStatus() {
    return await apiClient.get('/api/v1/stocks/status');
  },
  async sync() {
    return await apiClient.post('/api/v1/stocks/sync', {}, { timeout: 60000 });
  }
};
window.stocksAPI = stocksAPI;

console.log('[API] API module loaded');
