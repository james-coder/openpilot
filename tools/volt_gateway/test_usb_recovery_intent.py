"""One-shot reset mailbox. No USB enumeration or hardware claims."""
import ctypes as C
from pathlib import Path
import subprocess

import pytest

from openpilot.tools.volt_gateway.test_white_board import IO, Registers

ADDRESS=0x2001bfe0
MAGIC=0x56524732


@pytest.fixture(scope='module')
def lib(tmp_path_factory):
  own=Path(__file__).parent/'firmware'
  dest=tmp_path_factory.mktemp('recovery-intent')/'intent.so'
  subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fanalyzer','-shared','-fPIC',
                  str(own/'usb_recovery_intent.c'),'-o',str(dest)],check=True)
  dll=C.CDLL(str(dest))
  for name in ('vgw_usb_recovery_mark','vgw_usb_recovery_take'):
    getattr(dll,name).argtypes=[C.POINTER(IO)]
    getattr(dll,name).restype=C.c_bool
  return dll


def test_one_shot_software_reset_intent(lib):
  hw=Registers()
  assert lib.vgw_usb_recovery_mark(C.byref(hw.io))
  hw.values[0x40023874]=1<<28
  assert lib.vgw_usb_recovery_take(C.byref(hw.io))
  assert not lib.vgw_usb_recovery_take(C.byref(hw.io))
  assert {a for a,_ in hw.writes}=={ADDRESS,ADDRESS+4}


@pytest.mark.parametrize('reset',[0,1<<25,1<<26,1<<27,1<<29,1<<30])
def test_nonsoftware_reset_rejects_and_clears(lib,reset):
  hw=Registers()
  assert lib.vgw_usb_recovery_mark(C.byref(hw.io))
  hw.values[0x40023874]=reset
  assert not lib.vgw_usb_recovery_take(C.byref(hw.io))
  assert hw.values[ADDRESS]==hw.values[ADDRESS+4]==0


@pytest.mark.parametrize('a,b',[(0,0),(MAGIC,0),(0,~MAGIC&0xffffffff),(MAGIC^1,~MAGIC&0xffffffff)])
def test_torn_or_corrupt_intent(lib,a,b):
  hw=Registers()
  hw.values.update({ADDRESS:a,ADDRESS+4:b,0x40023874:1<<28})
  assert not lib.vgw_usb_recovery_take(C.byref(hw.io))


@pytest.mark.parametrize('ignored',[ADDRESS,ADDRESS+4])
def test_failed_marker_write(lib,ignored):
  hw=Registers(ignore=ignored)
  assert not lib.vgw_usb_recovery_mark(C.byref(hw.io))


def test_reserved_outside_bss_and_stack():
  from openpilot.tools.volt_gateway.board_build import linker
  assert '__bss_end <= 0x2001bfe0' in linker(0x08000000,0x20000)
  assert ADDRESS+8<=0x2001c000
