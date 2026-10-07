#!/bin/sh
# Run on the UniFi OS host, not a container. No network downloads.
set -eu
[ "$(id -u)" -eq 0 ] || { echo 'Run as root on the gateway.' >&2; exit 1; }
[ "$#" -eq 0 ] || { echo 'Usage: ./install.sh' >&2; exit 1; }
SOURCE=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
BASE=/data/iptv-igmp-keeper
for tool in systemctl systemd-run install; do
    command -v "$tool" >/dev/null || { echo "Missing dependency: $tool" >&2; exit 1; }
done
[ -x /usr/bin/python3 ] || { echo 'Requires /usr/bin/python3.' >&2; exit 1; }
/usr/bin/python3 -c 'import sys; assert sys.version_info >= (3, 8), "Python 3.8+ required"'
if [ ! -d /data/on_boot.d ] || ! systemctl is-enabled --quiet udm-boot.service; then
    echo 'Install/enable the unifi-common udm-boot framework first; see README.' >&2
    exit 1
fi
/usr/bin/python3 "$SOURCE/src/iptv-igmp-keeper.py" --config "$SOURCE/config/config.example.ini" --check-config
if [ -f "$BASE/config.ini" ]; then
    /usr/bin/python3 "$SOURCE/src/iptv-igmp-keeper.py" --config "$BASE/config.ini" --check-config
fi
# Stop before replacing code; SIGTERM restores owned sysctl values.
systemctl stop iptv-igmp-keeper.service 2>/dev/null || {
    if systemctl is-active --quiet iptv-igmp-keeper.service; then
        echo 'Cannot stop existing service; aborting.' >&2; exit 1
    fi
}
install -d -m 700 "$BASE"
install -m 755 "$SOURCE/src/iptv-igmp-keeper.py" "$BASE/keeper.py"
if [ ! -f "$BASE/config.ini" ]; then
    install -m 600 "$SOURCE/config/config.example.ini" "$BASE/config.ini"
fi
install -m 755 "$SOURCE/uninstall.sh" "$BASE/uninstall.sh"
install -m 755 "$SOURCE/unifi/50-iptv-igmp-keeper.sh" /data/on_boot.d/50-iptv-igmp-keeper.sh
/data/on_boot.d/50-iptv-igmp-keeper.sh
systemctl is-active --quiet iptv-igmp-keeper.service || {
    echo 'Service did not start; inspect journalctl -u iptv-igmp-keeper.' >&2; exit 1
}
echo 'Installed. Existing config preserved. Use --status after at least 5 seconds.'
