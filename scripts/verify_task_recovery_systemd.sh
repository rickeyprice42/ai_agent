#!/usr/bin/env bash
# Root-only fixture. Creates one temporary unit and a new isolated state folder.
# Production Avelin is never stopped or reconfigured.
set -Eeuo pipefail
code=${1:?absolute candidate code directory required}
state=${2:?new temporary state directory required}
[[ $(id -u) == 0 && "$code" == /tmp/avelin-ave75-recovery-code-* && "$state" == /tmp/avelin-ave75-recovery-state-* ]]
[[ ! -e "$state" && -f "$code/scripts/verify_task_recovery.py" ]]
unit=avelin-ave75-recovery-qa.service
unit_path=/run/systemd/system/$unit
[[ ! -e "$unit_path" ]]
cleanup() {
    systemctl stop "$unit" >/dev/null 2>&1 || true
    if [[ -f "$unit_path" ]]; then unlink "$unit_path"; fi
    systemctl daemon-reload
}
trap cleanup EXIT
install -d -o avelin -g avelin -m 0750 "$state"
cat > "$unit_path" <<EOF
[Unit]
Description=Avelin isolated AVE-75 recovery acceptance fixture
[Service]
Type=simple
User=avelin
WorkingDirectory=$code
ExecStart=/opt/avelin/venv/bin/python $code/scripts/verify_task_recovery.py service --output $state
KillMode=control-group
TimeoutStopSec=5
Restart=no
EOF
systemctl daemon-reload
systemctl start "$unit"
for attempt in {1..100}; do
    [[ ! -f "$state/ready.json" ]] || break
    sleep .05
done
test -f "$state/ready.json"
runuser -u avelin -- /opt/avelin/venv/bin/python "$code/scripts/verify_task_recovery.py" inspect --output "$state"
systemctl stop "$unit"
systemctl start "$unit"
for attempt in {1..200}; do
    [[ ! -f "$state/result.json" ]] || break
    sleep .05
done
if [[ ! -f "$state/result.json" ]]; then
    journalctl -u "$unit" -n 40 --no-pager
    exit 1
fi
cat "$state/result.json"
