#!/bin/sh
set -eu
[ "$(id -u)" -eq 0 ] || { echo 'Run as root.' >&2; exit 1; }
PURGE=no
case "${1:-}" in
    '') [ "$#" -eq 0 ] || exit 1 ;;
    --purge) [ "$#" -eq 1 ] || exit 1; PURGE=yes ;;
    *) echo 'Usage: ./uninstall.sh [--purge]' >&2; exit 1 ;;
esac
systemctl stop iptv-igmp-keeper.service 2>/dev/null || {
    if systemctl is-active --quiet iptv-igmp-keeper.service; then
        echo 'Cannot stop service; aborting removal.' >&2; exit 1
    fi
}
# Recover an override journal left by an unclean termination before removing files.
if [ -f /data/iptv-igmp-keeper/keeper.py ] && [ -f /data/iptv-igmp-keeper/config.ini ]; then
    /usr/bin/python3 - <<'PY'
import runpy
from pathlib import Path
m = runpy.run_path('/data/iptv-igmp-keeper/keeper.py')
cfg = m['read_config']('/data/iptv-igmp-keeper/config.ini')
override = m['VersionOverride'](Path(cfg['general']['state_path']).with_name('overrides.json'))
override.restore()
if override.saved:
    raise SystemExit('Restore incomplete; keep files and retry uninstall after resolving permissions.')
PY
fi
rm -f /data/on_boot.d/50-iptv-igmp-keeper.sh /data/iptv-igmp-keeper/keeper.py
systemctl reset-failed iptv-igmp-keeper.service 2>/dev/null || true
rm -f /run/iptv-igmp-keeper/status.json /run/iptv-igmp-keeper/status.lock /run/iptv-igmp-keeper/overrides.json
if [ "$PURGE" = yes ]; then
    rm -f /data/iptv-igmp-keeper/config.ini /data/iptv-igmp-keeper/keeper.log \
        /data/iptv-igmp-keeper/keeper.log.1 /data/iptv-igmp-keeper/keeper.log.2 \
        /data/iptv-igmp-keeper/uninstall.sh
    rmdir /data/iptv-igmp-keeper 2>/dev/null || true
fi
echo 'Removed keeper service and boot hook. Config/logs retained unless --purge; boot framework retained.'
