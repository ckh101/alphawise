/**
 * 定时任务调度器
 *
 * 启动时从数据库加载 enabled 的任务，用 node-cron 注册。
 * 到时间后通过 Worker RPC 触发执行。
 */

const cron = require('node-cron');
const db = require('../lib/db');
const { getWorker } = require('./pythonWorker');

const _jobs = new Map();

/**
 * 启动所有 enabled 的定时任务
 */
function startAll() {
  const tasks = db.getSchedulerTasks();
  for (const task of tasks) {
    if (task.enabled) {
      schedule(task);
    }
  }
  console.log(`[scheduler] Started ${_jobs.size} cron jobs`);
}

/**
 * 停止所有调度
 */
function stopAll() {
  for (const [id, job] of _jobs) {
    job.stop();
  }
  _jobs.clear();
  console.log('[scheduler] All cron jobs stopped');
}

/**
 * 注册单个任务的 cron 调度
 */
function schedule(task) {
  unschedule(task.id);

  const expr = task.cron_expression;
  if (!expr) {
    console.warn(`[scheduler] Empty cron for task ${task.id}`);
    return;
  }

  const trigger = () => {
    console.log(`[scheduler] Triggering task ${task.id}: ${task.name}`);
    getWorker().request('scheduler.trigger', { task_id: task.id }).catch((e) => {
      console.error(`[scheduler] Task ${task.id} trigger failed:`, e.message);
    });
  };

  if (expr.startsWith('INTERVAL:')) {
    // interval 模式：按墙钟时间对齐，从 start_time 起，每 intervalMin 分钟触发一次
    const parts = expr.slice(9).split(':');
    const intervalMin = parseInt(parts[1]) || 30;
    const cfg = task.schedule_config || {};
    const startStr = cfg.start_time || '09:30';
    const endStr = cfg.end_time || '11:30';
    const [sh, sm] = startStr.split(':').map(Number);
    const [eh, em] = endStr.split(':').map(Number);
    const startMin = (sh || 0) * 60 + (sm || 0);
    const endMin = (eh || 0) * 60 + (em || 0);
    const weekdays = cfg.weekdays || [1,2,3,4,5];

    // 记录“本次 tick 已触发”，避免同一分钟内重复触发；
    // 同时作为上次触发时刻，用于跨过长时间休眠后补判
    let lastFired = '';

    // 判断某时刻是否应触发：在窗口内、且从 start 起经过整数个 interval、且星期命中
    const shouldFire = (now) => {
      const nowMin = now.getHours() * 60 + now.getMinutes();
      if (nowMin < startMin || nowMin > endMin) return false;
      const jsDow = now.getDay() === 0 ? 7 : now.getDay();
      if (!weekdays.includes(jsDow)) return false;
      const offset = nowMin - startMin;
      return offset % intervalMin === 0;
    };

    const tick = () => {
      const now = new Date();
      // lastFired（时:分）已能防止同一分钟重复触发；不再用秒数挡截——
      // setInterval 相位若落在 31~59 秒，秒数挡截会把所有 tick 全杀掉，任务永不触发。
      const key = `${now.getHours()}:${now.getMinutes()}`;
      if (key === lastFired) return;
      if (!shouldFire(now)) return;
      lastFired = key;
      trigger();
    };

    // 每 60 秒检查一次（足够覆盖所有整分边界）
    const timer = setInterval(tick, 60 * 1000);
    // 启动后立即检查一次（处理“应用在窗口内启动/重启”的场景）
    setImmediate(tick);
    _jobs.set(task.id, { stop: () => clearInterval(timer) });
    console.log(
      `[scheduler] Scheduled task ${task.id} [${task.name}]: every ${intervalMin}min in ${startStr}-${endStr}`
    );
  } else if (cron.validate(expr)) {
    // 标准 cron 模式：自管每分钟轮询 + 整分判断，绕开 node-cron@4 Runner 的
    // “工作日跨周末后 nextRun 被错误推到次年”bug（详见 MatcherWalker.matchNext）。
    // 命中判断只支持 buildCron 生成的 5 段语法：数字 / * / 逗号列表 / a-b 范围。
    const fields = expr.trim().split(/\s+/);
    const parseField = (field, min, max) => {
      if (field === '*') return null; // null = 全部命中
      const set = new Set();
      for (const part of field.split(',')) {
        const m = part.match(/^(\d+)-(\d+)$/);
        if (m) {
          const lo = Math.max(min, +m[1]);
          const hi = Math.min(max, +m[2]);
          for (let v = lo; v <= hi; v++) set.add(v);
        } else if (/^\d+$/.test(part)) {
          set.add(+part);
        } else {
          throw new Error(`bad cron part: ${part}`);
        }
      }
      return set;
    };
    let cronFields;
    try {
      // 注意：标准 crontab 周字段 0=周日、1=周一…6=周六，与 JS Date.getDay() 一致
      cronFields = [
        parseField(fields[0], 0, 59), // minute
        parseField(fields[1], 0, 23), // hour
        parseField(fields[2], 1, 31), // day-of-month
        parseField(fields[3], 1, 12), // month
        parseField(fields[4], 0, 7),  // day-of-week（0 和 7 都视为周日）
      ];
    } catch (e) {
      console.warn(`[scheduler] Bad cron field for task ${task.id}: ${expr} (${e.message})`);
      return;
    }
    const match = (v, allowed) => allowed === null || allowed.has(v);

    let lastFired = '';
    const tick = () => {
      const now = new Date();
      // lastFired（时:分）已能防止同一分钟重复触发；不再用秒数挡截——
      // setInterval 相位若落在 31~59 秒，秒数挡截会把所有 tick 全杀掉，任务永不触发。
      const key = `${now.getHours()}:${now.getMinutes()}`;
      if (key === lastFired) return;
      const jsDow = now.getDay(); // 0=周日…6=周六
      // 周日归一成 7，让表达式里写 0 或 7 都能命中（与 INTERVAL 分支的 1-7 约定一致）
      const hit =
        match(now.getMinutes(), cronFields[0]) &&
        match(now.getHours(), cronFields[1]) &&
        match(now.getDate(), cronFields[2]) &&
        match(now.getMonth() + 1, cronFields[3]) &&
        (match(jsDow, cronFields[4]) || match(7, cronFields[4]));
      if (!hit) return;
      lastFired = key;
      trigger();
    };

    const timer = setInterval(tick, 60 * 1000);
    setImmediate(tick); // 应用在窗口内启动/重启时立即补判一次
    _jobs.set(task.id, { stop: () => clearInterval(timer) });
    console.log(`[scheduler] Scheduled task ${task.id} [${task.name}]: ${expr}`);
  } else {
    console.warn(`[scheduler] Invalid cron for task ${task.id}: ${expr}`);
  }
}

/**
 * 取消单个任务调度
 */
function unschedule(taskId) {
  const job = _jobs.get(taskId);
  if (job) {
    job.stop();
    _jobs.delete(taskId);
  }
}

/**
 * 任务更新后重新调度
 */
function refresh(taskId) {
  const task = db.getSchedulerTask(taskId);
  if (!task) {
    unschedule(taskId);
    return;
  }
  if (task.enabled) {
    schedule(task);
  } else {
    unschedule(taskId);
  }
}

module.exports = { startAll, stopAll, schedule, unschedule, refresh };
