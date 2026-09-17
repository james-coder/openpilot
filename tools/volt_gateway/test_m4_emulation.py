import hashlib

from Crypto.PublicKey import ECC
import pytest

from openpilot.tools.volt_gateway.authority import Challenge, ImageManifest, Phase
from openpilot.tools.volt_gateway.operator import authorize_image, sign_image
from openpilot.tools.volt_gateway import m4_emulation


@pytest.fixture(scope='module')
def emulated_elf(tmp_path_factory):
  pytest.importorskip('unicorn', reason='install optional requirements-emulation.txt; not a hardware pass')
  pytest.importorskip('elftools', reason='install optional requirements-emulation.txt; not a hardware pass')
  elf = tmp_path_factory.mktemp('m4-emu') / 'NEVER_FLASH-core-harness.elf'
  m4_emulation.build(elf)
  return elf


@pytest.mark.parametrize('valid', [True, False])
def test_real_thumb_instructions_verify_and_observe(emulated_elf, valid):
  key = ECC.generate(curve='P-256')
  raw = b'public emulator image fixture'
  image = sign_image(key, ImageManifest(b'd'*12, 1, b'l'*32, len(raw), hashlib.sha256(raw).digest(), b'b'*32, 1))
  challenge = Challenge(Phase.PROGRAM, b'd'*12, b's'*32, b'n'*32)
  token = authorize_image(key, image, challenge, 60000).pack()
  if not valid:
    token = token[:-1] + bytes([token[-1] ^ 1])
  result = m4_emulation.run(emulated_elf, image.pack(), token, key.public_key().export_key(format='DER'))
  assert result == {'completed':True, 'crypto_calls':2, 'failure_bits':0, 'authorized':valid, 'updated':valid}
