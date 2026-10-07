#!/bin/sh
set -eu
BASE=/data/unifi-btv
UNIT=unifi-btv.service
[ -f "$BASE/unifi-btv.py" ] && [ -f "$BASE/config.ini" ] || exit 1
# Idempotent when invoked again by the boot framework.
if systemctl is-active --quiet "$UNIT"; then
    exit 0
fi
systemctl reset-failed "$UNIT" 2>/dev/null || true
exec systemd-run --collect --unit="$UNIT" --description='unifi-btv adaptive IGMP membership keeper' \
    --property=Type=simple --property=Restart=on-failure --property=RestartSec=10s \
    --property=TimeoutStopSec=20s --property=KillSignal=SIGTERM \
    --property=UMask=0077 \
    /usr/bin/python3 "$BASE/unifi-btv.py" --config "$BASE/config.ini"
