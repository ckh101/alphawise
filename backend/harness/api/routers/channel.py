"""
Channel 管理 API 路由

管理飞书等外部 Channel 的配置、启停和状态查询。
支持多通道配置。
"""

import asyncio
import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from harness.api.models import ApiResponse, error_response, success_response
from harness.core.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/channel", tags=["Channel管理"])


# ========== 单通道配置（向后兼容）==========

class ChannelConfigRequest(BaseModel):
    enabled: str = "false"
    app_id: str = ""
    app_secret: str = ""
    verification_token: str = ""
    encrypt_key: str = ""


@router.get("/feishu/status", response_model=ApiResponse)
async def get_feishu_status() -> ApiResponse:
    try:
        from harness.services.feishu_client import get_feishu_client
        from harness.core.feishu_config import get_feishu_config, is_feishu_configured

        client = get_feishu_client()
        config = get_feishu_config()

        return success_response(data={
            "running": client.is_running,
            "configured": is_feishu_configured(),
            "enabled": config.get("feishu.enabled", "false"),
        })
    except Exception as e:
        logger.error(f"Failed to get feishu status: {e}")
        raise HTTPException(status_code=500, detail=error_response(500, str(e)).model_dump())


@router.post("/feishu/start", response_model=ApiResponse)
async def start_feishu() -> ApiResponse:
    try:
        from harness.services.feishu_client import get_feishu_client
        from harness.core.feishu_config import is_feishu_configured

        if not is_feishu_configured():
            return error_response(400, "飞书未配置，请先保存 app_id 和 app_secret")

        client = get_feishu_client()
        client.start()

        return success_response(message="飞书 Channel 启动中")
    except Exception as e:
        logger.error(f"Failed to start feishu: {e}")
        raise HTTPException(status_code=500, detail=error_response(500, str(e)).model_dump())


@router.post("/feishu/stop", response_model=ApiResponse)
async def stop_feishu() -> ApiResponse:
    try:
        from harness.services.feishu_client import get_feishu_client

        client = get_feishu_client()
        client.stop()

        return success_response(message="飞书 Channel 已停止")
    except Exception as e:
        logger.error(f"Failed to stop feishu: {e}")
        raise HTTPException(status_code=500, detail=error_response(500, str(e)).model_dump())


@router.get("/feishu/config", response_model=ApiResponse)
async def get_feishu_config_api() -> ApiResponse:
    try:
        from harness.core.feishu_config import get_feishu_config

        config = get_feishu_config()
        secret = config.get("feishu.app_secret", "")
        masked = f"****{secret[-4:]}" if len(secret) > 4 else "****" if secret else ""

        return success_response(data={
            "enabled": config.get("feishu.enabled", "false"),
            "app_id": config.get("feishu.app_id", ""),
            "app_secret": masked,
            "verification_token": config.get("feishu.verification_token", ""),
            "encrypt_key": config.get("feishu.encrypt_key", ""),
        })
    except Exception as e:
        logger.error(f"Failed to get feishu config: {e}")
        raise HTTPException(status_code=500, detail=error_response(500, str(e)).model_dump())


@router.put("/feishu/config", response_model=ApiResponse)
async def save_feishu_config_api(req: ChannelConfigRequest) -> ApiResponse:
    try:
        from harness.core.feishu_config import save_feishu_config

        data = {
            "feishu.enabled": req.enabled,
            "feishu.app_id": req.app_id,
            "feishu.verification_token": req.verification_token,
            "feishu.encrypt_key": req.encrypt_key,
        }
        if req.app_secret and not req.app_secret.startswith("****"):
            data["feishu.app_secret"] = req.app_secret

        save_feishu_config(data)

        return success_response(message="飞书配置已保存")
    except Exception as e:
        logger.error(f"Failed to save feishu config: {e}")
        raise HTTPException(status_code=500, detail=error_response(500, str(e)).model_dump())


# ========== 多通道管理 ==========

class ChannelCreateRequest(BaseModel):
    name: str = ""
    app_id: str = ""
    app_secret: str = ""
    verification_token: str = ""
    encrypt_key: str = ""
    enabled: str = "true"


class ChannelUpdateRequest(BaseModel):
    name: Optional[str] = None
    app_id: Optional[str] = None
    app_secret: Optional[str] = None
    verification_token: Optional[str] = None
    encrypt_key: Optional[str] = None
    enabled: Optional[str] = None


@router.get("/feishu/channels", response_model=ApiResponse)
async def list_channels_api() -> ApiResponse:
    """列出所有飞书通道配置"""
    try:
        from harness.core.feishu_config import list_channels
        from harness.services.feishu_client import get_channel_manager

        channels = list_channels()
        manager = get_channel_manager()
        running_ids = manager.get_running_channel_ids()

        # 脱敏 + 追加运行状态
        for ch in channels:
            secret = ch.get("app_secret", "")
            ch["app_secret"] = f"****{secret[-4:]}" if len(secret) > 4 else ("****" if secret else "")
            ch["running"] = ch.get("id") in running_ids

        return success_response(data={"channels": channels})
    except Exception as e:
        logger.error(f"Failed to list channels: {e}")
        raise HTTPException(status_code=500, detail=error_response(500, str(e)).model_dump())


@router.post("/feishu/channels", response_model=ApiResponse)
async def add_channel_api(req: ChannelCreateRequest) -> ApiResponse:
    """新增飞书通道"""
    try:
        from harness.core.feishu_config import add_channel
        ch = add_channel({
            "name": req.name or req.app_id[:8],
            "app_id": req.app_id,
            "app_secret": req.app_secret,
            "verification_token": req.verification_token,
            "encrypt_key": req.encrypt_key,
            "enabled": req.enabled,
        })
        # 如果启用，自动启动
        if req.enabled.lower() == "true":
            from harness.services.feishu_client import get_channel_manager
            get_channel_manager().start_channel(ch["id"])
        return success_response(data=ch, message="通道已添加")
    except Exception as e:
        logger.error(f"Failed to add channel: {e}")
        raise HTTPException(status_code=500, detail=error_response(500, str(e)).model_dump())


@router.put("/feishu/channels/{channel_id}", response_model=ApiResponse)
async def update_channel_api(channel_id: str, req: ChannelUpdateRequest) -> ApiResponse:
    """更新飞书通道配置"""
    try:
        from harness.core.feishu_config import update_channel
        updates = {k: v for k, v in req.model_dump().items() if v is not None}
        # 脱敏的 secret 不保存
        if updates.get("app_secret", "").startswith("****"):
            updates.pop("app_secret")
        result = update_channel(channel_id, updates)
        if not result:
            return error_response(404, "通道不存在")
        return success_response(data=result, message="通道已更新")
    except Exception as e:
        logger.error(f"Failed to update channel: {e}")
        raise HTTPException(status_code=500, detail=error_response(500, str(e)).model_dump())


@router.delete("/feishu/channels/{channel_id}", response_model=ApiResponse)
async def delete_channel_api(channel_id: str) -> ApiResponse:
    """删除飞书通道"""
    try:
        from harness.core.feishu_config import delete_channel
        from harness.services.feishu_client import get_channel_manager

        get_channel_manager().stop_channel(channel_id)
        if not delete_channel(channel_id):
            return error_response(404, "通道不存在")
        return success_response(message="通道已删除")
    except Exception as e:
        logger.error(f"Failed to delete channel: {e}")
        raise HTTPException(status_code=500, detail=error_response(500, str(e)).model_dump())


@router.post("/feishu/channels/{channel_id}/start", response_model=ApiResponse)
async def start_channel_api(channel_id: str) -> ApiResponse:
    """启动指定通道"""
    try:
        from harness.services.feishu_client import get_channel_manager
        if get_channel_manager().start_channel(channel_id):
            return success_response(message="通道启动中")
        return error_response(400, "通道配置不完整")
    except Exception as e:
        logger.error(f"Failed to start channel: {e}")
        raise HTTPException(status_code=500, detail=error_response(500, str(e)).model_dump())


@router.post("/feishu/channels/{channel_id}/stop", response_model=ApiResponse)
async def stop_channel_api(channel_id: str) -> ApiResponse:
    """停止指定通道"""
    try:
        from harness.services.feishu_client import get_channel_manager
        get_channel_manager().stop_channel(channel_id)
        return success_response(message="通道已停止")
    except Exception as e:
        logger.error(f"Failed to stop channel: {e}")
        raise HTTPException(status_code=500, detail=error_response(500, str(e)).model_dump())


# ========== SSE 事件流（支持 channel_id 过滤）==========

@router.get("/feishu/events")
async def feishu_event_stream(request: Request, channel_id: Optional[str] = None):
    """SSE endpoint，实时推送飞书对话事件。支持 ?channel_id=xxx 过滤。"""
    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def on_event(event_type: str, data: dict):
            # 如果指定了 channel_id 过滤，只推送匹配的事件
            if channel_id and data.get("channel_id") != channel_id:
                return
            loop.call_soon_threadsafe(
                queue.put_nowait, {"type": event_type, "data": data}
            )

        from harness.services.feishu_client import subscribe_feishu_events
        unsubscribe = subscribe_feishu_events(on_event)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            unsubscribe()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
