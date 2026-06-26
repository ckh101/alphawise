"""
基于Claude Agent SDK的Agent编排器实现
通过 get_sdk_config() 注入厂商配置，支持多厂商切换
"""

from datetime import datetime
from typing import Any, Annotated
import asyncio
import json

from harness.core.logger import get_logger

logger = get_logger(__name__)


class ClaudeAgentOrchestrator:
    """
    使用Claude Agent SDK的Agent编排器

    通过Claude Agent SDK管理:
    - 任务创建和执行
    - 技能调用（通过tools/function calling）
    - 工作流编排
    - 多轮对话

    底层通过ANTHROPIC_BASE_URL环境变量路由到GLM-4.7模型
    """

    def __init__(self):
        self.client: Any = None  # Claude SDK Client
        self.sdk_client: Any = None  # ClaudeSDKClient实例
        self.skills: dict[str, Any] = {}
        self.workflows: dict[str, list[str]] = {}
        self.sessions: dict[str, list[dict]] = {}
        self._response_queue: asyncio.Queue | None = None
        self.mcp_server: Any = None  # MCP服务器实例

        # 初始化Claude Agent SDK（配置为调用GLM）
        self._init_claude_sdk()

        logger.info("ClaudeAgentOrchestrator initialized")

    def _init_claude_sdk(self):
        """
        初始化Claude Agent SDK，从数据库读取LLM配置。
        不再设置全局 os.environ，SDK 配置通过 get_sdk_config() 注入。
        """
        try:
            from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
            from harness.core.database import get_llm_config, is_llm_configured

            if not is_llm_configured():
                logger.warning("LLM未配置，请先在设置中配置 API Key 和模型")
                self.client = None
                return

            llm_config = get_llm_config()
            self._model = llm_config["llm.model"]

            logger.info(f"Claude Agent SDK configured, model: {self._model}")

            # 保存SDK类引用以便后续使用
            self.client = ClaudeSDKClient
            self.ClaudeAgentOptions = ClaudeAgentOptions

        except ImportError as e:
            logger.warning(f"Claude Agent SDK package not installed: {e}")
            self.client = None
        except Exception as e:
            logger.error("Failed to initialize Claude Agent SDK", error=str(e))
            self.client = None

    def _rebuild_mcp_server(self) -> None:
        """
        重建MCP服务器，将所有已注册的技能转换为SDK工具
        """
        if self.client is None:
            return

        try:
            from claude_agent_sdk import tool, create_sdk_mcp_server

            # 为每个技能创建工具定义
            tool_definitions = []

            for skill_name, skill in self.skills.items():
                # 根据技能类型创建不同的工具
                if skill_name == "tdx-realtime-quote":
                    @tool(
                        name="tdx-realtime-quote",
                        description="获取股票实时行情数据，包括最新价、开盘价、最高价、最低价、成交量等",
                        input_schema={"symbols": Annotated[list[str], "股票代码列表，如 ['600519.SH']"]}
                    )
                    async def tdx_realtime_quote(args: dict) -> dict:
                        symbols = args.get("symbols", ["600519.SH"])
                        result = skill.get_quote(symbols)
                        return {
                            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
                        }
                    tool_definitions.append(tdx_realtime_quote)

                elif skill_name == "tdx-kline":
                    @tool(
                        name="tdx-kline",
                        description="获取股票K线数据，支持日K、周K、月K等不同周期",
                        input_schema={
                            "symbol": Annotated[str, "股票代码，如 600519.SH"],
                            "period": Annotated[str, "K线周期：daily(日K)/weekly(周K)/monthly(月K)"],
                            "start_date": Annotated[str, "开始日期，格式YYYY-MM-DD，可选"],
                            "end_date": Annotated[str, "结束日期，格式YYYY-MM-DD，可选"]
                        }
                    )
                    async def tdx_kline(args: dict) -> dict:
                        result = skill.get_bars(
                            args.get("symbol", "600519.SH"),
                            args.get("period", "daily"),
                            args.get("start_date"),
                            args.get("end_date")
                        )
                        return {
                            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
                        }
                    tool_definitions.append(tdx_kline)

                elif skill_name == "glm-analyze":
                    @tool(
                        name="glm-analyze",
                        description="对股票进行AI投资分析，包括技术面、基本面、资金面等多维度分析",
                        input_schema={
                            "symbol": Annotated[str, "股票代码，如 600519.SH"],
                            "analysis_type": Annotated[str, "分析类型：comprehensive(综合分析)/technical(技术面)/fundamental(基本面)"]
                        }
                    )
                    async def glm_analyze(args: dict) -> dict:
                        result = await skill.analyze(
                            args.get("symbol", "600519.SH"),
                            args.get("analysis_type", "comprehensive")
                        )
                        return {
                            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
                        }
                    tool_definitions.append(glm_analyze)

                elif skill_name == "glm-chat":
                    @tool(
                        name="glm-chat",
                        description="进行多轮对话，回答用户关于股票、投资等问题",
                        input_schema={
                            "message": Annotated[str, "用户消息内容"],
                            "session_id": Annotated[str, "会话ID，用于保持上下文", ""]
                        }
                    )
                    async def glm_chat(args: dict) -> dict:
                        result = await skill.chat(
                            args.get("message", ""),
                            args.get("session_id")
                        )
                        return {
                            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
                        }
                    tool_definitions.append(glm_chat)

            # 创建SDK MCP服务器
            if tool_definitions:
                self.mcp_server = create_sdk_mcp_server(
                    name="harness-skills",
                    version="1.0.0",
                    tools=tool_definitions
                )
                logger.info(f"MCP server rebuilt with {len(tool_definitions)} tools")

        except ImportError as e:
            logger.warning(f"Cannot import MCP server dependencies: {e}")
        except Exception as e:
            logger.error(f"Failed to rebuild MCP server: {e}", error=str(e))

    def register_skill(self, name: str, skill: Any) -> None:
        """
        注册技能到编排器

        Args:
            name: 技能名称
            skill: 技能实例
        """
        self.skills[name] = skill
        logger.info(f"Skill registered: {name}")

        # 重新创建MCP服务器以包含新技能
        self._rebuild_mcp_server()

    def register_workflow(self, name: str, steps: list[str]) -> None:
        """
        注册工作流

        Args:
            name: 工作流名称
            steps: 技能步骤列表
        """
        self.workflows[name] = steps
        logger.info(f"Workflow registered: {name} -> {steps}")

    async def execute_task(
        self,
        prompt: str,
        task_type: str = "chat",
        session_id: str | None = None
    ) -> dict[str, Any]:
        """
        使用Claude Agent SDK执行任务，支持本地意图识别和工具调用

        Args:
            prompt: 用户提示/任务描述
            task_type: 任务类型
            session_id: 会话ID

        Returns:
            执行结果
        """
        logger.info(f"Executing task with Claude Agent SDK: {task_type}")

        if not self.client:
            return await self._execute_fallback(prompt, task_type, session_id)

        try:
            # 生成或获取会话ID
            session_id = session_id or self._generate_session_id()

            # 第一步：意图识别和预执行
            # 检测股票代码（6位数字）- 使用更宽松的正则表达式
            import re
            # 匹配: 600519.SH 或 600519（支持前后有非数字字符）
            stock_pattern = r'(\d{6})\.(SH|SZ)(?!\d)|(?<!\d)(\d{6})(?!\d)'
            stocks = re.findall(stock_pattern, prompt)

            tool_results = []
            context_data = ""

            if stocks:
                # 提取股票代码
                stock_symbols = []
                for match in stocks:
                    if match[0]:  # 带后缀的格式，如 600519.SH
                        stock_symbols.append(f"{match[0]}.{match[1]}")
                    elif match[2]:  # 只有数字
                        code = match[2]
                        # 上海股票以6开头，深圳以0或3开头
                        if code.startswith('6'):
                            stock_symbols.append(f"{code}.SH")
                        else:
                            stock_symbols.append(f"{code}.SZ")

                logger.info(f"Detected stock symbols: {stock_symbols}")
                logger.info(f"Available skills: {list(self.skills.keys())}")

                # 根据用户意图调用相应的工具
                for symbol in stock_symbols[:1]:  # 暂时只处理第一个股票
                    # 如果用户询问价格、行情等，获取实时行情
                    if any(keyword in prompt.lower() for keyword in ['price', 'quote', '行情', '价格', 'real-time', 'realtime', 'get', '获取', 'current']):
                        try:
                            logger.info(f"Auto-calling tdx-realtime-quote for {symbol}")
                            result = await self._execute_tool_call("tdx-realtime-quote", {"symbols": [symbol]})
                            tool_results.append(result)

                            # 格式化数据供模型使用
                            if isinstance(result, dict) and 'result' in result:
                                quote_data = result['result'][0] if result['result'] else {}
                                context_data += f"\n## {symbol} 实时行情:\n"
                                context_data += f"- 名称: {quote_data.get('name', 'N/A')}\n"
                                context_data += f"- 价格: {quote_data.get('price', 'N/A')}\n"
                                context_data += f"- 开盘: {quote_data.get('open', 'N/A')}\n"
                                context_data += f"- 最高: {quote_data.get('high', 'N/A')}\n"
                                context_data += f"- 最低: {quote_data.get('low', 'N/A')}\n"
                                context_data += f"- 成交量: {quote_data.get('volume', 'N/A')}\n"

                        except Exception as e:
                            logger.error(f"Failed to get quote for {symbol}: {e}", exc_info=True)

                    # 如果用户询问分析，调用分析技能
                    if any(keyword in prompt.lower() for keyword in ['analyze', 'analysis', '分析', 'research', '投资', 'study']):
                        try:
                            logger.info(f"Auto-calling glm-analyze for {symbol}")
                            result = await self._execute_tool_call("glm-analyze", {"symbol": symbol, "analysis_type": "comprehensive"})
                            tool_results.append(result)

                            if isinstance(result, dict) and 'content' in result:
                                context_data += f"\n## {symbol} 分析:\n{result['content']}\n"

                        except Exception as e:
                            logger.error(f"Failed to analyze {symbol}: {e}", exc_info=True)

            # 构建增强的系统提示，包含已获取的数据
            system_prompt = self._build_system_prompt()

            # 如果已经通过意图识别获取了工具结果，直接使用这些结果生成回复
            # 不再使用MCP工具（因为GLM的Anthropic兼容端点不支持真正的工具调用）
            if context_data:
                system_prompt += f"\n\n## 已获取的数据:\n{context_data}\n\n请基于以上数据回答用户问题。直接提供分析和总结，不要再提及工具调用。"

                # 使用简化的提示词让AI基于已有数据生成回复
                final_prompt = f"用户问题: {prompt}\n\n请基于上述已获取的数据，直接回答用户问题。提供清晰、专业的分析。"

                response_content = ""

                # 不使用MCP工具，直接调用GLM生成回复
                from harness.core.database import get_sdk_config
                _sdk = get_sdk_config()
                options = self.ClaudeAgentOptions(
                    **_sdk,
                    system_prompt=system_prompt,
                    model=self._model,
                    max_turns=1,
                    permission_mode="bypassPermissions",
                )

                async with self.client(options=options) as client:
                    await client.query(final_prompt)

                    async for msg in client.receive_response():
                        if hasattr(msg, 'content'):
                            for content_block in msg.content:
                                if hasattr(content_block, 'text'):
                                    response_content += content_block.text
            else:
                # 没有获取到工具数据，使用常规流程
                system_prompt += "\n\n如果用户需要股票数据或分析，请说明你将使用相应的工具来获取信息。"

                options_kwargs = {
                    **_sdk,
                    "system_prompt": system_prompt,
                    "model": self._model,
                    "max_turns": 3,
                    "permission_mode": "bypassPermissions",
                }

                # 如果有MCP服务器，添加到配置中
                if self.mcp_server:
                    options_kwargs["mcp_servers"] = {"harness-skills": self.mcp_server}
                    options_kwargs["allowed_tools"] = ["tdx-realtime-quote", "tdx-kline", "glm-analyze", "glm-chat"]

                options = self.ClaudeAgentOptions(**options_kwargs)

                response_content = ""

                async with self.client(options=options) as client:
                    await client.query(prompt)

                    async for msg in client.receive_response():
                        if hasattr(msg, 'content'):
                            for content_block in msg.content:
                                if hasattr(content_block, 'text'):
                                    response_content += content_block.text

            # 保存对话历史
            if session_id not in self.sessions:
                self.sessions[session_id] = []
            self.sessions[session_id].append({"role": "user", "content": prompt})
            self.sessions[session_id].append({"role": "assistant", "content": response_content})

            return {
                "task_type": task_type,
                "session_id": session_id,
                "message": response_content,
                "tool_calls": tool_results,
                "status": "completed"
            }

        except Exception as e:
            logger.error(f"Claude Agent SDK task failed: {e}", error=str(e))
            import traceback
            traceback.print_exc()
            # 回退到直接调用GLM
            return await self._execute_fallback(prompt, task_type, session_id)

    def _build_system_prompt(self) -> str:
        """构建系统提示，包含可用技能和工作流信息"""
        # 获取每个技能的参数格式
        tool_schemas = {}
        for name, skill in self.skills.items():
            if name == "tdx-realtime-quote":
                tool_schemas[name] = {
                    "description": "获取股票实时行情数据",
                    "parameters": {"symbols": "股票代码列表，如 ['600519.SH']"}
                }
            elif name == "tdx-kline":
                tool_schemas[name] = {
                    "description": "获取股票K线数据",
                    "parameters": {
                        "symbol": "股票代码，如 600519.SH",
                        "period": "K线周期：daily/weekly/monthly",
                        "start_date": "开始日期(可选)，格式YYYY-MM-DD",
                        "end_date": "结束日期(可选)，格式YYYY-MM-DD"
                    }
                }
            elif name == "glm-analyze":
                tool_schemas[name] = {
                    "description": "对股票进行AI投资分析",
                    "parameters": {
                        "symbol": "股票代码，如 600519.SH",
                        "analysis_type": "分析类型：comprehensive/technical/fundamental"
                    }
                }
            elif name == "glm-chat":
                tool_schemas[name] = {
                    "description": "进行多轮对话",
                    "parameters": {
                        "message": "用户消息内容",
                        "session_id": "会话ID(可选)"
                    }
                }

        prompt_parts = [
            "你是Harness投研助手的Agent，负责协调各种技能完成用户任务。\n\n",
            "## 可用技能:\n"
        ]

        for name, schema in tool_schemas.items():
            prompt_parts.append(f"### {name}\n")
            prompt_parts.append(f"描述: {schema['description']}\n")
            prompt_parts.append(f"参数: {json.dumps(schema['parameters'], ensure_ascii=False)}\n\n")

        prompt_parts.append("## 工具调用格式:\n")
        prompt_parts.append(
            "当需要调用技能时，请严格按照以下JSON格式输出（不要有其他文字）:\n"
            "```\n"
            'TOOL_CALL: {"name": "技能名称", "arguments": {参数字典}}\n'
            "```\n\n"
            "例如:\n"
            "```\n"
            'TOOL_CALL: {"name": "tdx-realtime-quote", "arguments": {"symbols": ["600519.SH"]}}\n'
            "```\n\n"
        )

        prompt_parts.append("## 重要提醒:\n")
        prompt_parts.append(
            "1. 当用户需要股票数据或分析时，必须使用工具调用格式\n"
            "2. 工具调用必须单独一行，格式严格为 TOOL_CALL: {JSON}\n"
            "3. 不要直接编造数据，必须通过工具获取真实数据\n"
            "4. 获取工具结果后，再基于结果进行分析和回答\n"
        )

        return "".join(prompt_parts)

    async def _execute_tool_call(self, tool_name: str, arguments: dict) -> dict[str, Any]:
        """
        执行工具调用

        Args:
            tool_name: 工具名称
            arguments: 参数字典

        Returns:
            执行结果
        """
        skill = self.skills.get(tool_name)
        if not skill:
            return {"error": f"Tool not found: {tool_name}"}

        try:
            # 根据技能类型调用不同方法
            if tool_name == "glm-chat":
                result = await skill.chat(
                    arguments.get("message", ""),
                    arguments.get("session_id")
                )
            elif tool_name == "glm-analyze":
                result = await skill.analyze(
                    arguments.get("symbol", "600519.SH"),
                    arguments.get("analysis_type", "comprehensive")
                )
            elif tool_name == "tdx-realtime-quote":
                symbols = arguments.get("symbols", ["600519.SH"])
                if isinstance(symbols, str):
                    symbols = [symbols]
                result = skill.get_quote(symbols)
            elif tool_name == "tdx-kline":
                result = skill.get_bars(
                    arguments.get("symbol", "600519.SH"),
                    arguments.get("period", "daily"),
                    arguments.get("start_date"),
                    arguments.get("end_date")
                )
            else:
                result = {"error": f"Unknown tool: {tool_name}"}

            return {
                "tool": tool_name,
                "arguments": arguments,
                "result": result
            }

        except Exception as e:
            logger.error(f"Tool execution failed: {tool_name}", error=str(e))
            return {
                "tool": tool_name,
                "error": str(e)
            }

    async def _execute_fallback(
        self,
        prompt: str,
        task_type: str,
        session_id: str | None
    ) -> dict[str, Any]:
        """回退执行方式（使用OpenAI SDK直接调用LLM）"""
        try:
            from openai import OpenAI
            from harness.core.database import get_llm_config

            llm_config = get_llm_config()
            api_key = llm_config["llm.api_key"]
            base_url = llm_config["llm.base_url"]
            model = llm_config["llm.model"]

            client = OpenAI(api_key=api_key, base_url=base_url)

            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": self._build_system_prompt()},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7
            )

            content = ""
            if response.choices and response.choices[0].message.content:
                content = response.choices[0].message.content

            return {
                "message": content,
                "session_id": session_id or self._generate_session_id(),
                "status": "completed"
            }

        except Exception as e:
            logger.error(f"Fallback execution failed: {e}", error=str(e))
            return {
                "error": f"执行失败: {str(e)}"
            }

    def _generate_session_id(self) -> str:
        """生成会话ID"""
        import uuid
        return str(uuid.uuid4())


# 全局Claude Agent编排器实例
_claude_orchestrator: ClaudeAgentOrchestrator | None = None


def get_claude_orchestrator() -> ClaudeAgentOrchestrator:
    """
    获取全局Claude Agent编排器实例

    Returns:
        ClaudeAgentOrchestrator实例
    """
    global _claude_orchestrator
    if _claude_orchestrator is None:
        _claude_orchestrator = ClaudeAgentOrchestrator()
    return _claude_orchestrator
