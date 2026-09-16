"""Disposable VM adapter: use deployed authorizer logic on dummy USB port only."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def emit(case, **data):
  print(json.dumps(dict(case=case, **data)), flush=True)


def wait_device(bus):
  device = bus / (bus.name[3:] + '-1')
  deadline = time.monotonic() + 8
  while time.monotonic() < deadline:
    if (device / 'descriptors').exists():
      return device
    time.sleep(0.1)
  raise RuntimeError('Device did not enumerate')


def bindings(device):
  return [(p.name, (p / 'driver').resolve().name) for p in sorted(device.glob('*:*')) if (p / 'driver').exists()]


def policy_case(policy, bus, name, mode=0, action='authorize'):
  with subprocess.Popen(['/gadget', str(mode), '--allow-config']) as gadget:
    device = None
    try:
      device = wait_device(bus)
      assert not bindings(device) and (device / 'authorized').read_text().strip() == '0'
      if action == 'absent':
        time.sleep(1)
        assert (device / 'authorized').read_text().strip() == '0' and not bindings(device)
      elif action == 'crash':
        pid = os.fork()
        if pid == 0:
          original = Path.write_text

          def crash_after_device_authorization(path, value, *args, **kwargs):
            result = original(path, value, *args, **kwargs)
            if path == device / 'authorized' and value == '1':
              os._exit(91)
            return result

          Path.write_text = crash_after_device_authorization
          policy.authorize_device(bus, device)
          os._exit(92)
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 91
        interfaces = list(device.glob('*:*'))
        assert len(interfaces) == 5 and not bindings(device)
        assert all((p / 'authorized').read_text().strip() == '0' for p in interfaces)
        assert policy.authorize_device(bus, device)
        assert [d for _, d in bindings(device)] == list(policy.DRIVERS)
      elif action == 'interrupt':
        original = Path.write_text

        def disconnect_after_device_authorization(path, value, *args, **kwargs):
          result = original(path, value, *args, **kwargs)
          if path == device / 'authorized' and value == '1':
            gadget.terminate()
            gadget.wait(timeout=3)
            wait_removed(device)
          return result

        Path.write_text = disconnect_after_device_authorization
        try:
          try:
            policy.authorize_device(bus, device)
          except OSError:
            pass
          else:
            raise AssertionError('Disconnected authorization unexpectedly succeeded')
        finally:
          Path.write_text = original
        assert not device.exists()
      else:
        accepted = policy.authorize_device(bus, device)
        assert accepted == (mode == 0)
        if accepted:
          assert policy.authorize_device(bus, device)  # idempotency
          assert [d for _, d in bindings(device)] == list(policy.DRIVERS)
          assert len(list(Path('/dev').glob('ttyUSB*'))) == 4
          assert len(list(Path('/dev').glob('cdc-wdm*'))) == 1
          assert any(p.name.startswith('wwan') for p in Path('/sys/class/net').iterdir())
        else:
          assert (device / 'authorized').read_text().strip() == '0' and not bindings(device)
      assert all((bus / key).read_text().strip() == '0' for key in ('authorized_default', 'interface_authorized_default'))
      emit(name, status='pass', mode=mode, drivers=[d for _, d in bindings(device)] if device.exists() else [])
    finally:
      gadget.terminate()
      gadget.wait(timeout=3)
      if device is not None:
        wait_removed(device)


def wait_removed(device):
  end = time.monotonic() + 3
  while device.exists() and time.monotonic() < end:
    time.sleep(0.05)
  assert not device.exists(), 'Device remained after disconnect'


def positive_controls(buses):
  # Separate VM boot; never runs alongside the policy-enabled case suite.
  for bus in buses:
    (bus / 'authorized_default').write_text('1')
    (bus / 'interface_authorized_default').write_text('1')
  subprocess.run(['mount', '-t', 'configfs', 'none', '/sys/kernel/config'], check=True)
  keyboard = bytes.fromhex('05010906a101050719e029e71500250175019508810295017508810195057501050819012905910295017503910195067508150025650507190029658100c0')
  mouse = bytes.fromhex('05010902a1010901a100050919012903150025019503750181029501750581010501093009311581257f750895028106c0c0')
  root = Path('/sys/kernel/config/usb_gadget/test')
  for name, function, driver, report, protocol in [
    ('keyboard', 'hid', 'usbhid', keyboard, 1),
    ('mouse', 'hid', 'usbhid', mouse, 2),
    ('storage', 'mass_storage', 'usb-storage', None, 0),
    ('ethernet', 'ecm', 'cdc_ether', None, 0),
  ]:
    root.mkdir()
    (root / 'idVendor').write_text('0x1d6b')
    (root / 'idProduct').write_text('0x0104')
    config = root / 'configs/c.1'
    config.mkdir()
    func = root / f'functions/{function}.test'
    func.mkdir()
    if report is not None:
      (func / 'protocol').write_text(str(protocol))
      (func / 'subclass').write_text('1')
      (func / 'report_length').write_text('8' if name == 'keyboard' else '3')
      (func / 'report_desc').write_bytes(report)
    if name == 'storage':
      with Path('/tmp/lun.img').open('wb') as f:
        f.truncate(1024 * 1024)
      (func / 'lun.0/ro').write_text('1')
      (func / 'lun.0/file').write_text('/tmp/lun.img')
    link = config / 'function'
    link.symlink_to(func)
    (root / 'UDC').write_text('dummy_udc.0')
    try:
      device = wait_device(sorted(buses)[0])
      end = time.monotonic() + 8
      while time.monotonic() < end and driver not in [d for _, d in bindings(device)]:
        time.sleep(0.1)
      actual = [d for _, d in bindings(device)]
      assert driver in actual, (name, actual)
      emit('positive_' + name, status='pass', drivers=actual)
    finally:
      (root / 'UDC').write_text('\n')
      link.unlink()
      func.rmdir()
      config.rmdir()
      root.rmdir()
    time.sleep(0.3)
  print('POSITIVE_CONTROLS_DONE', flush=True)


def main():
  if not Path('/disposable-usb-lab').exists() or 'comma_disposable_usb_lab=1' not in Path('/proc/cmdline').read_text():
    raise RuntimeError('Disposable VM required')
  spec = importlib.util.spec_from_file_location('authorizer', '/authorize.py')
  policy = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(policy)
  buses = list(Path('/sys/devices/platform/dummy_hcd.0').glob('usb*'))
  if not buses:
    raise RuntimeError('No fixed dummy HCD')
  if sys.argv[1:] == ['--positive-controls']:
    positive_controls(buses)
    return
  for bus in buses:
    (bus / 'authorized_default').write_text('0')
    (bus / 'interface_authorized_default').write_text('0')
  bus = sorted(buses)[0]
  for cycle in range(10):
    policy_case(policy, bus, f'cycle_{cycle}_normal_before')
    policy_case(policy, bus, f'cycle_{cycle}_modified', mode=1 + cycle % 7)
    policy_case(policy, bus, f'cycle_{cycle}_normal_after')
  for action in ('absent', 'crash', 'interrupt'):
    policy_case(policy, bus, action, action=action)
    policy_case(policy, bus, action + '_recovery')
  print('BINDING_LAB_DONE', flush=True)


if __name__ == '__main__':
  main()
