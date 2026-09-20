import logging
import secrets
import time
from collections import defaultdict, deque

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.config import TALLY_CONNECTOR_SECRET
from backend.connector.manager import connector_manager

logger = logging.getLogger("connector")

router = APIRouter()

# Cheap insurance against scanning/brute-force bots hitting a genuinely
# public-internet-facing endpoint -- not meant as serious anti-DDoS, just
# enough that junk connection attempts don't spam logs or churn connections
# indefinitely. Single-process, in-memory: fine for a personal app with one
# real backend worker.
_RATE_LIMIT_WINDOW_S = 60
_RATE_LIMIT_MAX_ATTEMPTS = 10
_handshake_attempts: dict[str, deque] = defaultdict(deque)


def _is_rate_limited(client_ip: str) -> bool:
    now = time.monotonic()
    attempts = _handshake_attempts[client_ip]
    while attempts and now - attempts[0] > _RATE_LIMIT_WINDOW_S:
        attempts.popleft()
    attempts.append(now)
    return len(attempts) > _RATE_LIMIT_MAX_ATTEMPTS


@router.websocket("/ws/connector")
async def connector_ws(websocket: WebSocket):
    client_ip = websocket.client.host if websocket.client else "unknown"

    if _is_rate_limited(client_ip):
        await websocket.close(code=4429)
        return

    auth_header = websocket.headers.get("authorization", "")
    token = auth_header[len("Bearer "):].strip() if auth_header.startswith("Bearer ") else ""

    if not TALLY_CONNECTOR_SECRET or not secrets.compare_digest(token, TALLY_CONNECTOR_SECRET):
        logger.warning(f"Rejected connector handshake from {client_ip}: invalid or missing token.")
        # Reject before accept() -- the WS upgrade never completes for an
        # unauthenticated peer, so no data-plane channel exists even transiently.
        await websocket.close(code=4401)
        return

    await websocket.accept()
    await connector_manager.register(websocket)
    logger.info(f"Connector authenticated and connected from {client_ip}.")
    try:
        async for raw in websocket.iter_text():
            await connector_manager.handle_incoming(raw)
    except WebSocketDisconnect:
        pass
    finally:
        await connector_manager.unregister(websocket)
        logger.info(f"Connector disconnected ({client_ip}).")
