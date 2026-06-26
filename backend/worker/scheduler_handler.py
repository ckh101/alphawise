"""
定时任务 Handler

Worker 内执行定时任务触发：
- 同步触发 + SSE 流式触发
"""

import asyncio
import json
import logging

logger = logging.getLogger("worker.scheduler")


async def handle_trigger(method: str, params: dict) -> dict:
    """手动触发定时任务"""
    task_id = params.get("task_id")
    if task_id is None:
        return {"status": "error", "message": "task_id 参数不能为空"}

    from harness.services.scheduler import get_scheduler

    try:
        scheduler = get_scheduler()
        result = await scheduler.trigger_task(int(task_id))
        return {"status": "ok", "data": result}
    except Exception as e:
        logger.error(f"Scheduler trigger failed: task_id={task_id}, error={e}", exc_info=True)
        return {"status": "error", "message": str(e)}


async def handle_trigger_stream(method: str, params: dict):
    """流式触发定时任务 — yield SSE 格式 JSON chunk"""
    task_id = params.get("task_id")
    if task_id is None:
        yield json.dumps({"type": "error", "data": {"message": "task_id 参数不能为空"}})
        return

    from harness.services.scheduler import get_scheduler

    try:
        scheduler = get_scheduler()
        async for event in scheduler.trigger_task_stream(int(task_id)):
            yield json.dumps(event, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Scheduler trigger_stream failed: task_id={task_id}, error={e}", exc_info=True)
        yield json.dumps({"type": "error", "data": {"message": str(e)}})
