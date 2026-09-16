from struct import pack
from unittest.mock import patch  # noqa: TID251 - mocks only; tests use pytest

import pytest

from openpilot.system.qcomgpsd.modemdiag import ModemDiag, setup_logs, DIAG_LOG_CONFIG_F


def test_hdlc_roundtrip():
  diag = ModemDiag.__new__(ModemDiag)
  payload = bytes(range(256)) * 4
  assert diag.hdlc_decapsulate(diag.hdlc_encapsulate(payload)) == payload


@pytest.mark.parametrize('payload', [b'', b'\x00\x00\x7e', b'abc\x7e', b'x' * 131073])
def test_invalid_frame(payload):
  with pytest.raises(ValueError):
    ModemDiag.__new__(ModemDiag).hdlc_decapsulate(payload)


@pytest.mark.parametrize('bits', [4097, 0xffffffff])
def test_untrusted_log_mask_allocation(bits):
  response = pack('<3xII16I', 1, 0, bits, *([0] * 15))
  with patch('openpilot.system.qcomgpsd.modemdiag.send_recv', return_value=(DIAG_LOG_CONFIG_F, response)) as query:
    with pytest.raises(ValueError, match='item-ID'):
      setup_logs(object(), [])
    assert query.call_count == 1


def test_valid_log_mask():
  ranges = pack('<3xII16I', 1, 0, 16, *([0] * 15))
  with patch('openpilot.system.qcomgpsd.modemdiag.send_recv',
             side_effect=[(DIAG_LOG_CONFIG_F, ranges), (DIAG_LOG_CONFIG_F, pack('<3xII', 3, 0))]) as query:
    setup_logs(object(), [1, 15])
    assert query.call_args.args[2] == pack('<3xIII', 3, 0, 16) + b'\x02\x80'


def test_receive_flood_bounded():
  diag = ModemDiag.__new__(ModemDiag)
  diag.pend = b''
  class Serial:
    fd = 0
    def read(self, count):
      return b'x' * count
  diag.serial = Serial()
  with patch('openpilot.system.qcomgpsd.modemdiag.select.select', return_value=([0], [], [])):
    with pytest.raises(ValueError, match='bound'):
      diag.recv()


def test_receive_deadline():
  diag = ModemDiag.__new__(ModemDiag)
  diag.pend = b''
  with pytest.raises(TimeoutError):
    diag.recv(deadline=0)
