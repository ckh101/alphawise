"""
TDX Realtime Quote Skill Implementation
通达信实时行情获取技能实现
优先使用本地通达信进程服务，本地未启动时使用远程服务器
"""

from datetime import datetime
from typing import Any
import time

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

# 股票名称缓存：避免每次请求都加载全部股票名称
# 缓存结构: {code: name}
# 注意：由于API层每次动态加载模块，缓存使用全局文件系统存储
import hashlib
import json
from pathlib import Path

_CACHE_DIR = Path("./data/cache")
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_CACHE_FILE = _CACHE_DIR / "stock_names.json"
_CACHE_EXPIRY = 300  # 5分钟过期


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


def _connect_remote() -> Quotes:
    """
    连接到远程通达信服务器

    Returns:
        Quotes客户端实例

    Raises:
        TdxConnectionError: 连接失败
    """
    config = get_config()

    if len(config.tdx.servers) == 0:
        logger.warning("No TDX servers configured, using default servers")
        # 使用mootdx默认服务器
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


def _connect_tdx() -> Quotes:
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
    return _connect_remote()


def _get_stock_names(quotes: Quotes) -> dict[str, str]:
    """
    实时从TDX获取股票名称映射（全量，约 4-5s）。
    调用方应自行做缓存，避免每次 quote 都触发。
    """
    stock_names = {}
    try:
        load_start = time.time()
        for market_code in [0, 1]:  # 0=深圳, 1=上海
            stocks_data = quotes.stocks(market=market_code)
            if stocks_data is not None and len(stocks_data) > 0:
                for _, row in stocks_data.iterrows():
                    code = row.get('code')
                    name = row.get('name')
                    if code and name:
                        stock_names[code] = str(name)

        load_time = time.time() - load_start
        logger.info(f"Loaded {len(stock_names)} stock names in {load_time:.2f}s")

    except Exception as e:
        logger.warning(f"Failed to load stock names from TDX: {e}")

    return stock_names


def get_realtime_quote(symbols: list[str], quotes: Quotes | None = None) -> list[dict[str, Any]]:
    """
    获取实时行情数据

    Args:
        symbols: 股票代码列表，如 ["600519.SH", "000001.SZ"]
        quotes: 已建立的 TDX 连接（可选），不传则内部建连

    Returns:
        行情数据列表

    Raises:
        ValidationError: 股票代码格式错误
        TdxConnectionError: 连接通达信服务器失败
        SkillError: 数据获取失败
    """
    if not symbols:
        logger.warning("Empty symbols list provided")
        return []

    logger.info(f"Getting realtime quote for {len(symbols)} symbols")

    # 连接通达信服务（优先本地，备用远程）
    quotes = quotes or _connect_tdx()

    # name 不再在此全量加载（约 4-5s），由调用方（handler）从缓存/stocks 表补全
    results = []
    errors = []

    for symbol in symbols:
        try:
            # 验证股票代码
            code, market = _validate_symbol(symbol)

            # 获取实时行情
            # 注意：mootdx 的 quotes() 内部用 get_stock_market 自动判断市场，
            # 000001/000688 等 00 开头的代码会被误判为深圳市场。
            # 显式加上 sh/sz 前缀覆盖自动判断。
            market_prefix = "sh" if market == 1 else "sz"
            data = quotes.quotes(symbol=f"{market_prefix}{code}")

            if data is None or len(data) == 0:
                logger.warning(f"No data returned for symbol: {symbol}")
                errors.append(symbol)
                continue

            # 解析数据
            row = data.iloc[0]
            result = {
                "symbol": symbol,
                "name": "",  # 由 handler 层补全
                "price": float(row.get("price", 0)),          # 最新价
                "last_close": float(row.get("last_close", 0)), # 昨收价
                "open": float(row.get("open", 0)),            # 开盘价
                "high": float(row.get("high", 0)),            # 最高价
                "low": float(row.get("low", 0)),              # 最低价
                "volume": int(row.get("volume", 0)),          # 成交量（手）
                "amount": float(row.get("amount", 0)),        # 成交额
                "bid1": float(row.get("bid1", 0)),            # 买一价
                "bid2": float(row.get("bid2", 0)),
                "bid3": float(row.get("bid3", 0)),
                "bid4": float(row.get("bid4", 0)),
                "bid5": float(row.get("bid5", 0)),
                "ask1": float(row.get("ask1", 0)),            # 卖一价
                "ask2": float(row.get("ask2", 0)),
                "ask3": float(row.get("ask3", 0)),
                "ask4": float(row.get("ask4", 0)),
                "ask5": float(row.get("ask5", 0)),
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

            results.append(result)
            logger.debug(f"Got quote for {symbol}: price={result['price']}")

        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Failed to get quote for {symbol}", error=str(e))
            errors.append(symbol)

    if errors:
        logger.warning(f"Failed to get data for {len(errors)} symbols", symbols=errors)

    if not results and symbols:
        raise SkillError(
            f"Failed to get any quote data for {len(symbols)} symbols",
            error_code="TDX_004",
            details={"symbols": symbols, "errors": errors}
        )

    logger.info(f"Successfully retrieved {len(results)} quotes")
    return results


class TdxRealtimeQuoteSkill:
    """
    通达信实时行情技能类

    提供面向对象的接口，便于集成到Agent框架中
    """

    def __init__(self):
        self._quotes: Quotes | None = None
        self.description = "通达信实时行情技能，获取股票实时报价数据（优先本地通达信进程服务）"
        self.name = "tdx-realtime-quote"
        logger.info("TdxRealtimeQuoteSkill initialized")

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
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "股票代码列表，如['600519.SH', '000001.SZ']"
                    }
                },
                "required": ["symbols"]
            }
        }

    def connect(self) -> None:
        """连接到通达信服务（优先本地，备用远程）"""
        if self._quotes is None:
            self._quotes = _connect_tdx()

    def disconnect(self) -> None:
        """断开连接"""
        self._quotes = None
        logger.info("Disconnected from TDX service")

    def get_quote(self, symbols: list[str]) -> list[dict[str, Any]]:
        """
        获取实时行情

        Args:
            symbols: 股票代码列表

        Returns:
            行情数据列表
        """
        return get_realtime_quote(symbols)

    def __enter__(self):
        """上下文管理器入口"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器退出"""
        self.disconnect()
