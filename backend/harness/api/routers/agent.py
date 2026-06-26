"""
Agent API路由
提供Agent任务执行和管理的HTTP接口
支持Claude Agent SDK编排器
"""

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from fastapi.responses import StreamingResponse

from harness.api.models import ApiResponse, ClaudeExecuteRequest, error_response, success_response
from harness.core.exceptions import ValidationError
from harness.core.logger import get_logger
from harness.agent import get_orchestrator, get_available_skills, get_claude_orchestrator_instance
from harness.agent.react_orchestrator import get_react_orchestrator, ReactOrchestrator

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/agent", tags=["Agent服务"])


@router.get("/skills", response_model=ApiResponse)
async def list_skills() -> ApiResponse:
    """
    列出所有可用技能

    Returns:
        包含技能列表的响应
    """
    try:
        logger.debug("[API] list_skills endpoint called")
        skills = get_available_skills()
        logger.info(f"[API] list_skills: got {len(skills)} skills")
        return success_response(data={"skills": skills, "count": len(skills)})
    except Exception as e:
        logger.error(f"Failed to list skills: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=error_response(500, str(e)).model_dump()
        )


@router.get("/workflows", response_model=ApiResponse)
async def list_workflows() -> ApiResponse:
    """
    列出所有可用工作流

    Returns:
        包含工作流列表的响应
    """
    try:
        orchestrator = get_orchestrator()
        workflows = orchestrator.workflows

        workflow_list = [
            {"name": name, "steps": steps}
            for name, steps in workflows.items()
        ]

        return success_response(data={"workflows": workflow_list, "count": len(workflow_list)})
    except Exception as e:
        logger.error(f"Failed to list workflows: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=error_response(500, str(e)).model_dump()
        )


@router.post("/tasks", response_model=ApiResponse)
async def create_task(
    task_type: str = Query(..., description="任务类型: stock_analysis, investment_report, chat_query"),
    input_data: dict[str, Any] = None,
    priority: int = Query(0, description="任务优先级")
) -> ApiResponse:
    """
    创建并执行Agent任务

    Args:
        task_type: 任务类型
        input_data: 输入数据
        priority: 优先级

    Returns:
        包含任务结果的响应
    """
    try:
        logger.info(f"Creating agent task: type={task_type}")

        if input_data is None:
            input_data = {}

        orchestrator = get_orchestrator()

        # 验证任务类型
        valid_types = ["stock_analysis", "investment_report", "chat_query"]
        if task_type not in valid_types:
            raise ValidationError(
                f"无效的任务类型: {task_type}，支持: {valid_types}",
                error_code="AGENT_001",
                details={"task_type": task_type, "valid_types": valid_types}
            )

        # 创建任务
        task_id = orchestrator.create_task(task_type, input_data, priority)

        # 执行任务
        result = await orchestrator.execute_task(task_id)

        return success_response(data=result)

    except ValidationError as e:
        logger.warning(f"Validation error in create_task", error_code=e.error_code)
        raise HTTPException(
            status_code=400,
            detail=error_response(400, str(e), e.error_code, e.details).model_dump()
        )
    except Exception as e:
        logger.error(f"Task execution error: {str(e)}", task_type=task_type)
        import traceback
        traceback.print_exc()  # 打印完整堆栈用于调试
        raise HTTPException(
            status_code=500,
            detail=error_response(500, str(e)).model_dump()
        )


@router.get("/tasks/{task_id}", response_model=ApiResponse)
async def get_task_status(task_id: str) -> ApiResponse:
    """
    获取任务状态

    Args:
        task_id: 任务ID

    Returns:
        包含任务状态的响应
    """
    try:
        orchestrator = get_orchestrator()
        task_status = orchestrator.get_task_status(task_id)

        return success_response(data=task_status)

    except ValueError as e:
        logger.warning(f"Task not found: {task_id}")
        raise HTTPException(
            status_code=404,
            detail=error_response(404, str(e)).model_dump()
        )
    except Exception as e:
        logger.error(f"Failed to get task status: {str(e)}", task_id=task_id)
        raise HTTPException(
            status_code=500,
            detail=error_response(500, str(e)).model_dump()
        )


@router.get("/tasks", response_model=ApiResponse)
async def list_tasks(
    status: str | None = Query(None, description="过滤状态")
) -> ApiResponse:
    """
    列出所有任务

    Args:
        status: 状态过滤（可选）

    Returns:
        包含任务列表的响应
    """
    try:
        orchestrator = get_orchestrator()
        tasks = orchestrator.list_tasks(status)

        return success_response(data={"tasks": tasks, "count": len(tasks)})

    except Exception as e:
        logger.error(f"Failed to list tasks: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=error_response(500, str(e)).model_dump()
        )


# ==================== Claude Agent SDK 端点 ====================

@router.post("/claude/execute", response_model=ApiResponse)
async def execute_with_claude(request: ClaudeExecuteRequest) -> ApiResponse:
    """
    使用Claude Agent SDK执行任务

    Args:
        request: 执行请求

    Returns:
        包含执行结果的响应
    """
    try:
        logger.info(f"Executing task with Claude Agent SDK: type={request.task_type}")

        claude_orchestrator = get_claude_orchestrator_instance()

        # 使用Claude Agent SDK执行任务
        result = await claude_orchestrator.execute_task(
            prompt=request.prompt,
            task_type=request.task_type,
            session_id=request.session_id
        )

        logger.info(f"Claude Agent SDK task completed: {result.get('status')}")
        return success_response(data=result)

    except Exception as e:
        logger.error(f"Claude Agent SDK execution error: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=error_response(500, str(e)).model_dump()
        )


@router.get("/claude/status", response_model=ApiResponse)
async def get_claude_status() -> ApiResponse:
    """
    获取Claude Agent SDK状态

    Returns:
        包含状态信息的响应
    """
    try:
        claude_orchestrator = get_claude_orchestrator_instance()

        status = {
            "available": claude_orchestrator.client is not None,
            "skills_count": len(claude_orchestrator.skills),
            "workflows_count": len(claude_orchestrator.workflows),
            "skills": list(claude_orchestrator.skills.keys()),
            "workflows": list(claude_orchestrator.workflows.keys())
        }

        return success_response(data=status)

    except Exception as e:
        logger.error(f"Failed to get Claude status: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=error_response(500, str(e)).model_dump()
        )


# ==================== ReAct Agent 端点 ====================

MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    from harness.utils.file_parser import parse_file, ALLOWED_EXTENSIONS
    from pathlib import Path

    filename = file.filename or ""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件格式: {ext}")

    content_bytes = await file.read()
    if len(content_bytes) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail="文件大小超过 10MB 限制")

    try:
        text = parse_file(filename, content_bytes)
    except Exception as e:
        logger.error(f"File parse error: {e}", exc_info=True)
        raise HTTPException(status_code=422, detail=f"文件解析失败: {e}")

    file_id = str(uuid.uuid4())
    return success_response(data={
        "file_id": file_id,
        "filename": filename,
        "content_text": text,
        "char_count": len(text),
    })


@router.post("/react/analyze", response_model=ApiResponse)
async def react_analyze(
    request: ClaudeExecuteRequest
) -> ApiResponse:
    """
    使用ReAct Agent执行端到端投研分析

    这是新一代的智能投研Agent，基于ReAct架构：
    - 自动识别用户意图
    - 自动规划分析步骤
    - 依次获取数据并分析
    - 生成完整投研报告

    一句话输入，自动完成所有分析！

    Args:
        request: 分析请求（只需提供prompt）

    Returns:
        包含完整投研报告的响应
    """
    try:
        logger.info(f"Executing ReAct analysis: prompt={request.prompt[:50]}...")

        react_orchestrator = get_react_orchestrator()

        # 执行端到端分析
        result = await react_orchestrator.execute(
            prompt=request.prompt,
            session_id=request.session_id,
            file_context=request.file_context
        )

        logger.info(f"ReAct analysis completed: status={result.get('status')}")
        return success_response(data=result)

    except Exception as e:
        logger.error(f"ReAct analysis error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=error_response(500, str(e)).model_dump()
        )


@router.post("/react/analyze/stream")
async def react_analyze_stream(request: ClaudeExecuteRequest):
    """
    使用ReAct Agent执行端到端投研分析（SSE流式响应）

    与 /react/analyze 功能相同，但通过 Server-Sent Events 实时推送分析进度：
    - type=progress: 阶段进度更新
    - type=result: 最终分析结果
    - type=error: 错误信息
    """
    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()

        async def progress_callback(event: dict):
            """进度回调：将事件放入队列并让步"""
            await queue.put(("progress", event))
            # 强制让步，让 generator 有机会从队列取数据并推送
            await asyncio.sleep(0)

        # 推送初始意图识别进度
        await queue.put(("progress", {"phase": "intent", "status": "running", "message": "正在识别分析意图..."}))
        yield f"data: {json.dumps({'type': 'progress', 'data': {'phase': 'intent', 'status': 'running', 'message': '正在识别分析意图...'}}, ensure_ascii=False)}\n\n"

        # 在后台启动分析任务
        async def run_analysis():
            try:
                react_orchestrator = get_react_orchestrator()
                result = await react_orchestrator.execute(
                    prompt=request.prompt,
                    session_id=request.session_id,
                    progress_callback=progress_callback,
                    file_context=request.file_context
                )
                await queue.put(("result", result))
            except Exception as e:
                logger.error(f"Stream analysis error: {e}", exc_info=True)
                await queue.put(("error", {"message": str(e)}))

        task = asyncio.create_task(run_analysis())

        try:
            # 持续从队列读取事件并推送
            # 使用短超时 + drain drained queue 来确保实时推送
            while True:
                # 先排空队列中的所有待处理事件
                while not queue.empty():
                    try:
                        event_type, data = queue.get_nowait()
                        payload = json.dumps({"type": event_type, "data": data}, ensure_ascii=False)
                        yield f"data: {payload}\n\n"
                        if event_type in ("result", "error"):
                            return
                    except asyncio.QueueEmpty:
                        break

                # 如果分析任务已完成且队列为空，退出
                if task.done() and queue.empty():
                    break

                # 等待新事件或短暂让步
                try:
                    event_type, data = await asyncio.wait_for(queue.get(), timeout=1.0)
                    payload = json.dumps({"type": event_type, "data": data}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                    if event_type in ("result", "error"):
                        return
                except asyncio.TimeoutError:
                    # 发送 keepalive 防止连接超时
                    yield ": keepalive\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@router.get("/react/status", response_model=ApiResponse)
async def get_react_status() -> ApiResponse:
    """
    获取ReAct Agent状态

    Returns:
        包含状态信息的响应
    """
    try:
        react_orchestrator = get_react_orchestrator()

        status = {
            "available": True,
            "skills_count": len(react_orchestrator.skills),
            "tools_count": len(react_orchestrator.tools),
            "tools": list(react_orchestrator.tools.keys()),
            "skills": list(react_orchestrator.skills.keys()),
            "workflow_steps": ReactOrchestrator.ANALYSIS_WORKFLOW
        }

        return success_response(data=status)

    except Exception as e:
        logger.error(f"Failed to get ReAct status: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=error_response(500, str(e)).model_dump()
        )
