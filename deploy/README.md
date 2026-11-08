# Ubuntu deployment

This deployment keeps the public landing page and the private agent separate:

- `http://SERVER_IP/` serves `website/` publicly through nginx;
- `https://HOSTNAME.TAILNET.ts.net/` serves the agent through Tailscale Serve;
- FastAPI listens only on `127.0.0.1:8000`;
- the private nginx origin listens only on `127.0.0.1:8080`;
- one Uvicorn worker owns SQLite and the background queues.

The source release belongs in `/opt/avelin/current`. Persistent private state belongs
in `/var/lib/avelin`; never place `.env`, API keys, SQLite, VAPID keys, chats or
workspace files in Git.

## First installation

1. Upload the current source tree and the locally generated `frontend/dist` to
   `/opt/avelin/current`.
2. Run `sudo bash /opt/avelin/current/deploy/install_ubuntu.sh`.
3. Copy the verified SQLite backup to `/var/lib/avelin/avelin.sqlite3`, copy
   `data/workspace/` to `/var/lib/avelin/workspace/`, and copy `data/web-push/` to
   `/var/lib/avelin/web-push/`. Set owner `avelin:avelin` and mode `0600` for private
   files.
4. Fill `/etc/avelin/avelin.env` and put the existing provider-key file at
   `/etc/avelin/provider-keys.txt`. Keep both mode `0600` and never print their values.
5. Start the API and nginx, then publish the private origin:

   ```bash
   sudo systemctl restart avelin nginx
   sudo tailscale serve --bg http://127.0.0.1:8080
   tailscale serve status
   ```

6. Set `WEB_PUSH_VAPID_SUBJECT` to that HTTPS URL. Put the same URL in
   `/opt/avelin/current/website/agent-config.js` as `window.AVELIN_AGENT_URL`, then
   restart `avelin`. The landing-page login button will open the private agent without
   exposing its API on the public IPv4 address.

## Verification

```bash
systemctl --no-pager --full status avelin nginx
curl --fail --silent http://127.0.0.1:8000/api/health
curl --fail --silent http://127.0.0.1:8080/api/health
curl --fail --silent http://127.0.0.1/
sudo -u avelin /opt/avelin/venv/bin/python - <<'PY'
import sqlite3
db = sqlite3.connect('file:/var/lib/avelin/avelin.sqlite3?mode=ro', uri=True)
print(db.execute('PRAGMA integrity_check').fetchone()[0])
PY
```

The landing page can be switched to HTTPS after a domain is pointed at the server.
Until then, keep authentication and Web Push on the Tailscale HTTPS origin.
