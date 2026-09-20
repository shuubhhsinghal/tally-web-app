"""The one source of truth for "is a connector attached" and for
correlating outbound jobs with their eventual replies.

Single-process, in-memory -- this app runs as one long-lived uvicorn worker
with exactly one real Tally instance, so there is deliberately no
cross-process/Redis coordination here.
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Optional

import requests
from fastapi import WebSocket

from backend.connector.protocol import (
    ErrorKind,
    MSG_JOB_ACK,
    MSG_JOB_RESULT,
    MSG_HELLO,
    MSG_PING,
    MSG_PONG,
    build_job,
)

logger = logging.getLogger("connector")

HEARTBEAT_INTERVAL_S = 15
HEARTBEAT_TIMEOUT_S = 20


class ConnectorManager:
    def __init__(self):
        self._ws: Optional[WebSocket] = None
        self._send_lock = asyncio.Lock()
        self._pending: dict[str, "asyncio.Future"] = {}
        self._acked: set = set()
        self._last_pong: float = 0.0
        self._heartbeat_task: Optional[asyncio.Task] = None

    def is_connected(self) -> bool:
        return self._ws is not None

    async def register(self, ws: WebSocket) -> None:
        if self._ws is not None:
            logger.warning(
                "A new connector authenticated while one was already registered -- "
                "replacing it (most likely a stale/half-open connection, not a real duplicate)."
            )
            await self._evict_current()
        self._ws = ws
        self._last_pong = time.monotonic()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))

    async def unregister(self, ws: WebSocket) -> None:
        if self._ws is ws:
            self._ws = None
            if self._heartbeat_task is not None:
                self._heartbeat_task.cancel()
                self._heartbeat_task = None
            self._fail_all_pending()

    async def _evict_current(self) -> None:
        old_ws = self._ws
        self._ws = None
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        if old_ws is not None:
            try:
                await old_ws.close(code=4000, reason="replaced by newer connection")
            except Exception:
                pass
        self._fail_all_pending()

    def _fail_all_pending(self) -> None:
        # Never leave an awaiting caller hanging forever just because the
        # connector went away mid-flight. Whether this is "safe" (nothing was
        # sent) or "ambiguous" (the connector had already dispatched the
        # request to Tally) depends on whether we saw a job_ack for it --
        # getting this backwards would silently reopen the double-entry risk
        # delivery_uncertain exists to prevent.
        for job_id, fut in list(self._pending.items()):
            if fut.done():
                continue
            if job_id in self._acked:
                fut.set_exception(requests.exceptions.Timeout(
                    "Connector disconnected after dispatching this request to Tally; delivery is unknown."
                ))
            else:
                fut.set_exception(requests.exceptions.ConnectTimeout(
                    "Connector disconnected before starting this request; nothing was sent."
                ))
        self._pending.clear()
        self._acked.clear()

    async def handle_incoming(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        msg_type = msg.get("type")
        if msg_type == MSG_JOB_ACK:
            job_id = msg.get("id")
            if job_id:
                self._acked.add(job_id)
        elif msg_type == MSG_JOB_RESULT:
            fut = self._pending.get(msg.get("id"))
            if fut and not fut.done():
                fut.set_result(msg)
        elif msg_type == MSG_PONG:
            self._last_pong = time.monotonic()
        elif msg_type == MSG_HELLO:
            logger.info(f"Connector connected: {msg.get('connector_version', 'unknown version')}")

    async def send_job(self, xml_data, timeout: float):
        if self._ws is None:
            raise requests.exceptions.ConnectTimeout("No Tally connector is currently connected.")

        # Call sites pass either str or bytes (both were valid for the old
        # requests.post(..., data=xml_data)); a JSON text frame can only
        # carry str, so normalize here rather than pushing this concern onto
        # every one of the ~20 call sites.
        if isinstance(xml_data, (bytes, bytearray)):
            xml_data = xml_data.decode("utf-8")

        job_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending[job_id] = fut
        try:
            async with self._send_lock:
                if self._ws is None:
                    raise requests.exceptions.ConnectTimeout(
                        "Connector disconnected before this request could be sent."
                    )
                await self._ws.send_text(json.dumps(build_job(job_id, xml_data)))
            try:
                result = await asyncio.wait_for(fut, timeout=timeout)
            except asyncio.TimeoutError:
                raise requests.exceptions.Timeout("Connector did not respond in time.")
        finally:
            self._pending.pop(job_id, None)
            self._acked.discard(job_id)

        if not result.get("ok"):
            error = result.get("error") or {}
            kind = error.get("kind")
            message = error.get("message", "")
            exc_cls = {
                ErrorKind.CONNECTION_ERROR.value: requests.exceptions.ConnectionError,
                ErrorKind.TIMEOUT.value: requests.exceptions.Timeout,
            }.get(kind, requests.exceptions.ConnectionError)
            raise exc_cls(message or f"Connector reported error: {kind}")

        # Local import to avoid a module-load-time circular import with
        # transport.py (which imports connector_manager from this module).
        from backend.connector.transport import TallyResponse
        return TallyResponse(status_code=result.get("status_code") or 200, text=result.get("body") or "")

    async def _heartbeat_loop(self, ws: WebSocket) -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL_S)
                if self._ws is not ws:
                    return
                ping_sent_at = time.monotonic()
                try:
                    await ws.send_text(json.dumps({"type": MSG_PING}))
                except Exception:
                    await self.unregister(ws)
                    return
                await asyncio.sleep(HEARTBEAT_TIMEOUT_S)
                if self._ws is not ws:
                    return
                if self._last_pong < ping_sent_at:
                    logger.warning("Connector missed its heartbeat -- treating as disconnected.")
                    try:
                        await ws.close(code=4008, reason="heartbeat timeout")
                    except Exception:
                        pass
                    await self.unregister(ws)
                    return
        except asyncio.CancelledError:
            pass


connector_manager = ConnectorManager()
