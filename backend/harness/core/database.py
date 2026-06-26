"""
SQLite 数据库模块

使用 SQLAlchemy 管理 settings 表，存储大模型等可配置项。
配置只从数据库读取，不从 YAML 或环境变量导入。
"""

from datetime import datetime
from pathlib import Path

import json

from sqlalchemy import Column, String, DateTime, Integer, Float, Text, create_engine, desc, func, text
from sqlalchemy.orm import declarative_base, sessionmaker

from harness.core.logger import get_logger

logger = get_logger(__name__)

Base = declarative_base()


class Setting(Base):
    """配置项表"""
    __tablename__ = "settings"

    key = Column(String, primary_key=True)
    value = Column(String, default="")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AgentSession(Base):
    """智能体对话记录表"""
    __tablename__ = "agent_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), unique=True, nullable=False, index=True)
    prompt = Column(Text, nullable=False)
    response_mode = Column(String(32), default="chat")
    status = Column(String(16), default="completed")
    stock_symbol = Column(String(20), default="")
    stock_name = Column(String(64), default="")
    report = Column(Text, default="")
    thoughts = Column(Text, default="[]")
    actions = Column(Text, default="[]")
    tool_plan = Column(Text, default="[]")
    tool_calls_count = Column(Integer, default=0)
    duration = Column(Float, default=0.0)
    error_message = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    conversation_id = Column(String(64), index=True)
    turn_number = Column(Integer, default=1)


class SchedulerTask(Base):
    """定时任务表"""
    __tablename__ = "scheduler_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), default="")
    prompt = Column(Text, nullable=False)
    cron_expression = Column(String(64), default="")
    schedule_type = Column(String(16), default="weekly")
    schedule_config = Column(Text, default="{}")
    receive_id = Column(String(128), default="")
    receive_id_type = Column(String(16), default="chat_id")
    feishu_channel_id = Column(String(64), default="")
    use_channel_push_targets = Column(String(8), default="false")
    start_date = Column(String(10), default="")
    end_date = Column(String(10), default="")
    enabled = Column(String(8), default="true")
    last_run_at = Column(DateTime, nullable=True)
    last_run_status = Column(String(16), default="")
    last_run_session_id = Column(String(64), default="")
    run_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LlmCall(Base):
    """LLM 调用记录表"""
    __tablename__ = "llm_calls"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), index=True)
    phase = Column(String(64), default="")
    model = Column(String(64), default="")
    input_messages = Column(Text, default="")
    output_content = Column(Text, default="")
    duration_ms = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class WatchlistItem(Base):
    """自选股表"""
    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    name = Column(String(64), default="")
    group_name = Column(String(64), default="默认")
    sort_order = Column(Integer, default=0)
    notes = Column(String(256), default="")
    created_at = Column(DateTime, default=datetime.utcnow)


# 模块级状态
_engine = None
_Session = None


def _get_db_path() -> str:
    """获取数据库路径"""
    try:
        from harness.core.config import get_config
        db_path = get_config().storage.database_path
    except Exception:
        db_path = "./data/harness.db"

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return db_path


def init_db() -> None:
    """初始化数据库，创建表"""
    global _engine, _Session

    db_path = _get_db_path()
    _engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(_engine)
    _Session = sessionmaker(bind=_engine)

    _run_migrations(_engine)

    logger.info(f"Database initialized: {db_path}")


def _run_migrations(engine) -> None:
    """内联 Schema 迁移（SQLite 安全）"""
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(agent_sessions)"))
        columns = {row[1] for row in result}

        if "conversation_id" not in columns:
            conn.execute(text(
                "ALTER TABLE agent_sessions ADD COLUMN conversation_id VARCHAR(64)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_agent_sessions_conversation_id "
                "ON agent_sessions(conversation_id)"
            ))
            # 回填：旧数据 conversation_id = session_id
            conn.execute(text(
                "UPDATE agent_sessions SET conversation_id = session_id "
                "WHERE conversation_id IS NULL"
            ))

        if "turn_number" not in columns:
            conn.execute(text(
                "ALTER TABLE agent_sessions ADD COLUMN turn_number INTEGER DEFAULT 1"
            ))

        # scheduler_tasks 表
        result = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='scheduler_tasks'"
        ))
        if not result.fetchone():
            conn.execute(text("""
                CREATE TABLE scheduler_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(128) DEFAULT '',
                    prompt TEXT NOT NULL,
                    cron_expression VARCHAR(64) DEFAULT '',
                    schedule_type VARCHAR(16) DEFAULT 'weekly',
                    schedule_config TEXT DEFAULT '{}',
                    receive_id VARCHAR(128) DEFAULT '',
                    receive_id_type VARCHAR(16) DEFAULT 'chat_id',
                    feishu_channel_id VARCHAR(64) DEFAULT '',
                    start_date VARCHAR(10) DEFAULT '',
                    end_date VARCHAR(10) DEFAULT '',
                    enabled VARCHAR(8) DEFAULT 'true',
                    last_run_at DATETIME,
                    last_run_status VARCHAR(16) DEFAULT '',
                    last_run_session_id VARCHAR(64) DEFAULT '',
                    run_count INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """))

        # scheduler_tasks: 新增 use_channel_push_targets 列
        result = conn.execute(text("PRAGMA table_info(scheduler_tasks)"))
        columns = {row[1] for row in result}
        if "use_channel_push_targets" not in columns:
            conn.execute(text(
                "ALTER TABLE scheduler_tasks ADD COLUMN use_channel_push_targets VARCHAR(8) DEFAULT 'false'"
            ))

        # watchlist 表
        result = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='watchlist'"
        ))
        if not result.fetchone():
            conn.execute(text("""
                CREATE TABLE watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol VARCHAR(20) NOT NULL,
                    name VARCHAR(64) DEFAULT '',
                    group_name VARCHAR(64) DEFAULT '默认',
                    sort_order INTEGER DEFAULT 0,
                    notes VARCHAR(256) DEFAULT '',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_watchlist_symbol ON watchlist(symbol)"
            ))

        conn.commit()
        logger.info("Database migrations applied")


def get_session():
    """获取数据库会话"""
    if _Session is None:
        init_db()
    return _Session()


def get_setting(key: str, default: str | None = None) -> str | None:
    """读取单个配置"""
    session = get_session()
    try:
        row = session.query(Setting).filter_by(key=key).first()
        if row and row.value:
            return row.value
        return default
    finally:
        session.close()


def set_setting(key: str, value: str) -> None:
    """写入单个配置"""
    session = get_session()
    try:
        row = session.query(Setting).filter_by(key=key).first()
        if row:
            row.value = value
            row.updated_at = datetime.utcnow()
        else:
            session.add(Setting(key=key, value=value))
        session.commit()
    finally:
        session.close()


def get_all_settings(prefix: str = "") -> dict[str, str]:
    """按前缀读取一组配置"""
    session = get_session()
    try:
        query = session.query(Setting)
        if prefix:
            query = query.filter(Setting.key.like(f"{prefix}%"))
        return {row.key: row.value for row in query.all()}
    finally:
        session.close()


def update_settings(settings: dict[str, str]) -> None:
    """批量更新配置"""
    session = get_session()
    try:
        for key, value in settings.items():
            row = session.query(Setting).filter_by(key=key).first()
            if row:
                row.value = value
                row.updated_at = datetime.utcnow()
            else:
                session.add(Setting(key=key, value=value))
        session.commit()
    finally:
        session.close()


def get_llm_config() -> dict[str, str]:
    """
    获取大模型配置，只从 active provider 读取。
    无 provider 时返回空 dict。
    """
    provider = get_active_llm_provider()
    if provider:
        return {
            "llm.api_key": provider.get("api_key", ""),
            "llm.base_url": provider.get("base_url", ""),
            "llm.model": provider.get("model", ""),
            "llm.timeout": str(provider.get("timeout", 600)),
        }
    return {}


def is_llm_configured() -> bool:
    """检查大模型是否已配置（至少有 api_key 和 model）"""
    config = get_llm_config()
    return bool(config.get("llm.api_key") and config.get("llm.model"))


# ---------------------------------------------------------------------------
# 多厂商 Provider 管理
# ---------------------------------------------------------------------------

def get_llm_providers() -> list[dict]:
    """获取所有已配置的 LLM provider 列表"""
    import json
    raw = get_setting("llm.providers", "[]")
    try:
        providers = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        providers = []
    if not isinstance(providers, list):
        providers = []
    return providers


def save_llm_providers(providers: list[dict]) -> None:
    """保存完整的 provider 列表"""
    import json
    set_setting("llm.providers", json.dumps(providers, ensure_ascii=False))


def get_active_llm_provider() -> dict | None:
    """返回当前激活的 provider 完整配置，无则返回 None"""
    providers = get_llm_providers()
    active_id = get_setting("llm.active", "")
    if not active_id or not providers:
        return None
    for p in providers:
        if p.get("id") == active_id:
            return p
    # active_id 无效，取第一个
    return providers[0] if providers else None


def set_active_llm_provider(provider_id: str) -> None:
    """切换激活的 provider"""
    set_setting("llm.active", provider_id)


def get_sdk_config() -> dict:
    """
    返回 ClaudeAgentOptions 所需的公共 env + setting_sources。

    - env: 注入 provider 的 key/url，同时清除继承的本地 CLI 环境变量
    - setting_sources: 只用 ["project"]，让 CLI 扫描 cwd/.claude/skills/ 发现 skill，
      但不读 user 级 settings（避免 ~/.claude/settings.json 中的 env 覆盖 provider 配置）
    - 不返回 model，由调用方通过参数传入
    """
    provider = get_active_llm_provider()
    if not provider:
        return {}

    return {
        "env": {
            "ANTHROPIC_BASE_URL": provider.get("base_url", ""),
            "ANTHROPIC_API_KEY": provider.get("api_key", ""),
            "ANTHROPIC_AUTH_TOKEN": provider.get("api_key", ""),
            # 清除本地 CLI 遗留的环境变量，防止干扰
            "ANTHROPIC_MODEL": "",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "",
            "ANTHROPIC_REASONING_MODEL": "",
        },
        "setting_sources": ["project"],
    }


def save_agent_session(data: dict) -> int:
    """保存一次 Agent 对话记录，返回自增 ID"""
    session = get_session()
    try:
        # 序列化 actions 时精简大体积的 result 字段
        actions_raw = data.get("actions", [])
        actions_clean = []
        for a in actions_raw:
            item = {
                "tool_name": a.get("tool_name", ""),
                "arguments": a.get("arguments", {}),
                "timestamp": a.get("timestamp", ""),
                "execution_time": a.get("execution_time", 0),
            }
            if a.get("error"):
                item["error"] = a["error"]
            # result 只保留前 500 字符
            result = a.get("result")
            if result is not None:
                result_str = json.dumps(result, ensure_ascii=False, default=str)
                item["result_summary"] = result_str[:500]
            actions_clean.append(item)

        thoughts_raw = data.get("thoughts", [])

        row = AgentSession(
            session_id=data["session_id"],
            conversation_id=data.get("conversation_id") or data["session_id"],
            turn_number=data.get("turn_number", 1),
            prompt=data.get("prompt", ""),
            response_mode=data.get("response_mode", "chat"),
            status=data.get("status", "completed"),
            stock_symbol=data.get("stock_symbol", ""),
            stock_name=data.get("stock_name", ""),
            report=(data.get("report") or "")[:2000],
            thoughts=json.dumps(thoughts_raw, ensure_ascii=False, default=str),
            actions=json.dumps(actions_clean, ensure_ascii=False, default=str),
            tool_plan=json.dumps(data.get("tool_plan", []), ensure_ascii=False, default=str),
            tool_calls_count=len(actions_raw),
            duration=data.get("duration", 0.0),
            error_message=data.get("error_message", ""),
        )
        session.add(row)
        session.commit()
        logger.info(f"Agent session saved: id={row.id}, session_id={data['session_id']}")
        return row.id
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to save agent session: {e}")
        return -1
    finally:
        session.close()


def save_llm_call(data: dict) -> None:
    """保存一条 LLM 调用记录"""
    session = get_session()
    try:
        row = LlmCall(
            session_id=data.get("session_id", ""),
            phase=data.get("phase", ""),
            model=data.get("model", ""),
            input_messages=(data.get("input_messages") or "")[:10000],
            output_content=(data.get("output_content") or "")[:5000],
            duration_ms=data.get("duration_ms", 0),
        )
        session.add(row)
        session.commit()
    except Exception as e:
        session.rollback()
        logger.warning(f"Failed to save LLM call: {e}")
    finally:
        session.close()


# ===== SDK Skills 配置 =====

BUILTIN_SKILL_PREFIXES = ("mx-",)


def is_builtin_skill(name: str) -> bool:
    """判断是否为内置 skill"""
    return any(name.startswith(p) for p in BUILTIN_SKILL_PREFIXES)


def get_disabled_sdk_skills() -> list[str]:
    """获取被禁用的 SDK skill 名称列表"""
    all_settings = get_all_settings("skill.sdk.")
    disabled = []
    for key, value in all_settings.items():
        if value.lower() == "false":
            disabled.append(key.replace("skill.sdk.", ""))
    return disabled


def set_skill_enabled(name: str, enabled: bool) -> None:
    """设置 SDK skill 启用/禁用状态"""
    set_setting(f"skill.sdk.{name}", str(enabled).lower())


def get_mcp_configs() -> list[dict]:
    """获取 MCP 服务配置列表"""
    raw = get_setting("mcp.servers")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def set_mcp_configs(configs: list[dict]) -> None:
    """保存 MCP 服务配置列表"""
    set_setting("mcp.servers", json.dumps(configs, ensure_ascii=False))
