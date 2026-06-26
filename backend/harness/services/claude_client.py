"""
Claude Agent客户端模块
使用Anthropic Claude Agent SDK封装AI对话和分析功能
"""

import os
from typing import Any

from anthropic import Anthropic
from anthropic.types import Message

from harness.core.config import get_config
from harness.core.exceptions import GlmApiError, ValidationError
from harness.core.logger import get_logger

logger = get_logger(__name__)


# 全局Claude客户端实例
_claude_client: Anthropic | None = None


def _get_api_key() -> str:
    """
    获取Claude API密钥

    优先级：环境变量 > 配置文件

    Returns:
        API密钥字符串

    Raises:
        GlmApiError: API密钥未配置
    """
    # 优先从环境变量读取
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        return api_key

    # 从配置文件读取
    config = get_config()
    if hasattr(config, 'claude') and hasattr(config.claude, 'api_key') and config.claude.api_key:
        return config.claude.api_key

    raise GlmApiError(
        "Claude API密钥未配置，请设置环境变量ANTHROPIC_API_KEY或在配置文件中设置claude.api_key",
        error_code="CLAUDE_001",
        details={"env_var": "ANTHROPIC_API_KEY", "config_key": "claude.api_key"}
    )


def get_claude_client() -> Anthropic:
    """
    获取Claude客户端实例

    Returns:
        Anthropic客户端实例

    Raises:
        GlmApiError: API密钥未配置或客户端初始化失败
    """
    global _claude_client

    if _claude_client is not None:
        return _claude_client

    try:
        api_key = _get_api_key()
        _claude_client = Anthropic(api_key=api_key)
        logger.info("Claude客户端初始化成功")
        return _claude_client

    except GlmApiError:
        raise
    except Exception as e:
        logger.error("Claude客户端初始化失败", error=str(e))
        raise GlmApiError(
            f"Claude客户端初始化失败: {e}",
            error_code="CLAUDE_002",
            details={"error": str(e)}
        ) from e


async def chat_completion(
    messages: list[dict[str, str]],
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    stream: bool = False
) -> dict[str, Any]:
    """
    调用Claude聊天完成API

    Args:
        messages: 对话消息列表，格式 [{"role": "user", "content": "..."}]
        model: 模型名称，默认使用配置中的模型
        temperature: 温度参数，0-1之间，默认0.7
        max_tokens: 最大token数
        stream: 是否流式输出

    Returns:
        API响应结果

    Raises:
        ValidationError: 参数验证失败
        GlmApiError: API调用失败
    """
    # 验证参数
    if not messages or not isinstance(messages, list):
        raise ValidationError(
            "messages必须是非空列表",
            error_code="CLAUDE_003",
            details={"messages_type": type(messages).__name__}
        )

    for msg in messages:
        if not isinstance(msg, dict) or "role" not in msg or "content" not in msg:
            raise ValidationError(
                "每条消息必须包含role和content字段",
                error_code="CLAUDE_004",
                details={"message": msg}
            )

    # 获取配置
    config = get_config()
    model = model or (getattr(config, 'claude', None) and getattr(config.claude, 'model', None)) or "claude-sonnet-4-20250514"

    logger.info(
        "调用Claude聊天API",
        model=model,
        messages_count=len(messages),
        temperature=temperature,
        stream=stream
    )

    try:
        client = get_claude_client()

        # 构建请求参数
        request_params: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens or 4096,
            "temperature": temperature,
        }

        # 转换消息格式（Anthropic使用user/assistant角色）
        system_message = None
        claude_messages = []

        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            elif msg["role"] in ["user", "assistant"]:
                claude_messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })

        if system_message:
            request_params["system"] = system_message

        if claude_messages:
            request_params["messages"] = claude_messages

        # 调用API
        response = client.messages.create(**request_params)

        logger.info(
            "Claude API调用成功",
            model=model,
            response_id=response.id if hasattr(response, 'id') else None
        )

        # 提取响应内容
        content = ""
        if response.content:
            for block in response.content:
                if block.type == "text":
                    content += block.text

        return {
            "id": response.id if hasattr(response, 'id') else None,
            "model": response.model if hasattr(response, 'model') else model,
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": content
                },
                "finish_reason": response.stop_reason if hasattr(response, 'stop_reason') else None
            }],
            "usage": {
                "prompt_tokens": response.usage.input_tokens if hasattr(response.usage, 'input_tokens') else 0,
                "completion_tokens": response.usage.output_tokens if hasattr(response.usage, 'output_tokens') else 0,
                "total_tokens": (response.usage.input_tokens + response.usage.output_tokens) if hasattr(response.usage, 'input_tokens') else 0
            } if hasattr(response, 'usage') else {}
        }

    except GlmApiError:
        raise
    except Exception as e:
        logger.error("Claude API调用失败", error=str(e), error_type=type(e).__name__)
        raise GlmApiError(
            f"Claude API调用失败: {e}",
            error_code="CLAUDE_005",
            details={
                "model": model,
                "error": str(e),
                "error_type": type(e).__name__
            }
        ) from e


def reset_claude_client() -> None:
    """重置Claude客户端"""
    global _claude_client
    _claude_client = None
    logger.info("Claude客户端已重置")
