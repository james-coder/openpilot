import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location('parser_trial', Path(__file__).with_name('kernel_parser_trial.py'))
t = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(t)


def test_profile_pins_current_kernel_and_separate_units():
  assert t.BASELINE == '46fd613ff1b1147ad0212d205fc96181fb7b25dbdcb273210882a64efb56cdbd'
  assert t.ROOT.name == 'comma-kernel-parser-trial'
  assert t.TIMER == 'comma-kernel-parser-rollback.timer'
  assert str(t.ROOT / 'kernel_parser_trial.py') in t.SERVICE_TEXT
  assert 'OnActiveSec=8min' in t.TIMER_TEXT


@pytest.fixture
def context(tmp_path, monkeypatch):
  events = []
  monkeypatch.setattr(t, 'ROOT', tmp_path)
  monkeypatch.setattr(t, 'trusted', lambda p: None)
  monkeypatch.setattr(t, 'hardware', lambda: events.append('hardware'))
  monkeypatch.setattr(t, 'safe', lambda: events.append('safe'))
  monkeypatch.setattr(t, 'run', lambda argv, **kw: events.append(argv))
  monkeypatch.setattr(t, 'digest', lambda p: t.BASELINE)
  monkeypatch.setattr(t, 'boot_id', lambda: 'new-boot')
  return tmp_path, events


@pytest.mark.parametrize('same_boot', [True, False])
def test_restored_policy_kernel_does_not_reboot_loop(context, monkeypatch, same_boot):
  root, events = context
  (root / 'restore_boot_id').write_text('new-boot' if same_boot else 'old-boot')
  monkeypatch.setattr(t, 'phase', lambda: 'restored')
  t.rollback()
  if same_boot:
    assert events == ['hardware', 'safe', ['/usr/bin/systemctl', '--no-block', 'reboot']]
  else:
    assert events == [['/usr/bin/systemctl', 'stop', t.TIMER]]


@pytest.mark.parametrize('same_boot,version', [(True, '#5'), (False, '#4')])
def test_confirm_rejects_old_boot_or_wrong_kernel(context, monkeypatch, same_boot, version):
  root, events = context
  (root / 'start_boot_id').write_text('new-boot' if same_boot else 'old-boot')
  monkeypatch.setattr(t, 'manifest', lambda: {'kernel_version': '#5'})
  monkeypatch.setattr(t.os, 'uname', lambda: SimpleNamespace(version=version))
  with pytest.raises(RuntimeError, match='has not booted'):
    t.confirm()
  assert events == ['hardware', 'safe']


def test_install_rejects_existing_trial_without_overwrite(context):
  root, events = context
  sentinel = root / 'sentinel'
  sentinel.write_text('keep')
  with pytest.raises(RuntimeError, match='existing trial'):
    t.install(root, 'unused', '#5 SMP PREEMPT test')
  assert sentinel.read_text() == 'keep' and events == []
