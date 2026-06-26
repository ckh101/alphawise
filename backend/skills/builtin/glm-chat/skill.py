"""
GLM Chat Skill Implementation
GLM对话技能实现
"""

from datetime import datetime
from typing import Any
import uuid

from harness.core.exceptions import GlmApiError, SkillError, ValidationError
from harness.core.logger import get_logger
from harness.services.glm_agent_client import create_message

logger = get_logger(__name__)

# 会话历史存储（生产环境应使用Redis或数据库）
_session_history: dict[str, list[dict[str, str]]] = {}


def get_session_history(session_id: str) -> list[dict[str, str]]:
    """
    获取会话历史记录

    Args:
        session_id: 会话ID

    Returns:
        对话历史列表
    """
    return _session_history.get(session_id, [])


def clear_session(session_id: str) -> bool:
    """
    清除指定会话的历史记录

    Args:
        session_id: 会话ID

    Returns:
        成功返回True，失败返回False
    """
    if session_id in _session_history:
        del _session_history[session_id]
        logger.info(f"Session history cleared: {session_id}")
        return True
    return False


async def chat(
    message: str,
    session_id: str | None = None,
    history: list[dict[str, str]] | None = None,
    model: str | None = None,
    temperature: float = 0.7
) -> dict[str, Any]:
    """
    发送对话消息

    Args:
        message: 用户消息内容
        session_id: 会话ID（可选），用于关联多轮对话
        history: 对话历史（可选），优先级高于session_id
        model: 模型名称（可选）
        temperature: 温度参数（可选）

    Returns:
        包含AI回复和使用信息的字典

    Raises:
        ValidationError: 消息内容为空或格式错误
        GlmApiError: GLM API调用失败
        SkillError: 对话处理失败
    """
    # 验证消息
    if not message or not isinstance(message, str):
        raise ValidationError(
            "消息内容不能为空",
            error_code="CHAT_001",
            details={"message_type": type(message).__name__ if message else None}
        )

    message = message.strip()
    if not message:
        raise ValidationError(
            "消息内容不能为空",
            error_code="CHAT_002"
        )

    logger.info(f"Chat request: message_length={len(message)}, session_id={session_id}")

    # 生成或使用session_id
    if session_id is None:
        session_id = str(uuid.uuid4())
        logger.debug(f"Generated new session_id: {session_id}")

    # 构建消息列表
    messages: list[dict[str, str]] = []

    # 优先使用传入的history，否则从session获取
    if history:
        messages = history.copy()
        logger.debug(f"Using provided history: {len(messages)} messages")
    elif session_id in _session_history:
        messages = _session_history[session_id].copy()
        logger.debug(f"Using session history: {len(messages)} messages")

    # 添加当前用户消息
    messages.append({"role": "user", "content": message})

    # 调用GLM API（使用Claude Agent SDK风格接口）
    try:
        response = await create_message(
            messages=messages,
            model=model,
            temperature=temperature
        )

        # 提取AI回复（Claude Agent SDK风格格式）
        ai_message = response["content"][0]["text"]

        # 更新会话历史
        messages.append({"role": "assistant", "content": ai_message})
        _session_history[session_id] = messages

        # 限制历史长度（保留最近20条）
        if len(_session_history[session_id]) > 20:
            _session_history[session_id] = _session_history[session_id][-20:]
            logger.debug(f"Trimmed session history to 20 messages")

        logger.info(
            f"Chat success: session_id={session_id}, "
            f"tokens={response['usage']['output_tokens']}"
        )

        return {
            "message": ai_message,
            "session_id": session_id,
            "model": response["model"],
            "usage": {
                "prompt_tokens": response["usage"]["input_tokens"],
                "completion_tokens": response["usage"]["output_tokens"],
                "total_tokens": response["usage"]["input_tokens"] + response["usage"]["output_tokens"]
            },
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    except GlmApiError:
        raise
    except Exception as e:
        logger.error(f"Chat processing error: {str(e)}", session_id=session_id)
        raise SkillError(
            f"对话处理失败: {e}",
            error_code="CHAT_003",
            details={"session_id": session_id, "error": str(e)}
        ) from e


class GlmChatSkill:
    """
    GLM对话技能类

    提供面向对象的接口，便于集成到Agent框架中
    """

    def __init__(self):
        self._sessions: dict[str, list[dict[str, str]]] = {}
        self.description = "GLM对话技能，支持多轮对话和会话管理"
        self.name = "glm-chat"
        logger.info("GlmChatSkill initialized")

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
                    "message": {
                        "type": "string",
                        "description": "用户消息内容"
                    },
                    "session_id": {
                        "type": "string",
                        "description": "会话ID，用于多轮对话"
                    }
                },
                "required": ["message"]
            }
        }

    def chat(
        self,
        message: str,
        session_id: str | None = None,
        model: str | None = None,
        temperature: float = 0.7
    ) -> dict[str, Any]:
        """
        发送对话消息

        Args:
            message: 用户消息
            session_id: 会话ID
            model: 模型名称
            temperature: 温度参数

        Returns:
            对话响应
        """
        return chat(message, session_id, model=model, temperature=temperature)

    def get_history(self, session_id: str) -> list[dict[str, str]]:
        """获取会话历史"""
        return get_session_history(session_id)

    def clear_history(self, session_id: str) -> bool:
        """清除会话历史"""
        return clear_session(session_id)

    def list_sessions(self) -> list[str]:
        """列出所有活跃会话"""
        return list(_session_history.keys())
