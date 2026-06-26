- **名称**: tdx-stock-info
- **版本**: 0.1.0
- **描述**: 通达信股票详细信息获取技能
- **类别**: built-in
- **作者**: Harness Team

# TDX Stock Info Skill

通达信股票详细信息获取技能。

## 功能

获取股票的详细信息，包括：

### 基本信息
- 股票代码、名称
- 所属行业、省份
- IPO日期

### 股本信息
- 总股本、流通股本
- 国家股持股数

### 财务数据
- 总资产、净资产
- 流动资产、固定资产
- 营业收入、净利润
- 每股净资产

### 其他
- 股东人数
- 数据更新日期

## 使用方法

```python
from skills.builtin.tdx_stock_info import get_stock_info

# 获取股票详细信息
info = get_stock_info("600519.SH")
print(info)
```

## 返回数据格式

```python
{
    "symbol": "600519.SH",
    "code": "600519",
    "name": "贵州茅台",
    "industry": "酿酒行业",
    "province": "贵州",
    "ipo_date": "2001-08-27",
    "total_shares": 1256270000,      # 总股本（股）
    "float_shares": 1252270000,       # 流通股本（股）
    "total_assets": 2.10875e+12,      # 总资产（元）
    "net_assets": 2.05283e+11,        # 净资产（元）
    "revenue": 1.309e+11,             # 营业收入（元）
    "net_profit": 6.462e+10,          # 净利润（元）
    "eps": 205.28,                    # 每股净资产（元）
    ...
}
```
