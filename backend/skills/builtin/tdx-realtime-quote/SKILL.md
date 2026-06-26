# TDX Realtime Quote Skill

## 技能元数据
- **名称**: tdx-realtime-quote
- **版本**: 0.1.0
- **类别**: built-in
- **描述**: 获取通达信实时股票行情数据
- **作者**: Harness Team
- **创建时间**: 2026-04-02

## 功能描述
从通达信服务器获取实时股票报价数据，包括：
- 实时五档行情
- 分时成交数据
- 市场统计数据

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

### get_realtime_quote(symbols: list[str]) -> list[dict]
获取实时行情数据

**参数**:
- symbols: 股票代码列表，格式如 ["600519.SH", "000001.SZ"]

**返回**:
```python
[
    {
        "symbol": "600519.SH",
        "name": "贵州茅台",
        "price": 1680.50,      # 最新价
        "open": 1660.00,       # 开盘价
        "high": 1685.00,       # 最高价
        "low": 1655.00,        # 最低价
        "volume": 1234567,     # 成交量
        "amount": 2087654320,  # 成交额
        "bid1": 1680.00,       # 买一价
        "bid2": 1679.50,
        "bid3": 1679.00,
        "bid4": 1678.50,
        "bid5": 1678.00,
        "ask1": 1680.50,       # 卖一价
        "ask2": 1681.00,
        "ask3": 1681.50,
        "ask4": 1682.00,
        "ask5": 1682.50,
        "timestamp": "2026-04-02 14:59:59"
    }
]
```

**异常**:
- TdxConnectionError: 连接通达信服务器失败
- ValidationError: 股票代码格式错误
- SkillError: 数据获取失败

## 测试用例
- 测试单个股票查询
- 测试批量股票查询
- 测试无效股票代码
- 测试服务器连接失败
- 测试数据格式验证

## 使用示例
```python
from harness.skills.built_in.tdx_realtime_quote import get_realtime_quote

# 获取单只股票
data = get_realtime_quote(["600519.SH"])
print(data[0]["price"])

# 批量获取
data = get_realtime_quote(["600519.SH", "000001.SZ"])
```
