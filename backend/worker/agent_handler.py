"""
Agent Handler

Worker 内调用 ReactOrchestrator 执行分析任务。
同步和流式两种模式。
"""

import asyncio
import json
import logging

logger = logging.getLogger("worker.agent")


async def handle_analyze(method: str, params: dict) -> dict:
    """同步分析"""
    prompt = params.get("prompt", "")
    session_id = params.get("session_id")
    file_context = params.get("file_context")

    if not prompt:
        return {"status": "error", "message": "prompt 不能为空"}

    logger.info(f"ReAct analysis: prompt={prompt[:50]}...")

    from harness.agent.react_orchestrator import get_react_orchestrator
    react_orchestrator = get_react_orchestrator()

    result = await react_orchestrator.execute(
        prompt=prompt,
        session_id=session_id,
        file_context=file_context,
    )

    logger.info(f"ReAct analysis completed: status={result.get('status')}")
    return {"status": "ok", "data": result}


async def handle_analyze_stream(method: str, params: dict):
    """流式分析 — yield SSE 格式的 JSON chunk"""
    prompt = params.get("prompt", "")
    session_id = params.get("session_id")
    file_context = params.get("file_context")

    if not prompt:
        yield json.dumps({"type": "error", "data": {"message": "prompt 不能为空"}})
        return

    queue: asyncio.Queue = asyncio.Queue()

    async def progress_callback(event: dict):
        await queue.put(("progress", event))
        await asyncio.sleep(0)

    # 推送初始进度
    init_event = {"phase": "intent", "status": "running", "message": "正在识别分析意图..."}
    yield json.dumps({"type": "progress", "data": init_event}, ensure_ascii=False)

    async def run_analysis():
        try:
            from harness.agent.react_orchestrator import get_react_orchestrator
            react_orchestrator = get_react_orchestrator()
            result = await react_orchestrator.execute(
                prompt=prompt,
                session_id=session_id,
                progress_callback=progress_callback,
                file_context=file_context,
            )
            await queue.put(("result", result))
        except Exception as e:
            logger.error(f"Stream analysis error: {e}", exc_info=True)
            await queue.put(("error", {"message": str(e)}))

    task = asyncio.create_task(run_analysis())

    try:
        while True:
            # 先排空队列
            while not queue.empty():
                try:
                    event_type, data = queue.get_nowait()
                    yield json.dumps({"type": event_type, "data": data}, ensure_ascii=False)
                    if event_type in ("result", "error"):
                        return
                except asyncio.QueueEmpty:
                    break

            if task.done() and queue.empty():
                break

            try:
                event_type, data = await asyncio.wait_for(queue.get(), timeout=1.0)
                yield json.dumps({"type": event_type, "data": data}, ensure_ascii=False)
                if event_type in ("result", "error"):
                    return
            except asyncio.TimeoutError:
                pass
    finally:
        if not task.done():
            task.cancel()
