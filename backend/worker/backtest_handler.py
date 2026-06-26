"""
Backtest Handler

Worker 内调用 strategy-backtest skill 执行策略回测。
通过 registry 获取 skill 实例（与 backtest.py router 相同模式）。
"""

import logging

logger = logging.getLogger("worker.backtest")


class _SkillError(Exception):
    pass


class _ValidationError(Exception):
    pass


_agent_initialized = False


async def _ensure_agent_initialized():
    """确保 Agent 系统已初始化（懒初始化，首次调用触发）"""
    global _agent_initialized
    if _agent_initialized:
        return
    from harness.agent import initialize_agent
    await initialize_agent()
    _agent_initialized = True


async def _get_skill():
    """获取 strategy-backtest skill 实例"""
    from harness.agent.registry import get_registry
    await _ensure_agent_initialized()
    registry = get_registry()
    try:
        return registry.get("strategy-backtest")
    except ValueError:
        raise _SkillError("strategy-backtest skill 未注册")


async def handle_strategies(method: str, params: dict) -> dict:
    """列出可用策略"""
    skill = await _get_skill()
    result = skill.list_strategies()
    return {"status": "ok", "data": result}


async def handle_params(method: str, params: dict) -> dict:
    """获取策略参数"""
    strategy = params.get("strategy", "")
    if not strategy:
        raise _ValidationError("strategy 参数不能为空")

    skill = await _get_skill()
    result = skill.get_strategy_params(strategy)
    return {"status": "ok", "data": result}


async def handle_run(method: str, params: dict) -> dict:
    """执行策略回测"""
    symbol = params.get("symbol", "")
    strategy = params.get("strategy", "")
    if not symbol or not strategy:
        raise _ValidationError("symbol 和 strategy 参数不能为空")

    bt_params = params.get("params", {})
    start_date = params.get("start_date")
    end_date = params.get("end_date")
    initial_cash = params.get("initial_cash", 1000000)

    skill = await _get_skill()
    result = skill.run_backtest(
        symbol=symbol,
        strategy=strategy,
        params=bt_params,
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
    )
    return {"status": "ok", "data": result}
