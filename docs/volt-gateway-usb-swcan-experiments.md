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
