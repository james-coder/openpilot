import importlib.util
from pathlib import Path
import re
import subprocess

import pytest

SPEC = importlib.util.spec_from_file_location('authorize', Path(__file__).with_name('modem_usb') / 'authorize.py')
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


def test_actual_usb_core_interface_initialization(tmp_path):
  # Compile the actual assignment in usb_set_configuration, not a rewritten
  # policy predicate. This still does NOT execute USB enumeration or probe.
  source = Path('/home/james/git/volt-modem-kernel-candidate/drivers/usb/core/message.c')
  if not source.exists():
    pytest.skip('Off-device kernel candidate not available')
  assignments = re.findall(r'intf->authorized\s*=\s*[^;]+;', source.read_text())
  assignments = [a for a in assignments if 'HCD_INTF_AUTHORIZED' in a]
  assert len(assignments) == 1
  c = tmp_path / 'initialization.c'
  c.write_text('''
#include <assert.h>
#include <stdbool.h>
struct device { bool protected; };
struct usb_device { struct usb_device *parent; };
struct usb_interface { bool authorized; };
struct usb_hcd { struct { struct device *controller; } self; bool intf_default; };
#define HCD_INTF_AUTHORIZED(hcd) ((hcd)->intf_default)
static bool usb_modem_host(struct device *controller) { return controller->protected; }
static bool initialize(struct usb_device *dev, struct usb_hcd *hcd) {
  struct usb_interface storage, *intf = &storage;
''' + assignments[0] + '''
  return intf->authorized;
}
int main(void) {
  struct device controller;
  struct usb_hcd hcd = { .self = { .controller = &controller } };
  struct usb_device root = {0}, child = { .parent = &root }, behind_hub = { .parent = &child };
  for (int marked=0; marked<2; marked++) {
    controller.protected = marked;
    for (int allowed=0; allowed<2; allowed++) {
      hcd.intf_default = allowed;
      assert(initialize(&root, &hcd) == (marked || allowed));
      assert(initialize(&child, &hcd) == allowed);
      assert(initialize(&behind_hub, &hcd) == allowed);
    }
  }
  return 0;
}
''')
  exe = tmp_path / 'initialization'
  subprocess.run(['cc', '-std=c99', '-Wall', '-Wextra', '-Werror', str(c), '-o', str(exe)], check=True)
  subprocess.run([str(exe)], check=True)


def test_exact_descriptor_and_all_single_byte_changes():
  assert policy.matches(policy.EXPECTED)
  for i in range(len(policy.EXPECTED)):
    changed = bytearray(policy.EXPECTED)
    changed[i] ^= 1
    assert not policy.matches(bytes(changed))
  assert not policy.matches(policy.EXPECTED[:-1])
  assert not policy.matches(policy.EXPECTED + b'\x09\x04\x05\x00\x01\x03\x01\x01\x00')


@pytest.fixture
def bus(tmp_path):
  bus = tmp_path / 'usb7'
  bus.mkdir()
  for name in ('authorized_default', 'interface_authorized_default'):
    (bus / name).write_text('0\n')
  device = bus / '7-1'
  device.mkdir()
  (device / 'descriptors').write_bytes(policy.EXPECTED)
  (device / 'authorized').write_text('0\n')
  for i, driver in enumerate(policy.DRIVERS):
    target = tmp_path / 'drivers' / driver
    target.mkdir(parents=True, exist_ok=True)
    intf = device / f'7-1:1.{i}'
    intf.mkdir()
    (intf / 'authorized').write_text('0\n')
    (intf / 'driver').symlink_to(target)
  return bus, device


def test_normal_and_repeat_authorization(bus):
  root, device = bus
  assert policy.authorize_device(root, device)
  assert policy.authorize_device(root, device)
  assert (device / 'authorized').read_text() == '1'


def test_refuses_late_policy_on_permissive_kernel(bus):
  root, device = bus
  (root / 'interface_authorized_default').write_text('1')
  with pytest.raises(RuntimeError, match='Early kernel'):
    policy.authorize_device(root, device)
  assert (device / 'authorized').read_text().strip() == '0'


@pytest.mark.parametrize('kind', ['hid_keyboard', 'hid_mouse', 'storage', 'ethernet', 'serial', 'truncated', 'extra_config'])
def test_unexpected_personalities_denied(bus, kind):
  root, device = bus
  data = policy.EXPECTED
  if kind == 'truncated':
    data = data[:40]
  elif kind == 'extra_config':
    changed = bytearray(data)
    changed[17] = 2
    data = bytes(changed)
  else:
    extra = {'hid_keyboard': b'\x03\x01\x01', 'hid_mouse': b'\x03\x01\x02', 'storage': b'\x08\x06\x50',
             'ethernet': b'\x02\x06\x00', 'serial': b'\xff\x01\x00'}[kind]
    data += b'\x09\x04\x05\x00\x01' + extra + b'\x00'
  (device / 'descriptors').write_bytes(data)
  assert not policy.authorize_device(root, device)
  assert (device / 'authorized').read_text() == '0'


def test_extra_interface_even_with_normal_cached_descriptors_denied(bus):
  root, device = bus
  (device / '7-1:1.5').mkdir()
  assert not policy.authorize_device(root, device)
  assert (device / 'authorized').read_text() == '0'


def test_wrong_driver_deauthorizes_whole_device(bus):
  root, device = bus
  (device / '7-1:1.0/driver').unlink()
  wrong = root / 'usbhid'
  wrong.mkdir()
  (device / '7-1:1.0/driver').symlink_to(wrong)
  with pytest.raises(RuntimeError, match='driver'):
    policy.authorize_device(root, device)
  assert (device / 'authorized').read_text() == '0'


def test_compiled_kernel_policy_logic(tmp_path):
  # Native logic harness, NOT USB-core execution or malicious hardware testing.
  source = Path('/home/james/git/volt-modem-kernel-candidate/drivers/usb/core/modem-policy.c')
  if not source.exists():
    pytest.skip('Off-device kernel candidate not available')
  (tmp_path / 'linux').mkdir()
  (tmp_path / 'linux/of.h').write_text('')
  (tmp_path / 'linux/usb.h').write_text('')
  (tmp_path / 'usb.h').write_text('')
  stubs = '''
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <assert.h>
typedef uint8_t u8;
struct device { struct device *parent; bool *of_node; };
struct usb_bus { struct device *controller; };
struct usb_device_descriptor { u8 bytes[18]; };
struct config_desc { uint16_t wTotalLength; };
struct usb_host_config { struct config_desc desc; };
struct usb_device {
  struct usb_device *parent; struct usb_bus *bus; int portnum, speed, authorized;
  struct usb_device_descriptor descriptor; struct usb_host_config *config; char **rawdescriptors;
};
struct intf_desc { int bAlternateSetting, bInterfaceNumber; };
struct usb_host_interface { struct intf_desc desc; };
struct usb_interface { int authorized, num_altsetting; struct usb_host_interface *cur_altsetting; };
struct usb_driver { const char *name; };
#define USB_SPEED_HIGH 3
#define le16_to_cpu(x) (x)
static bool of_property_read_bool(bool *node, const char *name) {
  assert(!strcmp(name, "comma,untrusted-modem-host")); return node && *node;
}
'''
  expected = ','.join(str(b) for b in policy.EXPECTED)
  harness = '''
int main(void) {
  bool marked = true;
  struct device host = { .of_node = &marked };
  struct usb_bus bus = { .controller = &host };
  struct usb_device root = { .bus = &bus };
  struct usb_host_config cfg = { .desc = { .wTotalLength = 209 } };
  char raw[209], *configs[] = { raw };
  struct usb_device dev = { .parent = &root, .bus = &bus, .portnum = 1,
    .speed = USB_SPEED_HIGH, .config = &cfg, .rawdescriptors = configs, .authorized = 1 };
  struct usb_host_interface alt = { .desc = { .bAlternateSetting = 0, .bInterfaceNumber = 0 } };
  struct usb_interface intf = { .authorized = 1, .num_altsetting = 1, .cur_altsetting = &alt };
  struct usb_driver option = { .name = "option" }, hid = { .name = "usbhid" }, qmi = { .name = "qmi_wwan" };
  memcpy(&dev.descriptor, expected, 18); memcpy(raw, expected + 18, 209);
  assert(usb_modem_host(&host)); assert(!usb_modem_device(&root));
  assert(usb_modem_profile(&dev)); assert(usb_modem_driver_allowed(&dev, &intf, &option));
  assert(!usb_modem_driver_allowed(&dev, &intf, &hid));
  alt.desc.bInterfaceNumber = 4;
  assert(usb_modem_driver_allowed(&dev, &intf, &qmi));
  assert(!usb_modem_driver_allowed(&dev, &intf, &option));
  intf.authorized = 0; assert(!usb_modem_driver_allowed(&dev, &intf, &qmi)); intf.authorized = 1;
  dev.portnum = 2; assert(!usb_modem_profile(&dev)); dev.portnum = 1;
  dev.parent = &dev; assert(!usb_modem_profile(&dev)); dev.parent = &root;
  dev.speed = 5; assert(!usb_modem_profile(&dev)); dev.speed = USB_SPEED_HIGH;
  for (int i=0; i<209; i++) { raw[i] ^= 1; assert(!usb_modem_profile(&dev)); raw[i] ^= 1; }
  marked = false; assert(usb_modem_driver_allowed(&dev, &intf, &hid));
  return 0;
}
'''
  c = tmp_path / 'test.c'
  c.write_text(stubs + source.read_text() + '\nstatic const u8 expected[] = {' + expected + '};\n' + harness)
  exe = tmp_path / 'test'
  subprocess.run(['cc', '-std=c99', '-Wall', '-Wextra', '-Werror', '-I', str(tmp_path), str(c), '-o', str(exe)], check=True)
  subprocess.run([str(exe)], check=True)
