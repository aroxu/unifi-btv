#!/bin/sh
set -eu
[ "$(id -u)" -eq 0 ] || { echo 'Run as root.' >&2; exit 1; }
PURGE=no
case "${1:-}" in
    '') [ "$#" -eq 0 ] || exit 1 ;;
    --purge) [ "$#" -eq 1 ] || exit 1; PURGE=yes ;;
    *) echo 'Usage: ./uninstall.sh [--purge]' >&2; exit 1 ;;
esac
systemctl stop unifi-btv.service 2>/dev/null || {
    if systemctl is-active --quiet unifi-btv.service; then
        echo 'Cannot stop service; aborting removal.' >&2; exit 1
    fi
}
# Recover an override journal left by an unclean termination before removing files.
if [ -f /data/unifi-btv/unifi-btv.py ] && [ -f /data/unifi-btv/config.ini ]; then
    /usr/bin/python3 - <<'PY'
import runpy
from pathlib import Path
m = runpy.run_path('/data/unifi-btv/unifi-btv.py')
cfg = m['read_config']('/data/unifi-btv/config.ini')
override = m['VersionOverride'](Path(cfg['general']['state_path']).with_name('overrides.json'))
override.restore()
if override.saved:
    raise SystemExit('Restore incomplete; keep files and retry uninstall after resolving permissions.')
PY
fi
rm -f /data/on_boot.d/50-unifi-btv.sh /data/unifi-btv/unifi-btv.py
systemctl reset-failed unifi-btv.service 2>/dev/null || true
rm -f /run/unifi-btv/status.json /run/unifi-btv/status.lock /run/unifi-btv/overrides.json
if [ "$PURGE" = yes ]; then
    rm -f /data/unifi-btv/config.ini /data/unifi-btv/config.legacy.ini /data/unifi-btv/unifi-btv.log \
        /data/unifi-btv/unifi-btv.log.1 /data/unifi-btv/unifi-btv.log.2 \
        /data/unifi-btv/uninstall.sh
    rmdir /data/unifi-btv 2>/dev/null || true
fi
echo 'Removed keeper service and boot hook. Config/logs retained unless --purge; boot framework retained.'
