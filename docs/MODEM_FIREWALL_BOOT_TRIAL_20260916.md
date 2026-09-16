# Persistent cellular firewall trial

## Scope and failure policy

The rule contents are unchanged from the dual-stack packet lab and earlier
temporary target trial. Only ppp+/wwan+ ingress and forwarding are filtered.
No output filtering, routing, USB, modem command, kernel or openpilot manager
process changes are included. Wi-Fi traffic returns to existing policy.

Boot ordering is after netfilter-persistent and before comma.service/lte.service.
This is ordering, **not a fail-closed dependency**: a failed firewall unit does
not block openpilot or modem startup. In particular, a failure after confirmation
can leave incomplete protection. Do not claim that this layer guarantees cellular
isolation under all startup failures, or protects USB/QMI/parser attack surfaces.

## Packaging and rollback

- Root-owned code: `/usr/local/lib/comma-cellular`, outside the writable checkout.
- Root-owned persistent phase/filter snapshots: `/etc/comma-cellular-firewall`.
- Persistent systemd units: `comma-cellular-firewall.service`,
  `comma-cellular-rollback.service` and `comma-cellular-rollback.timer`.
- `/var` is tmpfs on this device, so it cannot hold reboot-surviving trial state.
- The separate timer is enabled/activated before firewall application; the
  controller checks activation explicitly. It expires after five minutes of
  activation and retries a failed restore every 30 seconds. Reboot reactivates
  the enabled timer with a new five-minute deadline. This is not an absolute
  deadline across repeated reboots.
- Rollback persists `rollback` before restoring either family. A later boot
  then retries restoration instead of reapplying. Both families are attempted
  even when one fails. Verified completion persists `restored` and stops the timer.
- Confirmation is explicit and follows external connectivity/boot checks.
  A late timer callback cannot restore a confirmed trial; a shared lock serializes
  boot, confirm and restore. A completed rollback also suppresses future apply.
- Phase replacement is atomic and fsynced. Root is briefly remounted writable
  for durable state updates and returned to its prior mode in a finally block.
  A hard kill/power failure during remount is not equivalent to graceful cleanup.
- Snapshots cover complete filter tables only. Do not run another firewall
  administrator during the trial. Rearming refuses a changed baseline.
- AGNOS replacement/reflash may replace these root-filesystem files; this is
  reboot persistence, not a promise of survival across OS image replacement.

## Validation so far

Local tests cover pending timer gating, partial-family failure, retry state
after simulated reboot, confirmation/late callbacks, stale snapshot rejection,
interrupted phase writes, and restoring read-only mount state on exceptions.
These are mocked tests, not a substitute for a live timer or boot test.

On the parked/offroad comma, initial installation returned root to read-only.
The persistent timer was independently active before manual service startup.
Both installed rule sets verified; LTE-bound HTTPS returned 200, uncached DNS
over ppp0 succeeded, and Wi-Fi SSH remained reachable.

After another fresh offroad gate, a managed warm reboot was requested to test
boot ordering and deliberately unconfirmed automatic rollback. Results must be
recorded below before claiming permanent deployment or reboot validation.

New boot ID: `b32a6e16-b232-48f4-87f0-387f0f127055`.
Firewall start/exit at 7.459272/8.859001 seconds monotonic; comma.service
started at 8.862810 seconds and LTE service at 13.152718 seconds. Both firewall
families verified after boot. Wi-Fi SSH returned and 41 fresh manager samples
over 20 seconds had no missing expected process; device remained offroad.

Post-reboot LTE connectivity has **not yet passed**: repeated PPP dialing reached
serial CONNECT and successful CHAP authentication, followed by modem hangup
before a usable persistent ppp0 link. Modem's high-level CONNECTED state is
therefore not sufficient proof of LTE data. Do not confirm until actual data
works; compare behavior after the scheduled rollback before attributing cause.

## Reboot-surviving automatic rollback passed

Without confirmation or a manual restore command, PID 1 started rollback at
01:29:25 UTC; it finished successfully at 01:29:26. Persistent phase became
`restored`, the timer stopped itself, both complete filter tables matched their
saved baselines (ignoring counters/timestamps), and root was read-only again.
Wi-Fi SSH remained reachable. This is an actual target reboot/timer result,
not a mocked test. Early journal wall-clock dates reflect the device's clock
being corrected after boot; use monotonic timestamps for startup ordering.

LTE still lacked a usable ppp0 link immediately after rollback. Further PPP
baseline observation and a freshly offroad-gated restart of existing
lte.service follow; no SIM/profile, parser, kernel or driving-process change.

Combined local parser/firewall suite: 102 passed; Ruff and diff checks passed.

PPP hangups continued at 01:29:29, :39 and :44 after baseline restoration.
The existing LTE service was restarted after a fresh offroad check; subsequent
LTE HTTPS returned 200 and uncached ppp0 DNS succeeded. This points away from
packet filtering but does not establish the underlying warm-boot modem fault.

## Re-arm correction

The first re-arm test exposed a systemd scheduling bug: `OnUnitActiveSec=30s`
retained the previous rollback invocation timestamp and immediately retriggered
rollback. The baseline was restored safely, but candidate verification failed,
so no confirmation or subsequent modem restart was performed in that attempt.

Correction: the timer now uses only `OnActiveSec=5min`. Failed restoration is
retried by the rollback service using `Restart=on-failure`, `RestartSec=30s`,
and disabled start limiting. Local assertions cover this packaging requirement.
The corrected re-arm showed a fresh five-minute deadline while retaining the
old last-trigger timestamp, without immediate restoration.

## Confirmed deployment

The corrected candidate was reapplied under a newly armed timer. Installed
IPv4/IPv6 verification and LTE HTTPS passed. With that verified policy still
active, the existing LTE service was restarted after another fresh offroad
gate. LTE HTTPS again returned 200, uncached DNS over ppp0 succeeded, and
Wi-Fi SSH remained reachable. The earlier failed re-arm attempt's output must
not be counted as this reconnect test: that attempt stopped before restart.

A separate harmless systemd test service, using the same failure-retry
properties, deliberately failed at 01:32:41 and automatically retried and
succeeded at 01:33:11. This validates actual target systemd retry scheduling,
not a second injection of a failed firewall restore. The full-table restore
itself and reboot-surviving timer were exercised above before the re-arm fix;
the final revised units were not subjected to another physical cold boot.

Final 30-second observation: 61 fresh valid manager samples, no missing expected
process. Fresh offroad verification, installed rule verification and LTE HTTPS
200 preceded explicit confirmation. Persistent phase is `confirmed`, firewall
service is enabled, rollback timer inactive, and root mounted read-only. Kernel
remains 4.9.103+; no kernel image, Panda configuration, manager readiness or
driving-process watchdog change was made. No road/engagement validation claimed.

The warm-boot PPP hangup root cause remains unresolved; a modem-only restart
recovered it, including reconnection with the firewall active. Do not turn this
result into a claim that every LTE startup issue is fixed.

### Recovery notes

Pending trials revert automatically. After confirmation, `restore` deliberately
does nothing: a stale queued timer cannot undo an accepted deployment.
For an operator-requested rollback, first verify offroad, check for unrelated
firewall changes since the saved baseline, and serialize against the controller
using `/run/comma-cellular-firewall.lock`. The installed controller's
`set_phase('rollback')` followed by `restore()` restores both saved filter
tables and leaves persistent `restored` state, which suppresses future boot
application. Arm the rollback timer for retry protection before restoring.
Do not blindly restore an old full-table snapshot after another administrator
has changed firewall policy. Source/snapshots/units remain available for
inspection; OS-image replacement requires reprovisioning rather than relying
on these files surviving the update.
