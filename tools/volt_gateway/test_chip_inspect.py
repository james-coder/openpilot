import pytest
from openpilot.tools.volt_gateway.chip_inspect import FIELDS, decode_options, read_field


def test_option_mirrors():
  result = decode_options(bytes.fromhex('efaa1055efaa1055ff7f0080ff7f0080'))
  assert result['rdp_byte'] == 0xaa and result['rdp_level'] == 0
  for index in range(16):
    data = bytearray.fromhex('efaa1055efaa1055ff7f0080ff7f0080')
    data[index] ^= 1
    with pytest.raises(ValueError):
      decode_options(bytes(data))


@pytest.mark.parametrize('field', FIELDS)
def test_only_pointer_and_upload(field):
  class Fake:
    def __init__(self):
      self.writes = []
      self.state = 2
    def controlWrite(self, request_type, request, value, index, payload, **kwargs):
      assert (request_type, value, index) == (0x21, 0, 0)
      assert request in (1, 6)
      if request == 1:
        assert payload == b'\x21' + FIELDS[field][1].to_bytes(4, 'little')
        self.state = 5
      else:
        assert payload == b''
        self.state = 2
      self.writes.append(request)
    def controlRead(self, request_type, request, value, index, length, **kwargs):
      assert request_type == 0xa1 and index == 0
      if request == 3:
        assert length == 6 and value == 0
        return bytes([0, 0, 0, 0, self.state, 0])
      assert request == 2 and value == 2 and length == FIELDS[field][2]
      return bytes(length)
    def setInterfaceAltSetting(self, interface, alt):
      assert interface == 0 and alt == FIELDS[field][0]
  fake = Fake()
  assert read_field(fake, field) == bytes(FIELDS[field][2])
  assert fake.writes.count(1) == 1  # only SET_ADDRESS, never payload/program/erase


def test_no_generic_address_api():
  with pytest.raises(KeyError):
    read_field(None, 'arbitrary_memory')
