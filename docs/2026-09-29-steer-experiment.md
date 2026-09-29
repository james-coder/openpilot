# Steering A/B experiment (friction assist), 2026-09-29

Question it answers: does a small torque nudge in the direction of the needed steering motion reduce the slow
side-to-side weave at ~35 mph, versus the stock controller? Stiction (about 0.15 Nm breakaway friction) plus a
proportional-only controller lets the wheel stick, then release, at 0.5 Hz and 1.5-2.6 Hz.

## What runs
- `selfdrive/controls/lib/steer_experiment.py`: armed by the `VoltSteerExperiment` param (JSON with `armed_at`;
  cleared on manager start and when finished). Alternates mode A (bit-for-bit the stock controller) and mode B
  (friction assist, optional gain/feedforward scales) in 60 s blocks, randomized within A/B pairs. Time counts
  only while openpilot steers, 25-50 mph, |accel| < 0.8 m/s2, straight-ish, no driver steering; any break over
  0.5 s restarts the block. A/B transitions ramp over 1 s.
- Friction assist: odd in the angle error, zero inside 0.1 deg (sensor noise), full at 0.6 deg, hard cap 0.06
  torque units (about 0.18 Nm of the 3 Nm limit). No delay is added to the command. Panda limits are untouched.
- Off in every other case: not armed, stale (>6 h) or malformed arming, below 5 m/s, driver steering, safety
  limited, any exception (the experiment disables itself and the controller returns to stock gains).
- All file I/O (`/data/steer_exp/status.json`, `blocks.jsonl`) is in a daemon thread; the control loop only does
  arithmetic.
- On-screen: third badge row "TEST n/10" (the mode is never shown; dims with "(paused)" while not counting;
  "TEST DONE" at the end). Optional: if the overlay fails it disables itself and driving is unaffected.

## Run it
1. Parked, on the new commit (reboot clears the param, so arm after the reboot):
   `ssh comma 'cd /data/openpilot && PYTHONPATH=. /usr/local/venv/bin/python -c "from openpilot.selfdrive.controls.lib import steer_experiment as se; print(se.arm())"'`
2. Drive with cruise set at 35: out on the road until the counter reads 5/10, turn around, finish at 10/10.
   Blocks pause when you steer, brake hard or leave the band; just keep driving steadily.
3. Home Wi-Fi: `.scratch/analysis/driveway_watch.sh` polls TCP port 22 (kernel-level, no work on the car), waits
   for the car to leave and return, checks it is parked, pulls block records, summarizes only the log segments
   newer than the arming marker on the car (nice'd, 4 at a time) and prints the A vs B report.
4. Abort at any time: `se.disarm()` (or reboot). Nothing else changes.

## Verified / not verified
- Unit tests (`selfdrive/controls/tests/test_steer_experiment.py`, `selfdrive/ui/tests/test_steer_test_overlay.py`):
  config clamping, friction shape and cap, schedule balance, ramps, gap voiding, qualifying conditions, file
  output, controller bit-for-bit stock when off or in mode A, bounded assist in mode B, fallback on exception.
- Not verified on the device or the road: anything about feel, whether B helps, or the overlay rendering on the
  comma screen. Cold-boot, storage-full and process-crash checks on the car are still to do; the experiment is
  in the controller process and the overlay in the UI process, so a crash of either follows their normal paths.
