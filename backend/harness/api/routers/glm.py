"""
GLM API路由
提供GLM对话和分析的HTTP接口
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from harness.api.models import ApiResponse, error_response, success_response
from harness.core.exceptions import GlmApiError, HarnessError, ValidationError
from harness.core.logger import get_logger

# 动态加载技能模块
import importlib.util
from pathlib import Path

logger = get_logger(__name__)

# 加载glm-chat技能
chat_path = Path(__file__).parent.parent.parent.parent / "skills" / "builtin" / "glm-chat"
chat_spec = importlib.util.spec_from_file_location(
    "glm_chat",
    chat_path / "skill.py"
)
chat_module = importlib.util.module_from_spec(chat_spec)
chat_spec.loader.exec_module(chat_module)

# 加载glm-analyze技能
analyze_path = Path(__file__).parent.parent.parent.parent / "skills" / "builtin" / "glm-analyze"
analyze_spec = importlib.util.spec_from_file_location(
    "glm_analyze",
    analyze_path / "skill.py"
)
analyze_module = importlib.util.module_from_spec(analyze_spec)
analyze_spec.loader.exec_module(analyze_module)

router = APIRouter(prefix="/api/v1/glm", tags=["GLM服务"])


@router.post("/chat", response_model=ApiResponse)
async def api_chat(
    message: str = Query(..., description="用户消息"),
    session_id: str | None = Query(None, description="会话ID")
) -> ApiResponse:
    """
    GLM对话接口

    Args:
        message: 用户消息
        session_id: 会话ID（可选）

    Returns:
        包含AI回复的响应
    """
    try:
        logger.info(f"API chat request: session_id={session_id}")

        result = await chat_module.chat(
            message=message,
            session_id=session_id
        )

        return success_response(data=result)

    except ValidationError as e:
        logger.warning(f"Validation error in chat", error_code=e.error_code)
        raise HTTPException(
            status_code=400,
            detail=error_response(400, str(e), e.error_code, e.details).model_dump()
        )
    except GlmApiError as e:
        error_msg = str(e)
        logger.error(f"GLM API error", error=error_msg)

        # 检查是否为API配额错误（通过错误代码检测，避免中文编码问题）
        if ("429" in error_msg or "1113" in error_msg or "APIReachLimitError" in error_msg or
            "quota" in error_msg.lower() or (e.details and "error_type" in e.details and
            "APIReachLimitError" in str(e.details["error_type"]))):
            # 返回503服务不可用，而不是500
            raise HTTPException(
                status_code=503,
                detail=error_response(503, "AI服务暂时不可用，请稍后重试", "GLM_QUOTA", {
                    "reason": "API配额已用完或资源不足",
                    "suggestion": "请检查API账户配额或稍后重试"
                }).model_dump()
            )

        # 其他GLM API错误返回500
        raise HTTPException(
            status_code=500,
            detail=error_response(500, str(e), e.error_code, e.details).model_dump()
        )
    except Exception as e:
        logger.error(f"Chat error", error=str(e))
        raise HTTPException(
            status_code=500,
            detail=error_response(500, str(e)).model_dump()
        )


@router.post("/analyze", response_model=ApiResponse)
async def api_analyze(
    symbol: str = Query(..., description="股票代码"),
    analysis_type: str = Query("comprehensive", description="分析类型")
) -> ApiResponse:
    """
    GLM股票分析接口

    Args:
        symbol: 股票代码
        analysis_type: 分析类型

    Returns:
        包含分析结果的响应
    """
    try:
        logger.info(f"API analyze request: symbol={symbol}, type={analysis_type}")

        result = await analyze_module.analyze_stock(
            symbol=symbol,
            analysis_type=analysis_type
        )

        return success_response(data=result)

    except ValidationError as e:
        logger.warning(f"Validation error in analyze", error_code=e.error_code)
        raise HTTPException(
            status_code=400,
            detail=error_response(400, str(e), e.error_code, e.details).model_dump()
        )
    except GlmApiError as e:
        error_msg = str(e)
        logger.error(f"GLM API error in analyze", error=error_msg)

        # 检查是否为API配额错误
        if ("429" in error_msg or "1113" in error_msg or "APIReachLimitError" in error_msg or
            "quota" in error_msg.lower() or (e.details and "error_type" in e.details and
            "APIReachLimitError" in str(e.details["error_type"]))):
            raise HTTPException(
                status_code=503,
                detail=error_response(503, "AI服务暂时不可用，请稍后重试", "GLM_QUOTA", {
                    "reason": "API配额已用完或资源不足",
                    "suggestion": "请检查API账户配额或稍后重试"
                }).model_dump()
            )

        raise HTTPException(
            status_code=500,
            detail=error_response(500, str(e), e.error_code, e.details).model_dump()
        )
    except Exception as e:
        logger.error(f"Analyze error", error=str(e))
        raise HTTPException(
            status_code=500,
            detail=error_response(500, str(e)).model_dump()
        )
