#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/enable_public_domain.sh" >&2
  exit 1
fi

APP_ROOT=/opt/avelin/current
ACME_ROOT=/var/www/letsencrypt
BOOTSTRAP_CONFIG="$APP_ROOT/deploy/nginx-avelin.conf"
HTTPS_CONFIG="$APP_ROOT/deploy/nginx-avelin-https.conf"

for required in "$BOOTSTRAP_CONFIG" "$HTTPS_CONFIG"; do
  if [[ ! -f "$required" ]]; then
    echo "Missing deployment file: $required" >&2
    exit 1
  fi
done

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y certbot

install -d -o www-data -g www-data -m 0755 "$ACME_ROOT"
install -m 0644 "$BOOTSTRAP_CONFIG" /etc/nginx/sites-available/avelin
ln -sfn /etc/nginx/sites-available/avelin /etc/nginx/sites-enabled/avelin
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

certbot certonly \
  --webroot \
  --webroot-path "$ACME_ROOT" \
  --cert-name avelin.tech \
  --domain avelin.tech \
  --domain www.avelin.tech \
  --domain app.avelin.tech \
  --non-interactive \
  --agree-tos \
  --register-unsafely-without-email

install -m 0644 "$HTTPS_CONFIG" /etc/nginx/sites-available/avelin
nginx -t
systemctl reload nginx
systemctl enable --now certbot.timer

echo "Public HTTPS enabled: https://avelin.tech and https://app.avelin.tech"
