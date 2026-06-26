"""
飞书 Handler

Worker 内管理飞书 WebSocket 连接：
- 启停飞书客户端（单通道 + 多通道管理器）
- 查询连接状态
"""

import logging

logger = logging.getLogger("worker.feishu")


async def handle_status(method: str, params: dict) -> dict:
    """获取飞书连接状态"""
    from harness.services.feishu_client import get_feishu_client, get_channel_manager

    client = get_feishu_client()
    manager = get_channel_manager()

    return {
        "status": "ok",
        "data": {
            "running": client.is_running,
            "channels_running": manager.get_running_channel_ids(),
            "any_running": manager.is_any_running,
        },
    }


async def handle_start(method: str, params: dict) -> dict:
    """启动飞书客户端"""
    from harness.services.feishu_client import get_feishu_client, get_channel_manager

    try:
        client = get_feishu_client()
        client.start()

        manager = get_channel_manager()
        count = manager.start_all()

        logger.info(f"Feishu started: {count} channels")
        return {"status": "ok", "message": f"飞书已启动，{count} 个通道已连接"}
    except Exception as e:
        logger.error(f"Feishu start failed: {e}", exc_info=True)
        return {"status": "error", "message": f"飞书启动失败: {e}"}


async def handle_stop(method: str, params: dict) -> dict:
    """停止飞书客户端"""
    from harness.services.feishu_client import get_feishu_client, get_channel_manager

    try:
        client = get_feishu_client()
        client.stop()

        manager = get_channel_manager()
        manager.stop_all()

        logger.info("Feishu stopped")
        return {"status": "ok", "message": "飞书已停止"}
    except Exception as e:
        logger.error(f"Feishu stop failed: {e}", exc_info=True)
        return {"status": "error", "message": f"飞书停止失败: {e}"}


async def handle_channel_start(method: str, params: dict) -> dict:
    """启动指定通道"""
    channel_id = params.get("channel_id", "")
    if not channel_id:
        return {"status": "error", "message": "channel_id 参数不能为空"}

    from harness.services.feishu_client import get_channel_manager

    try:
        manager = get_channel_manager()
        ok = manager.start_channel(channel_id)
        if ok:
            return {"status": "ok", "message": f"通道 {channel_id} 已启动"}
        return {"status": "error", "message": f"通道 {channel_id} 启动失败，请检查配置"}
    except Exception as e:
        logger.error(f"Channel start failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


async def handle_channel_stop(method: str, params: dict) -> dict:
    """停止指定通道"""
    channel_id = params.get("channel_id", "")
    if not channel_id:
        return {"status": "error", "message": "channel_id 参数不能为空"}

    from harness.services.feishu_client import get_channel_manager

    try:
        manager = get_channel_manager()
        manager.stop_channel(channel_id)
        return {"status": "ok", "message": f"通道 {channel_id} 已停止"}
    except Exception as e:
        logger.error(f"Channel stop failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


async def handle_channel_reload(method: str, params: dict) -> dict:
    """通道配置变更后重载：停止所有已运行通道，按最新配置重新启动"""
    from harness.services.feishu_client import get_channel_manager

    try:
        manager = get_channel_manager()
        manager.stop_all()
        count = manager.start_all()
        logger.info(f"Feishu channels reloaded: {count} channels")
        return {"status": "ok", "message": f"通道已重载，{count} 个通道已连接"}
    except Exception as e:
        logger.error(f"Channel reload failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


async def handle_send_message(method: str, params: dict) -> dict:
    """主动发送飞书消息"""
    receive_id = params.get("receive_id", "")
    receive_id_type = params.get("receive_id_type", "chat_id")
    text = params.get("text", "")
    channel_id = params.get("channel_id")

    if not receive_id or not text:
        return {"status": "error", "message": "receive_id 和 text 参数不能为空"}

    from harness.services.feishu_client import send_feishu_message

    try:
        ok = send_feishu_message(receive_id, receive_id_type, text, channel_id)
        if ok:
            return {"status": "ok", "message": "消息已发送"}
        return {"status": "error", "message": "消息发送失败"}
    except Exception as e:
        logger.error(f"Send message failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
