"""
Memory System — 三级记忆（短期/长期/人设）

从用户对话中提取关键信息，存储为本地 MD 文件。
Agent 在构建 prompt 时自动加载记忆上下文。
"""

import asyncio
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from harness.core.logger import get_logger

logger = get_logger(__name__)


class MemoryManager:
    """单例记忆管理器"""

    _instance: "MemoryManager | None" = None

    SHORT_TERM_MAX_DAYS = 7
    LONG_TERM_MAX_DAYS = 90

    SHORT_TERM_MAX_CHARS = 800
    LONG_TERM_MAX_CHARS = 600
    PERSONA_MAX_CHARS = 400

    def __init__(self):
        self._memory_dir = self._resolve_memory_dir()
        self._short_term_dir = self._memory_dir / "short_term"
        self._long_term_dir = self._memory_dir / "long_term"
        self._persona_dir = self._memory_dir / "persona"

        for d in (self._short_term_dir, self._long_term_dir, self._persona_dir):
            d.mkdir(parents=True, exist_ok=True)

        self._cache: dict[str, str] = {}
        self._cache_valid: dict[str, bool] = {}

    @classmethod
    def get_instance(cls) -> "MemoryManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _resolve_memory_dir(self) -> Path:
        # backend/harness/services/memory.py -> backend/data/memory/
        backend_dir = Path(__file__).parent.parent.parent
        return backend_dir / "data" / "memory"

    def _today_filename(self) -> str:
        return f"{datetime.now().strftime('%Y-%m-%d')}.md"

    # ---- 读取 ----

    async def load_relevant_memories(self, prompt: str, session_id: str = "") -> str:
        """加载三种记忆，返回格式化文本块"""
        try:
            parts = []

            persona = await self._load_persona()
            if persona:
                parts.append(f"## 用户画像\n{persona}")

            long_term = await self._load_long_term()
            if long_term:
                parts.append(f"## 长期记忆\n{long_term}")

            short_term = await self._load_short_term()
            if short_term:
                parts.append(f"## 近期对话\n{short_term}")

            if not parts:
                return ""

            return "\n\n".join(parts)

        except Exception as e:
            logger.warning(f"[Memory] Failed to load memories: {e}")
            return ""

    async def _load_short_term(self) -> str:
        cache_key = "short_term"
        if self._cache_valid.get(cache_key):
            return self._cache.get(cache_key, "")

        lines = []
        for i in range(self.SHORT_TERM_MAX_DAYS):
            dt = datetime.now() - timedelta(days=i)
            filepath = self._short_term_dir / f"{dt.strftime('%Y-%m-%d')}.md"
            if filepath.exists():
                content = await asyncio.to_thread(self._read_file, filepath)
                if content:
                    lines.append(content)

        result = "\n\n".join(lines)[-self.SHORT_TERM_MAX_CHARS:]
        self._cache[cache_key] = result
        self._cache_valid[cache_key] = True
        return result

    async def _load_long_term(self) -> str:
        cache_key = "long_term"
        if self._cache_valid.get(cache_key):
            return self._cache.get(cache_key, "")

        lines = []
        files = sorted(self._long_term_dir.glob("*.md"), reverse=True)
        for filepath in files[:14]:
            content = await asyncio.to_thread(self._read_file, filepath)
            if content:
                lines.append(content)

        result = "\n\n".join(lines)[-self.LONG_TERM_MAX_CHARS:]
        self._cache[cache_key] = result
        self._cache_valid[cache_key] = True
        return result

    async def _load_persona(self) -> str:
        cache_key = "persona"
        if self._cache_valid.get(cache_key):
            return self._cache.get(cache_key, "")

        filepath = self._persona_dir / "profile.md"
        if not filepath.exists():
            self._cache[cache_key] = ""
            self._cache_valid[cache_key] = True
            return ""

        content = await asyncio.to_thread(self._read_file, filepath)
        result = (content or "")[-self.PERSONA_MAX_CHARS:]
        self._cache[cache_key] = result
        self._cache_valid[cache_key] = True
        return result

    # ---- 写入 ----

    async def extract_and_store(
        self,
        user_prompt: str,
        assistant_response: str,
        session_id: str,
        session_data: dict,
    ) -> None:
        """从对话中提取记忆并写入 MD 文件"""
        try:
            extraction = await self._llm_extract(user_prompt, assistant_response, session_data)
            if not extraction:
                return

            self._cache_valid.clear()

            if extraction.get("short_term"):
                await self._append_short_term(session_id, extraction["short_term"])

            if extraction.get("long_term"):
                await self._append_long_term(extraction["long_term"])

            if extraction.get("persona"):
                await self._update_persona(extraction["persona"])

            logger.info(f"[Memory] Extracted and stored for session {session_id}")

        except Exception as e:
            logger.warning(f"[Memory] Extraction failed (non-critical): {e}")

    async def _llm_extract(
        self,
        user_prompt: str,
        assistant_response: str,
        session_data: dict,
    ) -> dict[str, Any] | None:
        """调用 LLM 提取结构化记忆"""
        from harness.services.glm_agent_client import query as llm_query

        symbols = session_data.get("stock_symbols", [])
        domain = session_data.get("domain", "")

        extraction_prompt = f"""分析以下对话，提取值得记住的信息，分为三类。
只提取有长期价值的信息，不要提取临时性的数据查询结果（如具体价格、涨跌幅）。

用户输入: {user_prompt[:500]}
助手回复: {assistant_response[:500]}
涉及的股票: {symbols}
对话领域: {domain}

请严格按以下JSON格式返回（不要添加任何其他文本）:
```json
{{
  "short_term": "简要记录本次对话主题和关键信息（1-2句话）",
  "long_term": "提取值得长期记住的信息：投资偏好、关注领域、操作习惯、学习到的用户模式。如无则返回空字符串",
  "persona": "关于用户性格特征的观察：沟通风格、专业程度、风险偏好、兴趣偏好。仅当有新发现时才填写，否则返回空字符串"
}}
```

注意:
- short_term: 记录对话主题和关键决策点
- long_term: 只记录用户偏好和模式，不记录具体股票数据
- persona: 只记录新发现的性格特征，不是每次都要填写
- 如果某类没有值得记住的信息，返回空字符串"""

        try:
            raw = await asyncio.wait_for(
                llm_query(prompt=extraction_prompt, temperature=0.1),
                timeout=30.0,
            )
            return self._parse_extraction_json(raw)
        except asyncio.TimeoutError:
            logger.warning("[Memory] LLM extraction timed out (20s)")
            return None
        except Exception as e:
            logger.warning(f"[Memory] LLM extraction failed: {e}")
            return None

    def _parse_extraction_json(self, raw: str) -> dict[str, Any] | None:
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', raw)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_str = raw.strip()

        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            start = json_str.find('{')
            end = json_str.rfind('}')
            if start >= 0 and end > start:
                try:
                    return json.loads(json_str[start:end + 1])
                except json.JSONDecodeError:
                    return None
            return None

    async def _append_short_term(self, session_id: str, content: str) -> None:
        filepath = self._short_term_dir / self._today_filename()
        timestamp = datetime.now().strftime("%H:%M")
        if not filepath.exists():
            header = f"# {datetime.now().strftime('%Y-%m-%d')} 对话记录\n"
            await asyncio.to_thread(self._write_file, filepath, header)
        entry = f"\n## {timestamp} {session_id}\n{content}\n"
        await asyncio.to_thread(self._append_file, filepath, entry)

    async def _append_long_term(self, content: str) -> None:
        filepath = self._long_term_dir / self._today_filename()
        if not filepath.exists():
            header = f"# {datetime.now().strftime('%Y-%m-%d')} 长期记忆\n"
            await asyncio.to_thread(self._write_file, filepath, header)
        entry = f"\n{content}\n"
        await asyncio.to_thread(self._append_file, filepath, entry)

    async def _update_persona(self, content: str) -> None:
        filepath = self._persona_dir / "profile.md"
        if not filepath.exists():
            header = "# 用户画像\n"
            await asyncio.to_thread(self._write_file, filepath, header)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"\n## 更新于 {timestamp}\n{content}\n"
        await asyncio.to_thread(self._append_file, filepath, entry)

    # ---- 维护 ----

    async def prune_old_files(self) -> None:
        """清理过期记忆文件"""
        cutoff_short = datetime.now() - timedelta(days=self.SHORT_TERM_MAX_DAYS)
        cutoff_long = datetime.now() - timedelta(days=self.LONG_TERM_MAX_DAYS)

        for filepath in self._short_term_dir.glob("*.md"):
            try:
                file_date = datetime.strptime(filepath.stem, "%Y-%m-%d")
                if file_date < cutoff_short:
                    filepath.unlink()
                    logger.info(f"[Memory] Pruned short-term: {filepath.name}")
            except (ValueError, OSError):
                pass

        for filepath in self._long_term_dir.glob("*.md"):
            try:
                file_date = datetime.strptime(filepath.stem, "%Y-%m-%d")
                if file_date < cutoff_long:
                    filepath.unlink()
                    logger.info(f"[Memory] Pruned long-term: {filepath.name}")
            except (ValueError, OSError):
                pass

    # ---- 文件操作 ----

    @staticmethod
    def _read_file(filepath: Path) -> str:
        try:
            return filepath.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    @staticmethod
    def _append_file(filepath: Path, content: str) -> None:
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(content)

    @staticmethod
    def _write_file(filepath: Path, content: str) -> None:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)


def get_memory_manager() -> MemoryManager:
    return MemoryManager.get_instance()
