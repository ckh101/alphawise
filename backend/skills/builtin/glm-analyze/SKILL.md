# GLM Analyze Skill

## 技能元数据
- **名称**: glm-analyze
- **版本**: 0.1.0
- **类别**: built-in
- **描述**: GLM投研分析技能，支持股票分析和报告生成
- **作者**: Harness Team
- **创建时间**: 2026-04-02

## 功能描述
基于智谱AI GLM-4.7模型的投资分析技能，支持：
- 股票基本面分析
- 技术面分析
- 行业分析
- 投研报告生成
- 风险评估

## 依赖项
- glm-chat skill
- tdx-realtime-quote skill
- tdx-kline skill
- harness.services.glm_client

## API接口

### analyze_stock(symbol: str, analysis_type: str = "comprehensive") -> dict
分析股票

**参数**:
- symbol: 股票代码，如 "600519.SH"
- analysis_type: 分析类型
  - "comprehensive": 综合分析（默认）
  - "fundamental": 基本面分析
  - "technical": 技术面分析
  - "risk": 风险评估

**返回**:
```python
{
    "symbol": "600519.SH",
    "name": "贵州茅台",
    "analysis_type": "comprehensive",
    "summary": "分析摘要",
    "fundamental": {...},
    "technical": {...},
    "risk": {...},
    "recommendation": "买入/持有/卖出",
    "confidence": 0.85,
    "timestamp": "2026-04-02 22:52:00"
}
```

### generate_report(symbols: list[str], report_type: str = "daily") -> dict
生成投研报告

**参数**:
- symbols: 股票代码列表
- report_type: 报告类型
  - "daily": 日报
  - "weekly": 周报
  - "research": 深度研究报告

**返回**:
```python
{
    "report_id": "rpt_20260402",
    "report_type": "daily",
    "symbols": ["600519.SH"],
    "content": "报告内容（Markdown格式）",
    "summary": "报告摘要",
    "generated_at": "2026-04-02 22:52:00"
}
```

## 测试用例
- 测试股票分析
- 测试报告生成
- 测试无效股票代码
- 测试API错误处理

## 使用示例
```python
from harness.skills.builtin.glm_analyze import analyze_stock, generate_report

# 分析股票
analysis = analyze_stock("600519.SH", "comprehensive")
print(analysis["summary"])
print(analysis["recommendation"])

# 生成日报
report = generate_report(["600519.SH", "000001.SZ"], "daily")
print(report["content"])
```
