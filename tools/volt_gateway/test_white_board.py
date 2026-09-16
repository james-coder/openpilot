import ctypes as C
from pathlib import Path
import subprocess

import pytest

R32 = C.CFUNCTYPE(C.c_uint32, C.c_void_p, C.c_uint32)
R16 = C.CFUNCTYPE(C.c_uint16, C.c_void_p, C.c_uint32)
W32 = C.CFUNCTYPE(None, C.c_void_p, C.c_uint32, C.c_uint32)


class IO(C.Structure):
  _fields_ = [('ctx', C.c_void_p), ('read32', R32), ('read16', R16), ('write32', W32)]


class Identity(C.Structure):
  _fields_ = [('device', C.c_uint16), ('revision', C.c_uint16), ('flash_kib', C.c_uint16)]


@pytest.fixture(scope='module')
def lib(tmp_path_factory):
  source = Path(__file__).parent / 'firmware'
  dest = tmp_path_factory.mktemp('white-mmio') / 'white.so'
  subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-fanalyzer', '-shared', '-fPIC',
                  str(source/'white_board.c'), str(source/'status_led.c'), '-o', str(dest)], check=True)
  result = C.CDLL(str(dest))
  result.vgw_white_quiesce.argtypes, result.vgw_white_quiesce.restype = [C.POINTER(IO)], C.c_bool
  result.vgw_white_identify.argtypes, result.vgw_white_identify.restype = [C.POINTER(IO), C.POINTER(Identity)], C.c_bool
  result.vgw_white_led.argtypes, result.vgw_white_led.restype = [C.POINTER(IO), C.c_uint8], None
  return result


class Registers:
  def __init__(self, initial=0, ignore=None):
    self.values = {}
    self.writes = []
    self.initial = initial
    self.ignore = ignore
    self.io = IO(None, R32(lambda _, a: self.values.get(a, initial)),
                 R16(lambda _, a: self.values.get(a, initial) & 0xffff), W32(self.write))

  def write(self, _, addr, value):
    self.writes.append((addr, value))
    if addr == self.ignore:
      return
    if addr in (0x40020018, 0x40020818):
      odr = addr - 4
      self.values[odr] = ((self.values.get(odr, self.initial) | (value & 0xffff)) & ~(value >> 16))
    else:
      self.values[addr] = value


@pytest.mark.parametrize('initial', [0, 0xffffffff, 0xa5a5a5a5])
def test_safe_order_and_no_can_flash_usb_access(lib, initial):
  regs = Registers(initial)
  assert lib.vgw_white_quiesce(C.byref(regs.io))
  allowed = {0x40023830, 0x40023820, 0x40020018, 0x40020818}
  allowed |= {base + offset for base in (0x40020000, 0x40020800) for offset in (0, 4, 8, 12)}
  allowed |= {0x40020400, 0x4002040c}
  assert {addr for addr, _ in regs.writes} <= allowed
  addresses = [addr for addr, _ in regs.writes]
  assert addresses.index(0x40020818) < addresses.index(0x40020800) < addresses.index(0x40023820)
  assert addresses.index(0x40020018) < addresses.index(0x40020000)
  # USB PA11/12 and debug PA13/14 fields stay untouched.
  for pin in (11, 12, 13, 14):
    mask = 3 << (pin*2)
    assert regs.values[0x40020000] & mask == initial & mask
  before = dict(regs.values)
  for rgb in range(8):
    lib.vgw_white_led(C.byref(regs.io), rgb)
    assert regs.writes[-1][0] == 0x40020818
    pins = (1 << 9) | (1 << 7) | (1 << 6)
    assert regs.values[0x40020814] & ~pins == before[0x40020814] & ~pins


@pytest.mark.parametrize('ignore', [0x40023830, 0x40020818, 0x40020018, 0x40023820])
def test_rejected_mmio_write_fails(lib, ignore):
  regs = Registers(ignore=ignore)
  assert not lib.vgw_white_quiesce(C.byref(regs.io))


@pytest.mark.parametrize('port', [0x40020000, 0x40020800])
@pytest.mark.parametrize('offset', [0, 4, 8, 12])
def test_pin_configuration_readback(lib, port, offset):
  regs = Registers(initial=0xffffffff, ignore=port+offset)
  assert not lib.vgw_white_quiesce(C.byref(regs.io))


@pytest.mark.parametrize('device,size,ok', [(0x463,1536,True), (0x463,1024,False), (0x413,1536,False), (0,0,False)])
def test_read_only_identity_gate(lib, device, size, ok):
  regs = Registers()
  regs.values.update({0xe0042000: 0x10000000 | device, 0x1fff7a22: size})
  identity = Identity()
  assert lib.vgw_white_identify(C.byref(regs.io), C.byref(identity)) == ok
  assert (identity.device, identity.revision, identity.flash_kib) == (device, 0x1000, size)
  assert not regs.writes
