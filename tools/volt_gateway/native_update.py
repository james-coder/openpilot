"""Host adapter executing the real portable C updater. Not MCU flash/crypto."""

import ctypes as c
import hashlib

from openpilot.tools.volt_gateway.adaptive import Conditions
from openpilot.tools.volt_gateway.authority import AuthorityError, ImageManifest
from openpilot.tools.volt_gateway.update_engine import Environment


SAMPLE = c.CFUNCTYPE(c.c_bool, c.c_void_p, c.POINTER(c.c_uint64), c.POINTER(c.c_uint64), c.POINTER(c.c_bool))
ERASE = c.CFUNCTYPE(c.c_bool, c.c_void_p, c.c_uint32, c.c_uint32)
DATA = c.CFUNCTYPE(c.c_bool, c.c_void_p, c.c_uint32, c.c_void_p, c.c_size_t)
START = c.CFUNCTYPE(c.c_bool, c.c_void_p)
HASH_ADD = c.CFUNCTYPE(c.c_bool, c.c_void_p, c.c_void_p, c.c_size_t)
BUFFER = c.CFUNCTYPE(c.c_bool, c.c_void_p, c.c_void_p)


class IO(c.Structure):
  _fields_ = [('ctx', c.c_void_p), ('capacity', c.c_uint32), ('erase_sizes', c.c_uint32 * 16), ('erase_count', c.c_uint8),
              ('sample', SAMPLE), ('erase', ERASE), ('write', DATA), ('read', DATA), ('hash_start', START),
              ('hash_add', HASH_ADD), ('hash_finish', BUFFER), ('mark_trial', BUFFER)]


class NativeUpdateEngine:
  def __init__(self, authority, slot, environment):
    self.authority, self.slot, self.environment = authority, slot, environment
    self.lib = lib = authority.lib
    lib.vgw_update_size.restype = c.c_size_t
    lib.vgw_update_init.argtypes = [c.c_void_p, c.c_void_p, c.POINTER(IO)]
    lib.vgw_update_init.restype = c.c_bool
    for name in ('begin', 'finish'):
      getattr(lib, 'vgw_update_' + name).argtypes = [c.c_void_p]
      getattr(lib, 'vgw_update_' + name).restype = c.c_bool
    lib.vgw_update_chunk.argtypes = [c.c_void_p, c.c_uint32, c.c_void_p, c.c_size_t]
    lib.vgw_update_chunk.restype = c.c_bool
    lib.vgw_update_offset.argtypes = lib.vgw_update_status.argtypes = [c.c_void_p]
    lib.vgw_update_offset.restype = lib.vgw_update_status.restype = c.c_uint32
    lib.vgw_update_abort.argtypes = [c.c_void_p]
    lib.vgw_update_abort.restype = None
    self.buffer = c.create_string_buffer(lib.vgw_update_size())
    self._hash = None
    self.callback_failed = False

    def wrap(fn):
      def callback(*args):
        try:
          fn(*args)
          return True
        except Exception:
          self.callback_failed = True
          return False
      return callback

    def sample(_, now, sampled, allowed):
      value = environment()
      if (not isinstance(value, Environment) or type(value.now_ms) is not int or type(value.sampled_ms) is not int
          or not 0 <= value.sampled_ms <= value.now_ms < 2**63 or not isinstance(value.conditions, Conditions)
          or any(type(flag) is not bool for flag in vars(value.conditions).values())):
        raise ValueError('invalid environment')
      now[0], sampled[0], allowed[0] = value.now_ms, value.sampled_ms, value.conditions.allowed

    def read(_, offset, out, size):
      data = slot.read(offset, size)
      if len(data) != size:
        raise ValueError('short storage read')
      c.memmove(out, data, size)

    def start(_):
      self._hash = hashlib.sha256()

    def add(_, data, size):
      self._hash.update(c.string_at(data, size))

    def finish(_, out):
      c.memmove(out, self._hash.digest(), 32)

    callbacks = (SAMPLE(wrap(sample)), ERASE(wrap(lambda _, offset, size: slot.erase(offset, size))),
                 DATA(wrap(lambda _, offset, data, size: slot.write(offset, c.string_at(data, size)))), DATA(wrap(read)),
                 START(wrap(start)), HASH_ADD(wrap(add)), BUFFER(wrap(finish)),
                 BUFFER(wrap(lambda _, data: slot.mark_trial(ImageManifest.unpack(c.string_at(data, 122))))))
    regions = tuple(slot.erase_regions)
    if not 1 <= len(regions) <= 16 or not 0 < slot.capacity <= 1048576:
      raise AuthorityError('native slot geometry bounds')
    end = 0
    for offset, size in regions:
      if offset != end or type(size) is not int or not 0 < size <= slot.capacity - end:
        raise AuthorityError('native erase geometry')
      end += size
    self.io = IO(None, slot.capacity, (c.c_uint32 * 16)(*(size for _, size in regions)), len(regions), *callbacks)
    self._callbacks = callbacks
    if not lib.vgw_update_init(self.buffer, authority.state, c.byref(self.io)):
      raise AuthorityError('native updater initialization rejected')

  @property
  def state(self):
    return ('idle', 'receiving', 'trial', 'aborted')[self.lib.vgw_update_status(self.buffer)]

  def begin(self):
    if not self.lib.vgw_update_begin(self.buffer):
      raise AuthorityError('native update begin rejected')

  def chunk(self, offset, data):
    if type(offset) is not int or not 0 <= offset <= 0xffffffff or not isinstance(data, bytes):
      raise AuthorityError('invalid chunk arguments')
    if not self.lib.vgw_update_chunk(self.buffer, offset, data, len(data)):
      raise AuthorityError('native chunk rejected')
    return self.lib.vgw_update_offset(self.buffer)

  def finish(self):
    if not self.lib.vgw_update_finish(self.buffer):
      raise AuthorityError('native finish rejected')

  def abort(self):
    self.lib.vgw_update_abort(self.buffer)
