"""
服务模块
"""

from .glm_client import chat_completion
from .glm_agent_client import create_message, query

__all__ = ["chat_completion", "create_message", "query"]
