"""Read-only telemetry gate for Bluetooth initialization; no vehicle publishers."""
import time


class UnsafeInitialization(RuntimeError):
  pass


class OffroadGate:
  """Permit Bluetooth startup with ignition truly off, or verifiably parked and disengaged.

  Some vehicles (e.g. the Volt's low-speed GMLAN) keep reporting ignitionCan long after the
  driver has parked, because accessory/telematics modules stay on the bus. Requiring a literal
  ignition-off would then never fire, so a car that is in Park, at standstill, and disengaged
  (and not letting openpilot actuate) is treated as equally safe to start the radio.
  """
  CORE = ('deviceState', 'pandaStates')
  PARKED = ('carState', 'selfdriveState')

  def __init__(self):
    from cereal import messaging
    self.sm = messaging.SubMaster([*self.CORE, *self.PARKED])

  def _fresh(self, service, now):
    return self.sm.seen[service] and self.sm.valid[service] and now - self.sm.recv_time[service] < 2

  def _describe(self, services, now):
    return ', '.join(f'{k}: seen={self.sm.seen[k]}, valid={self.sm.valid[k]}, age={now-self.sm.recv_time[k]:.1f}s' for k in services)

  def check(self, wait=False):
    deadline = time.monotonic() + (3 if wait else 0)
    while True:
      self.sm.update(0)
      now = time.monotonic()
      if all(self._fresh(k, now) for k in self.CORE):
        pandas = self.sm['pandaStates']
        if not pandas:
          raise UnsafeInitialization('No Panda telemetry')
        if any(p.controlsAllowed for p in pandas):
          raise UnsafeInitialization('Waiting for controls disallowed')
        ignition = self.sm['deviceState'].started or any(p.ignitionLine or p.ignitionCan for p in pandas)
        if not ignition:
          return  # Fully offroad: always safe.
        if not all(self._fresh(k, now) for k in self.PARKED):
          raise UnsafeInitialization('Waiting for fresh parked-state telemetry (' + self._describe(self.PARKED, now) + ')')
        car = self.sm['carState']
        if car.gearShifter == 'park' and car.standstill and abs(car.vEgo) < 0.1 and not self.sm['selfdriveState'].enabled:
          return  # Ignition/accessory still on, but verifiably parked and disengaged.
        raise UnsafeInitialization('Waiting for Park, stopped, and disengaged')
      if now >= deadline:
        raise UnsafeInitialization('Waiting for fresh telemetry (' + self._describe([k for k in self.CORE if not self._fresh(k, now)], now) + ')')
      time.sleep(.05)


_gate = None


def offroad():
  global _gate
  if _gate is None:
    _gate = OffroadGate()
    _gate.check(wait=True)
  else:
    _gate.check()
