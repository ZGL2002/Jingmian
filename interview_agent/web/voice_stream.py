"""WebSocket 语音流：浏览器 16k PCM 帧 ↔ DashScope 流式识别。

BaseHTTPMiddleware 不处理 websocket scope，鉴权必须在端点内手工完成。
"""
from __future__ import annotations
import asyncio
from fastapi import WebSocket
from .audio import StreamRecognizer


async def run_voice_websocket(websocket: WebSocket, audio, on_event_loop=None) -> None:
    """audio: AudioService；调用方负责 accept 前的全部守卫（鉴权/会话/可用性）。"""
    await websocket.accept()
    loop = asyncio.get_running_loop()
    events: asyncio.Queue = asyncio.Queue()

    def on_event(ev: dict) -> None:  # SDK 回调线程 → 事件循环
        loop.call_soon_threadsafe(events.put_nowait, ev)

    rec = StreamRecognizer(audio.engine, on_event)
    await asyncio.to_thread(rec.start)  # start 内部建连，避免阻塞事件循环

    async def pump() -> None:
        while True:
            ev = await events.get()
            await websocket.send_json(ev)
            if ev.get("type") == "error":
                # 识别会话已死（超时/网络错误）：主动断开，客户端会自动
                # 重连并建立全新识别会话，避免后续语音被静默丢弃
                await websocket.close(code=1011)
                return

    pump_task = asyncio.create_task(pump())
    try:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if data:
                rec.feed(data)
            elif msg.get("text") == '{"type":"stop"}':
                break
    finally:
        pump_task.cancel()
        try:
            await asyncio.to_thread(rec.stop)
        except Exception:  # noqa: BLE001 - 断连清理兜底
            pass
