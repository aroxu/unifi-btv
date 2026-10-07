# Troubleshooting

## No interfaces / no active groups

Run `--discover` using the same `--config` as the service. Inspect:

```sh
cat /proc/net/ip_mr_vif
cat /proc/net/ip_mr_cache
ip -br address
```

An existing IGMP proxy/multicast routing setup is required. No routes means automatic
role detection cannot bootstrap. Multiple route input interfaces mean ambiguous
upstream; set overrides for the intended IPTV path. Overrides require addressed
interfaces and do not bypass route/client/traffic checks. Multi-table/VRF routing,
IPv6 IPTV, hardware-offloaded counters invisible to these files and IGMP capture
that bypasses the host are not supported by this implementation.

An active group needs a downstream subscription AND packets increasing between
scans on a corresponding multicast route. Switching channels may take one scan.
A fresh route counter alone is never treated as a subscription. With native v2 report
suppression some clients can remain unseen; use passive captures to confirm this.

## Waiting for v2 / source-filter protection

Run a long `--dry-run --once --observe-seconds 450` as root. Incoming queries, not
outgoing gateway reports, establish upstream version. `auto` remains idle when no
query is seen. v3/v1 evidence blocks operation for 400s by default; source-filter
client records expire after 300s. Continued evidence extends these windows.

Do not use v2 to fix an IGMPv3/SSM provider. Set `force_version = off` and investigate
native proxy compatibility. Changing to `v2` does not bypass the guard. The daemon
only understands ASM subscriptions well enough to synthesize Reports.

A General Query fallback is possible only once upstream v2 is permitted and downstream
traffic is recent. It is suppressed by any recent downstream querier and by recent
client reports. A continuously active querier with invisible reports needs a capture/
bridge fix, not more queries. Set `query_downstream = off` to disable fallback entirely.

## Stops after roughly a few minutes

Check status freshness, route counters, report visibility and log messages. Compare
the refresh interval with the measured provider timeout. A 60s default is appropriate
for the reported ~260s B tv case, not a universal ISP guarantee. Client evidence
expires; if reports are absent, refresh intentionally stops. Traffic grace is only
90s, so start a new channel to establish fresh evidence after a prolonged outage.

## Jitter while membership remains alive

Test with UniFi **Prioritize QoS disabled for IPTV**, as it caused multicast jitter
in the reported B tv setup. Change one setting at a time and compare playback and
packet loss. Also inspect Wi-Fi vs wired paths, switch snooping, physical link errors,
and congestion. A membership keeper cannot repair packet scheduling or bandwidth loss.

## Service / boot / reprovision

```sh
systemctl status udm-boot.service iptv-igmp-keeper.service --no-pager
journalctl -u iptv-igmp-keeper.service -n 50 --no-pager
cat /data/iptv-igmp-keeper/keeper.log
ls -l /data/on_boot.d/50-iptv-igmp-keeper.sh
```

Transient services are recreated by the boot hook, not enabled with `systemctl enable`.
Verify the on-boot framework after every firmware update. Python must remain installed
on the host. Config edits require service restart. During reprovision the daemon
retries missing interfaces and relearns evidence; a brief interruption is expected.
Raw-socket/BPF permission failures are surfaced rather than silently capturing all
video traffic. Running inside a restricted container is not supported.

## Stop intervention / restore

```sh
systemctl stop iptv-igmp-keeper.service
```

SIGTERM restores owned upstream sysctl settings. To retain observation while disabling
intervention, set `force_version = off` and restart. If killed uncleanly, the next
start or uninstall restores the same-boot journal. If permissions prevent restoration,
resolve the cause and retry uninstall before deleting files. Persistent values set
by other tools are outside this project's ownership.
