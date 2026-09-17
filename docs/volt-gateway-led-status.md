# White Panda gateway LED indications

These are **new gateway patterns**, not interpretations of the original
historical forwarding firmware. See the dated
[physical bench record](volt-gateway-bench-bringup-20260916.md) for what is
actually installed and tested; source changes alone do not establish deployment.

## One-time startup color check

After a verified application slot is selected, exercise each color individually:

| Slot | First color | Second color | Third color |
| --- | --- | --- | --- |
| A | Red | Green | Blue |
| B | Red | Blue | Green |

Each color stays on for **800 ms**, followed by **200 ms off**. After the three
colors there is another **one-second dark interval**, then normal indication.
This makes a missing, wrong-color or mispopulated LED visually apparent. There
is no optical/current feedback: the software cannot certify that a light lit.

The animation is scheduled, never a blocking sleep. Boot, application work and
CAN servicing continue. Confirmation during the animation does not restart it.
It plays only once per initialized LED state, not every status poll or timer
wrap. In the board integration the application owns this introduction; the
loader does not start a second color sequence before handing off.

Before a slot is selected, blue blinks at 500 ms on/off. No slot is advertised
before verification. Fault, recovery or update indications interrupt the startup
cycle; a failed boot may therefore go directly to red without completing a
three-color test. Do not diagnose a failed LED solely from such an interrupted
sequence. Fault-clearing does not replay an interrupted introduction.

## Normal patterns

| Pattern | Meaning |
| --- | --- |
| Blue, one short pulse every four seconds | Confirmed slot A selected |
| Blue, two short pulses every four seconds | Confirmed slot B selected |
| Green, one/two short pulses every four seconds | Unconfirmed trial in A/B |
| Green/blue, three short pulses | Corresponding state but slot unknown; not normal production operation |
| Alternating blue/green, 500 ms each | Update receiving: a new chunk was accepted within 1.5 seconds |
| Short blue pulse every two seconds | Update waiting for new data; duplicates do not count as progress |
| Amber blinking, 500 ms on/off | Erasing the candidate slot |
| Blue, 800 ms on / 200 ms off | Verifying the received image |
| Steady magenta | Committing the trial-boot marker |
| Green, four short pulses every four seconds | Update committed; **not** yet booted/confirmed |
| Red, one long second every four seconds | Recovery waiting (reserved for the actual recovery service) |
| Red, numbered half-second pulses with a long group separator | Fault category below |
| Red 1.5 s, dark 0.5 s, blue 0.25 s, dark 3.75 s | RX-degraded / optional TX inhibited; inspect hardware/software loss counters (new off-device candidate) |

Normal short pulses are 150 ms on/150 ms off. Numbered **fault** pulses are
500 ms on/500 ms off, followed by three additional seconds dark. A nine-flash
group repeats every twelve seconds. Count within one group, not across cycles.
The confirmed-slot pattern means verified/confirmed image metadata, **not** a complete health check,
CAN-transmission permission or proof that the gateway is safe to use on a car.

The temporary SRAM-only probe now uses this same renderer, with a probe-specific
state: R/G/B once with the same off intervals, followed by one short blue pulse
every four seconds. It does not claim a selected application slot. The earlier
probe's separate `now/800` color-cycling loop was a human-interface regression
and has been removed. A 130-second waveform test verifies no repeated RGB cycle.
This regression escaped because production-renderer tests did not exercise the
probe's separate LED loop. The probe now calls the shared renderer rather than
maintaining a second implementation. The test checks every millisecond, including
off intervals and repeated status updates. It is software waveform evidence,
not optical inspection of the physical LEDs or vehicle-safety validation.

| Red pulses | Category | Current source |
| --- | --- | --- |
| 1 | No bootable application | Boot selection failure; not necessarily a signature failure |
| 2 | Selected image violates target/layout/vector policy | Boot port |
| 3 | Storage read/write/erase/readback failure | Latched boot storage error |
| 4 | Invalid trusted configuration | Boot initialization |
| 5 | CAN bus-off/driver fault | Reserved code; runtime fail-silent/reset is not guaranteed to display it |
| 6 | Watchdog reset | Reserved LED code; reset flags are available separately |
| 7 | Crypto/key failure | Boot public-key initialization; not a diagnosis from pulse count alone |
| 8 | Internal invariant or invalid indication | Panic/status validation |
| 9 | Update aborted | Update validation, timeout or storage failure; consult host detail |

Routine session closure or expiry with an idle updater must not create code 9.
The original session cleanup incorrectly aborted an idle updater, producing
false nine-flash alarms after ordinary CLI use. Cleanup now always revokes
permissions but only marks an in-progress update as interrupted; existing real
abort indications remain visible. Regression tests cover normal close/expiry
and interrupted-update close/expiry separately.

## Read the indication instead of counting it

`device_cli ... status` reports the renderer snapshot when INFO capability
`0x10` is present (application interface 1.1). Authenticated opcode 15 returns
seven bytes: schema 1, LED state, slot (0/1/255), error, last RGB mask, intro
active flag, CAN-peer state. The getter does not manufacture a healthy state
before the renderer has supplied a snapshot.

`device_cli ... indicators` uses USB vendor IN `0xd7`, value/index zero, to read
the same bounded non-secret snapshot **without opening or refreshing a session**.
It is unavailable in the cold recovery window. This permits checking the
post-disconnect/expired state without hiding it by starting another session.
It grants no configuration, transmission or flash permission.

CAN-peer states: disabled, local USB owner, awaiting authenticated peer,
authenticated, stale. The installed bench configuration is **disabled**, not a
missing-peer fault. Peer status is informational; no discovery/keepalive CAN
traffic or new Tres permission is introduced by this change. Separate visual
peer patterns and deployed comma/Tres backhaul remain future integration work.

An assertion following a storage failure preserves code 3 rather than hiding it
behind code 8. A CPU halt, reset loop, absent power or dead red LED can prevent
any visible error code. A future watchdog handler must record reset cause and
disable optional transmissions independently; it must not wait for a blink
sequence. LED rendering never feeds a watchdog, confirms a trial or enables TX.

## Wiring evidence and implementation

Historical Panda commit `3b35621671aaa6de3fc66d85d30e4208a77e2489`,
`board/boards/white.h:29` (`white_set_led`), identifies active-low outputs:

- Red: GPIOC pin 9.
- Green: GPIOC pin 7.
- Blue: GPIOC pin 6.

Evidence archive: `/home/james/diagnostics/volt-gateway/history/panda-3b356216.tar`.
This is source evidence, not continuity/optical verification of this individual
old board. Do not reuse the mapping on Tres or another Panda revision.

`firmware/status_led.c` computes colors with constant work and no allocation,
delays, interrupts or CAN access. `vgw_white_led_bsrr` computes one atomic
GPIOC set/reset-register value touching only those three pins. It does not
write registers. The future board adapter must establish clocks and preload
the inactive output levels before configuring the correct pins as outputs.
Use one LED owner, poll around 20 Hz and skip missed samples rather than
catching up. LED errors must never affect the vehicle-facing safety boundary.

`boot_port.c` exposes an observational three-byte state/slot/error snapshot;
it works without linking the renderer. Trailer validation remains a boot
integrity check independent of LED presence. The LED renderer consumes its
result, not the other way around. Update/recovery/runtime fault ownership and
priority still require integration with the actual board main loop.

`update.c` now exposes an observational phase snapshot through
`vgw_update_led`. Receiving means accepted new image bytes, not arbitrary CAN
activity, duplicate retransmissions or unauthenticated requests. After 1.5
seconds without new bytes the indication changes to waiting. The snapshot
cannot grant permission, feed the watchdog or change update state. Verification
and commit are separate phases; four green pulses appear only after the commit
callback and final safety check succeed. Normal A/B trial/confirmed patterns
resume on the subsequent boot. A short phase need not be visible: do not delay
verification or flash work just to display it. Flash-busy execution may leave
the last color steady until the RAM-resident scheduler integration is complete.

## Tests and limits

The earlier startup-LED validation passed **1,046 tests, zero failures/skips**, including
282 LED cases and eight ARM boot cases, plus lint/build/static-analysis checks.
See [artifact evidence](evidence/volt-gateway/boot-led-20260916.json).

Native tests cover all 256 RGB-mask inputs, exact active-low pin masks, pulse
timing, both startup orders, all colors, one-shot behavior, confirmation during
startup, preemption, clock wrap, skipped ticks, malformed status and null/absent
renderer calls. ARM boot tests also compare LED enabled versus disabled:
selection, confirmation, executed probe and resulting flash remain identical.

These are software tests, not physical LED inspection, brightness/color testing,
GPIO timing measurements or parked/driving validation. Physical LED validation
is part of the later authorized bench image test; no separate risky CAN test is
needed to inspect colors.
