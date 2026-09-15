#!/usr/bin/env python3
import numpy as np

from openpilot.common.pid import PIDController
from openpilot.system.hardware import HARDWARE

# raise fan setpoint on tici/tizi to reduce noise
# after raising LMH threshold in AGNOS 18.1 to prevent CPU throttling
OFFSET = 0 if HARDWARE.get_device_type() == "mici" else 5


class FanController:
  def __init__(self, rate: int) -> None:
    self.last_ignition = False
    self.parked_extra = 0.
    self.rate = rate
    self.controller = PIDController(k_p=0, k_i=4e-3, rate=rate)

  def update(self, cur_temp: float, ignition: bool, parked_cooling: bool = True) -> int:
    self.controller.pos_limit = 100 if ignition else 30
    self.controller.neg_limit = 30 if ignition else 0

    if ignition != self.last_ignition:
      self.controller.reset()
    self.last_ignition = ignition

    original = int(self.controller.update(
                 error=(cur_temp - (75 + OFFSET)),  # temperature setpoint in C
                 feedforward=np.interp(cur_temp, [60.0 + OFFSET, 100.0 + OFFSET], [0, 100])
              ))
    # Experimental high-temperature headroom only. Preserve the original ramp
    # below 70C and all onroad behavior; this does not measure OLED temperature.
    if ignition or not parked_cooling or OFFSET == 0:
      self.parked_extra = 0.
      return original
    target = max(original, float(np.interp(cur_temp, [70, 75], [12.5, 40]))) if cur_temp > 70 else original
    # Limit additional output changes to 2 percentage points per second.
    self.parked_extra += float(np.clip(target-original-self.parked_extra, -2/self.rate, 2/self.rate))
    return min(40, int(original + self.parked_extra))
