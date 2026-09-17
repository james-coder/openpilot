"""Bounded passive ISO-TP observer. Never sends requests or flow control."""
from openpilot.selfdrive.car.gm_egr_analysis import PASSIVE_KEYS, decode_sample
from openpilot.selfdrive.car.gm_egr_data import MAX_CAPTURE


class EgrPassiveObserver:
  def __init__(self):
    self.samples = []
    self.evidence = []
    self.full = False
    self._assembly = None

  def feed(self, now, address, data, bus):
    if self.full or bus != 0 or address not in (0x7E0, 0x7DF, 0x7E8) or len(data) != 8:
      return
    if len(self.evidence) >= MAX_CAPTURE:
      self.full = True
      return
    self.evidence.append({'direction': 'rx', 'mono': now, 'address': address, 'bus': bus, 'data': data.hex()})
    if address != 0x7E8:
      return
    if self._assembly and not 0 <= now - self._assembly['start'] <= 2:
      self._assembly = None
    kind = data[0] >> 4
    payload = None
    if kind == 0:
      self._assembly = None
      if 2 <= data[0] <= 7:
        payload = data[1:1 + data[0]]
    elif kind == 1:
      length = ((data[0] & 15) << 8) | data[1]
      self._assembly = {'start': now, 'length': length, 'next': 1, 'data': data[2:]} if 8 <= length <= 4095 else None
    elif kind == 2 and self._assembly:
      assembly = self._assembly
      if data[0] & 15 != assembly['next']:
        self._assembly = None
        return
      assembly['next'] = (assembly['next'] + 1) & 15
      assembly['data'] += data[1:]
      if len(assembly['data']) >= assembly['length']:
        payload = assembly['data'][:assembly['length']]
        self._assembly = None
    if payload is not None and len(payload) >= 2 and payload[0] >= 0x40:
      key = f'{payload[0] - 0x40:02X}:{payload[1]:02X}'
      if key in PASSIVE_KEYS:
        try:
          self.samples.append(decode_sample(key, payload.hex(), now))
        except ValueError:
          pass  # Malformed/negative/unknown replies remain in raw evidence only.
