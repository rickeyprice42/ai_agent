#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/install_ubuntu.sh" >&2
  exit 1
fi

APP_ROOT=/opt/avelin/current
STATE_ROOT=/var/lib/avelin
VENV_ROOT=/opt/avelin/venv

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip nginx ca-certificates curl rsync

if ! id avelin >/dev/null 2>&1; then
  useradd --system --home-dir "$STATE_ROOT" --create-home --shell /usr/sbin/nologin avelin
fi

install -d -o avelin -g avelin -m 0750 "$STATE_ROOT" "$STATE_ROOT/workspace" "$STATE_ROOT/web-push" "$APP_ROOT/.logs"
python3 -m venv "$VENV_ROOT"
"$VENV_ROOT/bin/python" -m pip install --upgrade pip
"$VENV_ROOT/bin/python" -m pip install -r "$APP_ROOT/requirements.server.txt"
PLAYWRIGHT_BROWSERS_PATH=/opt/avelin/playwright "$VENV_ROOT/bin/python" -m playwright install --with-deps chromium
chown -R avelin:avelin /opt/avelin/playwright "$APP_ROOT/.logs"

install -d -o root -g avelin -m 0750 /etc/avelin
if [[ ! -f /etc/avelin/avelin.env ]]; then
  install -o root -g root -m 0600 "$APP_ROOT/deploy/avelin.env.example" /etc/avelin/avelin.env
fi
install -m 0644 "$APP_ROOT/deploy/avelin.service" /etc/systemd/system/avelin.service
install -m 0644 "$APP_ROOT/deploy/nginx-avelin.conf" /etc/nginx/sites-available/avelin
ln -sfn /etc/nginx/sites-available/avelin /etc/nginx/sites-enabled/avelin
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl daemon-reload
systemctl enable nginx avelin

echo "Base installation complete. Put data under $STATE_ROOT, fill /etc/avelin/avelin.env, then start Avelin."
