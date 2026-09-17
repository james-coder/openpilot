"""Local USB recovery + exact-readback flash, with OBD left connected, parked.

Never uses the comma's signing key or sends vehicle CAN. Requires installed
software-recovery capability; older firmware needs its final cold-loader install.
ROM DFU is a physical-USB maintenance boundary, not the signed CAN update API.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import resource
import subprocess
import sys
import time

from openpilot.tools.volt_gateway.device_cli import Device, credentials, indicators
from openpilot.tools.volt_gateway.object_trial_flash import validate
from openpilot.tools.volt_gateway.operator import bounded_read
from openpilot.tools.volt_gateway.usb_transport import UsbTransport

SERIAL='370022000651363038363036'
ROM_SERIAL='365236793036'
USBIPD=Path('/mnt/c/Program Files/usbipd-win/usbipd.exe')


def request_recovery(device):
  info=device.command(1)
  if len(info)!=46 or info[0]!=1 or not int.from_bytes(info[2:6],'big')&128:
    raise RuntimeError('Installed firmware lacks software recovery; no reset or flash requested')
  device.command(20)  # authenticated local USB only; no update/VIN gate


def usb_present(rom):
  import usb1
  with usb1.USBContext() as context:
    for dev in context.getDeviceList(skip_on_error=True):
      if (dev.getVendorID(),dev.getProductID())!=((0x0483,0xdf11) if rom else (0xbbaa,0xddcc)):
        continue
      try:
        with closing(dev.open()) as handle:
          if handle.getASCIIStringDescriptor(dev.getSerialNumberDescriptor())!=(ROM_SERIAL if rom else SERIAL):
            continue
          if rom or bytes(handle.controlRead(0xc0,0xd6,0,0,64,timeout=1000))==b'voltgw-v1':
            return True
      except usb1.USBError:
        pass
  return False


def wait_usb(rom,busid,timeout=60):
  deadline=time.monotonic()+timeout
  instance=('USB\\VID_0483&PID_DF11\\'+ROM_SERIAL if rom else 'USB\\VID_BBAA&PID_DDCC\\'+SERIAL)
  while time.monotonic()<deadline:
    if usb_present(rom):
      return
    if USBIPD.is_file():
      result=subprocess.run([str(USBIPD),'state'],capture_output=True,text=True,timeout=10,check=True)
      devices=json.loads(result.stdout)['Devices']
      target=[d for d in devices if d.get('BusId')==busid and d.get('InstanceId','').upper()==instance]
      if len(target)==1:
        # Enumeration may briefly expose the cold loader before the app, or
        # detach WSL during personality changes. Retry only this exact device.
        subprocess.run([str(USBIPD),'attach','--wsl','--busid',busid],capture_output=True,timeout=15,check=False)
    time.sleep(.25)
  raise TimeoutError('Exact '+('ROM DFU' if rom else 'application')+' did not enumerate; no guessed recovery commands sent')


def main():
  resource.setrlimit(resource.RLIMIT_CORE,(0,0))
  parser=argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--pairing',type=Path,required=True)
  parser.add_argument('--prepared',type=Path,required=True)
  parser.add_argument('--before',type=Path,required=True)
  parser.add_argument('--evidence',type=Path,required=True)
  parser.add_argument('--parked',action='store_true',required=True,help='confirm vehicle is parked for this maintenance operation')
  parser.add_argument('--busid',default='2-2',help='WSL usbipd physical port; exact serial still enforced')
  args=parser.parse_args()
  keys=credentials(args.pairing)
  if keys['device'].hex()!=SERIAL:
    parser.error('This installer targets the labeled White Panda only')
  validate(bounded_read(args.before,1048576,private=True),
           bounded_read(args.prepared/'factory-flash.bin',1048576,private=True),
           json.loads(bounded_read(args.prepared/'manifest.json',4096,private=True)))
  if args.evidence.exists():
    parser.error('fresh evidence directory required before entering recovery')
  with UsbTransport(SERIAL) as transport:
    device=Device(transport,keys)
    try:
      request_recovery(device)
    except Exception:
      device.close()
      raise
    # No session-close after an accepted reset; USB is about to disconnect.
  print('Software recovery requested; waiting for ROM DFU.',flush=True)
  wait_usb(True,args.busid)
  subprocess.run([sys.executable,'-m','tools.volt_gateway.object_trial_flash',
                  '--prepared',str(args.prepared),'--before',str(args.before),'--evidence',str(args.evidence),
                  '--parked-obd-connected'],check=True)
  subprocess.run([sys.executable,'-m','tools.volt_gateway.dfu_leave'],check=True)
  wait_usb(False,args.busid)
  with UsbTransport(SERIAL) as transport:
    device=Device(transport,keys)
    try:
      info=device.command(1)
      if len(info)!=46 or not int.from_bytes(info[2:6],'big')&128:
        raise RuntimeError('Flashed application did not retain software recovery capability')
      print(json.dumps({'flashed_and_readback_verified':True,'running_build':info[6:38].hex(),
                        'indicators':indicators(device.command(15))}),flush=True)
    finally:
      device.close()


if __name__=='__main__':
  main()
