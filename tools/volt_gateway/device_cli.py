"""Paired local USB gateway CLI. No raw CAN transmit or firmware signing API.

Pairing bundle is a private, owner-only JSON file: device (hex12), pairing
(hex32), layout (hex32), policy (hex32). It is never printed or logged.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import resource
import struct
import time

from openpilot.tools.volt_gateway.host_files import bounded_read
from openpilot.tools.volt_gateway.protocol import ProtocolError
from openpilot.tools.volt_gateway.recovery_client import RecoveryClient, HELLO_REQUEST
from openpilot.tools.volt_gateway.usb_transport import UsbTransport

LED_STATES=('boot','running','trial','recovery','update','fault','update_wait','update_verify',
            'update_commit','update_ready','update_erase','probe','rx_degraded')
LED_ERRORS=('none','no_image','image_policy','storage','configuration','can','watchdog','crypto','internal','update_aborted')
PEER_STATES=('disabled','local_usb_owner','awaiting_authenticated_peer','authenticated','stale')
RECOVERY_PHASES=('unavailable','requested','intent_written','loader_entered',
                 'intent_consumed','usb_client_ready','phys_quiesced','rom_branch')


def recovery_diagnostics(data):
  if len(data)!=16:
    raise ProtocolError('recovery diagnostics unsupported or malformed')
  version,phase,reset,cfsr=struct.unpack('>4I',data)
  if version!=1 or phase>=len(RECOVERY_PHASES):
    raise ProtocolError('incompatible recovery diagnostics')
  return {'phase':RECOVERY_PHASES[phase],'phase_code':phase,'current_reset_flags':hex(reset),
          'current_cfsr':hex(cfsr),'note':'persistent last-attempt stage; registers are current, not a saved fault dump'}


def hvac_status(data):
  if not ((len(data)==4 and data[0]==1) or (len(data)==21 and data[0] in (2,3))) or data[1]>5:
    raise ProtocolError('invalid HVAC trial status')
  result={'state':('idle','press_pending','release_wait','release_pending','done','fault')[data[1]],
          'attempts':data[2],'interlock_ready':bool(data[3]),
          'note':'CAN completion is not proof of recirculation actuation'}
  if data[0] in (2,3):
    result.update(seen_mask=data[4],safe_mask=data[5],sampler_ready=bool(data[6]),power_valid=bool(data[7]),
                  stable=bool(data[8]),tx_inhibited=bool(data[9]),session_enabled=bool(data[10]),
                  voltage_mv=int.from_bytes(data[11:15],'big'),
                  input_ages_ms=list(struct.unpack('>3H',data[15:21])))
  return result


def indicators(data):
  if (len(data)!=7 or data[0]!=1 or data[1]>=len(LED_STATES) or data[2] not in (0,1,255) or
      data[3]>=len(LED_ERRORS) or data[4]>7 or data[5]>1 or data[6]>=len(PEER_STATES)):
    raise ProtocolError('invalid indicator snapshot')
  return {'supported':True,'state':LED_STATES[data[1]],'slot':{0:'A',1:'B',255:None}[data[2]],
          'error':LED_ERRORS[data[3]],'error_code':data[3],'rgb_sample':data[4],
          'boot_color_test_active':bool(data[5]),'can_peer':PEER_STATES[data[6]]}


class Device:
  def __init__(self,transport,credentials):
    self.transport=transport
    self.client=RecoveryClient(credentials['pairing'],transport.exchange(HELLO_REQUEST),
      device=credentials['device'],layout=credentials['layout'],policy=credentials['policy'])
    self.client.accept_open(transport.exchange(self.client.open_request))

  def command(self,opcode,payload=b''):
    packet=self.client.request(opcode,payload)
    # Repeat only exact authenticated bytes, never manufacture a fresh sequence
    # to retry an operation that may already have committed persistent state.
    for attempt in range(2):
      try:
        response=self.transport.exchange(packet,timeout=60 if opcode==0x82 else 10)
        result=self.client.accept(response)
        if result[0]:
          raise ProtocolError('gateway rejected operation')
        return result[1:]
      except TimeoutError:
        if attempt:
          raise
        packet=self.client.retry()
    raise AssertionError('unreachable bounded retry')

  def close(self):
    if self.client.pending is None:
      self.command(0x87)

  def observe(self,bus,seconds,*,capture=False):
    if not 1<=seconds<=3600 or bus not in (0,1,2,3):
      raise ValueError('bounded bus/observation duration required')
    self.command(11 if capture else 5,bytes([1<<bus if capture else bus])+seconds.to_bytes(2,'big'))
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
      time.sleep(max(0,min(4,deadline-time.monotonic())))
      self.command(2)  # authenticated liveness, not vehicle diagnostics
    self.command(12,b'') if capture else self.command(6,bytes([bus]))


def credentials(path):
  raw=json.loads(bounded_read(path,2048,private=True))
  if not isinstance(raw,dict) or set(raw)!={'device','pairing','layout','policy'}:
    raise ValueError('invalid pairing bundle schema')
  result={}
  for name,length in [('device',12),('pairing',32),('layout',32),('policy',32)]:
    value=raw[name]
    if not isinstance(value,str) or len(value)!=length*2:
      raise ValueError('invalid pairing field length')
    decoded=bytes.fromhex(value)
    if not any(decoded):
      raise ValueError('empty pairing field')
    result[name]=decoded
  return result


def decode_frame(data):
  if len(data)!=27:
    raise ProtocolError('frame record length')
  stamp,sequence,address,bus,flags,dlc=struct.unpack('>QIIBBB',data[:19])
  if bus>3 or flags>3 or dlc>8 or address>(0x1fffffff if flags&1 else 0x7ff):
    raise ProtocolError('invalid frame record')
  return {'timestamp_us':stamp,'sequence':sequence,'address':hex(address),'bus':bus,
          'flags':flags,'dlc':dlc,'payload':data[19:19+dlc].hex()}


def pages(device,op,bus=None):
  cursor=0
  maximum=256 if op==7 else 512
  for _ in range(maximum+1):
    reply=device.command(op,(bytes([bus]) if op==7 else b'')+cursor.to_bytes(2,'big'))
    size=58 if op==7 else 27
    if len(reply)<3:
      raise ProtocolError('short page')
    next_cursor,count=int.from_bytes(reply[:2],'big'),reply[2]
    if (len(reply)!=3+count*size or count>(2 if op==7 else 4) or
        not cursor<=next_cursor<=maximum or (count and next_cursor<=cursor)):
      raise ProtocolError('invalid pagination cursor/count')
    for index in range(count):
      record=reply[3+index*size:3+(index+1)*size]
      result=decode_frame(record[:27])
      if op==7:
        first,frames,dlc_mask,changed=struct.unpack('>QIHB',record[27:42])
        duration=result['timestamp_us']-first
        if duration<0 or not frames:
          raise ProtocolError('invalid ID statistics')
        result.update(first_us=first,count=frames,dlc_mask=dlc_mask,changed_byte_mask=changed,
                      changes=list(struct.unpack('>8H',record[42:])),
                      approximate_hz=(frames-1)*1e6/duration if duration else None)
      yield result
    if not count or next_cursor==maximum:
      return
    cursor=next_cursor
  raise ProtocolError('pagination bound exceeded')


def main():
  resource.setrlimit(resource.RLIMIT_CORE,(0,0))
  parser=argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--pairing',type=Path,required=True)
  parser.add_argument('command',choices=['info','status','indicators','buses','utilization','rx-health','clear-ids','observe','ids','capture','rules','subscribe',
                                        'hvac-trial-status','hvac-button-trial','parked-gateway-reboot','usb-recovery','recovery-diagnostics'])
  parser.add_argument('--bus',type=int,choices=[0,1,2,3],default=3,help='HSCAN CAN1=0, CAN2=1, CAN3=2; SWCAN=3 regardless of mux')
  parser.add_argument('--seconds',type=int,default=30)
  parser.add_argument('--id',type=lambda x:int(x,0))
  parser.add_argument('--extended',action='store_true')
  parser.add_argument('--max-hz',type=int,default=10)
  args=parser.parse_args()
  if not 1<=args.seconds<=3600 or not 1<=args.max_hz<=1000:
    parser.error('duration 1..3600 seconds; max-hz 1..1000')
  if args.command=='subscribe' and (args.id is None or not 0<=args.id<=(0x1fffffff if args.extended else 0x7ff)):
    parser.error('valid --id required')
  keys=credentials(args.pairing)
  with UsbTransport(keys['device'].hex()) as transport:
    if args.command=='recovery-diagnostics':
      print(json.dumps(recovery_diagnostics(bytes(transport.handle.controlRead(0xc0,0xd8,0,0,16,timeout=1000)))))
      return
    if args.command=='indicators':
      print(json.dumps(indicators(bytes(transport.handle.controlRead(0xc0,0xd7,0,0,7,timeout=1000)))))
      return  # Do not open a session just to inspect its closed/expired state.
    device=Device(transport,keys)
    reboot_requested=False
    try:
      if args.command in ('info','buses'):
        data=device.command(1)
        if len(data)!=46:
          raise ProtocolError('info length')
        print(json.dumps({'version':[data[0],data[1]],'capabilities':int.from_bytes(data[2:6],'big'),
          'build':data[6:38].hex(),'swcan_controller':data[38],'hscan_mask':data[39],'backhaul_controller':data[40],
          'id_capacity':int.from_bytes(data[41:43],'big'),'capture_capacity':int.from_bytes(data[43:45],'big'),
          'subscription_capacity':data[45]}))
      elif args.command=='status':
        data=device.command(2)
        if len(data)!=32:
          raise ProtocolError('status length')
        names=['uptime_ms','reset_flags','id_drops','capture_drops','invalid','telemetry_drops','telemetry_completed']
        result=dict(zip(names,struct.unpack('>Q6I',data),strict=True))
        info=device.command(1)
        if len(info)!=46 or info[0]!=1:
          raise ProtocolError('incompatible gateway information')
        result['indicators']=indicators(device.command(15)) if int.from_bytes(info[2:6],'big')&16 else {'supported':False}
        print(json.dumps(result))
      elif args.command=='rx-health':
        info=device.command(1)
        if len(info)!=46 or info[0]!=1 or not int.from_bytes(info[2:6],'big')&32:
          raise ProtocolError('RX staging diagnostics unsupported by this firmware')
        data=device.command(16,bytes([args.bus]))
        if len(data)!=16:
          raise ProtocolError('RX staging diagnostics length')
        print(json.dumps(dict(zip(('software_drops','irq_calls','queue_peak','max_queue_age_ms'),struct.unpack('>4I',data),strict=True))))
      elif args.command=='utilization':
        data=device.command(3,bytes([args.bus]))
        if len(data)!=56:
          raise ProtocolError('bus status length')
        names=['received','malformed','overflow','transmitted','arbitration_lost','tx_errors','esr','peak_permille',
               'short_start_ms','short_bits','medium_start_ms','medium_bits','long_start_ms','long_bits']
        print(json.dumps(dict(zip(names,struct.unpack('>14I',data),strict=True))))
      elif args.command=='clear-ids':
        device.command(4,bytes([args.bus]))
        print('ID table cleared for selected physical bus.')
      elif args.command in ('observe','ids','capture'):
        if args.command!='ids':
          device.observe(args.bus,args.seconds,capture=args.command=='capture')
        for record in pages(device,13 if args.command=='capture' else 7,args.bus):
          print(json.dumps(record))
      elif args.command=='rules':
        policy=device.command(14)
        if policy==b'\1':
          print('Experimental USB-only parked HVAC button pair; no arbitrary vehicle TX.')
        elif policy==b'\0':
          print('Vehicle-side TX policy: no permitted messages.')
        else:
          raise ProtocolError('unexpected write policy')
      elif args.command=='usb-recovery':
        info=device.command(1)
        if len(info)!=46 or info[0]!=1 or not int.from_bytes(info[2:6],'big')&128:
          raise ProtocolError('software USB recovery not supported by installed firmware')
        device.command(20)
        reboot_requested=True
        print('Authenticated USB recovery requested. CAN transceivers are quiesced on reset; no flash writes requested.')
      elif args.command in ('hvac-trial-status','hvac-button-trial','parked-gateway-reboot'):
        info=device.command(1)
        if len(info)!=46 or info[0]!=1 or not int.from_bytes(info[2:6],'big')&64:
          raise ProtocolError('experimental HVAC capability absent')
        status=device.command(18)
        if args.command=='parked-gateway-reboot':
          if len(status)!=21 or status[0] not in (2,3):
            raise ProtocolError('parked reboot not supported by this firmware')
          device.command(19)
          reboot_requested=True
          print('Authenticated secondary-gateway reboot requested; fresh Park/RUN/zero-speed rechecked before reset.')
          return
        if args.command=='hvac-button-trial':
          # Explicit CLI operation only; never auto-run on connect or retry with
          # a new sequence. The firmware independently enforces all interlocks.
          if not hvac_status(status)['interlock_ready']:
            raise ProtocolError('physical safety/session interlock not ready; no TX requested')
          device.command(17)
          deadline=time.monotonic()+4
          while time.monotonic()<deadline:
            time.sleep(0.1)
            status=device.command(18)
            hvac_status(status)
            if status[1] in (4,5):
              break
        print(json.dumps(hvac_status(status)))
      else:
        device.command(8,bytes([0,args.bus,int(args.extended)])+args.id.to_bytes(4,'big')+args.max_hz.to_bytes(2,'big'))
        end=time.monotonic()+args.seconds
        heartbeat=0
        while time.monotonic()<end:
          if time.monotonic()>=heartbeat:
            device.command(2)
            heartbeat=time.monotonic()+4
          try:
            transport.receive(min(end,time.monotonic()+.25))
          except TimeoutError:
            pass
          while transport.observations:
            observation=asdict(transport.observations.popleft())
            observation['data']=observation['data'].hex()
            observation['trust']='unauthenticated observation; never an actuation input'
            print(json.dumps(observation),flush=True)
        device.command(10)
    finally:
      if not reboot_requested:
        device.close()


if __name__=='__main__':
  main()
