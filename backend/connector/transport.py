"""Drop-in replacement for `requests.post(TALLY_URL, ...)` / `requests.get(TALLY_URL, ...)`.

Every existing call site's exception-handling (`except requests.exceptions.
ConnectTimeout / Timeout / ConnectionError`) and its use of the response
object (`.text`, `.status_code`, `.raise_for_status()`) keeps working
unchanged -- only the one line that dials out directly needs to change to
`await tally_transport.post(xml_data, timeout=...)`.
"""

from dataclasses import dataclass

import requests

from backend.connector.manager import connector_manager


@dataclass
class TallyResponse:
    status_code: int
    text: str

    def raise_for_status(self):
        if not (200 <= self.status_code < 300):
            raise requests.exceptions.HTTPError(f"{self.status_code} error from Tally connector")


class TallyConnectorTransport:
    async def post(self, xml_data, timeout: float = 10.0) -> TallyResponse:
        return await connector_manager.send_job(xml_data, timeout=timeout)

    async def get(self, timeout: float = 3.0) -> TallyResponse:
        # The only historical use of a bare GET was a connectivity ping with
        # the response body discarded -- callers should prefer
        # connector_manager.is_connected() instead (no round-trip needed),
        # but this is kept for parity with the old requests.get(TALLY_URL) shape.
        return await connector_manager.send_job(xml_data=None, timeout=timeout)


tally_transport = TallyConnectorTransport()
