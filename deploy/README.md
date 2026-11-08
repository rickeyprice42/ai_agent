# Ubuntu deployment

The production layout keeps the public landing page, public authenticated agent,
and private Tailscale maintenance endpoint separate:

- `https://avelin.tech/` serves `website/`;
- `https://www.avelin.tech/` redirects to the root domain;
- `https://app.avelin.tech/` serves the frontend and proxies `/api/` to FastAPI;
- `https://HOSTNAME.TAILNET.ts.net/` keeps the same agent available through Tailscale;
- FastAPI listens only on `127.0.0.1:8000`;
- the Tailscale nginx origin listens only on `127.0.0.1:8080`;
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
4. Fill `/etc/avelin/avelin.env` and keep it owned by `root:root` with mode `0600`.
   Put the existing provider-key file at `/etc/avelin/provider-keys.txt`, owned by
   `root:avelin` with mode `0640`, so the unprivileged service can read it without
   exposing it to other users. Never print either file's values.
5. Start the API and nginx, then publish the private origin:

   ```bash
   sudo systemctl restart avelin nginx
   sudo tailscale serve --bg http://127.0.0.1:8080
   tailscale serve status
   ```

## Public domain and HTTPS

Before enabling HTTPS, point these Cloudflare records at the server and keep them
`DNS only` for the initial certificate issuance:

| Type | Name | Value |
| --- | --- | --- |
| A | `@` | `78.40.199.9` |
| A | `www` | `78.40.199.9` |
| A | `app` | `78.40.199.9` |

After the authoritative nameservers have propagated and all three names resolve to
the server, run:

```bash
sudo bash /opt/avelin/current/deploy/enable_public_domain.sh
```

The script installs Certbot, exposes only the ACME challenge for the agent during
bootstrap, requests one certificate for all three names, installs the final HTTPS
configuration, and enables automatic renewal. Login and registration requests on
the public agent are rate-limited by nginx. Tailscale Serve is not changed.

Set `WEB_PUSH_VAPID_SUBJECT=https://app.avelin.tech` in
`/etc/avelin/avelin.env`, then restart Avelin. Existing Web Push subscriptions from
the Tailscale origin are not reusable; users need to enable notifications again on
the public origin.

## Closed test registration

Choose a private code of 8-64 ASCII letters or digits and set it through hidden
input. The command stores only its SHA-256 digest in the protected environment
file, never the code itself:

```bash
sudo /opt/avelin/venv/bin/python \
  /opt/avelin/current/scripts/set_registration_access_code.py \
  --env-file /etc/avelin/avelin.env
sudo systemctl restart avelin
```

The frontend then shows a closed-test dialog, while the API independently rejects
missing or incorrect codes. Never place the real code in Git or frontend files.

Passwords are stored only as salted PBKDF2 hashes and cannot be recovered. To
replace a forgotten password without deleting chats, run the interactive command
below. It reads the new password with hidden input and revokes existing sessions:

```bash
sudo -u avelin /opt/avelin/venv/bin/python \
  /opt/avelin/current/scripts/reset_user_password.py \
  --database /var/lib/avelin/avelin.sqlite3 \
  --login USERNAME_OR_EMAIL
```

## Verification

```bash
systemctl --no-pager --full status avelin nginx certbot.timer
curl --fail --silent http://127.0.0.1:8000/api/health
curl --fail --silent http://127.0.0.1:8080/api/health
curl --fail --silent https://avelin.tech/
curl --fail --silent https://app.avelin.tech/api/health
sudo -u avelin /opt/avelin/venv/bin/python - <<'PY'
import sqlite3
db = sqlite3.connect('file:/var/lib/avelin/avelin.sqlite3?mode=ro', uri=True)
print(db.execute('PRAGMA integrity_check').fetchone()[0])
PY
```

Keep the Cloudflare records as `DNS only` until origin HTTPS and long-running agent
responses are verified. Proxying can be evaluated later without changing nginx.
