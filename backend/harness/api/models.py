"""
API数据模型
统一的数据响应格式
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StockQuote(BaseModel):
    """股票行情数据模型"""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "symbol": "600519.SH",
                "name": "贵州茅台",
                "price": 1680.50,
                "open": 1660.00,
                "high": 1685.00,
                "low": 1655.00,
                "volume": 1234567,
                "amount": 2087654320.00,
                "bid1": 1680.00,
                "ask1": 1680.50,
                "timestamp": "2026-04-02 14:59:59"
            }
        }
    )

    symbol: str = Field(..., description="股票代码，如 600519.SH")
    name: str = Field(..., description="股票名称")
    price: float = Field(..., description="最新价")
    open: float = Field(..., description="开盘价")
    high: float = Field(..., description="最高价")
    low: float = Field(..., description="最低价")
    volume: int = Field(..., description="成交量（手）")
    amount: float = Field(..., description="成交额（元）")
    bid1: float = Field(..., description="买一价")
    bid2: float = Field(0, description="买二价")
    bid3: float = Field(0, description="买三价")
    bid4: float = Field(0, description="买四价")
    bid5: float = Field(0, description="买五价")
    ask1: float = Field(..., description="卖一价")
    ask2: float = Field(0, description="卖二价")
    ask3: float = Field(0, description="卖三价")
    ask4: float = Field(0, description="卖四价")
    ask5: float = Field(0, description="卖五价")
    timestamp: str = Field(..., description="时间戳")


class KlineBar(BaseModel):
    """K线数据模型"""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "symbol": "600519.SH",
                "date": "2024-01-02",
                "open": 1660.00,
                "high": 1685.00,
                "low": 1655.00,
                "close": 1680.00,
                "volume": 1234567,
                "amount": 2087654320.00
            }
        }
    )

    symbol: str = Field(..., description="股票代码")
    date: str = Field(..., description="日期，格式 YYYY-MM-DD")
    open: float = Field(..., description="开盘价")
    high: float = Field(..., description="最高价")
    low: float = Field(..., description="最低价")
    close: float = Field(..., description="收盘价")
    volume: int = Field(..., description="成交量（手）")
    amount: float = Field(..., description="成交额（元）")


class ApiResponse(BaseModel):
    """统一API响应格式"""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": 0,
                "message": "success",
                "data": {},
                "timestamp": "2026-04-02 14:59:59"
            }
        }
    )

    code: int = Field(..., description="响应码，0表示成功")
    message: str = Field(..., description="响应消息")
    data: Any = Field(None, description="响应数据")
    timestamp: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


class ErrorResponse(BaseModel):
    """错误响应格式"""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": 400,
                "message": "Invalid parameter",
                "error_code": "TDX_001",
                "details": {"symbol": "INVALID"},
                "timestamp": "2026-04-02 14:59:59"
            }
        }
    )

    code: int = Field(..., description="错误码")
    message: str = Field(..., description="错误消息")
    error_code: str | None = Field(None, description="业务错误码")
    details: dict[str, Any] | None = Field(None, description="错误详情")
    timestamp: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


class ClaudeExecuteRequest(BaseModel):
    """Claude执行请求"""
    prompt: str = Field(..., description="任务描述或提示词")
    task_type: str = Field(default="chat", description="任务类型")
    session_id: str | None = Field(default=None, description="会话ID")
    file_context: dict[str, str] | None = Field(default=None, description="文件上下文 {filename, content_text}")


class BacktestRequest(BaseModel):
    """策略回测请求"""
    symbol: str = Field(..., description="股票代码，如 600519.SH")
    strategy: str = Field(..., description="策略名称: ma_crossover, macd, rsi, bollinger_band")
    params: dict[str, Any] | None = Field(None, description="策略参数（可选，使用默认值）")
    start_date: str | None = Field(None, description="回测开始日期 YYYY-MM-DD")
    end_date: str | None = Field(None, description="回测结束日期 YYYY-MM-DD")
    initial_cash: float = Field(100000.0, description="初始资金")


# 成功响应构造函数
def success_response(data: Any = None, message: str = "success") -> ApiResponse:
    """构造成功响应"""
    return ApiResponse(code=0, message=message, data=data)


# 错误响应构造函数
def error_response(
    code: int,
    message: str,
    error_code: str | None = None,
    details: dict[str, Any] | None = None
) -> ErrorResponse:
    """构造错误响应"""
    return ErrorResponse(
        code=code,
        message=message,
        error_code=error_code,
        details=details
    )
