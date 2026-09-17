"""Dangerous, explicit DEBUG-only USB bench memory access. Never use in a car.

No automatic memory dumps, scanning, retries, secret reads or device-mode entry.
Reads can have peripheral side effects; writes can destroy state or the device.
"""
import argparse
from contextlib import closing
import resource
import struct

SERIAL='370022000651363038363036'
VERSION=b'voltgw-DEBUG-MEM-v1'


def request(address,value=None):
  if not isinstance(address,int) or not 0<=address<=0xfffffffc or address&3:
    raise ValueError('aligned 32-bit address required')
  if value is not None and (not isinstance(value,int) or not 0<=value<=0xffffffff):
    raise ValueError('32-bit value required')
  return struct.pack('<4sIII',b'VGM1',int(value is not None),address,0 if value is None else value)


def exchange(handle,address,value=None):
  packet=request(address,value)
  if bytes(handle.controlRead(0xc0,0xd6,0,0,64,timeout=1000))!=VERSION:
    raise ValueError('not the explicit DEBUG memory image; no memory command sent')
  if handle.bulkWrite(2,packet,timeout=1000)!=16:
    raise ValueError('short debug request; not retried')
  response=bytes(handle.bulkRead(0x81,64,timeout=1000))
  if len(response)!=16:
    raise ValueError('short debug response; not retried')
  magic,status,echo,result=struct.unpack('<4sIII',response)
  if magic!=b'VGR1' or status or echo!=address or (value is not None and result!=value):
    raise ValueError('invalid debug response; not retried')
  return result


def main():
  resource.setrlimit(resource.RLIMIT_CORE,(0,0))
  parser=argparse.ArgumentParser(description=__doc__)
  parser.add_argument('address',type=lambda v:int(v,0))
  parser.add_argument('--write',type=lambda v:int(v,0))
  parser.add_argument('--usb-only-bench',action='store_true',required=True)
  args=parser.parse_args()
  request(args.address,args.write)
  import usb1
  with usb1.USBContext() as ctx:
    matches=[]
    for d in ctx.getDeviceList():
      if (d.getVendorID(),d.getProductID())==(0xbbaa,0xddcc):
        with closing(d.open()) as h:
          if h.getASCIIStringDescriptor(d.getSerialNumberDescriptor())==SERIAL:
            matches.append(d)
    if len(matches)!=1:
      raise RuntimeError('exact labeled DEBUG Panda not present')
    with closing(matches[0].open()) as h:
      h.claimInterface(0)
      try:
        result=exchange(h,args.address,args.write)
        print(f'{args.address:#010x}: {result:#010x}')
      finally:
        h.releaseInterface(0)


if __name__=='__main__':
  main()
