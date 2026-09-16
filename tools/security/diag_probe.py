"""Operator-only, bounded read-only DIAG evidence; no GPS/configuration changes."""
import argparse
import json
import time
from struct import pack, unpack_from

REQUEST = b'\x73' + pack('<3xI', 1)
ERRORS = {19: 'invalid_command', 20: 'invalid_parameter', 21: 'invalid_length', 24: 'invalid_mode'}


def classify(opcode, payload):
  # Never print unknown payloads, identifiers or GNSS information.
  result = {'opcode': opcode, 'payload_bytes': len(payload), 'passed': False}
  if opcode in ERRORS:
    result.update(error=ERRORS[opcode], starts_with_request=payload.startswith(REQUEST),
                  equals_request=payload == REQUEST)
  elif opcode == 115 and len(payload) == 75:
    operation, status = unpack_from('<3xII', payload)
    masks = unpack_from('<16I', payload, 11)
    result.update(operation=operation, status=status, mask_bounds_valid=max(masks) <= 4096,
                  passed=operation == 1 and status == 0 and max(masks) <= 4096)
  else:
    result['error'] = 'unexpected_reply'
  return result


def gate(sm, offroad):
  offroad()
  deadline = time.monotonic() + 3
  while time.monotonic() < deadline:
    sm.update(100)
    if sm.seen['managerState'] and sm.valid['managerState'] and time.monotonic()-sm.recv_time['managerState'] < 2:
      gps = [p for p in sm['managerState'].processes if p.name == 'qcomgpsd']
      if len(gps) != 1 or any(p.running or p.shouldBeRunning for p in gps):
        raise RuntimeError('GPS ownership unavailable or active; refusing DIAG access')
      return
  raise RuntimeError('Fresh manager state unavailable')


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--queries', type=int, choices=range(1, 6), default=3)
  args = parser.parse_args()
  from cereal import messaging
  from openpilot.system.aranet.safety import offroad
  from openpilot.system.qcomgpsd.modemdiag import ModemDiag, send_recv
  sm = messaging.SubMaster(['managerState'])
  gate(sm, offroad)
  diag = ModemDiag()  # Exclusive serial ownership; no modem resets.
  try:
    for i in range(args.queries):
      gate(sm, offroad)
      start = time.monotonic()
      try:
        opcode, payload = send_recv(diag, REQUEST[0], REQUEST[1:])
        report = classify(opcode, payload)
      except (ValueError, OSError, TimeoutError) as exc:
        report = {'passed': False, 'error': type(exc).__name__}
      print(json.dumps(dict(query=i+1, elapsed_ms=round((time.monotonic()-start)*1000), **report)), flush=True)
      time.sleep(.25)
  finally:
    diag.serial.close()


if __name__ == '__main__':
  main()
