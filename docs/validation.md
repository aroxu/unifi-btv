# Validation record

Local validation on 2026-10-07, Linux x86_64, Python 3.13.5:

- 17 unit tests passed: IGMPv1/v2/v3 query detection; IPv4 options; truncated,
  fragmented and checksum-invalid packet rejection; IGMPv3 records/source filtering;
  control/SSM exclusion; sender TTL/Router Alert/checksum; multi-client leave/expiry;
  v2 evidence and v3 veto; allowlist-independent safety evidence; route discovery and
  ambiguity; timer conversion/clamp; invalid config; dry-run no writes/transmission;
  fallback/external querier/rate limiting; traffic expiry and counter reset;
  reprovision resets; sysctl journal ordering and ownership-aware restoration.
- Python compilation passed.
- ShellCheck 0.10.0 passed for installer, uninstaller and boot hook.
- Example configuration validation passed.
- Actual local `--discover --dry-run` passed with empty Linux multicast tables.
- Actual local `--dry-run --once --observe-seconds 2` completed, remained idle, and
  reported no interfaces/groups. No network or kernel setting was changed.

The local environment had no IPTV route, gateway access or raw capture privileges.
Therefore these checks do **not** validate live BPF capture, actual packet delivery,
UniFi sysctl behavior, persistence or playback. Sender behavior was checked with a
mock socket; lifecycle and intervention policy were checked with fixtures/mocks.
The CI workflow is provided but has not been run on GitHub in this session.

## One-line installer follow-up (2026-10-07)

- Added seven bootstrap tests using a mocked download and real shell/tar operations:
  piped Bash and POSIX shell execution, complete archive extraction, failed download
  cleanup, invalid archive rejection, missing required-file rejection, invalid hook
  rejection and existing destination preservation. Total: 24 tests passed.
- ShellCheck 0.10.0 passed after the bootstrap changes.
- Bootstrap tests use `--download-only`; no service or gateway setting is modified.
- The one-line source endpoint is `aroxu/unifi-btv` on `main`; a single codeload archive
  supplies all files from the same snapshot. Actual gateway installation remains pending.

## Renewal regression fix: 0.1.1 (2026-10-08)

- 33 local tests passed (the existing 24 plus nine renewal regression tests).
- A simulated 15-minute timeline includes upstream v2 Queries, local mDNS Reports,
  an external downstream querier without responses, and STB responses to fallback.
  Upstream Reports continue at no more than 60s intervals after initial detection.
  This is a mocked-clock test, not a 15-minute live gateway measurement.
- Tests retain actual SSM/source-filter blocking, ignore empty source deltas, verify
  IGMPv3 Max Response Code decoding and honor response deadlines. Fresh reports
  suppress fallback; another channel's report cannot hide a missing response.
- Status tests verify blocking groups and send counters. Python compilation passed.
- The reported gateway log confirms disabled renewal due to the old source-filter
  guard, but does not reveal its triggering group. Post-update playback is pending.

## Gateway acceptance procedure (pending)

1. Record model, UniFi OS version, Python version, proxy configuration and interface
   roles. Confirm routes, upstream Query version and bridge membership timer.
2. Run a 450s dry-run with a channel playing. Compare active groups and query versions
   against a simultaneous independently captured IGMP trace.
3. Confirm a v3 Query, source-specific subscription, ambiguous upstream and absent
   client evidence each suppress intervention. Use a lab for synthetic protocol tests.
4. Install, then watch longer than two upstream expiry periods. Confirm TTL 1,
   Router Alert and report interval on WAN. Verify downstream fallback is absent
   while an external querier is healthy.
5. Change channels repeatedly; turn off one of two STBs watching the same group;
   turn off both. Confirm membership expiration and no indefinite stale refresh.
6. Reprovision interfaces and reboot. Confirm rediscovery, capture recovery, boot
   hook execution and sysctl restoration on graceful stop/uninstall.
7. Compare jitter with Prioritize QoS on/off separately from membership tests.
8. Check behavior after a firmware upgrade; verify Python and udm-boot still exist.

Publish the resulting device/provider matrix only after collecting those results.
