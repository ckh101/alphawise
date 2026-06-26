"""
技能注册表模块
负责技能的注册、发现和管理
"""

from pathlib import Path
from typing import Any, Callable

from harness.core.logger import get_logger

logger = get_logger(__name__)


class SkillRegistry:
    """
    技能注册表

    管理所有可用技能的注册、发现和实例化
    """

    def __init__(self):
        self._skills: dict[str, type] = {}
        self._instances: dict[str, Any] = {}
        self._metadata: dict[str, dict[str, Any]] = {}
        logger.info("SkillRegistry initialized")

    def register(
        self,
        name: str,
        skill_class: type,
        metadata: dict[str, Any] | None = None
    ) -> None:
        """
        注册技能类

        Args:
            name: 技能名称
            skill_class: 技能类
            metadata: 技能元数据
        """
        self._skills[name] = skill_class
        # 确保元数据至少包含 name 字段
        if not metadata or not metadata.get("name"):
            metadata = {
                "name": name,
                "version": "0.1.0",
                "description": f"{name} skill"
            }
        self._metadata[name] = metadata
        logger.info(f"Skill registered: {name} -> {skill_class.__name__}, metadata={metadata}")

    def register_factory(
        self,
        name: str,
        factory: Callable[[], Any],
        metadata: dict[str, Any] | None = None
    ) -> None:
        """
        注册技能工厂函数

        Args:
            name: 技能名称
            factory: 工厂函数
            metadata: 技能元数据
        """
        self._skills[name] = factory
        # 确保元数据至少包含 name 字段
        if not metadata or not metadata.get("name"):
            metadata = {
                "name": name,
                "version": "0.1.0",
                "description": f"{name} skill"
            }
        self._metadata[name] = metadata
        logger.info(f"Skill factory registered: {name}")

    def get(self, name: str) -> Any:
        """
        获取技能实例

        Args:
            name: 技能名称

        Returns:
            技能实例

        Raises:
            ValueError: 技能不存在
        """
        if name not in self._skills:
            raise ValueError(f"Skill not found: {name}")

        # 如果已有实例，直接返回
        if name in self._instances:
            return self._instances[name]

        # 创建新实例
        skill_class_or_factory = self._skills[name]

        if callable(skill_class_or_factory) and not isinstance(skill_class_or_factory, type):
            # 工厂函数
            instance = skill_class_or_factory()
        else:
            # 类
            instance = skill_class_or_factory()

        self._instances[name] = instance
        logger.debug(f"Skill instance created: {name}")
        return instance

    def list_skills(self) -> list[dict[str, Any]]:
        """
        列出所有已注册技能

        Returns:
            技能元数据列表
        """
        logger.debug(f"list_skills called: metadata keys={list(self._metadata.keys())}, values={list(self._metadata.values())}")
        return list(self._metadata.values())

    def get_skill_info(self, name: str) -> dict[str, Any]:
        """
        获取技能信息

        Args:
            name: 技能名称

        Returns:
            技能元数据

        Raises:
            ValueError: 技能不存在
        """
        if name not in self._metadata:
            raise ValueError(f"Skill not found: {name}")

        return self._metadata[name]

    def has_skill(self, name: str) -> bool:
        """
        检查技能是否存在

        Args:
            name: 技能名称

        Returns:
            是否存在
        """
        return name in self._skills

    def discover_skills(self, skills_dir: str | Path) -> int:
        """
        自动发现并注册技能

        Args:
            skills_dir: 技能目录路径

        Returns:
            发现的技能数量
        """
        skills_path = Path(skills_dir)
        if not skills_path.exists():
            logger.warning(f"Skills directory not found: {skills_path}")
            return 0

        count = 0

        # 遍历技能目录
        for skill_dir in skills_path.iterdir():
            if not skill_dir.is_dir():
                continue

            # 检查是否有SKILL.md
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            # 检查是否有skill.py
            skill_py = skill_dir / "skill.py"
            if not skill_py.exists():
                continue

            # 读取SKILL.md获取元数据
            try:
                metadata = self._parse_skill_metadata(skill_md)
                logger.debug(f"Parsed metadata for {skill_dir.name}: {metadata}")

                # 动态导入技能模块
                skill_name = skill_dir.name

                # 使用导入工具加载模块
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    f"skills.{skill_name}",
                    skill_py
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # 注册技能
                # 检查是否有主要的技能类或函数
                main_class = None
                for attr_name in ["TdxRealtimeQuoteSkill", "TdxKlineSkill", "TdxStockInfoSkill", "GlmChatSkill", "GlmAnalyzeSkill", "WebSearchSkill", "StrategyBacktestSkill", "BrowserSearchSkill"]:
                    if hasattr(module, attr_name):
                        main_class = getattr(module, attr_name)
                        break

                if main_class:
                    self.register(skill_name, main_class, metadata)
                    count += 1
                else:
                    # 注册函数式技能
                    for func_name in ["get_realtime_quote", "get_kline", "analyze_stock", "generate_report", "chat", "analyze"]:
                        if hasattr(module, func_name):
                            self.register_factory(skill_name, lambda m=module, f=func_name: m, metadata)
                            count += 1
                            break

                logger.info(f"Auto-discovered skill: {skill_name}")

            except Exception as e:
                logger.error(f"Failed to load skill {skill_dir.name}: {e}")

        logger.info(f"Discovered {count} skills from {skills_path}")
        return count

    def _parse_skill_metadata(self, skill_md: Path) -> dict[str, Any]:
        """
        解析SKILL.md文件

        Args:
            skill_md: SKILL.md文件路径

        Returns:
            元数据字典
        """
        metadata = {}

        try:
            content = skill_md.read_text(encoding="utf-8")

            # 简单解析（生产环境应使用frontmatter解析器）
            for line in content.split("\n"):
                line = line.strip()
                # 尝试匹配中英文标签
                if line.startswith("- **名称**:") or line.startswith("- **name**:"):
                    metadata["name"] = line.split(":", 1)[1].strip()
                elif line.startswith("- **版本**:") or line.startswith("- **version**:"):
                    metadata["version"] = line.split(":", 1)[1].strip()
                elif line.startswith("- **描述**:") or line.startswith("- **description**:"):
                    metadata["description"] = line.split(":", 1)[1].strip()
                elif line.startswith("- **类别**:") or line.startswith("- **category**:"):
                    metadata["category"] = line.split(":", 1)[1].strip()
                elif line.startswith("- **作者**:") or line.startswith("- **author**:"):
                    metadata["author"] = line.split(":", 1)[1].strip()

        except Exception as e:
            logger.warning(f"Failed to parse skill metadata: {e}")

        return metadata


# 全局注册表实例
_registry: SkillRegistry | None = None


def get_registry() -> SkillRegistry:
    """
    获取全局注册表实例

    Returns:
        SkillRegistry实例
    """
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
    return _registry
