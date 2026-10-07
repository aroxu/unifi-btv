# Known working provider: SK Broadband B tv

## Original workaround field observations

The user reported the following in a UCG / SK Broadband B tv deployment. This is a
single-environment report, not a statement about every SKB region or UniFi release.
Exact hardware/firmware versions and captures were not supplied for this release.

- The SKB upstream sent IGMPv2 Queries, while the UCG sent IGMPv3 Reports. Channel
  join problems disappeared after setting `force_igmp_version=2` in that environment.
- A joined stream stopped after approximately 260 seconds when upstream membership
  was not refreshed. A manually transmitted WAN IGMPv2 Membership Report immediately
  restored the stream.
- The working fixed workaround used a 60s refresh and queried the downstream to learn
  current subscriptions. That experiment used `eth4` upstream and `br935` downstream;
  those names and its STB MAC are intentionally not built into this project.
- UniFi **Prioritize QoS** caused multicast jitter in that setup. Disabling it for
  the IPTV traffic resolved that observed jitter; test this separately from IGMP fixes.

“Known working” refers to the original v2 workaround. The adaptive implementation
still needs real-gateway testing, including channel changes, multiple STBs, reboot,
reprovision and firmware upgrades. No packet capture or bench result is fabricated.

## Suggested first trial

Use the default `auto` configuration, inspect discovery and run a 450s dry-run while
watching a channel. Confirm `upstream v2 query observed`, sensible interfaces and the
expected active group. If route discovery is impossible before the first successful
join, set upstream/downstream overrides after inspecting your gateway.

Only for an independently confirmed v2 ASM service can you use:

```ini
[igmp]
force_version = v2
query_downstream = fallback
[general]
refresh_interval = 60
```

The `v2` override still honors v1/v3 and source-filter protection. Unlike the original
script, this project does not force the downstream interface to v2 and does not
query it every minute. It may therefore behave differently on the original gateway.
Check report visibility before treating the adaptive build as a replacement.
