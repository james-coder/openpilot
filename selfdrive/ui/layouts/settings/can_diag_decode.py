"""Labels and one-frame summaries for GM diagnostic CAN traffic on this Volt (2017, 2nd gen).

Pure Python: no UI, CAN transmit, file or network access, and no DBC. It only describes frames the inspector has
already received, in constant time and without caching, so it cannot grow or stall the live panel.

Provenance of each ID label (shown to the user):
  GDS2     tester request ID; address and module name only, from the owner's GDS2 analysis.
  DERIVED  ECU reply ID inferred from request/reply pairing. Not independently confirmed; check before relying on it.
  CONFIRMED reply ID seen answering on the car in the owner's own testing (only 0x651, HVAC K33).
  standard OBD-II addressing: 0x7DF functional request, and 0x7E8-0x7EF = request ID + 8.
IDs that are not in the table are never labeled and never decoded. Nothing here knows manufacturer data identifiers
(DIDs), PIDs, status bits or DTC meanings; they are shown as raw hex. The only field decoded is the standard
P/C/B/U code text of a GMLAN DTC report frame, the same format the GM scanner (selfdrive/car/gm_diagnostics.py) reads.
"""
from typing import NamedTuple

GDS2, DERIVED, CONFIRMED, STANDARD = 'GDS2', 'DERIVED', 'confirmed on the car', 'standard'
# The panel's bus numbering (CanBus.OBSTACLE, "Obstacle/Radar"). Request 0x242 reaches the radar on this bus.
OBJECT_DETECTION_BUS = 1
OBD_FUNCTIONAL = 0x7DF
TESTER_BROADCAST = 0x101
ALL_NODES = 0xFE  # target byte at the start of a 0x101 frame

# Tester -> module physical requests: ID -> (module, note).
_REQUESTS = {
  0x241: ('BCM', ''),
  0x242: ('PSCM', 'power steering; the radar on the object-detection bus, keyless entry on the low-speed bus'),
  0x248: ('HVAC A26', 'low-speed bus'),
  0x251: ('HVAC K33', 'low-speed bus'),
  0x252: ('HMI', ''),
  0x254: ('Amplifier', ''),
  0x7E0: ('ECM', ''),
  0x7E1: ('HPCM', ''),
  0x7E4: ('HPCM2', 'hybrid battery module'),
  0x7E5: ('EBCM', ''),
  0x7E7: ('BECM', 'battery energy control module'),
}
# Module -> tester replies: reply ID -> the request ID whose module answers. Derived (inferred from the pairing) unless listed
# in _CONFIRMED_REPLIES. Unsegmented 0x5xx IDs carry the $A9 DTC reports and segmented 0x6xx IDs the $22 replies (inferred).
_REPLIES = {0x541: 0x241, 0x542: 0x242, 0x552: 0x252, 0x5E8: 0x7E0, 0x5E9: 0x7E1, 0x5EC: 0x7E4, 0x5ED: 0x7E5, 0x5EF: 0x7E7,
            0x641: 0x241, 0x642: 0x242, 0x648: 0x248, 0x651: 0x251, 0x652: 0x252, 0x7E9: 0x7E1}
_CONFIRMED_REPLIES = frozenset({0x651})  # K33 answered on the car in the owner's HVAC testing
# GMLAN "UUDT" report frames (0x5xx): DTC records `81 xx xx xx xx` rather than ISO-TP.
_REPORT_REPLIES = frozenset(address for address in _REPLIES if 0x540 <= address <= 0x55F or 0x5E8 <= address <= 0x5EF)


class DiagId(NamedTuple):
  address: int
  role: str  # 'request' | 'reply' | 'broadcast' | 'functional'
  module: str | None  # None when the module is not in the table
  basis: str
  who: str  # summary prefix, e.g. 'HMI <- tester' or 'HMI (derived) -> tester'
  text: str  # short description of the ID, ending with its provenance tag
  note: str = ''  # what the module is, or which other module shares the ID
  full: str = ''  # text with the note inserted before the provenance tag; used for search
  report: bool = False  # 0x81 DTC report frames are expected here


def _ident(address, role, module, basis, who, name, tag, note='', report=False):
  text = f'{name} {tag}'.rstrip()
  full = f'{name} ({note}) {tag}'.rstrip() if note else text
  return DiagId(address, role, module, basis, who, text, note, full, report)


def _request_id(address, module, note):
  return _ident(address, 'request', module, GDS2, f'{module} <- tester', f'{module} tester request ID', '[GDS2 analysis]', note)


def _reply_id(address, request):
  """Reply ID for the module that owns `request`; the module stays None when that request ID is not in the table."""
  module, note = _REQUESTS.get(request, (None, ''))
  report = address in _REPORT_REPLIES
  if address in _CONFIRMED_REPLIES:
    return _ident(address, 'reply', module, CONFIRMED, f'{module} -> tester', f'{module} reply ID', '[confirmed on the car]', note, report)
  if module:
    return _ident(address, 'reply', module, DERIVED, f'{module} (derived) -> tester', f'{module} reply ID',
                  f'[DERIVED from request 0x{request:X}, not confirmed]', note, report)
  return _ident(address, 'reply', None, STANDARD, 'unknown ECU reply', 'OBD-II response ID', f'(request 0x{request:X} + 8; module not in table)')


def _build():
  ids = {address: _request_id(address, *entry) for address, entry in _REQUESTS.items()}
  ids[TESTER_BROADCAST] = _ident(TESTER_BROADCAST, 'broadcast', None, GDS2, 'tester broadcast', 'tester-present/broadcast request ID', '[GDS2 analysis]')
  ids[OBD_FUNCTIONAL] = _ident(OBD_FUNCTIONAL, 'functional', None, STANDARD, 'all OBD-II ECUs <- tester', 'OBD-II functional request ID', '')
  ids.update((address, _reply_id(address, address-8)) for address in range(0x7E8, 0x7F0))  # OBD-II responses: request + 8
  ids.update((address, _reply_id(address, request)) for address, request in _REPLIES.items())
  # The same CAN IDs reach the Long Range Radar Sensor Module when seen on the object-detection bus.
  note = 'Long Range Radar Sensor Module on the object-detection bus'
  radar = {0x242: _ident(0x242, 'request', 'Radar', GDS2, 'Radar <- tester', 'Radar tester request ID', '[GDS2 analysis]', note)}
  for address in (0x542, 0x642):  # unsegmented (DTC report) and segmented replies
    radar[address] = _ident(address, 'reply', 'Radar', DERIVED, 'Radar (derived) -> tester', 'Radar reply ID',
                            '[DERIVED from request 0x242, not confirmed]', note, address in _REPORT_REPLIES)
  return ids, radar


_IDS, _RADAR_BUS_IDS = _build()

_SERVICES = {
  0x01: 'OBD-II current data', 0x02: 'OBD-II freeze-frame data', 0x03: 'OBD-II stored DTCs', 0x04: 'clear diagnostic information',
  0x05: 'OBD-II oxygen-sensor results', 0x06: 'OBD-II monitor results', 0x07: 'OBD-II pending DTCs', 0x08: 'OBD-II on-board control',
  0x09: 'OBD-II vehicle information', 0x0A: 'OBD-II permanent DTCs',
  0x10: 'initiate diagnostic operation', 0x19: 'read DTC information', 0x1A: 'read data by 1-byte ID',
  0x20: 'return to normal mode', 0x22: 'read data by ID', 0x27: 'security access', 0x2C: 'dynamically define message',
  0x3B: 'write data by ID', 0x3E: 'tester present', 0xA9: 'read diag info', 0xAA: 'read data by packet ID',
  0xAE: 'device control',
}  # service 0xFD and any other byte stay unnamed: "service 0xNN"
_READ_DTC_BY_STATUS = 0x81  # sub-function of 0xA9
_NRC = {
  0x10: 'general reject', 0x11: 'service not supported', 0x12: 'sub-function not supported', 0x13: 'incorrect length or format',
  0x14: 'response too long', 0x21: 'busy, repeat request', 0x22: 'conditions not correct', 0x24: 'request sequence error',
  0x31: 'request out of range', 0x33: 'security access denied', 0x35: 'invalid key', 0x36: 'too many attempts',
  0x37: 'time delay not expired', 0x78: 'response pending', 0x7E: 'sub-function not supported in this session',
  0x7F: 'service not supported in this session',
}
_FLOW = {0: 'clear to send', 1: 'wait', 2: 'overflow'}
MAX_CLASSIC_FRAME = 8


def label(address: int, bus: int | None = None) -> DiagId | None:
  """The table entry for a CAN ID, or None. Constant time."""
  if bus == OBJECT_DETECTION_BUS and (radar := _RADAR_BUS_IDS.get(address)) is not None:
    return radar
  return _IDS.get(address)


def label_text(address: int, bus: int | None = None) -> str:
  """One-line description of the ID, including its note ('' for IDs not in the table)."""
  ident = label(address, bus)
  return ident.full if ident else ''


def _hex(data) -> str:
  return data[:MAX_CLASSIC_FRAME].hex(' ').upper()


def _stmin(value: int) -> str:
  if value <= 0x7F:
    return f'{value} ms'
  return f'{(value-0xF0)*100} us' if 0xF1 <= value <= 0xF9 else 'reserved'


def _request(payload: bytes) -> str:
  sid = payload[0]
  if sid == 0xA9:
    if len(payload) > 1 and payload[1] == _READ_DTC_BY_STATUS:
      return f'read DTCs by status ({_hex(payload)})' if len(payload) > 2 else f'read DTCs by status, mask missing ({_hex(payload)})'
    return f'read diag info ({_hex(payload)})'
  return f'{_SERVICES.get(sid) or f"service 0x{sid:02X}"} ({_hex(payload)})'


def _reply(payload: bytes) -> str:
  sid = payload[0]
  if sid == 0x7F:
    if len(payload) < 3:
      return f'malformed negative reply ({_hex(payload)})'
    service = _SERVICES.get(payload[1]) or f'service 0x{payload[1]:02X}'
    reason = _NRC.get(payload[2]) or f'NRC 0x{payload[2]:02X}'
    return f'negative reply to {service}: {reason} ({_hex(payload)})'
  if sid < 0x40:
    return f'unexpected service 0x{sid:02X} in a reply ({_hex(payload)})'
  request = sid-0x40
  name = _SERVICES.get(request)
  return f'positive 0x{request:02X}{f" {name}" if name else ""} ({_hex(payload)})'


def _service(payload: bytes, from_tester: bool) -> str:
  return _request(payload) if from_tester else _reply(payload)


def _isotp(data: bytes, from_tester: bool) -> str:
  """ISO 15765-2 frame type: single, first, consecutive or flow control; classic 8-byte CAN only."""
  size = len(data)
  if size == 0:
    return 'empty frame'
  pci = data[0]
  kind = pci >> 4
  if kind == 0:
    length = pci & 0xF
    if not 1 <= length <= 7 or length > size-1:
      return f'malformed single frame (length {length}, {size-1} data bytes present)'
    return _service(data[1:1+length], from_tester)
  if kind == 1:
    total = ((pci & 0xF) << 8) | (data[1] if size > 1 else 0)
    if size < 3 or total < 8:
      return f'malformed first frame (total length {total}, {size} bytes)'
    return f'multi-frame start, {total} bytes: {_service(data[2:], from_tester)}'
  if kind == 2:
    return f'multi-frame continuation #{pci & 0xF}'
  if kind == 3:
    state = _FLOW.get(pci & 0xF)
    if state is None or size < 3:
      return f'malformed flow control ({_hex(data)})'
    return f'flow control: {state}, block size {data[1]}, STmin {_stmin(data[2])}'
  return f'not an ISO-TP frame (first byte {pci:02X})'


def _dtc_report(data: bytes) -> str:
  """GMLAN DTC report frame: 81, DTC high, DTC low, failure type, status, padding. Type and status stay raw."""
  if len(data) < 5:
    return f'malformed DTC report frame ({_hex(data)})'
  high, low, failure, status = data[1], data[2], data[3], data[4]
  if not (high or low or failure):
    return f'DTC report: end of report, status mask {status:02X}'
  if not (high or low):
    return f'DTC report: record without a code ({_hex(data)})'
  code = f'{"PCBU"[high >> 6]}{(high >> 4) & 3}{high & 15:X}{low:02X}'
  return f'DTC report: {code}, type {failure:02X}, status {status:02X}'


def summarize(address: int, data: bytes, bus: int | None = None) -> str:
  """'<who>: <what>' for a frame on a table ID, '' for any other ID. Never raises on any payload."""
  ident = label(address, bus)
  if ident is None:
    return ''
  try:
    size = len(data)
    if size > MAX_CLASSIC_FRAME:
      return f'{ident.who}: oversized payload ({size} bytes), not decoded'
    who, from_tester = ident.who, ident.role != 'reply'
    if ident.role == 'broadcast':
      # 0x101 carries the target address first, then a normal ISO-TP frame. 0xFE addresses every module.
      if size < 2:
        return f'{who}: short frame ({_hex(data)})'
      who = 'all modules <- tester' if data[0] == ALL_NODES else f'diagnostic address 0x{data[0]:02X} <- tester'
      data = data[1:]
    if ident.report and size and data[0] == 0x81:
      return f'{who}: {_dtc_report(data)}'
    return f'{who}: {_isotp(data, from_tester)}'
  except Exception:  # A display helper must not take the panel down; the raw bytes are still shown.
    return f'{ident.who}: undecodable frame'
