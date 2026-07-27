"""
飞书 Channel WebSocket 长连接客户端

通过 lark-oapi SDK 的 WsClient 接收飞书消息，
调用 ReactOrchestrator 处理后回复。

支持多通道：每个飞书应用一个 FeishuChannelInstance。
"""

import asyncio
import concurrent.futures
import json
import re
import sys
import threading
from datetime import datetime
from typing import Any, Callable, Optional

from harness.core.logger import get_logger

logger = get_logger(__name__)

# 模块级事件广播：SSE endpoint 订阅
_event_callbacks: list[Callable] = []
_event_lock = threading.Lock()

# 共享事件循环：lark-oapi SDK 使用全局模块变量 loop，
# 所有通道必须在同一个 loop 上运行。
_shared_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_thread: Optional[threading.Thread] = None
_loop_ready = threading.Event()


def _ensure_shared_loop() -> asyncio.AbstractEventLoop:
    """确保共享事件循环线程已启动并就绪"""
    global _shared_loop, _loop_thread
    if _shared_loop is not None and _shared_loop.is_running():
        return _shared_loop

    _loop_ready.clear()
    _shared_loop = asyncio.new_event_loop()

    def _run_loop():
        asyncio.set_event_loop(_shared_loop)
        _loop_ready.set()
        _shared_loop.run_forever()

    _loop_thread = threading.Thread(target=_run_loop, name="feishu-shared-loop", daemon=True)
    _loop_thread.start()
    _loop_ready.wait(timeout=5)
    logger.info("Feishu shared event loop started")
    return _shared_loop


def subscribe_feishu_events(callback: Callable) -> Callable:
    """注册事件回调，返回取消订阅函数"""
    with _event_lock:
        _event_callbacks.append(callback)
    def unsubscribe():
        with _event_lock:
            try:
                _event_callbacks.remove(callback)
            except ValueError:
                pass
    return unsubscribe


def _broadcast_event(event_type: str, data: dict) -> None:
    """广播事件给所有 SSE 订阅者（线程安全），同时转发到 Node.js"""
    with _event_lock:
        callbacks = list(_event_callbacks)
    for cb in callbacks:
        try:
            cb(event_type, data)
        except Exception:
            pass
    # 转发到 Node.js 后端的 SSE 端点
    _forward_to_node(event_type, data)


def _forward_to_node(event_type: str, data: dict) -> None:
    """将事件通过 HTTP POST 转发到 Node.js"""
    try:
        import urllib.request
        payload = json.dumps({"type": event_type, "data": data}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:9998/api/v1/channel/feishu/internal/event",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass  # 不阻塞主流程


# ---------------------------------------------------------------------------
# Markdown 表格解析辅助（模块级私有，供 FeishuChannelInstance 块级解析器复用）
# ---------------------------------------------------------------------------

_TABLE_CELL_RE = re.compile(r"\|")


def _is_table_row(line: str) -> bool:
    """是否为 GFM 表格的数据/表头行（以 | 开头并含至少一个内部分隔）。"""
    s = line.strip()
    return s.startswith("|") and s.count("|") >= 2


def _is_table_separator(line: str) -> bool:
    """是否为表格分隔行（如 |---|:--:|）。"""
    s = line.strip()
    if not s.startswith("|"):
        return False
    # 去掉首尾 | 后，每段应只含 -、: 和空白
    inner = s.strip("|")
    cells = inner.split("|")
    if not cells:
        return False
    return all(re.fullmatch(r"\s*:?-{1,}:?\s*", c) for c in cells) and any(c.strip() for c in cells)


def _split_table_cells(line: str) -> list[str]:
    """拆一行表格为单元格列表，去掉首尾空单元、转义符。"""
    s = line.strip()
    s = s.strip("|")
    # 用负向断言避免切到 \|
    cells = re.split(r"(?<!\\)\|", s)
    return [c.strip() for c in cells]


def _strip_md_inline(text: str) -> str:
    """剥掉单元格内的行内 markdown 标记（加粗/斜体/代码/链接），table 组件 data_type=text 用。"""
    # 链接 [text](url) → text
    text = re.sub(r"\[([^\]]*)\]\([^)]+\)", r"\1", text)
    # 加粗/斜体 **x** / __x__ / *x* / _x_
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"(?<!\w)\*([^*]+)\*(?!\w)", r"\1", text)
    text = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"\1", text)
    # 行内代码 `x`
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text.strip()


def _build_table_element(headers: list[str], rows: list[list[str]]) -> dict:
    """构造飞书 table 组件 JSON。列数 > 10 时截断到 10（飞书硬限）。"""
    max_cols = 10
    headers = headers[:max_cols]
    col_keys = [f"c{i}" for i in range(len(headers))]
    columns = [
        {
            "name": col_keys[i],
            "display_name": _strip_md_inline(headers[i]),
            "data_type": "text",
            "width": "auto",
        }
        for i in range(len(headers))
    ]
    table_rows: list[dict] = []
    for raw_row in rows:
        row_obj: dict = {}
        for i in range(len(headers)):
            cell = raw_row[i] if i < len(raw_row) else ""
            row_obj[col_keys[i]] = _strip_md_inline(cell)
        table_rows.append(row_obj)
    return {
        "tag": "table",
        "page_size": min(10, max(1, len(table_rows))) if table_rows else 1,
        "row_height": "low",
        "columns": columns,
        "rows": table_rows,
    }


def _extract_card_text(card: dict) -> str:
    """从卡片 JSON 提取全部纯文本，用于 230099 降级纯文本兜底。"""
    parts: list[str] = []
    body = card.get("body") or {}
    for el in body.get("elements", []):
        tag = el.get("tag")
        if tag == "markdown":
            parts.append(el.get("content", ""))
        elif tag == "table":
            for row in el.get("rows", []):
                parts.append(" | ".join(str(v) for v in row.values()))
    return "\n".join(p for p in parts if p)


class FeishuChannelInstance:
    """单个飞书通道实例（一个 app_id 对应一个连接）"""

    def __init__(self, channel_id: str, channel_name: str,
                 app_id: str, app_secret: str,
                 verification_token: str = "", encrypt_key: str = "") -> None:
        self.channel_id = channel_id
        self.channel_name = channel_name
        self._app_id = app_id
        self._app_secret = app_secret
        self._verification_token = verification_token
        self._encrypt_key = encrypt_key

        self._lark_client: Any = None
        self._ws_client: Any = None
        self._running: bool = False
        self._sessions: dict[str, str] = {}
        self._executor: Optional[concurrent.futures.ThreadPoolExecutor] = None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        """启动飞书 WebSocket 连接"""
        if self._running:
            return

        # 构建 Lark Client
        from lark_oapi import Client as LarkClient
        self._lark_client = LarkClient.builder() \
            .app_id(self._app_id) \
            .app_secret(self._app_secret) \
            .build()

        # 注册消息事件处理器
        from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
        handler = EventDispatcherHandler.builder(
            encrypt_key=self._encrypt_key,
            verification_token=self._verification_token,
        ).register_p2_im_message_receive_v1(self._on_message).build()

        # 创建 WsClient
        from lark_oapi.ws.client import Client as WsClient
        from lark_oapi.core.enum import LogLevel
        self._ws_client = WsClient(
            app_id=self._app_id,
            app_secret=self._app_secret,
            event_handler=handler,
            log_level=LogLevel.INFO,
            auto_reconnect=True,
        )

        self._running = True
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        loop = _ensure_shared_loop()
        # 设置 SDK 全局 loop（所有通道共用同一个 loop）
        import lark_oapi.ws.client as ws_module
        ws_module.loop = loop
        # 在共享 loop 上启动连接和后台任务
        async def _start():
            try:
                await self._ws_client._connect()
                loop.create_task(self._ws_client._ping_loop())
                logger.info(f"Feishu channel [{self.channel_name}] connected")
            except Exception as e:
                logger.error(f"Feishu channel [{self.channel_name}] connect failed: {e}")
                self._running = False
        loop.call_soon_threadsafe(lambda: loop.create_task(_start()))
        logger.info(f"Feishu channel [{self.channel_name}] starting...")

    def stop(self) -> None:
        """停止连接"""
        self._running = False
        if self._executor:
            self._executor.shutdown(wait=False)
            self._executor = None
        self._ws_client = None
        self._lark_client = None
        logger.info(f"Feishu channel [{self.channel_name}] stopped")

    def _on_message(self, data: Any) -> None:
        """处理飞书消息事件"""
        try:
            msg = data.event.message
            sender = data.event.sender

            chat_id = msg.chat_id
            msg_id = msg.message_id
            msg_type = msg.message_type
            content_str = msg.content

            if msg_type != "text":
                return

            content = json.loads(content_str)
            text = content.get("text", "").strip()
            if not text:
                return

            # @ 去重：飞书群中 @A bot 的消息也会推送给 bot B
            # mentions[].name 是飞书应用名称，与 channel_name 配置一致
            mentions = getattr(msg, 'mentions', None) or []
            has_mention_for_me = False
            display_text = text
            for m in mentions:
                m_name = getattr(m, 'name', '') or ''
                m_key = getattr(m, 'key', '') or ''
                if m_key and m_name:
                    if not m_key.startswith('@'):
                        m_key = '@' + m_key
                    display_text = display_text.replace(m_key, '@' + m_name)
                if m_name == self.channel_name:
                    has_mention_for_me = True
            if mentions and not has_mention_for_me:
                mentioned = [getattr(m, 'name', '') for m in mentions]
                # 检查是否有其他已注册通道匹配这些 mention——如果没有，说明配置名与机器人名不一致
                from harness.services.feishu_client import get_channel_manager
                all_channel_names = [inst.channel_name for inst in get_channel_manager()._instances.values() if inst.is_running]
                if not any(name in all_channel_names for name in mentioned):
                    logger.info(f"[{self.channel_name}] no channel matches mentioned {mentioned}, replying config hint")
                    self._send_reply(msg_id,
                        f"未找到匹配的通道配置。请检查设置中飞书通道名称是否与机器人名称一致。\n"
                        f"当前通道: {self.channel_name}，被 @的名称: {', '.join(mentioned)}",
                        chat_id)
                else:
                    logger.info(f"[{self.channel_name}] not @me (mentioned: {mentioned}), skip")
                return

            sender_id = sender.sender_id.open_id if sender and sender.sender_id else "unknown"
            logger.info(f"[{self.channel_name}] message: chat_id={chat_id}, sender={sender_id}, text={text[:50]}")

            session_id = self._get_or_create_session(chat_id)

            _broadcast_event("feishu_message", {
                "channel_id": self.channel_id,
                "channel_name": self.channel_name,
                "chat_id": chat_id, "session_id": session_id,
                "text": display_text, "sender_id": sender_id,
            })

            self._process_and_reply(text, session_id, msg_id, chat_id)

        except Exception as e:
            logger.error(f"[{self.channel_name}] message handling error: {e}", exc_info=True)

    def _process_and_reply(self, text: str, session_id: str, msg_id: str, chat_id: str) -> None:
        if self._executor:
            self._executor.submit(self._do_process, text, session_id, msg_id, chat_id)

    def _do_process(self, text: str, session_id: str, msg_id: str, chat_id: str) -> None:
        if sys.platform == "win32":
            loop = asyncio.ProactorEventLoop()
        else:
            loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(self._run_analysis(text, session_id))
            self._send_reply(msg_id, result, chat_id)
            _broadcast_event("feishu_reply", {
                "channel_id": self.channel_id,
                "channel_name": self.channel_name,
                "session_id": session_id, "text": result,
            })
        except Exception as e:
            logger.error(f"[{self.channel_name}] analysis failed: {e}", exc_info=True)
            self._send_reply(msg_id, f"处理失败: {e}", chat_id)
            _broadcast_event("feishu_error", {
                "channel_id": self.channel_id,
                "channel_name": self.channel_name,
                "session_id": session_id, "message": str(e),
            })
        finally:
            loop.close()

    async def _run_analysis(self, prompt: str, session_id: str) -> str:
        from harness.agent.react_orchestrator import ReactOrchestrator, get_react_orchestrator
        # 创建独立 orchestrator 实例，复制全局 tools（只读引用），避免通道间状态污染
        global_orch = get_react_orchestrator()
        orchestrator = ReactOrchestrator()
        orchestrator.tools = dict(global_orch.tools)
        orchestrator.skills = dict(global_orch.skills)

        async def progress_callback(event: dict):
            _broadcast_event("feishu_progress", {
                "channel_id": self.channel_id,
                "channel_name": self.channel_name,
                "session_id": session_id,
                "message": event.get("message", ""),
            })

        result = await orchestrator.execute(
            prompt=prompt, session_id=session_id,
            progress_callback=progress_callback,
        )
        return result.get("report", result.get("message", "分析完成"))

    @staticmethod
    def _sanitize_for_card(text: str) -> str:
        """清理 Markdown 中飞书卡片不支持的内容（外部图片 URL 等）"""
        # 移除 Markdown 图片语法 ![alt](url)，保留 alt 文本
        text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'\1', text)
        # 移除 HTML img 标签
        text = re.sub(r'<img[^>]*>', '', text)
        return text

    # ------------------------------------------------------------------
    # Markdown → 飞书卡片 JSON 转换
    # 飞书卡片 markdown 元素不支持 GFM 表格，表格必须用 table 组件。
    # 这里把 report 解析成块级序列，表格转 table 组件，其余转 markdown 元素。
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_markdown_blocks(text: str) -> list[dict]:
        """把 markdown 解析成块级序列。

        每块形如：
          {"type": "md", "content": <原始 md 文本>}      # 标题/段落/列表/代码块等，飞书 markdown 元素能渲染
          {"type": "table", "headers": [...], "rows": [[...], ...]}  # GFM 表格

        相邻的非表格块合并成一个 md 块，减少卡片元素数量。
        """
        lines = text.split("\n")
        blocks: list[dict] = []
        md_buf: list[str] = []

        def flush_md() -> None:
            if md_buf:
                content = "\n".join(md_buf).strip()
                if content:
                    blocks.append({"type": "md", "content": content})
                md_buf.clear()

        i = 0
        n = len(lines)
        while i < n:
            line = lines[i]

            # 识别 GFM 表格：一行 |...| 紧跟一行 |---|---|
            if _is_table_row(line) and i + 1 < n and _is_table_separator(lines[i + 1]):
                flush_md()
                headers = _split_table_cells(line)
                # 分隔行跳过
                j = i + 2
                rows: list[list[str]] = []
                while j < n and _is_table_row(lines[j]):
                    rows.append(_split_table_cells(lines[j]))
                    j += 1
                blocks.append({"type": "table", "headers": headers, "rows": rows})
                i = j
                continue

            md_buf.append(line)
            i += 1

        flush_md()
        return blocks

    @staticmethod
    def _blocks_to_card_elements(blocks: list[dict]) -> list[dict]:
        """块序列 → 飞书卡片 body.elements 数组。

        table 块 → table 组件（单元格 data_type=text，剥掉单元格内的 markdown 标记）
        md 块    → markdown 元素
        """
        elements: list[dict] = []
        for block in blocks:
            if block["type"] == "table":
                elements.append(_build_table_element(block["headers"], block["rows"]))
            else:
                elements.append({"tag": "markdown", "content": block["content"]})
        return elements

    @staticmethod
    def _elements_to_cards(elements: list[dict]) -> list[dict]:
        """把 elements 装配成 1~N 张飞书卡片（schema 2.0）。

        拆卡条件（飞书硬限）：单卡 table 组件 ≥ 5 个时，下一个 table 开新卡；
        以及单卡累计内容字符数超过安全阈值（25000）时开新卡。
        """
        MAX_TABLE_PER_CARD = 5
        MAX_CHARS_PER_CARD = 25000
        cards: list[dict] = []
        cur_elements: list[dict] = []
        cur_table_count = 0
        cur_chars = 0

        def cur_card() -> dict:
            return {
                "schema": "2.0",
                "config": {"update_multi": True},
                "body": {
                    "direction": "vertical",
                    "elements": list(cur_elements),
                },
            }

        for el in elements:
            is_table = el.get("tag") == "table"
            el_chars = len(json.dumps(el, ensure_ascii=False))
            # 当前元素放进去会超限，且当前卡已有内容 → 封卡开新卡
            over_table = is_table and cur_table_count >= MAX_TABLE_PER_CARD
            over_chars = cur_chars + el_chars > MAX_CHARS_PER_CARD and cur_elements
            if cur_elements and (over_table or over_chars):
                cards.append(cur_card())
                cur_elements = []
                cur_table_count = 0
                cur_chars = 0
            cur_elements.append(el)
            if is_table:
                cur_table_count += 1
            cur_chars += el_chars

        if cur_elements:
            cards.append(cur_card())
        return cards

    @staticmethod
    def _markdown_to_cards(text: str) -> list[dict]:
        """一条龙：markdown 文本 → 飞书卡片 JSON 列表。"""
        blocks = FeishuChannelInstance._parse_markdown_blocks(text)
        elements = FeishuChannelInstance._blocks_to_card_elements(blocks)
        return FeishuChannelInstance._elements_to_cards(elements)

    def _send_reply(self, reply_to_msg_id: str, text: str, chat_id: str = "") -> None:
        if not self._lark_client:
            logger.warning(f"[{self.channel_name}] skip reply: lark_client is None")
            return
        if not text or not text.strip():
            logger.warning(f"[{self.channel_name}] skip reply: empty text")
            return

        text = self._sanitize_for_card(text)

        try:
            import time as _time
            from lark_oapi.api.im.v1 import (
                CreateMessageRequest, CreateMessageRequestBody,
                ReplyMessageRequest, ReplyMessageRequestBody,
            )
            cards = self._markdown_to_cards(text)

            def _send_text(text_content: str) -> bool:
                """纯文本发送（按 4000 字符切分）—— 230099 兜底"""
                if not chat_id:
                    return False
                for t in self._split_text(text_content, 4000):
                    treq = CreateMessageRequest.builder() \
                        .receive_id_type("chat_id") \
                        .request_body(
                            CreateMessageRequestBody.builder()
                            .receive_id(chat_id)
                            .msg_type("text")
                            .content(json.dumps({"text": t}))
                            .build()
                        ) \
                        .build()
                    resp = self._lark_client.im.v1.message.create(treq)
                    if not resp.success():
                        logger.warning(f"[{self.channel_name}] text fallback failed: code={resp.code}, chat_id={chat_id}")
                        return False
                return True

            def _send_create(card: dict) -> bool:
                """CreateMessage 发单张卡片；230099 时降级纯文本（取卡片内全部文本）"""
                if not chat_id:
                    return False
                create_req = CreateMessageRequest.builder() \
                    .receive_id_type("chat_id") \
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(chat_id)
                        .msg_type("interactive")
                        .content(json.dumps(card, ensure_ascii=False))
                        .build()
                    ) \
                    .build()
                for attempt in range(3):
                    resp = self._lark_client.im.v1.message.create(create_req)
                    if resp.success():
                        return True
                    # 卡片表格超限，重试无意义，降级纯文本
                    if resp.code == 230099:
                        logger.warning(f"[{self.channel_name}] card table over limit, fallback to text: chat_id={chat_id}")
                        return _send_text(_extract_card_text(card))
                    logger.warning(f"[{self.channel_name}] CreateMessage failed (attempt {attempt+1}): code={resp.code}")
                    if attempt < 2:
                        _time.sleep(1)
                return False

            # 第一张卡：先尝试 ReplyMessage（仅1次），失败则整个回复走 CreateMessage
            use_reply = True
            first_card = cards[0]
            reply_req = ReplyMessageRequest.builder() \
                .message_id(reply_to_msg_id) \
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .msg_type("interactive")
                    .content(json.dumps(first_card, ensure_ascii=False))
                    .build()
                ) \
                .build()
            resp = self._lark_client.im.v1.message.reply(reply_req)
            if not resp.success():
                logger.warning(f"[{self.channel_name}] ReplyMessage failed: code={resp.code}, msg={resp.msg}, fallback to CreateMessage")
                use_reply = False
                if not _send_create(cards[0]):
                    logger.error(f"[{self.channel_name}] first card send failed, abort remaining cards: chat_id={chat_id}")
                    return

            # 后续卡片全部用 CreateMessage
            for card in cards[1:]:
                if not _send_create(card):
                    logger.error(f"[{self.channel_name}] card send failed, abort: chat_id={chat_id}")
                    break

            logger.info(f"[{self.channel_name}] reply sent: msg_id={reply_to_msg_id}, use_reply={use_reply}, cards={len(cards)}")
        except Exception as e:
            logger.error(f"[{self.channel_name}] reply error: {e}", exc_info=True)

    @staticmethod
    def _split_text(text: str, max_len: int) -> list[str]:
        if len(text) <= max_len:
            return [text]
        chunks = []
        while text:
            if len(text) <= max_len:
                chunks.append(text)
                break
            cut = text.rfind("\n", 0, max_len)
            if cut < max_len // 2:
                cut = max_len
            chunks.append(text[:cut])
            text = text[cut:]
        return chunks

    def _get_or_create_session(self, chat_id: str) -> str:
        if chat_id not in self._sessions:
            self._sessions[chat_id] = f"feishu_{chat_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        return self._sessions[chat_id]


# ========== 兼容层：旧单通道 FeishuClient ==========

class FeishuClient:
    """飞书 WebSocket 长连接客户端（向后兼容单通道）"""

    def __init__(self) -> None:
        self._instance: Optional[FeishuChannelInstance] = None

    @property
    def is_running(self) -> bool:
        return self._instance.is_running if self._instance else False

    def start(self) -> None:
        if self._running:
            logger.warning("Feishu client already running")
            return

        from harness.core.feishu_config import get_feishu_config, is_feishu_configured

        config = get_feishu_config()
        if not is_feishu_configured():
            logger.info("Feishu not configured, skip starting")
            return

        enabled = config.get("feishu.enabled", "false")
        # 兼容 enabled 存为布尔的历史数据（Node 端曾存布尔 True/False）
        if isinstance(enabled, bool):
            enabled = "true" if enabled else "false"
        if enabled.lower() != "true":
            logger.info("Feishu channel disabled")
            return

        self._instance = FeishuChannelInstance(
            channel_id="default",
            channel_name="默认通道",
            app_id=config["feishu.app_id"],
            app_secret=config["feishu.app_secret"],
            verification_token=config.get("feishu.verification_token", ""),
            encrypt_key=config.get("feishu.encrypt_key", ""),
        )
        self._instance.start()

    def stop(self) -> None:
        if self._instance:
            self._instance.stop()
            self._instance = None

    @property
    def _running(self) -> bool:
        return self._instance is not None and self._instance.is_running


# 全局单例
_feishu_client: Optional[FeishuClient] = None


def get_feishu_client() -> FeishuClient:
    global _feishu_client
    if _feishu_client is None:
        _feishu_client = FeishuClient()
    return _feishu_client


# ========== 多通道管理器 ==========

class FeishuChannelManager:
    """管理多个飞书通道实例"""

    def __init__(self) -> None:
        self._instances: dict[str, FeishuChannelInstance] = {}

    def start_all(self) -> int:
        """从配置启动所有已启用的通道，返回启动数量"""
        from harness.core.feishu_config import list_channels
        channels = list_channels()
        started = 0
        for ch in channels:
            ch_enabled = ch.get("enabled", "true")
            # 兼容 enabled 存为布尔的历史数据
            if isinstance(ch_enabled, bool):
                ch_enabled = "true" if ch_enabled else "false"
            if ch_enabled.lower() != "true":
                continue
            if not ch.get("app_id") or not ch.get("app_secret"):
                continue
            inst = FeishuChannelInstance(
                channel_id=ch["id"],
                channel_name=ch.get("name", ch["id"]),
                app_id=ch["app_id"],
                app_secret=ch["app_secret"],
                verification_token=ch.get("verification_token", ""),
                encrypt_key=ch.get("encrypt_key", ""),
            )
            inst.start()
            self._instances[ch["id"]] = inst
            started += 1
        logger.info(f"Feishu channel manager: {started} channels started")
        return started

    def stop_all(self) -> None:
        for inst in self._instances.values():
            inst.stop()
        self._instances.clear()
        logger.info("Feishu channel manager: all channels stopped")

    def start_channel(self, channel_id: str) -> bool:
        """启动指定通道"""
        if channel_id in self._instances and self._instances[channel_id].is_running:
            return True
        from harness.core.feishu_config import get_channel
        ch = get_channel(channel_id)
        if not ch or not ch.get("app_id") or not ch.get("app_secret"):
            return False
        inst = FeishuChannelInstance(
            channel_id=ch["id"],
            channel_name=ch.get("name", ch["id"]),
            app_id=ch["app_id"],
            app_secret=ch["app_secret"],
            verification_token=ch.get("verification_token", ""),
            encrypt_key=ch.get("encrypt_key", ""),
        )
        inst.start()
        self._instances[channel_id] = inst
        return True

    def stop_channel(self, channel_id: str) -> None:
        inst = self._instances.pop(channel_id, None)
        if inst:
            inst.stop()

    def get_running_channel_ids(self) -> list[str]:
        return [cid for cid, inst in self._instances.items() if inst.is_running]

    @property
    def is_any_running(self) -> bool:
        return any(inst.is_running for inst in self._instances.values())


_manager: Optional[FeishuChannelManager] = None


def get_channel_manager() -> FeishuChannelManager:
    global _manager
    if _manager is None:
        _manager = FeishuChannelManager()
    return _manager


# ========== 主动发送消息（定时任务用） ==========

def send_feishu_message(receive_id: str, receive_id_type: str, text: str,
                        channel_id: Optional[str] = None) -> bool:
    """
    主动发送飞书消息（不需要 reply_to_msg_id）

    Args:
        receive_id: 接收者 ID（chat_id 或 open_id）
        receive_id_type: "chat_id" 或 "open_id"
        text: 消息文本
        channel_id: 使用哪个飞书通道的 credentials，None 则用第一个可用通道

    Returns:
        bool: 是否发送成功
    """
    import time as _time
    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

    # 1. 获取 lark_client：优先复用已运行通道
    #    必须与 receive_id 同源（同一飞书应用），否则会报 99992361 open_id cross app。
    lark_client = None
    if _manager and _manager._instances and channel_id:
        inst = _manager._instances.get(channel_id)
        if inst and inst.is_running and inst._lark_client:
            lark_client = inst._lark_client

    # 2. 未复用到运行中通道：按 channel_id 从配置读取该通道的 credentials 构建
    #    （保证 app 与 receive_id 同源，避免 cross app）
    if not lark_client:
        from harness.core.feishu_config import get_channel, get_feishu_config
        app_id, app_secret = "", ""
        if channel_id:
            ch = get_channel(channel_id) or {}
            app_id = ch.get("app_id", "")
            app_secret = ch.get("app_secret", "")
        if not app_id or not app_secret:
            # 向后兼容：无 channel_id 时退回单通道配置
            cfg = get_feishu_config()
            app_id = cfg.get("feishu.app_id", "")
            app_secret = cfg.get("feishu.app_secret", "")
        if not app_id or not app_secret:
            logger.warning("[send_feishu_message] no feishu credentials available")
            return False
        from lark_oapi import Client as LarkClient
        lark_client = LarkClient.builder().app_id(app_id).app_secret(app_secret).build()

    if not text or not text.strip():
        logger.warning("[send_feishu_message] empty text, skip")
        return False

    text = FeishuChannelInstance._sanitize_for_card(text)

    try:
        cards = FeishuChannelInstance._markdown_to_cards(text)

        def _send_text_fallback(text_content: str) -> bool:
            """卡片表格超限(code 230099)时降级为纯文本发送，按 4000 字符切分"""
            for t in FeishuChannelInstance._split_text(text_content, 4000):
                treq = CreateMessageRequest.builder() \
                    .receive_id_type(receive_id_type) \
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(receive_id)
                        .msg_type("text")
                        .content(json.dumps({"text": t}))
                        .build()
                    ) \
                    .build()
                if not lark_client.im.v1.message.create(treq).success():
                    logger.warning(f"[send_feishu_message] text fallback failed: receive_id={receive_id}")
                    return False
            return True

        for i, card in enumerate(cards):
            req = CreateMessageRequest.builder() \
                .receive_id_type(receive_id_type) \
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(receive_id)
                    .msg_type("interactive")
                    .content(json.dumps(card, ensure_ascii=False))
                    .build()
                ) \
                .build()
            sent = False
            last_code = None
            for attempt in range(3):
                resp = lark_client.im.v1.message.create(req)
                if resp.success():
                    sent = True
                    break
                last_code = resp.code
                # 卡片表格超限，重试无意义，直接降级纯文本
                if resp.code == 230099:
                    logger.warning(f"[send_feishu_message] card table over limit, fallback to text: receive_id={receive_id}")
                    sent = _send_text_fallback(_extract_card_text(card))
                    break
                logger.warning(f"[send_feishu_message] failed (attempt {attempt+1}): code={resp.code}, msg={resp.msg}")
                if attempt < 2:
                    _time.sleep(2)
            if not sent:
                logger.error(f"[send_feishu_message] failed after retries: receive_id={receive_id}, last_code={last_code}")
                return False
        logger.info(f"[send_feishu_message] sent: receive_id={receive_id}, cards={len(cards)}")
        return True
    except Exception as e:
        logger.error(f"[send_feishu_message] error: {e}", exc_info=True)
        return False
