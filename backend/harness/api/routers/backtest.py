"""
策略回测API路由
提供策略列表、参数查询和回测执行接口
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from harness.api.models import ApiResponse, BacktestRequest, error_response, success_response
from harness.core.exceptions import SkillError, ValidationError
from harness.core.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/backtest", tags=["策略回测"])


def _get_skill():
    """获取回测技能实例"""
    from harness.agent.registry import get_registry
    registry = get_registry()
    if not registry.has_skill("strategy-backtest"):
        raise HTTPException(
            status_code=503,
            detail=error_response(503, "回测服务未就绪").model_dump()
        )
    return registry.get("strategy-backtest")


@router.get("/strategies", response_model=ApiResponse)
async def list_strategies() -> ApiResponse:
    """
    列出所有可用回测策略

    Returns:
        策略列表及默认参数
    """
    try:
        skill = _get_skill()
        strategies = skill.list_strategies()
        return success_response(data={"strategies": strategies, "count": len(strategies)})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list strategies: {e}")
        raise HTTPException(
            status_code=500,
            detail=error_response(500, str(e)).model_dump()
        )


@router.get("/params", response_model=ApiResponse)
async def get_strategy_params(
    strategy: str = Query(..., description="策略名称")
) -> ApiResponse:
    """
    获取指定策略的参数

    Returns:
        策略参数和描述
    """
    try:
        skill = _get_skill()
        params = skill.get_strategy_params(strategy)
        return success_response(data=params)
    except ValidationError as e:
        raise HTTPException(
            status_code=400,
            detail=error_response(400, str(e), e.error_code, e.details).model_dump()
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get strategy params: {e}")
        raise HTTPException(
            status_code=500,
            detail=error_response(500, str(e)).model_dump()
        )


@router.post("/run", response_model=ApiResponse)
async def run_backtest(request: BacktestRequest) -> ApiResponse:
    """
    执行策略回测

    Args:
        request: 回测请求参数

    Returns:
        回测结果，包含指标、交易记录和资金曲线
    """
    try:
        logger.info(f"Running backtest: {request.symbol} strategy={request.strategy}")
        skill = _get_skill()

        result = skill.run_backtest(
            symbol=request.symbol,
            strategy=request.strategy,
            params=request.params,
            start_date=request.start_date,
            end_date=request.end_date,
            initial_cash=request.initial_cash,
        )

        return success_response(data=result)
    except ValidationError as e:
        raise HTTPException(
            status_code=400,
            detail=error_response(400, str(e), e.error_code, e.details).model_dump()
        )
    except SkillError as e:
        raise HTTPException(
            status_code=422,
            detail=error_response(422, str(e), e.error_code, e.details).model_dump()
        )
    except Exception as e:
        logger.error(f"Backtest failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=error_response(500, str(e)).model_dump()
        )
