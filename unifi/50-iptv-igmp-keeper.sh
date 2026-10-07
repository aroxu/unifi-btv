#!/bin/sh
set -eu
BASE=/data/iptv-igmp-keeper
UNIT=iptv-igmp-keeper.service
[ -f "$BASE/keeper.py" ] && [ -f "$BASE/config.ini" ] || exit 1
# Idempotent when invoked again by the boot framework.
if systemctl is-active --quiet "$UNIT"; then
    exit 0
fi
systemctl reset-failed "$UNIT" 2>/dev/null || true
exec systemd-run --collect --unit="$UNIT" --description='Adaptive IPTV IGMP membership keeper' \
    --property=Type=simple --property=Restart=on-failure --property=RestartSec=10s \
    --property=TimeoutStopSec=20s --property=KillSignal=SIGTERM \
    --property=UMask=0077 \
    /usr/bin/python3 "$BASE/keeper.py" --config "$BASE/config.ini"
