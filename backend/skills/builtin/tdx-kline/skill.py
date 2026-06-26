"""
TDX Kline Skill Implementation
通达信K线数据获取技能实现
"""

from datetime import datetime
from typing import Any

# 修复sys.stdout和sys.stderr以避免tqdm在API环境中出现OSError
import sys

class SafeStream:
    """创建一个安全的流对象，忽略flush等可能失败的操作"""
    def __init__(self, original_stream):
        self.original_stream = original_stream

    def write(self, data):
        try:
            return self.original_stream.write(data)
        except (OSError, ValueError):
            return len(data) if data else 0

    def flush(self):
        try:
            return self.original_stream.flush()
        except (OSError, ValueError):
            pass

    def __getattr__(self, name):
        return getattr(self.original_stream, name)

# 在导入mootdx之前修复stdout和stderr
sys.stdout = SafeStream(sys.stdout)
sys.stderr = SafeStream(sys.stderr)

from mootdx.quotes import Quotes

from harness.core.config import get_config
from harness.core.exceptions import SkillError, TdxConnectionError, ValidationError
from harness.core.logger import get_logger

logger = get_logger(__name__)


def _try_connect_local() -> Quotes | None:
    """
    尝试连接本地通达信进程服务

    通达信标准版通过进程间通信提供数据接口，无需监听7709端口
    mootdx会自动检测并连接到通达信进程

    Returns:
        Quotes客户端实例，如果通达信未运行则返回None
    """
    try:
        # 不指定host/port，让mootdx自动检测通达信进程
        quotes = Quotes.factory(
            market='std',
            timeout=3  # 3秒超时
        )

        # 快速测试连接
        test_data = quotes.stocks(market=1)
        if test_data is not None and len(test_data) > 0:
            logger.info("Connected to local TDX process service (auto-detected)")
            return quotes
        else:
            logger.debug("Local TDX service returned no data")
            return None
    except Exception as e:
        logger.debug(f"Local TDX service not available: {e}")
        return None


def _connect_tdx_service() -> Quotes:
    """
    连接到通达信服务
    优先使用本地通达信进程服务，本地未启动时使用远程服务器

    Returns:
        Quotes客户端实例

    Raises:
        TdxConnectionError: 连接失败
    """
    # 先尝试本地通达信进程服务
    logger.info("Attempting to connect to local TDX process service first...")
    local_quotes = _try_connect_local()

    if local_quotes is not None:
        return local_quotes

    # 本地服务不可用，使用远程服务器
    logger.info("Local TDX service not available, falling back to remote server...")
    config = get_config()

    if len(config.tdx.servers) == 0:
        logger.warning("No TDX servers configured, using default servers")
        from mootdx import HQ_HOSTS
        _, host, port = HQ_HOSTS[0]
    else:
        server = config.tdx.servers[0]
        host, port = server.host, server.port

    logger.debug(f"Connecting to remote TDX server: {host}:{port}")

    try:
        quotes = Quotes.factory(
            market='std',
            timeout=config.tdx.timeout,
            host=host,
            port=port
        )
        logger.info(f"Connected to remote TDX server: {host}:{port}")
        return quotes

    except Exception as e:
        logger.error(f"Failed to connect to TDX server", error=str(e), host=host, port=port)
        raise TdxConnectionError(
            f"Failed to connect to TDX server {host}:{port}: {e}",
            error_code="TDX_003",
            details={"host": host, "port": port, "error": str(e)}
        ) from e

# K线周期映射
PERIOD_MAP = {
    "1min": 8,      # 1分钟
    "5min": 0,      # 5分钟
    "15min": 1,     # 15分钟
    "30min": 2,     # 30分钟
    "60min": 3,     # 60分钟
    "daily": 9,     # 日K
    "weekly": 5,    # 周K
    "monthly": 6,   # 月K
}


def _validate_symbol(symbol: str) -> tuple[str, int]:
    """
    验证股票代码格式并返回市场代码

    Args:
        symbol: 股票代码，如 "600519.SH" 或 "000001.SZ"

    Returns:
        (代码, 市场代码) 元组
        市场代码: 1=上海, 0=深圳

    Raises:
        ValidationError: 股票代码格式错误
    """
    if not symbol or "." not in symbol:
        raise ValidationError(
            f"Invalid symbol format: {symbol}. Expected format: 600519.SH or 000001.SZ",
            error_code="TDX_001",
            details={"symbol": symbol}
        )

    code, market_suffix = symbol.split(".")
    market_suffix = market_suffix.upper()

    if market_suffix == "SH":
        market = 1  # 上海市场
    elif market_suffix == "SZ":
        market = 0  # 深圳市场
    else:
        raise ValidationError(
            f"Invalid market suffix: {market_suffix}. Expected: SH or SZ",
            error_code="TDX_002",
            details={"symbol": symbol, "suffix": market_suffix}
        )

    logger.debug(f"Symbol validated: {symbol} -> code={code}, market={market}")
    return code, market


def _validate_period(period: str) -> int:
    """
    验证K线周期并返回mootdx周期代码

    Args:
        period: K线周期字符串

    Returns:
        mootdx周期代码

    Raises:
        ValidationError: 周期参数错误
    """
    if period not in PERIOD_MAP:
        raise ValidationError(
            f"Invalid period: {period}. Supported: {list(PERIOD_MAP.keys())}",
            error_code="TDX_005",
            details={"period": period, "supported": list(PERIOD_MAP.keys())}
        )

    period_code = PERIOD_MAP[period]
    logger.debug(f"Period validated: {period} -> code={period_code}")
    return period_code


def _parse_date(date_str: str | None) -> datetime | None:
    """
    解析日期字符串

    Args:
        date_str: 日期字符串，格式 "YYYY-MM-DD"

    Returns:
        datetime对象或None
    """
    if not date_str:
        return None

    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as e:
        raise ValidationError(
            f"Invalid date format: {date_str}. Expected: YYYY-MM-DD",
            error_code="TDX_006",
            details={"date": date_str}
        ) from e


def _filter_by_date(data: list[dict], start_date: datetime | None, end_date: datetime | None) -> list[dict]:
    """
    按日期范围过滤K线数据

    Args:
        data: K线数据列表
        start_date: 开始日期
        end_date: 结束日期

    Returns:
        过滤后的数据列表
    """
    if not start_date and not end_date:
        return data

    filtered = []
    for bar in data:
        date_str = bar["date"]
        # 处理可能带时间的日期字符串，如 "2025-01-02 15:00"
        if " " in date_str:
            date_str = date_str.split(" ")[0]
        bar_date = datetime.strptime(date_str, "%Y-%m-%d")

        if start_date and bar_date < start_date:
            continue
        if end_date and bar_date > end_date:
            continue

        filtered.append(bar)

    logger.debug(f"Filtered data: {len(data)} -> {len(filtered)} bars")
    return filtered


def get_kline(
    symbol: str,
    period: str,
    start_date: str | None = None,
    end_date: str | None = None,
    quotes: Quotes | None = None
) -> list[dict[str, Any]]:
    """
    获取K线数据

    Args:
        symbol: 股票代码，如 "600519.SH"
        period: K线周期（1min, 5min, 15min, 30min, 60min, daily, weekly, monthly）
        start_date: 开始日期（可选），格式 "2024-01-01"
        end_date: 结束日期（可选），格式 "2024-12-31"
        quotes: 已建立的 TDX 连接（可选），不传则内部建连

    Returns:
        K线数据列表

    Raises:
        ValidationError: 参数错误
        TdxConnectionError: 连接失败
        SkillError: 数据获取失败
    """
    logger.info(f"Getting kline data: {symbol} period={period}")

    # 验证参数
    code, market = _validate_symbol(symbol)
    period_code = _validate_period(period)

    # 解析日期
    start_dt = _parse_date(start_date) if start_date else None
    end_dt = _parse_date(end_date) if end_date else None

    # 连接通达信服务（优先本地，备用远程）
    quotes = quotes or _connect_tdx_service()

    # 获取K线数据
    try:
        # mootdx的bars()方法获取K线数据
        # 参数：symbol(股票代码), frequency(K线类型), start(起始位置), offset(获取数量)
        # 注意：不使用market参数，直接在symbol中区分
        data = quotes.bars(symbol=code, frequency=period_code, start=0, offset=1000)

        if data is None or len(data) == 0:
            logger.warning(f"No kline data returned for {symbol}")
            # 返回空数组而不是抛出错误，空数据是正常情况
            return []

        # 转换数据格式
        results = []
        for _, row in data.iterrows():
            results.append({
                "symbol": symbol,
                "date": row["datetime"].strftime("%Y-%m-%d") if hasattr(row["datetime"], "strftime") else str(row["datetime"]).split(" ")[0],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
                "amount": float(row.get("amount", 0))
            })

        # 按日期升序排序（mootdx返回的是降序）
        results.sort(key=lambda x: x["date"])

        # 日期过滤
        results = _filter_by_date(results, start_dt, end_dt)

        logger.info(f"Successfully retrieved {len(results)} kline bars")
        return results

    except SkillError:
        raise
    except Exception as e:
        logger.error(f"Failed to get kline data", error=str(e), symbol=symbol, period=period)
        raise SkillError(
            f"Failed to get kline data for {symbol}: {e}",
            error_code="TDX_008",
            details={"symbol": symbol, "period": period, "error": str(e)}
        ) from e


class TdxKlineSkill:
    """
    通达信K线数据技能类

    提供面向对象的接口，便于集成到Agent框架中
    """

    def __init__(self):
        self._quotes: Quotes | None = None
        self.description = "通达信K线数据技能，获取股票K线数据"
        self.name = "tdx-kline"
        logger.info("TdxKlineSkill initialized")

    def get_tool_definition(self) -> dict[str, Any]:
        """
        获取Claude Agent SDK工具定义

        Returns:
            工具定义字典
        """
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码，如600519.SH"
                    },
                    "period": {
                        "type": "string",
                        "enum": ["1min", "5min", "15min", "30min", "60min", "daily", "weekly", "monthly"],
                        "description": "K线周期"
                    },
                    "start_date": {
                        "type": "string",
                        "description": "开始日期，格式2024-01-01"
                    },
                    "end_date": {
                        "type": "string",
                        "description": "结束日期，格式2024-12-31"
                    }
                },
                "required": ["symbol", "period"]
            }
        }

    def connect(self) -> None:
        """连接到通达信服务（优先本地，备用远程）"""
        if self._quotes is None:
            self._quotes = _connect_tdx_service()

    def disconnect(self) -> None:
        """断开连接"""
        self._quotes = None
        logger.info("Disconnected from TDX server")

    def get_bars(
        self,
        symbol: str,
        period: str,
        start_date: str | None = None,
        end_date: str | None = None
    ) -> list[dict[str, Any]]:
        """
        获取K线数据

        Args:
            symbol: 股票代码
            period: K线周期
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            K线数据列表
        """
        return get_kline(symbol, period, start_date, end_date)

    def __enter__(self):
        """上下文管理器入口"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器退出"""
        self.disconnect()
