"""Bounded, immutable report archive. Only call from the background I/O thread."""
import hashlib
import json
import os
import tempfile
from pathlib import Path

from openpilot.selfdrive.car.gm_egr_data import MAX_REPORT_BYTES

ARCHIVE_DIR = Path('/data/media/0/diagnostics/gm')


def archive_report(report, directory=ARCHIVE_DIR):
  payload = json.dumps(report, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
  if len(payload) > MAX_REPORT_BYTES:
    raise ValueError('Diagnostic report exceeds archive size limit')
  directory = Path(directory)
  directory.mkdir(parents=True, exist_ok=True, mode=0o700)
  # Content-addressed suffix makes retries idempotent without overwriting evidence.
  stamp = ''.join(c for c in report.get('timestamp', '') if c.isdigit())[:20] or 'unknown'
  target = directory / f'{stamp}_{hashlib.sha256(payload).hexdigest()}.json'
  fd, temporary = tempfile.mkstemp(prefix='.report-', dir=directory)
  try:
    with os.fdopen(fd, 'wb') as stream:
      stream.write(payload)
      stream.flush()
      os.fsync(stream.fileno())
    try:
      os.link(temporary, target)  # Atomic publication, never replaces an existing file.
    except FileExistsError:
      if target.read_bytes() != payload:
        raise ValueError('Archive collision') from None
    dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
      os.fsync(dir_fd)
    finally:
      os.close(dir_fd)
  finally:
    os.unlink(temporary)
  return str(target)
