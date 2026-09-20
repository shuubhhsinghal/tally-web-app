"""Message shapes exchanged over the /ws/connector WebSocket.

The connector script (running on the Tally machine) and this backend agree
on this wire format. Keep it a plain dict/JSON contract (no pydantic model
required on the connector side) so the connector script has zero
backend-specific dependencies beyond `websockets` + `requests`.
"""

from enum import Enum


class ErrorKind(str, Enum):
    # Connector's own local call to Tally never even connected (Tally isn't
    # running / its HTTP gateway is off) -- as safe as "nothing was ever sent".
    CONNECTION_ERROR = "connection_error"
    # Connector's local call connected but Tally never replied in time --
    # AMBIGUOUS: Tally may have processed the request before the reply was lost.
    TIMEOUT = "timeout"
    OTHER = "other"


MSG_JOB = "job"
MSG_JOB_ACK = "job_ack"
MSG_JOB_RESULT = "job_result"
MSG_HELLO = "hello"
MSG_PING = "ping"
MSG_PONG = "pong"


def build_job(job_id: str, xml_data):
    return {"type": MSG_JOB, "id": job_id, "xml": xml_data}


def build_job_result_ok(job_id: str, status_code: int, body: str):
    return {"type": MSG_JOB_RESULT, "id": job_id, "ok": True, "status_code": status_code, "body": body}


def build_job_result_error(job_id: str, kind: str, message: str = ""):
    return {"type": MSG_JOB_RESULT, "id": job_id, "ok": False, "error": {"kind": kind, "message": message}}
