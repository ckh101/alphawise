"""
飞书 Channel 配置管理

复用 Setting 表存储飞书配置，key 前缀 "feishu."。
支持多通道：feishu.channels 存储通道列表（JSON 数组）。
单通道配置（feishu.app_id 等）保持向后兼容。
"""

import json
from typing import Optional

from harness.core.database import get_all_settings, update_settings
from harness.core.logger import get_logger

logger = get_logger(__name__)

CHANNELS_KEY = "feishu.channels"


def _get_channels_raw() -> list[dict]:
    """从 DB 读取通道列表原始数据"""
    settings = get_all_settings("feishu.")
    raw = settings.get(CHANNELS_KEY, "")
    if raw:
        try:
            channels = json.loads(raw)
            if isinstance(channels, list):
                return channels
        except json.JSONDecodeError:
            pass
    return []


def _save_channels_raw(channels: list[dict]) -> None:
    """保存通道列表到 DB"""
    update_settings({CHANNELS_KEY: json.dumps(channels, ensure_ascii=False)})


# ========== 单通道配置（向后兼容）==========

def get_feishu_config() -> dict[str, str]:
    """
    从数据库读取飞书配置，fallback 到 YAML。
    返回: {feishu.enabled, feishu.app_id, feishu.app_secret, ...}
    """
    db_config = get_all_settings("feishu.")
    if db_config:
        return db_config

    # fallback 到 YAML 配置
    try:
        from harness.core.config import get_config
        cfg = get_config().feishu
        return {
            "feishu.enabled": str(cfg.enabled).lower(),
            "feishu.app_id": cfg.app_id,
            "feishu.app_secret": cfg.app_secret,
            "feishu.verification_token": cfg.verification_token,
            "feishu.encrypt_key": cfg.encrypt_key,
        }
    except Exception:
        return {
            "feishu.enabled": "false",
            "feishu.app_id": "",
            "feishu.app_secret": "",
            "feishu.verification_token": "",
            "feishu.encrypt_key": "",
        }


def is_feishu_configured() -> bool:
    """检查飞书是否已配置（app_id + app_secret 非空）"""
    config = get_feishu_config()
    return bool(config.get("feishu.app_id") and config.get("feishu.app_secret"))


def save_feishu_config(config: dict[str, str]) -> None:
    """保存飞书配置到数据库"""
    data = {}
    for key, value in config.items():
        if key.startswith("feishu."):
            data[key] = str(value)
        else:
            data[f"feishu.{key}"] = str(value)

    update_settings(data)
    logger.info("Feishu config saved", keys=list(data.keys()))


# ========== 多通道配置 ==========

def list_channels() -> list[dict]:
    """列出所有通道配置，每个通道包含 id, name, app_id, enabled 等"""
    return _get_channels_raw()


def get_channel(channel_id: str) -> Optional[dict]:
    """获取指定通道配置"""
    for ch in _get_channels_raw():
        if ch.get("id") == channel_id:
            return ch
    return None


def add_channel(channel: dict) -> dict:
    """新增通道，自动分配 id，返回新增后的通道"""
    channels = _get_channels_raw()
    # 生成 id
    from datetime import datetime
    ch_id = f"ch_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    channel["id"] = ch_id
    channel.setdefault("name", channel.get("app_id", "")[:8])
    channel.setdefault("enabled", "true")
    channels.append(channel)
    _save_channels_raw(channels)
    logger.info(f"Feishu channel added: id={ch_id}, name={channel.get('name')}")
    return channel


def update_channel(channel_id: str, updates: dict) -> Optional[dict]:
    """更新通道配置"""
    channels = _get_channels_raw()
    for ch in channels:
        if ch.get("id") == channel_id:
            # 不允许修改 id
            updates.pop("id", None)
            ch.update(updates)
            _save_channels_raw(channels)
            logger.info(f"Feishu channel updated: id={channel_id}")
            return ch
    return None


def delete_channel(channel_id: str) -> bool:
    """删除通道配置"""
    channels = _get_channels_raw()
    new_channels = [ch for ch in channels if ch.get("id") != channel_id]
    if len(new_channels) == len(channels):
        return False
    _save_channels_raw(new_channels)
    logger.info(f"Feishu channel deleted: id={channel_id}")
    return True


def get_channel_push_targets(channel_id: str) -> list[dict]:
    """获取通道的默认推送目标列表"""
    ch = get_channel(channel_id)
    if not ch:
        return []
    return ch.get("push_targets", [])
