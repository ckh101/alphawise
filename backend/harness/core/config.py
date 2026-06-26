"""
配置管理模块

负责加载和管理应用配置，支持多环境配置文件。
"""

import os
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field

from harness.core.exceptions import ConfigError


class AppConfig(BaseModel):
    """应用基础配置"""
    name: str = "harness-investment-research"
    version: str = "0.1.0"
    environment: str = "development"
    debug: bool = False
    description: str = "Harness 投资研究平台"


class ServerConfig(BaseModel):
    """服务器配置"""
    host: str = "127.0.0.1"
    port: int = 8000
    workers: int = 1
    reload: bool = False


class LoggingConfig(BaseModel):
    """日志配置"""
    level: str = "INFO"
    console: bool = True
    file: bool = True
    rotation: str = "100 MB"
    retention: str = "10 days"
    format: str = (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
        "{name}:{function}:{line} | {message}"
    )


class TracingConfig(BaseModel):
    """请求追踪配置"""
    enabled: bool = True
    sample_rate: float = 1.0


class TdxServerConfig(BaseModel):
    """通达信服务器配置"""
    host: str
    port: int


class TdxConfig(BaseModel):
    """通达信配置"""
    servers: list[TdxServerConfig] = Field(default_factory=list)
    timeout: int = 5
    retry: int = 3


class ClaudeConfig(BaseModel):
    """Claude AI配置"""
    api_key: str = ""
    model: str = "claude-sonnet-4-20250514"
    timeout: int = 30
    max_retries: int = 3


class GlmConfig(BaseModel):
    """GLM API配置（LLM配置已迁移到数据库 settings 表）"""
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    timeout: int = 30
    max_retries: int = 3


class StorageConfig(BaseModel):
    """存储配置"""
    database_path: str = "./data/harness.db"
    data_dir: str = "./data"


class FeishuConfig(BaseModel):
    """飞书 Channel 配置"""
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    verification_token: str = ""
    encrypt_key: str = ""


class Config(BaseModel):
    """应用总配置"""
    app: AppConfig = Field(default_factory=AppConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    tracing: TracingConfig = Field(default_factory=TracingConfig)
    tdx: TdxConfig = Field(default_factory=TdxConfig)
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)
    glm: GlmConfig = Field(default_factory=GlmConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)

    @classmethod
    def from_yaml(cls, path: Path) -> "Config":
        """
        从YAML文件加载配置

        Args:
            path: YAML文件路径

        Returns:
            Config对象

        Raises:
            ConfigError: 配置文件不存在、格式错误或加载失败
        """
        from harness.core.logger import get_logger
        logger = get_logger(__name__)

        logger.debug(f"Loading configuration from {path}")

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            config = cls(**data)
            logger.info(
                f"Configuration loaded successfully",
                environment=config.app.environment,
                config_path=str(path)
            )
            return config

        except FileNotFoundError as e:
            logger.error(f"Configuration file not found", path=str(path))
            raise ConfigError(
                f"Configuration file not found: {path}",
                error_code="CONFIG_001",
                details={"path": str(path)}
            ) from e
        except yaml.YAMLError as e:
            logger.error(f"Invalid YAML format in configuration file", error=str(e))
            raise ConfigError(
                f"Invalid YAML format: {e}",
                error_code="CONFIG_002",
                details={"path": str(path), "yaml_error": str(e)}
            ) from e
        except Exception as e:
            logger.error(f"Failed to load configuration", error=str(e))
            raise ConfigError(
                f"Failed to load configuration: {e}",
                error_code="CONFIG_003",
                details={"path": str(path), "error": str(e)}
            ) from e


# 全局配置实例
_config: Optional[Config] = None


def get_config() -> Config:
    """
    获取全局配置实例

    Returns:
        Config对象
    """
    global _config
    if _config is None:
        # 从环境变量读取配置文件路径
        env = os.getenv("HARNESS_ENV", "dev")
        config_path = Path(__file__).parent.parent.parent / "resources" / "config" / f"config.{env}.yaml"
        _config = Config.from_yaml(config_path)
    return _config


def reload_config() -> Config:
    """
    重新加载配置

    Returns:
        新的Config对象
    """
    from harness.core.logger import get_logger
    logger = get_logger(__name__)

    global _config
    _config = None
    logger.info("Configuration reloaded")
    return get_config()


# 全局配置实例（延迟初始化）
def _get_settings() -> Config:
    """延迟获取设置，避免循环导入"""
    return get_config()


# 创建一个懒加载的settings对象
class _LazySettings:
    """懒加载设置对象，避免模块初始化时的循环依赖"""

    def __init__(self):
        self._cached: Optional[Config] = None

    def __getattr__(self, name: str) -> Any:
        if self._cached is None:
            self._cached = get_config()
        return getattr(self._cached, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_cached":
            super().__setattr__(name, value)
        else:
            if self._cached is None:
                self._cached = get_config()
            setattr(self._cached, name, value)


settings = _LazySettings()
