"""
TDX Stock Info Skill Implementation
通达信股票详细信息获取技能实现
"""

import re
from typing import Any

import pandas as pd

# 修复sys.stdout和sys.stderr以避免tqdm在API环境中出现OSError
import sys
import io

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


def get_stock_info(symbol: str, quotes: Any = None) -> dict[str, Any]:
    """
    获取股票详细信息

    Args:
        symbol: 股票代码，如 "600519.SH"
        quotes: 已建立的 TDX 连接（可选），不传则内部建连

    Returns:
        股票详细信息字典。name 字段不在内部通过 stocks() 全量拉取，
        由调用方（Node.js 端）从 stocks 表补齐。

    Raises:
        ValidationError: 参数错误
        TdxConnectionError: 连接失败
        SkillError: 数据获取失败
    """
    logger.info(f"Getting stock info: {symbol}")

    # 验证参数
    code, market = _validate_symbol(symbol)

    # 复用传入的连接，或内部建连
    if quotes is None:
        import importlib.util
        from pathlib import Path
        _quote_skill_path = Path(__file__).resolve().parent.parent / "tdx-realtime-quote" / "skill.py"
        _spec = importlib.util.spec_from_file_location("_tdx_quote_conn", str(_quote_skill_path))
        _qmod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_qmod)
        try:
            quotes = _qmod._connect_tdx()
            logger.info("Connected to TDX (internal connect)")
        except Exception as e:
            logger.error("Failed to connect to TDX server", error=str(e))
            raise TdxConnectionError(
                f"Failed to connect to TDX server: {e}",
                error_code="TDX_003",
                details={"error": str(e)}
            ) from e

    # name 不在此处通过 stocks() 全量拉取（4-5s），由 Node.js 端从 stocks 表补齐
    stock_name = ""

    # 从 F10 公司概况提取准确的行业信息
    f10_industry = ""
    try:
        raw_f10 = quotes.F10(symbol=code)
        if raw_f10 and isinstance(raw_f10, dict):
            corp = raw_f10.get("公司概况", "")
            m = re.search(r"行业类别\s*｜\s*([^\s｜]+)", corp)
            if m:
                f10_industry = m.group(1).strip("｜").strip()
                logger.info(f"F10 industry for {symbol}: {f10_industry}")
    except Exception as e:
        logger.warning(f"F10 industry lookup failed for {symbol}: {e}")

    try:
        # 获取财务和公司信息
        finance_data = quotes.finance(market=market, symbol=code)

        if finance_data is None or len(finance_data) == 0:
            logger.warning(f"No stock info found for {symbol}")
            raise SkillError(
                f"No stock info available for {symbol}",
                error_code="TDX_009",
                details={"symbol": symbol}
            )

        # 提取第一条记录
        row = finance_data.iloc[0]

        # 行业映射（通达信 finance API 的 industry 字段，通过实际采样验证）
        industry_map = {
            0: "金融",
            1: "金融",
            3: "钢铁",
            5: "石油石化",
            6: "公路交通",
            7: "汽车",
            8: "交通运输",
            9: "综合",
            10: "旅游酒店",
            11: "房地产",
            12: "商贸零售",
            13: "商贸零售",
            14: "食品饮料",
            15: "纺织服装",
            16: "电力",
            17: "农林牧渔",
            18: "传媒",
            19: "化工",
            20: "煤炭",
            21: "建筑工程",
            22: "建筑工程",
            23: "家用电器",
            24: "通信",
            25: "综合",
            26: "机械设备",
            27: "化工",
            28: "化工",
            29: "光伏",
            30: "医药生物",
            31: "房地产",
            34: "医药生物",
            35: "电子",
            36: "有色金属",
            37: "食品饮料",
            38: "轻工制造",
            39: "环保",
            41: "纺织服装",
            42: "水务",
            43: "电气设备",
            46: "包装印刷",
            47: "化工",
            48: "建筑材料",
            49: "国防军工",
            50: "休闲服务",
            51: "电子",
        }

        province_map = {
            1: "上海", 2: "新疆", 3: "上海", 4: "甘肃", 5: "上海",
            6: "青海", 7: "北京", 8: "陕西", 9: "天津",
            10: "广西", 11: "河北", 12: "广东", 13: "河南",
            14: "辽宁", 15: "山东", 16: "上海", 17: "山西",
            18: "广东", 19: "湖北", 20: "福建", 21: "湖南",
            22: "江西", 23: "四川", 24: "安徽", 25: "重庆",
            26: "江苏", 27: "上海", 28: "浙江", 29: "贵州",
            30: "海南", 32: "内蒙古",
        }

        industry_code = int(row.get("industry", 0)) if row.get("industry") else 0
        province_code = int(row.get("province", 0)) if row.get("province") else 0

        # 构建返回数据
        logger.info(f"Building result with stock_name: {repr(stock_name)}")
        result = {
            "symbol": symbol,
            "code": code,
            "name": stock_name,
            # 基本信息
            "industry": f10_industry or industry_map.get(industry_code, f"行业{industry_code}"),
            "province": province_map.get(province_code, f"省份{province_code}"),
            "ipo_date": str(row.get("ipo_date", "")) if row.get("ipo_date") else "",
            # 股本信息 (单位：股)
            "total_shares": int(row.get("zongguben", 0)) if row.get("zongguben") else 0,
            "float_shares": int(row.get("liutongguben", 0)) if row.get("liutongguben") else 0,
            "state_shares": int(row.get("guojiagu", 0)) if row.get("guojiagu") else 0,
            # 财务数据 (单位：元)
            "total_assets": float(row.get("zongzichan", 0)) if row.get("zongzichan") else 0,
            "net_assets": float(row.get("jingzichan", 0)) if row.get("jingzichan") else 0,
            "current_assets": float(row.get("liudongzichan", 0)) if row.get("liudongzichan") else 0,
            "fixed_assets": float(row.get("gudingzichan", 0)) if row.get("gudingzichan") else 0,
            # 经营数据 (单位：元)
            "revenue": float(row.get("zhuyingshouru", 0)) if row.get("zhuyingshouru") else 0,
            "net_profit": float(row.get("jinglirun", 0)) if row.get("jinglirun") else 0,
            "operating_profit": float(row.get("yingyelirun", 0)) if row.get("yingyelirun") else 0,
            # 每股指标 (单位：元)
            "eps": float(row.get("meigujingzichan", 0)) if row.get("meigujingzichan") else 0,
            # 其他
            "shareholders": int(row.get("gudongrenshu", 0)) if row.get("gudongrenshu") else 0,
            "updated_date": str(row.get("updated_date", "")) if row.get("updated_date") else "",
        }

        logger.info(f"Successfully retrieved stock info for {symbol}")
        return result

    except SkillError:
        raise
    except Exception as e:
        logger.error(f"Failed to get stock info", error=str(e), symbol=symbol)
        raise SkillError(
            f"Failed to get stock info for {symbol}: {e}",
            error_code="TDX_010",
            details={"symbol": symbol, "error": str(e)}
        ) from e


def get_f10_data(symbol: str, sections: list[str] | None = None) -> dict[str, Any]:
    """
    获取 F10 文本数据（解码后的原始文本）

    Args:
        symbol: 股票代码，如 "600519.SH"
        sections: 需要的板块列表，默认取 ["财务分析", "行业分析", "分红扩股"]

    Returns:
        dict: {symbol, sections: {板块名: 解码后文本}}
    """
    logger.info(f"Getting F10 data: {symbol}")
    code, market = _validate_symbol(symbol)

    if sections is None:
        sections = ["财务分析", "行业分析", "分红扩股"]

    config = get_config()
    if len(config.tdx.servers) == 0:
        from mootdx import HQ_HOSTS
        _, host, port = HQ_HOSTS[0]
    else:
        server = config.tdx.servers[0]
        host, port = server.host, server.port

    try:
        quotes = Quotes.factory(market='std', timeout=config.tdx.timeout, host=host, port=port)
        raw_f10 = quotes.F10(symbol=code)
    except Exception as e:
        logger.error(f"F10 data fetch failed for {symbol}: {e}")
        return {"symbol": symbol, "sections": {}, "error": str(e)}

    decoded = {}
    if raw_f10 and isinstance(raw_f10, dict):
        for key, content in raw_f10.items():
            if sections and key not in sections:
                continue
            try:
                decoded[key] = content.encode('latin1').decode('gbk')
            except (UnicodeEncodeError, UnicodeDecodeError, AttributeError):
                decoded[key] = str(content) if content else ""

    logger.info(f"F10 data retrieved for {symbol}: {list(decoded.keys())}")
    return {"symbol": symbol, "sections": decoded}


class TdxStockInfoSkill:
    """
    通达信股票详细信息技能类

    提供面向对象的接口，便于集成到Agent框架中
    """

    def __init__(self):
        self._quotes: Quotes | None = None
        logger.info("TdxStockInfoSkill initialized")

    def connect(self) -> None:
        """连接到通达信服务器"""
        if self._quotes is None:
            config = get_config()
            if len(config.tdx.servers) == 0:
                from mootdx import HQ_HOSTS
                _, host, port = HQ_HOSTS[0]
            else:
                server = config.tdx.servers[0]
                host, port = server.host, server.port

            self._quotes = Quotes.factory(
                market='std',
                timeout=config.tdx.timeout,
                host=host,
                port=port
            )
            logger.info(f"Connected to TDX server: {host}:{port}")

    def disconnect(self) -> None:
        """断开连接"""
        self._quotes = None
        logger.info("Disconnected from TDX server")

    def get_info(self, symbol: str) -> dict[str, Any]:
        """
        获取股票详细信息

        Args:
            symbol: 股票代码

        Returns:
            股票详细信息
        """
        return get_stock_info(symbol)

    def get_f10(self, symbol: str, sections: list[str] | None = None) -> dict[str, Any]:
        """获取 F10 数据"""
        return get_f10_data(symbol, sections)

    def __enter__(self):
        """上下文管理器入口"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器退出"""
        self.disconnect()
