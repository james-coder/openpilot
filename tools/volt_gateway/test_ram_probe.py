import os
from pathlib import Path
import struct

import pytest
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_CPU_ARM_CORTEX_M4, UC_ARM_REG_SP, UC_ARM_REG_PC, UC_ARM_REG_LR, UC_ARM_REG_R0

from openpilot.tools.volt_gateway import ram_probe, ram_probe_load
from openpilot.tools.volt_gateway.test_board_image import binary


@pytest.fixture(scope='module')
def probe(tmp_path_factory):
  repository=Path(os.environ.get('VOLTGW_PANDA_HISTORY','/home/james/diagnostics/volt-gateway/history/vntarasov-panda.git'))
  if not repository.is_dir():
    pytest.skip('historical White USB source absent; RAM probe not validated')
  out=tmp_path_factory.mktemp('ram-probe')/'build'
  ram_probe.build(repository,out)
  return out


@pytest.mark.parametrize('revision,adc,voltage',[(0,1260,4776),(1,539,4776)])
def test_probe_actual_reset_has_no_can_or_flash_driver(probe,revision,adc,voltage):
  start,blob,symbols=binary(probe/'NEVER_FLASH-probe.elf')
  cpu=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
  cpu.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M4)
  for address,size in [(0x20000000,0x20000),(0x40000000,0x100000),(0x50000000,0x10000),
                       (0x1fff7000,0x1000),(0xe000e000,0x2000),(0xe0042000,0x1000)]:
    cpu.mem_map(address,size)
  cpu.mem_write(0x20000000,b'\xa5'*0x20000)
  cpu.mem_write(start,blob)
  def rd(a):
    return int.from_bytes(cpu.mem_read(a,4),'little')
  def wr(a,v):
    cpu.mem_write(a,struct.pack('<I',v))
  for address,value in [(0xe0042000,0x10000463),(0x40023800,3),(0x40023874,3),
                        (0x40007004,0x4000),(0x40020010,revision<<13)]:
    wr(address,value)
  cpu.mem_write(0x1fff7a22,struct.pack('<H',1024))
  def on_read(machine,access,address,size,value,data):
    if address==0x40023800:
      v=rd(address)
      wr(address,(v|2|((v&(1<<16))<<1)|((v&(1<<24))<<1)) if v&(1<<24) else (v|2|((v&(1<<16))<<1))&~(1<<25))
    elif address==0x40023808:
      v=rd(address)
      wr(address,(v&~12)|((v&3)<<2))
    elif address==0x50000010:
      wr(address,0x80000000)
    elif address==0x40000024:
      wr(address,rd(address)+1)
    elif address==0x40012000 and rd(0x40012008)&(1<<22):
      wr(address,4)
      wr(0x4001203c,adc)
  def on_write(machine,access,address,size,value,data):
    assert not 0x40006400<=address<0x40007000, 'probe touched CAN controller'
    assert address not in (0x40023c04,0x40023c10,0x40003000), 'probe programmed flash or started watchdog'
    if address in (0x40020018,0x40020418,0x40020818):
      wr(address-4,(rd(address-4)|(value&65535))&~(value>>16))
    if address==0xe000ed0c:
      raise AssertionError(f'unexpected reset at {cpu.reg_read(UC_ARM_REG_PC):#x}')
  cpu.hook_add(UC_HOOK_MEM_READ,on_read,begin=0x40000000,end=0x5000ffff)
  cpu.hook_add(UC_HOOK_MEM_WRITE,on_write,begin=0x40000000,end=0xe000ed0c)
  polls=[]
  def poll(machine,address,size,data):
    polls.append(True)
    if len(polls)==3:
      machine.emu_stop()
  address=symbols['vgw_white_usb_poll']&~1
  cpu.hook_add(UC_HOOK_CODE,poll,begin=address,end=address)
  stack,entry=struct.unpack_from('<II',blob)
  cpu.reg_write(UC_ARM_REG_SP,stack)
  cpu.emu_start(entry,0,timeout=5_000_000,count=10_000_000)
  assert len(polls)==3
  assert rd(0x40023820)&(7<<25)==7<<25
  assert rd(0x40020414)&0xc000==0
  cpu.reg_write(UC_ARM_REG_SP,0x2001e000)
  cpu.reg_write(UC_ARM_REG_LR,0x2001fff1)
  cpu.reg_write(UC_ARM_REG_R0,0x2001f000)
  cpu.emu_start(symbols['vgw_ram_probe_report']|1,0x2001fff0,count=100000)
  result=struct.unpack('>12I',cpu.mem_read(0x2001f000,48))
  assert result[:5]==(0x56505231,0x10000463,1024,revision,voltage)


class DfuRam:
  def __init__(self):
    self.state=2
    self.address=ram_probe_load.START
    self.ram=bytearray(ram_probe_load.END-ram_probe_load.START)
    self.jumped=False
    self.writes=0
  def setInterfaceAltSetting(self,interface,alt):
    assert (interface,alt)==(0,0)
  def controlWrite(self,kind,request,value,index,data,timeout):
    assert (kind,index,timeout)==(0x21,0,2000)
    if request==6:
      assert not data
      self.state=2
    elif request==1 and value==0:
      assert len(data)==5 and data[0]==0x21  # no erase or unprotect opcode
      self.address=int.from_bytes(data[1:],'little')
      assert ram_probe_load.START<=self.address<ram_probe_load.END
      self.state=5
    else:
      assert request==1 and value==2
      if not data:
        self.jumped=True
      else:
        offset=self.address-ram_probe_load.START
        assert len(data)<=1024 and offset+len(data)<=len(self.ram)
        self.ram[offset:offset+len(data)]=data
        self.writes+=1
        self.state=5
    return len(data)
  def controlRead(self,kind,request,value,index,size,timeout):
    assert (kind,index,timeout)==(0xa1,0,2000)
    if request==3:
      assert value==0 and size==6
      return bytes([0,0,0,0,self.state,0])
    assert request==2 and value==2
    offset=self.address-ram_probe_load.START
    return bytes(self.ram[offset:offset+size])


def test_sram_load_exact_readback_never_flash(probe):
  hardware=DfuRam()
  blob=(probe/'NEVER_FLASH-probe.bin').read_bytes()
  ram_probe_load.load(hardware,blob)
  assert hardware.jumped and hardware.ram[:len(blob)]==blob
  assert hardware.writes==(len(blob)+1023)//1024


@pytest.mark.parametrize('address,size',[(0x08000000,8),(0x1fffc000,8),(0x20000000,8),
                                        (0x2000fffc,8),(0x20010000,8),(0x20004000,2048)])
def test_sram_pointer_rejects_every_other_region(address,size):
  hardware=DfuRam()
  with pytest.raises(ValueError):
    ram_probe_load.pointer(hardware,address,size)
  assert not hardware.jumped and hardware.writes==0
