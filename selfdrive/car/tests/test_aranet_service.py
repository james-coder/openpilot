import importlib
import json
import os
from pathlib import Path
import socket
import struct
from types import SimpleNamespace

import pytest

from openpilot.selfdrive.car import aranet
from openpilot.selfdrive.car.tests.test_aranet import payload
from openpilot.system.aranet import bluetooth, firmware, install, protocol, safety
from openpilot.system.manager.process_config import managed_processes


def test_no_manager_dependency():
  assert 'aranetd' not in managed_processes
  root = Path(__file__).resolve().parents[3]
  text = (root / 'system/manager/aranet.service').read_text()
  assert 'User=comma' in text and 'CapabilityBoundingSet=\n' in text
  for filename in ('system/manager/aranet.service', 'system/aranet/aranet-bluetooth.service'):
    text = (root / filename).read_text()
    assert 'Requires=' not in text and 'BindsTo=' not in text
    assert 'After=multi-user.target' not in text  # Would cycle with WantedBy at boot.
    assert 'CPUSchedulingPolicy=idle' in text and 'CPUQuota=5%' in text
    assert 'RestartSec=30' in text and 'MemoryMax=' in text


@pytest.mark.parametrize('raw', [b'', b'[]', b'null', b'{}', b'x'*4097, b'{"version":1,"type":"command"}'])
def test_feed_rejects_invalid(raw):
  with pytest.raises(ValueError):
    protocol.decode_message(raw)


def test_feed_and_database(tmp_path, monkeypatch):
  monkeypatch.setattr(aranet.time, 'time', lambda: 100000.)
  history = aranet.History(tmp_path)
  a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
  try:
    msg = dict(type='advertisement', address=aranet.SENSOR, raw=payload().hex(), rssi=-63, received=100000.)
    a.send(protocol.encode(msg))
    decoded = protocol.decode_message(b.recv(4097))
    assert aranet.consume_advertisement(decoded, history) == (100000., 100000.)
    assert aranet.consume_advertisement(decoded, history) == (100000., None)
    assert history.db.execute('select co2,rssi from readings').fetchone() == (1159., -63)
    for key, value in [('rssi', 97), ('received', float('nan')), ('received', 0), ('raw', 'ff'), ('address', 'other')]:
      with pytest.raises(ValueError):
        aranet.consume_advertisement(dict(decoded, **{key: value}), history)
  finally:
    a.close()
    b.close()
    history.db.close()


@pytest.mark.parametrize('failure', ['unseen', 'stale', 'invalid', 'empty', 'ignition', 'started', 'controls'])
def test_initialization_gate_denies(failure, monkeypatch):
  monkeypatch.setattr(safety.time, 'monotonic', lambda: 10.)
  panda = SimpleNamespace(ignitionLine=failure=='ignition', ignitionCan=False, controlsAllowed=failure=='controls')
  data = {'deviceState': SimpleNamespace(started=failure=='started'), 'pandaStates': [] if failure=='empty' else [panda]}
  class SM:
    services = list(data)
    seen = dict.fromkeys(data, failure != 'unseen')
    valid = dict.fromkeys(data, failure != 'invalid')
    recv_time = dict.fromkeys(data, 0 if failure == 'stale' else 9.)
    def update(self, timeout):
      pass
    def __getitem__(self, key):
      return data[key]
  gate = safety.OffroadGate.__new__(safety.OffroadGate)
  gate.sm = SM()
  with pytest.raises(safety.UnsafeInitialization):
    gate.check()


def test_initialization_denied_before_hardware(monkeypatch):
  def denied():
    raise safety.UnsafeInitialization('onroad')
  monkeypatch.setattr(bluetooth, 'offroad', denied)
  monkeypatch.setattr(bluetooth.subprocess, 'run', lambda *a, **k: pytest.fail('must not run tools'))
  with pytest.raises(safety.UnsafeInitialization):
    bluetooth.Radio().initialize()


def test_firmware_checks_each_send_and_receive(monkeypatch):
  def denied():
    raise safety.UnsafeInitialization('ignition changed')
  monkeypatch.setattr(firmware, 'offroad', denied)
  uart = firmware.UART(-1, None)
  with pytest.raises(safety.UnsafeInitialization):
    uart.send(0xfc00)
  with pytest.raises(safety.UnsafeInitialization):
    uart.event()


def test_firmware_nvm_and_bounds():
  tags = b''
  for tag, data in [(17, b'\x82\x00\x11'), (27, b'\x01')]:
    tags += struct.pack('<HHII', tag, len(data), 0, 0) + data
  body = b'\x02' + len(tags).to_bytes(3, 'little') + tags
  original = b'\x04' + len(body).to_bytes(3, 'little') + body
  changed = firmware.prepare_nvm(original)
  assert [(a,b) for a,b in zip(original, changed, strict=True) if a!=b] == [(0x82,2),(0x11,0),(1,0)]
  for raw in (b'', b'\x04\x00\x00\x00', original[:-1]):
    with pytest.raises(ValueError):
      firmware.prepare_nvm(raw)


def test_status_write_failure_is_nonfatal(tmp_path, monkeypatch):
  monkeypatch.setattr(aranet, 'ROOT', tmp_path / 'missing' / 'directory')
  aranet.status('error', 'storage_failed')


def test_migration_preserves_data_and_refuses_symlinks(tmp_path):
  history = tmp_path / 'history'
  history.mkdir()
  db = history / 'history.sqlite'
  db.write_bytes(b'preserve')
  install.migrate_history(history, os.getuid(), os.getgid())
  assert db.read_bytes() == b'preserve'
  (history / 'collector.lock').symlink_to(db)
  with pytest.raises(RuntimeError):
    install.migrate_history(history, os.getuid(), os.getgid())
  link = tmp_path / 'link'
  link.symlink_to(history)
  with pytest.raises(RuntimeError):
    install.migrate_history(link, os.getuid(), os.getgid())


def test_feature_screen_import_is_lazy():
  import ast
  path = Path(__file__).resolve().parents[2] / 'ui/layouts/settings/device.py'
  tree = ast.parse(path.read_text())
  for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name in ('__init__', '_initialize_items'):
      assert not any(isinstance(n, ast.ImportFrom) and 'aranet' in (n.module or '') for n in ast.walk(node))


def test_status_text_is_bounded():
  module = importlib.import_module('openpilot.selfdrive.ui.layouts.settings.aranet')
  for text in ('x'*2000, 'Bluetooth initialization failed: ' * 50, ''):
    lines = module.status_lines(text, 100, len)
    assert len(lines) <= 2
    assert all(len(line) <= 100 for line in lines)


def prepare_main(tmp_path, monkeypatch):
  monkeypatch.setattr(aranet, 'ROOT', tmp_path)
  monkeypatch.setattr(aranet, 'background_priority', lambda: None)
  monkeypatch.setattr(aranet.signal, 'signal', lambda *args: None)
  delays = []
  def stop_after_sleep(seconds):
    delays.append(seconds)
    raise KeyboardInterrupt
  monkeypatch.setattr(aranet.time, 'sleep', stop_after_sleep)
  return delays


def test_missing_helper_retries_without_hardware(tmp_path, monkeypatch):
  delays = prepare_main(tmp_path, monkeypatch)
  monkeypatch.setattr(protocol, 'SOCKET', tmp_path / 'absent.sock')
  with pytest.raises(KeyboardInterrupt):
    aranet.main()
  assert delays == [30]
  status = json.loads((tmp_path / 'status.json').read_text())
  assert status['state'] == 'unavailable'
  assert status['last_write'] is None


def test_paused_main_does_not_open_feed(tmp_path, monkeypatch):
  delays = prepare_main(tmp_path, monkeypatch)
  (tmp_path / 'paused').touch()
  monkeypatch.setattr(aranet.socket, 'socket', lambda *a: pytest.fail('paused recorder opened feed'))
  with pytest.raises(KeyboardInterrupt):
    aranet.main()
  assert delays == [5]
  assert json.loads((tmp_path / 'status.json').read_text())['state'] == 'paused'


def test_storage_failure_is_bounded(tmp_path, monkeypatch):
  delays = prepare_main(tmp_path, monkeypatch)
  (tmp_path / 'history.sqlite').write_bytes(b'not a database')
  with pytest.raises(KeyboardInterrupt):
    aranet.main()
  assert delays == [30]
  assert json.loads((tmp_path / 'status.json').read_text())['state'] == 'storage_failed'


def test_duplicate_collector_does_not_compete(tmp_path, monkeypatch):
  import fcntl
  prepare_main(tmp_path, monkeypatch)
  with (tmp_path / 'collector.lock').open('w') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with pytest.raises(BlockingIOError):
      aranet.main()


def test_corrupt_asset_rejected_before_install(tmp_path):
  (tmp_path / 'usr/bin').mkdir(parents=True)
  (tmp_path / 'usr/bin/hciattach').write_bytes(b'wrong')
  with pytest.raises(RuntimeError, match='checksum'):
    install.verify_assets(tmp_path)


def test_installer_restores_readonly_root_after_failure(monkeypatch):
  calls = []
  monkeypatch.setattr(install, 'offroad', lambda: None)
  monkeypatch.setattr(install.subprocess, 'check_output', lambda *a, **k: 'ro,relatime')
  monkeypatch.setattr(install.subprocess, 'run', lambda args, **kwargs: calls.append(args))
  with pytest.raises(RuntimeError), install.writable_root():
    raise RuntimeError('injected copy failure')
  assert calls == [['mount', '-o', 'remount,rw', '/'], ['mount', '-o', 'remount,ro', '/']]


def test_bluez_tool_receives_eof_pipe_not_systemd_devnull(monkeypatch):
  monkeypatch.setattr(bluetooth, 'offroad', lambda: None)
  def tool(argv, **kwargs):
    assert kwargs['input'] == b''
    assert kwargs['timeout'] == 10
    return SimpleNamespace(returncode=0, stdout=b'Index list with 0 items', stderr=b'')
  monkeypatch.setattr(bluetooth.subprocess, 'run', tool)
  assert bluetooth.run_checked(['btmgmt', 'info']) == 'Index list with 0 items'
