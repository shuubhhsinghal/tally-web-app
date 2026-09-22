import os
from dotenv import load_dotenv

load_dotenv()

# Shared secret the /ws/connector endpoint requires from the connector script
# before it will accept the WebSocket upgrade. This is the only thing standing
# between the internet and a channel that can push arbitrary XML at Tally, so
# it must be set for real deployments -- there is deliberately no default.
TALLY_CONNECTOR_SECRET = os.environ.get("TALLY_CONNECTOR_SECRET", "")
