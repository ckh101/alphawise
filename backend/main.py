"""
后端应用入口

启动FastAPI服务器。
"""

import asyncio
import sys
from pathlib import Path

# Windows 上 asyncio 子进程需要 ProactorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import uvicorn

# 添加项目根目录到Python路径
# 开发模式: main.py 在 backend/ 下，parent.parent = 项目根 (harness/)
# 打包模式: main.py 在 resources/backend/ 下，parent = backend/ (harness 包在这里)
_backend_dir = Path(__file__).parent
_project_root = _backend_dir.parent if (_backend_dir.parent / "harness").is_dir() else _backend_dir
sys.path.insert(0, str(_project_root))

from harness.core.config import get_config
from harness.core.logger import get_logger


def main() -> None:
    """
    启动应用
    """
    # 获取配置
    config = get_config()
    logger = get_logger("main")

    logger.info(
        "[main] Starting application",
        name=config.app.name,
        version=config.app.version,
        environment=config.app.environment,
    )

    # 启动服务器
    uvicorn.run(
        "harness.api.main:create_app",
        host=config.server.host,
        port=config.server.port,
        workers=config.server.workers,
        reload=config.server.reload,
        log_level=config.logging.level.lower(),
        factory=True,
    )


if __name__ == "__main__":
    main()
