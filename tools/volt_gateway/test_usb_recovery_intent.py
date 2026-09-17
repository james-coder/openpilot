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
  for name in ('vgw_usb_recovery_mark','vgw_usb_recovery_take','vgw_usb_recovery_present','vgw_usb_recovery_client'):
    getattr(dll,name).argtypes=[C.POINTER(IO)]
    getattr(dll,name).restype=C.c_bool
  dll.vgw_usb_recovery_trace.argtypes=[C.POINTER(IO),C.c_uint32]
  dll.vgw_usb_recovery_report.argtypes=[C.POINTER(IO),C.POINTER(C.c_uint8)]
  dll.vgw_usb_recovery_report.restype=C.c_uint
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
  assert '__bss_end <= 0x2001bfc0' in linker(0x08000000,0x20000)
  assert ADDRESS+8<=0x2001c000


def test_client_switch_setup_preserves_can_pins(lib):
  hw=Registers()
  hw.values.update({0x40020000:0xaaaaaaaa,0x40020400:0x55555555,
                    0x40020014:0x41,0x40020414:0xc004})
  assert lib.vgw_usb_recovery_client(C.byref(hw.io))
  assert hw.values[0x40020000]&~(3<<26)==0xaaaaaaaa&~(3<<26)
  assert hw.values[0x40020400]&~(3<<4)==0x55555555&~(3<<4)
  assert hw.values[0x40020014]==0x2041
  assert hw.values[0x40020414]==0xc000
  assert {a for a,_ in hw.writes}=={0x40023830,0x40020018,0x40020418,0x40020000,0x40020400}


@pytest.mark.parametrize('ignored',[0x40023830,0x40020000,0x40020400,0x40020018])
def test_client_setup_failed_write_rejected(lib,ignored):
  hw=Registers(ignore=ignored)
  assert not lib.vgw_usb_recovery_client(C.byref(hw.io))


def test_trace_survives_intent_consumption_without_disclosing_memory(lib):
  hw=Registers()
  out=(C.c_uint8*16)()
  assert lib.vgw_usb_recovery_report(C.byref(hw.io),out)==16
  assert bytes(out)[4:8]==bytes(4)
  lib.vgw_usb_recovery_trace(C.byref(hw.io),7)
  hw.values[0x40023874]=1<<28
  assert lib.vgw_usb_recovery_mark(C.byref(hw.io))
  assert lib.vgw_usb_recovery_present(C.byref(hw.io))
  assert lib.vgw_usb_recovery_take(C.byref(hw.io))
  assert not lib.vgw_usb_recovery_present(C.byref(hw.io))
  lib.vgw_usb_recovery_report(C.byref(hw.io),out)
  assert bytes(out)==bytes.fromhex('00000001000000071000000000000000')
  hw.values[0x2001bfc8]^=1
  lib.vgw_usb_recovery_report(C.byref(hw.io),out)
  assert bytes(out)[4:8]==bytes(4)
