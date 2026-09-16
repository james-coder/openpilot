"""Unprivileged fresh telemetry observer for the fixed LTE reset broker."""
import os
import sys
import time


def observation_safe(sm, now):
  for key in ('deviceState', 'pandaStates', 'managerState'):
    if not sm.seen[key] or not sm.valid[key] or not 0 <= now - sm.recv_time[key] < 2:
      return False
  if sm['deviceState'].started or not sm['pandaStates']:
    return False
  if any(p.ignitionLine or p.ignitionCan or p.controlsAllowed for p in sm['pandaStates']):
    return False
  gps = [p for p in sm['managerState'].processes if p.name == 'qcomgpsd']
  return len(gps) == 1 and not gps[0].running and not gps[0].shouldBeRunning


def check():
  if os.geteuid() == 0:
    return False  # user-writable openpilot/dependencies must never load as root
  sys.path.insert(0, '/data/openpilot')
  from cereal import messaging
  sm = messaging.SubMaster(['deviceState', 'pandaStates', 'managerState'])
  deadline = time.monotonic() + 7
  safe_since = None
  while time.monotonic() < deadline:
    sm.update(100)
    now = time.monotonic()
    if observation_safe(sm, now):
      if safe_since is None:
        safe_since = now
      if now - safe_since >= 3:
        return True
    else:
      safe_since = None
  return False


if __name__ == '__main__':
  sys.exit(0 if check() else 2)
