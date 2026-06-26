"""
智能体监控 API
提供对话记录查询和统计接口
"""

import json
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Query

from harness.api.models import ApiResponse, error_response, success_response
from harness.core.database import AgentSession, LlmCall, get_session
from harness.core.logger import get_logger

from sqlalchemy import desc, func

CST = timezone(timedelta(hours=8))


def _cst_str(utc_dt) -> str:
    """UTC datetime 转中国标准时间字符串"""
    if utc_dt is None:
        return ""
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    return utc_dt.astimezone(CST).strftime("%Y-%m-%d %H:%M:%S")


class BatchDeleteRequest(BaseModel):
    ids: list[int]

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/agent/monitor", tags=["智能体监控"])

TOOL_DISPLAY_NAMES = {
    "get_stock_info": "基本信息",
    "get_realtime_quote": "实时行情",
    "get_kline_data": "K线数据",
    "technical_analysis": "技术分析",
    "fundamental_analysis": "基本面分析",
    "risk_assessment": "风险评估",
    "web_search": "联网搜索",
    "investment_advice": "投资建议",
    "run_backtest": "策略回测",
}

MODE_DISPLAY_NAMES = {
    "report": "投研报告",
    "chat": "智能对话",
    "search_summary": "资讯搜索",
}


@router.get("/stats", response_model=ApiResponse)
async def get_stats() -> ApiResponse:
    """获取统计概览"""
    session = get_session()
    try:
        total = session.query(AgentSession).count()
        completed = session.query(AgentSession).filter(
            AgentSession.status == "completed"
        ).count()

        avg_duration = session.query(func.avg(AgentSession.duration)).scalar() or 0

        # 工具使用频率
        all_rows = session.query(AgentSession.actions).all()
        tool_counts: dict[str, int] = {}
        for (actions_json,) in all_rows:
            try:
                actions = json.loads(actions_json) if actions_json else []
                for a in actions:
                    name = a.get("tool_name", "unknown")
                    tool_counts[name] = tool_counts.get(name, 0) + 1
            except Exception:
                pass

        top_tools = sorted(tool_counts.items(), key=lambda x: x[1], reverse=True)[:5]

        # 最近7天每日对话数 (SQLite 用 strftime 提取日期部分)
        date_col = func.strftime("%Y-%m-%d", AgentSession.created_at).label("d")
        daily = (
            session.query(date_col, func.count())
            .group_by(date_col)
            .order_by(desc("d")).limit(7).all()
        )
        daily_counts = [{"date": d, "count": c} for d, c in daily if d]

        return success_response(data={
            "total_sessions": total,
            "completed": completed,
            "failed": total - completed,
            "success_rate": round(completed / total * 100, 1) if total else 0,
            "avg_duration": round(avg_duration, 1),
            "top_tools": [
                {
                    "name": n,
                    "count": c,
                    "display_name": TOOL_DISPLAY_NAMES.get(n, n),
                }
                for n, c in top_tools
            ],
            "daily_counts": daily_counts,
        })
    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        raise HTTPException(
            status_code=500,
            detail=error_response(500, str(e)).model_dump(),
        )
    finally:
        session.close()


@router.get("/sessions", response_model=ApiResponse)
async def list_sessions(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    status: str | None = Query(None, description="状态过滤: completed, failed"),
    stock_symbol: str | None = Query(None, description="股票代码过滤"),
) -> ApiResponse:
    """列出对话记录（分页）"""
    session = get_session()
    try:
        query = session.query(AgentSession)
        if status:
            query = query.filter(AgentSession.status == status)
        if stock_symbol:
            query = query.filter(AgentSession.stock_symbol.contains(stock_symbol))

        total = query.count()
        rows = (
            query.order_by(desc(AgentSession.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

        items = []
        for r in rows:
            items.append({
                "id": r.id,
                "session_id": r.session_id,
                "conversation_id": r.conversation_id or r.session_id,
                "turn_number": r.turn_number or 1,
                "prompt": r.prompt[:100],
                "response_mode": r.response_mode,
                "response_mode_display": MODE_DISPLAY_NAMES.get(r.response_mode, r.response_mode),
                "status": r.status,
                "stock_symbol": r.stock_symbol,
                "stock_name": r.stock_name,
                "tool_calls_count": r.tool_calls_count,
                "duration": r.duration,
                "created_at": _cst_str(r.created_at),
            })

        return success_response(data={
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        })
    except Exception as e:
        logger.error(f"Failed to list sessions: {e}")
        raise HTTPException(
            status_code=500,
            detail=error_response(500, str(e)).model_dump(),
        )
    finally:
        session.close()


@router.post("/sessions/batch-delete", response_model=ApiResponse)
async def batch_delete_sessions(req: BatchDeleteRequest) -> ApiResponse:
    """批量删除对话记录"""
    ids = req.ids
    session = get_session()
    try:
        if not ids:
            return success_response(message="无记录需要删除")
        # 先查要删除记录的 session_id，用于关联删除 llm_calls
        rows = session.query(AgentSession.session_id).filter(
            AgentSession.id.in_(ids)
        ).all()
        sids = [r.session_id for r in rows]
        if sids:
            session.query(LlmCall).filter(LlmCall.session_id.in_(sids)).delete(synchronize_session=False)
        deleted = session.query(AgentSession).filter(
            AgentSession.id.in_(ids)
        ).delete(synchronize_session=False)
        session.commit()
        return success_response(message=f"已删除 {deleted} 条记录", data={"deleted": deleted})
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to batch delete sessions: {e}")
        raise HTTPException(
            status_code=500,
            detail=error_response(500, str(e)).model_dump(),
        )
    finally:
        session.close()


@router.get("/sessions/{session_id}", response_model=ApiResponse)
async def get_session_detail(session_id: str) -> ApiResponse:
    """获取单条对话详情"""
    session = get_session()
    try:
        # 支持 session_id 或数字 id 查找
        row = session.query(AgentSession).filter(
            AgentSession.session_id == session_id
        ).first()
        if not row:
            try:
                row = session.query(AgentSession).get(int(session_id))
            except (ValueError, TypeError):
                pass
        if not row:
            raise HTTPException(
                status_code=404,
                detail=error_response(404, "Session not found").model_dump(),
            )

        # 查询关联的 LLM 调用记录
        llm_calls_rows = session.query(LlmCall).filter(
            LlmCall.session_id == row.session_id
        ).order_by(LlmCall.created_at).all()
        llm_calls = []
        for lc in llm_calls_rows:
            llm_calls.append({
                "phase": lc.phase,
                "model": lc.model,
                "input_preview": (lc.input_messages or "")[:500],
                "output_preview": (lc.output_content or "")[:500],
                "duration_ms": lc.duration_ms,
                "created_at": _cst_str(lc.created_at),
            })

        return success_response(data={
            "id": row.id,
            "session_id": row.session_id,
            "conversation_id": row.conversation_id or row.session_id,
            "turn_number": row.turn_number or 1,
            "prompt": row.prompt,
            "response_mode": row.response_mode,
            "response_mode_display": MODE_DISPLAY_NAMES.get(row.response_mode, row.response_mode),
            "status": row.status,
            "stock_symbol": row.stock_symbol,
            "stock_name": row.stock_name,
            "report": row.report,
            "thoughts": json.loads(row.thoughts) if row.thoughts else [],
            "actions": json.loads(row.actions) if row.actions else [],
            "tool_plan": json.loads(row.tool_plan) if row.tool_plan else [],
            "tool_calls_count": row.tool_calls_count,
            "duration": row.duration,
            "error_message": row.error_message,
            "llm_calls": llm_calls,
            "created_at": _cst_str(row.created_at),
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get session detail: {e}")
        raise HTTPException(
            status_code=500,
            detail=error_response(500, str(e)).model_dump(),
        )
    finally:
        session.close()


@router.delete("/sessions/{session_id}", response_model=ApiResponse)
async def delete_session(session_id: str) -> ApiResponse:
    """删除对话记录"""
    session = get_session()
    try:
        row = session.query(AgentSession).filter(
            AgentSession.session_id == session_id
        ).first()
        if not row:
            raise HTTPException(
                status_code=404,
                detail=error_response(404, "Session not found").model_dump(),
            )
        session.delete(row)
        session.commit()
        return success_response(message="已删除")
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to delete session: {e}")
        raise HTTPException(
            status_code=500,
            detail=error_response(500, str(e)).model_dump(),
        )
    finally:
        session.close()


# ==================== Conversation (会话) 端点 ====================


@router.get("/conversations", response_model=ApiResponse)
async def list_conversations() -> ApiResponse:
    """按 conversation_id 分组返回会话列表，每组取最新一条作为摘要"""
    db = get_session()
    try:
        # 子查询：每个 conversation_id 的最大 id（即最新一条）
        latest = (
            db.query(
                AgentSession.conversation_id,
                func.max(AgentSession.id).label("max_id"),
                func.count(AgentSession.id).label("turn_count"),
            )
            .filter(AgentSession.conversation_id.isnot(None))
            .group_by(AgentSession.conversation_id)
            .subquery()
        )

        rows = (
            db.query(AgentSession, latest.c.turn_count)
            .join(latest, AgentSession.id == latest.c.max_id)
            .order_by(desc(AgentSession.created_at))
            .limit(200)
            .all()
        )

        items = []
        for row, turn_count in rows:
            items.append({
                "conversation_id": row.conversation_id,
                "title": (row.prompt or "")[:60],
                "created_at": _cst_str(row.created_at),
                "turn_count": turn_count,
                "stock_symbol": row.stock_symbol,
                "stock_name": row.stock_name,
                "response_mode": row.response_mode,
            })

        return success_response(data={"items": items, "total": len(items)})
    except Exception as e:
        logger.error(f"Failed to list conversations: {e}")
        raise HTTPException(status_code=500, detail=error_response(500, str(e)).model_dump())
    finally:
        db.close()


@router.get("/conversations/{conversation_id}/messages", response_model=ApiResponse)
async def get_conversation_messages(conversation_id: str) -> ApiResponse:
    """返回某个会话所有轮次的消息"""
    db = get_session()
    try:
        rows = (
            db.query(AgentSession)
            .filter(AgentSession.conversation_id == conversation_id)
            .order_by(AgentSession.turn_number)
            .all()
        )
        if not rows:
            raise HTTPException(status_code=404, detail=error_response(404, "会话不存在").model_dump())

        messages = []
        for r in rows:
            messages.append({"role": "user", "content": r.prompt or ""})
            report = r.report or ""
            if report:
                messages.append({"role": "assistant", "content": report})

        return success_response(data={
            "conversation_id": conversation_id,
            "messages": messages,
            "turn_count": len(rows),
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get conversation messages: {e}")
        raise HTTPException(status_code=500, detail=error_response(500, str(e)).model_dump())
    finally:
        db.close()


@router.delete("/conversations/{conversation_id}", response_model=ApiResponse)
async def delete_conversation(conversation_id: str) -> ApiResponse:
    """删除某个会话的所有轮次"""
    db = get_session()
    try:
        sids = [r.session_id for r in
                db.query(AgentSession.session_id)
                .filter(AgentSession.conversation_id == conversation_id).all()]
        if sids:
            db.query(LlmCall).filter(LlmCall.session_id.in_(sids)).delete(synchronize_session=False)
        deleted = db.query(AgentSession).filter(
            AgentSession.conversation_id == conversation_id
        ).delete(synchronize_session=False)
        db.commit()
        return success_response(message=f"已删除 {deleted} 条记录", data={"deleted": deleted})
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to delete conversation: {e}")
        raise HTTPException(status_code=500, detail=error_response(500, str(e)).model_dump())
    finally:
        db.close()


@router.delete("/conversations/clear/all", response_model=ApiResponse)
async def clear_all_conversations() -> ApiResponse:
    """清空所有对话记录"""
    db = get_session()
    try:
        db.query(LlmCall).delete(synchronize_session=False)
        deleted = db.query(AgentSession).delete(synchronize_session=False)
        db.commit()
        return success_response(message=f"已清空 {deleted} 条记录", data={"deleted": deleted})
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to clear conversations: {e}")
        raise HTTPException(status_code=500, detail=error_response(500, str(e)).model_dump())
    finally:
        db.close()


