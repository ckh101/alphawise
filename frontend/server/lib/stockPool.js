/**
 * 股票池同步：从 TDX 拉取全量股票名 + 代码，写入 stocks 表
 *
 * 同步策略：
 * - 启动时如果 stocks 表为空，触发一次（异步，不阻塞启动）
 * - 每日凌晨 03:00 自动全量更新一次（node-cron）
 * - 提供 HTTP 接口手动触发
 */

const cron = require('node-cron');
const db = require('../lib/db');
const { getWorker } = require('./pythonWorker');

let _syncing = false;
let _cronJob = null;

/**
 * 全量同步：调用 Python Worker 拉取所有股票，写入 stocks 表
 */
async function syncAll() {
  if (_syncing) return { ok: false, message: 'syncing in progress' };
  _syncing = true;
  console.log('[stockPool] Starting full sync...');
  const start = Date.now();
  try {
    const result = await getWorker().request('tdx.fetch_all_stocks', {});
    if (result.status !== 'ok' || !Array.isArray(result.data)) {
      throw new Error(result.message || 'fetch failed');
    }
    const count = db.upsertStocks(result.data);
    db.setSetting('stocks.last_sync_at', new Date().toISOString());
    const ms = Date.now() - start;
    console.log(`[stockPool] Synced ${count} stocks in ${ms}ms`);
    return { ok: true, count, duration_ms: ms };
  } catch (e) {
    console.error('[stockPool] Sync failed:', e.message);
    return { ok: false, message: e.message };
  } finally {
    _syncing = false;
  }
}

function isSyncing() { return _syncing; }

/**
 * 启动定时任务（每日凌晨 03:00）
 */
function startCron() {
  if (_cronJob) return;
  _cronJob = cron.schedule('0 3 * * *', () => {
    console.log('[stockPool] Cron triggered daily sync');
    syncAll().catch(() => {});
  });
  console.log('[stockPool] Cron scheduled: daily at 03:00');
}

function stopCron() {
  if (_cronJob) { _cronJob.stop(); _cronJob = null; }
}

/**
 * 启动时如果表为空，触发一次后台同步（非阻塞）
 */
function bootstrapIfEmpty() {
  const count = db.getStocksCount();
  if (count === 0) {
    console.log('[stockPool] Stocks table empty, triggering background sync');
    syncAll().catch(() => {});
  } else {
    console.log(`[stockPool] Stocks table has ${count} rows, skip bootstrap`);
  }
}

module.exports = {
  syncAll,
  isSyncing,
  startCron,
  stopCron,
  bootstrapIfEmpty,
};
