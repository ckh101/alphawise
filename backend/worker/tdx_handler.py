"""
TDX 数据 Handler

Worker 内调用现有 skill 代码获取行情/K线/股票信息。
TDX 连接采用全局单例，避免每次请求重新建连（本地探测 3s + 远程连接）。
模块加载采用缓存，避免重复 importlib。
name 字段不在 Python 端补全，统一由 Node.js 端从 stocks 表（SQLite）补齐。
"""

import importlib.util
import logging
from pathlib import Path

logger = logging.getLogger("worker.tdx")

_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills" / "builtin"

# TDX 连接单例：首次 _get_quotes() 建连，后续复用
_QUOTES_INSTANCE = None

# 模块缓存：{skill_dir_name: module}，避免重复 importlib 加载
_MODULE_CACHE: dict = {}


class _ValidationError(Exception):
    pass


class _SkillError(Exception):
    pass


def _load_module(skill_dir: str):
    """动态加载 skill 模块，按 skill 名缓存，避免重复加载。"""
    if skill_dir in _MODULE_CACHE:
        return _MODULE_CACHE[skill_dir]
    skill_path = _SKILLS_DIR / skill_dir / "skill.py"
    if not skill_path.exists():
        raise _SkillError(f"Skill not found: {skill_path}")
    spec = importlib.util.spec_from_file_location(f"skill_{skill_dir.replace('-', '_')}", str(skill_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _MODULE_CACHE[skill_dir] = module
    return module


def _get_quotes():
    """返回 TDX 连接单例，首次调用建连，后续复用。"""
    global _QUOTES_INSTANCE
    if _QUOTES_INSTANCE is not None:
        return _QUOTES_INSTANCE
    mod = _load_module("tdx-realtime-quote")
    _QUOTES_INSTANCE = mod._connect_tdx()
    logger.info("TDX connection singleton established")
    return _QUOTES_INSTANCE


async def handle_quote(method: str, params: dict) -> dict:
    """获取实时行情。name 字段不在此补全，由 Node.js 端从 stocks 表补齐。"""
    symbols = params.get("symbols", "")
    if not symbols:
        raise _ValidationError("symbols 参数不能为空")

    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    module = _load_module("tdx-realtime-quote")
    result = module.get_realtime_quote(symbol_list, _get_quotes())
    return {"status": "ok", "data": result}


async def handle_kline(method: str, params: dict) -> dict:
    """获取 K 线数据"""
    symbol = params.get("symbol", "")
    if not symbol:
        raise _ValidationError("symbol 参数不能为空")

    period = params.get("period", "daily")
    start_date = params.get("start_date")
    end_date = params.get("end_date")

    module = _load_module("tdx-kline")
    result = module.get_kline(symbol, period, start_date, end_date, _get_quotes())
    return {"status": "ok", "data": result}


async def handle_stock_info(method: str, params: dict) -> dict:
    """获取股票信息"""
    symbol = params.get("symbol", "")
    if not symbol:
        raise _ValidationError("symbol 参数不能为空")

    module = _load_module("tdx-stock-info")
    result = module.get_stock_info(symbol, _get_quotes())
    return {"status": "ok", "data": result}


async def handle_health(method: str, params: dict) -> dict:
    """TDX 连接健康检查"""
    try:
        module = _load_module("tdx-realtime-quote")
        # 尝试获取一个常见股票来验证连接
        module.get_realtime_quote(["000001.SZ"])
        return {"status": "ok", "message": "TDX 连接正常"}
    except Exception as e:
        return {"status": "error", "message": f"TDX 连接失败: {e}"}


async def handle_fetch_all_stocks(method: str, params: dict) -> dict:
    """全量拉取股票列表，返回 [{code, name, market, symbol}, ...]"""
    try:
        mod = _load_module("tdx-realtime-quote")
        quotes = _get_quotes()
        if not quotes:
            return {"status": "error", "message": "TDX connect failed"}

        code_to_name = mod._get_stock_names(quotes)
        items = []
        for code, name in code_to_name.items():
            if not code or not name:
                continue
            if code.startswith("6") or code.startswith("5"):
                symbol = f"{code}.SH"
                market = "SH"
            elif code.startswith(("0", "3")):
                symbol = f"{code}.SZ"
                market = "SZ"
            elif code.startswith(("8", "4")):
                symbol = f"{code}.BJ"
                market = "BJ"
            else:
                symbol = code
                market = ""
            items.append({"code": str(code), "name": str(name), "market": market, "symbol": symbol})
        return {"status": "ok", "data": items, "count": len(items)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


async def handle_search_by_name(method: str, params: dict) -> dict:
    """根据名称或代码片段查询股票。

    name 补全职责已移交 Node.js 端（前端 /search-by-name 走本地 stocks 表），
    此处保留方法以兼容 rpc.py 映射，统一返回空。
    """
    return {"status": "ok", "data": []}
