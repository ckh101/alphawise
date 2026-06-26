"""
API路由模块
"""

from .tdx import router as tdx_router
from .glm import router as glm_router
from .agent import router as agent_router
from .backtest import router as backtest_router
from .settings import router as settings_router
from .monitor import router as monitor_router
from .channel import router as channel_router
from .scheduler import router as scheduler_router
from .watchlist import router as watchlist_router

__all__ = ["tdx_router", "glm_router", "agent_router", "backtest_router", "settings_router", "monitor_router", "channel_router", "scheduler_router", "watchlist_router"]
