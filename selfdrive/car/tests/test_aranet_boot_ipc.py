"""Device-only cross-UID boot-order regression; isolated from live messaging.

Run as root on AGNOS with pytest -p no:cacheprovider. No CAN or service changes.
"""
import configparser
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile

import pytest


@pytest.mark.parametrize('root_first,broken_mask', [(True, False), (False, False), (True, True)])
def test_bluetooth_boot_order_permissions(root_first, broken_mask):
  if os.geteuid() != 0:
    pytest.skip('Cross-UID IPC regression requires root on the device')
  try:
    comma = pwd.getpwnam('comma')
  except KeyError:
    pytest.skip('Device comma account required')
  unit = configparser.ConfigParser()
  unit.read(Path(__file__).resolve().parents[3] / 'system/aranet/aranet-bluetooth.service')
  assert unit['Service']['Group'] == 'comma'
  mask = 0o022 if broken_mask else int(unit['Service']['UMask'], 8)
  with tempfile.TemporaryDirectory(prefix='msgq_aranet-boot-test-', dir='/dev/shm') as directory:
    os.chown(directory, comma.pw_uid, comma.pw_gid)
    env = dict(os.environ, OPENPILOT_PREFIX=Path(directory).name.removeprefix('msgq_'))
    subscriber = 'from cereal import messaging; sm = messaging.SubMaster(["deviceState", "pandaStates"])'
    publisher = '; '.join(['from cereal import messaging', 'pm = messaging.PubMaster(["deviceState", "pandaStates"])',
                           'pm.send("deviceState", messaging.new_message("deviceState"))',
                           'pm.send("pandaStates", messaging.new_message("pandaStates", 0))'])

    def run(code, uid, umask):
      return subprocess.run([sys.executable, '-c', code], env=env, user=uid, group=comma.pw_gid,
                            extra_groups=[], umask=umask, capture_output=True, text=True, timeout=20)

    if not root_first:
      initial = run(publisher, comma.pw_uid, 0o022)
      assert initial.returncode == 0, initial.stderr
    root_result = run(subscriber, 0, mask)
    assert root_result.returncode == 0, root_result.stderr
    result = run(publisher, comma.pw_uid, 0o022)
    if broken_mask:
      assert result.returncode != 0
      assert 'Permission denied' in result.stdout + result.stderr
    else:
      assert result.returncode == 0, result.stdout + result.stderr
      result = run(subscriber, comma.pw_uid, 0o022)
      assert result.returncode == 0, result.stdout + result.stderr
