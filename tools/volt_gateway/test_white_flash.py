"""Register/flash fault simulation, not STM32 electrical/timing validation."""
import ctypes as C
from pathlib import Path
import subprocess

import pytest

U, P, B = C.c_uint32, C.c_void_p, C.c_bool
RR = C.CFUNCTYPE(U, P, U)
RW = C.CFUNCTYPE(B, P, U, U)
READ = C.CFUNCTYPE(B, P, U, P, U)
PROGRAM = C.CFUNCTYPE(B, P, U, C.c_uint8)
NOW = C.CFUNCTYPE(U, P)
BOOL = C.CFUNCTYPE(B, P)
VOID = C.CFUNCTYPE(None, P)
ACR, KEYR, SR, CR = 0x40023c00, 0x40023c04, 0x40023c0c, 0x40023c10
LOCK, BUSY = 1 << 31, 1 << 16
SLOT_SIZE, SECTOR = 0x60000, 0x20000
BASES = (0x08040000, 0x080a0000)
MAGIC = bytes.fromhex('77c295f360d2ef7f3552500f2cb67980')


class IO(C.Structure):
  _fields_ = [('ctx', P), ('reg_read', RR), ('reg_write', RW), ('read', READ), ('program8', PROGRAM),
              ('now_ms', NOW), ('permit', BOOL), ('enter', BOOL), ('leave', VOID), ('service', VOID), ('fatal', VOID), ('poll_limit', U)]


@pytest.fixture(scope='module')
def lib(tmp_path_factory):
  source = Path(__file__).parent/'firmware/white_flash.c'
  binary = tmp_path_factory.mktemp('flash')/'flash.so'
  subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-fanalyzer', '-shared', '-fPIC', '-DVGW_FLASH_TEST',
                  str(source), '-o', str(binary)], check=True)
  lib = C.CDLL(str(binary))
  for name, result, args in (
    ('size', C.c_size_t, []), ('init', B, [P, C.POINTER(IO), U, B]), ('erase', B, [P,U]),
    ('program', B, [P,U,P,U]), ('trailer', B, [P,U,P,U]), ('read', B, [P,U,P,U]), ('failed',B,[P]),
  ):
    fn = getattr(lib, 'vgw_white_flash_' + name)
    fn.restype, fn.argtypes = result, args
  return lib


class Model:
  def __init__(self, lib, slot=0, mutable=True):
    self.lib, self.slot = lib, slot
    self.state = C.create_string_buffer(lib.vgw_white_flash_size())
    self.memory = bytearray(b'\xff' * SLOT_SIZE)
    self.registers = {ACR: 0x705, CR: LOCK, SR: 0}
    self.writes, self.operations, self.errors = [], [], []
    self.busy_reads, self.busy_left = 2, 0
    self.clock, self.clock_step = 0, 1
    self.allowed, self.in_critical = True, False
    self.keys, self.fatals, self.serviced = 0, 0, 0
    self.false_program = self.false_erase = self.stuck = self.error_after = False
    self.deny_after = None
    self.ignore_reg = None
    self.io = IO(None, RR(self.reg_read), RW(self.reg_write), READ(self.read), PROGRAM(self.program),
                 NOW(self.now), BOOL(lambda _: self.allowed), BOOL(self.enter), VOID(self.leave),
                 VOID(self.service), VOID(self.fatal), 100)
    assert lib.vgw_white_flash_init(self.state, C.byref(self.io), slot, mutable)

  def reg_read(self, _, addr):
    if addr == SR and self.registers[SR] & BUSY:
      if not self.stuck:
        self.busy_left -= 1
        if self.busy_left <= 0:
          self.registers[SR] = 0x20 if self.error_after else 1
    return self.registers.get(addr, 0)

  def reg_write(self, _, addr, value):
    self.writes.append((addr, value))
    if not self.in_critical or addr not in (ACR, CR, SR, KEYR):
      self.errors.append('bad register/critical ownership')
      return False
    if addr == self.ignore_reg:
      return True  # falsely successful driver write
    if addr == KEYR:
      self.keys = self.keys + 1 if value == (0x45670123 if self.keys == 0 else 0xcdef89ab) else 0
      if self.keys == 2:
        self.registers[CR] = 0
        self.keys = 0
    elif addr == SR:
      self.registers[SR] &= ~value
    else:
      if addr == CR and self.registers[SR] & BUSY:
        self.errors.append('changed CR while busy')
        return False
      if addr == CR and value & 4:
        self.errors.append('mass erase requested')
        return False
      if addr == CR and value & 0x10000:
        sector = (value >> 3) & 31
        offset = 0x08020000 + (sector - 5) * SECTOR - BASES[self.slot]
        if value != (0x10002 | sector << 3) or not 0 <= offset <= SLOT_SIZE-SECTOR or self.registers[CR] & LOCK:
          self.errors.append('bad erase command')
          return False
        self.operations.append(('erase', offset))
        if not self.false_erase:
          self.memory[offset:offset+SECTOR] = b'\xff'*SECTOR
        self.busy_left = self.busy_reads
        self.registers[SR] = BUSY
        value &= ~0x10000
      self.registers[addr] = value
    return True

  def read(self, _, addr, out, size):
    offset = addr - BASES[self.slot]
    if not 0 <= offset <= SLOT_SIZE or size > SLOT_SIZE - offset or self.registers[SR] & BUSY:
      self.errors.append('out-of-slot or busy memory read')
      return False
    C.memmove(out, bytes(self.memory[offset:offset+size]), size)
    return True

  def program(self, _, addr, value):
    offset = addr - BASES[self.slot]
    if not self.in_critical or self.registers[CR] != 1 or not 0 <= offset < SLOT_SIZE or self.registers[SR] & BUSY:
      self.errors.append('bad byte program')
      return False
    self.operations.append(('program', offset))
    if not self.false_program:
      self.memory[offset] &= value
    self.busy_left = self.busy_reads
    self.registers[SR] = BUSY
    return True

  def now(self, _):
    self.clock = (self.clock + self.clock_step) & 0xffffffff
    return self.clock

  def enter(self, _):
    if self.in_critical:
      return False
    self.in_critical = True
    return True

  def leave(self, _):
    self.in_critical = False

  def service(self, _):
    self.serviced += 1
    if self.deny_after is not None and self.serviced >= self.deny_after:
      self.allowed = False

  def fatal(self, _):
    self.fatals += 1  # Native-only escape; real callback must not return.

  def clean(self):
    assert not self.errors
    assert self.registers[CR] == LOCK
    assert self.registers[ACR] == 0x705
    assert not self.in_critical


@pytest.mark.parametrize('slot', [0,1])
@pytest.mark.parametrize('sector', range(3))
def test_exact_sector_erase(lib, slot, sector):
  m = Model(lib,slot)
  m.memory[:] = b'\x66'*SLOT_SIZE
  assert lib.vgw_white_flash_erase(m.state,sector*SECTOR)
  assert m.memory[sector*SECTOR:(sector+1)*SECTOR] == b'\xff'*SECTOR
  assert m.memory[:sector*SECTOR] == b'\x66'*(sector*SECTOR)
  assert m.memory[(sector+1)*SECTOR:] == b'\x66'*(SLOT_SIZE-(sector+1)*SECTOR)
  assert m.operations == [('erase',sector*SECTOR)]
  m.clean()


@pytest.mark.parametrize('size', [1,2,3,4,63,255,256])
def test_byte_program_and_duplicate(lib,size):
  m = Model(lib)
  data = bytes(i % 251 for i in range(size))
  assert lib.vgw_white_flash_program(m.state,3,data,size)
  assert m.memory[3:3+size] == data
  operations = list(m.operations)
  assert lib.vgw_white_flash_program(m.state,3,data,size)
  assert m.operations == operations
  m.clean()


@pytest.mark.parametrize('off,size', [(0,0),(0,257),(SLOT_SIZE-65,2),(SLOT_SIZE-64,1),(0xffffffff,2),(SLOT_SIZE,1)])
def test_payload_bounds_no_register_writes(lib, off,size):
  m = Model(lib)
  assert not lib.vgw_white_flash_program(m.state,off,bytes(257),size)
  assert not m.writes
  assert lib.vgw_white_flash_failed(m.state)


@pytest.mark.parametrize('off', [1,SECTOR+1,SLOT_SIZE,0xffffffff])
def test_erase_bounds(lib,off):
  m = Model(lib)
  assert not lib.vgw_white_flash_erase(m.state,off)
  assert not m.writes


@pytest.mark.parametrize('off,data', [(SLOT_SIZE-32,b'\x01\xff\xff\xff'), (SLOT_SIZE-24,b'\x01\xff\xff\xff'), (SLOT_SIZE-16,MAGIC)])
def test_narrow_metadata_and_protected_image(lib,off,data):
  m = Model(lib,mutable=False)
  assert lib.vgw_white_flash_trailer(m.state,off,data,len(data))
  assert m.memory[off:off+len(data)] == data
  m.clean()
  assert not lib.vgw_white_flash_erase(m.state,0)
  assert not lib.vgw_white_flash_program(m.state,0,b'x',1)


@pytest.mark.parametrize('off,data', [(SLOT_SIZE-32,b'\x02\xff\xff\xff'), (SLOT_SIZE-24,b'\x01\x00\xff\xff'),
                                   (SLOT_SIZE-16,bytes(16)), (SLOT_SIZE-64,b'\x01\xff\xff\xff')])
def test_metadata_rejects_arbitrary_values(lib,off,data):
  m = Model(lib)
  assert not lib.vgw_white_flash_trailer(m.state,off,data,len(data))
  assert not m.writes


@pytest.mark.parametrize('kind', ['false_program','false_erase','error_after','power_loss','not_allowed','zero_to_one'])
def test_failures_latch_and_stop(lib,kind):
  m = Model(lib)
  if kind in ('false_program','false_erase','error_after'):
    setattr(m,kind,True)
  if kind == 'power_loss':
    m.deny_after=1
  if kind == 'not_allowed':
    m.allowed=False
  if kind == 'zero_to_one':
    m.memory[0]=0
  if kind == 'false_erase':
    m.memory[0]=0
    assert not lib.vgw_white_flash_erase(m.state,0)
  else:
    assert not lib.vgw_white_flash_program(m.state,0,b'\x55',1)
  operations=list(m.operations)
  assert lib.vgw_white_flash_failed(m.state)
  assert not lib.vgw_white_flash_program(m.state,1,b'x',1)
  assert m.operations == operations
  m.clean()


@pytest.mark.parametrize('frozen_clock', [False,True])
def test_busy_timeout_stays_in_ram_critical_section(lib,frozen_clock):
  m = Model(lib)
  m.stuck=True
  m.clock_step=0 if frozen_clock else 20
  assert not lib.vgw_white_flash_program(m.state,0,b'x',1)
  assert m.fatals and m.in_critical
  assert len(m.operations)==1 and not m.errors
  assert lib.vgw_white_flash_failed(m.state)


@pytest.mark.parametrize('reg', [ACR,KEYR])
def test_register_write_readback(lib,reg):
  m = Model(lib)
  m.ignore_reg=reg
  assert not lib.vgw_white_flash_program(m.state,0,b'x',1)
  assert not m.operations
  assert lib.vgw_white_flash_failed(m.state)


def test_clock_wrap_is_not_a_timeout(lib):
  m = Model(lib)
  m.clock=0xfffffffe
  assert lib.vgw_white_flash_program(m.state,0,b'x',1)
  m.clean()
