"""Exact labeled Panda: load the reviewed probe into volatile SRAM only.

No flash/option/OTP write, erase, unprotect or application replacement operation.
Requires ROM DFU already entered. 0x20000000..0x20003fff belongs to ROM and is
never overwritten. All writes are range-checked and verified before the jump.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import struct
import time

from openpilot.tools.volt_gateway import chip_inspect
from openpilot.tools.volt_gateway.operator import bounded_read

START=0x20004000
END=0x20010000


def wait_download(handle):
  for _ in range(20):
    status=chip_inspect.status(handle)
    if status[0]:
      raise ValueError('ROM rejected SRAM operation; no retries/bypass')
    if status[4]==5:
      return
    delay=int.from_bytes(status[1:4],'little')
    if status[4] not in (3,4) or delay>1000:
      raise ValueError('invalid SRAM download state/delay')
    time.sleep(max(.001,delay/1000))
  raise TimeoutError('SRAM download timeout')


def pointer(handle,address,size):
  if not START<=address<END or not 0<size<=1024 or size>END-address:
    raise ValueError('outside fixed probe SRAM window')
  chip_inspect.idle(handle)
  handle.controlWrite(0x21,1,0,0,b'\x21'+address.to_bytes(4,'little'),timeout=2000)
  wait_download(handle)


def load(handle,blob):
  if not 472<=len(blob)<=END-START or len(blob)%4:
    raise ValueError('probe image bounds')
  stack,entry=struct.unpack_from('<II',blob)
  if stack!=0x20020000 or not entry&1 or not START+472<=entry&~1<START+len(blob):
    raise ValueError('probe vectors outside SRAM code')
  handle.setInterfaceAltSetting(0,0)
  # Confirm that this ROM accepts the intended volatile region before any write.
  pointer(handle,START,8)
  chip_inspect.idle(handle)
  if len(handle.controlRead(0xa1,2,2,0,8,timeout=2000))!=8:
    raise ValueError('ROM SRAM read refused')
  for offset in range(0,len(blob),1024):
    chunk=blob[offset:offset+1024]
    pointer(handle,START+offset,len(chunk))
    handle.controlWrite(0x21,1,2,0,chunk,timeout=2000)
    wait_download(handle)
    chip_inspect.idle(handle)
    readback=bytes(handle.controlRead(0xa1,2,2,0,len(chunk),timeout=2000))
    if readback!=chunk:
      raise ValueError('SRAM readback mismatch; no jump')
  pointer(handle,START,8)
  # Empty manifest executes the SRAM vector; no flash payload was supplied.
  handle.controlWrite(0x21,1,2,0,b'',timeout=2000)
  try:
    handle.controlRead(0xa1,3,0,0,6,timeout=2000)
  except Exception as error:
    import usb1
    if not isinstance(error,(usb1.USBErrorNoDevice,usb1.USBErrorIO)):
      raise


def main():
  import usb1
  parser=argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--build',type=Path,required=True)
  args=parser.parse_args()
  report=json.loads(bounded_read(args.build/'report.json',4096))
  blob=bounded_read(args.build/'NEVER_FLASH-probe.bin',END-START)
  if (report.get('sha256')!=hashlib.sha256(blob).hexdigest() or report.get('size')!=len(blob) or
      report.get('load_address')!=START or report.get('flash_writes') is not False):
    parser.error('probe build report mismatch')
  with usb1.USBContext() as context:
    matches=[]
    for device in context.getDeviceList(skip_on_error=False):
      if (device.getVendorID(),device.getProductID())==(0x0483,0xdf11):
        with closing(device.open()) as handle:
          if handle.getASCIIStringDescriptor(device.getSerialNumberDescriptor())==chip_inspect.SERIAL:
            matches.append(device)
    if len(matches)!=1:
      parser.error('exact labeled Panda ROM not present')
    with closing(matches[0].open()) as handle:
      handle.claimInterface(0)
      load(handle,blob)
  print('Verified SRAM-only probe loaded and jump requested. Flash and option bytes not written.')


if __name__=='__main__':
  main()
