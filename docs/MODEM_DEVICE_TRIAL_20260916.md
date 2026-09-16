# Comma Stage 1 device trial — 2026-09-16 UTC

Scope: reversible firewall trial only. No kernel/Panda flash, parser promotion,
openpilot restart, SIM-profile modification, modem reset or cold boot.

Fresh `system.aranet.safety.offroad()` checks preceded device changes. They
require fresh device/panda telemetry, ignition off, device not started and
controls not allowed. Device checkout remains
`216a5aa12a6acd8f4991d2c3c934a513ed6a4b46`; boot ID at start was
`e5e6e23d-7bb2-46a9-8571-29a4794e3d4a`.

## Baseline

- LTE state CONNECTED, registration roaming, network type LTE.
- `curl --interface ppp0 https://connect.comma.ai`: HTTPS 200.
- `resolvectl query --cache=no -i ppp0 example.com`: network DNS response on ppp0.
- Fresh manager telemetry: no missing expected processes; device not started.
- qcomgpsd not running and not expected while offroad. No GPS acquisition or
  sustained-GPS claim is possible from this test state.
- Existing host IPv4/IPv6 filter policies ACCEPT, with no rules.

## Staging and isolation

Only two files were staged under root-owned
`/run/comma-cellular-stage1-20260916`: the firewall candidate and its rollback
tool, retaining repository-relative paths. This is volatile staging, not a
persistent installer or manager dependency. Installed driving code is untouched.

The rollback tool now binds each trial to a network-namespace identity, checks
that identity before restore/confirmation, passes NetworkNamespacePath to the
systemd callback for isolated tests, uses per-trial units, and verifies restored
rules against both snapshots (excluding comments and packet counters).

The isolated expiry trial uses `comma-cell-lab-20260916`; the isolated partial
failure trial uses `comma-cell-fault-20260916`. Neither has vehicle or external
interfaces. Initial host filter rules were rechecked and remained unchanged.

## Results recorded so far

- Actual target-kernel IPv4/IPv6 candidate apply/verification passed inside a
  network namespace, including the IPv6 hop-limit match.
- Live partial-apply test passed: IPv4 applied, an injected error prevented
  IPv6 commit, and the actual restore restored and verified both families.
- Independent timer-expiration/retry passed: after the invoking SSH session
  exited, systemd ran the callback at 00:19:46 UTC. A deliberately unavailable
  IPv6 snapshot caused a visible failure while IPv4 restoration succeeded.
  The timer stayed active. After the snapshot was returned, the automatic
  retry at 00:20:15 restored and verified both families, marked the trial
  restored, and stopped the timer. Host firewall rules were unchanged during
  these namespace tests.
- The live host trial was then armed for three minutes; results below are
  recorded only after actual checks, not inferred from the isolated tests.
- During the live ruleset: both-family rule verification passed, repeated
  LTE-bound HTTPS returned 200, uncached DNS succeeded explicitly through
  ppp0, new Wi-Fi SSH sessions remained reachable, modem stayed CONNECTED,
  and ten seconds of fresh manager telemetry showed no missing expected
  processes. There is no global IPv6 address on the observed PPP connection;
  real cellular IPv6 data transport was not tested (dual-stack packet behavior
  was tested in the native VM).
- Live-host automatic rollback passed: phase changed to restored, the timer
  stopped, and a separate SSH session independently compared both original
  filter snapshots with current rules. They matched exactly excluding
  timestamps/counters. No manual restore command or confirmation was used.
- After rollback, LTE-bound HTTPS again returned 200 and uncached DNS on ppp0
  succeeded. New Wi-Fi SSH sessions remained reachable. This was a temporary
  live deployment test, **not a permanent firewall installation**.
- Final manager telemetry had no missing expected processes; checkout and boot
  ID remained unchanged. Both empty test namespaces were removed after a fresh
  offroad check. Root-owned staging/snapshots remain in /run for inspection and
  disappear on reboot; no active rollback timer remains for these trials.
- Local regression suite: 85 tests passed; Ruff and diff checks passed.

Still separate: actual GPS acquisition/sustained GPS, read-only SIM/eSIM API
compatibility, reconnect, cold boot and driving engagement validation. No
claim that this firewall trial establishes complete EG25 containment.

Read-only PPP-hook follow-up: the installed `/etc/ppp/ip-{up,down,pre-up}` and
IPv6 wrappers pass arguments to fixed run-parts directories, quoted. The
`0000usepeerdns` hooks skip resolver-file manipulation when systemd-resolved
is active (it is active here). The inactive fallback contains unquoted path
expansions in ip-down; do not infer exploitable modem-to-shell injection from
that alone, since interface/path origin and constraints matter. No hooks were
changed. Full pppd/options/environment provenance review remains open.
