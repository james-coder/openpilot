# Volt Object CAN startup wait

## Confirmed failure

Route `0000007b--1f94f7f3f1`, segment 0, recorded model recognition at 1.262 s
relative to the first logged event. The fingerprint had an empty Object bus.
At 1.361 s CarParams selected CHEVROLET_VOLT with radarUnavailable/dashcamOnly
true. Camera header 1056 first appeared at 2.199 s; radar header 1120 at 2.598 s.
The configuration remained dashcam-only at 51.399 s despite subsequent traffic.
This is distinct from failing model recognition: model retries had already
succeeded and therefore did not help. No charging-cord input controls this
decision. Whether charging influenced hardware wake-up timing is unestablished.

## Fix and safety boundary

Before constructing live Volt CarParams, wait up to five additional seconds for
either existing GM Object header on bus 1 with DLC 8. Stop immediately if valid
evidence already exists or arrives. Ignore wrong-bus, TX-echo, unrelated and
wrong-length frames. A callback returning after the deadline cannot contribute
late evidence. The live receive callback has a 20 ms socket timeout.

This does not assume radar presence from model identity/cache, fabricate frames,
change Panda safety or transmit CAN. Missing headers still lead to dashcam-only.
The existing runtime radar checks remain intact. Replay/offline calls and other
models do not gain a wait. It executes before publishing/configuring CarParams,
not as an in-motion transition from passive mode to driving mode.

## Validation

78 tests plus 44 subtests passed across startup-wait, fingerprint retry, legacy
CAN fingerprint and GM tests. Lint passed. Coverage includes the observed header
delays, absent traffic, wrong bus, TX echoes, malformed DLC, unrelated traffic,
deadline crossing, existing valid headers, unrelated models and live-only call
ordering before parameter construction. Initial test errors were missing `docs`
arguments in new test fixtures, corrected before the passing run.

These are host tests, not proof of engagement or road validation. Deployment and
post-restart results must be recorded separately. Rollback is a revert of the
single helper/test commit; no firmware or persistent vehicle setting is needed.

## Parked deployment and verification

Source commit: opendbc `931530b1eeaf2ae5faad3944e608c73618db46c9`.
The comma's pre-change helper file matched the local base byte-for-byte, and its
worktrees were clean. Only this commit was cherry-picked onto the device's
existing submodule history, producing `d69e8fdb347293ebd1fbfc430ae995d55da8d17c`.
All 15 new tests passed on the comma. Deployment parent commit is
`e919091dc2759be045f1c843d6bcaccbc9d8b9e3` on `deploy/volt-gm-egr`.
Both deployment commits were backed up to origin through the laptop; the comma's
own push failed host-key verification, which was not bypassed or weakened.

Immediately before reboot, live valid/alive telemetry confirmed Park, 0 m/s,
and selfdrive disabled/inactive. Only the comma was rebooted. After reconnect:

- CHEVROLET_VOLT, radarUnavailable=false, dashcamOnly=false, passive=false.
- CarParams and live Panda both selected GM safety.
- No current selfdrive alert; no required manager process stopped.
- Selfdrive remained disengaged: no engagement or road test was performed.

The vehicle was already awake during this reboot. The delayed-header behavior is
covered by regression tests using recorded timing, but a future cold vehicle
startup is still a distinct physical validation. No Panda firmware, persistent
user toggles, optional-process exemptions or radar-error bypasses were changed.
