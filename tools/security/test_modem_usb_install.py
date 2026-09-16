import importlib.util
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

SPEC = importlib.util.spec_from_file_location('installer', Path(__file__).with_name('modem_usb') / 'install.py')
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)
SOURCE = Path(__file__).with_name('modem_usb')


@pytest.fixture
def target(tmp_path, monkeypatch):
  monkeypatch.setattr(installer, 'ROOT', tmp_path / 'lib' / 'comma-modem-usb')
  monkeypatch.setattr(installer, 'UNIT', tmp_path / 'units' / 'comma-modem-usb.service')
  installer.ROOT.parent.mkdir()
  installer.UNIT.parent.mkdir()
  monkeypatch.setattr(installer.os, 'geteuid', lambda: 0)
  monkeypatch.setattr(installer.os, 'statvfs', lambda _: SimpleNamespace(f_flag=installer.os.ST_RDONLY))
  monkeypatch.setattr(installer.os, 'sync', lambda: None)
  monkeypatch.setattr(installer, 'trusted_directory', lambda _: None)
  calls = []
  monkeypatch.setattr(installer, 'run', lambda argv: calls.append(argv))
  return calls


def test_install_does_not_start_authorizer_or_change_usb(target):
  installer.install(SOURCE)
  assert (installer.ROOT / 'authorize.py').read_bytes() == (SOURCE / 'authorize.py').read_bytes()
  assert target == [
    ['/usr/bin/mount', '-o', 'remount,rw', '/'],
    ['/usr/bin/systemd-analyze', 'verify', str(installer.UNIT)],
    ['/usr/bin/systemctl', 'daemon-reload'],
    ['/usr/bin/systemctl', 'enable', installer.UNIT.name],
    ['/usr/bin/mount', '-o', 'remount,ro', '/']]


@pytest.mark.parametrize('existing', ['directory', 'unit', 'dangling_symlink'])
def test_existing_install_untouched(target, existing):
  if existing == 'directory':
    installer.ROOT.mkdir()
  elif existing == 'unit':
    installer.UNIT.write_text('preserve')
  else:
    installer.ROOT.symlink_to('/missing')
  with pytest.raises(RuntimeError, match='existing'):
    installer.install(SOURCE)
  assert not target


def test_verify_failure_rolls_back_and_restores_readonly(target, monkeypatch):
  def run(argv):
    target.append(argv)
    if argv[0] == '/usr/bin/systemd-analyze':
      raise RuntimeError('unit verification failed')
  monkeypatch.setattr(installer, 'run', run)
  with pytest.raises(RuntimeError, match='verification'):
    installer.install(SOURCE)
  assert not installer.ROOT.exists() and not installer.UNIT.exists()
  assert target[-1] == ['/usr/bin/mount', '-o', 'remount,ro', '/']


def test_non_root_does_nothing(target, monkeypatch):
  monkeypatch.setattr(installer.os, 'geteuid', lambda: 1000)
  with pytest.raises(RuntimeError, match='Root'):
    installer.install(SOURCE)
  assert not target


def test_partial_enable_and_disable_failure_still_removes_own_files(target, monkeypatch):
  def run(argv):
    target.append(argv)
    if argv[:2] in (['/usr/bin/systemctl', 'enable'], ['/usr/bin/systemctl', 'disable']):
      raise subprocess.CalledProcessError(1, argv)
  monkeypatch.setattr(installer, 'run', run)
  with pytest.raises(subprocess.CalledProcessError):
    installer.install(SOURCE)
  assert not installer.ROOT.exists() and not installer.UNIT.exists()
  assert target[-1] == ['/usr/bin/mount', '-o', 'remount,ro', '/']


def test_untrusted_parent_does_nothing(target, monkeypatch):
  def denied(_):
    raise RuntimeError('Untrusted')
  monkeypatch.setattr(installer, 'trusted_directory', denied)
  with pytest.raises(RuntimeError, match='Untrusted'):
    installer.install(SOURCE)
  assert not target
