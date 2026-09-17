# Object-CAN authenticated transport trial

Owner selected standard IDs `0x6F0` request / `0x6F1` response, Object bus 1.
This is a bounded parked experiment, not production approval or HVAC actuation.
See [ID evidence](volt-gateway-can-id-selection.md).

## Implemented off device

- White canonical provisioning: CAN1 primary listener, CAN2 Object backhaul,
  CAN3 SWCAN listener. No vehicle-write rules. Fixed two IDs; no raw-TX API.
- Primary GM safety opt-in flag 64, ASCM+EV only: `0x6F0`, bus 1, standard
  Classic CAN, DLC8, bounded ISO-TP frame types/length, at most 100 frames/s.
  Requires controls disallowed and stationary wheel-speed evidence <500 ms old.
  Does not change steering, brake, gas, relay or RX-watchdog checks.
- Environment opt-in `VOLTGW_OBJECT_PARKED_TRIAL=1` selects that flag only for
  CHEVROLET_VOLT. Absent opt-in leaves existing driving operation unchanged.
- `object_probe` performs authenticated INFO, empty write-policy and bus-status
  reads, then closes. No subscriptions, update commands or actuation.
- Host checks live Park, speed, disengagement and matching primary safety flags
  before every frame. Lifetime <=60 s; total <=512 frames; spacing >=12 ms.
- Optional abstract Unix datagram mailbox feeds card's existing sole sendcan
  publisher. Same-UID credential check, 16-byte records, <100 ms freshness,
  one nonblocking receive per tick. Missing/crashed/denied mailbox does not
  become an engagement requirement or modify manager process health.

The White response limit remains 100 fps with a two-frame token burst, fixed
response ID, bounded queues and RX/error/utilization inhibition. At 160 estimated
bits/frame, 100 fps is 3.2% of 500 kbit/s **per transmitting side**, not a combined
budget. The finite status probe uses substantially less total traffic.
Host or bus attackers can still cause denial of service; unauthenticated
telemetry is not trusted for actuation. Primary safety limits transport, while
White authenticates whole logical commands and separately verifies signed updates.

## Physical White flash completed

Owner confirmed OBD disconnected, USB only, then replugged into fixed recovery
listener. Exact ROM serial `365236793036`, application identity
`370022000651363038363036`. No option bytes changed.

Firmware came from the existing fully passed 1,671-test observer-clock build:
`/home/james/diagnostics/volt-gateway/builds/validation-observer-clock-20260917-01/board`.
No new firmware C changes were necessary to select its existing CAN transport.
Private preparation changes provisioning policy while preserving identity,
pairing secret and signing key. Both application slots were signed and verified.

Full live predecessor matched saved IRQ image before programming. Reviewed flash
regions were loader 0x00000..0x0ffff, provisioning 0x20000..0x3ffff, A first
sector 0x40000..0x5ffff, B first sector 0xa0000..0xbffff. Full 1 MiB readback matched:

`b668c5d66e436e736fdbbe7ddb460770d2e62f74071f18e6832fde38cd467f77`

Evidence (contains secrets; never commit binaries):
`/home/james/diagnostics/volt-gateway/flashing/370022000651363038363036-20260916/object01/`.
CAN-silent rollback remains `after-bench-rx-irq01.bin` in its parent directory.
New pairing bundle is owner-only `.voltgw-private/<serial>/bench-object01/pairing.json`.
Never deploy factory image or signing private key to comma.

Authenticated physical USB check: protocol 1.1, mapping `[3,3,2]`, running slot A,
no error, no boot-pattern loop, empty write rules, all bus RX/TX/errors and RX
overflow statistics zero on disconnected bench. Build identity:
`ab7d5606a3e22ec84d62b187da71a4fbaa303dbbdeb7f80454f1f33e71156b18`.

## Connection and deployment requirements

USB enumeration intentionally claims exclusive local control for that boot.
Therefore **USB must be disconnected and White cold-powered from OBD alone**
for CAN control. Merely closing the USB CLI is insufficient. Do not interpret
USB-owned silence as an Object-bus wiring fault.

Primary Tres and comma changes have NOT yet been deployed or physically tested.
Before their deployment verify fresh live Park/zero-speed/disengagement; retain
the current primary firmware/software rollback, run safety tests, build its exact
target and enable only the reviewed flag. No all-output mode. Do not change the
global manager watchdog or stop driving processes to run this optional feature.

Then run `python -m tools.volt_gateway.object_probe --pairing <owner-only-file>`
on comma, with White connected OBD-only. Verify authenticated reply, current
build/policy, response IDs, bus errors/drop counters and normal process health.
No CAN exchange has yet been demonstrated on the actual car in this increment.

## Tests and limitations

Local targeted safety/provisioning/transport run: 311 passed, 448 subtests passed,
129 inherited nonapplicable/base-suite skips. Mailbox tests: 14 passed, including
absent/stopped/permission-denied/closed socket, malformed/stale input, bounded
backlog, and two consecutive real `Car.step` calls with simulated engagement and
optional failure. Image packaging/mailbox subset: 27 passed. Lint/compile checks
passed. These are host tests, not real engagement or cold-vehicle-start validation.

First broad validator: 1,678 tests passed, all build/analyzer steps passed, but
the overall result intentionally failed source-consistency because host tools
were still being edited during the run. A stable-source rerun is recorded
separately; do not call the first report an overall pass.

Stable-source rerun completed: **1,679 passed, zero failed/skipped**, all target
builds, static analyzers, lint and source-consistency checks passed. Report:
`/home/james/diagnostics/volt-gateway/builds/validation-object-trial-20260917-02/report.json`,
SHA-256 `e0e8833307e12242d8d9a4b1105ae742da08f348264fbad1459d0ede1b5e9a4e`.
`production_ready` remains false: actual CAN transport and primary deployment
have not yet been validated. Root implementation commit `18a3e656d`, primary
safety/opt-in commit `276a7f69`, both pushed. Existing unrelated diagnostic edits
remain unstaged and were not swept into these commits.
