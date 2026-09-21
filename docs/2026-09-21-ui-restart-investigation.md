# 2026-09-21: comma logo mid-drive (UI restart), CO2 badge fixes, wind badge

## What happened

During the 10-hour trip the driver saw the comma logo while driving. Logs pulled over the
comma.ai SSH tunnel while the car was still moving:

- The device did **not** reboot. It booted at 18:16 UTC and was still up 5 h later.
  Route 9f ended at 19:02:57 when the car was put in Park for about two minutes; route a0
  started 2976 s into the same boot when it went back into Drive.
- At **23:28:08 UTC** the manager logged `Restarting ui (exitcode -6)` and started a new UI
  half a second later. Exit code −6 is SIGABRT: a native abort, not a Python exception.
  That restart is the comma logo. It was the only process restart of the drive; the UI kept
  one PID from 19:06 to 23:28.
- Nothing else recorded the abort. The journal on this device is not persistent across
  boots and had no line from the UI; dmesg had no segfault or OOM; the process's stderr goes
  to the manager's tmux pane, whose 3000-line history is flooded by the manager's process
  list within minutes. Memory was 65-67 % used, no swap, no OOM kill.
- Correlation only: kernel Bluetooth messages (`hci0 advertising data length corrected`,
  `ACL packet for unknown connection handle`) were rate-limited at 23:28:00, eight seconds
  before the abort. Not evidence of cause.

## Changes

- `selfdrive/ui/ui.py`: `faulthandler` now writes every thread's Python stack to
  `/data/log/ui_faults.log` on a fatal signal, so the next abort is diagnosable.
- CO2 badge (commit 470b78d1d): parked-only Bluetooth gate removed, leftover `hci0` adopted
  instead of refused, stale window 240 s -> 900 s, badge keeps a reading for
  max(900 s, 3x interval), both services log to the journal and always restart.
- Wind badge (commits 48df7ad32, aadacfa4e): `windd` daemon + `WindOverlay`, Open-Meteo.

## Deploy

All on `main-james`. Deploy needs the car parked (manager restart plus reinstall of the two
CO2 systemd units); a watcher on trader does it over the tunnel at the first parked check.

## Next time it happens

```
ssh comma-tunnel 'tail -80 /data/log/ui_faults.log'
```
