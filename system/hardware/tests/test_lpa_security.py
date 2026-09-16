import pytest

from openpilot.system.hardware.tici.lpa import encode_tlv, iter_tlv, find_tag, es9p_url, es9p_request


@pytest.mark.parametrize('size', [0, 1, 127, 128, 255, 256, 8192, 65536])
def test_normal_tlv(size):
  value = b'x' * size
  encoded = encode_tlv(0xbf20, value)
  assert list(iter_tlv(encoded)) == [(0xbf20, value)]
  assert list(iter_tlv(encoded, True)) == [(0xbf20, value, 0, len(encoded))]


@pytest.mark.parametrize('data', [b'\x80', b'\xbf', b'\xbf\x80', b'\xbf\x81\x81\x81\x01\x00',
                                 b'\x80\x80', b'\x80\x85\0\0\0\0\0', b'\x80\x82\x01', b'\x80\x02x'])
def test_malformed_tlv(data):
  with pytest.raises(ValueError):
    list(iter_tlv(data))


def test_nested_and_trailing_validation():
  child = encode_tlv(0x80, b'normal')
  assert find_tag(find_tag(encode_tlv(0xbf20, child), 0xbf20), 0x80) == b'normal'
  with pytest.raises(ValueError):
    find_tag(child + b'\x80', 0x80)


def test_tlv_count_bound():
  with pytest.raises(ValueError, match='Too many'):
    list(iter_tlv(b'\x80\x00' * 65537))


@pytest.mark.parametrize('address', ['localhost', '127.0.0.1', '[::1]', 'provider.example:443',
                                    'provider.example@127.0.0.1', 'provider.example/path',
                                    'provider.example?x', 'provider.example\r\nX: y', '-bad.example'])
def test_authority_injection(address):
  with pytest.raises(ValueError):
    es9p_url(address, 'handleNotification')


def test_normal_authority():
  assert es9p_url('smdp.provider.example', 'handleNotification') == 'https://smdp.provider.example/gsma/rsp2/es9plus/handleNotification'


def test_redirect_not_followed():
  class Session:
    def post(self, url, **kwargs):
      assert kwargs['allow_redirects'] is False
      assert kwargs['verify'].endswith('gsma_ci_bundle.pem')
      return type('Response', (), {'status_code': 302})()
  with pytest.raises(RuntimeError, match='redirect'):
    es9p_request('smdp.provider.example', 'handleNotification', {}, session=Session())
