"""
通达信数据API路由
提供实时行情和K线数据的HTTP接口
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from harness.api.models import ApiResponse, ErrorResponse, KlineBar, StockQuote, error_response, success_response
from harness.core.exceptions import HarnessError, SkillError, TdxConnectionError, ValidationError
from harness.core.logger import get_logger

# 动态加载技能模块
import importlib.util
from pathlib import Path

logger = get_logger(__name__)

# 获取技能路径
skills_base_path = Path(__file__).parent.parent.parent.parent / "skills" / "builtin"
quote_path = skills_base_path / "tdx-realtime-quote"
kline_path = skills_base_path / "tdx-kline"
info_path = skills_base_path / "tdx-stock-info"


def _load_kline_module():
    """动态加载K线技能模块（每次请求时重新加载）"""
    import uuid
    unique_name = f"tdx_kline_dynamic_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(
        unique_name,
        str(kline_path / "skill.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_quote_module():
    """动态加载行情技能模块（每次请求时重新加载）"""
    import uuid
    unique_name = f"tdx_realtime_quote_dynamic_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(
        unique_name,
        str(quote_path / "skill.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_info_module():
    """动态加载股票信息技能模块（每次请求时重新加载）"""
    import uuid
    # 使用唯一的模块名避免缓存
    unique_name = f"tdx_stock_info_dynamic_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(
        unique_name,
        str(info_path / "skill.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

router = APIRouter(prefix="/api/v1/tdx", tags=["通达信数据"])


@router.get("/quote", response_model=ApiResponse)
async def get_quote(
    symbols: str = Query(..., description="股票代码列表，逗号分隔，如 600519.SH,000001.SZ")
) -> ApiResponse:
    """
    获取实时行情数据

    Args:
        symbols: 股票代码列表，逗号分隔

    Returns:
        包含行情数据的响应
    """
    try:
        symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]

        if not symbol_list:
            raise ValidationError("symbols参数不能为空", error_code="API_001")

        logger.info(f"API request: get_quote symbols={symbol_list}")

        # 动态加载技能模块
        quote_module = _load_quote_module()
        data = quote_module.get_realtime_quote(symbol_list)

        # 转换为Pydantic模型
        quotes = [StockQuote(**item) for item in data]

        return success_response(data=[q.model_dump() for q in quotes])

    except ValidationError as e:
        logger.warning(f"Validation error in get_quote", error_code=e.error_code, message=str(e))
        raise HTTPException(
            status_code=400,
            detail=error_response(400, str(e), e.error_code, e.details).model_dump()
        )
    except TdxConnectionError as e:
        logger.error(f"TDX connection error in get_quote", error_code=e.error_code, message=str(e))
        raise HTTPException(
            status_code=503,
            detail=error_response(503, "通达信服务器连接失败", e.error_code, e.details).model_dump()
        )
    except SkillError as e:
        logger.error(f"Skill error in get_quote", error_code=e.error_code, message=str(e))
        raise HTTPException(
            status_code=500,
            detail=error_response(500, str(e), e.error_code, e.details).model_dump()
        )
    except Exception as e:
        logger.error(f"Unexpected error in get_quote", error=str(e))
        raise HTTPException(
            status_code=500,
            detail=error_response(500, "内部错误").model_dump()
        )


@router.get("/kline", response_model=ApiResponse)
async def get_kline(
    symbol: str = Query(..., description="股票代码，如 600519.SH"),
    period: str = Query(..., description="K线周期: 1min,5min,15min,30min,60min,daily,weekly,monthly"),
    start_date: str | None = Query(None, description="开始日期，格式 YYYY-MM-DD"),
    end_date: str | None = Query(None, description="结束日期，格式 YYYY-MM-DD")
) -> ApiResponse:
    """
    获取K线数据

    Args:
        symbol: 股票代码
        period: K线周期
        start_date: 开始日期（可选）
        end_date: 结束日期（可选）

    Returns:
        包含K线数据的响应
    """
    try:
        logger.info(f"API request: get_kline symbol={symbol} period={period}")

        # 动态加载技能模块
        kline_module = _load_kline_module()
        data = kline_module.get_kline(symbol, period, start_date, end_date)

        # 转换为Pydantic模型
        bars = [KlineBar(**item) for item in data]

        return success_response(data=[b.model_dump() for b in bars])

    except ValidationError as e:
        logger.warning(f"Validation error in get_kline", error_code=e.error_code, message=str(e))
        raise HTTPException(
            status_code=400,
            detail=error_response(400, str(e), e.error_code, e.details).model_dump()
        )
    except TdxConnectionError as e:
        logger.error(f"TDX connection error in get_kline", error_code=e.error_code, message=str(e))
        raise HTTPException(
            status_code=503,
            detail=error_response(503, "通达信服务器连接失败", e.error_code, e.details).model_dump()
        )
    except SkillError as e:
        logger.error(f"Skill error in get_kline", error_code=e.error_code, message=str(e))
        raise HTTPException(
            status_code=500,
            detail=error_response(500, str(e), e.error_code, e.details).model_dump()
        )
    except Exception as e:
        logger.error(f"Unexpected error in get_kline", error=str(e))
        raise HTTPException(
            status_code=500,
            detail=error_response(500, "内部错误").model_dump()
        )


@router.get("/health", response_model=ApiResponse)
async def health_check() -> ApiResponse:
    """
    健康检查端点

    测试通达信连接状态
    """
    try:
        # 尝试连接通达信服务器
        from mootdx.quotes import Quotes
        from mootdx import HQ_HOSTS
        from harness.core.config import get_config

        config = get_config()

        if len(config.tdx.servers) > 0:
            server = config.tdx.servers[0]
            host, port = server.host, server.port
        else:
            _, host, port = HQ_HOSTS[0]

        # 尝试创建连接（不发送实际请求）
        quotes = Quotes.factory(market='std', timeout=config.tdx.timeout, host=host, port=port)

        return success_response(data={
            "status": "healthy",
            "tdx_server": f"{host}:{port}",
            "connection": "ok"
        })

    except Exception as e:
        logger.warning(f"TDX health check failed", error=str(e))
        return success_response(data={
            "status": "degraded",
            "error": str(e)
        })


@router.get("/stock-info", response_model=ApiResponse)
async def get_stock_info(
    symbol: str = Query(..., description="股票代码，如 600519.SH")
) -> ApiResponse:
    """
    获取股票详细信息

    Args:
        symbol: 股票代码

    Returns:
        包含股票详细信息的响应
    """
    try:
        logger.info(f"API request: get_stock_info symbol={symbol}")

        # 动态加载技能模块
        info_module = _load_info_module()
        data = info_module.get_stock_info(symbol)

        # 详细调试：检查data中的每个字段
        logger.info(f"Stock info retrieved, name={repr(data.get('name'))}, type={type(data.get('name'))}")
        logger.info(f"Stock info data keys: {list(data.keys())}")
        logger.info(f"Stock info raw data: {data}")
        return success_response(data=data)

    except ValidationError as e:
        logger.warning(f"Validation error in get_stock_info", error_code=e.error_code, message=str(e))
        raise HTTPException(
            status_code=400,
            detail=error_response(400, str(e), e.error_code, e.details).model_dump()
        )
    except TdxConnectionError as e:
        logger.error(f"TDX connection error in get_stock_info", error_code=e.error_code, message=str(e))
        raise HTTPException(
            status_code=503,
            detail=error_response(503, "通达信服务器连接失败", e.error_code, e.details).model_dump()
        )
    except SkillError as e:
        logger.error(f"Skill error in get_stock_info", error_code=e.error_code, message=str(e))
        raise HTTPException(
            status_code=500,
            detail=error_response(500, str(e), e.error_code, e.details).model_dump()
        )
    except Exception as e:
        logger.error(f"Unexpected error in get_stock_info", error=str(e))
        raise HTTPException(
            status_code=500,
            detail=error_response(500, "内部错误").model_dump()
        )
