# TDX Kline Skill

## 技能元数据
- **名称**: tdx-kline
- **版本**: 0.1.0
- **类别**: built-in
- **描述**: 获取通达信K线数据（日K/周K/月K）
- **作者**: Harness Team
- **创建时间**: 2026-04-02

## 功能描述
从通达信服务器获取历史K线数据，包括：
- 日K线数据
- 周K线数据
- 月K线数据
- 1分钟/5分钟/15分钟/30分钟/60分钟K线

## 依赖项
- mootdx >= 0.11.0
- harness.core.config
- harness.core.logger
- harness.core.exceptions

## 配置要求
需要配置通达信服务器地址列表：
```yaml
tdx:
  servers:
    - host: "119.147.212.81"
      port: 7709
```

## API接口

### get_kline(symbol: str, period: str, start_date: str = None, end_date: str = None) -> list[dict]
获取K线数据

**参数**:
- symbol: 股票代码，格式如 "600519.SH"
- period: K线周期，支持：
  - "1min", "5min", "15min", "30min", "60min"
  - "daily", "weekly", "monthly"
- start_date: 开始日期（可选），格式 "2024-01-01"
- end_date: 结束日期（可选），格式 "2024-12-31"

**返回**:
```python
[
    {
        "symbol": "600519.SH",
        "date": "2024-01-02",
        "open": 1660.00,      # 开盘价
        "high": 1685.00,      # 最高价
        "low": 1655.00,       # 最低价
        "close": 1680.00,     # 收盘价
        "volume": 1234567,    # 成交量（手）
        "amount": 2087654320  # 成交额（元）
    }
]
```

**异常**:
- ValidationError: 股票代码或周期参数错误
- TdxConnectionError: 连接通达信服务器失败
- SkillError: 数据获取失败

## 测试用例
- 测试日K线获取
- 测试周K线获取
- 测试分钟K线获取
- 测试日期范围过滤
- 测试无效股票代码
- 测试无效周期参数

## 使用示例
```python
from harness.skills.builtin.tdx_kline import get_kline

# 获取日K线
data = get_kline("600519.SH", "daily")

# 获取指定日期范围
data = get_kline("600519.SH", "daily", "2024-01-01", "2024-12-31")

# 获取5分钟K线
data = get_kline("600519.SH", "5min")
```
