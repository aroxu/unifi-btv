#!/bin/sh
# Local checkout or curl -fsSL .../install.sh | bash, on the UniFi OS host.
set -eu
BASE=/data/unifi-btv
LEGACY_BASE=/data/iptv-igmp-keeper
DOWNLOAD_ONLY=''
STAGING=''
SOURCE=''
case "${1:-}" in
    '') [ "$#" -eq 0 ] || { echo 'Usage: install.sh [--download-only NEW_DIRECTORY]' >&2; exit 1; } ;;
    --download-only)
        if [ "$#" -ne 2 ] || [ -z "$2" ]; then
            echo 'Usage: install.sh --download-only NEW_DIRECTORY' >&2; exit 1
        fi
        DOWNLOAD_ONLY=$2
        if [ -e "$DOWNLOAD_ONLY" ] || [ -L "$DOWNLOAD_ONLY" ]; then
            echo 'Download directory already exists; choose a new directory.' >&2; exit 1
        fi
        ;;
    *) echo 'Usage: install.sh [--download-only NEW_DIRECTORY]' >&2; exit 1 ;;
esac
cleanup() {
    if [ -n "$STAGING" ]; then
        rm -rf -- "$STAGING"
    fi
}
trap cleanup 0
trap 'exit 1' HUP INT TERM

if [ -z "$DOWNLOAD_ONLY" ]; then
    [ "$(id -u)" -eq 0 ] || { echo 'Run as root on the gateway.' >&2; exit 1; }
    for tool in systemctl systemd-run install; do
        command -v "$tool" >/dev/null || { echo "Missing dependency: $tool" >&2; exit 1; }
    done
    [ -x /usr/bin/python3 ] || { echo 'Requires /usr/bin/python3.' >&2; exit 1; }
    /usr/bin/python3 -c 'import sys; assert sys.version_info >= (3, 8), "Python 3.8+ required"'
    if [ ! -d /data/on_boot.d ] || ! systemctl is-enabled --quiet udm-boot.service; then
        echo 'Install/enable the unifi-common udm-boot framework first: https://github.com/unifi-utilities/unifi-common' >&2
        exit 1
    fi
    # $0 is a script path for local execution, but a shell name when piped.
    if [ -f "$0" ]; then
        SOURCE=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
    fi
fi

if [ -z "$SOURCE" ]; then
    for tool in curl tar mktemp; do
        command -v "$tool" >/dev/null || { echo "Missing download dependency: $tool" >&2; exit 1; }
    done
    STAGING=$(mktemp -d "${TMPDIR:-/tmp}/unifi-btv.XXXXXXXX")
    mkdir "$STAGING/source"
    echo 'Downloading aroxu/unifi-btv (main)...'
    # One archive keeps all payload files from the same repository snapshot.
    curl -fsSL --retry 3 --connect-timeout 15 --max-time 120 \
        https://codeload.github.com/aroxu/unifi-btv/tar.gz/refs/heads/main \
        -o "$STAGING/source.tar.gz"
    tar -xzf "$STAGING/source.tar.gz" -C "$STAGING/source" --strip-components=1
    SOURCE=$STAGING/source
fi

# Check the entire payload before changing an existing installation.
for file in src/unifi-btv.py config/config.example.ini uninstall.sh unifi/50-unifi-btv.sh; do
    [ -s "$SOURCE/$file" ] || { echo "Missing or empty source file: $file" >&2; exit 1; }
done
sh -n "$SOURCE/uninstall.sh"
sh -n "$SOURCE/unifi/50-unifi-btv.sh"
if [ -n "$DOWNLOAD_ONLY" ]; then
    mv -- "$SOURCE" "$DOWNLOAD_ONLY"
    echo "Downloaded to $DOWNLOAD_ONLY; no service or gateway settings changed."
    exit 0
fi

/usr/bin/python3 "$SOURCE/src/unifi-btv.py" --config "$SOURCE/config/config.example.ini" --check-config
CONFIG_SOURCE=$SOURCE/config/config.example.ini
MIGRATING=no
if [ -f "$BASE/config.ini" ]; then
    CONFIG_SOURCE=$BASE/config.ini
elif [ -f "$LEGACY_BASE/config.ini" ]; then
    if [ -z "$STAGING" ]; then
        STAGING=$(mktemp -d "${TMPDIR:-/tmp}/unifi-btv.XXXXXXXX")
    fi
    /usr/bin/python3 - "$SOURCE/src/unifi-btv.py" "$LEGACY_BASE/config.ini" "$STAGING/config.ini" <<'MIGRATE'
import runpy
import sys
module = runpy.run_path(sys.argv[1])
module['migrate_legacy_config'](sys.argv[2], sys.argv[3])
MIGRATE
    CONFIG_SOURCE=$STAGING/config.ini
    MIGRATING=yes
fi
/usr/bin/python3 "$SOURCE/src/unifi-btv.py" --config "$CONFIG_SOURCE" --check-config

# Stop both names before replacing code or removing the legacy boot hook.
for unit in unifi-btv.service iptv-igmp-keeper.service; do
    systemctl stop "$unit" 2>/dev/null || {
        if systemctl is-active --quiet "$unit"; then
            echo "Cannot stop $unit; aborting." >&2; exit 1
        fi
    }
done
# Also recover a same-boot journal left by an unclean legacy termination.
/usr/bin/python3 - "$SOURCE/src/unifi-btv.py" "$LEGACY_BASE/config.ini" <<'RESTORE'
import configparser
from pathlib import Path
import runpy
import sys
module = runpy.run_path(sys.argv[1])
legacy = configparser.ConfigParser(interpolation=None)
legacy.read(sys.argv[2])
state_path = legacy.get('general', 'state_path', fallback='/run/iptv-igmp-keeper/status.json')
override = module['VersionOverride'](Path(state_path).with_name('overrides.json'))
if override.saved:
    override.restore()
    if override.saved:
        raise SystemExit('Legacy IGMP version restore incomplete; resolve it before migrating.')
RESTORE
install -d -m 700 "$BASE"
install -m 755 "$SOURCE/src/unifi-btv.py" "$BASE/unifi-btv.py"
if [ ! -f "$BASE/config.ini" ]; then
    install -m 600 "$CONFIG_SOURCE" "$BASE/config.ini"
fi
if [ "$MIGRATING" = yes ]; then
    if [ ! -f "$BASE/config.legacy.ini" ]; then
        install -m 600 "$LEGACY_BASE/config.ini" "$BASE/config.legacy.ini"
    fi
    for suffix in '' .1 .2; do
        if [ -f "$LEGACY_BASE/keeper.log$suffix" ] && [ ! -e "$BASE/unifi-btv.log$suffix" ]; then
            install -m 600 "$LEGACY_BASE/keeper.log$suffix" "$BASE/unifi-btv.log$suffix"
        fi
    done
fi
install -m 755 "$SOURCE/uninstall.sh" "$BASE/uninstall.sh"
install -m 755 "$SOURCE/unifi/50-unifi-btv.sh" /data/on_boot.d/50-unifi-btv.sh
rm -f /data/on_boot.d/50-iptv-igmp-keeper.sh
/data/on_boot.d/50-unifi-btv.sh
systemctl is-active --quiet unifi-btv.service || {
    echo 'Service did not start; inspect journalctl -u unifi-btv.' >&2; exit 1
}
# Keep the legacy config/logs as backups; remove obsolete launchers after success.
rm -f "$LEGACY_BASE/keeper.py" "$LEGACY_BASE/uninstall.sh"
systemctl reset-failed iptv-igmp-keeper.service 2>/dev/null || true
echo 'unifi-btv installed. Existing config preserved; legacy config migrated when needed.'
