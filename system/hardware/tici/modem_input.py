"""Bounded parsing of untrusted modem responses; no hardware or privilege changes."""
import re
import time


def read_at_response(port, timeout=5.0, *, reject_errors=True):
  """Read one response, bounding elapsed time as well as bytes (including blanks).

  Serial per-read timeouts alone do not bound a peer that continuously sends.
  Leave bytes following the terminal line for the caller's normal drain policy.
  """
  deadline = time.monotonic() + timeout
  previous_timeout = port.timeout
  lines, line = [], bytearray()
  total = count = 0
  try:
    while True:
      remaining = deadline - time.monotonic()
      if remaining <= 0:
        raise TimeoutError('AT response deadline exceeded')
      port.timeout = remaining
      raw = port.read(1)
      if not raw:
        raise TimeoutError('AT response incomplete')
      total += len(raw)
      line.extend(raw)
      if total > 65536 or len(line) > 16384:
        raise RuntimeError('AT response size limit exceeded')
      if raw != b'\n':
        continue
      count += 1
      if count > 256:
        raise RuntimeError('AT response line limit exceeded')
      value = line.decode('utf-8', errors='strict').strip()
      line.clear()
      if value == 'OK':
        return lines
      if value == 'ERROR' or value.startswith('+CME ERROR'):
        if reject_errors:
          raise RuntimeError('AT command rejected')
        return lines
      if value:
        lines.append(value)
  except UnicodeError as exc:
    raise RuntimeError('Invalid AT response encoding') from exc
  finally:
    port.timeout = previous_timeout


def logical_channel(value):
  # ISO 7816 logical channels: 1..19; 0 is the basic, not an opened channel.
  if not re.fullmatch(r'(?:[1-9]|1[0-9])', value):
    raise RuntimeError('Invalid logical channel returned by modem')
  return value
