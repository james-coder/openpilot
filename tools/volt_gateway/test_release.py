import hashlib
import io
import subprocess
import tarfile

from Crypto.PublicKey import ECC
import pytest

from openpilot.tools.volt_gateway.authority import AuthorityError, ImageManifest
from openpilot.tools.volt_gateway.operator import sign_image
from openpilot.tools.volt_gateway.release import check_staged, package, reject_secret


def test_public_only_reproducible_package():
  key = ECC.generate(curve='P-256')
  image = b'public firmware fixture'
  m = ImageManifest(bytes(12), 1, bytes(32), len(image), hashlib.sha256(image).digest(), bytes(32), 1)
  signed = sign_image(key, m).pack()
  public = key.public_key().export_key(format='DER')
  result = package(image, signed, public)
  assert result == package(image, signed, public)
  with tarfile.open(fileobj=io.BytesIO(result)) as a:
    assert set(a.getnames()) == {'image.bin', 'manifest.bin', 'firmware-public.der', 'checksums.json'}
    assert a.extractfile('image.bin').read() == image
    assert not ECC.import_key(a.extractfile('firmware-public.der').read()).has_private()
  with pytest.raises(AuthorityError):
    package(image, signed, key.export_key(format='DER'))
  with pytest.raises(AuthorityError):
    package(image + b'changed', signed, public)


@pytest.mark.parametrize('name,body', [('.voltgw-private/anything', b'opaque'), ('some/routine-key.bin', bytes(32)),
                                     ('renamed.txt', b'-----BEGIN ENCRYPTED PRIVATE KEY-----\nsecret'),
                                     ('renamed', b'-----BEGIN EC PRIVATE KEY-----\nsecret')])
def test_secret_paths_and_contents(name, body):
  with pytest.raises(AuthorityError):
    reject_secret(name, body)


def test_der_private_rejected():
  with pytest.raises(AuthorityError):
    reject_secret('renamed', ECC.generate(curve='P-256').export_key(format='DER'))


def test_scan_checks_index_not_worktree(tmp_path):
  subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
  path = tmp_path / 'renamed.txt'
  path.write_bytes(b'-----BEGIN PRIVATE KEY-----\nsecret')
  subprocess.run(['git', '-C', str(tmp_path), 'add', 'renamed.txt'], check=True)
  path.write_text('innocent worktree replacement')
  assert check_staged(tmp_path) == ['renamed.txt']
