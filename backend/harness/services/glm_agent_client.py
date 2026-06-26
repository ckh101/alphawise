"""
LLM客户端模块 - 使用Claude Agent SDK
通过Claude Agent SDK调用任意兼容Anthropic API的大模型
"""

import asyncio
import os
import sys
from datetime import datetime
from typing import Any

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
from claude_agent_sdk.types import AssistantMessage, TextBlock, ResultMessage

from harness.core.config import get_config
from harness.core.exceptions import GlmApiError, ValidationError
from harness.core.logger import get_logger

logger = get_logger(__name__)

# 系统提示
SYSTEM_PROMPT = """你是灵智投研助手，一款专业的智能投研 Agent。

核心能力：股票分析、行情查询、新闻搜索、策略回测。
用户通过自然语言与你对话，你自动调用数据工具完成分析。

身份定位：
- 你是一个专业的投研助手，不是聊天机器人
- 回答简洁、专业、数据驱动，避免营销话术和客套
- 永远不要提及你的底层技术实现（如 Electron、FastAPI、SDK、模型名称等）
- 永远不要列举你的功能清单，除非用户明确问"你能做什么"
- 当用户问"你是谁"时，简单说你是灵智投研助手，可以帮他们分析股票、查行情、做回测即可

输出格式：
- 标准 Markdown，表格用 GFM 格式
- 列表项之间不留多余空行
- 代码块前后各留一个空行"""


def _run_sdk_query_sync(
    system_prompt: str,
    model: str,
    query_content: str,
    timeout: int,
    enable_skills: bool = False,
) -> str:
    """
    在独立线程的新事件循环中运行 Claude Agent SDK。

    Windows 上 uvicorn 的事件循环不支持 asyncio.create_subprocess_exec，
    所以需要在独立线程中创建 ProactorEventLoop 来运行 SDK。
    """
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()

    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(
            _sdk_query(system_prompt, model, query_content, timeout, enable_skills)
        )
    finally:
        loop.close()


def _get_project_root() -> str:
    """获取项目根目录 = backend/ 目录（.claude/skills/ 所在目录）"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def _sdk_query(
    system_prompt: str,
    model: str,
    query_content: str,
    timeout: int,
    enable_skills: bool = False,
) -> str:
    """内部异步查询方法"""
    from harness.core.database import get_sdk_config
    sdk_config = get_sdk_config()

    project_root = _get_project_root()

    options_kwargs: dict[str, Any] = {
        **sdk_config,
        "system_prompt": system_prompt,
        "model": model,
        "cwd": project_root,
    }

    if enable_skills:
        options_kwargs.update({
            "system_prompt": (
                "你是一个任务执行助手。当用户要求你使用某个 skill 时，"
                "请先调用 Skill 工具加载该 skill，"
                "然后严格按照 SKILL.md 中的使用说明，"
                "调用 Bash 工具执行对应的 Python 脚本。"
                "最后将脚本的标准输出完整返回，不要省略。"
            ),
            "allowed_tools": ["Skill", "Bash", "Read"],
            "max_turns": 10,
        })
    else:
        options_kwargs["max_turns"] = 1
        options_kwargs["disallowed_tools"] = ["Skill", "Bash", "Read"]

    options = ClaudeAgentOptions(**options_kwargs)

    response_content = ""

    async with ClaudeSDKClient(options=options) as client:
        await client.query(query_content)

        async for msg in client.receive_response():
            if hasattr(msg, 'content'):
                for content_block in msg.content:
                    if hasattr(content_block, 'text'):
                        response_content += content_block.text

    return response_content


# ---------------------------------------------------------------------------
# SDK Agent Query — 自主规划模式（工具 + Skills）
# ---------------------------------------------------------------------------

def _run_sdk_agent_sync(
    system_prompt: str,
    model: str,
    user_message: str,
    mcp_server_config: Any,
    project_root: str,
) -> str:
    """在独立线程的 ProactorEventLoop 中运行 SDK Agent（Windows 兼容）"""
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(
            _sdk_agent_core(system_prompt, model, user_message, mcp_server_config, project_root)
        )
    finally:
        loop.close()


async def _sdk_agent_core(
    system_prompt: str,
    model: str,
    user_message: str,
    mcp_server_config: Any,
    project_root: str,
) -> dict[str, Any]:
    """SDK Agent 异步核心：自主规划 + 工具调用 + Skills 自动发现

    Returns:
        dict with keys: text, tool_calls, thoughts, llm_calls, duration_ms, num_turns
    """
    from claude_agent_sdk.types import (
        AssistantMessage, TextBlock, ToolUseBlock, ToolResultBlock,
        ThinkingBlock, ResultMessage, SystemMessage,
    )
    from harness.core.database import get_sdk_config
    sdk_config = get_sdk_config()

    options = ClaudeAgentOptions(
        **sdk_config,
        system_prompt=system_prompt,
        model=model,
        mcp_servers={"harness-tools": mcp_server_config} if mcp_server_config else {},
        skills="all",
        cwd=project_root,
        max_turns=20,
        permission_mode="bypassPermissions",
        include_partial_messages=True,
    )

    result_text = ""
    tool_calls: list[dict[str, Any]] = []
    thoughts: list[dict[str, Any]] = []
    llm_calls: list[dict[str, Any]] = []
    duration_ms = 0
    num_turns = 0

    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(user_message)
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    thinking_text = ""
                    turn_tools = []
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            result_text += block.text
                        elif isinstance(block, ToolUseBlock):
                            turn_tools.append({
                                "tool_name": block.name,
                                "tool_input": block.input,
                                "tool_use_id": block.id,
                            })
                        elif isinstance(block, ThinkingBlock):
                            thinking_text += block.thinking
                        elif isinstance(block, ToolResultBlock):
                            for tc in tool_calls:
                                if tc.get("tool_use_id") == block.tool_use_id:
                                    tc["result_preview"] = (
                                        str(block.content)[:200] if block.content else ""
                                    )
                                    tc["error"] = block.is_error or False
                                    break

                    tool_calls.extend(turn_tools)

                    if thinking_text:
                        thoughts.append({
                            "type": "thinking",
                            "content": thinking_text[:500],
                        })

                    if msg.model:
                        llm_calls.append({
                            "phase": "sdk_agent",
                            "model": msg.model,
                            "input_preview": user_message[:200],
                            "output_preview": result_text[-200:] if result_text else "",
                            "duration_ms": None,
                            "created_at": datetime.now().strftime("%H:%M:%S"),
                        })

                elif isinstance(msg, ResultMessage):
                    duration_ms = msg.duration_ms
                    num_turns = msg.num_turns
                    if msg.is_error:
                        if result_text:
                            logger.warning(f"[SDK] ResultMessage error but have partial text ({len(result_text)} chars), returning it. model={model}, turns={num_turns}")
                        else:
                            error_detail = msg.result or "SDK execution error"
                            logger.error(f"[SDK] ResultMessage error with no text: {error_detail}, model={model}, turns={num_turns}")
                            raise RuntimeError(f"SDK_RESULT_ERROR: {error_detail}")
    except RuntimeError as e:
        if "SDK_RESULT_ERROR" in str(e):
            logger.error(f"[SDK] SDK returned error result, re-raising for retry: {e}")
        raise
    except Exception as e:
        err_type = type(e).__name__
        err_msg = str(e)
        logger.error(f"[SDK] Exception in _sdk_agent_core: {err_type}: {err_msg}, model={model}, text_so_far={len(result_text)} chars, turns={num_turns}")
        raise

    return {
        "text": result_text,
        "tool_calls": tool_calls,
        "thoughts": thoughts,
        "llm_calls": llm_calls,
        "duration_ms": duration_ms,
        "num_turns": num_turns,
    }


async def sdk_agent_query(
    system_prompt: str,
    user_message: str,
    mcp_server_config: Any = None,
    timeout: int = 300,
) -> dict[str, Any]:
    """
    SDK 自主模式查询入口。

    Returns:
        dict with keys: text, tool_calls, thoughts, llm_calls, duration_ms, num_turns
    """
    from harness.core.database import get_llm_config

    model = get_llm_config().get("llm.model", "glm-4.7")
    project_root = _get_project_root()

    logger.info(f"[SDK Agent] Starting autonomous query, model={model}")

    result = await asyncio.wait_for(
        asyncio.to_thread(
            _run_sdk_agent_sync,
            system_prompt,
            model,
            user_message,
            mcp_server_config,
            project_root,
        ),
        timeout=timeout,
    )

    logger.info(
        f"[SDK Agent] Query completed, response length={len(result.get('text', ''))}, "
        f"tool_calls={len(result.get('tool_calls', []))}, "
        f"turns={result.get('num_turns', 0)}"
    )
    return result


async def create_message(
    messages: list[dict[str, str]],
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    timeout: int = 600,
    enable_skills: bool = False,
) -> dict[str, Any]:
    """
    使用Claude Agent SDK创建消息

    Args:
        messages: 对话消息列表
        model: 模型名称
        temperature: 温度参数
        max_tokens: 最大token数
        timeout: 超时时间（秒）

    Returns:
        API响应结果（Claude Agent SDK格式）
    """
    # 验证参数
    if not messages or not isinstance(messages, list):
        raise ValidationError(
            "messages必须是非空列表",
            error_code="GLM_003"
        )

    # SDK 使用 Claude Code 环境变量
    from harness.core.database import get_llm_config

    db_model = get_llm_config().get("llm.model", "glm-4.7")
    model = model or db_model

    logger.info(
        "使用Claude Agent SDK调用LLM",
        model=model,
        messages_count=len(messages),
    )

    try:
        # 提取系统提示（如果有）
        system_prompt = SYSTEM_PROMPT
        user_messages = []

        for msg in messages:
            if msg.get("role") == "system":
                system_prompt = msg.get("content", system_prompt)
            else:
                user_messages.append(msg)

        # 如果没有用户消息，使用空字符串
        if not user_messages:
            user_messages.append({"role": "user", "content": ""})

        # 合并所有用户消息作为查询内容
        query_content = "\n\n".join([msg.get("content", "") for msg in user_messages])

        # 在独立线程中运行 SDK（避免 Windows 上 uvicorn 事件循环不支持子进程的问题）
        response_content = await asyncio.to_thread(
            _run_sdk_query_sync,
            system_prompt,
            model,
            query_content,
            timeout,
            enable_skills,
        )

        logger.info(f"LLM响应成功，长度: {len(response_content)}")

        # 返回统一格式的响应
        return {
            "type": "text",
            "id": str(os.urandom(16)),
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": response_content
                }
            ],
            "model": model,
            "stop_reason": "stop",
            "usage": {
                "input_tokens": 0,
                "output_tokens": len(response_content),
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0
            }
        }

    except Exception as e:
        logger.error("Claude Agent SDK调用失败", error=str(e), error_type=type(e).__name__)
        raise GlmApiError(
            f"LLM调用失败: {e}",
            error_code="GLM_005",
            details={"error": str(e), "error_type": type(e).__name__}
        ) from e


async def query(
    prompt: str,
    model: str | None = None,
    temperature: float = 0.7,
    enable_skills: bool = False,
) -> str:
    """
    简单查询

    Args:
        prompt: 用户提示
        model: 模型名称
        temperature: 温度参数
        enable_skills: 是否启用Skills（妙想等第三方skill）

    Returns:
        AI回复文本
    """
    result = await create_message(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        temperature=temperature,
        enable_skills=enable_skills,
    )

    # 提取文本内容
    return result["content"][0]["text"]
