# USB-configurable SWCAN experiments

Implemented September 17, 2026. This replaces the need to compile each candidate
ID/payload into the experimental application. It is not a production recirculation
controller and does not establish the meaning or safety of a chosen payload.

Only builds with `VGW_HVAC_EXPERIMENT` expose this operation. Normal applications
and the trusted recovery loader do not expose it. INFO capability `0x100` identifies
support; older firmware is rejected by the CLI rather than silently falling back.

`device_cli swcan-tx --id ID [--extended] --data HEX` uses the existing required
`--pairing` file. It sends one Classic CAN data frame on SWCAN only. The USB host
chooses any valid standard/extended ID and 0–8 payload bytes. No per-ID or payload
allowlist remains for this experimental operation. `--bus` must be 3. An empty
`--data ''` requests DLC zero. There is no automatic release frame or repeat loop.

Authenticated opcode 21 payload: big-endian 32-bit ID, one-byte extended flag
(0 or 1), one-byte DLC, exactly DLC data bytes. Length/ID/flag validation precedes
mailbox writes. Only the local USB session can invoke it, never the CAN backhaul.
Existing authenticated sequence/replay handling remains in the transport.

The device independently requires fresh Park/RUN/zero-speed evidence, a live
session, healthy SWCAN, no update/recovery/restart in progress, and a free mailbox.
At most one frame is pending and one frame may be started per second. There is no
four-attempt reboot requirement for this operation; the displayed attempt count
saturates at 255. The legacy two-frame operation retains its old bounds. Both
operations share cooldown/state so they cannot overlap. A mailbox failure or
20-ms completion timeout latches the existing fault, without retransmission.
Session/state loss aborts pending transmission; an already transmitted frame
cannot be recalled. No HSCAN transmit API, persistent rule, or boot-time TX is added.

This is a broad **experimental** SWCAN write surface: parked checks and rate limits
do not make arbitrary payloads benign. Do not use it for steering, braking,
propulsion, gear, stability, restraints, or blind sweeps of unknown commands.
Host-selected frames need deliberate review. TX completion proves only CAN
transmission, not HVAC acceptance or flap movement.

Build output:
`/home/james/diagnostics/volt-gateway/builds/usb-swcan-configurable-20260917-01`.
Implementation/build does not mean deployment. The previously installed hvac05
image lacks opcode 21. No physical TX or flashing was performed for this change.
OBD-connected ROM recovery remains an unresolved physical issue.

## TX/session correction (candidate, not flashed)

The original physical ON/OFF replay of `0x10B02099` selector `0006070d`
completed both sends (counter 0 → 1 → 2), ten seconds apart. Owner observed no
LED/screen change. This is not evidence of recirculation actuation.

The initial status could report session disabled after successful authentication:
opening a session did not establish the application lease, and the experiment
dispatch returned before normal lease renewal. The host now explicitly sends
authenticated liveness before experimental operations, including status. The
firmware also renews the lease on authenticated experimental status. Neither
change bypasses the independent physical interlocks. The updated host was
checked against the installed old firmware: status returned session enabled,
interlock ready, state done, attempts 2. No additional CAN TX/reset was requested.

The candidate handles a raw-frame arbitration loss or clean-controller timeout
as `failed`, not a permanent fault. There is no automatic retransmission. A new
explicit request must satisfy cooldown, mailbox availability and every normal
interlock. Hardware errors and loss of permission remain blocking. Legacy
two-frame behavior retains its fault latch.

Hardware completion is accounted for before evaluating permission for future
transmission, so a delayed poll cannot hide a completed raw TX. SWCAN arbitration
and TX-error counters now include experimental mailbox results. Status v4 adds
failure reason plus saved TSR/ESR; v1–v3 remain readable. A failed attempt is
distinct from a successful `done`. These changes address demonstrated software
defects but do not establish the cause of each historical intermittent failure.

Build: `/home/james/diagnostics/volt-gateway/builds/usb-swcan-tx-fix-20260917-01`.
Experimental loader/A/B ARM builds succeed with no undefined symbols. Native
HVAC, application and CLI regression suites: 82 passed, no skips, using pinned
crypto/boot dependencies. This is not physical validation of the new firmware.
The existing whole-image emulator suite also passed all 10 cases, including
busy-bus cold boots in both slots (241.66s). That suite builds the normal
non-experimental application: it is a boot/USB regression check, not emulated
or physical proof of the experimental raw-TX path. Total: 92 passing cases.

## Physical USB-only deployment, September 17

After the owner disconnected OBD, flashed labeled serial
`370022000651363038363036` using authenticated software USB recovery, without a
USB replug. The initial attempt stopped before programming because the new
evidence parent directory was absent; creating it allowed the existing installer
to proceed. Live predecessor readback matched the saved hvac05 image. Complete
1-MiB post-write readback matched SHA-256
`3b6c9b0a9689b87c33a735f4c0389a9fb980455803c59e867145c0621300f50e`.

Application enumerated and reported capabilities **511**, including configurable
SWCAN TX (`0x100`), slot **A**, state **running**, error **none**, and completed boot
LED introduction. Running build:
`2cc6cfa6940b220018b178866863858d5534051c66229138de91f5d31abdf708`.
Private evidence is under
`/home/james/diagnostics/volt-gateway/flashing/370022000651363038363036-20260917/usb-swcan-01`.
No CAN TX command was issued. This verifies flash contents and USB boot/capability,
not physical HVAC operation or OBD-connected recovery. Existing regression suites
passed 88 tests before deployment; no new experiment-specific test suite was added.
An immediately following status session timed out during HELLO; a subsequent
read-only retry succeeded without reset/replug. It reported idle, zero attempts,
no vehicle inputs, TX not inhibited, and interlock not ready (expected with OBD
disconnected). The transient HELLO timeout's cause is not established.
