# First parked USB/SWCAN receive check

This is a physical validation gate, not a completed test. No primary Tres,
openpilot manager, vehicle actuator, safety mode or gateway provisioning changes
are part of this procedure. Do not run a firmware update during this test.

## Before vehicle connection

1. Verify the final image's USB-only cold boot, authenticated status and empty
   vehicle transmit policy. Check `indicators` after closing the session and
   after its 20-second timeout; an idle updater must not show a red update fault.
2. Verify that the attached serial is `370022000651363038363036` (the labeled
   Panda), SWCAN controller is 3, HSCAN mask is 3, backhaul controller is zero,
   and all three controllers remain listen-only. `rules` must report no allowed
   vehicle messages. A CLI success is not a physical wire-level TX measurement.
3. Identify the actual connector/extension/splitter to be used. The typical
   mapping in `volt-gateway-topology.md` is not a continuity measurement.
   Resolve any unknown routing with the harness disconnected and unpowered;
   never measure resistance on a powered vehicle network. Do not fabricate a
   connection based solely on the nominal DLC pinout.

## Owner-assisted parked test

- Confirm current vehicle state: outside, stationary, in Park. Keep the laptop
  and leads secure. Do not drive or change primary driving software during this
  first test. Never operate an engine inside a garage for this purpose.
- Connect only the verified secondary-Panda vehicle connection and laptop USB.
  No guessed frames, diagnostic sessions, output controls or GMLAN wake pulses.
- With the existing `tools.volt_gateway.device_cli` and the owner's private
  pairing-file path, query `info`, `status`, `rules`, and `utilization --bus 3`.
  Also record `utilization --bus 0` and `--bus 1` for available HSCAN.
- Run `clear-ids --bus 3`, then `observe --bus 3 --seconds 30`. This returns a
  bounded ID/statistics table over USB, not a vehicle-bus forwarding stream.
  Preserve the output, firmware build ID and host UTC start/end timestamps.
- Query all three bus-status counters again. Record frames, errors, overflow
  and conservative utilization estimates. Any transmitted count must remain
  zero. Unexpected vehicle warnings, bus-off, errors or resets are a stop-and-
  investigate result, not a reason to enable unrestricted output.
- If SWCAN frames are present, run a short `capture --bus 3 --seconds 10` and
  preserve the bounded records/drop counters. A full capture buffer is expected
  on a busy network; do not claim it contains every frame for ten seconds.

Zero received frames do not establish that the vehicle has no SWCAN traffic:
check awake state, verified wiring, selected controller/PHY and bitrate before
drawing that conclusion. Manual HVAC button correlation is a later read-only
experiment after reception is established. Do not test side radar by creating
unsafe moving-vehicle situations.

## Separate installed CAN-backhaul gate

The normal peer would be the comma's integrated Tres, not another White Panda.
The present bench image does not transmit gateway messages on any vehicle bus.
Choose the actual shared HSCAN and transport IDs from wiring/log evidence first.
Then implement/review a narrowly bounded primary-Panda TX policy and an optional,
isolated comma service. Gateway/service absence, crash, permissions failure and
cold-boot unavailability must never block normal driving engagement. No generic
all-output safety mode or guessed arbitration IDs are permitted.
