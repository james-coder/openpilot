"""Formerly a telemetry gate that only allowed Bluetooth initialization while parked.

Removed 2026-09-21 at the owner's request: it kept the CO2 badge off for whole drives
whenever the device finished booting after the car was already in Drive. Bluetooth
initialization touches only the device's own radio (power ioctl, firmware upload, HCI
attach on its own UART); it never talks to the panda or the car. `offroad()` is kept as a
no-op so the callers and the install remount path do not change shape.
"""


def offroad():
  return
