# Tally Connector -- setup

Run this on the machine where Tally is actually running. It keeps a connection
open to the backend so the backend can talk to Tally without needing
Tailscale, port forwarding, or exposing Tally's HTTP interface to the internet.

## One-time setup

1. Install Python 3.9+ if you don't already have it: https://www.python.org/downloads/
2. Open a terminal in this folder and install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Copy `connector_config.env.example` to `connector_config.env` and fill in:
   - `BACKEND_WS_URL` -- the backend's WebSocket address (given to you separately).
   - `CONNECTOR_TOKEN` -- the shared secret (given to you separately; treat it like a password).
   - `TALLY_LOCAL_URL` -- leave as-is unless Tally's HTTP gateway is on a different port than 9000.

## Running it

```
python connector.py
```

Leave this window open -- it needs to keep running for the web app to reach Tally.
It automatically reconnects if the connection drops (network blip, backend restart,
your computer waking from sleep), and logs everything to `connector.log` in this folder.

To stop it, press Ctrl+C.

## Troubleshooting

- **"BACKEND_WS_URL and CONNECTOR_TOKEN must both be set"**: you haven't filled in
  `connector_config.env` yet (or it's missing -- make sure you renamed the `.example`
  copy, not just left it as `connector_config.env.example`).
- **Keeps reconnecting in a loop**: check `connector.log` for the actual error. A wrong
  `CONNECTOR_TOKEN` will be rejected by the backend every time (look for "rejected" in
  the log) -- double check it matches exactly.
- **Connected, but the web app still shows Tally as offline / requests fail**: make sure
  Tally itself is open and its HTTP/XML gateway is enabled on the port in
  `TALLY_LOCAL_URL` (Tally's own settings, not this script's).
