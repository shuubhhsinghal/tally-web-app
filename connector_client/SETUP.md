# Tally Connector -- setup (Windows)

Run this on the machine where Tally is actually running. It keeps a connection
open to the backend so the backend can talk to Tally without needing
Tailscale, port forwarding, or exposing Tally's HTTP interface to the internet.

## One-time setup

1. Install Python if you don't already have it: https://www.python.org/downloads/
   **Important**: on the first install screen, check the box that says
   **"Add python.exe to PATH"** (or "Add Python to PATH") before clicking Install.
   If you skip this, the auto-start step below won't work.
2. Open Command Prompt in this folder (in File Explorer, click the address bar,
   type `cmd`, press Enter) and install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Copy `connector_config.env.example` to a new file named `connector_config.env`
   in this same folder, and fill in:
   - `BACKEND_WS_URL` -- the backend's WebSocket address (given to you separately).
   - `CONNECTOR_TOKEN` -- the shared secret (given to you separately; treat it like a password).
   - `TALLY_LOCAL_URL` -- leave as-is unless Tally's HTTP gateway is on a different port than 9000.

## Running it

You have two ways to run it:

### Auto-start (recommended) -- runs invisibly, starts itself at every login

Double-click **`install_autostart.vbs`** in this folder. That's it, one time only.
It sets up Windows to launch the connector automatically and silently whenever
you log in (no window, nothing to remember), and starts it immediately so you
can confirm it's working right away.

- To check it's running: look at `connector.log` in this folder.
- To stop it: double-click **`stop_connector.vbs`**.
- To remove auto-start entirely: double-click **`uninstall_autostart.vbs`**.

### Manual (for testing/troubleshooting) -- runs in a visible window

```
python connector.py
```
Leave the window open -- closing it stops the connector. Useful when you want
to watch it work directly instead of checking the log file.

Either way, it automatically reconnects if the connection drops (network blip,
backend restart, laptop waking from sleep).

## Troubleshooting

- **"Could not find connector.py next to this file" when running install_autostart.vbs**:
  make sure all the files stayed together in the same folder after downloading/copying.
- **Setup failed, mentions Python/PATH**: Python wasn't added to PATH during install.
  Reinstall Python from python.org and check "Add Python to PATH" this time.
- **"BACKEND_WS_URL and CONNECTOR_TOKEN must both be set"** (visible if you run it
  manually): you haven't filled in `connector_config.env` yet, or it's still named
  `connector_config.env.example`.
- **Keeps reconnecting in a loop**: check `connector.log` for the actual error. A wrong
  `CONNECTOR_TOKEN` will be rejected by the backend every time (look for "rejected" in
  the log) -- double check it matches exactly.
- **Connected, but the web app still shows Tally as offline / requests fail**: make sure
  Tally itself is open and its HTTP/XML gateway is enabled on the port in
  `TALLY_LOCAL_URL` (Tally's own settings, not this script's).
- **Auto-start isn't starting it**: open Task Manager (Ctrl+Shift+Esc) → Startup tab,
  confirm "TallyConnector" is listed and enabled. You can also just re-run
  `install_autostart.vbs` -- it's safe to run again.
