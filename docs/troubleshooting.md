# Troubleshooting

## Version 0.1.0 stops after a few minutes

If upstream v2 Queries are present but status says `enabled: false` and
`source-filter/SSM evidence present`, the keeper has stopped refreshing. Version
0.1.0 incorrectly classified local control subscriptions such as `224.0.0.251`
(mDNS) as SSM. Empty IGMPv3 ALLOW/BLOCK deltas also incorrectly blocked operation.
Version 0.1.1 ignores those records while retaining actual source-filter/SSM guards.

Update in a root SSH session:

```sh
curl -fsSL https://raw.githubusercontent.com/aroxu/unifi-btv/main/install.sh | bash
python3 /data/unifi-btv/unifi-btv.py --version
```

Expect `0.1.1` or newer. After updating, change the TV channel once to establish
fresh reports and traffic; a long-expired stream is not automatically resurrected.
Then inspect status after the upstream Query has been observed (allow a few minutes):

```sh
python3 /data/unifi-btv/unifi-btv.py --config /data/unifi-btv/config.ini --status
```

For an eligible v2 ASM service, `enabled` should be true, `active_groups` should
contain the channel, and `reports_sent` should increase on subsequent refreshes.
`last_report_age_seconds` should remain near the configured interval while active.
The counters record successful send calls, not ISP acknowledgment.

If protection still blocks operation, `source_filter_groups` identifies the blocking
groups and `memberships` includes their modes, report ages and traffic eligibility.
Do not disable protection blindly: an actual source-specific subscription requires
native IGMPv3 support. Supply this status and recent log lines when reporting it.


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
traffic is recent. A fresh report for that group suppresses fallback; another
channel's report does not. External Queries defer fallback only through their
advertised response window. Query presence alone is not proof that clients answered.
If fallback also gets no response, inspect packet visibility and the bridge/STB path.
Set `query_downstream = off` to disable fallback entirely.

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
systemctl status udm-boot.service unifi-btv.service --no-pager
journalctl -u unifi-btv.service -n 50 --no-pager
cat /data/unifi-btv/unifi-btv.log
ls -l /data/on_boot.d/50-unifi-btv.sh
```

Transient services are recreated by the boot hook, not enabled with `systemctl enable`.
Verify the on-boot framework after every firmware update. Python must remain installed
on the host. Config edits require service restart. During reprovision the daemon
retries missing interfaces and relearns evidence; a brief interruption is expected.
Raw-socket/BPF permission failures are surfaced rather than silently capturing all
video traffic. Running inside a restricted container is not supported.

## Stop intervention / restore

```sh
systemctl stop unifi-btv.service
```

SIGTERM restores owned upstream sysctl settings. To retain observation while disabling
intervention, set `force_version = off` and restart. If killed uncleanly, the next
start or uninstall restores the same-boot journal. If permissions prevent restoration,
resolve the cause and retry uninstall before deleting files. Persistent values set
by other tools are outside this project's ownership.
