"""Bounded passive SWCAN HVAC capture; contains no vehicle TX operation."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time

from openpilot.tools.volt_gateway.device_cli import Device, credentials, pages
from openpilot.tools.volt_gateway.usb_transport import UsbTransport


def main():
  parser=argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--pairing',type=Path,required=True)
  parser.add_argument('--output',type=Path,required=True)
  parser.add_argument('--seconds',type=int,default=180)
  args=parser.parse_args()
  if not 5<=args.seconds<=600:
    parser.error('seconds must be 5..600')
  keys=credentials(args.pairing)
  # Capturing hypothetical addresses is read-only, not permission to transmit.
  addresses=[0x10ad6080,0x10b02099,0x10814099,0x10734099,0x106d4099]
  addresses += [0x10000000+(0x22d<<13)+source for source in (0x80,0x81,0x99,0x97,0x40)]
  addresses += [0x10000000+(family<<13)+source for family,source in
                ((0x56a,0x80),(0x56c,0x60),(0x56f,0x80),(0x58e,0x80),(0x581,0x80))]
  total=0
  with args.output.open('x') as log, UsbTransport(keys['device'].hex()) as transport:
    args.output.chmod(0o600)
    def emit(record):
      nonlocal total
      raw=json.dumps(record)+'\n'
      total+=len(raw)
      if total>2_000_000:
        raise RuntimeError('bounded capture size reached')
      log.write(raw)
      log.flush()
    device=Device(transport,keys)
    try:
      device.command(1)
      device.command(5,b'\x03'+args.seconds.to_bytes(2,'big'))
      for handle,address in enumerate(addresses):
        device.command(8,bytes([handle,3,1])+address.to_bytes(4,'big')+(100).to_bytes(2,'big'))
      emit({'kind':'start','host_time':time.time(),'seconds':args.seconds,'addresses':[hex(a) for a in addresses]})
      print('PASSIVE CAPTURE READY',flush=True)
      end=time.monotonic()+args.seconds
      heartbeat=0
      while time.monotonic()<end:
        if time.monotonic()>=heartbeat:
          device.command(2)
          heartbeat=time.monotonic()+3
        try:
          transport.receive(min(end,time.monotonic()+.2))
        except TimeoutError:
          pass
        while transport.observations:
          record=asdict(transport.observations.popleft())
          record.update(data=record['data'].hex(),host_time=time.time(),kind='frame')
          emit(record)
          if record['address'] not in (0x10814099,0x10734099,0x106d4099):
            print(hex(record['address']),record['data'],flush=True)
      device.command(6,b'\x03')
      for record in pages(device,7,3):
        emit({'kind':'id_stat',**record})
      emit({'kind':'end','host_time':time.time(),'host_observation_drops':transport.observation_drops})
    finally:
      device.command(10)
      device.close()


if __name__=='__main__':
  main()
