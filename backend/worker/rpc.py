"""
JSON-RPC 分发

method -> handler 映射。
Handler 调用现有 Python 代码（skills、orchestrator 等）。
"""

import json
import logging

logger = logging.getLogger("worker.rpc")


async def _dispatch_tdx(method: str, params: dict) -> dict:
    from worker.tdx_handler import handle_quote, handle_kline, handle_stock_info, handle_health, handle_search_by_name, handle_fetch_all_stocks
    mapping = {
        "tdx.quote": handle_quote,
        "tdx.kline": handle_kline,
        "tdx.stock_info": handle_stock_info,
        "tdx.health": handle_health,
        "tdx.search_by_name": handle_search_by_name,
        "tdx.fetch_all_stocks": handle_fetch_all_stocks,
    }
    handler = mapping.get(method)
    if handler is None:
        return {"status": "error", "message": f"unknown tdx method: {method}"}
    return await handler(method, params)


async def _dispatch_backtest(method: str, params: dict) -> dict:
    from worker.backtest_handler import handle_strategies, handle_params, handle_run
    mapping = {
        "backtest.strategies": handle_strategies,
        "backtest.params": handle_params,
        "backtest.run": handle_run,
    }
    handler = mapping.get(method)
    if handler is None:
        return {"status": "error", "message": f"unknown backtest method: {method}"}
    return await handler(method, params)


async def _dispatch_agent(method: str, params: dict) -> dict:
    from worker.agent_handler import handle_analyze
    if method == "react.analyze":
        return await handle_analyze(method, params)
    return {"status": "error", "message": f"unknown agent method: {method}"}


async def _dispatch_agent_stream(method: str, params: dict):
    from worker.agent_handler import handle_analyze_stream
    if method == "react.analyze_stream":
        async for chunk in handle_analyze_stream(method, params):
            yield chunk
    else:
        yield json.dumps({"status": "error", "message": f"unknown stream method: {method}"})


async def _dispatch_feishu(method: str, params: dict) -> dict:
    from worker.feishu_handler import (
        handle_status, handle_start, handle_stop,
        handle_channel_start, handle_channel_stop, handle_channel_reload, handle_send_message,
    )
    mapping = {
        "feishu.status": handle_status,
        "feishu.start": handle_start,
        "feishu.stop": handle_stop,
        "feishu.channel_start": handle_channel_start,
        "feishu.channel_stop": handle_channel_stop,
        "feishu.channel_reload": handle_channel_reload,
        "feishu.send_message": handle_send_message,
    }
    handler = mapping.get(method)
    if handler is None:
        return {"status": "error", "message": f"unknown feishu method: {method}"}
    return await handler(method, params)


async def _dispatch_scheduler(method: str, params: dict) -> dict:
    if method == "scheduler.trigger":
        from worker.scheduler_handler import handle_trigger
        return await handle_trigger(method, params)
    return {"status": "error", "message": f"unknown scheduler method: {method}"}


async def _dispatch_pdf(method: str, params: dict) -> dict:
    from worker.pdf_handler import handle_generate_pdf
    if method == "pdf.generate":
        return await handle_generate_pdf(method, params)
    return {"status": "error", "message": f"unknown pdf method: {method}"}


async def _dispatch_scheduler_stream(method: str, params: dict):
    if method == "scheduler.trigger_stream":
        from worker.scheduler_handler import handle_trigger_stream
        async for chunk in handle_trigger_stream(method, params):
            yield chunk
    else:
        yield json.dumps({"status": "error", "message": f"unknown scheduler stream method: {method}"})


# method prefix -> dispatcher 映射
_PREFIX_MAP = {
    "tdx.": _dispatch_tdx,
    "backtest.": _dispatch_backtest,
    "react.": _dispatch_agent,
    "feishu.": _dispatch_feishu,
    "scheduler.": _dispatch_scheduler,
    "pdf.": _dispatch_pdf,
}

# 流式方法集合
_STREAM_METHODS = {"react.analyze_stream", "scheduler.trigger_stream"}


async def dispatch(method: str, params: dict) -> dict:
    """分发普通 RPC 调用"""
    for prefix, dispatcher in _PREFIX_MAP.items():
        if method.startswith(prefix):
            try:
                return await dispatcher(method, params)
            except Exception as e:
                # 用异常类名判断错误类型
                ename = type(e).__name__
                if "Validation" in ename:
                    return {"status": "error", "message": str(e), "code": 400}
                if "TdxConnection" in ename:
                    return {"status": "error", "message": str(e), "code": 503}
                if "Skill" in ename:
                    return {"status": "error", "message": str(e), "code": 500}
                logger.error(f"RPC error for {method}: {e}", exc_info=True)
                return {"status": "error", "message": str(e)}

    return {"status": "error", "message": f"unknown method: {method}"}


async def dispatch_stream(method: str, params: dict):
    """分发流式 RPC 调用"""
    if method not in _STREAM_METHODS:
        yield json.dumps({"status": "error", "message": f"not a stream method: {method}"})
        return

    try:
        if method.startswith("react."):
            async for chunk in _dispatch_agent_stream(method, params):
                yield chunk
        elif method.startswith("scheduler."):
            async for chunk in _dispatch_scheduler_stream(method, params):
                yield chunk
    except Exception as e:
        logger.error(f"Stream RPC error for {method}: {e}", exc_info=True)
        yield json.dumps({"type": "error", "data": {"message": str(e)}})
