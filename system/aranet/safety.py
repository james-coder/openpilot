"""Read-only telemetry gate for Bluetooth initialization; no vehicle publishers."""
import time


class UnsafeInitialization(RuntimeError):
  pass


class OffroadGate:
  def __init__(self):
    from cereal import messaging
    self.sm = messaging.SubMaster(['deviceState', 'pandaStates'])

  def check(self, wait=False):
    deadline = time.monotonic() + (3 if wait else 0)
    while True:
      self.sm.update(0)
      now = time.monotonic()
      fresh = all(self.sm.seen.values()) and all(self.sm.valid.values()) and all(
        now - self.sm.recv_time[k] < 2 for k in self.sm.services)
      if fresh:
        pandas = self.sm['pandaStates']
        if not pandas or self.sm['deviceState'].started or any(p.ignitionLine or p.ignitionCan or p.controlsAllowed for p in pandas):
          raise UnsafeInitialization('Waiting for ignition off')
        return
      if now >= deadline:
        raise UnsafeInitialization('Waiting for fresh device/Panda telemetry')
      time.sleep(.05)


_gate = None


def offroad():
  global _gate
  if _gate is None:
    _gate = OffroadGate()
    _gate.check(wait=True)
  else:
    _gate.check()
