# Dashcam event saving

Saves the footage around a hard-braking, ABS or extreme-g event so the deleter never recycles it, and shows a silent
"EVENT SAVED" banner. Optional: it is never part of driving, engagement or disengagement.

## What it does
- `eventd` (onroad) watches `carState` and `accelerometer`. Detection logic: `system/review/detector.py`.
- On an event it sets `user.preserve` on the current log segment (the deleter also keeps the two before it), keeps flagging
  new segments for 30 s while the episode runs, appends to `/data/media/0/review/events.jsonl`, and writes
  `/data/review/last_event.json`, which the onroad banner (`selfdrive/ui/onroad/event_overlay.py`) shows for 10 s.
- The deleter keeps flagged footage up to a 15 GiB budget (`PRESERVE_BUDGET_BYTES`), newest first; beyond it the oldest
  flagged footage becomes ordinary again, so protection can never fill the disk. It also now reads the flag uncached,
  so a flag set after the deleter started is seen, and any error computing protection means "protect nothing".

## Triggers (cautious; calibrated on 33 min of normal driving plus the 2026-10-01 near-miss, small sample)
| Kind | Trigger |
|---|---|
| hard brake | wheel-speed decel >= 0.45 g for 0.3 s, pedal pressed, >= 2.5 m/s |
| ABS stop | pedal pressed, wheel decel >= 0.6 g and 0.35 g above the IMU's |
| extreme g | horizontal >= 0.9 g (0.1 s smoothed) or a >10 Hz jolt >= 0.9 g |

Checked against 416 stored segments (5.2 h of driving, held out from the thresholds): no ABS or extreme-g false alarms and
two genuine firm stops (0.51 g and 0.60 g) crossed the hard-brake line, about 0.4 per hour. Normal-driving peaks of the
0.1 s-smoothed horizontal g stayed at or below 0.66 g, well under the 0.9 g trigger.

**Not detected:** swerves (no calibration yet), and light impacts. Jolt cannot separate a light knock from a pothole on this
windshield mount (horizontal jolt reaches 0.95 g at the 99th percentile of normal segments, and an earlier jolt trigger fired
about once an hour on bumps), so an `impact_jolt_g` trigger exists but is disabled. Only strong impacts (>= 0.9 g smoothed)
and braking events are caught. Re-tune with `python -m openpilot.tools.dashcam.detector_replay <telemetry.npz>`.

## Turning it off
Create `/data/review/eventd.off` and restart the drive; the process then idles. No rebuild needed.

## Verification status
Unit tests (detector, recorder, live loop with fake sockets, SIGINT, optional-process engagement/disengagement paths for
absent/stopped/crashed/permission-denied/cold-boot) and the deleter tests pass on the workstation. Replay of the real
2026-10-01 incident produces exactly one episode. **Not yet done:** deployment, behavior on the car, a banner on the real
screen, engagement behavior after the change, and false-alarm rate over real drives. See AGENTS.md before deploying.

## Deploying (needs James's go-ahead; car parked, ignition off, never while moving)
The car updates by branch (`git fetch origin <branch> && git reset --hard origin/<branch>` on the device, then a restart).
This feature is pure Python (no new params keys, no native build), so no scons step is needed.
1. Back up the data James needs first (policy `comma-backup-before-device-changes`).
2. Confirm `IsOnroad=0` and enough free space; note the car's current commit for rollback.
3. Update the branch on the car, then run on the device: `pytest system/review system/loggerd/tests/test_deleter.py
   selfdrive/selfdrived/tests/test_optional_cabin_process.py`.
4. Restart the manager (or reboot) while parked. Check `eventd` runs only onroad, the banner file is absent, and `SIGINT`
   stops it at once. Check the deleter and UI still start and the car still engages normally on the next drive.
5. A real trigger cannot be staged safely (never inject fake carState onto the live bus). The first real event verifies
   the xattr, `events.jsonl` and the banner. Until then those are verified by tests only.
Rollback: restore the previous commit and restart; `touch /data/review/eventd.off` disables just this feature.
