"""Bounded, read-only emissions queries. The caller owns CAN and checks permission every tick."""
import copy
import math
from collections import defaultdict
from datetime import datetime, UTC

from opendbc.car.can_definitions import CanData
from opendbc.car.uds import CanClient, IsoTpMessage

OBD_SAFETY_FLAG = 8
SERVICES = (("lamp", b"\x01\x01"), ("stored", b"\x03"), ("pending", b"\x07"), ("permanent", b"\x0a"))
RESPONSE_ADDRS = range(0x7E8, 0x7F0)
STAGE_TIMEOUT = 2.0
SCAN_TIMEOUT = 10.0
MAX_REPLY = 512  # service, count, and at most 255 two-byte DTCs
MAX_FRAMES_PER_TICK = 128


def scan_block_reason(*, supported, started, initialized, fresh, park, speed, enabled, firmware_ready):
  if not supported:
    return "Check-engine scans are available for the supported Volt."
  if not started:
    return "Turn the car on and shift to Park."
  if not initialized or not fresh:
    return "Waiting for fresh vehicle data."
  if enabled:
    return "Disengage openpilot before scanning."
  if not park or not math.isfinite(speed) or abs(speed) >= .1:
    return "Shift to Park and keep the car stationary."
  if not firmware_ready:
    return "Waiting for diagnostic firmware support."
  return ""


def decode_reply(name: str, data: bytes) -> dict:
  if name == "lamp":
    if len(data) != 6 or data[:2] != b"\x41\x01":
      raise ValueError("Malformed lamp response")
    return {"state": "ok", "mil": bool(data[2] & 0x80), "count": data[2] & 0x7F}
  expected = {"stored": 0x43, "pending": 0x47, "permanent": 0x4A}[name]
  if len(data) < 2 or data[0] != expected:
    raise ValueError("Unexpected service response")
  end = 2 + data[1] * 2
  if len(data) < end or any(data[end:]):
    raise ValueError("Malformed fault-code count or padding")
  codes = set()
  for hi, lo in zip(data[2:end:2], data[3:end:2], strict=True):
    if hi or lo:
      codes.add(f'{"PCBU"[hi >> 6]}{(hi >> 4) & 3}{hi & 15:X}{lo:02X}')
  return {"state": "ok", "codes": sorted(codes)}


class ObdScanner:
  def __init__(self):
    self.active = False
    self.revision = 0
    self.report = None
    self.status = {"version": 1, "state": "idle", "message": "Ready to scan."}
    self._out = []
    self._messages = {}
    self._buffers = defaultdict(list)

  def start(self, request_id: str, vehicle: dict, now: float):
    if self.active:
      return
    self.active = True
    self.report = None
    self._started = now
    self._stage = -1
    self.status = {"version": 1, "request_id": request_id, "vehicle": vehicle, "state": "scanning",
                   "timestamp": datetime.now(UTC).isoformat(), "ecus": {}}
    self._next_stage(now)

  def _next_stage(self, now):
    self._stage += 1
    self._out = []
    self._buffers.clear()
    self._messages = {}
    if self._stage == len(SERVICES):
      self._finish()
      return
    name, request = SERVICES[self._stage]
    self._deadline = now + STAGE_TIMEOUT
    self.status.update(message=f"Reading {name} ({self._stage + 1}/4)...", progress=self._stage)
    for addr in RESPONSE_ADDRS:
      client = CanClient(self._send, lambda a=addr: self._receive(a), addr - 8, addr, 0)
      msg = IsoTpMessage(client, timeout=0, separation_time=.01)
      msg.send(request, setup_only=True)
      self._messages[addr] = msg
    self._out.append(CanData(0x7DF, (bytes([len(request)]) + request).ljust(8, b"\x00"), 0))
    self.revision += 1

  def _send(self, addr, data, bus):
    # Responses can only elicit this one fixed flow-control packet.
    if bus != 0 or addr + 8 not in RESPONSE_ADDRS or data != b"\x30\x00\x0a\x00\x00\x00\x00\x00":
      raise ValueError("Unexpected diagnostic transmission")
    self._out.append(CanData(addr, data, bus))

  def _receive(self, addr):
    return self._buffers.pop(addr, [])

  def _record(self, addr, result):
    name = SERVICES[self._stage][0]
    self.status["ecus"].setdefault(f"{addr:03X}", {})[name] = result
    self._messages.pop(addr, None)
    self.revision += 1

  def cancel(self, reason="Scan cancelled."):
    if self.active:
      self.active = False
      self._out.clear()
      self._messages.clear()
      self._buffers.clear()
      self.status.update(state="cancelled", message=reason)
      self.revision += 1

  def _finish(self):
    self.active = False
    if self._stage < len(SERVICES):
      for addr, msg in list(self._messages.items()):
        if msg.rx_dat:
          self._record(addr, {"state": "timeout", "error": "Incomplete response"})
    for ecu in self.status["ecus"].values():
      for name, _ in SERVICES:
        ecu.setdefault(name, {"state": "timeout", "error": "No response"})
    replies = [result for ecu in self.status["ecus"].values() for result in ecu.values()]
    success = any(r["state"] == "ok" for r in replies)
    complete = bool(replies) and all(r["state"] == "ok" for r in replies)
    state = "complete" if complete else "partial" if success else "error"
    message = {"complete": "Scan complete.", "partial": "Partial scan: some requests could not be read.",
               "error": "No readable responses. Check that the car is on and in Park."}[state]
    self.status.update(state=state, message=message, progress=4)
    if success:
      self.report = copy.deepcopy(self.status)
    self.revision += 1

  def tick(self, now: float, frames: list[CanData], block_reason: str) -> list[CanData]:
    if not self.active:
      return []
    if block_reason:
      self.cancel(block_reason)
      return []
    if len(frames) > MAX_FRAMES_PER_TICK:
      self.cancel("Scan stopped: excessive diagnostic traffic.")
      return []
    if now - self._started >= SCAN_TIMEOUT:
      self._finish()
      self._out.clear()
      return []
    if now >= self._deadline:
      for addr, msg in list(self._messages.items()):
        if msg.rx_dat:
          self._record(addr, {"state": "timeout", "error": "Incomplete response"})
      self._next_stage(now)
      # Discard the previous stage's trailing frames rather than attributing them to a new request.
      frames = []
    if not self.active:
      return []
    name, request = SERVICES[self._stage]
    for addr, data, bus in frames:
      if bus != 0 or addr not in self._messages:
        continue
      try:
        if len(data) != 8:
          raise ValueError("Malformed CAN frame length")
        kind = data[0] >> 4
        if kind == 1 and ((data[0] & 15) << 8 | data[1]) > MAX_REPLY:
          raise ValueError("Response too large")
        if kind not in (0, 1, 2):
          raise ValueError("Unexpected ISO-TP frame")
        # Ignore replies to other services before ISO-TP can send flow control.
        if kind in (0, 1):
          offset = 1 if kind == 0 else 2
          if data[offset] not in (request[0] + 0x40, 0x7F):
            continue
        if f"{addr:03X}" not in self.status["ecus"]:
          self.status["ecus"][f"{addr:03X}"] = {}
          self.revision += 1
        self._buffers[addr].append(CanData(addr, data, bus))
        reply, _ = self._messages[addr].recv()
        if reply is not None:
          if reply[:1] == b"\x7f":
            if len(reply) != 3 or reply[1] != request[0]:
              raise ValueError("Malformed negative response")
            if reply[2] == 0x78:
              # Re-arm reception without extending the fixed stage deadline.
              self._messages[addr].send(request, setup_only=True)
            else:
              self._record(addr, {"state": "unsupported" if reply[2] in (0x11, 0x12, 0x31) else "error",
                                  "error": f"ECU response 0x{reply[2]:02X}"})
          else:
            self._record(addr, decode_reply(name, reply))
      except (AssertionError, ValueError, IndexError) as e:
        self._out = [frame for frame in self._out if frame.address != addr - 8]
        self._record(addr, {"state": "error", "error": str(e) or "Malformed response"})
    out, self._out = self._out, []
    return out
