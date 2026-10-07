#!/bin/sh
# Local checkout or curl -fsSL .../install.sh | bash, on the UniFi OS host.
set -eu
BASE=/data/iptv-igmp-keeper
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
    STAGING=$(mktemp -d "${TMPDIR:-/tmp}/iptv-igmp-keeper.XXXXXXXX")
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
for file in src/iptv-igmp-keeper.py config/config.example.ini uninstall.sh unifi/50-iptv-igmp-keeper.sh; do
    [ -s "$SOURCE/$file" ] || { echo "Missing or empty source file: $file" >&2; exit 1; }
done
sh -n "$SOURCE/uninstall.sh"
sh -n "$SOURCE/unifi/50-iptv-igmp-keeper.sh"
if [ -n "$DOWNLOAD_ONLY" ]; then
    mv -- "$SOURCE" "$DOWNLOAD_ONLY"
    echo "Downloaded to $DOWNLOAD_ONLY; no service or gateway settings changed."
    exit 0
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
