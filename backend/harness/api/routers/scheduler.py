"""
定时任务路由

CRUD + 手动触发 + 启用/禁用
"""

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import or_

from harness.core.database import SchedulerTask as SchedulerTaskModel
from harness.core.database import get_session
from harness.core.logger import get_logger
from harness.services.scheduler import build_cron, get_scheduler

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/scheduler", tags=["定时任务"])


# ========== Request Models ==========

class TaskCreate(BaseModel):
    name: str = ""
    prompt: str
    schedule_type: str = "weekly"
    schedule_config: dict
    receive_id: str = ""
    receive_id_type: str = "chat_id"
    feishu_channel_id: Optional[str] = None
    use_channel_push_targets: bool = False
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    enabled: bool = True


class TaskUpdate(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None
    schedule_type: Optional[str] = None
    schedule_config: Optional[dict] = None
    receive_id: Optional[str] = None
    receive_id_type: Optional[str] = None
    feishu_channel_id: Optional[str] = None
    use_channel_push_targets: Optional[bool] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    enabled: Optional[bool] = None


class TaskToggle(BaseModel):
    enabled: bool


# ========== Helpers ==========

def _task_to_dict(task: SchedulerTaskModel) -> dict:
    import json
    return {
        "id": task.id,
        "name": task.name,
        "prompt": task.prompt,
        "cron_expression": task.cron_expression,
        "schedule_type": task.schedule_type,
        "schedule_config": json.loads(task.schedule_config) if task.schedule_config else {},
        "receive_id": task.receive_id,
        "receive_id_type": task.receive_id_type,
        "feishu_channel_id": task.feishu_channel_id,
        "use_channel_push_targets": task.use_channel_push_targets == "true",
        "start_date": task.start_date,
        "end_date": task.end_date,
        "enabled": task.enabled == "true",
        "last_run_at": task.last_run_at.isoformat() if task.last_run_at else None,
        "last_run_status": task.last_run_status,
        "last_run_session_id": task.last_run_session_id,
        "run_count": task.run_count,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


# ========== Routes ==========

@router.get("/tasks")
async def list_tasks(keyword: Optional[str] = None):
    """列出所有定时任务，支持关键词搜索"""
    session = get_session()
    try:
        query = session.query(SchedulerTaskModel).order_by(SchedulerTaskModel.id.desc())
        if keyword:
            pattern = f"%{keyword}%"
            query = query.filter(
                or_(
                    SchedulerTaskModel.name.like(pattern),
                    SchedulerTaskModel.prompt.like(pattern),
                )
            )
        tasks = query.all()
        return {"code": 0, "data": {"tasks": [_task_to_dict(t) for t in tasks]}}
    finally:
        session.close()


@router.post("/tasks")
async def create_task(body: TaskCreate):
    """创建定时任务"""
    cron_expr = build_cron(body.schedule_type, body.schedule_config)
    import json

    session = get_session()
    try:
        task = SchedulerTaskModel(
            name=body.name,
            prompt=body.prompt,
            cron_expression=cron_expr,
            schedule_type=body.schedule_type,
            schedule_config=json.dumps(body.schedule_config, ensure_ascii=False),
            receive_id=body.receive_id,
            receive_id_type=body.receive_id_type,
            feishu_channel_id=body.feishu_channel_id or "",
            use_channel_push_targets="true" if body.use_channel_push_targets else "false",
            start_date=body.start_date or "",
            end_date=body.end_date or "",
            enabled="true" if body.enabled else "false",
        )
        session.add(task)
        session.commit()
        session.refresh(task)

        # 注册到调度器
        get_scheduler().add_task(task)

        return {"code": 0, "data": _task_to_dict(task)}
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to create task: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.get("/tasks/status")
async def get_tasks_status():
    """获取所有任务的实时执行状态"""
    scheduler = get_scheduler()
    return {"code": 0, "data": scheduler.get_all_status()}


@router.get("/tasks/{task_id}")
async def get_task(task_id: int):
    """获取单个任务详情"""
    session = get_session()
    try:
        task = session.query(SchedulerTaskModel).filter_by(id=task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        return {"code": 0, "data": _task_to_dict(task)}
    finally:
        session.close()


@router.put("/tasks/{task_id}")
async def update_task(task_id: int, body: TaskUpdate):
    """更新任务"""
    import json

    session = get_session()
    try:
        task = session.query(SchedulerTaskModel).filter_by(id=task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")

        update_data = body.model_dump(exclude_unset=True)
        need_cron_update = False

        if "name" in update_data:
            task.name = update_data["name"]
        if "prompt" in update_data:
            task.prompt = update_data["prompt"]
        if "schedule_type" in update_data:
            task.schedule_type = update_data["schedule_type"]
            need_cron_update = True
        if "schedule_config" in update_data:
            task.schedule_config = json.dumps(update_data["schedule_config"], ensure_ascii=False)
            need_cron_update = True
        if "receive_id" in update_data:
            task.receive_id = update_data["receive_id"]
        if "receive_id_type" in update_data:
            task.receive_id_type = update_data["receive_id_type"]
        if "feishu_channel_id" in update_data:
            task.feishu_channel_id = update_data["feishu_channel_id"] or ""
        if "use_channel_push_targets" in update_data:
            task.use_channel_push_targets = "true" if update_data["use_channel_push_targets"] else "false"
        if "start_date" in update_data:
            task.start_date = update_data["start_date"] or ""
        if "end_date" in update_data:
            task.end_date = update_data["end_date"] or ""
        if "enabled" in update_data:
            task.enabled = "true" if update_data["enabled"] else "false"

        if need_cron_update:
            task.cron_expression = build_cron(task.schedule_type, json.loads(task.schedule_config))

        task.updated_at = datetime.now()
        session.commit()
        session.refresh(task)

        # 更新调度器
        get_scheduler().update_task(task)

        return {"code": 0, "data": _task_to_dict(task)}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to update task: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: int):
    """删除任务"""
    session = get_session()
    try:
        task = session.query(SchedulerTaskModel).filter_by(id=task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        session.delete(task)
        session.commit()

        # 从调度器移除
        get_scheduler().remove_task(task_id)

        return {"code": 0, "message": "已删除"}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to delete task: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.post("/tasks/{task_id}/trigger")
async def trigger_task(task_id: int):
    """手动触发任务（测试执行，推送飞书）"""
    scheduler = get_scheduler()
    result = await scheduler.trigger_task(task_id)
    return {"code": 0, "data": result}


@router.post("/tasks/{task_id}/trigger/stream")
async def trigger_task_stream(task_id: int):
    """手动触发任务（SSE 流式进度推送）"""

    async def event_generator():
        scheduler = get_scheduler()
        async for event in scheduler.trigger_task_stream(task_id):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.put("/tasks/{task_id}/toggle")
async def toggle_task(task_id: int, body: TaskToggle):
    """启用/禁用任务"""
    session = get_session()
    try:
        task = session.query(SchedulerTaskModel).filter_by(id=task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")

        task.enabled = "true" if body.enabled else "false"
        task.updated_at = datetime.now()
        session.commit()
        session.refresh(task)

        # 更新调度器
        get_scheduler().update_task(task)

        return {"code": 0, "data": _task_to_dict(task)}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to toggle task: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()
