"""Document actual DIAG behavior without changing production retry/correlation."""

from struct import pack

import pytest

from openpilot.system.qcomgpsd import modemdiag as d


class Replies:
  def __init__(self, frames):
    self.frames = iter(frames)
    self.sent = []
    self.deadlines = []

  def send(self, opcode, payload):
    self.sent.append((opcode, payload))

  def recv(self, deadline):
    self.deadlines.append(deadline)
    value = next(self.frames)
    if isinstance(value, Exception):
      raise value
    return value


@pytest.mark.parametrize('opcode', [19, 20, 21, 24, 38])
def test_first_non_log_reply_is_not_silently_correlated(opcode):
  valid = (115, pack('<3xII16I', 1, 0, *([0] * 16)))
  diag = Replies([(16, b'ignored log'), (opcode, b'unknown reply'), valid])
  assert d.send_recv(diag, 115, pack('<3xI', 1)) == (opcode, b'unknown reply')
  assert next(diag.frames) == valid  # production does not silently retry/discard
  assert len(diag.sent) == 1 and len(set(diag.deadlines)) == 1


def test_log_frames_share_one_deadline():
  diag = Replies([(16, b'one'), (16, b'two'), TimeoutError('deadline')])
  with pytest.raises(TimeoutError):
    d.send_recv(diag, 115, pack('<3xI', 1))
  assert len(diag.deadlines) == 3 and len(set(diag.deadlines)) == 1


def test_bad_crc_reaches_error_not_a_negative_reply():
  diag = d.ModemDiag.__new__(d.ModemDiag)
  frame = diag.hdlc_encapsulate(b'\x13' + bytes(17))
  diag.pend = bytes([frame[0] ^ 1]) + frame[1:]
  with pytest.raises(ValueError, match='CRC'):
    diag.recv()
