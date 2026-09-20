import os
from dotenv import load_dotenv

load_dotenv()

if os.environ.get("TESTING") == "true":
    # Tests must never be able to reach the real Tally instance, even if a
    # test forgets to mock requests.post/requests.get -- point at a reserved
    # loopback port that's essentially guaranteed to have nothing listening
    # on it, so an unmocked call fails fast (connection refused) instead of
    # silently succeeding against the real production Tally. Confirmed via a
    # live test earlier that a forgotten mock really does reach production
    # Tally otherwise (only safe because the fake ledger name didn't exist
    # there) -- this closes that gap for good rather than relying on every
    # test remembering to mock requests itself.
    TALLY_URL = "http://127.0.0.1:1"
else:
    TALLY_URL = os.environ.get("TALLY_URL", "http://100.107.220.58:9000")

# Shared secret the /ws/connector endpoint requires from the connector script
# before it will accept the WebSocket upgrade. This is the only thing standing
# between the internet and a channel that can push arbitrary XML at Tally, so
# it must be set for real deployments -- there is deliberately no default.
TALLY_CONNECTOR_SECRET = os.environ.get("TALLY_CONNECTOR_SECRET", "")
