"""
Agent初始化模块
负责初始化Agent系统并注册所有技能
支持Claude Agent SDK编排器和ReAct编排器
"""

from pathlib import Path

from harness.agent.registry import get_registry
from harness.agent.orchestrator import get_orchestrator
from harness.agent.claude_orchestrator import get_claude_orchestrator
from harness.agent.react_orchestrator import get_react_orchestrator
from harness.core.logger import get_logger

logger = get_logger(__name__)


async def initialize_agent() -> None:
    """
    初始化Agent系统

    自动发现并注册所有技能，初始化所有编排器
    - 传统AgentOrchestrator
    - Claude Agent SDK编排器
    - ReAct编排器（新一代端到端智能投研Agent）
    """
    logger.info("Initializing Agent system...")

    registry = get_registry()
    orchestrator = get_orchestrator()
    claude_orchestrator = get_claude_orchestrator()
    react_orchestrator = get_react_orchestrator()

    # 获取技能目录
    project_root = Path(__file__).parent.parent.parent  # backend目录
    builtin_skills_dir = project_root / "skills" / "builtin"
    custom_skills_dir = project_root / "skills" / "custom"

    # 发现并注册内置技能
    logger.info("Discovering built-in skills...")
    builtin_count = registry.discover_skills(builtin_skills_dir)
    logger.info(f"Registered {builtin_count} built-in skills")

    # 发现并注册自定义技能
    if custom_skills_dir.exists():
        logger.info("Discovering custom skills...")
        custom_count = registry.discover_skills(custom_skills_dir)
        logger.info(f"Registered {custom_count} custom skills")

    # 将技能注册到所有编排器
    all_skills = registry.list_skills()
    logger.info(f"Total skills available: {len(all_skills)}")

    for skill_info in all_skills:
        skill_name = skill_info["name"]
        try:
            skill_instance = registry.get(skill_name)
            # 注册到原有编排器
            orchestrator.register_skill(skill_name, skill_instance)
            # 注册到Claude Agent SDK编排器
            claude_orchestrator.register_skill(skill_name, skill_instance)
            # 注册到ReAct编排器（新一代智能投研Agent）
            react_orchestrator.register_skill(skill_name, skill_instance)
            logger.info(f"Skill loaded into all orchestrators: {skill_name}")
        except Exception as e:
            logger.error(f"Failed to load skill {skill_name} into orchestrator: {e}")

    # 注册预定义工作流
    # 原有编排器
    orchestrator.register_workflow("stock_analysis", [
        "tdx-realtime-quote",
        "tdx-kline",
        "glm-analyze"
    ])

    orchestrator.register_workflow("investment_report", [
        "tdx-realtime-quote",
        "glm-analyze"
    ])

    orchestrator.register_workflow("chat_query", [
        "glm-chat"
    ])

    # Claude Agent SDK编排器
    claude_orchestrator.register_workflow("stock_analysis", [
        "tdx-realtime-quote",
        "tdx-kline",
        "glm-analyze"
    ])

    claude_orchestrator.register_workflow("investment_report", [
        "tdx-realtime-quote",
        "glm-analyze"
    ])

    claude_orchestrator.register_workflow("chat_query", [
        "glm-chat"
    ])

    logger.info("Agent system initialized successfully")
    logger.info("Available orchestrators: AgentOrchestrator, ClaudeAgentOrchestrator, ReactOrchestrator")


def get_available_skills() -> list[dict]:
    """
    获取所有可用技能列表

    Returns:
        技能列表
    """
    registry = get_registry()
    logger.debug(f"[get_available_skills] registry={id(registry)}, metadata_keys={list(registry._metadata.keys())}")
    skills = registry.list_skills()
    logger.debug(f"[get_available_skills] returning {len(skills)} skills")
    return skills


def get_claude_orchestrator_instance():
    """
    获取Claude Agent SDK编排器实例

    Returns:
        ClaudeAgentOrchestrator实例
    """
    return get_claude_orchestrator()
