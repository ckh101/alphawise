# Skills 目录

本目录存放所有的 Skill（技能）模块。

## 目录结构

```
skills/
├── built-in/     # 系统内置 Skill
│   ├── tdx-realtime-quote/
│   ├── tdx-kline/
│   ├── tech-indicator/
│   └── ...
└── custom/       # 用户自定义 Skill
    └── user-skill-1/
```

## Skill 标准结构

每个 Skill 必须遵循以下标准结构：

```
skill-name/
├── SKILL.md        # 必需：技能描述文件
├── scripts/        # 可选：执行脚本
│   └── main.py
├── references/     # 可选：参考文档
│   └── api-guide.md
└── assets/         # 可选：资源模板
    └── template.json
```

## SKILL.md 格式

```markdown
---
name: 技能名称
description: 技能描述
version: 1.0.0
author: 作者
---

## 功能描述

[详细描述技能功能]

## 输入参数

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| symbol | str | 是 | 股票代码 |

## 输出格式

[描述输出数据结构]

## 调用示例

```python
result = skill.execute(symbol="600519.SH")
```

## 执行入口

`scripts/main.py`

## 依赖

- mootdx >= 0.4.0
```

## 内置 Skill 列表

| Skill 名称 | 功能描述 | 状态 |
|-----------|----------|------|
| tdx-realtime-quote | 通达信实时行情查询 | 待开发 |
| tdx-kline | K线数据获取 | 待开发 |
| tech-indicator | 技术指标计算 | 待开发 |
| news-crawler | 新闻公告抓取 | 待开发 |
| strategy-backtest | 策略回测 | 待开发 |
| risk-alert | 风险预警 | 待开发 |
