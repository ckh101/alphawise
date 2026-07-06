"""
定时任务调度器

使用 APScheduler AsyncIOScheduler 管理定时任务，
复用 ReactOrchestrator 执行分析，通过飞书主动发送消息推送结果。
"""

import asyncio
import json
from datetime import datetime, time as dtime
from typing import Callable, Dict, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from zoneinfo import ZoneInfo

from harness.core.database import get_session
from harness.core.database import SchedulerTask as SchedulerTaskModel
from harness.core.logger import get_logger

logger = get_logger(__name__)


def build_cron(schedule_type: str, schedule_config: dict) -> str:
    """将前端配置转为标准 cron 表达式"""
    time_str = schedule_config.get("time", "09:00")
    parts = time_str.split(":")
    hour = parts[0] if len(parts) > 0 else "9"
    minute = parts[1] if len(parts) > 1 else "0"

    if schedule_type == "weekly":
        days = schedule_config.get("weekdays", [1, 2, 3, 4, 5])
        day_map = {1: "mon", 2: "tue", 3: "wed", 4: "thu", 5: "fri", 6: "sat", 7: "sun"}
        dow = ",".join(day_map[d] for d in sorted(days) if d in day_map)
        return f"{minute} {hour} * * {dow}"

    elif schedule_type == "monthly":
        day_from = schedule_config.get("day_from", 1)
        day_to = schedule_config.get("day_to", 31)
        return f"{minute} {hour} {day_from}-{day_to} * *"

    elif schedule_type == "once":
        date = schedule_config.get("date", "")
        if date:
            d = datetime.strptime(date, "%Y-%m-%d")
            return f"{minute} {hour} {d.day} {d.month} *"
        return f"{minute} {hour} * * *"

    elif schedule_type == "interval":
        start = schedule_config.get("start_time", "09:30").replace(":", "")
        end = schedule_config.get("end_time", "11:30").replace(":", "")
        interval = schedule_config.get("interval_minutes", 30)
        return f"INTERVAL:{start}-{end}:{interval}"

    return f"{minute} {hour} * * *"


class TaskScheduler:
    """定时任务调度器"""

    def __init__(self) -> None:
        self._scheduler: Optional[AsyncIOScheduler] = None
        self._run_status: Dict[int, dict] = {}

    def start(self) -> None:
        """启动调度器，从数据库加载所有 enabled 任务"""
        # 幂等：避免 worker 重复启动时 APScheduler 报 "already started"
        if self._scheduler and self._scheduler.running:
            return
        # 必须显式指定本地时区：APScheduler 默认 UTC，
        # 否则 cron "15 9 * * 1-5" 会被当作 UTC 09:15（=北京 17:15）触发。
        self._scheduler = AsyncIOScheduler(timezone=ZoneInfo("Asia/Shanghai"))
        self._load_tasks_from_db()
        self._scheduler.start()
        logger.info("[scheduler] Started, tasks loaded")

    def shutdown(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            logger.info("[scheduler] Shutdown")

    def get_all_status(self) -> Dict[int, dict]:
        """获取所有任务的实时执行状态"""
        return dict(self._run_status)

    def _load_tasks_from_db(self) -> None:
        """从数据库加载所有 enabled 任务并注册"""
        session = get_session()
        try:
            tasks = session.query(SchedulerTaskModel).filter(
                SchedulerTaskModel.enabled == "true"
            ).all()
            for task in tasks:
                self._register_job(task)
            logger.info(f"[scheduler] Loaded {len(tasks)} tasks from DB")
        except Exception as e:
            logger.error(f"[scheduler] Failed to load tasks: {e}")
        finally:
            session.close()

    def _register_job(self, task: SchedulerTaskModel) -> None:
        """注册单个任务到 APScheduler"""
        if not self._scheduler or not task.cron_expression:
            return
        job_id = f"scheduler_task_{task.id}"
        try:
            cron_expr = task.cron_expression
            if cron_expr.startswith("INTERVAL:"):
                # interval 模式：解析 "INTERVAL:0930-1130:30"
                parts = cron_expr[len("INTERVAL:"):].split(":")
                time_range = parts[0]  # "0930-1130"
                interval_min = int(parts[1]) if len(parts) > 1 else 30
                # 用 interval trigger，每 N 分钟触发一次
                trigger = IntervalTrigger(minutes=interval_min)
            else:
                trigger = CronTrigger.from_crontab(cron_expr)

            self._scheduler.add_job(
                self._execute_task,
                trigger=trigger,
                args=[task.id],
                id=job_id,
                replace_existing=True,
                misfire_grace_time=300,
            )
            logger.info(f"[scheduler] Registered job: id={task.id}, cron={cron_expr}")
        except Exception as e:
            logger.error(f"[scheduler] Failed to register job {task.id}: {e}")

    def add_task(self, task: SchedulerTaskModel) -> None:
        """新增任务时注册到调度器"""
        if task.enabled == "true":
            self._register_job(task)

    def remove_task(self, task_id: int) -> None:
        """删除任务时从调度器移除"""
        if not self._scheduler:
            return
        job_id = f"scheduler_task_{task_id}"
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass

    def update_task(self, task: SchedulerTaskModel) -> None:
        """更新任务：先移除再重新注册"""
        self.remove_task(task.id)
        if task.enabled == "true":
            self._register_job(task)

    def _update_status(self, task_id: int, step: str, detail: str) -> None:
        """更新任务执行状态"""
        self._run_status[task_id] = {
            "step": step,
            "detail": detail,
            "updated_at": datetime.now().isoformat(),
        }
        logger.info(f"[scheduler] Task {task_id} status: {step} - {detail}")

    async def _execute_task(self, task_id: int) -> None:
        """定时任务执行入口（被 APScheduler 调用）"""
        logger.info(f"[scheduler] Executing task id={task_id}")
        await self._run_task(task_id, push_feishu=True)

    async def _run_task(
        self,
        task_id: int,
        push_feishu: bool = True,
        on_progress: Optional[Callable[[str, str], None]] = None,
    ) -> dict:
        """执行任务核心逻辑"""
        def _emit(step: str, detail: str) -> None:
            self._update_status(task_id, step, detail)
            if on_progress:
                on_progress(step, detail)

        session = get_session()
        try:
            task = session.query(SchedulerTaskModel).filter_by(id=task_id).first()
            if not task:
                _emit("failed", "任务不存在")
                return {"status": "failed", "message": "任务不存在"}

            _emit("init", f"正在初始化: {task.name or '未命名任务'}")

            today = datetime.now().strftime("%Y-%m-%d")
            if task.start_date and today < task.start_date:
                _emit("skipped", "未到有效开始日期")
                return {"status": "skipped", "message": "未到有效开始日期"}
            if task.end_date and today > task.end_date:
                _emit("skipped", "已过有效结束日期")
                return {"status": "skipped", "message": "已过有效结束日期"}

            # 节假日跳过检查（所有调度类型通用）
            schedule_config = json.loads(task.schedule_config) if task.schedule_config else {}
            if schedule_config.get("skip_holidays"):
                try:
                    from harness.services.trade_calendar import is_trading_day
                    if not is_trading_day(today):
                        _emit("skipped", "非交易日，跳过执行")
                        return {"status": "skipped", "message": "非交易日，跳过执行"}
                except Exception as e:
                    logger.warning(f"[scheduler] Holiday check failed: {e}")

            # interval 模式：检查时间窗口 + 星期
            if task.schedule_type == "interval" and task.cron_expression.startswith("INTERVAL:"):
                now_time = datetime.now().time()
                cfg = schedule_config
                # 时间窗口
                start_str = cfg.get("start_time", "09:30")
                end_str = cfg.get("end_time", "11:30")
                start_t = dtime(int(start_str.split(":")[0]), int(start_str.split(":")[1]))
                end_t = dtime(int(end_str.split(":")[0]), int(end_str.split(":")[1]))
                if not (start_t <= now_time <= end_t):
                    _emit("skipped", "不在执行时间窗口内")
                    return {"status": "skipped", "message": "不在执行时间窗口内"}
                # 星期检查
                weekdays = cfg.get("weekdays", [1, 2, 3, 4, 5])
                if datetime.now().isoweekday() not in weekdays:
                    _emit("skipped", "非指定星期，跳过")
                    return {"status": "skipped", "message": "非指定星期，跳过"}

            _emit("prepare", "正在加载工具和技能...")
            from harness.agent.react_orchestrator import ReactOrchestrator, get_react_orchestrator
            global_orch = get_react_orchestrator()
            orchestrator = ReactOrchestrator()
            orchestrator.tools = dict(global_orch.tools)
            orchestrator.skills = dict(global_orch.skills)

            session_id = f"sched_{task_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

            _emit("analyze", "正在执行 AI 分析（可能需要 1-10 分钟）...")
            result = await orchestrator.execute(
                prompt=task.prompt,
                session_id=session_id,
            )

            report = result.get("report", result.get("message", "分析完成"))
            status = "success"
            error_message = ""

            if result.get("status") == "failed":
                status = "failed"
                error_message = result.get("error", "未知错误")
                report = f"定时任务执行失败: {error_message}"
                _emit("failed", f"分析失败: {error_message}")
            else:
                _emit("feishu", "分析完成，正在推送飞书...")

            feishu_sent = False
            if push_feishu and status == "success":
                from harness.services.feishu_client import send_feishu_message

                # 确定推送目标列表
                push_targets = []
                if task.use_channel_push_targets == "true" and task.feishu_channel_id:
                    from harness.core.feishu_config import get_channel_push_targets
                    push_targets = get_channel_push_targets(task.feishu_channel_id)
                elif task.receive_id:
                    # 向后兼容：使用任务自身的推送目标
                    push_targets = [{"receive_id": task.receive_id, "receive_id_type": task.receive_id_type or "chat_id"}]

                for target in push_targets:
                    sent = send_feishu_message(
                        receive_id=target["receive_id"],
                        receive_id_type=target.get("receive_id_type", "chat_id"),
                        text=report,
                        channel_id=task.feishu_channel_id or None,
                    )
                    if sent:
                        feishu_sent = True

                if feishu_sent:
                    _emit("feishu_done", f"飞书推送成功 ({len(push_targets)} 个目标)")
                elif push_targets:
                    _emit("feishu_failed", "飞书推送失败")

            task.last_run_at = datetime.now()
            task.last_run_status = status
            task.last_run_session_id = session_id
            task.run_count = (task.run_count or 0) + 1
            session.commit()

            if status == "success":
                _emit("done", "任务执行完成")

            return {
                "status": status,
                "report": report,
                "message": result.get("message", ""),
                "session_id": session_id,
                "feishu_sent": feishu_sent,
            }

        except Exception as e:
            logger.error(f"[scheduler] Task execution failed: id={task_id}, error={e}", exc_info=True)
            session.rollback()
            _emit("failed", f"执行异常: {str(e)}")
            return {"status": "failed", "message": str(e), "report": "", "feishu_sent": False}
        finally:
            session.close()

    async def trigger_task(self, task_id: int) -> dict:
        """手动触发任务（测试执行，推送飞书）"""
        return await self._run_task(task_id, push_feishu=True)

    async def trigger_task_stream(self, task_id: int):
        """手动触发任务（SSE 流式推送进度）"""
        queue = asyncio.Queue()

        def on_progress(step: str, detail: str) -> None:
            try:
                queue.put_nowait({"type": "progress", "step": step, "detail": detail})
            except Exception:
                pass

        async def _run():
            try:
                result = await self._run_task(task_id, push_feishu=True, on_progress=on_progress)
                await queue.put({"type": "result", "data": result})
            except Exception as e:
                await queue.put({"type": "error", "data": {"message": str(e)}})

        task = asyncio.create_task(_run())

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=600)
                yield event
                if event.get("type") in ("result", "error"):
                    break
            except asyncio.TimeoutError:
                yield {"type": "progress", "step": "running", "detail": "仍在执行中..."}


# 全局单例
_scheduler: Optional[TaskScheduler] = None


def get_scheduler() -> TaskScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = TaskScheduler()
    return _scheduler
