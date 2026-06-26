"""
Agent编排器模块
负责任务编排、技能调度和工作流管理
"""

from datetime import datetime
from typing import Any
import uuid

from harness.core.logger import get_logger

logger = get_logger(__name__)


class Task:
    """Agent任务类"""

    def __init__(
        self,
        task_id: str,
        task_type: str,
        input_data: dict[str, Any],
        priority: int = 0
    ):
        self.task_id = task_id
        self.task_type = task_type
        self.input_data = input_data
        self.priority = priority
        self.status = "pending"
        self.result = None
        self.error = None
        self.created_at = datetime.now()
        self.started_at = None
        self.completed_at = None

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "input_data": self.input_data,
            "priority": self.priority,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class AgentOrchestrator:
    """
    Agent编排器

    负责协调各个技能模块，处理复杂的多步骤任务
    """

    def __init__(self):
        self.tasks: dict[str, Task] = {}
        self.skills: dict[str, Any] = {}
        self.workflows: dict[str, list[str]] = {}
        logger.info("AgentOrchestrator initialized")

    def register_skill(self, name: str, skill: Any) -> None:
        """
        注册技能

        Args:
            name: 技能名称
            skill: 技能实例
        """
        self.skills[name] = skill
        logger.info(f"Skill registered: {name}")

    def register_workflow(self, name: str, steps: list[str]) -> None:
        """
        注册工作流

        Args:
            name: 工作流名称
            steps: 技能步骤列表
        """
        self.workflows[name] = steps
        logger.info(f"Workflow registered: {name} -> {steps}")

    def create_task(
        self,
        task_type: str,
        input_data: dict[str, Any],
        priority: int = 0
    ) -> str:
        """
        创建新任务

        Args:
            task_type: 任务类型
            input_data: 输入数据
            priority: 优先级

        Returns:
            任务ID
        """
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        task = Task(task_id, task_type, input_data, priority)
        self.tasks[task_id] = task
        logger.info(f"Task created: {task_id} type={task_type}")
        return task_id

    async def execute_task(self, task_id: str) -> dict[str, Any]:
        """
        执行任务

        Args:
            task_id: 任务ID

        Returns:
            执行结果
        """
        if task_id not in self.tasks:
            raise ValueError(f"Task not found: {task_id}")

        task = self.tasks[task_id]
        task.status = "running"
        task.started_at = datetime.now()

        logger.info(f"Executing task: {task_id} type={task.task_type}")

        try:
            # 根据任务类型执行相应的工作流
            if task.task_type == "stock_analysis":
                result = await self._execute_stock_analysis(task.input_data)
            elif task.task_type == "investment_report":
                result = await self._execute_investment_report(task.input_data)
            elif task.task_type == "chat_query":
                result = await self._execute_chat_query(task.input_data)
            else:
                raise ValueError(f"Unknown task type: {task.task_type}")

            task.result = result
            task.status = "completed"
            task.completed_at = datetime.now()

            logger.info(f"Task completed: {task_id}")

            return {
                "task_id": task_id,
                "status": "completed",
                "result": result
            }

        except Exception as e:
            logger.error(f"Task failed: {task_id}", error=str(e))
            task.status = "failed"
            task.error = str(e)
            task.completed_at = datetime.now()

            raise

    async def _execute_stock_analysis(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """
        执行股票分析工作流

        工作流: 1.获取实时行情 -> 2.获取K线数据 -> 3.GLM分析
        """
        symbol = input_data.get("symbol")

        logger.info(f"Executing stock analysis workflow for: {symbol}")
        logger.debug(f"Available skills: {list(self.skills.keys())}")

        # 1. 获取实时行情
        quote_skill = self.skills.get("tdx-realtime-quote")
        if not quote_skill:
            raise RuntimeError("tdx-realtime-quote skill not registered")

        quote_data = quote_skill.get_quote([symbol])

        # 2. 获取K线数据
        kline_skill = self.skills.get("tdx-kline")
        if not kline_skill:
            raise RuntimeError("tdx-kline skill not registered")

        kline_data = kline_skill.get_bars(symbol, "daily")

        # 3. GLM分析
        analyze_skill = self.skills.get("glm-analyze")
        if not analyze_skill:
            raise RuntimeError("glm-analyze skill not registered")

        analysis = await analyze_skill.analyze(symbol, "comprehensive")

        return {
            "symbol": symbol,
            "quote": quote_data[0] if quote_data else None,
            "kline": kline_data[:5],  # 返回最近5天
            "analysis": analysis
        }

    async def _execute_investment_report(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """
        执行投研报告生成工作流

        工作流: 1.获取多个股票行情 -> 2.批量分析 -> 3.生成报告
        """
        symbols = input_data.get("symbols", [])
        report_type = input_data.get("report_type", "daily")

        logger.info(f"Executing investment report workflow for: {symbols}")
        logger.debug(f"Available skills: {list(self.skills.keys())}")

        # 批量获取行情
        quote_skill = self.skills.get("tdx-realtime-quote")
        if not quote_skill:
            raise RuntimeError("tdx-realtime-quote skill not registered")

        quotes_data = quote_skill.get_quote(symbols)

        # 生成报告
        analyze_skill = self.skills.get("glm-analyze")
        if not analyze_skill:
            raise RuntimeError("glm-analyze skill not registered")

        report = await analyze_skill.report(symbols, report_type)

        return {
            "symbols": symbols,
            "quotes": quotes_data,
            "report": report
        }

    async def _execute_chat_query(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """
        执行聊天查询工作流

        工作流: 直接调用GLM聊天技能
        """
        message = input_data.get("message")
        session_id = input_data.get("session_id")

        logger.debug(f"Available skills: {list(self.skills.keys())}")

        chat_skill = self.skills.get("glm-chat")
        if not chat_skill:
            raise RuntimeError("glm-chat skill not registered")

        response = await chat_skill.chat(message, session_id)

        return {
            "message": response["message"],
            "session_id": response["session_id"]
        }

    def get_task_status(self, task_id: str) -> dict[str, Any]:
        """
        获取任务状态

        Args:
            task_id: 任务ID

        Returns:
            任务信息
        """
        if task_id not in self.tasks:
            raise ValueError(f"Task not found: {task_id}")

        return self.tasks[task_id].to_dict()

    def list_tasks(self, status: str | None = None) -> list[dict[str, Any]]:
        """
        列出所有任务

        Args:
            status: 过滤状态（可选）

        Returns:
            任务列表
        """
        tasks = list(self.tasks.values())

        if status:
            tasks = [t for t in tasks if t.status == status]

        return [task.to_dict() for task in tasks]


# 全局编排器实例
_orchestrator: AgentOrchestrator | None = None


def get_orchestrator() -> AgentOrchestrator:
    """
    获取全局编排器实例

    Returns:
        AgentOrchestrator实例
    """
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = AgentOrchestrator()
    return _orchestrator
