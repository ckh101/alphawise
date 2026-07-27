/**
 * SQLite 数据库层 (better-sqlite3)
 *
 * 与 Python 版 backend/harness/core/database.py 完全相同的表结构和函数。
 * 同步 API，WAL 模式，busy_timeout。
 */

const path = require('path');
const fs = require('fs');

// ---------------------------------------------------------------------------
// 数据库路径
// ---------------------------------------------------------------------------

const isDev = process.env.NODE_ENV === 'development';

function getBackendDir() {
  if (isDev) {
    // server/lib/db.js -> frontend/ -> root -> backend/
    return path.resolve(__dirname, '..', '..', '..', 'backend');
  }
  return path.join(process.env.ELECTRON_RESOURCES_PATH, 'backend');
}

function getDbPath() {
  return path.join(getBackendDir(), 'data', 'harness.db');
}

// ---------------------------------------------------------------------------
// 单例
// ---------------------------------------------------------------------------

let _db = null;

function getDb() {
  if (_db) return _db;

  const dbPath = getDbPath();
  fs.mkdirSync(path.dirname(dbPath), { recursive: true });

  const Database = require('better-sqlite3');
  _db = new Database(dbPath);

  _db.pragma('journal_mode = WAL');
  _db.pragma('busy_timeout = 5000');
  _db.pragma('foreign_keys = ON');

  return _db;
}

// ---------------------------------------------------------------------------
// DDL — 建表 + 迁移
// ---------------------------------------------------------------------------

function initDb() {
  const db = getDb();

  db.exec(`
    CREATE TABLE IF NOT EXISTS settings (
      key   TEXT PRIMARY KEY,
      value TEXT NOT NULL DEFAULT '',
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS agent_sessions (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id      TEXT    NOT NULL UNIQUE,
      prompt          TEXT    NOT NULL DEFAULT '',
      response_mode   TEXT    NOT NULL DEFAULT 'chat',
      status          TEXT    NOT NULL DEFAULT 'completed',
      stock_symbol    TEXT    NOT NULL DEFAULT '',
      stock_name      TEXT    NOT NULL DEFAULT '',
      report          TEXT    NOT NULL DEFAULT '',
      thoughts        TEXT    NOT NULL DEFAULT '[]',
      actions         TEXT    NOT NULL DEFAULT '[]',
      tool_plan       TEXT    NOT NULL DEFAULT '[]',
      tool_calls_count INTEGER NOT NULL DEFAULT 0,
      duration        REAL    NOT NULL DEFAULT 0.0,
      error_message   TEXT    NOT NULL DEFAULT '',
      created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
      conversation_id TEXT,
      turn_number     INTEGER NOT NULL DEFAULT 1
    );

    CREATE INDEX IF NOT EXISTS ix_agent_sessions_session_id
      ON agent_sessions(session_id);
    CREATE INDEX IF NOT EXISTS ix_agent_sessions_conversation_id
      ON agent_sessions(conversation_id);

    CREATE TABLE IF NOT EXISTS scheduler_tasks (
      id                INTEGER PRIMARY KEY AUTOINCREMENT,
      name              TEXT    NOT NULL DEFAULT '',
      prompt            TEXT    NOT NULL,
      cron_expression   TEXT    NOT NULL DEFAULT '',
      schedule_type     TEXT    NOT NULL DEFAULT 'weekly',
      schedule_config   TEXT    NOT NULL DEFAULT '{}',
      receive_id        TEXT    NOT NULL DEFAULT '',
      receive_id_type   TEXT    NOT NULL DEFAULT 'chat_id',
      feishu_channel_id TEXT    NOT NULL DEFAULT '',
      start_date        TEXT    NOT NULL DEFAULT '',
      end_date          TEXT    NOT NULL DEFAULT '',
      enabled           TEXT    NOT NULL DEFAULT 'true',
      last_run_at       TEXT,
      last_run_status   TEXT    NOT NULL DEFAULT '',
      last_run_session_id TEXT  NOT NULL DEFAULT '',
      run_count         INTEGER NOT NULL DEFAULT 0,
      created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
      updated_at        TEXT    NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS llm_calls (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id      TEXT,
      phase           TEXT    NOT NULL DEFAULT '',
      model           TEXT    NOT NULL DEFAULT '',
      input_messages  TEXT    NOT NULL DEFAULT '',
      output_content  TEXT    NOT NULL DEFAULT '',
      duration_ms     INTEGER NOT NULL DEFAULT 0,
      created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS ix_llm_calls_session_id
      ON llm_calls(session_id);
  `);

  // ---- 内联迁移（与 Python 版 _run_migrations 对齐） ----
  runMigrations(db);

  console.log(`[db] Initialized: ${getDbPath()}`);
}

function runMigrations(db) {
  // agent_sessions: conversation_id
  const cols = db.prepare("PRAGMA table_info(agent_sessions)").all();
  const colNames = new Set(cols.map(c => c.name));

  if (!colNames.has('conversation_id')) {
    db.exec(`
      ALTER TABLE agent_sessions ADD COLUMN conversation_id TEXT;
    `);
    db.exec(`
      CREATE INDEX IF NOT EXISTS ix_agent_sessions_conversation_id
        ON agent_sessions(conversation_id);
    `);
    db.prepare(
      "UPDATE agent_sessions SET conversation_id = session_id WHERE conversation_id IS NULL"
    ).run();
  }

  if (!colNames.has('turn_number')) {
    db.exec(`ALTER TABLE agent_sessions ADD COLUMN turn_number INTEGER DEFAULT 1`);
  }

  // scheduler_tasks: use_channel_push_targets
  const taskCols = db.prepare("PRAGMA table_info(scheduler_tasks)").all();
  const taskColNames = new Set(taskCols.map(c => c.name));
  if (!taskColNames.has('use_channel_push_targets')) {
    db.exec(`ALTER TABLE scheduler_tasks ADD COLUMN use_channel_push_targets TEXT NOT NULL DEFAULT 'false'`);
  }

  // watchlist 表
  db.exec(`
    CREATE TABLE IF NOT EXISTS watchlist (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      symbol TEXT NOT NULL,
      name TEXT NOT NULL DEFAULT '',
      group_name TEXT NOT NULL DEFAULT '默认',
      sort_order INTEGER NOT NULL DEFAULT 0,
      notes TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
  `);
  db.exec(`CREATE INDEX IF NOT EXISTS ix_watchlist_symbol ON watchlist(symbol)`);

  // 股票池表（基础信息，每日同步）
  db.exec(`
    CREATE TABLE IF NOT EXISTS stocks (
      code TEXT NOT NULL,
      name TEXT NOT NULL DEFAULT '',
      market TEXT NOT NULL DEFAULT '',
      symbol TEXT NOT NULL,
      updated_at TEXT NOT NULL DEFAULT (datetime('now')),
      PRIMARY KEY (code)
    );
  `);
  db.exec(`CREATE INDEX IF NOT EXISTS ix_stocks_name ON stocks(name)`);
  db.exec(`CREATE INDEX IF NOT EXISTS ix_stocks_symbol ON stocks(symbol)`);
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

function getSetting(key, defaultVal = null) {
  const db = getDb();
  const row = db.prepare('SELECT value FROM settings WHERE key = ?').get(key);
  if (row && row.value !== '') return row.value;
  return defaultVal;
}

function setSetting(key, value) {
  const db = getDb();
  db.prepare(`
    INSERT INTO settings (key, value, updated_at)
    VALUES (?, ?, datetime('now'))
    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
  `).run(key, value);
}

function getAllSettings(prefix = '') {
  const db = getDb();
  let stmt;
  let rows;
  if (prefix) {
    stmt = db.prepare("SELECT key, value FROM settings WHERE key LIKE ?");
    rows = stmt.all(prefix + '%');
  } else {
    stmt = db.prepare('SELECT key, value FROM settings');
    rows = stmt.all();
  }
  const result = {};
  for (const r of rows) result[r.key] = r.value;
  return result;
}

function updateSettings(settings) {
  const db = getDb();
  const upsert = db.prepare(`
    INSERT INTO settings (key, value, updated_at)
    VALUES (?, ?, datetime('now'))
    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
  `);
  const batch = db.transaction((entries) => {
    for (const [key, value] of entries) upsert.run(key, value);
  });
  batch(Object.entries(settings));
}

// ---------------------------------------------------------------------------
// Environment Variable Management (env. prefix in settings table)
// ---------------------------------------------------------------------------

const ENV_PREFIX = 'env.';

function getEnvVars() {
  const all = getAllSettings(ENV_PREFIX);
  const result = {};
  for (const [key, value] of Object.entries(all)) {
    result[key.slice(ENV_PREFIX.length)] = value;
  }
  return result;
}

function saveEnvVars(envVars) {
  const batch = {};
  for (const [k, v] of Object.entries(envVars)) {
    batch[ENV_PREFIX + k] = String(v);
  }
  updateSettings(batch);
}

function deleteEnvVars(keys) {
  const db = getDb();
  const del = db.prepare('DELETE FROM settings WHERE key = ?');
  const batch = db.transaction((entries) => {
    for (const k of entries) del.run(ENV_PREFIX + k);
  });
  batch(keys);
}

// ---------------------------------------------------------------------------
// LLM config helpers
// ---------------------------------------------------------------------------

function getLlmConfig() {
  const provider = getActiveLlmProvider();
  if (provider) {
    return {
      'llm.api_key': provider.api_key || '',
      'llm.base_url': provider.base_url || '',
      'llm.model': provider.model || '',
      'llm.timeout': String(provider.timeout || 600),
    };
  }
  return {};
}

function isLlmConfigured() {
  const config = getLlmConfig();
  return !!(config['llm.api_key'] && config['llm.model']);
}

// ---------------------------------------------------------------------------
// 多厂商 Provider 管理
// ---------------------------------------------------------------------------

function getLlmProviders() {
  const raw = getSetting('llm.providers', '[]');
  try {
    const providers = JSON.parse(raw);
    return Array.isArray(providers) ? providers : [];
  } catch {
    return [];
  }
}

function saveLlmProviders(providers) {
  setSetting('llm.providers', JSON.stringify(providers));
}

function getActiveLlmProvider() {
  const providers = getLlmProviders();
  const activeId = getSetting('llm.active', '');
  if (!activeId || !providers.length) return null;
  const found = providers.find(p => p.id === activeId);
  return found || (providers.length ? providers[0] : null);
}

function setActiveLlmProvider(providerId) {
  setSetting('llm.active', providerId);
}

// ---------------------------------------------------------------------------
// Agent Session
// ---------------------------------------------------------------------------

function saveAgentSession(data) {
  const db = getDb();

  // 精简 actions 中的 result 字段
  const actionsRaw = data.actions || [];
  const actionsClean = actionsRaw.map(a => {
    const item = {
      tool_name: a.tool_name || '',
      arguments: a.arguments || {},
      timestamp: a.timestamp || '',
      execution_time: a.execution_time || 0,
    };
    if (a.error) item.error = a.error;
    if (a.result !== undefined && a.result !== null) {
      const resultStr = JSON.stringify(a.result);
      item.result_summary = resultStr.slice(0, 500);
    }
    return item;
  });

  try {
    const result = db.prepare(`
      INSERT INTO agent_sessions (
        session_id, conversation_id, turn_number,
        prompt, response_mode, status,
        stock_symbol, stock_name,
        report, thoughts, actions, tool_plan,
        tool_calls_count, duration, error_message
      ) VALUES (
        ?, ?, ?,
        ?, ?, ?,
        ?, ?,
        ?, ?, ?, ?,
        ?, ?, ?
      )
    `).run(
      data.session_id,
      data.conversation_id || data.session_id,
      data.turn_number || 1,
      data.prompt || '',
      data.response_mode || 'chat',
      data.status || 'completed',
      data.stock_symbol || '',
      data.stock_name || '',
      (data.report || '').slice(0, 2000),
      JSON.stringify(data.thoughts || []),
      JSON.stringify(actionsClean),
      JSON.stringify(data.tool_plan || []),
      actionsRaw.length,
      data.duration || 0.0,
      data.error_message || '',
    );

    return result.lastInsertRowid;
  } catch (err) {
    console.error('[db] Failed to save agent session:', err.message);
    return -1;
  }
}

// ---------------------------------------------------------------------------
// LLM Call
// ---------------------------------------------------------------------------

function saveLlmCall(data) {
  const db = getDb();
  try {
    db.prepare(`
      INSERT INTO llm_calls (session_id, phase, model, input_messages, output_content, duration_ms)
      VALUES (?, ?, ?, ?, ?, ?)
    `).run(
      data.session_id || '',
      data.phase || '',
      data.model || '',
      (data.input_messages || '').slice(0, 10000),
      (data.output_content || '').slice(0, 5000),
      data.duration_ms || 0,
    );
  } catch (err) {
    console.warn('[db] Failed to save LLM call:', err.message);
  }
}

// ---------------------------------------------------------------------------
// Scheduler Tasks
// ---------------------------------------------------------------------------

function getSchedulerTasks(keyword) {
  const db = getDb();
  let rows;
  if (keyword) {
    const pattern = `%${keyword}%`;
    rows = db.prepare(
      "SELECT * FROM scheduler_tasks WHERE name LIKE ? OR prompt LIKE ? ORDER BY id DESC"
    ).all(pattern, pattern);
  } else {
    rows = db.prepare('SELECT * FROM scheduler_tasks ORDER BY id DESC').all();
  }
  return rows.map(taskRowToObj);
}

function getSchedulerTask(taskId) {
  const db = getDb();
  const row = db.prepare('SELECT * FROM scheduler_tasks WHERE id = ?').get(taskId);
  return row ? taskRowToObj(row) : null;
}

function createSchedulerTask(data) {
  const db = getDb();
  const result = db.prepare(`
    INSERT INTO scheduler_tasks (
      name, prompt, cron_expression, schedule_type, schedule_config,
      receive_id, receive_id_type, feishu_channel_id, use_channel_push_targets,
      start_date, end_date, enabled
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    data.name || '',
    data.prompt,
    data.cron_expression || '',
    data.schedule_type || 'weekly',
    JSON.stringify(data.schedule_config || {}),
    data.receive_id || '',
    data.receive_id_type || 'chat_id',
    data.feishu_channel_id || '',
    data.use_channel_push_targets ? 'true' : 'false',
    data.start_date || '',
    data.end_date || '',
    data.enabled !== false ? 'true' : 'false',
  );
  return getSchedulerTask(Number(result.lastInsertRowid));
}

function updateSchedulerTask(taskId, data) {
  const db = getDb();
  const sets = [];
  const vals = [];

  const fields = [
    'name', 'prompt', 'cron_expression', 'schedule_type', 'schedule_config',
    'receive_id', 'receive_id_type', 'feishu_channel_id', 'use_channel_push_targets',
    'start_date', 'end_date',
  ];

  for (const f of fields) {
    if (data[f] !== undefined) {
      sets.push(`${f} = ?`);
      let val = data[f];
      if (f === 'schedule_config') val = JSON.stringify(data[f]);
      else if (f === 'use_channel_push_targets') val = data[f] ? 'true' : 'false';
      // start_date/end_date 等列为 NOT NULL，前端传 null 时归一为 ''（与 INSERT 路径一致），
      // 避免 "NOT NULL constraint failed"
      else if (val === null) val = '';
      vals.push(val);
    }
  }
  if (data.enabled !== undefined) {
    sets.push('enabled = ?');
    vals.push(data.enabled ? 'true' : 'false');
  }

  if (sets.length === 0) return getSchedulerTask(taskId);

  sets.push("updated_at = datetime('now')");
  vals.push(taskId);

  db.prepare(`UPDATE scheduler_tasks SET ${sets.join(', ')} WHERE id = ?`).run(...vals);
  return getSchedulerTask(taskId);
}

function deleteSchedulerTask(taskId) {
  const db = getDb();
  const result = db.prepare('DELETE FROM scheduler_tasks WHERE id = ?').run(taskId);
  return result.changes > 0;
}

function updateSchedulerTaskRun(taskId, runData) {
  const db = getDb();
  db.prepare(`
    UPDATE scheduler_tasks
    SET last_run_at = datetime('now'),
        last_run_status = ?,
        last_run_session_id = ?,
        run_count = run_count + 1,
        updated_at = datetime('now')
    WHERE id = ?
  `).run(
    runData.status || '',
    runData.session_id || '',
    taskId,
  );
}

/** 将 scheduler_tasks 行转为与 Python 版 _task_to_dict 一致的对象 */
function taskRowToObj(row) {
  return {
    id: row.id,
    name: row.name,
    prompt: row.prompt,
    cron_expression: row.cron_expression,
    schedule_type: row.schedule_type,
    schedule_config: JSON.parse(row.schedule_config || '{}'),
    receive_id: row.receive_id,
    receive_id_type: row.receive_id_type,
    feishu_channel_id: row.feishu_channel_id,
    use_channel_push_targets: (row.use_channel_push_targets === 'true'),
    start_date: row.start_date,
    end_date: row.end_date,
    enabled: row.enabled === 'true',
    last_run_at: row.last_run_at || null,
    last_run_status: row.last_run_status,
    last_run_session_id: row.last_run_session_id,
    run_count: row.run_count,
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}

// ---------------------------------------------------------------------------
// Monitor — 统计 & 查询
// ---------------------------------------------------------------------------

function getMonitorStats() {
  const db = getDb();

  const total = db.prepare('SELECT COUNT(*) AS c FROM agent_sessions').get().c;
  const completed = db.prepare("SELECT COUNT(*) AS c FROM agent_sessions WHERE status = 'completed'").get().c;
  const avgDuration = db.prepare('SELECT AVG(duration) AS v FROM agent_sessions').get().v || 0;

  // 工具使用频率
  const allActions = db.prepare('SELECT actions FROM agent_sessions').all();
  const toolCounts = {};
  for (const { actions } of allActions) {
    try {
      const list = JSON.parse(actions || '[]');
      for (const a of list) {
        const name = a.tool_name || 'unknown';
        toolCounts[name] = (toolCounts[name] || 0) + 1;
      }
    } catch (_) { /* skip */ }
  }
  const topTools = Object.entries(toolCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5)
    .map(([name, count]) => ({ name, count }));

  // 最近 7 天每日对话数
  const daily = db.prepare(`
    SELECT strftime('%Y-%m-%d', created_at) AS d, COUNT(*) AS c
    FROM agent_sessions
    GROUP BY d
    ORDER BY d DESC
    LIMIT 7
  `).all();
  const dailyCounts = daily.filter(r => r.d).map(r => ({ date: r.d, count: r.c }));

  return {
    total_sessions: total,
    completed,
    failed: total - completed,
    success_rate: total ? Math.round(completed / total * 1000) / 10 : 0,
    avg_duration: Math.round(avgDuration * 10) / 10,
    top_tools: topTools,
    daily_counts: dailyCounts,
  };
}

function listSessions({ page = 1, pageSize = 20, status, stockSymbol } = {}) {
  const db = getDb();
  const conditions = [];
  const params = [];

  if (status) {
    conditions.push('status = ?');
    params.push(status);
  }
  if (stockSymbol) {
    conditions.push('stock_symbol LIKE ?');
    params.push(`%${stockSymbol}%`);
  }

  const where = conditions.length ? `WHERE ${conditions.join(' AND ')}` : '';

  const total = db.prepare(`SELECT COUNT(*) AS c FROM agent_sessions ${where}`).get(...params).c;

  const offset = (page - 1) * pageSize;
  params.push(pageSize, offset);
  const rows = db.prepare(
    `SELECT * FROM agent_sessions ${where} ORDER BY created_at DESC LIMIT ? OFFSET ?`
  ).all(...params);

  const items = rows.map(r => ({
    id: r.id,
    session_id: r.session_id,
    conversation_id: r.conversation_id || r.session_id,
    turn_number: r.turn_number || 1,
    prompt: (r.prompt || '').slice(0, 100),
    response_mode: r.response_mode,
    status: r.status,
    stock_symbol: r.stock_symbol,
    stock_name: r.stock_name,
    tool_calls_count: r.tool_calls_count,
    duration: r.duration,
    created_at: r.created_at,
  }));

  return { items, total, page, page_size: pageSize };
}

function getSessionDetail(sessionId) {
  const db = getDb();

  let row = db.prepare('SELECT * FROM agent_sessions WHERE session_id = ?').get(sessionId);
  if (!row) {
    const numId = Number(sessionId);
    if (Number.isInteger(numId)) {
      row = db.prepare('SELECT * FROM agent_sessions WHERE id = ?').get(numId);
    }
  }
  if (!row) return null;

  const llmRows = db.prepare(
    'SELECT * FROM llm_calls WHERE session_id = ? ORDER BY created_at'
  ).all(row.session_id);

  const llmCalls = llmRows.map(lc => ({
    phase: lc.phase,
    model: lc.model,
    input_preview: (lc.input_messages || '').slice(0, 500),
    output_preview: (lc.output_content || '').slice(0, 500),
    duration_ms: lc.duration_ms,
    created_at: lc.created_at,
  }));

  return {
    id: row.id,
    session_id: row.session_id,
    conversation_id: row.conversation_id || row.session_id,
    turn_number: row.turn_number || 1,
    prompt: row.prompt,
    response_mode: row.response_mode,
    status: row.status,
    stock_symbol: row.stock_symbol,
    stock_name: row.stock_name,
    report: row.report,
    thoughts: JSON.parse(row.thoughts || '[]'),
    actions: JSON.parse(row.actions || '[]'),
    tool_plan: JSON.parse(row.tool_plan || '[]'),
    tool_calls_count: row.tool_calls_count,
    duration: row.duration,
    error_message: row.error_message,
    llm_calls: llmCalls,
    created_at: row.created_at,
  };
}

function deleteSession(sessionId) {
  const db = getDb();
  const row = db.prepare('SELECT session_id FROM agent_sessions WHERE session_id = ?').get(sessionId);
  if (!row) return false;
  db.prepare('DELETE FROM llm_calls WHERE session_id = ?').run(sessionId);
  db.prepare('DELETE FROM agent_sessions WHERE session_id = ?').run(sessionId);
  return true;
}

function batchDeleteSessions(ids) {
  const db = getDb();
  if (!ids || ids.length === 0) return 0;

  const sids = db.prepare(
    `SELECT session_id FROM agent_sessions WHERE id IN (${ids.map(() => '?').join(',')})`
  ).all(...ids).map(r => r.session_id);

  if (sids.length > 0) {
    db.prepare(
      `DELETE FROM llm_calls WHERE session_id IN (${sids.map(() => '?').join(',')})`
    ).run(...sids);
  }

  const result = db.prepare(
    `DELETE FROM agent_sessions WHERE id IN (${ids.map(() => '?').join(',')})`
  ).run(...ids);

  return result.changes;
}

// ---------------------------------------------------------------------------
// Conversations — 按 conversation_id 分组
// ---------------------------------------------------------------------------

function listConversations() {
  const db = getDb();
  // 每组取最新一条 + 轮次数
  const rows = db.prepare(`
    SELECT
      a.*,
      sub.turn_count
    FROM agent_sessions a
    JOIN (
      SELECT conversation_id, MAX(id) AS max_id, COUNT(*) AS turn_count
      FROM agent_sessions
      WHERE conversation_id IS NOT NULL
      GROUP BY conversation_id
    ) sub ON a.id = sub.max_id
    ORDER BY a.created_at DESC
    LIMIT 200
  `).all();

  return rows.map(r => ({
    conversation_id: r.conversation_id,
    title: (r.prompt || '').slice(0, 60),
    created_at: r.created_at,
    turn_count: r.turn_count,
    stock_symbol: r.stock_symbol,
    stock_name: r.stock_name,
    response_mode: r.response_mode,
  }));
}

function getConversationMessages(conversationId) {
  const db = getDb();
  const rows = db.prepare(
    'SELECT * FROM agent_sessions WHERE conversation_id = ? ORDER BY turn_number'
  ).all(conversationId);

  if (rows.length === 0) return null;

  const messages = [];
  for (const r of rows) {
    messages.push({ role: 'user', content: r.prompt || '' });
    if (r.report) {
      messages.push({ role: 'assistant', content: r.report });
    }
  }

  return {
    conversation_id: conversationId,
    messages,
    turn_count: rows.length,
  };
}

function deleteConversation(conversationId) {
  const db = getDb();
  const sids = db.prepare(
    'SELECT session_id FROM agent_sessions WHERE conversation_id = ?'
  ).all(conversationId).map(r => r.session_id);

  if (sids.length > 0) {
    db.prepare(
      `DELETE FROM llm_calls WHERE session_id IN (${sids.map(() => '?').join(',')})`
    ).run(...sids);
  }
  const result = db.prepare(
    'DELETE FROM agent_sessions WHERE conversation_id = ?'
  ).run(conversationId);

  return result.changes;
}

function clearAllConversations() {
  const db = getDb();
  db.prepare('DELETE FROM llm_calls').run();
  const result = db.prepare('DELETE FROM agent_sessions').run();
  return result.changes;
}

// ---------------------------------------------------------------------------
// Watchlist — 自选股
// ---------------------------------------------------------------------------

function getWatchlistItems() {
  const db = getDb();
  return db.prepare(
    'SELECT * FROM watchlist ORDER BY group_name, sort_order, id DESC'
  ).all();
}

function getWatchlistItem(id) {
  const db = getDb();
  return db.prepare('SELECT * FROM watchlist WHERE id = ?').get(id);
}

function addWatchlistItem({ symbol, name, group_name, notes }) {
  const db = getDb();
  // 补全市场后缀
  let sym = (symbol || '').trim().toUpperCase();
  if (/^\d{6}$/.test(sym)) {
    if (sym.startsWith('6')) sym += '.SH';
    else if (sym.startsWith('0') || sym.startsWith('3')) sym += '.SZ';
    else if (sym.startsWith('8') || sym.startsWith('4')) sym += '.BJ';
  }
  // 去重
  const existing = db.prepare('SELECT id FROM watchlist WHERE symbol = ?').get(sym);
  if (existing) return getWatchlistItem(existing.id);

  // 如果没传 name，从 stocks 表同步查
  let finalName = name || '';
  if (!finalName) {
    const stock = db.prepare('SELECT name FROM stocks WHERE symbol = ?').get(sym);
    if (stock && stock.name) finalName = stock.name;
  }

  const result = db.prepare(
    'INSERT INTO watchlist (symbol, name, group_name, notes) VALUES (?, ?, ?, ?)'
  ).run(sym, finalName, group_name || '默认', notes || '');
  return getWatchlistItem(Number(result.lastInsertRowid));
}

function deleteWatchlistItem(id) {
  const db = getDb();
  const r = db.prepare('DELETE FROM watchlist WHERE id = ?').run(id);
  return r.changes > 0;
}

function updateWatchlistItem(id, data) {
  const db = getDb();
  const sets = [];
  const vals = [];
  for (const f of ['name', 'group_name', 'notes', 'sort_order']) {
    if (data[f] !== undefined) { sets.push(`${f} = ?`); vals.push(data[f]); }
  }
  if (!sets.length) return getWatchlistItem(id);
  vals.push(id);
  db.prepare(`UPDATE watchlist SET ${sets.join(', ')} WHERE id = ?`).run(...vals);
  return getWatchlistItem(id);
}

// ---------------------------------------------------------------------------
// Stocks — 全量股票池（基础信息）
// ---------------------------------------------------------------------------

function getStocksCount() {
  const db = getDb();
  return db.prepare('SELECT COUNT(*) AS c FROM stocks').get().c;
}

function getStocksLastSyncAt() {
  return getSetting('stocks.last_sync_at', null);
}

function getStockBySymbol(symbol) {
  const db = getDb();
  return db.prepare('SELECT code, name, market, symbol FROM stocks WHERE symbol = ?').get(symbol) || null;
}

function searchStocks(query, limit = 10) {
  const db = getDb();
  const q = (query || '').trim();
  if (!q) return [];

  // 提取数字部分用于 code 模糊匹配
  const numPart = q.replace(/[^\d]/g, '');
  const pattern = `%${q}%`;

  // UNION 三种匹配：精确名称 → 名称包含 → 代码包含；分别打分
  let sql, params;
  if (numPart) {
    sql = `
      SELECT code, name, market, symbol FROM (
        SELECT *, 1 AS score FROM stocks WHERE name = ?
        UNION ALL
        SELECT *, 2 AS score FROM stocks WHERE name LIKE ? AND name != ?
        UNION ALL
        SELECT *, 3 AS score FROM stocks WHERE code LIKE ?
      )
      GROUP BY code
      ORDER BY MIN(score), code
      LIMIT ?
    `;
    params = [q, pattern, q, `%${numPart}%`, limit];
  } else {
    sql = `
      SELECT code, name, market, symbol FROM (
        SELECT *, 1 AS score FROM stocks WHERE name = ?
        UNION ALL
        SELECT *, 2 AS score FROM stocks WHERE name LIKE ? AND name != ?
      )
      GROUP BY code
      ORDER BY MIN(score), code
      LIMIT ?
    `;
    params = [q, pattern, q, limit];
  }

  return db.prepare(sql).all(...params);
}

function upsertStocks(items) {
  if (!items || !items.length) return 0;
  const db = getDb();
  const stmt = db.prepare(`
    INSERT INTO stocks (code, name, market, symbol, updated_at)
    VALUES (?, ?, ?, ?, datetime('now'))
    ON CONFLICT(code) DO UPDATE SET
      name = excluded.name,
      market = excluded.market,
      symbol = excluded.symbol,
      updated_at = datetime('now')
  `);
  const tx = db.transaction((rows) => {
    for (const r of rows) stmt.run(r.code, r.name, r.market, r.symbol);
  });
  tx(items);
  return items.length;
}

// ---------------------------------------------------------------------------
// SDK Skills
// ---------------------------------------------------------------------------

const BUILTIN_SKILL_PREFIXES = ['mx-'];

function isBuiltinSkill(name) {
  return BUILTIN_SKILL_PREFIXES.some(p => name.startsWith(p));
}

function getDisabledSdkSkills() {
  const all = getAllSettings('skill.sdk.');
  const disabled = [];
  for (const [key, value] of Object.entries(all)) {
    if (value.toLowerCase() === 'false') {
      disabled.push(key.replace('skill.sdk.', ''));
    }
  }
  return disabled;
}

function setSkillEnabled(name, enabled) {
  setSetting(`skill.sdk.${name}`, String(enabled));
}

// ---------------------------------------------------------------------------
// MCP Configs
// ---------------------------------------------------------------------------

function getMcpConfigs() {
  const raw = getSetting('mcp.servers');
  if (!raw) return [];
  try {
    return JSON.parse(raw);
  } catch (_) {
    return [];
  }
}

function setMcpConfigs(configs) {
  setSetting('mcp.servers', JSON.stringify(configs));
}

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

module.exports = {
  getDb,
  initDb,

  // Settings
  getSetting,
  setSetting,
  getAllSettings,
  updateSettings,
  getLlmConfig,
  isLlmConfigured,
  getLlmProviders,
  saveLlmProviders,
  getActiveLlmProvider,
  setActiveLlmProvider,

  // Env Vars
  getEnvVars,
  saveEnvVars,
  deleteEnvVars,

  // Agent Session
  saveAgentSession,

  // LLM Call
  saveLlmCall,

  // Scheduler Tasks
  getSchedulerTasks,
  getSchedulerTask,
  createSchedulerTask,
  updateSchedulerTask,
  deleteSchedulerTask,
  updateSchedulerTaskRun,

  // Monitor
  getMonitorStats,
  listSessions,
  getSessionDetail,
  deleteSession,
  batchDeleteSessions,

  // Conversations
  listConversations,
  getConversationMessages,
  deleteConversation,
  clearAllConversations,

  // SDK Skills
  isBuiltinSkill,
  getDisabledSdkSkills,
  setSkillEnabled,

  // MCP
  getMcpConfigs,
  setMcpConfigs,

  // Watchlist
  getWatchlistItems,
  getWatchlistItem,
  addWatchlistItem,
  deleteWatchlistItem,
  updateWatchlistItem,

  // Stocks (全量股票池)
  getStocksCount,
  getStocksLastSyncAt,
  getStockBySymbol,
  searchStocks,
  upsertStocks,
};
