"""
GLM Analyze Skill Implementation
GLM投研分析技能实现
"""

from datetime import datetime
from typing import Any
import uuid

from harness.core.exceptions import GlmApiError, SkillError, ValidationError
from harness.core.logger import get_logger
from harness.services.glm_agent_client import create_message

logger = get_logger(__name__)


async def analyze_stock(
    symbol: str,
    analysis_type: str = "comprehensive"
) -> dict[str, Any]:
    """
    分析股票

    Args:
        symbol: 股票代码，如 "600519.SH"
        analysis_type: 分析类型（comprehensive, fundamental, technical, risk）

    Returns:
        包含分析结果的字典

    Raises:
        ValidationError: 参数错误
        GlmApiError: GLM API调用失败
        SkillError: 分析处理失败
    """
    # 验证参数
    if not symbol:
        raise ValidationError(
            "股票代码不能为空",
            error_code="ANALYZE_001"
        )

    valid_types = ["comprehensive", "fundamental", "technical", "risk"]
    if analysis_type not in valid_types:
        raise ValidationError(
            f"无效的分析类型: {analysis_type}，支持: {valid_types}",
            error_code="ANALYZE_002",
            details={"analysis_type": analysis_type, "valid_types": valid_types}
        )

    logger.info(f"Stock analysis: symbol={symbol}, type={analysis_type}")

    # 构建分析提示词
    system_prompt = """你是一位专业的证券分析师，擅长股票分析和投资研究。
请基于提供的信息进行客观、专业的分析，给出明确的投资建议。"""

    user_prompt = f"""请对股票 {symbol} 进行{analysis_type}分析。

分析要点：
1. 公司基本情况
2. 行业地位和竞争优势
3. 财务状况分析
4. 技术面分析（如适用）
5. 风险因素
6. 投资建议（买入/持有/卖出）及理由

请以结构化的方式输出分析结果。"""

    try:
        # 调用GLM进行分析（使用Claude Agent SDK风格接口）
        response = await create_message(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3  # 降低温度以获得更一致的分析结果
        )

        # 提取分析内容（Claude Agent SDK风格格式）
        analysis_content = response["content"][0]["text"]

        # 解析分析结果（简化版，实际应使用结构化输出）
        result = {
            "symbol": symbol,
            "analysis_type": analysis_type,
            "content": analysis_content,
            "model": response["model"],
            "usage": {
                "prompt_tokens": response["usage"]["input_tokens"],
                "completion_tokens": response["usage"]["output_tokens"],
                "total_tokens": response["usage"]["input_tokens"] + response["usage"]["output_tokens"]
            },
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        logger.info(f"Stock analysis completed: symbol={symbol}")

        return result

    except GlmApiError:
        raise
    except Exception as e:
        logger.error(f"Stock analysis error: {str(e)}", symbol=symbol, analysis_type=analysis_type)
        raise SkillError(
            f"股票分析失败: {e}",
            error_code="ANALYZE_003",
            details={"symbol": symbol, "analysis_type": analysis_type, "error": str(e)}
        ) from e


async def generate_report(
    symbols: list[str],
    report_type: str = "daily"
) -> dict[str, Any]:
    """
    生成投研报告

    Args:
        symbols: 股票代码列表
        report_type: 报告类型（daily, weekly, research）

    Returns:
        包含报告内容的字典

    Raises:
        ValidationError: 参数错误
        GlmApiError: GLM API调用失败
        SkillError: 报告生成失败
    """
    # 验证参数
    if not symbols or not isinstance(symbols, list):
        raise ValidationError(
            "股票代码列表不能为空",
            error_code="REPORT_001"
        )

    valid_types = ["daily", "weekly", "research"]
    if report_type not in valid_types:
        raise ValidationError(
            f"无效的报告类型: {report_type}，支持: {valid_types}",
            error_code="REPORT_002",
            details={"report_type": report_type, "valid_types": valid_types}
        )

    logger.info(f"Report generation: symbols={symbols}, type={report_type}")

    # 构建报告提示词
    system_prompt = f"""你是一位资深的投资研究总监，擅长撰写专业的证券研究报告。
请生成一份{report_type}投研报告，内容客观、专业、有深度。"""

    symbols_str = "、".join(symbols)
    user_prompt = f"""请为以下股票生成一份{report_type}投研报告：{symbols_str}

报告应包含：
1. 市场概览
2. 个股分析
3. 行业动态
4. 投资策略建议
5. 风险提示

请使用Markdown格式输出。"""

    try:
        # 调用GLM生成报告
        response = await chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.5
        )

        report_content = response["choices"][0]["message"]["content"]

        # 生成报告ID
        report_id = f"rpt_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"

        result = {
            "report_id": report_id,
            "report_type": report_type,
            "symbols": symbols,
            "content": report_content,
            "model": response["model"],
            "usage": response["usage"],
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        logger.info(f"Report generation completed: report_id={report_id}")

        return result

    except GlmApiError:
        raise
    except Exception as e:
        logger.error(f"Report generation error: {str(e)}", symbols=symbols, report_type=report_type)
        raise SkillError(
            f"报告生成失败: {e}",
            error_code="REPORT_003",
            details={"symbols": symbols, "report_type": report_type, "error": str(e)}
        ) from e


class GlmAnalyzeSkill:
    """
    GLM分析技能类

    提供面向对象的接口，便于集成到Agent框架中
    """

    def __init__(self):
        self.description = "GLM股票分析技能，提供综合分析、基本面分析、技术面分析和风险分析"
        self.name = "glm-analyze"
        logger.info("GlmAnalyzeSkill initialized")

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
                    "analysis_type": {
                        "type": "string",
                        "enum": ["comprehensive", "fundamental", "technical", "risk"],
                        "description": "分析类型"
                    }
                },
                "required": ["symbol"]
            }
        }

    async def analyze(
        self,
        symbol: str,
        analysis_type: str = "comprehensive"
    ) -> dict[str, Any]:
        """
        分析股票

        Args:
            symbol: 股票代码
            analysis_type: 分析类型

        Returns:
            分析结果
        """
        return await analyze_stock(symbol, analysis_type)

    async def report(
        self,
        symbols: list[str],
        report_type: str = "daily"
    ) -> dict[str, Any]:
        """
        生成投研报告

        Args:
            symbols: 股票代码列表
            report_type: 报告类型

        Returns:
            报告内容
        """
        return await generate_report(symbols, report_type)
