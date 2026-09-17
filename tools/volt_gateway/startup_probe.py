"""Bounded passive startup comparison. No CAN transmit, reset or deployment API.

Tres mode subscribes to boardd's existing CAN stream, never opens Panda USB.
White mode uses the paired USB read-only gateway commands. Stdout is JSONL;
retain it outside /tmp. UTC timestamps are host clocks, not synchronized edges.
"""
import argparse
from collections import Counter
from datetime import UTC, datetime
import json
from pathlib import Path
import resource
import struct
import time


def emit(side, **fields):
  print(json.dumps({'side': side, 'utc': datetime.now(UTC).isoformat(), **fields}), flush=True)


def tres(seconds):
  from cereal import messaging
  sock = messaging.sub_sock('can', timeout=100)
  counts = Counter()
  ids = {b: set() for b in range(3)}
  batches = invalid = 0
  start = last = time.monotonic()
  emit('tres', event='armed', seconds=seconds, source='existing boardd CAN publication')
  while time.monotonic() - start < seconds:
    event = messaging.recv_one(sock)
    if event is not None:
      batches += 1
      if not event.valid:
        invalid += 1
      for frame in event.can:
        bus = int(frame.src)
        if bus not in ids:
          continue  # Exclude TX echoes/rejections and unselected buses.
        counts[bus] += 1
        if len(ids[bus]) < 512:
          ids[bus].add(int(frame.address))
    now = time.monotonic()
    if now - last >= 1:
      emit('tres', elapsed=round(now-start, 3), rx=dict(counts),
           unique_ids={b: len(v) for b, v in ids.items()}, batches=batches, invalid_batches=invalid)
      last = now
  emit('tres', event='finished', rx=dict(counts), ids={b: sorted(v) for b, v in ids.items()})


def white(seconds, pairing):
  from openpilot.tools.volt_gateway.device_cli import Device, credentials
  from openpilot.tools.volt_gateway.usb_transport import UsbTransport
  keys = credentials(pairing)
  with UsbTransport(keys['device'].hex()) as transport:
    device = Device(transport, keys)
    try:
      info = device.command(1)
      if (len(info) != 46 or info[:2] != b'\1\1' or info[38:41] != bytes([3, 3, 0]) or
          device.command(14) != b'\0'):
        raise ValueError('expected CAN-silent USB bench policy')
      emit('white', event='armed', seconds=seconds, build=info[6:38].hex())
      rx_health = bool(int.from_bytes(info[2:6], 'big') & 0x20)
      start = time.monotonic()
      while time.monotonic() - start < seconds:
        status = struct.unpack('>Q6I', device.command(2))
        buses = {}
        for bus in (0, 1, 3):
          v = struct.unpack('>14I', device.command(3, bytes([bus])))
          buses[bus] = dict(zip(('rx', 'malformed', 'overflow', 'tx', 'arbitration_lost', 'tx_errors', 'esr'), v[:7], strict=True))
          if rx_health:
            counters = struct.unpack('>4I', device.command(16, bytes([bus])))
            buses[bus].update(zip(('software_drops', 'irq_calls', 'queue_peak', 'max_queue_age_ms'), counters, strict=True))
          if v[3] or v[5] or v[6] & 6:
            raise ValueError('unexpected TX or CAN fault; observation stopped')
        emit('white', uptime_ms=status[0], reset_flags=status[1], buses=buses)
        time.sleep(3)
      emit('white', event='finished')
    finally:
      device.close()


def main():
  resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--side', choices=('white', 'tres'), required=True)
  parser.add_argument('--seconds', type=int, default=180)
  parser.add_argument('--pairing', type=Path)
  args = parser.parse_args()
  if not 1 <= args.seconds <= 300 or (args.side == 'white' and args.pairing is None):
    parser.error('duration 1..300 seconds and White pairing path required')
  if args.side == 'tres':
    tres(args.seconds)
  else:
    white(args.seconds, args.pairing)


if __name__ == '__main__':
  main()
