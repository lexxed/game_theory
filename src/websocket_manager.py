"""Background combined WebSocket with reconnect. Public market path only."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import websockets

from config import get

log = logging.getLogger("gt.ws")

MessageHandler = Callable[[str, dict], None]


class WebsocketManager:
    """
    Connects to wss://fstream.binance.com/market/stream?streams=a/b/c
    Runs an asyncio loop in a daemon thread. Never raises into the caller.
    """

    def __init__(self, on_message: MessageHandler, on_status: Callable[[str, str], None] | None = None):
        self.on_message = on_message
        self.on_status = on_status or (lambda _s, _m: None)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self._streams: list[str] = []
        self.connected = False
        self.last_msg_ms = 0
        self.last_error = ""
        self.reconnects = 0
        self.url = ""

    def start(self, streams: list[str]) -> None:
        self.stop()
        self._stop.clear()
        self._streams = list(streams)
        self._thread = threading.Thread(target=self._thread_main, name="gt-ws", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._loop and self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(lambda: None)
            except Exception:
                pass
        if self._thread and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=3)
        self._thread = None
        self.connected = False

    def update_streams(self, streams: list[str]) -> None:
        """Drop and reconnect with a new stream list (symbol change)."""
        self.start(streams)

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run())
        except Exception as exc:
            self.last_error = str(exc)
            log.exception("ws thread crashed")
        finally:
            try:
                self._loop.stop()
                self._loop.close()
            except Exception:
                pass

    async def _run(self) -> None:
        backoff = 1.0
        base = get("binance.ws_combined", "wss://fstream.binance.com/market/stream")
        while not self._stop.is_set():
            streams = "/".join(self._streams)
            self.url = f"{base}?streams={streams}"
            try:
                self.on_status("ws", "connecting")
                async with websockets.connect(
                    self.url,
                    ping_interval=15,
                    ping_timeout=10,
                    open_timeout=12,
                    close_timeout=3,
                    max_size=2**22,
                ) as ws:
                    self.connected = True
                    self.last_error = ""
                    backoff = 1.0
                    self.on_status("ws", "live")
                    await self._read_loop(ws)
            except Exception as exc:
                self.connected = False
                self.last_error = f"{type(exc).__name__}: {exc}"
                self.reconnects += 1
                self.on_status("ws", f"reconnect {self.last_error}")
                log.warning("ws disconnect: %s", self.last_error)
            if self._stop.is_set():
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.7, 30.0)

        self.connected = False
        self.on_status("ws", "stopped")

    async def _read_loop(self, ws) -> None:
        started = time.time()
        while not self._stop.is_set():
            # Binance public sockets are recycled ~24h; reconnect early.
            if time.time() - started > 23 * 3600:
                return
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=8)
            except TimeoutError:
                # aggTrade should tick often; markPrice@1s always ticks.
                continue
            except Exception:
                raise
            self.last_msg_ms = int(time.time() * 1000)
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            stream = payload.get("stream", "")
            data = payload.get("data", payload)
            if not isinstance(data, dict):
                continue
            try:
                self.on_message(stream, data)
            except Exception:
                log.exception("handler error on %s", stream)
