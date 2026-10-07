# unifi-iptv-igmp-keeper

[English](README.md) | [한국어](README.ko.md)

Repository: [aroxu/unifi-btv](https://github.com/aroxu/unifi-btv)

A conservative, adaptive IPv4 multicast IPTV membership keeper for Linux-based
UniFi OS / UCG gateways. Python 3.8+, standard library only. MIT licensed.

This is a workaround layered on an **existing, working IGMP proxy/multicast routing
configuration**. It does not configure IPTV VLANs, DHCP, firewall rules, IGMP proxy,
or multicast routes. It does not implement IGMPv3 source filtering or IPv6 MLD.

**Release status: initial implementation, locally tested; gateway validation pending.**
The original fixed-configuration workaround worked with SK Broadband B tv. The new
adaptive daemon has not yet been validated on that gateway or across UniFi firmware.
See [the B tv field report](docs/skb-btv.md) and [validation details](docs/validation.md).

## How it works

1. Read `/proc/net/ip_mr_vif` and `/proc/net/ip_mr_cache`. Choose an upstream only
   when multicast routes have one unambiguous input interface, and derive downstream
   interfaces from their output VIFs. Explicit interface overrides are supported.
2. Capture IGMP on those interfaces with a kernel packet filter. Track downstream
   subscriptions separately by interface, client MAC and group. A leave from one
   client does not remove another client's subscription.
3. In `auto`, require a recent **upstream IGMPv2 Query** before applying upstream
   `force_igmp_version=2` and sending periodic IGMPv2 Membership Reports. The daemon
   changes only that upstream interface's setting and journals the original value.
4. Refresh only ASM groups with a fresh downstream report, a matching route output,
   and a recently increasing multicast route packet counter. Groups in `224.0.0/24`
   and `232/8` are never refreshed. No upstream Leave is synthesized.
5. When permitted to operate in v2, send a downstream General Query only where
   traffic is active and both client reports and external queries have been absent
   for 150 seconds. The fallback is rate limited per interface and can be disabled.
6. Re-scan every five seconds. Recreated interfaces, IPv4/MAC changes and topology
   changes reset observations and capture sockets. Do not reuse stale client evidence.

### Safety boundaries

- `force_version = auto`: require observed upstream v2 Query evidence (400s lifetime).
- `force_version = v2`: explicit ASM provider override; permits starting without a Query.
- `force_version = off`: observe only; no reports, queries or version enforcement.
- **Both `auto` and `v2` stop when upstream v1/v3 Queries or downstream source-filter/SSM
  evidence are present.** The original owned sysctl value is restored. Mixed-service
  interfaces are blocked as a whole because version enforcement is interface-wide.
- IGMPv3 `EXCLUDE {}` represents ASM and can be tracked. Source-bearing records,
  including ALLOW/BLOCK changes, block intervention; the daemon does not attempt
  incomplete IGMPv3 state reconstruction. Empty INCLUDE removes that client's entry.
- Evidence is observational, not proof of provider capability. Start in dry-run and
  observe at least several Query cycles. A missed packet or an unseen client remains
  a limitation. On IGMPv3/SSM providers use `off` and fix the native proxy configuration.
- No default-route guessing, no forced downstream v2, no global sysctl changes.
- Ambiguous multi-upstream routes are idle until configured explicitly. The Linux
  default multicast table/network namespace is supported; VRFs and alternate tables
  require a different implementation.

## Requirements

Run on the UniFi OS **host**, as root for live capture and intervention:

- Linux with IPv4 multicast routing, `/proc`, `/sys`, AF_PACKET and classic BPF.
- `/usr/bin/python3` 3.8 or later; `systemd-run`, `systemctl`, POSIX shell and `install`.
- For one-line installation: `curl`, `tar`, `mktemp` and `bash`.
- An IPv4 address on each selected upstream/downstream interface.
- An enabled `udm-boot.service` running `/data/on_boot.d` scripts, from
  [unifi-utilities/unifi-common](https://github.com/unifi-utilities/unifi-common).

Install and verify the boot framework according to its own documentation first.
This repository **does not download or install that framework**, nor does creating
`/data/on_boot.d` alone make scripts run at boot. Firmware upgrades can change these
prerequisites; verify them again after upgrades.

## Inspect before installing

Copy this repository to your gateway, enter its directory, and run:

```sh
python3 src/iptv-igmp-keeper.py --config config/config.example.ini --check-config
python3 src/iptv-igmp-keeper.py --config config/config.example.ini --discover
python3 src/iptv-igmp-keeper.py --config config/config.example.ini --dry-run --once --observe-seconds 450
```

`--discover` is read-only and needs no raw socket. `--dry-run` uses live capture when
interfaces are found, but sends no packets, changes no sysctls, and writes no logs,
status files or journals. It prints decisions to stderr and the final JSON to stdout.
A default `--once` lasts 15s; it may be too short to observe a periodic upstream Query.
Without `--dry-run`, `--once` can intervene, then restores owned settings on exit.

If discovery is idle, start a channel and retry. If the initial join itself fails,
copy the example configuration and set known interface names explicitly:

```ini
[interfaces]
upstream = eth4
downstream = br935
```

These are **examples**, not UniFi defaults. Never infer interface roles from names alone.
Auto cannot learn a topology from nonexistent routes. Setting overrides still does
not allow membership refresh without actual routes, traffic and client evidence.

## Install / update

One-line install/update in a **root SSH session on the gateway**, after the on-boot
framework and requirements above are in place:

```sh
curl -fsSL https://raw.githubusercontent.com/aroxu/unifi-btv/main/install.sh | bash
```

The piped installer downloads one archive of `main`, validates its required files,
then installs and starts the daemon. It cleans up temporary files on success/failure
and preserves an existing configuration. Missing prerequisites or a failed download
abort before replacing the installation. `main` tracks the latest code.

To download the source for inspection without installing (no root required):

```sh
curl -fsSL https://raw.githubusercontent.com/aroxu/unifi-btv/main/install.sh | bash -s -- --download-only ./unifi-btv-source
```

Choose a destination that does not already exist. You can then inspect the source,
run the dry-run commands above from that directory, and install locally.

From an existing checkout:

```sh
sudo sh install.sh
sudo python3 /data/iptv-igmp-keeper/keeper.py --config /data/iptv-igmp-keeper/config.ini --status
systemctl status iptv-igmp-keeper.service --no-pager
```

The installer preserves existing `/data/iptv-igmp-keeper/config.ini`, installs the
boot hook and starts a transient systemd service. Re-running it updates code.
Edit that config and run `systemctl restart iptv-igmp-keeper.service` to apply changes.
The boot hook recreates the transient service each boot; do not `systemctl enable`
this transient unit. It restarts after failures and restores owned settings on SIGTERM.

`--config` must be supplied to manual invocations to read the installed configuration;
omitting it selects built-in defaults. Invalid settings fail validation.

## Timing, logs and status

Automatic refresh = one quarter of the smallest readable downstream bridge
`multicast_membership_interval`, clamped to **10–60 seconds**. Sysfs clock ticks are
converted using `SC_CLK_TCK`. With no bridge timer, assume 260s and refresh at 60s.
An explicit `refresh_interval` allows 10–120s. This is a bridge-based heuristic,
not measurement of the ISP's upstream timeout; reduce it for a measured shorter timeout.

Client subscriptions expire after 300s; traffic evidence after 90s. The brief traffic
grace allows recovery of a recently stalled stream. A long-dead stream is deliberately
not resurrected without new traffic. v2 report suppression can hide individual clients;
this tracker is conservative and is not an authoritative subscriber database.

Logs: `/data/iptv-igmp-keeper/keeper.log`, 512 KiB per file, two backups (about 1.5 MiB).
Only changes, fallback queries, errors and hourly heartbeat are logged. Status is
atomically replaced under `/run/iptv-igmp-keeper`, avoiding persistent flash writes.
`--status` displays the latest snapshot and its age; stale/missing status exits 1.
A fresh snapshot is not a guarantee that IPTV works. MAC addresses are not logged;
interface/group/IP information can still identify a network.

A same-boot override journal allows restoration after an unclean daemon restart.
Restoration only changes a sysctl still set to the value owned by this daemon on the
same interface identity. Reboot discards `/run` and kernel overrides naturally.
External tools setting the identical value cannot be distinguished; avoid competing
version-management tools. Persistent administrator sysctl settings are not edited.

## Uninstall

```sh
sudo sh /data/iptv-igmp-keeper/uninstall.sh
# Or remove the default config and logs as well:
sudo sh /data/iptv-igmp-keeper/uninstall.sh --purge
```

Stops the service, restores owned settings and removes the boot hook and executable.
Default uninstall retains config/logs and the uninstaller. `--purge` removes the
known default files; custom log/state paths are retained for manual cleanup. The
shared on-boot framework is never removed.

## Development

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile src/iptv-igmp-keeper.py
shellcheck install.sh uninstall.sh unifi/50-iptv-igmp-keeper.sh
```

No runtime pip dependencies. CI runs unit tests and shell lint. Contributions should
include sanitized interface/route evidence, query versions, UniFi OS/device versions,
and packet captures with identifying addresses removed. Do not commit live config.

## References

- [Linux IP sysctl documentation](https://www.kernel.org/doc/html/latest/networking/ip-sysctl.html)
  for `force_igmp_version` semantics.
- [Linux bridge documentation](https://www.kernel.org/doc/html/latest/networking/bridge.html)
  for membership timer units.
- [RFC 2236](https://www.rfc-editor.org/rfc/rfc2236.html) and
  [RFC 3376](https://www.rfc-editor.org/rfc/rfc3376.html) for IGMP wire formats and compatibility.

Independent community project; not affiliated with Ubiquiti or any ISP.
