"""
Tally Connector -- run this on the machine where Tally is running.

Keeps an outbound WebSocket connection open to the backend so the backend can
run requests against Tally without Tally's HTTP interface ever being exposed
to the internet or routed over a flaky VPN hop. This script only ever makes
requests to Tally on THIS machine (http://localhost:9000 by default) -- it
never listens for inbound connections itself.

Setup: see SETUP.md next to this file.
"""

import asyncio
import json
import logging
import os
import random
import sys

import requests
import websockets
import websockets.exceptions
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "connector_config.env"))

BACKEND_WS_URL = os.environ.get("BACKEND_WS_URL", "")
CONNECTOR_TOKEN = os.environ.get("CONNECTOR_TOKEN", "")
TALLY_LOCAL_URL = os.environ.get("TALLY_LOCAL_URL", "http://localhost:9000")

# Generous local timeouts -- the backend's own per-job wait (based on the
# timeout it asked for) is what actually governs how long a caller waits;
# these just need to be long enough to never cut off a real Tally response.
CONNECT_TIMEOUT_S = 5
READ_TIMEOUT_S = 150
MAX_MESSAGE_SIZE = 50 * 1024 * 1024  # 50MB -- matches the backend's --ws-max-size

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "connector.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("connector")


def run_tally_request(xml_data):
    """Blocking call, always run via asyncio.to_thread -- never call this
    directly from the event loop."""
    try:
        if xml_data is None:
            resp = requests.get(TALLY_LOCAL_URL, timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S))
        else:
            resp = requests.post(
                TALLY_LOCAL_URL,
                data=xml_data,
                headers={"Content-Type": "text/xml"},
                timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S),
            )
        return {"ok": True, "status_code": resp.status_code, "body": resp.text}
    except requests.exceptions.Timeout:
        # Connected to Tally locally, but it never replied in time --
        # ambiguous from the backend's point of view (Tally may have already
        # processed the request), reported as such via error.kind="timeout".
        return {"ok": False, "error": {"kind": "timeout", "message": "Tally did not respond in time."}}
    except requests.exceptions.RequestException as e:
        # Never even connected locally (Tally isn't running / its HTTP
        # gateway is off) -- as safe as "nothing was sent".
        return {"ok": False, "error": {"kind": "connection_error", "message": str(e)}}


async def process_jobs(ws, job_queue: "asyncio.Queue"):
    """Processes jobs strictly one at a time. Tally's XML import gateway is
    not documented as safe for concurrent requests, so this is the one place
    that actually needs to enforce sequential processing, regardless of how
    many things the backend might dispatch."""
    while True:
        job = await job_queue.get()
        job_id = job["id"]
        xml_data = job.get("xml")
        try:
            # Sent the instant we start, before the outcome is known -- this
            # is what lets the backend tell "nothing was sent" apart from
            # "sent, but the reply never made it back" if this connection
            # drops before job_result goes out.
            await ws.send(json.dumps({"type": "job_ack", "id": job_id}))
            result = await asyncio.to_thread(run_tally_request, xml_data)
            result["type"] = "job_result"
            result["id"] = job_id
            await ws.send(json.dumps(result))
            if result["ok"]:
                logger.info(f"Job {job_id}: Tally responded ({result['status_code']}).")
            else:
                logger.warning(f"Job {job_id}: {result['error']['kind']} -- {result['error']['message']}")
        except Exception as e:
            logger.error(f"Job {job_id}: unexpected error while processing -- {e}")
        finally:
            job_queue.task_done()


async def receive_loop(ws, job_queue: "asyncio.Queue"):
    async for raw in ws:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        msg_type = msg.get("type")
        if msg_type == "job":
            await job_queue.put(msg)
        elif msg_type == "ping":
            await ws.send(json.dumps({"type": "pong"}))
        elif msg_type == "hello":
            pass  # server doesn't currently send this, reserved


async def connect_once():
    headers = {"Authorization": f"Bearer {CONNECTOR_TOKEN}"}
    async with websockets.connect(
        BACKEND_WS_URL,
        additional_headers=headers,
        ping_interval=20,
        ping_timeout=20,
        max_size=MAX_MESSAGE_SIZE,
    ) as ws:
        logger.info("Connected to backend.")
        await ws.send(json.dumps({"type": "hello", "connector_version": "1.0.0"}))
        job_queue: asyncio.Queue = asyncio.Queue()
        processor = asyncio.create_task(process_jobs(ws, job_queue))
        try:
            await receive_loop(ws, job_queue)
        finally:
            processor.cancel()


async def main():
    if not BACKEND_WS_URL or not CONNECTOR_TOKEN:
        logger.error(
            "BACKEND_WS_URL and CONNECTOR_TOKEN must both be set in connector_config.env -- see SETUP.md."
        )
        input("Press Enter to exit...")
        sys.exit(1)

    backoff = 1
    while True:
        try:
            await connect_once()
            backoff = 1  # reset after a clean session, so a later drop starts backing off from scratch
        except (websockets.exceptions.WebSocketException, OSError) as e:
            logger.warning(f"Disconnected from backend: {e}")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")

        delay = backoff * (0.8 + 0.4 * random.random())
        logger.info(f"Reconnecting in {delay:.1f}s...")
        await asyncio.sleep(delay)
        backoff = min(backoff * 2, 30)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nConnector stopped.")
