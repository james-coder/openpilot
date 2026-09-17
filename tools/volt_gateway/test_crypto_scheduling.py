import pytest

from openpilot.tools.volt_gateway import crypto_scheduling, target_crypto


def test_exact_pinned_patch_has_no_arithmetic_changes(tmp_path):
  archive = target_crypto.archive_path()
  if not archive.is_file():
    pytest.skip('pinned crypto archive missing')
  root = target_crypto.extract(archive, tmp_path)
  path = root/'library/bignum_core.c'
  original = path.read_text()
  report = crypto_scheduling.patch(root)
  updated = path.read_text()
  inserted = ('        /* Volt gateway: no arithmetic or loop-bound changes. */\n' +
              '        if ((i & 7U) == 0) {\n' +
              '            extern void vgw_crypto_service_slice(void);\n' +
              '            vgw_crypto_service_slice();\n' +
              '        }\n')
  assert updated.count(inserted) == 1
  assert updated.replace(inserted, '') == original
  assert report['before_sha256'] != report['after_sha256']
  with pytest.raises(ValueError):
    crypto_scheduling.patch(root)


@pytest.mark.parametrize('source', ['', '    for (;;) {\n',
  '    for (size_t i = 0; i < (A_limbs + N_limbs) * biL; i++) {\n'*2])
def test_drift_rejected_without_writes(tmp_path, source):
  (tmp_path/'library').mkdir()
  path = tmp_path/'library/bignum_core.c'
  path.write_text(source)
  with pytest.raises(ValueError):
    crypto_scheduling.patch(tmp_path)
  assert path.read_text() == source
