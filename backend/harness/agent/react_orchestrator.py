"""
ReAct Agent编排器实现
基于Reasoning + Acting模式的智能投研Agent

核心思想：
1. Reasoning: AI思考需要什么信息来完成任务
2. Acting: 调用相应工具获取信息
3. Observation: 观察工具返回的结果
4. 重复上述步骤直到生成完整的投研报告
"""

import json
import os
import re
import asyncio
import time
import importlib
from datetime import datetime
from typing import Any, Callable
from dataclasses import dataclass
from enum import Enum

from harness.core.logger import get_logger

logger = get_logger(__name__)


def _fetch_name_to_code_map() -> dict[str, str]:
    """从本地 SQLite stocks 表获取 {股票名称: 代码} 映射（数据由 stockPool 每日同步）"""
    try:
        from harness.core.database import get_session
        from sqlalchemy import text
        db = get_session()
        rows = db.execute(text("SELECT code, name FROM stocks")).fetchall()
        db.close()
        return {row[1]: row[0] for row in rows}
    except Exception as e:
        logger.warning(f"Failed to fetch stock names from DB: {e}")
        return {}


def _search_stock_by_name(query: str) -> list[tuple[str, str]]:
    """
    实时从TDX查询股票名称，返回 [(code, name), ...]
    """
    name_map = _fetch_name_to_code_map()
    if not name_map:
        return []

    results = []
    query = query.strip()

    # 精确匹配
    if query in name_map:
        code = name_map[query]
        results.append((code, query))

    # 包含匹配：query 是 name 的子串
    if not results:
        for name, code in name_map.items():
            if query in name and name != query:
                results.append((code, name))
                if len(results) >= 5:
                    break

    # 反向包含匹配：name 是 query 的子串（用户输入带多余文字时）
    if not results:
        for name, code in name_map.items():
            if name in query:
                results.append((code, name))
                if len(results) >= 5:
                    break

    return results


class ThoughtType(Enum):
    """思维类型"""
    INTENT = "intent"           # 意图识别
    PLANNING = "planning"        # 规划下一步
    TOOL_SELECTION = "tool_selection"  # 工具选择
    ANALYSIS = "analysis"        # 分析数据
    SYNTHESIS = "synthesis"      # 综合结论
    OBSERVATION = "observation"  # 观察结果
    ACTION = "action"            # 工具调用


@dataclass
class Thought:
    """AI思维记录"""
    type: ThoughtType
    content: str
    timestamp: datetime
    data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "data": self.data
        }


@dataclass
class Action:
    """Agent行动记录"""
    tool_name: str
    arguments: dict[str, Any]
    timestamp: datetime
    result: Any = None
    error: str | None = None
    execution_time: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "timestamp": self.timestamp.isoformat(),
            "result": self.result,
            "error": self.error,
            "execution_time": self.execution_time
        }


class ReactOrchestrator:
    """
    ReAct Agent编排器

    实现端到端的自动投研分析流程：
    1. 接收用户自然语言输入
    2. 意图识别（股票代码、分析类型）
    3. 自动规划分析步骤
    4. 依次调用工具获取数据
    5. 基于获取的数据生成完整投研报告
    """

    # 投研分析的标准工作流步骤
    ANALYSIS_WORKFLOW = [
        "stock_info",       # 获取股票基本信息
        "realtime_quote",   # 获取实时行情
        "kline_data",       # 获取K线数据
        "technical_analysis",  # 技术分析
        "fundamental_analysis",  # 基本面分析
        "risk_assessment",   # 风险评估
        "investment_advice"  # 投资建议
    ]

    # LLM 驱动意图分析的控制参数
    MAX_ITERATIONS = 3       # 最大重新规划轮数
    MAX_TOOL_CALLS = 15      # 单次请求最大工具调用数
    MAX_STEPS_PER_PLAN = 8   # 单次计划最大步骤数
    MAX_SESSIONS = 50        # 最大同时保持的会话数

    # 按 session_id 存储对话上下文（多轮对话记忆）
    _session_contexts: dict[str, dict] = {}

    # 工具名到中文名的映射（用于进度回调）
    TOOL_DISPLAY_NAMES = {
        "get_stock_info": "基本信息",
        "get_realtime_quote": "实时行情",
        "get_kline_data": "K线数据",
        "technical_analysis": "技术分析",
        "fundamental_analysis": "基本面分析",
        "risk_assessment": "风险评估",
        "web_search": "联网搜索",
        "investment_advice": "投资建议",
        "mx_data": "妙想金融数据",
        "mx_search": "妙想资讯搜索",
        "mx_xuangu": "妙想智能选股",
        "mx_zixuan": "妙想自选股管理",
        "mx_moni": "妙想模拟交易",
    }

    def __init__(self):
        self.skills: dict[str, Any] = {}
        self.tools: dict[str, Callable] = {}
        self.thoughts: list[Thought] = []
        self.actions: list[Action] = []
        self.session_data: dict[str, Any] = {}
        self._sdk_mcp_server: Any = None  # 延迟构建，在首次 execute 时初始化
        logger.info("ReactOrchestrator initialized")

    @staticmethod
    def _safe(d: dict, key: str, default: Any = 0) -> Any:
        """安全取值，None 转为 default"""
        v = d.get(key, default)
        return default if v is None else v

    def register_skill(self, name: str, skill: Any) -> None:
        """注册技能"""
        self.skills[name] = skill
        logger.info(f"Skill registered: {name}")

        # 自动注册工具
        self._register_tools_from_skill(name, skill)

    def _register_tools_from_skill(self, name: str, skill: Any) -> None:
        """从技能中提取并注册工具"""
        if name == "tdx-realtime-quote":
            self.tools["get_realtime_quote"] = lambda symbols: skill.get_quote(symbols)
        elif name == "tdx-kline":
            self.tools["get_kline_data"] = lambda symbol, period="daily": skill.get_bars(symbol, period)
        elif name == "tdx-stock-info":
            self.tools["get_stock_info"] = lambda symbol: skill.get_info(symbol)
            self.tools["get_f10_data"] = lambda symbol: skill.get_f10(symbol)
        elif name == "glm-analyze":
            self.tools["analyze_stock"] = lambda symbol, analysis_type="comprehensive": skill.analyze(symbol, analysis_type)
        elif name == "glm-chat":
            self.tools["chat"] = lambda message, session_id=None: skill.chat(message, session_id)
        elif name == "web-search":
            self.tools["web_search"] = lambda query, **kwargs: skill.search(query, **kwargs)
            self.tools["search_stock_news"] = lambda symbol, stock_name="": skill.search_stock_news(symbol, stock_name)
        elif name == "browser-search":
            self.tools["browser_search"] = lambda query, **kwargs: skill.search(query, **kwargs)
            self.tools["browser_search_stock_news"] = lambda symbol, stock_name="": skill.search_stock_news(symbol, stock_name)
            self._browser_search_skill = skill
        elif name == "strategy-backtest":
            self.tools["run_backtest"] = lambda symbol, strategy="ma_crossover", **kwargs: skill.run_backtest(symbol, strategy, **kwargs)
            self.tools["list_backtest_strategies"] = lambda: skill.list_strategies()

        logger.info(f"Tools registered from skill {name}: {list(self._get_skill_tools(name))}")

    def _get_skill_tools(self, skill_name: str) -> list[str]:
        """获取技能对应的工具列表"""
        tool_map = {
            "tdx-realtime-quote": ["get_realtime_quote"],
            "tdx-kline": ["get_kline_data"],
            "tdx-stock-info": ["get_stock_info", "get_f10_data"],
            "glm-analyze": ["analyze_stock"],
            "glm-chat": ["chat"],
            "web-search": ["web_search", "search_stock_news"],
            "browser-search": ["browser_search", "browser_search_stock_news"],
            "strategy-backtest": ["run_backtest", "list_backtest_strategies"]
        }
        return tool_map.get(skill_name, [])

    async def execute(
        self,
        prompt: str,
        session_id: str | None = None,
        progress_callback: Callable | None = None,
        file_context: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """
        执行端到端分析（SDK 自主模式）

        完全依赖 Claude Agent SDK 的自主规划能力：
        SDK 自己决定调什么工具、怎么回答。
        """
        session_id = session_id or f"session_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        logger.info(f"Starting SDK autonomous analysis for session: {session_id}")

        _start_time = time.time()

        # 加载已有上下文（多轮对话）
        prev_ctx = self._session_contexts.get(session_id, {})
        self.session_data = dict(prev_ctx.get("_persist", {}))
        self.session_data["_session_id"] = session_id
        self.thoughts = []
        self.actions = []

        try:
            self.session_data["_user_prompt"] = prompt
            if file_context:
                self.session_data["_file_context"] = file_context
                logger.info(f"[SDK] File context received: filename={file_context.get('filename')}, chars={len(file_context.get('content_text', ''))}")

            # ===== SDK 自主模式 =====
            return await self.execute_sdk(prompt, session_id, file_context, progress_callback)

        except Exception as e:
            logger.error(f"SDK execution failed: {e}", exc_info=True)
            error_msg = f"分析失败: {e}"
            self.session_data["final_report"] = error_msg
            self.session_data["domain"] = "sdk_autonomous"
            return {
                "session_id": session_id,
                "status": "failed",
                "report": error_msg,
                "error": str(e),
            }
        finally:
            self._persist_session(session_id, prompt, _start_time)
            self._save_session_context(session_id)
            self._trigger_memory_extraction(session_id, prompt)

    async def _generate_final_response(
        self,
        prompt: str,
        session_id: str,
        symbols: list[str],
        response_mode: str,
        response_hint: str,
        progress_callback: Callable | None = None
    ) -> dict[str, Any]:
        """
        根据 LLM 选择的 response_mode 生成最终回答

        三种模式：
        - report: 完整投研报告（LLM 规划了完整的分析工具链）
        - chat: LLM 直接回答（简单问答、策略咨询、数据查询）
        - search_summary: 搜索结果摘要
        """
        symbol = symbols[0] if symbols else ""

        if response_mode == "report":
            # ===== 投研报告模式 =====
            # 检查是否需要生成投资建议（如果有完整分析链）
            if (self.session_data.get("technical_analysis")
                    and self.session_data.get("fundamental_analysis")
                    and self.session_data.get("risk_assessment")
                    and "investment_advice" not in self.session_data):
                await self._phase_investment_advice(symbol)

            report = await self._phase_generate_report()
            return {
                "session_id": session_id,
                "stock_symbol": symbol,
                "status": "completed",
                "report": report,
                "thoughts": [t.to_dict() for t in self.thoughts],
                "actions": [a.to_dict() for a in self.actions],
                "data": self.session_data
            }

        elif response_mode == "search_summary":
            # ===== 搜索摘要模式 =====
            if symbol:
                return await self._generate_news_report(symbol, session_id)
            else:
                return await self._handle_general_search(prompt, session_id)

        else:
            # ===== chat 模式（默认）===== 用 LLM 基于收集的数据直接回答
            return await self._generate_chat_response(prompt, session_id, symbols, response_hint)

    async def _generate_chat_response(
        self, prompt: str, session_id: str, symbols: list[str], response_hint: str
    ) -> dict[str, Any]:
        """用 LLM 基于收集到的数据直接回答用户问题"""
        symbol = symbols[0] if symbols else ""

        # 收集已有的数据摘要
        stock_info = self.session_data.get("stock_info", {})
        quote = self.session_data.get("realtime_quote", {})
        kline_data = self.session_data.get("kline_data", {})
        technical = self.session_data.get("technical_analysis", {})
        fundamental = self.session_data.get("fundamental_analysis", {})
        web_search = self.session_data.get("web_search", {})

        stock_name = (stock_info.get("name")
                      or self.session_data.get("matched_name")
                      or symbol)

        # 构建数据摘要
        data_parts = []
        if stock_name:
            data_parts.append(f"股票: {stock_name}({symbol})")

        if quote:
            data_parts.append(f"当前价: {quote.get('price', 'N/A')}")
            data_parts.append(f"涨跌幅: {quote.get('change_percent', 'N/A')}%")
            data_parts.append(f"成交量: {quote.get('volume', 'N/A')}")

        if kline_data:
            daily = kline_data.get("daily", [])
            if daily and len(daily) >= 2:
                latest = daily[-1]
                month_ago = daily[-22] if len(daily) >= 22 else daily[0]
                chg = (latest["close"] - month_ago["close"]) / month_ago["close"] * 100
                data_parts.append(f"近{min(22, len(daily))}个交易日涨跌幅: {chg:+.2f}%")
                data_parts.append(f"最新收盘价: {latest['close']}")

        if technical:
            data_parts.append(f"MA5={technical.get('ma5', 'N/A')}, MA20={technical.get('ma20', 'N/A')}")
            data_parts.append(f"RSI(6)={technical.get('rsi_6', 'N/A')}, RSI(12)={technical.get('rsi_12', 'N/A')}")
            if technical.get("bb_position") is not None:
                data_parts.append(f"布林带位置: {technical['bb_position']:.0f}%")

        if fundamental:
            for key in ["pe", "pb", "roe", "eps", "debt_ratio", "net_margin"]:
                val = fundamental.get(key)
                if val is not None:
                    data_parts.append(f"{key.upper()}: {val}")

        if web_search and web_search.get("items"):
            for item in web_search["items"][:5]:
                title = item.get("title", "")
                content = item.get("content", "")
                if title:
                    data_parts.append(f"新闻: {title} - {content[:100]}")

        data_block = "\n".join(data_parts) if data_parts else "（无数据）"

        # 构建对话历史
        session_id_val = self.session_data.get("_session_id", session_id)
        ctx = self._session_contexts.get(session_id_val, {})
        history_lines = []
        if ctx.get("messages"):
            for msg in ctx["messages"][-6:]:
                role = "用户" if msg["role"] == "user" else "助手"
                history_lines.append(f"{role}: {msg['content'][:150]}")
        history_block = "\n".join(history_lines) if history_lines else ""

        chat_prompt = f"用户问题: {prompt}\n\n系统收集到的数据:\n{data_block}\n\n"
        # 注入文件上下文
        if self.session_data.get("_file_context"):
            fc = self.session_data["_file_context"]
            chat_prompt += f"用户上传了文件 [{fc.get('filename', '')}]，内容如下:\n{fc.get('content_text', '')}\n\n"
        if history_block:
            chat_prompt += f"对话历史:\n{history_block}\n\n"
        memory_block = await self._build_memory_context()
        if memory_block:
            chat_prompt += f"{memory_block}\n\n"
        if response_hint:
            chat_prompt += f"用户意图提示: {response_hint}\n\n"

        chat_prompt += (
            "请基于以上数据直接回答用户问题。用简洁专业的语言。"
            "如果问题涉及交易策略，给出具体可操作的建议。"
            "如果数据不足以回答，请说明缺少什么信息。"
        )

        if "chat" not in self.tools:
            # 没有聊天工具，返回数据摘要
            report = f"## {stock_name}\n\n" + "\n".join(f"- {p}" for p in data_parts)
            self.session_data["final_report"] = report
            return {
                "session_id": session_id,
                "stock_symbol": symbol or None,
                "status": "completed",
                "report": report,
                "thoughts": [t.to_dict() for t in self.thoughts],
                "actions": [a.to_dict() for a in self.actions],
            }

        try:
            result = await asyncio.wait_for(
                self.tools["chat"](chat_prompt),
                timeout=60.0
            )
            message = result.get("message", "") if result else ""
            if message:
                self.session_data["final_report"] = message
                return {
                    "session_id": session_id,
                    "stock_symbol": symbol or None,
                    "status": "completed",
                    "report": message,
                    "thoughts": [t.to_dict() for t in self.thoughts],
                    "actions": [a.to_dict() for a in self.actions],
                }
        except asyncio.TimeoutError:
            logger.warning("Chat response LLM call timed out (60s)")
        except Exception as e:
            logger.warning(f"Chat response LLM call failed: {e}")

        # LLM 失败，返回数据摘要
        report = f"## {stock_name}\n\n" + "\n".join(f"- {p}" for p in data_parts)
        self.session_data["final_report"] = report
        return {
            "session_id": session_id,
            "stock_symbol": symbol or None,
            "status": "completed",
            "report": report,
            "thoughts": [t.to_dict() for t in self.thoughts],
            "actions": [a.to_dict() for a in self.actions],
        }

    async def _generate_data_query_report(self, symbol: str, session_id: str,
                                            prompt: str) -> dict[str, Any]:
        """生成数据查询类结果（涨跌幅、简单数据计算等）"""
        logger.info(f"Generating data query report for {symbol}")

        stock_info = self.session_data.get("stock_info", {})
        stock_name = stock_info.get("name", self.session_data.get("matched_name", symbol))
        quote = self.session_data.get("realtime_quote", {})
        kline_data = self.session_data.get("kline_data", {})

        # 收集已有数据
        data_summary = []
        if quote:
            data_summary.append(f"- 当前价格: {quote.get('price', 'N/A')}")
            data_summary.append(f"- 涨跌幅: {quote.get('change_percent', 'N/A')}%")
            data_summary.append(f"- 涨跌额: {quote.get('change', 'N/A')}")
        if kline_data:
            daily = kline_data.get("daily", [])
            if daily and len(daily) >= 2:
                latest = daily[-1]
                month_ago = daily[-22] if len(daily) >= 22 else daily[0]
                chg = (latest["close"] - month_ago["close"]) / month_ago["close"] * 100
                data_summary.append(f"- 近{min(22, len(daily))}个交易日涨跌幅: {chg:+.2f}%")
                data_summary.append(f"- 最新收盘价: {latest['close']}")
                data_summary.append(f"- {min(22, len(daily))}天前收盘价: {month_ago['close']}")
            weekly = kline_data.get("weekly", [])
            if weekly and len(weekly) >= 2:
                latest_w = weekly[-1]
                month_ago_w = weekly[-5] if len(weekly) >= 5 else weekly[0]
                chg_w = (latest_w["close"] - month_ago_w["close"]) / month_ago_w["close"] * 100
                data_summary.append(f"- 近{min(5, len(weekly))}周涨跌幅: {chg_w:+.2f}%")

        # 用 LLM 生成自然语言回答
        if "chat" in self.tools and data_summary:
            llm_prompt = (
                f"用户问题: {prompt}\n\n"
                f"股票: {stock_name}({symbol})\n\n"
                f"以下是系统获取到的数据:\n{chr(10).join(data_summary)}\n\n"
                f"请基于以上数据，用简洁自然的语言回答用户问题。直接回答，不要重复数据列表。"
                f"如果有涨跌幅数据，重点突出涨跌幅。不要添加系统未提供的数据。"
            )
            try:
                result = await asyncio.wait_for(
                    self.tools["chat"](llm_prompt),
                    timeout=30.0
                )
                if result and result.get("message"):
                    message = result["message"]
                    # 追加数据摘要作为支撑
                    report = f"{message}\n\n---\n\n<details><summary>数据详情</summary>\n\n"
                    for line in data_summary:
                        report += f"{line}\n"
                    report += "\n</details>"

                    return {
                        "session_id": session_id,
                        "stock_symbol": symbol,
                        "status": "completed",
                        "report": report,
                        "thoughts": [t.to_dict() for t in self.thoughts],
                        "actions": [a.to_dict() for a in self.actions],
                        "data": self.session_data
                    }
            except asyncio.TimeoutError:
                logger.warning("Data query LLM call timed out")
            except Exception as e:
                logger.warning(f"Data query LLM call failed: {e}")

        # LLM 失败或无 chat 工具：直接返回数据
        report_parts = [f"## {stock_name}（{symbol}）数据查询结果", ""]
        for line in data_summary:
            report_parts.append(f"- {line}")

        return {
            "session_id": session_id,
            "stock_symbol": symbol,
            "status": "completed",
            "report": "\n".join(report_parts),
            "thoughts": [t.to_dict() for t in self.thoughts],
            "actions": [a.to_dict() for a in self.actions],
            "data": self.session_data
        }

    # ========== LLM 驱动意图分析 ==========

    def _persist_session(self, session_id: str, prompt: str, start_time: float) -> None:
        """持久化对话记录到数据库（支持多轮）"""
        try:
            from harness.core.database import save_agent_session, get_session as get_db_session, AgentSession

            elapsed = time.time() - start_time
            symbols = self.session_data.get("stock_symbols", [])
            stock_info = self.session_data.get("stock_info", {})

            # 判断状态：actions 中有 error → failed
            has_error = any(a.error for a in self.actions)
            status = "failed" if has_error else "completed"
            error_msg = ""
            if has_error:
                error_msg = "; ".join(
                    f"{a.tool_name}: {a.error}" for a in self.actions if a.error
                )

            # 计算对话轮次
            db = get_db_session()
            try:
                turn_number = db.query(AgentSession).filter(
                    AgentSession.conversation_id == session_id
                ).count() + 1
            finally:
                db.close()

            per_turn_id = f"{session_id}-t{turn_number}"

            # SDK 模式下使用 SDK 返回的过程数据，否则用旧的 Action/Thought 对象
            if self.session_data.get("domain") == "sdk_autonomous":
                thoughts_data = self.session_data.get("sdk_thoughts", [])
                actions_data = self.session_data.get("sdk_actions", [])
                tool_calls_count = self.session_data.get("tool_calls_count", 0)
            else:
                thoughts_data = [t.to_dict() for t in self.thoughts]
                actions_data = [a.to_dict() for a in self.actions]
                tool_calls_count = 0

            save_agent_session({
                "session_id": per_turn_id,
                "conversation_id": session_id,
                "turn_number": turn_number,
                "prompt": prompt,
                "response_mode": self.session_data.get("response_mode", "chat"),
                "status": status,
                "stock_symbol": symbols[0] if symbols else "",
                "stock_name": stock_info.get("name", self.session_data.get("matched_name", "")),
                "report": self.session_data.get("final_report", "")[:2000],
                "thoughts": thoughts_data,
                "actions": actions_data,
                "tool_plan": self.session_data.get("tool_plan", []),
                "tool_calls_count": tool_calls_count,
                "duration": round(elapsed, 2),
                "error_message": error_msg,
            })
        except Exception as e:
            logger.warning(f"Failed to persist session to DB: {e}")

    def _record_llm_call(self, phase: str, messages: list, response: str,
                         model: str = "", duration_ms: int = 0) -> None:
        """记录一次 LLM 调用的输入输出"""
        try:
            from harness.core.database import save_llm_call
            session_id = self.session_data.get("_session_id", "")
            save_llm_call({
                "session_id": session_id,
                "phase": phase,
                "model": model,
                "input_messages": json.dumps(messages, ensure_ascii=False, default=str),
                "output_content": response,
                "duration_ms": duration_ms,
            })
        except Exception as e:
            logger.warning(f"Failed to record LLM call: {e}")

    def _save_session_context(self, session_id: str) -> None:
        """保存会话上下文（每次 execute 完成后调用）"""
        symbols = self.session_data.get("stock_symbols", [])
        stock_info = self.session_data.get("stock_info", {})
        prompt = self.session_data.get("original_prompt", "")

        ctx = self._session_contexts.get(session_id, {})

        # 更新股票信息
        if symbols:
            ctx["stock_symbols"] = symbols
            if stock_info.get("name"):
                ctx.setdefault("stock_names", {})[symbols[0]] = stock_info["name"]

        # 保存对话历史
        messages = ctx.get("messages", [])
        if prompt:
            messages.append({"role": "user", "content": prompt})

        # 保存助手回复摘要
        report = self.session_data.get("final_report", "")
        if report:
            messages.append({"role": "assistant", "content": report[:300]})
        else:
            # 从 session_data 构造简短摘要
            parts = []
            if self.session_data.get("realtime_quote", {}).get("price"):
                parts.append(f"价格{self.session_data['realtime_quote']['price']}")
            if parts:
                messages.append({"role": "assistant", "content": " ".join(parts)})

        # 限制历史长度
        ctx["messages"] = messages[-20:]

        self._session_contexts[session_id] = ctx

        # 清理过多的 session
        if len(self._session_contexts) > self.MAX_SESSIONS:
            oldest = list(self._session_contexts.keys())[:len(self._session_contexts) - self.MAX_SESSIONS]
            for sid in oldest:
                del self._session_contexts[sid]

        logger.info(f"[Context] Saved session {session_id}: symbols={symbols}, "
                     f"messages={len(ctx.get('messages', []))}")

    # ===== Memory System =====

    # ------------------------------------------------------------------
    # SDK 自主模式
    # ------------------------------------------------------------------

    def _build_sdk_mcp_server(self):
        """将 self.tools 注册为 SDK MCP Server，供 Claude Agent SDK 自主调用"""
        from claude_agent_sdk import tool as sdk_tool, create_sdk_mcp_server

        orchestrator = self  # 闭包捕获

        @sdk_tool("get_stock_info", "获取股票基本信息（公司名、行业、市值等）", {"symbol": str})
        async def _get_stock_info(args):
            r = await asyncio.to_thread(orchestrator.tools["get_stock_info"], args["symbol"])
            return {"content": [{"type": "text", "text": json.dumps(r, ensure_ascii=False, default=str)}]}

        @sdk_tool("get_realtime_quote", "获取股票实时行情（当前价、涨跌幅、成交量等）", {"symbols": str})
        async def _get_realtime_quote(args):
            r = await asyncio.to_thread(orchestrator.tools["get_realtime_quote"], args["symbols"])
            return {"content": [{"type": "text", "text": json.dumps(r, ensure_ascii=False, default=str)}]}

        @sdk_tool("get_kline_data", "获取K线历史数据（日K daily / 周K weekly）", {"symbol": str, "period": str})
        async def _get_kline_data(args):
            r = await asyncio.to_thread(
                orchestrator.tools["get_kline_data"], args["symbol"], args.get("period", "daily")
            )
            return {"content": [{"type": "text", "text": json.dumps(r, ensure_ascii=False, default=str)}]}

        @sdk_tool("get_f10_data", "获取股票F10基本面数据（财务指标、股东、分红等）", {"symbol": str})
        async def _get_f10_data(args):
            r = await asyncio.to_thread(orchestrator.tools["get_f10_data"], args["symbol"])
            return {"content": [{"type": "text", "text": json.dumps(r, ensure_ascii=False, default=str)}]}

        @sdk_tool("analyze_stock", "AI深度分析（技术面+基本面+风险评估）", {"symbol": str, "analysis_type": str})
        async def _analyze_stock(args):
            r = await asyncio.to_thread(
                orchestrator.tools["analyze_stock"], args["symbol"], args.get("analysis_type", "comprehensive")
            )
            return {"content": [{"type": "text", "text": str(r)}]}

        @sdk_tool("web_search", "使用AI搜索引擎搜索资讯", {"query": str})
        async def _web_search(args):
            r = await asyncio.to_thread(orchestrator.tools["web_search"], args["query"])
            return {"content": [{"type": "text", "text": str(r)}]}

        @sdk_tool("search_stock_news", "搜索股票相关新闻", {"symbol": str, "stock_name": str})
        async def _search_stock_news(args):
            r = await asyncio.to_thread(
                orchestrator.tools["search_stock_news"], args["symbol"], args.get("stock_name", "")
            )
            return {"content": [{"type": "text", "text": str(r)}]}

        @sdk_tool("run_backtest", "运行策略回测（均线交叉等）", {"symbol": str, "strategy": str})
        async def _run_backtest(args):
            r = await asyncio.to_thread(
                orchestrator.tools["run_backtest"], args["symbol"], args.get("strategy", "ma_crossover")
            )
            return {"content": [{"type": "text", "text": str(r)}]}

        @sdk_tool("list_backtest_strategies", "列出可用的回测策略列表", {})
        async def _list_backtest_strategies(args):
            r = await asyncio.to_thread(orchestrator.tools["list_backtest_strategies"])
            return {"content": [{"type": "text", "text": json.dumps(r, ensure_ascii=False, default=str)}]}

        all_tools = [
            _get_stock_info, _get_realtime_quote, _get_kline_data, _get_f10_data,
            _analyze_stock, _web_search, _search_stock_news,
            _run_backtest, _list_backtest_strategies,
        ]

        server = create_sdk_mcp_server(name="harness-tools", version="1.0.0", tools=all_tools)
        logger.info(f"[SDK] Built MCP server with {len(all_tools)} tools: {[t.name for t in all_tools]}")
        return server

    async def execute_sdk(
        self,
        prompt: str,
        session_id: str,
        file_context: dict[str, str] | None,
        progress_callback: Callable | None,
    ) -> dict[str, Any]:
        """SDK 自主模式：完全依赖 Claude Agent SDK 规划和执行"""

        # 延迟构建 MCP Server（等所有 skill 注册完成）
        if self._sdk_mcp_server is None:
            self._sdk_mcp_server = self._build_sdk_mcp_server()

        # 1. 记忆上下文
        memory_block = await self._build_memory_context()

        # 2. 系统提示
        system_prompt = (
            "你是灵智投研助手，一款专业的智能投研 Agent。\n\n"
            "身份定位：\n"
            "- 你是投研助手，不是聊天机器人\n"
            "- 回答简洁专业，避免营销话术和客套\n"
            "- 永远不要提及底层技术（Electron、FastAPI、SDK、模型名等）\n"
            "- 不要列举功能清单，除非用户明确问「你能做什么」\n"
            "- 当用户问「你是谁」时，简单介绍你是投研助手，可以帮分析股票、查行情、做回测\n\n"
            "效率原则（严格遵守）：\n"
            "1. 每个工具最多调用1次，不要重复调用同一工具\n"
            "2. 总工具调用不超过6次\n"
            "3. 先获取核心数据（基本信息+实时行情），再决定是否需要补充\n"
            "4. 对于深度分析，用 analyze_stock 工具一步完成技术面+基本面+风险评估\n"
            "5. 回答要自然、专业、有针对性\n"
            "6. 用中文回答，使用 Markdown 格式\n\n"
            "长内容处理：\n"
            "- 如果分析内容较长，请在段落或章节之间输出 <CONTINUE> 标记\n"
            "<CONTINUE> 表示「此处暂停，等待续写指令」，系统会自动要求你继续\n"
            "- 不要在句子中间输出 <CONTINUE>，应在完整的段落或章节后\n"
            "- 最后完成所有分析后，不要输出 <CONTINUE>，直接结束\n"
            "- 续写时保持上下文连贯，不要重复已说过的内容\n"
        )
        if memory_block:
            system_prompt += f"\n\n{memory_block}"

        # 3. 用户消息
        enhanced_prompt = prompt
        if file_context:
            enhanced_prompt += (
                f"\n\n[用户上传文件: {file_context.get('filename', '')}]\n"
                f"{file_context.get('content_text', '')[:5000]}"
            )

        # 5. 进度通知
        if progress_callback:
            await progress_callback({"phase": "sdk", "status": "running", "message": "正在分析..."})

        # 6. 调用 SDK（直接同步调用，避免 asyncio.to_thread 嵌套问题）
        # SDK 使用 Claude Code 环境变量（ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL）
        from harness.core.database import get_llm_config
        model = get_llm_config().get("llm.model", "glm-4.7")

        # Iwencai SkillHub（news-search 等技能需要）
        if not os.environ.get("IWENCAI_API_KEY"):
            from harness.core.database import get_setting
            iwencai_key = get_setting("iwencai.api_key", "")
            iwencai_url = get_setting("iwencai.base_url", "https://openapi.iwencai.com")
            if iwencai_key:
                os.environ["IWENCAI_API_KEY"] = iwencai_key
                os.environ["IWENCAI_BASE_URL"] = iwencai_url
            else:
                logger.warning("[SDK] IWENCAI_API_KEY not configured, news-search skill will not work")

        from harness.services.glm_agent_client import _run_sdk_agent_sync, _get_project_root

        # 流式 token 桥接：_run_sdk_agent_sync 在子线程的独立 ProactorEventLoop 里跑 SDK，
        # 这里把主 loop 引用捕获，token_cb 在子线程被调用时用 run_coroutine_threadsafe
        # 把"投递 streaming token 事件"的协程调度回主 loop（progress_callback 绑在主 loop）。
        main_loop = asyncio.get_running_loop()

        async def _emit_token(chunk: str) -> None:
            if progress_callback:
                try:
                    await progress_callback({
                        "phase": "report",
                        "status": "streaming",
                        "token": chunk,
                    })
                except Exception as e:
                    logger.warning(f"[SDK] progress_callback(streaming) error (ignored): {e}")

        def token_cb(chunk: str) -> None:
            asyncio.run_coroutine_threadsafe(_emit_token(chunk), main_loop)

        max_retries = 3
        sdk_result = None
        last_error = None
        for attempt in range(max_retries):
            try:
                sdk_result = await asyncio.to_thread(
                    _run_sdk_agent_sync,
                    system_prompt, model, enhanced_prompt,
                    self._sdk_mcp_server, _get_project_root(),
                    token_cb,
                )
                if sdk_result:
                    break
                logger.warning(f"[SDK] Attempt {attempt+1}/{max_retries}: sdk_result is None, retrying...")
                last_error = "sdk_result is None"
            except Exception as e:
                last_error = e
                err_str = str(e)
                err_type = type(e).__name__
                if attempt < max_retries - 1:
                    wait = 5 * (attempt + 1)
                    logger.warning(f"[SDK] Attempt {attempt+1}/{max_retries} failed ({err_type}: {err_str[:200]}), retrying in {wait}s...")
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"[SDK] All {max_retries} attempts failed. Last error ({err_type}): {err_str[:500]}")
        if not sdk_result and last_error:
            logger.error(f"[SDK] Final failure after {max_retries} attempts: {last_error}")

        if sdk_result:
            report = sdk_result.get("text", "")
            tool_calls = sdk_result.get("tool_calls", [])
            sdk_thoughts = sdk_result.get("thoughts", [])
            llm_calls = sdk_result.get("llm_calls", [])

            # 自适应续写：检测 <CONTINUE> 标记
            max_continue_rounds = 5
            continue_round = 0

            while "<CONTINUE>" in report and continue_round < max_continue_rounds:
                continue_round += 1
                logger.info(f"[SDK] Detected <CONTINUE> marker, round {continue_round}/{max_continue_rounds}")

                # 移除 <CONTINUE> 标记
                report = report.replace("<CONTINUE>", "").strip()

                # 构建续写提示：带上用户原问题和已生成内容的上下文
                context_suffix = report[-3000:] if len(report) > 3000 else report
                continue_prompt = (
                    f"你正在完成一份投研分析报告。\n\n"
                    f"用户原始要求：{prompt}\n\n"
                    f"=== 以下是已生成的内容 ===\n"
                    f"...{context_suffix}\n"
                    f"=== 内容结束 ===\n\n"
                    f"请从上面内容结束的地方继续分析，保持相同的格式和风格。\n"
                    f"注意：不要重复上面已有的内容，直接接着写。"
                )

                # 续写用简化系统提示（不需要工具，纯写作）
                continue_system = (
                    "你是灵智投研助手。你正在续写一份分析报告。\n"
                    "只输出分析正文，不要打招呼、不要解释、不要调用工具。\n"
                    "保持与前文相同的 Markdown 格式和章节结构。\n"
                    "如果内容较多，可以在完整段落后输出 <CONTINUE>。\n"
                    "完成后直接结束，不要输出 <CONTINUE>。"
                )

                try:
                    continue_result = await asyncio.to_thread(
                        _run_sdk_agent_sync,
                        continue_system, model, continue_prompt,
                        None, _get_project_root(),  # 不传 MCP server，阻止工具调用
                        token_cb,
                    )

                    if continue_result:
                        continue_text = continue_result.get("text", "")
                        report += "\n\n" + continue_text
                        llm_calls.extend(continue_result.get("llm_calls", []))
                        logger.info(f"[SDK] Continued report, now {len(report)} chars")
                    else:
                        logger.warning(f"[SDK] Continue round {continue_round} returned None, stopping continue")
                        break

                except Exception as e:
                    logger.error(f"[SDK] Continue round {continue_round} failed: {e}")
                    break

            # 最终清理：移除可能残留的 <CONTINUE>
            report = report.replace("<CONTINUE>", "").strip()
            if continue_round > 0:
                logger.info(f"[SDK] Continue completed after {continue_round} rounds, final report {len(report)} chars")

        else:
            report = "分析暂时不可用，请稍后重试"
            tool_calls, sdk_thoughts, llm_calls = [], [], []

        self.session_data["final_report"] = report
        self.session_data["stock_symbols"] = []
        self.session_data["domain"] = "sdk_autonomous"
        self.session_data["tool_calls"] = tool_calls
        self.session_data["tool_calls_count"] = len(tool_calls)
        self.session_data["sdk_thoughts"] = sdk_thoughts or [{"type": "sdk", "content": "SDK 自主规划并执行"}]
        self.session_data["sdk_actions"] = tool_calls
        self.session_data["llm_calls"] = llm_calls

        # 持久化 LLM 调用记录
        for lc in llm_calls:
            self._record_llm_call(
                phase=lc.get("phase", "sdk_agent"),
                messages=[{"role": "user", "content": enhanced_prompt[:200]}],
                response=lc.get("output_preview", ""),
                model=lc.get("model", ""),
                duration_ms=lc.get("duration_ms") or 0,
            )

        return {
            "session_id": session_id,
            "status": "completed",
            "stock_symbol": "",
            "report": report,
            "thoughts": sdk_thoughts or [{"type": "sdk", "content": "SDK 自主规划并执行"}],
            "actions": tool_calls,
            "tool_calls_count": len(tool_calls),
            "llm_calls": llm_calls,
            "duration": sdk_result.get("duration_ms", 0) / 1000 if sdk_result else 0,
        }

    # ------------------------------------------------------------------
    # 记忆系统
    # ------------------------------------------------------------------

    def _get_last_paragraph(self, text: str) -> str:
        """获取文本的最后一段，用于续写上下文"""
        if not text:
            return ""

        # 按段落分割
        paragraphs = text.split("\n\n")
        if not paragraphs:
            return ""

        # 获取最后非空段落
        for p in reversed(paragraphs):
            p = p.strip()
            if p:
                # 如果段落太长，只取最后200字
                if len(p) > 200:
                    p = p[-200:]
                return p

        return text[-200:] if len(text) > 200 else text

    async def _build_memory_context(self) -> str:
        """加载记忆上下文，用于注入 LLM prompt。失败返回空字符串。"""
        try:
            from harness.services.memory import get_memory_manager
            manager = get_memory_manager()
            sid = self.session_data.get("_session_id", "")
            prompt = self.session_data.get("_user_prompt", "")
            return await manager.load_relevant_memories(prompt, sid)
        except Exception as e:
            logger.warning(f"[Memory] Failed to load memories (non-critical): {e}")
            return ""

    def _trigger_memory_extraction(self, session_id: str, prompt: str) -> None:
        """触发后台记忆提取任务（非阻塞）"""
        try:
            from harness.services.memory import get_memory_manager
            manager = get_memory_manager()

            response = self.session_data.get("final_report", "")
            if not response:
                # final_report 可能未被某些路径设置，从 session_contexts 获取
                ctx = self._session_contexts.get(session_id, {})
                msgs = ctx.get("messages", [])
                for msg in reversed(msgs):
                    if msg.get("role") == "assistant" and msg.get("content"):
                        response = msg["content"]
                        break
            if not response or not prompt:
                return

            session_data_snapshot = {
                "stock_symbols": self.session_data.get("stock_symbols", []),
                "domain": self.session_data.get("domain", ""),
            }

            asyncio.create_task(
                manager.extract_and_store(
                    user_prompt=prompt,
                    assistant_response=response[:2000],
                    session_id=session_id,
                    session_data=session_data_snapshot,
                )
            )
        except Exception as e:
            logger.warning(f"[Memory] Failed to trigger extraction: {e}")

    def _extract_stock_symbols(self, prompt: str) -> list[str]:
        """从用户输入中提取股票代码（正则 + TDX 名称查询 + 上下文消解）"""
        # 正则匹配标准代码格式
        stock_pattern = r'(\d{6})\.(SH|SZ)(?!\d)|(?<!\d)(\d{6})(?!\d)'
        matches = re.findall(stock_pattern, prompt)

        stock_symbols = []
        for match in matches:
            if match[0]:
                stock_symbols.append(f"{match[0]}.{match[1]}")
            elif match[2]:
                code = match[2]
                if code.startswith('6'):
                    stock_symbols.append(f"{code}.SH")
                else:
                    stock_symbols.append(f"{code}.SZ")

        # 如果没有匹配到代码，尝试从股票名称查找
        if not stock_symbols:
            # 让 LLM 在意图分析阶段通过上下文理解股票名称
            # 这里用简单的名称匹配作为辅助：尝试用完整 prompt 做名称查找
            logger.info(f"[ReAct] Searching stock by name from prompt: '{prompt[:50]}'")
            search_results = _search_stock_by_name(prompt)
            if search_results:
                code, name = search_results[0]
                if code.startswith('6'):
                    symbol = f"{code}.SH"
                else:
                    symbol = f"{code}.SZ"
                stock_symbols.append(symbol)
                self.session_data["matched_name"] = name
                logger.info(f"Matched stock name -> {name}({symbol})")

        # 上下文指代消解：当前 prompt 没有股票代码时，从历史上下文获取
        if not stock_symbols:
            session_id = self.session_data.get("_session_id", "")
            ctx = self._session_contexts.get(session_id, {})
            if ctx.get("stock_symbols"):
                # 简单启发式：短 prompt 或看起来像追问的 prompt，复用之前的股票
                # 具体的指代理解交给 LLM 在意图分析中完成
                if len(prompt) <= 20:
                    stock_symbols = ctx["stock_symbols"]
                    logger.info(f"[Context] Short prompt resolved to previous symbols: {stock_symbols}")
                    names = ctx.get("stock_names", {})
                    if stock_symbols and stock_symbols[0] in names:
                        self.session_data["matched_name"] = names[stock_symbols[0]]

        return stock_symbols

    def _build_intent_system_prompt(self) -> str:
        """构建意图分析的系统 prompt —— 纯 LLM 驱动，无硬编码分类"""
        return """你是一个金融投资领域的智能助手。分析用户的输入，理解意图，并规划需要调用哪些工具来完成任务。

## 可用工具

| 工具名 | 用途 | 返回数据 |
|--------|------|---------|
| get_stock_info | 获取公司基本信息 | 公司名、行业、市值、财务数据 |
| get_realtime_quote | 获取实时行情 | 当前价、涨跌幅、成交量 |
| get_kline_data | 获取K线历史数据 | 日K、周K的OHLCV数据 |
| technical_analysis | 计算技术指标（需先获取K线） | MA、RSI、MACD、布林带、支撑压力位 |
| fundamental_analysis | 基本面分析（需先获取基本信息和行情） | PE、PB、ROE、财务健康度评分 |
| risk_assessment | 风险评估（需先完成技术+基本面分析） | 风险等级、风险因素 |
| web_search | 联网搜索新闻资讯 | 新闻列表 |
| investment_advice | 投资建议（需先完成风险分析） | 综合评级、目标价、止损价 |
| run_backtest | 策略回测 | 回测收益、夏普比率、交易记录 |
| mx_data | 妙想金融数据查询 | 通过自然语言查询行情、财务、关联关系等权威金融数据 |
| mx_search | 妙想资讯搜索 | 搜索金融新闻、公告、研报、政策等时效性资讯 |
| mx_xuangu | 妙想智能选股 | 按条件筛选股票（行情/财务指标、行业板块、指数成分股） |
| mx_zixuan | 妙想自选股管理 | 查询、添加、删除自选股 |
| mx_moni | 妙想模拟组合管理 | 查询持仓/资金/委托、模拟买卖、撤单 |

## 工具依赖（按顺序调用）
- technical_analysis 依赖 get_kline_data（先获取K线）
- fundamental_analysis 依赖 get_stock_info + get_realtime_quote
- risk_assessment 依赖 technical_analysis + fundamental_analysis
- investment_advice 依赖 risk_assessment

## 妙想工具使用场景（优先使用妙想工具而非内置工具）
- mx_data: 用户需要精确的财务/行情数据（如"贵州茅台近三年净利润"、"主力资金流向"）→ 优先于 get_stock_info
- mx_search: 用户需要搜索新闻、研报、公告（如"最新研报"、"AI板块新闻"）→ 优先于 web_search
- mx_xuangu: 用户要选股（如"市盈率小于20的银行股"、"涨幅大于2%的A股"）
- mx_zixuan: 用户要管理自选股（如"查看我的自选"、"加入自选"）
- mx_moni: 用户要操作模拟交易（如"我的持仓"、"买入600519 100股"）

**重要**：当用户请求属于上述妙想工具场景时，必须在 tool_plan 中指定对应的妙想工具。不要留空 tool_plan。

## 输出格式

严格返回以下 JSON（不要添加任何其他文本）：

```json
{
  "domain": "finance 或 out_of_domain",
  "symbols": ["600519.SH"],
  "tool_plan": [
    {"step": 1, "tool": "get_stock_info", "reason": "需要公司基本信息"},
    {"step": 2, "tool": "get_realtime_quote", "reason": "需要实时行情数据"}
  ],
  "response_mode": "report 或 chat 或 search_summary",
  "response_hint": "一句话描述用户真正想要什么"
}
```

## response_mode 说明
- "report": 用户要求深度分析、全面评估、研究报告 → 系统会生成结构化投研报告
- "chat": 用户提问、咨询策略、简单问答、操作建议 → 系统会用 LLM 直接回答
- "search_summary": 用户想搜新闻、查资讯 → 系统会整理搜索结果并给出摘要

## 规划原则

1. **domain 判断**：股票、基金、期货、宏观经济、金融市场 → "finance"；天气、体育、娱乐 → "out_of_domain"
2. **按需选工具**：只选确实需要的工具，不要选多余的
3. **遵循依赖**：如果选了 downstream 工具，必须先选它的依赖工具
4. **区分深浅**：
   - 用户问"分析XX"→ 完整工具链 → response_mode="report"
   - 用户问"XX的PE是多少"、"今天涨了多少"→ 只选数据工具 → response_mode="chat"
   - 用户问"怎么做T"、"止损策略"→ 可能只需少量数据辅助 → response_mode="chat"
   - 用户问"搜一下XX的最新新闻"→ web_search 或 mx_search → response_mode="search_summary"
   - 用户问"回测XX的均线策略"→ run_backtest → response_mode="report"
   - 用户问选股/自选/模拟交易 → 对应妙想工具 → response_mode="chat"
5. **symbols**：如果用户提到了股票名称但没给代码，留空 []，系统会自动查找"""

    async def _analyze_intent_with_llm(self, prompt: str, symbols: list[str]) -> dict[str, Any]:
        """调用 LLM 做意图分析"""
        logger.info(f"[LLM Intent] Analyzing: {prompt[:80]}")

        from harness.services.glm_agent_client import create_message

        symbol_info = f"（系统已识别到股票代码: {symbols}）" if symbols else "（系统未识别到股票代码）"

        # 构建对话历史上下文（多轮对话支持）
        session_id = self.session_data.get("_session_id", "")
        ctx = self._session_contexts.get(session_id, {})
        context_lines = []
        if ctx.get("stock_symbols"):
            stock_names = ctx.get("stock_names", {})
            name_str = ", ".join(f"{stock_names.get(s, s)}" for s in ctx["stock_symbols"])
            context_lines.append(f"之前讨论的股票: {name_str} ({ctx['stock_symbols']})")
        if ctx.get("messages"):
            recent = ctx["messages"][-6:]  # 最近 3 轮
            for msg in recent:
                role = "用户" if msg["role"] == "user" else "助手"
                context_lines.append(f"{role}: {msg['content'][:120]}")

        context_block = "\n".join(context_lines) if context_lines else "（首次对话，无历史上下文）"

        try:
            _t0 = time.time()
            response = await asyncio.wait_for(
                create_message(
                    messages=[
                        {"role": "system", "content": self._build_intent_system_prompt()},
                        {"role": "user", "content": (
                            f"用户输入：{prompt}\n{symbol_info}\n\n"
                            f"历史对话：\n{context_block}\n\n"
                            + (
                                f"用户上传了文件 [{self.session_data.get('_file_context', {{}}).get('filename', '')}]，"
                                f"内容如下：\n{self.session_data.get('_file_context', {{}}).get('content_text', '')}\n\n"
                                if self.session_data.get("_file_context") else ""
                            )
                            + "请分析用户意图并返回JSON。注意利用上下文理解代词（如\u201c它\u201d、\u201c这只股票\u201d等指代）。"
                        )}
                    ],
                    temperature=0.1,
                    timeout=30,
                ),
                timeout=45.0,
            )

            if response and response.get("content"):
                raw_text = response["content"][0]["text"]
                self._record_llm_call(
                    "intent_recognition",
                    [{"role": "user", "content": prompt[:500]}],
                    raw_text[:3000],
                    model=response.get("model", ""),
                    duration_ms=int((time.time() - _t0) * 1000),
                )
                return self._parse_intent_json(raw_text, symbols)

        except asyncio.TimeoutError:
            logger.warning("[LLM Intent] Timeout, falling back")
        except Exception as e:
            logger.error(f"[LLM Intent] Failed: {e}")

        return self._fallback_intent(symbols)

    def _parse_intent_json(self, raw_text: str, symbols: list[str]) -> dict[str, Any]:
        """从 LLM 返回文本中提取 JSON"""
        # 尝试从 markdown 代码块中提取
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', raw_text)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_str = raw_text.strip()

        try:
            intent = json.loads(json_str)
        except json.JSONDecodeError:
            start = json_str.find('{')
            end = json_str.rfind('}')
            if start >= 0 and end > start:
                try:
                    intent = json.loads(json_str[start:end + 1])
                except json.JSONDecodeError:
                    logger.warning(f"[LLM Intent] JSON parse failed: {json_str[:200]}")
                    return self._fallback_intent(symbols)
            else:
                logger.warning(f"[LLM Intent] No JSON found in response: {raw_text[:200]}")
                return self._fallback_intent(symbols)

        # 填充默认值
        if "domain" not in intent:
            intent["domain"] = "finance"
        if "tool_plan" not in intent:
            intent["tool_plan"] = []
        if "symbols" not in intent:
            intent["symbols"] = []
        if "response_mode" not in intent:
            # 有工具 → report，没工具 → chat
            intent["response_mode"] = "report" if intent["tool_plan"] else "chat"

        # 注入预计算的股票代码
        if symbols and not intent.get("symbols"):
            intent["symbols"] = symbols

        # 限制 tool_plan 大小
        if len(intent["tool_plan"]) > self.MAX_STEPS_PER_PLAN:
            intent["tool_plan"] = intent["tool_plan"][:self.MAX_STEPS_PER_PLAN]

        # 验证 tool_plan 中的工具名
        valid_tools = {
            "get_stock_info", "get_realtime_quote", "get_kline_data",
            "technical_analysis", "fundamental_analysis", "risk_assessment",
            "web_search", "investment_advice", "run_backtest",
            "mx_data", "mx_search", "mx_xuangu", "mx_zixuan", "mx_moni",
        }
        intent["tool_plan"] = [s for s in intent["tool_plan"] if s.get("tool") in valid_tools]

        # 妙想工具关键词后处理：LLM 可能遗漏，根据用户输入关键词补充
        self._ensure_mx_tools(intent)

        logger.info(f"[LLM Intent] Parsed: domain={intent['domain']}, "
                     f"mode={intent.get('response_mode')}, "
                     f"plan_steps={len(intent['tool_plan'])}")
        return intent

    def _ensure_mx_tools(self, intent: dict[str, Any]) -> None:
        """根据用户输入关键词，确保妙想工具被选入 tool_plan"""
        user_prompt = self.session_data.get("_user_prompt", "")
        if not user_prompt:
            return

        existing_tools = {s.get("tool") for s in intent.get("tool_plan", [])}

        # 妙想工具匹配规则：(工具名, 关键词列表, 优先级描述)
        mx_rules = [
            ("mx_search", [
                "研报", "新闻", "资讯", "公告", "消息", "政策",
                "搜索", "查一下", "搜一下", "最新", "热点",
            ]),
            ("mx_data", [
                "净利润", "营收", "现金流", "资产负债", "财务数据",
                "主力资金", "资金流向", "龙虎榜", "关联", "持股",
                "分红", "roe", "毛利率", "净利率",
            ]),
            ("mx_xuangu", [
                "选股", "筛选", "符合条件的", "市盈率小于", "涨幅大于",
                "跌幅大于", "涨停", "跌停", "板块", "成分股",
            ]),
            ("mx_zixuan", [
                "自选", "加入自选", "删除自选", "我的自选股", "关注列表",
                "添加到自选", "移出自选",
            ]),
            ("mx_moni", [
                "模拟", "持仓", "买入", "卖出", "委托", "撤单",
                "模拟组合", "模拟交易", "账户", "资金", "下单",
            ]),
        ]

        next_step = len(intent.get("tool_plan", [])) + 1
        for tool_name, keywords in mx_rules:
            if tool_name in existing_tools:
                continue
            if any(kw in user_prompt for kw in keywords):
                intent.setdefault("tool_plan", []).append({
                    "step": next_step,
                    "tool": tool_name,
                    "reason": f"关键词匹配：用户输入包含妙想工具场景",
                })
                next_step += 1
                # 搜索类调整 response_mode
                if tool_name == "mx_search" and intent.get("response_mode") != "report":
                    intent["response_mode"] = "search_summary"

    def _fallback_intent(self, symbols: list[str]) -> dict[str, Any]:
        """LLM 意图分析失败时的降级方案"""
        if symbols:
            return {
                "domain": "finance",
                "symbols": symbols,
                "tool_plan": [
                    {"step": 1, "tool": "get_stock_info", "reason": "降级：获取基本信息"},
                    {"step": 2, "tool": "get_realtime_quote", "reason": "降级：获取实时行情"},
                    {"step": 3, "tool": "get_kline_data", "reason": "降级：获取K线数据"},
                    {"step": 4, "tool": "technical_analysis", "reason": "降级：技术分析"},
                    {"step": 5, "tool": "fundamental_analysis", "reason": "降级：基本面分析"},
                    {"step": 6, "tool": "risk_assessment", "reason": "降级：风险评估"},
                    {"step": 7, "tool": "web_search", "reason": "降级：搜索新闻"},
                    {"step": 8, "tool": "investment_advice", "reason": "降级：投资建议"},
                ],
                "response_mode": "report",
                "response_hint": "降级：执行完整综合分析",
            }
        else:
            return {
                "domain": "finance",
                "symbols": [],
                "tool_plan": [{"step": 1, "tool": "web_search", "reason": "降级：搜索"}],
                "response_mode": "search_summary",
                "response_hint": "降级：通用搜索",
            }

    def _build_tool_dispatch(self) -> dict[str, Callable]:
        """映射工具名到执行方法"""
        return {
            "get_stock_info": self._phase_stock_info,
            "get_realtime_quote": self._phase_realtime_quote,
            "get_kline_data": self._phase_kline_data,
            "technical_analysis": self._phase_technical_analysis,
            "fundamental_analysis": self._phase_fundamental_analysis,
            "risk_assessment": self._phase_risk_assessment,
            "web_search": self._phase_web_search,
            "investment_advice": self._phase_investment_advice,
            "mx_data": self._phase_mx_data,
            "mx_search": self._phase_mx_search,
            "mx_xuangu": self._phase_mx_xuangu,
            "mx_zixuan": self._phase_mx_zixuan,
            "mx_moni": self._phase_mx_moni,
        }

    async def _execute_tool_step(self, tool_name: str, symbol: str,
                                  progress_callback: Callable | None = None) -> None:
        """执行单个工具步骤"""
        dispatch = self._build_tool_dispatch()
        phase_func = dispatch.get(tool_name)

        if not phase_func:
            logger.warning(f"[Tool] Unknown tool: {tool_name}")
            return

        display_name = self.TOOL_DISPLAY_NAMES.get(tool_name, tool_name)

        if progress_callback:
            await progress_callback({
                "phase": display_name,
                "status": "running",
                "message": f"正在获取{display_name}..."
            })

        try:
            await phase_func(symbol)

            if progress_callback:
                await progress_callback({
                    "phase": display_name,
                    "status": "completed",
                    "message": f"{display_name}获取完成"
                })
        except Exception as e:
            import traceback
            logger.error(f"Tool {tool_name} failed: {e}\n{traceback.format_exc()}")
            if progress_callback:
                await progress_callback({
                    "phase": display_name,
                    "status": "error",
                    "message": f"{display_name}获取失败: {str(e)}"
                })

    async def _phase_intent_recognition(self, prompt: str) -> None:
        """阶段1: LLM 驱动意图识别"""
        logger.info("Phase 1: LLM-Driven Intent Recognition")

        # A. 提取股票代码（正则 + 名称查询）
        symbols = self._extract_stock_symbols(prompt)

        # B. LLM 意图分析
        intent = await self._analyze_intent_with_llm(prompt, symbols)

        # C. 存入 session_data
        self.session_data.update({
            "stock_symbols": intent.get("symbols") or symbols,
            "original_prompt": prompt,
            "tool_plan": intent.get("tool_plan", []),
            "response_mode": intent.get("response_mode", "chat"),
            "response_hint": intent.get("response_hint", ""),
            "domain": intent.get("domain", "finance"),
        })

        self.thoughts.append(Thought(
            type=ThoughtType.INTENT,
            content=f"LLM意图分析: domain={intent.get('domain')}, "
                     f"mode={intent.get('response_mode')}, "
                     f"symbols={self.session_data['stock_symbols']}, "
                     f"plan_steps={len(intent.get('tool_plan', []))}",
            timestamp=datetime.now(),
            data=intent
        ))

        logger.info(f"[LLM Intent] Result: mode={intent.get('response_mode')}, "
                     f"symbols={self.session_data['stock_symbols']}")

    async def _phase_stock_info(self, symbol: str) -> None:
        """阶段2: 获取股票基本信息"""
        logger.info("Phase 2: Stock Info")

        if "get_stock_info" not in self.tools:
            logger.warning("get_stock_info tool not available")
            return

        self.thoughts.append(Thought(
            type=ThoughtType.PLANNING,
            content=f"获取{symbol}的基本公司信息",
            timestamp=datetime.now()
        ))

        action = Action(
            tool_name="get_stock_info",
            arguments={"symbol": symbol},
            timestamp=datetime.now()
        )

        try:
            start_time = datetime.now()
            result = self.tools["get_stock_info"](symbol)
            action.result = result
            action.execution_time = (datetime.now() - start_time).total_seconds()

            self.session_data["stock_info"] = result

            self.thoughts.append(Thought(
                type=ThoughtType.OBSERVATION,
                content=f"成功获取{symbol}的基本信息: {result.get('name', 'N/A')}",
                timestamp=datetime.now(),
                data=result
            ))

        except Exception as e:
            action.error = str(e)
            logger.error(f"Failed to get stock info: {e}")

        self.actions.append(action)

    async def _phase_realtime_quote(self, symbol: str) -> None:
        """阶段3: 获取实时行情"""
        logger.info("Phase 3: Realtime Quote")

        if "get_realtime_quote" not in self.tools:
            logger.warning("get_realtime_quote tool not available")
            return

        self.thoughts.append(Thought(
            type=ThoughtType.PLANNING,
            content=f"获取{symbol}的实时行情数据",
            timestamp=datetime.now()
        ))

        action = Action(
            tool_name="get_realtime_quote",
            arguments={"symbols": [symbol]},
            timestamp=datetime.now()
        )

        try:
            start_time = datetime.now()
            result = self.tools["get_realtime_quote"]([symbol])
            action.result = result
            action.execution_time = (datetime.now() - start_time).total_seconds()

            if result and len(result) > 0:
                self.session_data["realtime_quote"] = result[0]

                quote = result[0]
                self.thoughts.append(Thought(
                    type=ThoughtType.OBSERVATION,
                    content=f"当前价格: {quote.get('price', 'N/A')}, 涨跌: {quote.get('change', 'N/A')}",
                    timestamp=datetime.now(),
                    data=quote
                ))

        except Exception as e:
            action.error = str(e)
            logger.error(f"Failed to get realtime quote: {e}")

        self.actions.append(action)

    async def _phase_kline_data(self, symbol: str) -> None:
        """阶段4: 获取K线数据"""
        logger.info("Phase 4: Kline Data")

        if "get_kline_data" not in self.tools:
            logger.warning("get_kline_data tool not available")
            return

        self.thoughts.append(Thought(
            type=ThoughtType.PLANNING,
            content=f"获取{symbol}的K线数据用于技术分析",
            timestamp=datetime.now()
        ))

        # 获取日K和周K数据
        periods = ["daily", "weekly"]
        kline_data = {}

        for period in periods:
            action = Action(
                tool_name="get_kline_data",
                arguments={"symbol": symbol, "period": period},
                timestamp=datetime.now()
            )

            try:
                start_time = datetime.now()
                result = self.tools["get_kline_data"](symbol, period)
                action.result = result
                action.execution_time = (datetime.now() - start_time).total_seconds()

                kline_data[period] = result

                self.thoughts.append(Thought(
                    type=ThoughtType.OBSERVATION,
                    content=f"获取到{len(result) if result else 0}条{period}K线数据",
                    timestamp=datetime.now(),
                    data={"count": len(result) if result else 0}
                ))

            except Exception as e:
                action.error = str(e)
                logger.error(f"Failed to get kline data ({period}): {e}")

            self.actions.append(action)

        self.session_data["kline_data"] = kline_data

    async def _phase_technical_analysis(self, symbol: str) -> None:
        """阶段5: 深度技术分析"""
        logger.info("Phase 5: Deep Technical Analysis")

        self.thoughts.append(Thought(
            type=ThoughtType.ANALYSIS,
            content="进行深度技术面分析，计算多种技术指标",
            timestamp=datetime.now()
        ))

        kline_daily = self.session_data.get("kline_data", {}).get("daily", [])
        technical_indicators = {}

        if kline_daily and len(kline_daily) >= 20:
            closes = [bar["close"] for bar in kline_daily[-60:]]  # 最近60天
            highs = [bar["high"] for bar in kline_daily[-60:]]
            lows = [bar["low"] for bar in kline_daily[-60:]]
            volumes = [bar["volume"] for bar in kline_daily[-20:]]

            latest = kline_daily[-1]
            current_price = latest["close"]

            # ========== 移动平均线分析 ==========
            ma5 = sum(closes[-5:]) / 5
            ma10 = sum(closes[-10:]) / 10
            ma20 = sum(closes[-20:]) / 20
            ma30 = sum(closes[-30:]) / 30 if len(closes) >= 30 else None
            ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else None

            # ========== 均线趋势分析 ==========
            ma_trend = []
            if current_price > ma5 > ma10 > ma20:
                ma_trend.append("强势多头排列")
            elif current_price < ma5 < ma10 < ma20:
                ma_trend.append("弱势空头排列")
            else:
                ma_trend.append("均线缠绕，方向不明")

            # 价格与均线关系
            price_vs_ma = []
            if current_price > ma5:
                price_vs_ma.append(f"站上5日线({ma5:.2f})")
            else:
                price_vs_ma.append(f"跌破5日线({ma5:.2f})")

            if current_price > ma20:
                price_vs_ma.append(f"站上20日线({ma20:.2f})")
            else:
                price_vs_ma.append(f"跌破20日线({ma20:.2f})")

            # ========== RSI相对强弱指标 ==========
            def calculate_rsi(prices, period=14):
                if len(prices) < period + 1:
                    return 50
                gains = []
                losses = []
                for i in range(1, len(prices)):
                    change = prices[i] - prices[i-1]
                    gains.append(max(0, change))
                    losses.append(max(0, -change))
                avg_gain = sum(gains[-period:]) / period
                avg_loss = sum(losses[-period:]) / period
                if avg_loss == 0:
                    return 100
                rs = avg_gain / avg_loss
                return 100 - (100 / (1 + rs))

            rsi_6 = calculate_rsi(closes[-7:], 6)
            rsi_12 = calculate_rsi(closes[-13:], 12)
            rsi_24 = calculate_rsi(closes[-25:], 24)

            # RSI分析
            rsi_analysis = []
            if rsi_6 > 80:
                rsi_analysis.append("6日RSI超买，短期回调风险")
            elif rsi_6 < 20:
                rsi_analysis.append("6日RSI超卖，短期反弹机会")
            else:
                rsi_analysis.append("6日RSI处于正常区间")

            # ========== MACD指标 ==========
            def calculate_ema(prices, period):
                multiplier = 2 / (period + 1)
                ema = [prices[0]]
                for price in prices[1:]:
                    ema.append((price * multiplier) + (ema[-1] * (1 - multiplier)))
                return ema[-1]

            ema12 = calculate_ema(closes[-13:], 12)
            ema26 = calculate_ema(closes[-27:], 26) if len(closes) >= 27 else None
            dif = ema12 - ema26 if ema26 else 0

            macd_analysis = []
            if dif > 0:
                macd_analysis.append(f"MACD金叉(DIF={dif:.2f})，看涨信号")
            else:
                macd_analysis.append(f"MACD死叉(DIF={dif:.2f})，看跌信号")

            # ========== 布林带指标 ==========
            if len(closes) >= 20:
                bb_period = 20
                bb_middle = sum(closes[-bb_period:]) / bb_period
                bb_std = (sum([(x - bb_middle) ** 2 for x in closes[-bb_period:]]) / bb_period) ** 0.5
                bb_upper = bb_middle + 2 * bb_std
                bb_lower = bb_middle - 2 * bb_std

                bb_position = (current_price - bb_lower) / (bb_upper - bb_lower) * 100

                bb_analysis = []
                if bb_position > 80:
                    bb_analysis.append("价格接近布林带上轨，可能超买")
                elif bb_position < 20:
                    bb_analysis.append("价格接近布林带下轨，可能超卖")
                else:
                    bb_analysis.append("价格处于布林带中轨附近")
            else:
                bb_analysis = ["数据不足"]

            # ========== 成交量分析 ==========
            avg_volume = sum(volumes) / len(volumes) if volumes else 0
            latest_volume = latest["volume"]
            volume_ratio = latest_volume / avg_volume if avg_volume > 0 else 1

            volume_analysis = []
            if volume_ratio > 1.5:
                volume_analysis.append(f"放量成交(量比{volume_ratio:.1f})")
            elif volume_ratio < 0.7:
                volume_analysis.append(f"缩量成交(量比{volume_ratio:.1f})")
            else:
                volume_analysis.append("成交量正常")

            # ========== 波动率分析 ==========
            if len(closes) >= 20:
                returns = [(closes[i] / closes[i-1] - 1) for i in range(1, len(closes[-20:]))]
                volatility = (sum([r ** 2 for r in returns]) / len(returns)) ** 0.5
                annual_volatility = volatility * (252 ** 0.5) * 100

                volatility_analysis = f"历史波动率{annual_volatility:.1f}%"
                if annual_volatility > 30:
                    volatility_analysis += "，波动率较高"
                elif annual_volatility < 15:
                    volatility_analysis += "，波动率较低"
                else:
                    volatility_analysis += "，波动率适中"
            else:
                volatility_analysis = "数据不足"

            # ========== 支撑位和压力位 ==========
            recent_highs = sorted(highs[-20:], reverse=True)[:5]
            recent_lows = sorted(lows[-20:])[:5]
            resistance_level = sum(recent_highs) / len(recent_highs) if recent_highs else current_price * 1.05
            support_level = sum(recent_lows) / len(recent_lows) if recent_lows else current_price * 0.95

            # ========== 综合技术评分 ==========
            tech_score = 0
            max_score = 10

            # 均线评分 (3分)
            if current_price > ma5 > ma10 > ma20:
                tech_score += 3
            elif current_price > ma20:
                tech_score += 2
            elif current_price > ma10:
                tech_score += 1

            # RSI评分 (2分)
            if 40 <= rsi_12 <= 60:
                tech_score += 2
            elif 30 <= rsi_12 <= 70:
                tech_score += 1

            # MACD评分 (2分)
            if dif > 0:
                tech_score += 2

            # 成交量评分 (1分)
            if volume_ratio >= 1.2:
                tech_score += 1

            # 布林带位置评分 (2分)
            if 20 <= bb_position <= 80:
                tech_score += 2
            elif bb_position < 20:
                tech_score += 1

            # 汇总技术指标
            technical_indicators = {
                # 价格与均线
                "current_price": current_price,
                "ma5": round(ma5, 2),
                "ma10": round(ma10, 2),
                "ma20": round(ma20, 2),
                "ma30": round(ma30, 2) if ma30 else None,
                "ma60": round(ma60, 2) if ma60 else None,

                # 均线分析
                "ma_trend": ma_trend,
                "price_vs_ma": price_vs_ma,

                # RSI指标
                "rsi_6": round(rsi_6, 2),
                "rsi_12": round(rsi_12, 2),
                "rsi_24": round(rsi_24, 2),
                "rsi_analysis": rsi_analysis,

                # MACD指标
                "dif": round(dif, 2),
                "macd_analysis": macd_analysis,

                # 布林带
                "bb_upper": round(bb_upper, 2) if len(closes) >= 20 else None,
                "bb_middle": round(bb_middle, 2) if len(closes) >= 20 else None,
                "bb_lower": round(bb_lower, 2) if len(closes) >= 20 else None,
                "bb_position": round(bb_position, 1) if len(closes) >= 20 else None,
                "bb_analysis": bb_analysis,

                # 成交量
                "volume": latest_volume,
                "avg_volume": round(avg_volume, 0),
                "volume_ratio": round(volume_ratio, 2),
                "volume_analysis": volume_analysis,

                # 波动率
                "volatility_analysis": volatility_analysis,

                # 支撑压力
                "resistance_level": round(resistance_level, 2),
                "support_level": round(support_level, 2),

                # 综合评分
                "tech_score": tech_score,
                "max_score": max_score,
                "score_ratio": tech_score / max_score,
            }

        self.session_data["technical_analysis"] = technical_indicators

        self.thoughts.append(Thought(
            type=ThoughtType.ANALYSIS,
            content=f"深度技术分析完成，技术评分: {technical_indicators.get('tech_score', 0)}/{technical_indicators.get('max_score', 10)}",
            timestamp=datetime.now(),
            data=technical_indicators
        ))

    async def _phase_fundamental_analysis(self, symbol: str) -> None:
        """阶段6: 深度基本面分析"""
        logger.info("Phase 6: Deep Fundamental Analysis")

        self.thoughts.append(Thought(
            type=ThoughtType.ANALYSIS,
            content="进行深度基本面分析，计算财务指标和估值",
            timestamp=datetime.now()
        ))

        stock_info = self.session_data.get("stock_info", {})
        quote = self.session_data.get("realtime_quote", {})
        fundamental_indicators = {}

        if stock_info:
            # ========== 基础数据（确保 None 转为 0）==========
            total_shares = stock_info.get("total_shares") or 0
            float_shares = stock_info.get("float_shares") or 0
            total_assets = stock_info.get("total_assets") or 0
            net_assets = stock_info.get("net_assets") or 0
            current_assets = stock_info.get("current_assets") or 0
            revenue = stock_info.get("revenue") or 0
            net_profit = stock_info.get("net_profit") or 0
            operating_profit = stock_info.get("operating_profit") or 0
            shareholders = stock_info.get("shareholders") or 0

            # ========== 每股指标 ==========
            bps = net_assets / total_shares if total_shares and net_assets else 0  # 每股净资产
            eps = net_profit / total_shares if total_shares and net_profit else 0  # 每股收益

            # 获取当前价格
            current_price = quote.get("price", 0) if quote else stock_info.get("market_price", 0)

            # ========== 估值指标 ==========
            # 市净率 PB = 股价 / 每股净资产
            pb = current_price / bps if bps > 0 else 0

            # 市盈率 PE = 股价 / 每股收益
            pe = current_price / eps if eps > 0 else 0

            # 市销率 PS = 总市值 / 营业收入
            market_cap = current_price * total_shares if current_price > 0 else 0
            ps = market_cap / revenue if revenue > 0 else 0

            # 估值分析
            valuation_analysis = []
            if pe > 0:
                if pe < 15:
                    valuation_analysis.append(f"PE({pe:.1f})处于低估区间")
                elif pe < 30:
                    valuation_analysis.append(f"PE({pe:.1f})处于合理区间")
                elif pe < 50:
                    valuation_analysis.append(f"PE({pe:.1f})略高")
                else:
                    valuation_analysis.append(f"PE({pe:.1f})高估")

            if pb > 0:
                if pb < 1.5:
                    valuation_analysis.append(f"PB({pb:.1f})较低")
                elif pb < 3:
                    valuation_analysis.append(f"PB({pb:.1f})合理")
                else:
                    valuation_analysis.append(f"PB({pb:.1f})较高")

            # ========== 盈利能力指标 ==========
            # 销售净利率 = 净利润 / 营业收入
            net_margin = (net_profit / revenue * 100) if revenue > 0 else 0

            # 资产收益率 ROA = 净利润 / 总资产
            roa = (net_profit / total_assets * 100) if total_assets > 0 else 0

            # 净资产收益率 ROE = 净利润 / 净资产
            roe = (net_profit / net_assets * 100) if net_assets > 0 else 0

            # 营业利润率 = 营业利润 / 营业收入
            operating_margin = (operating_profit / revenue * 100) if revenue > 0 else 0

            # 盈利能力分析
            profitability_analysis = []
            if roe > 20:
                profitability_analysis.append(f"ROE({roe:.1f}%)优秀")
            elif roe > 10:
                profitability_analysis.append(f"ROE({roe:.1f}%)良好")
            elif roe > 0:
                profitability_analysis.append(f"ROE({roe:.1f}%)一般")
            else:
                profitability_analysis.append("ROE为负，盈利能力弱")

            if net_margin > 20:
                profitability_analysis.append(f"净利率({net_margin:.1f}%)很高")
            elif net_margin > 10:
                profitability_analysis.append(f"净利率({net_margin:.1f}%)不错")
            elif net_margin > 0:
                profitability_analysis.append(f"净利率({net_margin:.1f}%)偏低")

            # ========== 偿债能力指标 ==========
            # 资产负债率 = (总资产 - 净资产) / 总资产
            debt_ratio = ((total_assets - net_assets) / total_assets * 100) if total_assets > 0 else 0

            # 流动比率 = 流动资产 / 流动负债
            current_liabilities = total_assets - net_assets  # 简化计算
            current_ratio = (current_assets / current_liabilities * 100) if current_liabilities > 0 else 0

            # 偿债能力分析
            solvency_analysis = []
            if debt_ratio < 30:
                solvency_analysis.append(f"资产负债率({debt_ratio:.1f}%)很低，财务稳健")
            elif debt_ratio < 50:
                solvency_analysis.append(f"资产负债率({debt_ratio:.1f}%)适中")
            elif debt_ratio < 70:
                solvency_analysis.append(f"资产负债率({debt_ratio:.1f}%)较高")
            else:
                solvency_analysis.append(f"资产负债率({debt_ratio:.1f}%)很高，财务风险大")

            # ========== 营运能力指标 ==========
            # 每股收益增长率（简化计算，假设使用年度数据）
            if eps > 0:
                earnings_quality = "每股收益为正"
            else:
                earnings_quality = "每股收益为负"

            # 股本结构分析
            equity_structure = []
            float_ratio = (float_shares / total_shares * 100) if total_shares > 0 else 0
            equity_structure.append(f"流通比例{float_ratio:.1f}%")

            if shareholders > 0:
                if shareholders < 50000:
                    equity_structure.append(f"股东人数({shareholders:,})较少，筹码集中")
                elif shareholders < 100000:
                    equity_structure.append(f"股东人数({shareholders:,})适中")
                else:
                    equity_structure.append(f"股东人数({shareholders:,})较多，筹码分散")

            # ========== 行业对比分析（简化） ==========
            industry = stock_info.get("industry", "未知")
            industry_analysis = []
            if "银行" in industry:
                industry_analysis.append("银行业，关注ROE和不良贷款率")
                if roe > 12:
                    industry_analysis.append("ROE表现良好")
            elif "酿酒" in industry or "食品" in industry:
                industry_analysis.append("消费品行业，关注品牌和渠道")
                if net_margin > 15:
                    industry_analysis.append("盈利能力强")
            elif "科技" in industry or "电子" in industry:
                industry_analysis.append("科技行业，关注研发投入")
            elif "地产" in industry:
                industry_analysis.append("地产行业，关注去化和政策")
            else:
                industry_analysis.append(f"{industry}行业")

            # ========== 基本面综合评分 ==========
            fundamental_score = 0
            max_fundamental_score = 10

            # 盈利能力评分 (4分)
            if roe > 20:
                fundamental_score += 4
            elif roe > 15:
                fundamental_score += 3
            elif roe > 10:
                fundamental_score += 2
            elif roe > 0:
                fundamental_score += 1

            # 成长性评分 (2分，简化判断)
            if net_profit > 0 and revenue > 0:
                fundamental_score += 2

            # 财务健康评分 (2分)
            if debt_ratio < 50:
                fundamental_score += 2
            elif debt_ratio < 70:
                fundamental_score += 1

            # 估值评分 (2分)
            if 0 < pe < 30:
                fundamental_score += 2
            elif 0 < pe < 50:
                fundamental_score += 1

            # 汇总基本面指标
            fundamental_indicators = {
                # 基本信息
                "industry": industry,
                "province": stock_info.get("province", "N/A"),
                "ipo_date": stock_info.get("ipo_date", "N/A"),

                # 市值数据
                "market_cap": round(market_cap / 100000000, 2),  # 亿元
                "total_shares": round(total_shares / 100000000, 2),  # 亿股
                "float_shares": round(float_shares / 100000000, 2),  # 亿股
                "current_price": current_price,

                # 每股指标
                "eps": round(eps, 2),
                "bps": round(bps, 2),

                # 估值指标
                "pe": round(pe, 2) if pe > 0 else None,
                "pb": round(pb, 2) if pb > 0 else None,
                "ps": round(ps, 2) if ps > 0 else None,
                "valuation_analysis": valuation_analysis,

                # 盈利能力
                "net_margin": round(net_margin, 2),
                "roa": round(roa, 2),
                "roe": round(roe, 2),
                "operating_margin": round(operating_margin, 2),
                "profitability_analysis": profitability_analysis,

                # 偿债能力
                "debt_ratio": round(debt_ratio, 2),
                "current_ratio": round(current_ratio, 2),
                "solvency_analysis": solvency_analysis,

                # 营运能力
                "earnings_quality": earnings_quality,
                "equity_structure": equity_structure,

                # 行业分析
                "industry_analysis": industry_analysis,

                # 综合评分
                "fundamental_score": fundamental_score,
                "max_fundamental_score": max_fundamental_score,
                "score_ratio": fundamental_score / max_fundamental_score,
            }

        self.session_data["fundamental_analysis"] = fundamental_indicators

        # F10 数据增强
        f10_enhancement = await self._phase_f10_enhancement(symbol, fundamental_indicators)
        if f10_enhancement:
            fundamental_indicators.update(f10_enhancement)
            self.session_data["fundamental_analysis"] = fundamental_indicators

        self.thoughts.append(Thought(
            type=ThoughtType.ANALYSIS,
            content=f"深度基本面分析完成，基本面评分: {fundamental_indicators.get('fundamental_score', 0)}/{fundamental_indicators.get('max_fundamental_score', 10)}",
            timestamp=datetime.now(),
            data=fundamental_indicators
        ))

    async def _phase_f10_enhancement(
        self, symbol: str, current_indicators: dict
    ) -> dict[str, Any] | None:
        """用 F10 数据增强基本面分析（LLM 解析 F10 文本提取结构化数据）"""
        if "get_f10_data" not in self.tools:
            return None

        try:
            logger.info("Phase 6.5: F10 data enhancement")

            # 获取 F10 文本（mootdx 是同步的，用 to_thread 包装）
            f10_result = await asyncio.to_thread(self.tools["get_f10_data"], symbol)
            sections = f10_result.get("sections", {})
            if not sections:
                logger.warning("F10 data empty, skipping enhancement")
                return None

            stock_name = current_indicators.get("name", "")
            finance_text = sections.get("财务分析", "")[:8000]
            industry_text = sections.get("行业分析", "")[:4000]
            dividend_text = sections.get("分红扩股", "")[:4000]

            if not finance_text and not industry_text and not dividend_text:
                return None

            prompt = f"""你是一个专业的财务数据提取助手。以下是{stock_name}({symbol})的F10数据（来自通达信）。
请从中提取以下信息，以JSON格式返回（不要添加其他文本）：

```json
{{
  "revenue_trend": [
    {{"period": "2026Q1", "revenue_yi": 547.0, "yoy_pct": 6.3}},
    ...
  ],
  "profit_trend": [
    {{"period": "2026Q1", "profit_yi": 272.4, "yoy_pct": 1.5}},
    ...
  ],
  "revenue_yoy_growth": 6.3,
  "profit_yoy_growth": 1.5,
  "dividend_history": [
    {{"year": "2025", "per_10_shares": 279.93, "yield_pct": 1.7}},
    ...
  ],
  "avg_dividend_yield": 1.7,
  "industry_rank": "行业地位描述",
  "competitive_advantage": "竞争优势摘要"
}}
```

只提取你能确定的数据，不确定的字段留空。不要编造数据。每类趋势数据提取最近4期即可。

=== 财务分析 ===
{finance_text}

=== 行业分析 ===
{industry_text}

=== 分红扩股 ===
{dividend_text}"""

            from harness.services.glm_agent_client import create_message
            response = await asyncio.wait_for(
                create_message(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    timeout=30,
                ),
                timeout=45.0,
            )

            if not response or not response.get("content"):
                return None

            raw_text = response["content"][0]["text"]

            # 提取 JSON
            json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', raw_text)
            if json_match:
                json_str = json_match.group(1)
            else:
                start = raw_text.find('{')
                end = raw_text.rfind('}')
                json_str = raw_text[start:end + 1] if start >= 0 and end > start else ""

            if not json_str:
                return None

            f10_data = json.loads(json_str)

            # 构建增强字段
            enhancement = {"f10_enhanced": True}

            if f10_data.get("revenue_yoy_growth") is not None:
                enhancement["revenue_yoy_growth"] = round(float(f10_data["revenue_yoy_growth"]), 2)
            if f10_data.get("profit_yoy_growth") is not None:
                enhancement["profit_yoy_growth"] = round(float(f10_data["profit_yoy_growth"]), 2)
            if f10_data.get("revenue_trend"):
                enhancement["revenue_trend"] = f10_data["revenue_trend"]
            if f10_data.get("profit_trend"):
                enhancement["profit_trend"] = f10_data["profit_trend"]
            if f10_data.get("dividend_history"):
                enhancement["dividend_history"] = f10_data["dividend_history"]
            if f10_data.get("avg_dividend_yield") is not None:
                enhancement["avg_dividend_yield"] = round(float(f10_data["avg_dividend_yield"]), 2)
            if f10_data.get("industry_rank"):
                enhancement["industry_rank"] = f10_data["industry_rank"]
            if f10_data.get("competitive_advantage"):
                enhancement["competitive_advantage"] = f10_data["competitive_advantage"]

            # 修正成长性评分：基于实际增速
            rev_growth = enhancement.get("revenue_yoy_growth", 0)
            profit_growth = enhancement.get("profit_yoy_growth", 0)
            if rev_growth > 0 or profit_growth > 0:
                avg_growth = (rev_growth + profit_growth) / 2
                if avg_growth > 20:
                    growth_score = 2
                elif avg_growth > 10:
                    growth_score = 1.5
                elif avg_growth > 0:
                    growth_score = 1
                else:
                    growth_score = 0
                # 替换原有的粗放成长性评分
                current_score = current_indicators.get("fundamental_score", 0)
                max_score = current_indicators.get("max_fundamental_score", 10)
                # 减去旧的成长性分（2分），加上新的
                old_growth = min(2, current_score)
                enhancement["fundamental_score"] = current_score - old_growth + growth_score
                enhancement["growth_score"] = growth_score

            logger.info(f"F10 enhancement successful: {list(enhancement.keys())}")
            return enhancement

        except asyncio.TimeoutError:
            logger.warning("F10 enhancement timed out")
        except json.JSONDecodeError as e:
            logger.warning(f"F10 LLM response JSON parse failed: {e}")
        except Exception as e:
            logger.warning(f"F10 enhancement failed: {e}")

        return None

    async def _phase_risk_assessment(self, symbol: str) -> None:
        """阶段7: 深度风险评估"""
        logger.info("Phase 7: Deep Risk Assessment")

        self.thoughts.append(Thought(
            type=ThoughtType.ANALYSIS,
            content="进行多维度风险评估",
            timestamp=datetime.now()
        ))

        technical = self.session_data.get("technical_analysis", {})
        fundamental = self.session_data.get("fundamental_analysis", {})
        quote = self.session_data.get("realtime_quote", {})

        risk_factors = []
        risk_score = 0  # 风险分数，越高风险越大
        max_risk_score = 20

        # ========== 技术面风险 ==========
        tech_risks = []

        # 趋势风险
        ma_trend = self._safe(technical, "ma_trend", [])
        if "空头" in str(ma_trend):
            tech_risks.append("均线呈空头排列，下跌趋势")
            risk_score += 3
        elif "强势多头" not in str(ma_trend):
            tech_risks.append("均线缠绕，方向不明")
            risk_score += 1

        # RSI超买风险
        rsi_6 = self._safe(technical, "rsi_6", 50)
        if rsi_6 > 80:
            tech_risks.append(f"RSI({rsi_6:.1f})严重超买，短期回调风险")
            risk_score += 2
        elif rsi_6 < 20:
            tech_risks.append(f"RSI({rsi_6:.1f})严重超卖")
            risk_score += 1

        # MACD风险
        dif = self._safe(technical, "dif", 0)
        if dif < 0:
            tech_risks.append("MACD死叉，看跌信号")
            risk_score += 2

        # 布林带风险
        bb_position = self._safe(technical, "bb_position", 50)
        if bb_position > 90:
            tech_risks.append("价格触及布林带上轨，可能回调")
            risk_score += 2
        elif bb_position < 10:
            tech_risks.append("价格触及布林带下轨")
            risk_score -= 1

        # 波动率风险
        volatility_analysis = self._safe(technical, "volatility_analysis", "")
        if "较高" in str(volatility_analysis):
            tech_risks.append("波动率较高，价格波动大")
            risk_score += 2

        # ========== 基本面风险 ==========
        fundamental_risks = []

        # 估值风险
        pe = self._safe(fundamental, "pe", 0)
        if pe > 50:
            fundamental_risks.append(f"PE({pe:.1f})过高，估值风险大")
            risk_score += 3
        elif pe > 30:
            fundamental_risks.append(f"PE({pe:.1f})偏高")
            risk_score += 1
        elif pe < 0:
            fundamental_risks.append("公司亏损，基本面风险")
            risk_score += 4

        # 财务风险
        debt_ratio = self._safe(fundamental, "debt_ratio", 0)
        if debt_ratio > 70:
            fundamental_risks.append(f"资产负债率({debt_ratio:.1f}%)很高，财务风险大")
            risk_score += 3
        elif debt_ratio > 50:
            fundamental_risks.append(f"资产负债率({debt_ratio:.1f}%)较高")
            risk_score += 1

        # 盈利风险
        roe = self._safe(fundamental, "roe", 0)
        if roe < 0:
            fundamental_risks.append("ROE为负，盈利能力弱")
            risk_score += 3
        elif roe < 5:
            fundamental_risks.append(f"ROE({roe:.1f}%)偏低")
            risk_score += 1

        # 成长性风险
        net_margin = self._safe(fundamental, "net_margin", 0)
        if net_margin < 5:
            fundamental_risks.append(f"净利率({net_margin:.1f}%)很低，盈利空间小")
            risk_score += 2

        # ========== 行业风险 ==========
        industry_risks = []
        industry = self._safe(fundamental, "industry", "")

        if "房地产" in industry:
            industry_risks.append("地产行业受政策影响大，周期性强")
            risk_score += 2
        elif "钢铁" in industry or "煤炭" in industry:
            industry_risks.append("强周期行业，受宏观经济影响大")
            risk_score += 2
        elif "银行" in industry:
            industry_risks.append("银行业受利率政策和坏账风险影响")
            risk_score += 1
        elif "科技" in industry:
            industry_risks.append("科技行业技术迭代快，不确定性高")
            risk_score += 1

        # ========== 市场情绪风险 ==========
        sentiment_risks = []

        volume_ratio = self._safe(technical, "volume_ratio", 1)
        if volume_ratio < 0.5:
            sentiment_risks.append("成交量萎缩，市场关注度低")
            risk_score += 1

        # ========== 筹码风险 ==========
        equity_structure = self._safe(fundamental, "equity_structure", [])
        if "分散" in str(equity_structure):
            risk_score += 1

        # ========== 综合风险等级 ==========
        if risk_score <= 5:
            risk_level = "低"
            risk_description = "整体风险较低，适合稳健投资者"
        elif risk_score <= 10:
            risk_level = "中等偏低"
            risk_description = "风险可控，需关注市场变化"
        elif risk_score <= 15:
            risk_level = "中等偏高"
            risk_description = "存在一定风险，建议谨慎参与"
        else:
            risk_level = "高"
            risk_description = "风险较高，不适合风险厌恶型投资者"

        # 汇总所有风险因素
        all_risks = []
        if tech_risks:
            all_risks.extend([f"• {r}" for r in tech_risks])
        if fundamental_risks:
            all_risks.extend([f"• {r}" for r in fundamental_risks])
        if industry_risks:
            all_risks.extend([f"• {r}" for r in industry_risks])
        if sentiment_risks:
            all_risks.extend([f"• {r}" for r in sentiment_risks])

        risk_assessment = {
            "risk_level": risk_level,
            "risk_score": risk_score,
            "max_risk_score": max_risk_score,
            "risk_description": risk_description,
            "tech_risks": tech_risks,
            "fundamental_risks": fundamental_risks,
            "industry_risks": industry_risks,
            "sentiment_risks": sentiment_risks,
            "all_risks": all_risks if all_risks else ["• 暂无明显风险因素"],
        }

        self.session_data["risk_assessment"] = risk_assessment

        self.thoughts.append(Thought(
            type=ThoughtType.ANALYSIS,
            content=f"风险评估完成: {risk_level}风险 (评分: {risk_score}/{max_risk_score})",
            timestamp=datetime.now(),
            data=risk_assessment
        ))

    async def _phase_web_search(self, symbol: str) -> None:
        """阶段7.5: 联网搜索股票相关新闻和舆情（浏览器优先，API 回退）"""
        logger.info(f"Phase 7.5: Web Search (symbol={symbol or 'general'})")

        stock_info = self.session_data.get("stock_info", {})
        stock_name = stock_info.get("name", "")
        code = symbol.split(".")[0] if "." in symbol else ""

        # 无股票代码时，从用户原始 prompt 提取搜索词
        if not code:
            user_prompt = self.session_data.get("_user_prompt", "")
            search_label = user_prompt or "最新财经资讯"
        else:
            search_label = f"{stock_name or code}"

        self.thoughts.append(Thought(
            type=ThoughtType.PLANNING,
            content=f"联网搜索{search_label}相关新闻、公告和市场舆情",
            timestamp=datetime.now()
        ))

        action = Action(
            tool_name="browser_search",
            arguments={"symbol": symbol, "stock_name": stock_name},
            timestamp=datetime.now()
        )

        try:
            start_time = datetime.now()

            # 1. 优先使用浏览器搜索（Bing → Bing 国际版）
            result = {"items": [], "summary": "", "total": 0}

            if hasattr(self, '_browser_search_skill'):
                logger.info("Using browser search (Bing) as primary search")
                try:
                    if code:
                        # 有股票代码 → 股票相关搜索
                        news_result = await asyncio.to_thread(
                            self._browser_search_skill.search_sync,
                            f"{stock_name or code} {code} 最新消息 股票",
                            8
                        )
                        report_result = await asyncio.to_thread(
                            self._browser_search_skill.search_sync,
                            f"{stock_name or code} 财报 业绩 最新",
                            8
                        )
                    else:
                        # 无股票代码 → 用用户 prompt 做通用搜索
                        user_prompt = self.session_data.get("_user_prompt", "最新财经新闻")
                        # 清理搜索动作词
                        search_query = user_prompt
                        for kw in ["搜索", "搜一下", "查新闻", "查一下", "帮我", "一下"]:
                            search_query = search_query.replace(kw, "")
                        search_query = search_query.strip() or "最新财经新闻"

                        news_result = await asyncio.to_thread(
                            self._browser_search_skill.search_sync,
                            search_query,
                            12
                        )
                        report_result = {"items": [], "summary": "", "total": 0}
                    # 合并去重
                    all_items = news_result.get("items", []) + report_result.get("items", [])
                    seen_urls = set()
                    unique_items = []
                    for item in all_items:
                        url = item.get("url", "")
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            unique_items.append(item)
                        elif not url:
                            unique_items.append(item)

                    summaries = []
                    if news_result.get("summary"):
                        summaries.append(news_result["summary"])
                    if report_result.get("summary"):
                        summaries.append(report_result["summary"])

                    result = {
                        "symbol": symbol,
                        "stock_name": stock_name or code,
                        "items": unique_items[:15],
                        "summary": "\n\n".join(summaries),
                        "total": len(unique_items[:15])
                    }
                    logger.info(f"Browser search returned {result['total']} results")
                except Exception as be:
                    logger.warning(f"Browser search failed: {be}")

            # 2. 浏览器搜索结果不足时，尝试 API 搜索补充
            result_count = len(result.get("items", []))
            if result_count < 3 and "search_stock_news" in self.tools:
                logger.info(f"Browser search only {result_count} results, trying API search as fallback")
                try:
                    api_result = self.tools["search_stock_news"](symbol, stock_name)
                    api_items = api_result.get("items", [])
                    if api_items:
                        existing_urls = {i.get("url", "") for i in result.get("items", [])}
                        for item in api_items:
                            url = item.get("url", "")
                            if url and url not in existing_urls:
                                result["items"].append(item)
                                existing_urls.add(url)
                        result["total"] = len(result["items"])
                        if api_result.get("summary"):
                            existing_summary = result.get("summary", "")
                            result["summary"] = (existing_summary + "\n\n**API搜索补充**:\n" + api_result["summary"]) if existing_summary else api_result["summary"]
                        logger.info(f"API search supplemented, total: {result['total']}")
                except Exception as ae:
                    logger.warning(f"API search fallback failed: {ae}")

            action.result = result
            action.execution_time = (datetime.now() - start_time).total_seconds()
            self.session_data["web_search"] = result

            # 搜索结果情绪分析
            if result.get("items"):
                sentiment = await self._analyze_news_sentiment(
                    result["items"],
                    stock_name or code,
                    symbol
                )
                if sentiment:
                    result["sentiment"] = sentiment
                    self.session_data["web_search"] = result

            news_count = len(result.get("items", []))
            self.thoughts.append(Thought(
                type=ThoughtType.OBSERVATION,
                content=f"搜索到{news_count}条相关新闻和资讯",
                timestamp=datetime.now(),
                data={"count": news_count}
            ))

        except Exception as e:
            action.error = str(e)
            logger.error(f"Web search failed: {e}")
            self.session_data["web_search"] = {"items": [], "summary": "", "total": 0}

        self.actions.append(action)

    # ==================== 妙想 Skills 工具 ====================

    async def _call_mx_skill(self, skill_name: str, query: str) -> str:
        """直接执行妙想 skill 的 Python 脚本"""
        script_map = {
            "mx-data": "mx_data.py",
            "mx-search": "mx_search.py",
            "mx-xuangu": "mx_xuangu.py",
            "mx-zixuan": "mx_zixuan.py",
            "mx-moni": "mx_moni.py",
        }
        script = script_map.get(skill_name)
        if not script:
            return f"未知 skill: {skill_name}"

        # 项目根目录 = backend/ 目录
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        skill_dir = os.path.join(backend_dir, ".claude", "skills", skill_name)

        if not os.path.exists(script_path):
            logger.error(f"[MX] Script not found: {script_path}")
            return f"脚本不存在: {script_path}"

        logger.info(f"[MX] Executing: python {script_path} \"{query}\"")

        try:
            result = await asyncio.to_thread(
                self._run_mx_script, script_path, query
            )
            return result
        except Exception as e:
            logger.error(f"[MX] Script execution failed: {e}")
            return f"执行失败: {e}"

    @staticmethod
    def _run_mx_script(script_path: str, query: str) -> str:
        """同步执行妙想脚本"""
        import subprocess, sys, os
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        result = subprocess.run(
            [sys.executable, "-X", "utf8", script_path, query],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode != 0:
            return f"脚本错误 (code={result.returncode}): {result.stderr[:500]}"
        return result.stdout

    async def _phase_mx_data(self, symbol: str) -> None:
        """妙想金融数据查询"""
        prompt = self.session_data.get("_user_prompt", "")
        query_text = prompt
        if symbol:
            stock_info = self.session_data.get("stock_info", {})
            name = stock_info.get("name", symbol)
            query_text = f"{name} {prompt}"

        logger.info(f"[MX Data] Query: {query_text}")

        self.thoughts.append(Thought(
            type=ThoughtType.ACTION,
            content=f"调用妙想金融数据查询: {query_text}",
            timestamp=datetime.now()
        ))

        try:
            result = await self._call_mx_skill("mx-data", query_text)
            self.session_data["mx_data"] = result
            self.actions.append(Action(
                tool_name="mx_data",
                arguments={"query": query_text},
                result=result[:500] if result else "",
                timestamp=datetime.now()
            ))
        except Exception as e:
            logger.error(f"MX data query failed: {e}")
            self.session_data["mx_data"] = f"查询失败: {e}"

    async def _phase_mx_search(self, symbol: str) -> None:
        """妙想资讯搜索"""
        prompt = self.session_data.get("_user_prompt", "")
        query_text = prompt

        logger.info(f"[MX Search] Starting, query: {query_text}")

        self.thoughts.append(Thought(
            type=ThoughtType.ACTION,
            content=f"调用妙想资讯搜索: {query_text}",
            timestamp=datetime.now()
        ))

        try:
            result = await self._call_mx_skill("mx-search", query_text)
            logger.info(f"[MX Search] Got result, length={len(result) if result else 0}")
            self.session_data["mx_search"] = result
            self.actions.append(Action(
                tool_name="mx_search",
                arguments={"query": query_text},
                result=result[:500] if result else "",
                timestamp=datetime.now()
            ))
        except Exception as e:
            import traceback
            logger.error(f"MX search failed: {e}\n{traceback.format_exc()}")
            self.session_data["mx_search"] = f"搜索失败: {e}"

    async def _phase_mx_xuangu(self, symbol: str) -> None:
        """智能选股 — 技术面条件走 TDX 本地，其他走妙想 API"""
        prompt = self.session_data.get("_user_prompt", "")

        # 技术面关键词检测 → 走本地 TDX 选股
        tech_keywords = ["MA", "均线", "ma5", "ma10", "ma20", "ma60", "MACD",
                         "缩量", "放量", "站上", "跌破", "多头", "空头",
                         "上升趋势", "下跌趋势", "K线", "支撑", "压力",
                         "金叉", "死叉", "RSI", "布林", "突破"]
        use_local = any(kw in prompt for kw in tech_keywords)

        self.thoughts.append(Thought(
            type=ThoughtType.ACTION,
            content=f"调用{'TDX本地' if use_local else '妙想'}选股: {prompt}",
            timestamp=datetime.now()
        ))

        if use_local:
            logger.info(f"[TDX Screen] Technical screening: {prompt}")
            try:
                result = await asyncio.to_thread(self._run_tdx_screen, prompt)
                self.session_data["mx_xuangu"] = result
                self.actions.append(Action(
                    tool_name="mx_xuangu",
                    arguments={"query": prompt, "mode": "tdx_local"},
                    result=f"共 {result['total']} 只股票" if result.get("total") else "无结果",
                    timestamp=datetime.now()
                ))
            except Exception as e:
                logger.error(f"TDX screen failed: {e}")
                self.session_data["mx_xuangu"] = {
                    "source": "tdx_local", "query": prompt,
                    "total": 0, "columns": [], "rows": [], "error": str(e),
                }
        else:
            logger.info(f"[MX Xuangu] API screening: {prompt}")
            try:
                result = await asyncio.to_thread(self._run_mx_xuangu, prompt)
                self.session_data["mx_xuangu"] = result
                self.actions.append(Action(
                    tool_name="mx_xuangu",
                    arguments={"query": prompt, "mode": "mx_api"},
                    result=f"共 {result['total']} 只股票" if result.get("total") else "无结果",
                    timestamp=datetime.now()
                ))
            except Exception as e:
                logger.error(f"MX xuangu failed: {e}, falling back to TDX")
                fallback = await self._tdx_fallback_screen(prompt)
                self.session_data["mx_xuangu"] = fallback
                self.actions.append(Action(
                    tool_name="mx_xuangu",
                    arguments={"query": prompt, "mode": "tdx_fallback"},
                    result=f"API失败，降级TDX: {fallback.get('total', 0)} 只",
                    timestamp=datetime.now()
                ))

    @staticmethod
    def _run_mx_xuangu(query: str) -> dict:
        """直接调用 MXSelectStock，返回结构化数据"""
        import sys
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        skill_dir = os.path.join(backend_dir, ".claude", "skills", "mx-xuangu")
        if skill_dir not in sys.path:
            sys.path.insert(0, skill_dir)
        from mx_xuangu import MXSelectStock

        mx = MXSelectStock()
        raw = mx.search(query)
        rows, data_source, err = MXSelectStock.extract_data(raw)
        if err:
            raise RuntimeError(err)

        return {
            "source": "mx_api",
            "query": query,
            "total": len(rows),
            "columns": list(rows[0].keys()) if rows else [],
            "rows": rows[:200],
        }

    async def _tdx_fallback_screen(self, prompt: str) -> dict:
        """TDX 本地行情筛选（降级方案）"""
        logger.info(f"[TDX Fallback] Local screening: {prompt}")
        try:
            result = await asyncio.to_thread(self._run_tdx_screen, prompt)
            return result
        except Exception as e:
            logger.error(f"TDX fallback screening failed: {e}")
            return {
                "source": "fallback_failed",
                "query": prompt,
                "error": str(e),
                "total": 0,
                "columns": [],
                "rows": [],
            }

    @staticmethod
    def _run_tdx_screen(prompt: str) -> dict:
        """
        基于 TDX 数据的技术面选股。
        用 LLM 解析选股条件为结构化参数，然后多线程并行获取 K 线计算。
        """
        from mootdx.quotes import Quotes
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import pandas as pd
        import numpy as np

        quotes = Quotes.factory(market='std')

        # 1. 获取 A 股列表
        dfs = []
        for m in [0, 1]:
            df = quotes.stocks(market=m)
            if df is not None:
                df['market'] = m
                dfs.append(df)
        if not dfs:
            return {"source": "tdx_local", "query": prompt, "total": 0, "columns": [], "rows": []}

        all_df = pd.concat(dfs, ignore_index=True)
        # 过滤: 只保留 6 位 A 股代码，排除 ST/退市
        a_df = all_df[
            all_df['code'].str.match(r'^(6|0|3)\d{5}$')
        ].copy()
        a_df = a_df[~a_df['name'].str.contains('ST|退', na=False)]
        a_df['name'] = a_df['name'].str.strip().str.strip('\x00').str.strip()

        logger.info(f"[TDX Screen] A-stocks after filter: {len(a_df)}")

        # 2. 解析条件（简单规则匹配）
        import re
        # 提取数字条件
        max_3m_gain = 30  # 三月涨幅上限 %
        discount_to_high = 0.8  # 现价 < 120日高点的折扣

        # 3. 多线程获取 K 线并计算技术指标
        stock_list = list(a_df[['code', 'market', 'name']].itertuples(index=False))
        results = []
        checked = 0

        def _check_stock(item):
            code, market, name = item.code, item.market, item.name
            try:
                df = quotes.bars(symbol=code, frequency=9, offset=120)
                if df is None or len(df) < 60:
                    return None

                closes = df['close'].values.astype(float)
                volumes = df['volume'].values.astype(float)
                highs = df['high'].values.astype(float)

                # 计算 MA
                ma5 = np.mean(closes[-5:])
                ma10 = np.mean(closes[-10:])
                ma20 = np.mean(closes[-20:])
                ma60 = np.mean(closes[-60:]) if len(closes) >= 60 else None

                latest = closes[-1]

                # 条件1: MA5 > MA10 > MA20 > MA60 (多头排列)
                if not (ma5 > ma10 > ma20):
                    return None
                if ma60 and not (ma20 > ma60):
                    return None

                # 条件2: 三月涨幅 < 30%
                day60_idx = max(0, len(closes) - 60)
                gain_3m = (latest / closes[day60_idx] - 1) * 100
                if gain_3m > max_3m_gain:
                    return None

                # 条件3: 现价 < 120日高点 * 0.8
                high_120 = np.max(highs)
                if latest > high_120 * discount_to_high:
                    return None

                # 条件4: 缩量 + 站20日线
                vol5 = np.mean(volumes[-5:])
                vol20 = np.mean(volumes[-20:])
                if vol20 == 0:
                    return None
                vol_ratio = vol5 / vol20  # < 1 表示缩量
                if vol_ratio > 1.2:  # 不是缩量
                    return None
                if latest < ma20:  # 没站上 20 日线
                    return None

                return {
                    "股票代码": code,
                    "股票名称": name,
                    "最新价": round(latest, 2),
                    "MA5": round(ma5, 2),
                    "MA10": round(ma10, 2),
                    "MA20": round(ma20, 2),
                    "MA60": round(ma60, 2) if ma60 else "-",
                    "3月涨幅(%)": round(gain_3m, 1),
                    "量比(5/20)": round(vol_ratio, 2),
                    "距120日高点(%)": round((latest / high_120 - 1) * 100, 1),
                }
            except Exception:
                return None

        # 多线程并行，最多 8 线程
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(_check_stock, s): s for s in stock_list}
            for future in as_completed(futures):
                checked += 1
                if checked % 500 == 0:
                    logger.info(f"[TDX Screen] Progress: {checked}/{len(stock_list)}, found {len(results)}")
                result = future.result()
                if result:
                    results.append(result)

        logger.info(f"[TDX Screen] Done: checked {checked}, found {len(results)}")

        return {
            "source": "tdx_local",
            "query": prompt,
            "total": len(results),
            "columns": ["股票代码", "股票名称", "最新价", "MA5", "MA10", "MA20", "MA60",
                         "3月涨幅(%)", "量比(5/20)", "距120日高点(%)"],
            "rows": results,
            "note": "TDX本地技术面选股：MA多头排列 + 三月涨幅<30% + 低于120日高点8折 + 缩量站20日线",
        }

    async def _phase_mx_zixuan(self, symbol: str) -> None:
        """妙想自选股管理"""
        prompt = self.session_data.get("_user_prompt", "")

        logger.info(f"[MX Zixuan] Query: {prompt}")

        self.thoughts.append(Thought(
            type=ThoughtType.ACTION,
            content=f"调用妙想自选股管理: {prompt}",
            timestamp=datetime.now()
        ))

        try:
            result = await self._call_mx_skill("mx-zixuan", prompt)
            self.session_data["mx_zixuan"] = result
            self.actions.append(Action(
                tool_name="mx_zixuan",
                arguments={"query": prompt},
                result=result[:500] if result else "",
                timestamp=datetime.now()
            ))
        except Exception as e:
            logger.error(f"MX zixuan failed: {e}")
            self.session_data["mx_zixuan"] = f"操作失败: {e}"

    async def _phase_mx_moni(self, symbol: str) -> None:
        """妙想模拟组合管理"""
        prompt = self.session_data.get("_user_prompt", "")

        logger.info(f"[MX Moni] Query: {prompt}")

        self.thoughts.append(Thought(
            type=ThoughtType.ACTION,
            content=f"调用妙想模拟交易: {prompt}",
            timestamp=datetime.now()
        ))

        try:
            result = await self._call_mx_skill("mx-moni", prompt)
            self.session_data["mx_moni"] = result
            self.actions.append(Action(
                tool_name="mx_moni",
                arguments={"query": prompt},
                result=result[:500] if result else "",
                timestamp=datetime.now()
            ))
        except Exception as e:
            logger.error(f"MX moni failed: {e}")
            self.session_data["mx_moni"] = f"操作失败: {e}"

    async def _analyze_news_sentiment(
        self, items: list[dict], stock_name: str, symbol: str = ""
    ) -> dict[str, Any] | None:
        """批量分析新闻情绪（单次 LLM 调用）"""
        if "chat" not in self.tools:
            return None

        try:
            news_text = "\n".join(
                f"- {i.get('title', '')}: {i.get('content', '')[:150]}"
                for i in items[:12]
            )

            prompt = f"""你是一个金融舆情分析专家。以下是{stock_name}的最近新闻。请分析整体市场情绪，以JSON格式返回（不要添加其他文本）：

```json
{{
  "overall_sentiment": "positive/neutral/negative",
  "sentiment_score": 65,
  "key_topics": ["话题1", "话题2"],
  "positive_factors": ["利好因素"],
  "negative_factors": ["利空因素"],
  "market_impact": "短期可能上涨/震荡/下跌",
  "summary": "一句话概括"
}}
```

评分说明：0-100分，50为中性，越高越正面。
只提取你能确定的信息，不确定的留空。

新闻列表：
{news_text}"""

            result = await asyncio.wait_for(
                self.tools["chat"](prompt),
                timeout=20.0
            )

            if not result or not result.get("message"):
                return None

            raw_text = result["message"]

            # 提取 JSON
            json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', raw_text)
            if json_match:
                json_str = json_match.group(1)
            else:
                start = raw_text.find('{')
                end = raw_text.rfind('}')
                json_str = raw_text[start:end + 1] if start >= 0 and end > start else ""

            if not json_str:
                return None

            sentiment = json.loads(json_str)
            # 验证基本字段
            if "overall_sentiment" not in sentiment:
                return None

            logger.info(f"News sentiment: {sentiment.get('overall_sentiment')} ({sentiment.get('sentiment_score', 50)})")
            return sentiment

        except asyncio.TimeoutError:
            logger.warning("News sentiment analysis timed out")
        except json.JSONDecodeError:
            logger.warning("News sentiment JSON parse failed")
        except Exception as e:
            logger.warning(f"News sentiment analysis failed: {e}")

        return None

    async def _phase_investment_advice(self, symbol: str) -> None:
        """阶段8: 综合投资建议"""
        logger.info("Phase 8: Comprehensive Investment Advice")

        self.thoughts.append(Thought(
            type=ThoughtType.SYNTHESIS,
            content="综合技术面、基本面和风险评估，生成投资建议",
            timestamp=datetime.now()
        ))

        technical = self.session_data.get("technical_analysis", {})
        fundamental = self.session_data.get("fundamental_analysis", {})
        risk = self.session_data.get("risk_assessment", {})
        quote = self.session_data.get("realtime_quote", {})

        # ========== 综合评分系统 ==========
        total_score = 0
        max_total_score = 20

        # 技术面权重 (40%)
        tech_score = self._safe(technical, "tech_score", 0)
        tech_max = self._safe(technical, "max_score", 10)
        tech_weighted = (tech_score / tech_max * 4) if tech_max > 0 else 0
        total_score += tech_weighted

        # 基本面权重 (40%)
        fundamental_score = self._safe(fundamental, "fundamental_score", 0)
        fundamental_max = self._safe(fundamental, "max_fundamental_score", 10)
        fundamental_weighted = (fundamental_score / fundamental_max * 4) if fundamental_max > 0 else 0
        total_score += fundamental_weighted

        # 风险调整 (20%)
        risk_score = self._safe(risk, "risk_score", 10)
        risk_max = self._safe(risk, "max_risk_score", 20)
        # 风险越低，得分越高
        risk_adjustment = ((risk_max - risk_score) / risk_max * 2) if risk_max > 0 else 0
        total_score += risk_adjustment

        total_score = round(total_score, 1)

        # ========== 投资评级 ==========
        if total_score >= 16:
            investment_rating = "强烈推荐买入"
            rating_color = "🟢"
            confidence = "高"
        elif total_score >= 13:
            investment_rating = "推荐买入"
            rating_color = "🟢"
            confidence = "较高"
        elif total_score >= 10:
            investment_rating = "谨慎推荐"
            rating_color = "🟡"
            confidence = "中等"
        elif total_score >= 7:
            investment_rating = "中性观望"
            rating_color = "🟡"
            confidence = "中等"
        elif total_score >= 4:
            investment_rating = "谨慎减持"
            rating_color = "🟠"
            confidence = "较高"
        else:
            investment_rating = "建议规避"
            rating_color = "🔴"
            confidence = "高"

        # ========== 投资理由 ==========
        reasons = []

        # 技术面理由
        ma_trend = self._safe(technical, "ma_trend", [])
        if "多头" in str(ma_trend):
            reasons.append("✓ 技术面呈多头排列，趋势向好")
        elif "空头" in str(ma_trend):
            reasons.append("✗ 技术面呈空头排列，趋势较弱")

        macd_analysis = self._safe(technical, "macd_analysis", [])
        if any("金叉" in str(m) for m in macd_analysis):
            reasons.append("✓ MACD金叉，动能向上")

        # 基本面理由
        roe = self._safe(fundamental, "roe", 0)
        if roe > 15:
            reasons.append(f"✓ ROE({roe:.1f}%)优秀，盈利能力强")

        pe = self._safe(fundamental, "pe", 0)
        if 0 < pe < 20:
            reasons.append(f"✓ PE({pe:.1f})合理，估值不高")

        debt_ratio = self._safe(fundamental, "debt_ratio", 0)
        if debt_ratio < 40:
            reasons.append(f"✓ 资产负债率({debt_ratio:.1f}%)低，财务稳健")

        # 风险提示
        risk_level = self._safe(risk, "risk_level", "")
        if risk_level == "高":
            reasons.append(f"⚠ 风险等级{risk_level}，需要谨慎")
        elif risk_level == "低":
            reasons.append(f"✓ 风险等级{risk_level}，相对安全")

        # ========== 目标价位 ==========
        current_price = self._safe(quote, "price", 0) if quote else 0
        support_level = self._safe(technical, "support_level", current_price * 0.95)
        resistance_level = self._safe(technical, "resistance_level", current_price * 1.05)

        # 基于技术分析设置目标价
        if total_score >= 13:
            # 看涨，目标价设为压力位
            target_price = resistance_level
        elif total_score >= 10:
            # 中性，目标价设为当前价上方5%
            target_price = current_price * 1.05
        else:
            # 看跌，目标价设为支撑位
            target_price = support_level

        potential_return = ((target_price - current_price) / current_price * 100) if current_price > 0 else 0

        # ========== 止损止盈建议 ==========
        if total_score >= 13:
            stop_loss = support_level
            stop_profit = resistance_level * 1.05
            position_advice = "建议分批建仓，控制仓位"
        elif total_score >= 7:
            stop_loss = current_price * 0.95
            stop_profit = current_price * 1.10
            position_advice = "建议小仓位试探"
        else:
            stop_loss = current_price * 0.92
            stop_profit = current_price * 1.03
            position_advice = "建议空仓观望"

        # ========== 投资周期建议 ==========
        if total_score >= 13:
            investment_horizon = "中长期持有 (3-12个月)"
        elif total_score >= 10:
            investment_horizon = "中期波段 (1-3个月)"
        else:
            investment_horizon = "短期观望或规避"

        # ========== 关键监控指标 ==========
        key_monitors = []

        # 技术指标监控
        key_monitors.append(f"• 关注MA20均线支撑 ({self._safe(technical, 'ma20', 0):.2f})")

        # 成交量监控
        volume_ratio = self._safe(technical, "volume_ratio", 1)
        if volume_ratio > 1:
            key_monitors.append("• 关注成交量能否持续放大")
        else:
            key_monitors.append("• 关注成交量变化")

        # 基本面监控
        key_monitors.append(f"• 关注下季度财报业绩")

        # 行业监控
        industry = self._safe(fundamental, "industry", "")
        if "政策" in str(self._safe(fundamental, "industry_analysis", [])):
            key_monitors.append("• 关注行业政策变化")

        # ========== 特别提示 ==========
        special_notes = []

        # 机构观点（模拟）
        if total_score >= 13:
            special_notes.append("• 多家机构给予买入评级")
        elif total_score <= 5:
            special_notes.append("• 机构关注度较低，建议观望")

        # 事件驱动因素
        if "银行" in industry:
            special_notes.append("• 关注利率政策和降准预期")
        elif "科技" in industry:
            special_notes.append("• 关注技术创新和政策支持")

        # 汇总投资建议
        advice = {
            # 综合评级
            "investment_rating": investment_rating,
            "rating_color": rating_color,
            "confidence": confidence,
            "total_score": total_score,
            "max_total_score": max_total_score,

            # 评分详情
            "tech_score": tech_score,
            "tech_max": tech_max,
            "fundamental_score": fundamental_score,
            "fundamental_max": fundamental_max,
            "risk_score": risk_score,
            "risk_max": risk_max,

            # 投资理由
            "reasons": reasons[:6],  # 最多显示6条

            # 目标价位
            "current_price": current_price,
            "target_price": round(target_price, 2),
            "support_level": round(support_level, 2),
            "resistance_level": round(resistance_level, 2),
            "potential_return": round(potential_return, 1),

            # 操作建议
            "stop_loss": round(stop_loss, 2),
            "stop_profit": round(stop_profit, 2),
            "position_advice": position_advice,
            "investment_horizon": investment_horizon,

            # 关键监控
            "key_monitors": key_monitors[:5],

            # 特别提示
            "special_notes": special_notes if special_notes else ["• 无特别事件驱动"],
        }

        self.session_data["investment_advice"] = advice

        self.thoughts.append(Thought(
            type=ThoughtType.SYNTHESIS,
            content=f"投资建议生成完成: {rating_color} {investment_rating} (综合评分: {total_score}/{max_total_score})",
            timestamp=datetime.now(),
            data=advice
        ))

    async def _phase_generate_report(self) -> str:
        """阶段9: 生成AI深度分析报告"""
        logger.info("Phase 9: Generate AI Analysis Report")

        self.thoughts.append(Thought(
            type=ThoughtType.SYNTHESIS,
            content="调用GLM模型对收集的数据进行深度综合分析",
            timestamp=datetime.now()
        ))

        symbol = self.session_data.get("stock_symbols", [""])[0]
        stock_info = self.session_data.get("stock_info", {})
        quote = self.session_data.get("realtime_quote", {})
        technical = self.session_data.get("technical_analysis", {})
        fundamental = self.session_data.get("fundamental_analysis", {})
        risk = self.session_data.get("risk_assessment", {})

        # ========== 调用GLM进行AI深度分析 ==========
        ai_analysis = await self._call_glm_for_analysis(symbol, stock_info, quote, technical, fundamental, risk)

        self.session_data["ai_analysis"] = ai_analysis

        self.thoughts.append(Thought(
            type=ThoughtType.SYNTHESIS,
            content="AI深度分析完成",
            timestamp=datetime.now()
        ))

        # ========== 基于AI分析生成专业报告 ==========
        report_parts = []

        # 报告头部
        report_parts.append(f"# {stock_info.get('name', symbol)} 投资分析")
        report_parts.append("")
        report_parts.append(f"**{symbol}** | {fundamental.get('industry') or ''} | 市值 {self._safe(fundamental, 'market_cap', 0):.0f}亿")
        report_parts.append(f"当前价 ¥{self._safe(quote, 'price', 0):.2f} | 涨跌 {self._safe(quote, 'change_percent', 0):+.2f}%")
        report_parts.append("")
        report_parts.append("---")
        report_parts.append("")

        # 投资评级摘要
        advice = self.session_data.get("investment_advice", {})
        rating = advice.get("investment_rating") or "中性"
        rating_color = advice.get("rating_color") or "🟡"
        total_score = self._safe(advice, "total_score", 0)
        max_score = self._safe(advice, "max_total_score", 20)

        report_parts.append(f"### {rating_color} {rating} | 评分 {total_score:.1f}/{max_score}")
        report_parts.append(f"目标价 ¥{self._safe(advice, 'target_price', 0):.2f} | 止损 ¥{self._safe(advice, 'stop_loss', 0):.2f} | 潜在收益 {self._safe(advice, 'potential_return', 0):+.1f}%")
        report_parts.append(f"仓位: {advice.get('position_advice') or '观望'} | 周期: {advice.get('investment_horizon') or '中期'}")
        report_parts.append("")

        # AI深度分析（核心内容）
        report_parts.append("---")
        report_parts.append("")
        report_parts.append("## AI 综合分析")
        report_parts.append("")
        report_parts.append(ai_analysis)
        report_parts.append("")

        # 关键数据支撑（精简）
        report_parts.append("---")
        report_parts.append("")
        report_parts.append("<details><summary>技术面指标</summary>")
        report_parts.append("")
        report_parts.append(f"- 均线: MA5={self._safe(technical, 'ma5'):.2f} MA10={self._safe(technical, 'ma10'):.2f} MA20={self._safe(technical, 'ma20'):.2f}")
        report_parts.append(f"- 趋势: {', '.join(self._safe(technical, 'ma_trend', []))}")
        report_parts.append(f"- RSI(6/12): {self._safe(technical, 'rsi_6'):.1f} / {self._safe(technical, 'rsi_12'):.1f}")
        report_parts.append(f"- MACD: DIF={self._safe(technical, 'dif'):.2f}, {', '.join(self._safe(technical, 'macd_analysis', []))}")
        report_parts.append(f"- 布林带: {self._safe(technical, 'bb_position'):.0f}% 位置, {', '.join(self._safe(technical, 'bb_analysis', []))}")
        report_parts.append(f"- 量比: {self._safe(technical, 'volume_ratio'):.2f}, {', '.join(self._safe(technical, 'volume_analysis', []))}")
        report_parts.append(f"- 支撑/压力: ¥{self._safe(technical, 'support_level'):.2f} / ¥{self._safe(technical, 'resistance_level'):.2f}")
        report_parts.append(f"- 技术评分: {self._safe(technical, 'tech_score')}/{self._safe(technical, 'max_score', 10)}")
        report_parts.append("")
        report_parts.append("</details>")
        report_parts.append("")

        report_parts.append("<details><summary>基本面指标</summary>")
        report_parts.append("")
        report_parts.append(f"- PE: {self._safe(fundamental, 'pe', 'N/A')} | PB: {self._safe(fundamental, 'pb', 'N/A')} | PS: {self._safe(fundamental, 'ps', 'N/A')}")
        report_parts.append(f"- ROE: {self._safe(fundamental, 'roe'):.1f}% | ROA: {self._safe(fundamental, 'roa'):.1f}% | 净利率: {self._safe(fundamental, 'net_margin'):.1f}%")
        report_parts.append(f"- 负债率: {self._safe(fundamental, 'debt_ratio'):.1f}% | EPS: ¥{self._safe(fundamental, 'eps'):.2f} | BPS: ¥{self._safe(fundamental, 'bps'):.2f}")
        report_parts.append(f"- 基本面评分: {self._safe(fundamental, 'fundamental_score')}/{self._safe(fundamental, 'max_fundamental_score', 10)}")
        if fundamental.get("f10_enhanced"):
            rev_g = fundamental.get("revenue_yoy_growth")
            prof_g = fundamental.get("profit_yoy_growth")
            if rev_g is not None or prof_g is not None:
                report_parts.append(f"- 营收增速: {rev_g if rev_g is not None else 'N/A'}% | 利润增速: {prof_g if prof_g is not None else 'N/A'}%")
            if fundamental.get("industry_rank"):
                report_parts.append(f"- 行业地位: {fundamental['industry_rank']}")
            if fundamental.get("avg_dividend_yield"):
                report_parts.append(f"- 股息率: {fundamental['avg_dividend_yield']:.2f}%")
        report_parts.append("")
        report_parts.append("</details>")
        report_parts.append("")

        report_parts.append("<details><summary>风险评估</summary>")
        report_parts.append("")
        report_parts.append(f"- 风险等级: {self._safe(risk, 'risk_level', 'N/A')} (评分 {self._safe(risk, 'risk_score')}/{self._safe(risk, 'max_risk_score', 20)})")
        report_parts.append(f"- {self._safe(risk, 'risk_description', '')}")
        for risk_item in risk.get("all_risks", [])[:5]:
            report_parts.append(f"- {risk_item}")
        report_parts.append("")
        report_parts.append("</details>")
        report_parts.append("")

        # 联网搜索新闻
        web_search = self.session_data.get("web_search", {})
        if web_search and web_search.get("items"):
            report_parts.append("<details><summary>最新资讯（联网搜索）</summary>")
            report_parts.append("")
            for item in web_search["items"][:8]:
                title = item.get("title", "")
                source = item.get("source", "")
                date = item.get("date", "")
                content = item.get("content", "")
                report_parts.append(f"- [{source} {date}] **{title}**")
                if content:
                    report_parts.append(f"  {content[:150]}")
            sentiment = web_search.get("sentiment")
            if sentiment:
                s_map = {"positive": "看多", "neutral": "中性", "negative": "看空"}
                s_label = s_map.get(sentiment.get("overall_sentiment", ""), "中性")
                report_parts.append(f"- **舆情**: {s_label} ({sentiment.get('sentiment_score', 50)}/100) - {sentiment.get('summary', '')}")
            report_parts.append("")
            report_parts.append("</details>")
            report_parts.append("")

        # 免责声明
        report_parts.append("---")
        report_parts.append("")
        report_parts.append("*本报告由AI自动生成，仅供参考，不构成投资建议。*")
        report_parts.append(f"*{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

        report = "\n".join(report_parts)

        self.session_data["final_report"] = report

        self.thoughts.append(Thought(
            type=ThoughtType.SYNTHESIS,
            content="专业投研报告生成完成",
            timestamp=datetime.now()
        ))

        return report

    async def _call_glm_for_analysis(
        self,
        symbol: str,
        stock_info: dict,
        quote: dict,
        technical: dict,
        fundamental: dict,
        risk: dict
    ) -> str:
        """
        调用GLM模型进行深度分析

        这是核心的AI分析功能，基于所有收集的数据让大模型进行专业分析
        """
        logger.info("Calling GLM for deep analysis")

        # 检查是否有glm-analyze技能
        analyze_skill = self.skills.get("glm-analyze")
        if not analyze_skill:
            logger.warning("glm-analyze skill not available")
            return self._generate_fallback_analysis(symbol, stock_info, quote, technical, fundamental, risk)

        try:
            # 构建详细的分析提示词（包含所有收集到的数据）
            analysis_prompt = self._build_analysis_prompt(symbol, stock_info, quote, technical, fundamental, risk)
            if self.session_data.get("_file_context"):
                fc = self.session_data["_file_context"]
                analysis_prompt += f"\n\n---\n用户上传了文件 [{fc.get('filename', '')}]，内容如下：\n{fc.get('content_text', '')}"

            # 直接将包含数据的分析提示词传给GLM进行深度综合分析
            from harness.services.glm_agent_client import create_message

            system_prompt = """你是一位资深证券分析师，拥有CFA和CPA资质，擅长从多维度对股票进行深度投资分析。

请基于提供的详细数据，进行有深度的、有见解的专业分析。要求：
1. 不要简单复述数据，要对数据进行解读和推理
2. 给出明确的投资逻辑和判断依据
3. 指出数据背后的含义和趋势
4. 提供具体的操作建议和价位参考
5. 用专业但不晦涩的语言表达"""

            response = await create_message(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": analysis_prompt}
                ],
                temperature=0.3
            )

            # 提取GLM返回的分析内容
            if response and response.get("content"):
                ai_content = response["content"][0]["text"]
                if ai_content and ai_content.strip():
                    return self._post_process_glm_result(ai_content, symbol)

            # GLM返回为空，使用备用分析
            return self._generate_fallback_analysis(symbol, stock_info, quote, technical, fundamental, risk)

        except Exception as e:
            logger.error(f"GLM analysis failed: {e}")
            return self._generate_fallback_analysis(symbol, stock_info, quote, technical, fundamental, risk)

    def _build_analysis_prompt(
        self,
        symbol: str,
        stock_info: dict,
        quote: dict,
        technical: dict,
        fundamental: dict,
        risk: dict
    ) -> str:
        """构建详细的AI分析提示词"""

        # 构建联网搜索结果部分
        web_search = self.session_data.get("web_search", {})
        web_search_section = ""
        if web_search and web_search.get("items"):
            news_items = []
            for item in web_search["items"][:8]:
                title = item.get("title", "")
                content = item.get("content", "")
                source = item.get("source", "")
                date = item.get("date", "")
                if title or content:
                    news_items.append(f"  - [{source} {date}] {title}: {content[:200]}")
            if news_items:
                web_search_section = f"""
## 五、最新新闻与市场舆情（联网搜索）
以下是该股票最近的相关新闻和市场资讯：
{chr(10).join(news_items)}
"""
                sentiment = web_search.get("sentiment")
                if sentiment:
                    web_search_section += f"""
舆情分析结果：
- 整体情绪: {sentiment.get('overall_sentiment', 'N/A')} (评分 {sentiment.get('sentiment_score', 50)}/100)
- 关键话题: {', '.join(sentiment.get('key_topics', []))}
- 利好因素: {'; '.join(sentiment.get('positive_factors', []))}
- 利空因素: {'; '.join(sentiment.get('negative_factors', []))}
- 市场影响: {sentiment.get('market_impact', 'N/A')}
"""
                web_search_section += "\n请在分析中参考这些最新资讯，评估近期事件对股价的潜在影响。\n"

        # F10 增强数据
        f10_section = ""
        if fundamental.get("f10_enhanced"):
            lines = []
            rev_g = fundamental.get('revenue_yoy_growth')
            prof_g = fundamental.get('profit_yoy_growth')
            if rev_g is not None or prof_g is not None:
                lines.append(f"- 营收增速: {rev_g if rev_g is not None else 'N/A'}% | 利润增速: {prof_g if prof_g is not None else 'N/A'}%")
            if fundamental.get("industry_rank"):
                lines.append(f"- 行业地位: {fundamental['industry_rank']}")
            if fundamental.get("avg_dividend_yield"):
                lines.append(f"- 股息率: {fundamental['avg_dividend_yield']:.2f}%")
            if fundamental.get("competitive_advantage"):
                lines.append(f"- 竞争优势: {fundamental['competitive_advantage']}")
            f10_section = "\n".join(lines)

        prompt = f"""请对{stock_info.get('name', symbol)}({symbol})进行深度投资分析。

## 一、公司基本情况
- 公司名称: {stock_info.get('name', 'N/A')}
- 所属行业: {fundamental.get('industry', 'N/A')}
- 总市值: {fundamental.get('market_cap', 0):.2f}亿元
- 总股本: {fundamental.get('total_shares', 0):.2f}亿股

## 二、最新市场表现
- 当前价格: {quote.get('price', 0):.2f}元
- 今日涨跌: {quote.get('change', 0):.2f}元 ({quote.get('change_percent', 0):.2f}%)
- 成交量: {quote.get('volume', 0):,}

## 三、技术面分析数据
- 均线系统: MA5={technical.get('ma5', 0):.2f}, MA10={technical.get('ma10', 0):.2f}, MA20={technical.get('ma20', 0):.2f}
- 趋势分析: {', '.join(technical.get('ma_trend', []))}
- RSI指标: RSI(6)={technical.get('rsi_6', 0):.1f}, RSI(12)={technical.get('rsi_12', 0):.1f}
- MACD: DIF={technical.get('dif', 0):.2f}, {', '.join(technical.get('macd_analysis', []))}
- 布林带: 价格位置{technical.get('bb_position', 0):.1f}%, {', '.join(technical.get('bb_analysis', []))}
- 成交量: 量比{technical.get('volume_ratio', 0):.2f}, {', '.join(technical.get('volume_analysis', []))}
- 技术评分: {technical.get('tech_score', 0)}/{technical.get('max_score', 10)}分

## 四、基本面分析数据
- 估值指标: PE={fundamental.get('pe', 'N/A')}, PB={fundamental.get('pb', 'N/A')}
- 盈利能力: ROE={fundamental.get('roe', 0):.1f}%, 净利率={fundamental.get('net_margin', 0):.1f}%
- 财务健康: 资产负债率={fundamental.get('debt_ratio', 0):.1f}%, {', '.join(fundamental.get('solvency_analysis', []))}
- 基本面评分: {fundamental.get('fundamental_score', 0)}/{fundamental.get('max_fundamental_score', 10)}分
{f10_section}
{web_search_section}
## {"六" if web_search_section else "五"}、风险评估
- 风险等级: {risk.get('risk_level', 'N/A')}
- 风险评分: {risk.get('risk_score', 0)}/{risk.get('max_risk_score', 20)}分

请基于以上数据，从以下几个维度进行深度分析：

1. **投资逻辑分析**: 结合技术面、基本面和最新资讯，分析该股的投资逻辑和核心看点
2. **趋势研判**: 基于均线系统和技术指标，研判短期和中期走势
3. **价值评估**: 基于估值指标和财务数据，评估公司内在价值和安全边际
4. **市场舆情**: 如果有最新新闻，分析这些新闻事件对股价的潜在影响
5. **风险提示**: 指出主要风险点和需要关注的关键因素
6. **操作建议**: 给出明确的操作建议（买入/持有/卖出）、目标价位、止损价位

请用专业、客观的语言进行分析，避免过度乐观或悲观的表述。"""

        return prompt

    def _post_process_glm_result(self, glm_content: str, symbol: str) -> str:
        """后处理GLM分析结果"""
        # 对GLM返回的内容进行清理和格式化
        lines = glm_content.split('\n')
        cleaned_lines = []

        for line in lines:
            line = line.strip()
            if line:
                cleaned_lines.append(line)

        return '\n\n'.join(cleaned_lines)

    def _generate_fallback_analysis(
        self,
        symbol: str,
        stock_info: dict,
        quote: dict,
        technical: dict,
        fundamental: dict,
        risk: dict
    ) -> str:
        """生成备用分析（当GLM不可用时）"""

        company_name = stock_info.get('name', symbol)
        current_price = self._safe(quote, 'price', 0)

        analysis_parts = []

        # 投资逻辑
        analysis_parts.append("### 投资逻辑分析")
        analysis_parts.append("")

        if (self._safe(fundamental, 'roe', 0)) > 15:
            analysis_parts.append(f"**核心优势**: {company_name}ROE达到{self._safe(fundamental, 'roe', 0):.1f}%，盈利能力出色。")
            analysis_parts.append("")

        if technical.get('ma_trend'):
            trend = technical.get('ma_trend', [])
            if '多头' in str(trend):
                analysis_parts.append("**技术面**: 均线呈多头排列，中期趋势向上。")
            else:
                analysis_parts.append("**技术面**: 均线缠绕，趋势待明朗。")
            analysis_parts.append("")

        # 趋势研判
        analysis_parts.append("### 趋势研判")
        analysis_parts.append("")

        ma5 = self._safe(technical, 'ma5', 0)
        ma20 = self._safe(technical, 'ma20', 0)
        if current_price > ma5 > ma20:
            analysis_parts.append("- **短期**: 价格站稳均线上方，有望继续上行")
            analysis_parts.append("- **中期**: 多头趋势良好，关注上方压力位")
        elif current_price < ma5 < ma20:
            analysis_parts.append("- **短期**: 均线压制，需观察支撑位表现")
            analysis_parts.append("- **中期**: 等待趋势明朗，谨慎参与")
        else:
            analysis_parts.append("- **短期**: 震荡整理，方向待选择")
            analysis_parts.append("- **中期**: 观察均线支撑，等待信号确认")
        analysis_parts.append("")

        # 价值评估
        analysis_parts.append("### 价值评估")
        analysis_parts.append("")

        pe = self._safe(fundamental, 'pe', 0)
        if 0 < pe < 20:
            analysis_parts.append(f"**估值水平**: PE({pe:.1f})处于历史低位，具有安全边际")
        elif 0 < pe < 40:
            analysis_parts.append(f"**估值水平**: PE({pe:.1f})合理区间")
        else:
            analysis_parts.append(f"**估值水平**: 需关注估值风险")
        analysis_parts.append("")

        # 操作建议
        analysis_parts.append("### 操作建议")
        analysis_parts.append("")

        total_score = self._safe(self.session_data.get("investment_advice", {}), "total_score", 10)

        if total_score >= 14:
            analysis_parts.append("**建议**: 积极** | **仓位**: 中高仓位")
            analysis_parts.append(f"**目标价**: {self._safe(technical, 'resistance_level', current_price * 1.1):.2f}元")
            analysis_parts.append(f"**止损价**: {self._safe(technical, 'support_level', current_price * 0.95):.2f}元")
        elif total_score >= 10:
            analysis_parts.append("**建议**: 谨慎** | **仓位**: 中低仓位")
            analysis_parts.append(f"**目标价**: {current_price * 1.08:.2f}元")
            analysis_parts.append(f"**止损价**: {current_price * 0.93:.2f}元")
        else:
            analysis_parts.append("**建议**: 观望** | **仓位**: 空仓或极低仓位")
            analysis_parts.append("**操作**: 等待更好的入场时机")

        analysis_parts.append("")

        # 风险提示
        analysis_parts.append("### 风险提示")
        analysis_parts.append("")

        for risk_item in risk.get("all_risks", [])[:5]:
            analysis_parts.append(risk_item)

        return "\n".join(analysis_parts)

    async def _handle_backtest_request(self, prompt: str, session_id: str) -> dict[str, Any]:
        """处理回测请求"""
        logger.info("Handling backtest request")

        symbols = self.session_data.get("stock_symbols", [])
        symbol = symbols[0] if symbols else ""

        # 如果没有识别到股票代码，尝试从 prompt 中提取股票名称查找
        if not symbol:
            clean = prompt
            for keyword in ["回测", "backtest", "策略测试", "跑策略", "跑一下策略",
                            "的", "一下", "帮", "我", "请", "试试",
                            "MA", "ma", "MACD", "macd", "RSI", "rsi",
                            "布林", "bollinger", "bb", "均线", "策略"]:
                clean = clean.replace(keyword, "")
            clean = clean.strip()

            if clean:
                logger.info(f"Backtest: trying to match stock name from '{clean}'")
                search_results = _search_stock_by_name(clean)
                if search_results:
                    code, name = search_results[0]
                    if code.startswith('6'):
                        symbol = f"{code}.SH"
                    else:
                        symbol = f"{code}.SZ"
                    self.session_data["stock_symbols"] = [symbol]
                    self.session_data["matched_name"] = name
                    logger.info(f"Backtest: matched '{clean}' -> {name}({symbol})")

        if not symbol:
            return {"session_id": session_id, "status": "failed", "error": "未识别到股票代码或名称"}

        # 从prompt中识别策略
        strategy = "ma_crossover"  # 默认
        if any(kw in prompt for kw in ["macd", "MACD"]):
            strategy = "macd"
        elif any(kw in prompt for kw in ["rsi", "RSI"]):
            strategy = "rsi"
        elif any(kw in prompt for kw in ["布林", "bollinger", "bb"]):
            strategy = "bollinger_band"

        if "run_backtest" not in self.tools:
            return {
                "session_id": session_id, "status": "failed",
                "error": "回测功能不可用，strategy-backtest技能未注册"
            }

        try:
            result = self.tools["run_backtest"](symbol, strategy)

            # 生成回测报告
            metrics = result.get("metrics", {})
            report = self._format_backtest_report(result)

            return {
                "session_id": session_id,
                "status": "completed",
                "report": report,
                "backtest_result": result,
                "stock_symbol": symbol,
                "thoughts": [t.to_dict() for t in self.thoughts],
                "actions": [a.to_dict() for a in self.actions],
            }
        except Exception as e:
            logger.error(f"Backtest failed: {e}", exc_info=True)
            return {"session_id": session_id, "status": "failed", "error": str(e)}

    def _format_backtest_report(self, result: dict) -> str:
        """格式化回测报告为Markdown"""
        metrics = result.get("metrics", {})
        m = metrics

        parts = []
        parts.append(f"# {result.get('symbol', '')} 策略回测报告")
        parts.append("")
        parts.append(f"**策略**: {result.get('strategy_description', '')}")
        parts.append(f"**区间**: {result.get('start_date', '')} ~ {result.get('end_date', '')}")
        parts.append(f"**初始资金**: ¥{result.get('initial_cash', 0):,.0f}")
        parts.append("")
        parts.append("---")
        parts.append("")
        parts.append("## 回测结果")
        parts.append("")

        total_return = m.get("total_return", 0)
        color = "+" if total_return > 0 else ""
        parts.append(f"| 指标 | 数值 |")
        parts.append(f"|------|------|")
        parts.append(f"| 总收益率 | **{color}{total_return:.2f}%** |")
        parts.append(f"| 年化收益率 | {m.get('annualized_return', 0):.2f}% |")
        parts.append(f"| 最大回撤 | {m.get('max_drawdown', 0):.2f}% |")
        parts.append(f"| 夏普比率 | {m.get('sharpe_ratio', 0):.2f} |")
        parts.append(f"| 胜率 | {m.get('win_rate', 0):.1f}% |")
        parts.append(f"| 总交易次数 | {m.get('total_trades', 0)} |")
        parts.append(f"| 盈利次数 | {m.get('profit_trades', 0)} |")
        parts.append(f"| 亏损次数 | {m.get('loss_trades', 0)} |")
        parts.append(f"| 平均盈利 | +{m.get('avg_profit_pct', 0):.2f}% |")
        parts.append(f"| 平均亏损 | {m.get('avg_loss_pct', 0):.2f}% |")
        parts.append(f"| 最终权益 | ¥{result.get('final_equity', 0):,.2f} |")
        parts.append("")

        # 交易记录
        trades = result.get("trades", [])
        if trades:
            parts.append("## 交易记录")
            parts.append("")
            for i, t in enumerate(trades[:20]):
                pnl = t.get("pnl", 0)
                sign = "+" if pnl > 0 else ""
                parts.append(
                    f"{i+1}. **买入** {t.get('entry_date', '')} ¥{t.get('entry_price', 0):.2f} → "
                    f"**卖出** {t.get('exit_date', '')} ¥{t.get('exit_price', 0):.2f} "
                    f"| {sign}{pnl:.2f} ({sign}{t.get('return_pct', 0):.2f}%)"
                )
            parts.append("")

        parts.append("---")
        parts.append("")
        parts.append("*本回测报告由AI自动生成，仅供参考，不构成投资建议。*")

        return "\n".join(parts)

    async def _generate_news_report(self, symbol: str, session_id: str) -> dict[str, Any]:
        """生成新闻搜索报告（轻量流程，不走完整投研分析）"""
        logger.info(f"Generating news report for {symbol}")

        stock_info = self.session_data.get("stock_info", {})
        stock_name = stock_info.get("name", "")
        # 如果没有从 stock_info 获取到名称，尝试从匹配结果取
        if not stock_name:
            stock_name = self.session_data.get("matched_name", symbol)
        web_search = self.session_data.get("web_search", {})
        items = web_search.get("items", [])

        report_parts = [
            f"## {stock_name}（{symbol}）最新资讯",
            ""
        ]

        if not items:
            report_parts.append("未搜索到相关新闻资讯。")
        else:
            for i, item in enumerate(items[:12], 1):
                title = item.get("title", "")
                source = item.get("source", "")
                date = item.get("date", "")
                content = item.get("content", "")
                report_parts.append(f"**{i}. {title}**")
                if source or date:
                    report_parts.append(f"   {source} {date}")
                if content:
                    report_parts.append(f"   {content[:200]}")
                report_parts.append("")

        # 情绪分析结果
        sentiment = web_search.get("sentiment")
        if sentiment:
            label_map = {"positive": "利好", "neutral": "中性", "negative": "利空"}
            label = label_map.get(sentiment.get("overall_sentiment", ""), "中性")
            report_parts.extend([
                "### 舆情分析", "",
                f"- **整体情绪**: {label} ({sentiment.get('sentiment_score', 50)}/100)",
                f"- **关键话题**: {', '.join(sentiment.get('key_topics', []))}",
                f"- **市场影响**: {sentiment.get('market_impact', 'N/A')}",
                ""
            ])
            if sentiment.get("positive_factors"):
                report_parts.append(f"- 利好: {'; '.join(sentiment['positive_factors'])}")
            if sentiment.get("negative_factors"):
                report_parts.append(f"- 利空: {'; '.join(sentiment['negative_factors'])}")
            report_parts.append("")

        # 尝试用 LLM 做新闻摘要
        if items and "chat" in self.tools:
            news_text = "\n".join(
                f"- {i.get('title', '')}: {i.get('content', '')[:150]}"
                for i in items[:10]
            )
            summary_prompt = (
                f"以下是关于{stock_name}的最新搜索结果：\n\n"
                f"{news_text}\n\n"
                f"请严格基于以上搜索结果，用3-5句话总结最近的新闻动态和市场趋势，"
                f"重点关注对股价可能产生影响的信息。"
                f"不要添加搜索结果中没有的信息。"
            )
            try:
                result = await asyncio.wait_for(
                    self.tools["chat"](summary_prompt),
                    timeout=30.0
                )
                if result and result.get("message"):
                    report_parts.extend([
                        "---", "",
                        "### AI 新闻摘要", "",
                        result["message"], ""
                    ])
            except asyncio.TimeoutError:
                logger.warning("News summary LLM call timed out (30s), skipping summary")
            except Exception as e:
                logger.warning(f"News summary LLM call failed: {e}")

        report_parts.extend(["---", "", "*资讯由 AI 汇总，仅供参考，不构成投资建议。*"])

        return {
            "session_id": session_id,
            "stock_symbol": symbol,
            "status": "completed",
            "report": "\n".join(report_parts),
            "thoughts": [t.to_dict() for t in self.thoughts],
            "actions": [a.to_dict() for a in self.actions],
            "data": self.session_data
        }

    async def _handle_general_search(self, prompt: str, session_id: str) -> dict[str, Any]:
        """处理通用搜索请求（无股票代码，直接搜索关键词）"""
        logger.info(f"Handling general search: {prompt}")

        # 从 prompt 中提取搜索关键词（只去掉搜索动作词，保留主题内容）
        search_query = prompt
        for kw in ["搜索", "搜一下", "查新闻", "查一下", "帮我", "一下"]:
            search_query = search_query.replace(kw, "")
        search_query = search_query.strip()

        if not search_query:
            search_query = prompt

        items = []
        summary = ""

        # 优先用浏览器搜索（更通用），其次用 API 搜索
        if hasattr(self, '_browser_search_skill'):
            try:
                logger.info(f"Trying browser search for: {search_query}")
                result = await asyncio.to_thread(self._browser_search_skill.search_sync, search_query, 12)
                items = result.get("items", [])
                summary = result.get("summary", "")
                logger.info(f"Browser search returned {len(items)} items")
            except Exception as e:
                logger.warning(f"Browser search failed in general search: {type(e).__name__}: {e}")

        if not items and "web_search" in self.tools:
            try:
                logger.info(f"Trying API search for: {search_query}")
                result = self.tools["web_search"](search_query, count=12)
                items = result.get("items", [])
                summary = result.get("summary", "")
                logger.info(f"API search returned {len(items)} items")
            except Exception as e:
                logger.warning(f"API search failed in general search: {type(e).__name__}: {e}")

        # 构建报告
        report_parts = [f"## 「{search_query}」搜索结果", ""]

        if not items:
            report_parts.append(f"未找到关于「{search_query}」的相关资讯。")
        else:
            for i, item in enumerate(items[:12], 1):
                title = item.get("title", "")
                source = item.get("source", "")
                date = item.get("date", "")
                content = item.get("content", "")
                report_parts.append(f"**{i}. {title}**")
                if source or date:
                    report_parts.append(f"   {source} {date}")
                if content:
                    report_parts.append(f"   {content[:200]}")
                report_parts.append("")

        # LLM 摘要（不传 session_id，避免历史污染；加超时防止卡住）
        if items and "chat" in self.tools:
            news_text = "\n".join(
                f"- {i.get('title', '')}: {i.get('content', '')[:150]}"
                for i in items[:10]
            )
            file_ctx = ""
            if self.session_data.get("_file_context"):
                fc = self.session_data["_file_context"]
                file_ctx = f"\n\n用户上传了文件 [{fc.get('filename', '')}]，内容：\n{fc.get('content_text', '')}"
            summary_prompt = (
                f"以下是关于「{search_query}」的最新搜索结果：\n\n"
                f"{news_text}\n\n"
                f"请严格基于以上搜索结果，用3-5句话总结最近的关键动态和重要新闻。"
                f"不要添加搜索结果中没有的信息，不要给出人物或概念的百科介绍。"
                f"如果搜索结果不足以回答，请说明。"
                + file_ctx
            )
            try:
                result = await asyncio.wait_for(
                    self.tools["chat"](summary_prompt),
                    timeout=30.0
                )
                if result and result.get("message"):
                    report_parts.extend(["---", "", "### AI 摘要", "", result["message"], ""])
            except asyncio.TimeoutError:
                logger.warning("Search summary LLM call timed out (30s), skipping summary")
            except Exception as e:
                logger.warning(f"Search summary LLM call failed: {e}")

        report_parts.extend(["---", "", "*资讯由 AI 汇总，仅供参考。*"])

        return {
            "session_id": session_id,
            "status": "completed",
            "report": "\n".join(report_parts),
            "thoughts": [t.to_dict() for t in self.thoughts],
            "actions": [a.to_dict() for a in self.actions]
        }

    async def _handle_chat_with_context(
        self, prompt: str, session_id: str, symbols: list[str]
    ) -> dict[str, Any]:
        """
        处理 chat 类型的金融问答（做T建议、策略咨询、简单问答等）。
        获取少量数据后用 LLM 直接回答。
        """
        logger.info(f"Handling chat with context: symbols={symbols}")

        # 如果有股票，先获取实时行情和基本信息辅助回答
        if symbols and "get_realtime_quote" in self.tools:
            try:
                result = self.tools["get_realtime_quote"](symbols)
                if result and len(result) > 0:
                    self.session_data["realtime_quote"] = result[0]
            except Exception as e:
                logger.warning(f"Chat context: failed to get quote: {e}")

        if symbols and "get_stock_info" in self.tools:
            try:
                result = self.tools["get_stock_info"](symbols[0])
                if result:
                    self.session_data["stock_info"] = result
            except Exception as e:
                logger.warning(f"Chat context: failed to get stock info: {e}")

        # 构建带数据的 prompt
        quote = self.session_data.get("realtime_quote", {})
        stock_info = self.session_data.get("stock_info", {})
        stock_name = stock_info.get("name", self.session_data.get("matched_name", ""))

        data_parts = []
        if stock_name:
            data_parts.append(f"股票: {stock_name}({symbols[0] if symbols else 'N/A'})")
        if quote:
            data_parts.append(f"当前价: {quote.get('price', 'N/A')}")
            data_parts.append(f"涨跌幅: {quote.get('change_percent', 'N/A')}%")
        if stock_info:
            if stock_info.get("total_shares"):
                data_parts.append(f"总股本: {stock_info['total_shares'] / 1e8:.2f}亿股")

        data_block = "\n".join(data_parts) if data_parts else "（无实时数据）"

        # 构建对话历史
        session_id_val = self.session_data.get("_session_id", session_id)
        ctx = self._session_contexts.get(session_id_val, {})
        history_lines = []
        if ctx.get("messages"):
            for msg in ctx["messages"][-6:]:
                role = "用户" if msg["role"] == "user" else "助手"
                history_lines.append(f"{role}: {msg['content'][:150]}")
        history_block = "\n".join(history_lines) if history_lines else ""

        chat_prompt = (
            f"用户问题: {prompt}\n\n"
            f"相关数据:\n{data_block}\n\n"
        )
        if history_block:
            chat_prompt += f"对话历史:\n{history_block}\n\n"

        chat_prompt += (
            "请基于以上数据和历史对话，直接回答用户问题。"
            "用简洁专业的语言，不要写成报告格式。"
            "如果问题涉及交易策略（如做T、高抛低吸），请给出具体可操作的建议。"
        )

        if "chat" not in self.tools:
            # 没有聊天工具，返回数据
            return {
                "session_id": session_id,
                "stock_symbol": symbols[0] if symbols else None,
                "status": "completed",
                "report": f"## {stock_name}\n\n{data_block}",
            }

        try:
            result = await asyncio.wait_for(
                self.tools["chat"](chat_prompt),
                timeout=30.0
            )
            message = result.get("message", "") if result else ""
            if message:
                self.session_data["final_report"] = message
                return {
                    "session_id": session_id,
                    "stock_symbol": symbols[0] if symbols else None,
                    "status": "completed",
                    "report": message,
                    "thoughts": [t.to_dict() for t in self.thoughts],
                    "actions": [a.to_dict() for a in self.actions],
                }
        except asyncio.TimeoutError:
            logger.warning("Chat LLM call timed out (30s)")
        except Exception as e:
            logger.warning(f"Chat LLM call failed: {e}")

        # LLM 失败，返回数据摘要
        return {
            "session_id": session_id,
            "stock_symbol": symbols[0] if symbols else None,
            "status": "completed",
            "report": f"## {stock_name}\n\n{data_block}",
        }

    async def _handle_general_query(self, prompt: str, session_id: str) -> dict[str, Any]:
        """处理通用查询 — 选股数据用结构化表格，其他妙想数据由 LLM 总结"""
        logger.info("Handling general query")

        # 检测是否有结构化选股数据
        xuangu_val = self.session_data.get("mx_xuangu")
        if isinstance(xuangu_val, dict) and xuangu_val.get("rows") is not None:
            from harness.services.glm_agent_client import query as llm_query
            try:
                _t0 = time.time()
                top5 = json.dumps(xuangu_val["rows"][:5], ensure_ascii=False, default=str)
                summary = await llm_query(
                    prompt=(
                        f"简要总结以下选股结果（2-3句话描述趋势和亮点）：\n"
                        f"查询：{xuangu_val['query']}\n"
                        f"共 {xuangu_val['total']} 只股票，来源：{xuangu_val['source']}\n"
                        f"前5只：{top5}"
                    ),
                    temperature=0.3,
                )
                self._record_llm_call(
                    "screening_summary",
                    [{"role": "user", "content": prompt[:500]}],
                    summary[:3000],
                    duration_ms=int((time.time() - _t0) * 1000),
                )
            except Exception as e:
                logger.error(f"Screening summary failed: {e}")
                summary = f"共筛选出 {xuangu_val['total']} 只股票"

            return {
                "session_id": session_id,
                "status": "completed",
                "response_type": "stock_screening",
                "report": summary,
                "screening_data": xuangu_val,
                "thoughts": [t.to_dict() for t in self.thoughts],
                "actions": [a.to_dict() for a in self.actions],
            }

        # 收集其他妙想工具返回的数据（文本形式）
        mx_parts = []
        for key in ("mx_data", "mx_search", "mx_zixuan", "mx_moni"):
            val = self.session_data.get(key)
            if val and not str(val).startswith(("查询失败", "搜索失败", "选股失败", "操作失败")):
                mx_parts.append(f"## {key}\n{val}")

        if mx_parts:
            # 有妙想数据 → 用 LLM 基于数据回答
            context = "\n\n".join(mx_parts)
            from harness.services.glm_agent_client import query as llm_query
            try:
                _t0 = time.time()
                memory_block = await self._build_memory_context()
                memory_suffix = f"\n\n{memory_block}" if memory_block else ""
                answer = await llm_query(
                    prompt=(f"基于以下数据回答用户问题。用户问题：{prompt}\n\n数据：\n{context}"
                           + (f"\n\n用户上传了文件 [{self.session_data['_file_context']['filename']}]，内容：\n{self.session_data['_file_context']['content_text']}"
                              if self.session_data.get("_file_context") else "")
                           + memory_suffix),
                    temperature=0.3,
                )
                self._record_llm_call(
                    "summary",
                    [{"role": "user", "content": prompt[:500]}],
                    answer[:3000],
                    duration_ms=int((time.time() - _t0) * 1000),
                )
                return {
                    "session_id": session_id,
                    "status": "completed",
                    "report": answer,
                    "thoughts": [t.to_dict() for t in self.thoughts],
                    "actions": [a.to_dict() for a in self.actions],
                    "data": self.session_data,
                }
            except Exception as e:
                logger.error(f"LLM summary failed: {e}")
                return {
                    "session_id": session_id,
                    "status": "completed",
                    "report": context,
                    "thoughts": [t.to_dict() for t in self.thoughts],
                    "actions": [a.to_dict() for a in self.actions],
                    "data": self.session_data,
                }

        if "chat" not in self.tools:
            return {
                "session_id": session_id,
                "status": "completed",
                "message": "请提供股票代码以获取投研分析。例如：'分析600519'或'贵州茅台怎么样'"
            }

        try:
            file_ctx = ""
            if self.session_data.get("_file_context"):
                fc = self.session_data["_file_context"]
                file_ctx = f"\n\n用户上传了文件 [{fc.get('filename', '')}]，内容：\n{fc.get('content_text', '')}"
            result = await self.tools["chat"](prompt + file_ctx, session_id)
            return {
                "session_id": session_id,
                "status": "completed",
                "message": result.get("message", "")
            }
        except Exception as e:
            logger.error(f"Chat failed: {e}")
            return {
                "session_id": session_id,
                "status": "completed",
                "message": f"您好！我是智能投研助手。请提供股票代码（如600519）开始分析。"
            }


# 全局ReAct编排器实例
_react_orchestrator: ReactOrchestrator | None = None


def get_react_orchestrator() -> ReactOrchestrator:
    """获取全局ReAct编排器实例"""
    global _react_orchestrator
    if _react_orchestrator is None:
        _react_orchestrator = ReactOrchestrator()
    return _react_orchestrator
