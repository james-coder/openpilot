import pytest

from openpilot.system.hardware.tici import modem_input


class Port:
  def __init__(self, data):
    self.data = bytearray(data)
    self.timeout = 5

  def read(self, size):
    data = bytes(self.data[:size])
    del self.data[:size]
    return data


def test_normal_and_trailing_data():
  port = Port(b'\r\n+CSQ: 20,99\r\n\r\nOK\r\n+URC: 1\r\n')
  assert modem_input.read_at_response(port) == ['+CSQ: 20,99']
  assert port.data == b'+URC: 1\r\n'
  assert port.timeout == 5


@pytest.mark.parametrize('lines', [
  ['AT+CREG?', '+CREG: 2,1,"1234","12345678",7'],
  ['+QCCID: 8901234567890123456F'],
  ['+CCHO: 1'],
  ['+CGLA: 8,"BF209000"'],
  ['+CGCONTRDP: 1,5,"apn","10.0.0.2.255.255.255.255","10.0.0.1","8.8.8.8"'],
  ['+QGPS: 1'],
  ['+CREG: 5', '+CSQ: 20,99'],
])
def test_normal_modem_sim_gps_transcripts(lines):
  wire = ('\r\n' + '\r\n'.join(lines) + '\r\n\r\nOK\r\n').encode()
  assert modem_input.read_at_response(Port(wire)) == lines


@pytest.mark.parametrize('data', [b'x'*16385, b'\n'*257, (b'x'*1000+b'\n')*66,
                                b'ERROR\r\n', b'+CME ERROR: 13\r\n', b'\xff\r\n'],
                         ids=['long-line', 'many-lines', 'large-total', 'error', 'cme-error', 'invalid-utf8'])
def test_invalid_response_is_bounded(data):
  port = Port(data)
  with pytest.raises(RuntimeError):
    modem_input.read_at_response(port)
  assert port.timeout == 5


def test_partial_response():
  with pytest.raises(TimeoutError):
    modem_input.read_at_response(Port(b'OK'))


@pytest.mark.parametrize('terminal', [b'ERROR', b'+CME ERROR: 3'])
def test_explicit_legacy_gps_error_policy(terminal):
  data = b'AT+QGPS?\r\n+QGPS: 0\r\n' + terminal + b'\r\n'
  assert modem_input.read_at_response(Port(data), reject_errors=False) == ['AT+QGPS?', '+QGPS: 0']
  with pytest.raises(RuntimeError):
    modem_input.read_at_response(Port(data))


def test_streaming_peer_cannot_extend_deadline(monkeypatch):
  ticks = iter([0., 1., 2., 3., 4., 5.])
  monkeypatch.setattr(modem_input.time, 'monotonic', lambda: next(ticks))
  port = Port(b'x'*1000)
  with pytest.raises(TimeoutError):
    modem_input.read_at_response(port)
  assert len(port.data) == 996
  assert port.timeout == 5


@pytest.mark.parametrize('value', ['1', '9', '10', '19'])
def test_channel(value):
  assert modem_input.logical_channel(value) == value


@pytest.mark.parametrize('value', ['0', '20', '-1', '01', '1;AT+CFUN=1,1', '1\r\nAT', '١', '1 ', ''])
def test_channel_injection(value):
  with pytest.raises(RuntimeError):
    modem_input.logical_channel(value)
