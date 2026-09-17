import ctypes as C
from pathlib import Path
import struct
import subprocess

import pytest

from openpilot.tools.volt_gateway import debug_memory


@pytest.fixture(scope='module')
def lib(tmp_path_factory):
  own=Path(__file__).parent/'firmware'
  out=tmp_path_factory.mktemp('debug-memory')
  mock=out/'mock.c'
  mock.write_text('''#include <stdint.h>
unsigned reads,writes; uint32_t last,value;
uint32_t vgw_debug_read32(uint32_t a) { reads++; last=a; return value; }
void vgw_debug_write32(uint32_t a,uint32_t v) { writes++; last=a; value=v; }
''')
  subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-shared','-fPIC','-DDEBUG=1','-DVGW_RAM_PROBE',
                  str(own/'debug_memory.c'),str(mock),'-o',str(out/'debug.so')],check=True)
  dll=C.CDLL(str(out/'debug.so'))
  dll.vgw_debug_memory.argtypes=[C.c_void_p,C.c_size_t,C.c_void_p]
  return dll


def invoke(lib,packet):
  out=C.create_string_buffer(16)
  lib.vgw_debug_memory(packet,len(packet),out)
  return struct.unpack('<4I',out.raw)


def test_read_write_and_arbitrary_addresses(lib):
  for address in (0,0x2001c400,0x40003004,0xe0042000,0xfffffffc):
    assert invoke(lib,debug_memory.request(address,0xa5c35a3c))==(0x31524756,0,address,0xa5c35a3c)
    assert invoke(lib,debug_memory.request(address))==(0x31524756,0,address,0xa5c35a3c)
    assert C.c_uint32.in_dll(lib,'last').value==address


@pytest.mark.parametrize('packet',[b'',bytes(15),bytes(17),b'BAD!'+bytes(12),
  struct.pack('<4sIII',b'VGM1',2,0,0),struct.pack('<4sIII',b'VGM1',0,1,0)])
def test_malformed_never_accesses_memory(lib,packet):
  before=[C.c_uint.in_dll(lib,name).value for name in ('reads','writes')]
  assert invoke(lib,packet)[1]==1
  assert before==[C.c_uint.in_dll(lib,name).value for name in ('reads','writes')]


@pytest.mark.parametrize('flags',[[],['-DDEBUG=1'],['-DVGW_RAM_PROBE'],['-DDEBUG=0','-DVGW_RAM_PROBE']])
def test_production_or_nonexplicit_build_rejected(tmp_path,flags):
  source=Path(__file__).parent/'firmware/debug_memory.c'
  result=subprocess.run(['cc',*flags,'-c',str(source),'-o',str(tmp_path/'no.o')],capture_output=True)
  assert result.returncode!=0
  assert b'Arbitrary memory access' in result.stderr


def test_host_refuses_normal_firmware_without_bulk_access():
  class Normal:
    def controlRead(self,*args,**kwargs):
      return b'voltgw-v1'
  with pytest.raises(ValueError,match='no memory command'):
    debug_memory.exchange(Normal(),0x2001c400,1)


@pytest.mark.parametrize('address,value',[(-4,None),(1,None),(0x100000000,None),(0,-1),(0,0x100000000)])
def test_host_bounds(address,value):
  with pytest.raises(ValueError):
    debug_memory.request(address,value)
