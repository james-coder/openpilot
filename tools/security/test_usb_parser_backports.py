"""Off-device source-derived parser regressions; no USB hardware access.

Only temporary source/object artifacts are written. The deployed build is
read-only. These tests are not full USB enumeration or target validation.
"""
import hashlib
from pathlib import Path
import re
import shlex
import shutil
import subprocess

import pytest

KERNEL = Path('/home/james/git/volt-modem-kernel-candidate')
PATCHES = Path(__file__).parent / 'kernel_backports'
SOURCE_HASH = '3bb0ac8008652da4c40a9e3aed0533c8093ca6bb41a33e86ff263a96d01b3de9'
CONFIG_HASH = '2c1248037df31728e47d41430b80d51a8ccc3a8e728401af074ca9f85ab049f4'


def test_archived_series_matches_deployed_parser(tmp_path):
  vendor = Path('/tmp/modem-security-audit.7sM9Y2/vendor.git')
  if not vendor.exists():
    pytest.skip('Pinned vendor Git history unavailable')
  data = subprocess.check_output(['git', '--git-dir=' + str(vendor), 'show',
                                 'c368754c26c7b9659de187addc6cccedc6cfb0a0:drivers/usb/core/config.c'])
  target = tmp_path / 'drivers/usb/core/config.c'
  target.parent.mkdir(parents=True)
  target.write_bytes(data)
  subprocess.run(['git', 'apply', '--include=drivers/usb/core/config.c',
                  str(PATCHES / '0100-complete-offdevice-candidate.patch')], cwd=tmp_path, check=True)
  assert hashlib.sha256(target.read_bytes()).hexdigest() == SOURCE_HASH
  assert 'diff --git a/drivers/usb/core/config.c' not in (PATCHES / '0101-protected-root-hub-interface.patch').read_text()


@pytest.fixture
def candidate(tmp_path):
  source = KERNEL / 'drivers/usb/core/config.c'
  if not source.exists():
    pytest.skip('Exact deployed vendor source unavailable; not a validation pass')
  original = source.read_bytes()
  assert hashlib.sha256(original).hexdigest() == SOURCE_HASH
  target = tmp_path / 'drivers/usb/core/config.c'
  target.parent.mkdir(parents=True)
  shutil.copyfile(source, target)
  for patch in ('0102-duplicate-endpoints.patch', '0103-maxpacket-validation.patch'):
    subprocess.run(['git', 'apply', '--check', str(PATCHES / patch)], cwd=tmp_path, check=True)
    subprocess.run(['git', 'apply', str(PATCHES / patch)], cwd=tmp_path, check=True)
  yield target, original.decode()
  assert source.read_bytes() == original


PRELUDE = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#define CHECK(x) do { if (!(x)) return __LINE__; } while (0)
#define BIT(n) (1U << (n))
#define USB_SPEED_LOW 1
#define USB_SPEED_FULL 2
#define USB_SPEED_HIGH 3
#define USB_SPEED_SUPER 5
#define USB_SPEED_SUPER_PLUS 6
#define USB_ENDPOINT_XFER_CONTROL 0
#define USB_ENDPOINT_XFER_ISOC 1
#define USB_ENDPOINT_XFER_BULK 2
#define USB_ENDPOINT_XFER_INT 3
#define le16_to_cpu(x) (x)
#define cpu_to_le16(x) (x)
#define dev_warn(...) ((void)0)
#define to_usb_device(x) (x)
struct device { int speed; };
struct usb_endpoint_descriptor { unsigned char bEndpointAddress, bmAttributes; uint16_t wMaxPacketSize; };
struct usb_host_endpoint { struct usb_endpoint_descriptor desc; };
struct usb_host_interface {
 struct { unsigned char bInterfaceNumber, bAlternateSetting, bNumEndpoints; } desc;
 struct usb_host_endpoint *endpoint;
};
struct usb_interface_cache { unsigned num_altsetting; struct usb_host_interface *altsetting; };
struct usb_host_config { struct { unsigned char bNumInterfaces; } desc; struct usb_interface_cache **intf_cache; };
#define usb_endpoint_type(d) ((d)->bmAttributes & 3)
#define usb_endpoint_xfer_control(d) (usb_endpoint_type(d) == 0)
#define usb_endpoint_xfer_isoc(d) (usb_endpoint_type(d) == 1)
#define usb_endpoint_xfer_int(d) (usb_endpoint_type(d) == 3)
#define usb_endpoint_num(d) ((d)->bEndpointAddress & 15)
#define usb_endpoint_maxp(d) ((d)->wMaxPacketSize & 0x7ff)
'''


def run_c(tmp_path, code, name):
  source, binary = tmp_path / (name + '.c'), tmp_path / name
  source.write_text(PRELUDE + code)
  subprocess.run(['cc', '-std=c99', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-sign-compare',
                  '-fsanitize=undefined', '-fno-sanitize-recover=all', '-I', str(PATCHES.parent), str(source), '-o', str(binary)], check=True)
  return subprocess.run([str(binary)], check=False).returncode


def test_duplicate_endpoints_and_old_code_fails(candidate, tmp_path):
  target, original = candidate
  fixed = target.read_text()
  helpers = fixed[fixed.index('static bool endpoint_is_duplicate'):fixed.index('static int usb_parse_endpoint')]
  assert 'config_endpoint_is_duplicate(config, inum, asnum, d)' in fixed
  assert 'usb_parse_endpoint(ddev, cfgno, config, inum, asnum,' in fixed
  vectors = r'''
#include "usb_lab_profile.h"
int main(void) {
 struct usb_host_endpoint ep = { .desc = { .bEndpointAddress = 0x81, .bmAttributes = 2 } };
 struct usb_host_interface alt = { .desc = { .bInterfaceNumber = 0, .bAlternateSetting = 0, .bNumEndpoints = 1 }, .endpoint = &ep };
 struct usb_interface_cache cache = { .num_altsetting = 1, .altsetting = &alt };
 struct usb_interface_cache *caches[] = { &cache };
 struct usb_host_config cfg = { .desc = { .bNumInterfaces = 1 }, .intf_cache = caches };
 struct usb_endpoint_descriptor d = ep.desc;
 CHECK(config_endpoint_is_duplicate(&cfg, 0, 0, &d));
 CHECK(!config_endpoint_is_duplicate(&cfg, 0, 1, &d)); /* alternate reuse */
 CHECK(config_endpoint_is_duplicate(&cfg, 1, 0, &d)); /* cross-interface */
 d.bEndpointAddress = 1;
 CHECK(!config_endpoint_is_duplicate(&cfg, 1, 0, &d)); /* opposite bulk directions */
 d.bmAttributes = 0;
 CHECK(config_endpoint_is_duplicate(&cfg, 1, 0, &d)); /* candidate control */
 d.bmAttributes = 2; ep.desc.bmAttributes = 0;
 CHECK(config_endpoint_is_duplicate(&cfg, 1, 0, &d)); /* existing control */
 d.bEndpointAddress = 2;
 CHECK(!config_endpoint_is_duplicate(&cfg, 1, 0, &d));
 cache.num_altsetting = 0;
 CHECK(!config_endpoint_is_duplicate(&cfg, 1, 0, &d));
 /* Captured normal EG25: test every endpoint against all prior interfaces. */
 struct usb_host_endpoint endpoints[5][3] = {0};
 struct usb_host_interface alts[5] = {0};
 struct usb_interface_cache stores[5] = {0};
 struct usb_interface_cache *ptrs[5];
 struct usb_host_config normal = { .desc = { .bNumInterfaces = 5 }, .intf_cache = ptrs };
 for (int k=0; k<5; k++) {
   ptrs[k] = &stores[k]; stores[k].num_altsetting = 1; stores[k].altsetting = &alts[k];
   alts[k].desc.bInterfaceNumber = k; alts[k].endpoint = endpoints[k];
 }
 int interface = -1, count = 0;
 for (size_t offset=18; offset<sizeof(expected); offset+=expected[offset]) {
   CHECK(expected[offset] >= 2 && offset + expected[offset] <= sizeof(expected));
   if (expected[offset+1] == 4) interface = expected[offset+2];
   if (expected[offset+1] != 5) continue;
   CHECK(interface >= 0 && interface < 5 && alts[interface].desc.bNumEndpoints < 3);
   struct usb_endpoint_descriptor incoming = { .bEndpointAddress = expected[offset+2], .bmAttributes = expected[offset+3] };
   CHECK(!config_endpoint_is_duplicate(&normal, interface, 0, &incoming));
   endpoints[interface][alts[interface].desc.bNumEndpoints++].desc = incoming;
   count++;
 }
 CHECK(count == 14);
 return 0;
}
'''
  assert run_c(tmp_path, helpers + vectors, 'duplicates_fixed') == 0
  # Extract the actual original loop; convert its skip branch to a predicate.
  old = original.split('/* Check for duplicate endpoint addresses */', 1)[1].split('\n\tendpoint =', 1)[0]
  old = re.sub(r'dev_warn\(.*?;', '', old, flags=re.S)
  old = old.replace('goto skip_to_next_endpoint_or_interface_descriptor;', 'return true;')
  wrapper = '''static bool config_endpoint_is_duplicate(struct usb_host_config *config,
int inum, int asnum, struct usb_endpoint_descriptor *d) {
 struct usb_host_interface empty = {0};
 struct usb_host_interface *ifp = &empty;
 int i;
 if (inum == 0 && asnum == 0) ifp = &config->intf_cache[0]->altsetting[0];
''' + old + '\nreturn false; }\n'
  assert run_c(tmp_path, wrapper + vectors, 'duplicates_original') != 0


def test_packet_size_and_old_code_fails(candidate, tmp_path):
  target, original = candidate
  fixed = target.read_text()
  tables = fixed[fixed.index('static const unsigned short low_speed_maxpacket_maxes'):fixed.index('static bool endpoint_is_duplicate')]
  vectors = r'''
#include "usb_lab_profile.h"
int main(void) {
 CHECK(validate(USB_SPEED_HIGH, 2, 512) == 512);
 CHECK(validate(USB_SPEED_HIGH, 3, 0x1400) == 0x1400); /* legal HS transactions */
 CHECK(validate(USB_SPEED_HIGH, 1, 0x0c00) == 0x0c00);
 CHECK(validate(USB_SPEED_HIGH, 2, 0x1200) == 1024); /* illegal bulk transaction bits */
 CHECK(validate(USB_SPEED_FULL, 3, 0x0840) == 64);
 CHECK(validate(USB_SPEED_LOW, 3, 0x0808) == 8);
 CHECK(validate(USB_SPEED_HIGH, 3, 0x2400) == 1024); /* reserved high bits */
 CHECK(validate(USB_SPEED_HIGH, 1, 0) == 0); /* preserve zero handling */
 CHECK(validate(USB_SPEED_SUPER, 2, 1024) == 1024);
 int count = 0;
 for (size_t offset=18; offset<sizeof(expected); offset+=expected[offset]) {
   CHECK(expected[offset] >= 2 && offset + expected[offset] <= sizeof(expected));
   if (expected[offset+1] != 5) continue;
   unsigned raw = expected[offset+4] | (expected[offset+5] << 8);
   CHECK(validate(USB_SPEED_HIGH, expected[offset+3] & 3, raw) == raw);
   count++;
 }
 CHECK(count == 14);
 return 0;
}
'''
  for name, source in [('fixed', fixed), ('original', original)]:
    block = source.split('/* Validate the wMaxPacketSize field */', 1)[1].split('\n\t/*\n\t * Some buggy high speed', 1)[0]
    function = '''static unsigned validate(int speed, int type, unsigned raw) {
 struct device dev = { .speed = speed }, *ddev = &dev;
 struct usb_host_endpoint storage = { .desc = { .bmAttributes = type, .wMaxPacketSize = raw } }, *endpoint = &storage;
 struct usb_endpoint_descriptor *d = &endpoint->desc;
 unsigned int maxp;
 const unsigned short *maxpacket_maxes;
 int i, j;
''' + block + '\nreturn endpoint->desc.wMaxPacketSize; }\n'
    result = run_c(tmp_path, tables + function + vectors, 'packet_' + name)
    assert (result == 0) == (name == 'fixed')


def test_cross_compile_config_object(candidate, tmp_path):
  target, _ = candidate
  config = KERNEL / 'out/.config'
  assert hashlib.sha256(config.read_bytes()).hexdigest() == CONFIG_HASH
  recorded = (KERNEL / 'out/drivers/usb/core/.config.o.cmd').read_text().splitlines()[0].split(' := ', 1)[1]
  args = shlex.split(recorded)
  assert args[-1] == '../drivers/usb/core/config.c'
  args[-1] = str(target)
  args[args.index('-o') + 1] = str(tmp_path / 'config.o')
  args = [a if not a.startswith('-Wp,-MD,') else '-Wp,-MD,' + str(tmp_path / 'config.d') for a in args]
  subprocess.run(args, cwd=KERNEL / 'out', check=True, timeout=60)
  header = (tmp_path / 'config.o').read_bytes()[:20]
  assert header[:4] == b'\x7fELF' and int.from_bytes(header[18:20], 'little') == 183  # AArch64
