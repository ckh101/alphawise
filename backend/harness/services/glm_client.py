"""
GLM客户端模块 - 使用Claude Agent SDK
统一使用Claude Agent SDK调用GLM模型，不再使用ZhipuAI SDK
"""

import os
from typing import Any

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

from harness.core.exceptions import GlmApiError, ValidationError
from harness.core.logger import get_logger

logger = get_logger(__name__)

# 系统提示
SYSTEM_PROMPT = """你是Harness投研助手的AI助手，负责帮助用户进行股票分析和投资研究。

你拥有专业的金融知识和分析能力，可以通过调用各种技能来获取实时行情、K线数据，并提供专业的投资建议。

请始终以这个身份回答问题，不要提及你是GLM或Z.ai开发的模型，也不要提及Anthropic或Claude。"""


async def chat_completion(
    messages: list[dict[str, str]],
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    system_prompt: str | None = None
) -> dict[str, Any]:
    """
    使用Claude Agent SDK调用GLM聊天完成API

    Args:
        messages: 对话消息列表
        model: 模型名称
        temperature: 温度参数
        max_tokens: 最大token数
        system_prompt: 系统提示（可选）

    Returns:
        API响应结果
    """
    # 验证参数
    if not messages or not isinstance(messages, list):
        raise ValidationError(
            "messages必须是非空列表",
            error_code="GLM_003"
        )

    # 设置环境变量 — 从数据库读取配置
    from harness.core.database import get_llm_config, is_llm_configured, get_sdk_config

    if not is_llm_configured():
        raise GlmApiError(
            "大模型未配置，请先在设置中配置 API Key 和模型",
            error_code="GLM_001",
        )

    llm_config = get_llm_config()
    db_model = llm_config["llm.model"]

    # 使用系统提示
    final_system_prompt = system_prompt or SYSTEM_PROMPT

    model = model or db_model

    logger.info(
        "使用Claude Agent SDK调用GLM",
        model=model,
        messages_count=len(messages),
        temperature=temperature
    )

    try:
        # 配置Claude Agent选项 — 通过 env 注入，不污染全局 os.environ
        sdk_config = get_sdk_config()
        options = ClaudeAgentOptions(
            **sdk_config,
            system_prompt=final_system_prompt,
            model=model,
            max_turns=1,
        )

        response_content = ""
        tool_calls = []

        async with ClaudeSDKClient(options=options) as client:
            # 发送查询
            await client.query(messages[-1].get("content", ""))

            # 接收响应
            async for msg in client.receive_response():
                if hasattr(msg, 'content'):
                    for content_block in msg.content:
                        if hasattr(content_block, 'text'):
                            response_content += content_block.text

        # 返回兼容格式
        return {
            "id": str(os.urandom(16)),
            "model": model,
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": response_content
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": len(str(messages)),
                "completion_tokens": len(response_content),
                "total_tokens": len(str(messages)) + len(response_content)
            }
        }

    except Exception as e:
        logger.error("Claude Agent SDK调用失败", error=str(e))
        raise GlmApiError(
            f"GLM API调用失败: {e}",
            error_code="GLM_005",
            details={"error": str(e)}
        ) from e
