"""Real C recovery dispatcher + real C crypto/updater + modeled inactive flash."""
import ctypes as C
from dataclasses import replace
import hashlib
from pathlib import Path
import subprocess

import pytest

from openpilot.tools.volt_gateway import test_target_crypto as crypto_tests
from openpilot.tools.volt_gateway.test_authority import bundle as bundle, Slot, LiveEnvironment, READY
from openpilot.tools.volt_gateway.authority import Challenge, Phase
from openpilot.tools.volt_gateway.native_harness import NativeAuthority, NativeLibraries, VERIFY, HASH
from openpilot.tools.volt_gateway.native_update import NativeUpdateEngine, IO
from openpilot.tools.volt_gateway.operator import authorize_image
from openpilot.tools.volt_gateway.protocol import ProtocolError, seal
from openpilot.tools.volt_gateway.recovery_client import RecoveryClient, HELLO_REQUEST

archive=crypto_tests.archive
native=crypto_tests.native
HMAC=C.CFUNCTYPE(C.c_bool,C.c_void_p,C.c_void_p,C.c_size_t,C.c_void_p)
HASH_BOOL=C.CFUNCTYPE(C.c_bool,C.c_void_p,C.c_size_t,C.c_void_p)
NONCE=C.CFUNCTYPE(C.c_bool,C.c_void_p,C.c_void_p)


class Provision(C.Structure):
  _fields_=[('pairing',C.c_uint8*32),('device',C.c_uint8*12),('layout',C.c_uint8*32),
             ('policy',C.c_uint8*32),('build',C.c_uint8*32)]


@pytest.fixture(scope='module')
def service_library(tmp_path_factory):
  own=Path(__file__).parent/'firmware'
  out=tmp_path_factory.mktemp('recovery-service')/'service.so'
  adapter=out.with_suffix('.c')
  adapter.write_text('#include "recovery_service.h"\n'+
    'bool vgw_test_update_led(vgw_recovery_service *s,uint8_t out[3]) { return vgw_update_led(&s->update,s->previous,out); }\n')
  names=('authority','update','recovery_service','recovery_runtime','white_runtime','white_startup',
         'white_clock','white_rng','white_can','white_board','white_watchdog','status_led','observe',
         'recovery_link','recovery_transport','application')
  subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fanalyzer','-shared','-fPIC',
                  '-I',str(own),str(adapter),*(str(own/(n+'.c')) for n in names),'-o',str(out)],check=True)
  return out


class Recovery:
  pairing=b'p'*32  # disposable fixture only, never real provisioning
  policy=hashlib.sha256(b'bench-test-only policy').digest()

  def __init__(self,service_library,native,bundle,slot=None,transport=None,before_open=None):
    self.key,self.image,self.manifest=bundle
    m=self.manifest.manifest
    authority=NativeAuthority(NativeLibraries(service_library,Path(native._name)),
      self.key.public_key().export_key(format='DER'),m.device,m.layout,Phase.PROGRAM)
    self.env=LiveEnvironment()
    self.slot=slot or Slot()
    self.engine=NativeUpdateEngine(authority,self.slot,self.env)  # owns real IO bindings
    self.lib=lib=authority.lib
    lib.vgw_recovery_service_size.restype=C.c_size_t
    self.state=C.create_string_buffer(lib.vgw_recovery_service_size())
    lib.vgw_recovery_service_init.argtypes=[C.c_void_p,C.POINTER(Provision),C.POINTER(IO),VERIFY,HASH,HMAC,HASH_BOOL,NONCE,C.c_void_p,C.c_void_p,C.c_uint]
    lib.vgw_recovery_service_init.restype=C.c_bool
    lib.vgw_recovery_service_request.argtypes=[C.c_void_p,C.c_void_p,C.c_size_t,C.c_uint64,C.c_void_p,C.POINTER(C.c_size_t)]
    lib.vgw_recovery_service_request.restype=C.c_bool
    self.nonces=0
    self.entropy_ok=True
    def nonce(_,out):
      self.nonces+=1
      C.memmove(out,hashlib.sha256(str(self.nonces).encode()).digest(),32)
      return self.entropy_ok
    self.callbacks=(authority._verify,authority._sha,HMAC(('vgw_crypto_hmac',native)),
                    HASH_BOOL(('vgw_crypto_sha256',native)),NONCE(nonce))
    fields=(self.pairing,m.device,m.layout,self.policy,m.build)
    self.provision=Provision(*(type_ .from_buffer_copy(value) for (_,type_),value in zip(Provision._fields_,fields,strict=True)))
    assert lib.vgw_recovery_service_init(self.state,C.byref(self.provision),C.byref(self.engine.io),
                                        *self.callbacks,authority.crypto_state,None,getattr(self.slot,'slot',1))
    self.now=0
    self.transport=transport
    if before_open:
      before_open(self)
    self.hello=self.wire(HELLO_REQUEST)
    self.client=RecoveryClient(self.pairing,self.hello,device=m.device,layout=m.layout,policy=self.policy,host_nonce=b'n'*32)
    self.open_reply=self.wire(self.client.open_request)
    self.client.accept_open(self.open_reply)

  def wire(self,pdu,now=None):
    self.now=self.now+10 if now is None else now
    self.env.now=self.now
    if self.transport is not None:
      return self.transport(self,pdu)
    out=C.create_string_buffer(512)
    size=C.c_size_t(123)
    ok=self.lib.vgw_recovery_service_request(self.state,pdu,len(pdu),self.now,out,C.byref(size))
    assert ok or size.value==0
    return out.raw[:size.value] if ok else None

  def command(self,opcode,payload=b''):
    pdu=self.client.request(opcode,payload)
    reply=self.wire(pdu)
    assert reply is not None
    return self.client.accept(reply)

  def authorize(self,lease=60000):
    result=self.command(0x80,self.manifest.pack())
    assert result[0]==0
    self.challenge=Challenge.unpack(result[1:])
    self.token=authorize_image(self.key,self.manifest,self.challenge,lease).pack()
    assert self.command(0x81,self.token)==b'\0'


def test_signed_update_and_identical_retry_do_not_repeat_flash(service_library,native,bundle):
  r=Recovery(service_library,native,bundle)
  assert r.command(0x88)==b'\0'+r.slot.capacity.to_bytes(4,'big')+b'\1'
  r.authorize()
  request=r.client.request(0x82)
  reply=r.wire(request)
  before=r.slot.erases
  assert r.wire(request)==reply and r.slot.erases==before
  assert r.client.accept(reply)==b'\0'
  for offset in range(0,len(r.image),256):
    request=r.client.request(0x83,offset.to_bytes(4,'big')+r.image[offset:offset+256])
    reply=r.wire(request)
    writes=r.slot.writes
    assert r.wire(request)==reply and r.slot.writes==writes
    assert r.client.accept(reply)==b'\0'
  assert r.command(0x84)==b'\0'
  assert r.slot.trial is not None
  assert r.command(0x85)[1]==2
  assert r.command(0x84)==b'\0'  # status-preserving repeat, no second commit
  assert r.command(0x86)==b'\1'  # cannot undo a persisted trial marker
  assert r.command(0x83,b'\0'*5)==b'\1'
  assert r.command(0x85)[1]==2


def test_pairing_key_and_signed_image_do_not_grant_program(service_library,native,bundle):
  r=Recovery(service_library,native,bundle)
  assert r.command(0x80,r.manifest.pack())[0]==0
  assert r.command(0x82)==b'\1'
  assert r.slot.erases==r.slot.writes==0


@pytest.mark.parametrize('mutation',['tag','session','sequence','major','minor','length','opcode'])
def test_unauthorized_or_incompatible_command_has_no_effect(service_library,native,bundle,mutation):
  r=Recovery(service_library,native,bundle)
  r.authorize()
  request=r.client.request(0x82)
  if mutation=='tag':
    request=request[:-1]+bytes([request[-1]^1])
  elif mutation=='sequence':
    request=seal(r.client.host_key,r.client.session,50,0,0x82,b'')
  elif mutation=='session':
    request=seal(r.client.host_key,b'x'*8,r.client.sequence,0,0x82,b'')
  elif mutation=='minor':
    request=seal(r.client.host_key,r.client.session,r.client.sequence,0,0x82,b'',minor=1)
  else:
    import hmac
    body=bytearray(request[:-16])
    body[{'major':4,'length':31,'opcode':7}[mutation]]=255
    request=bytes(body)+hmac.digest(r.client.host_key,body,'sha256')[:16]
  reply=r.wire(request)
  assert reply is None or reply[32]==1
  assert r.slot.erases==r.slot.writes==0


def test_open_replay_does_not_reset_sequence_or_repeat_erase(service_library,native,bundle):
  r=Recovery(service_library,native,bundle)
  r.authorize()
  old=r.client.request(0x82)
  reply=r.wire(old)
  assert r.client.accept(reply)==b'\0'
  assert r.command(0x85)[0]==0
  erases=r.slot.erases
  assert r.wire(r.client.open_request,2000)==r.open_reply
  assert r.wire(old) is None and r.slot.erases==erases


@pytest.mark.parametrize('condition',['stationary','offroad','power_stable','vehicle_awake','authenticated','compatible','errors'])
def test_unsafe_environment_never_erases(service_library,native,bundle,condition):
  r=Recovery(service_library,native,bundle)
  r.authorize()
  r.env.conditions=replace(READY,**{condition:condition=='errors'})
  assert r.command(0x82)==b'\1' and r.slot.erases==0


def test_close_rotates_challenge_old_open_cannot_reenter(service_library,native,bundle):
  r=Recovery(service_library,native,bundle)
  old=r.client.open_request
  assert r.command(0x87)==b'\0'
  hello=r.wire(HELLO_REQUEST,2000)
  assert hello!=r.hello and r.wire(old) is None
  m=r.manifest.manifest
  new=RecoveryClient(r.pairing,hello,device=m.device,layout=m.layout,policy=r.policy,host_nonce=b'n'*32)
  new.accept_open(r.wire(new.open_request,4000))
  assert r.wire(r.client.request(0x85)) is None


@pytest.mark.parametrize('expiry',[False,True])
@pytest.mark.parametrize('updating',[False,True])
def test_normal_session_end_is_not_an_update_fault(service_library,native,bundle,expiry,updating):
  r=Recovery(service_library,native,bundle)
  r.lib.vgw_test_update_led.argtypes=[C.c_void_p,C.c_void_p]
  r.lib.vgw_test_update_led.restype=C.c_bool
  led=C.create_string_buffer(3)
  assert not r.lib.vgw_test_update_led(r.state,led)
  if updating:
    r.authorize()
    assert r.command(0x82)==b'\0'
  if expiry:
    assert r.wire(r.client.request(0x85),now=r.now+20000) is None
  else:
    assert r.command(0x87)==b'\0'
  assert r.lib.vgw_test_update_led(r.state,led)==updating
  if updating:
    assert led.raw==bytes([5,255,9])  # a real interrupted transfer still faults
  else:
    assert r.slot.erases==r.slot.writes==0


def test_expiry_and_entropy_failure_fail_closed(service_library,native,bundle):
  r=Recovery(service_library,native,bundle)
  r.authorize()
  assert r.wire(r.client.request(0x82),3600020) is None
  assert r.slot.erases==0
  r.entropy_ok=False
  assert r.wire(HELLO_REQUEST) is None


def test_host_rejects_tampered_or_cross_transaction_reply(service_library,native,bundle):
  r=Recovery(service_library,native,bundle)
  request=r.client.request(0x85)
  reply=r.wire(request)
  with pytest.raises(ProtocolError):
    r.client.accept(reply[:-1]+bytes([reply[-1]^1]))
  assert r.client.retry()==request
  assert r.client.accept(reply)[0]==0


def test_truncation_and_each_corrupted_byte_no_flash_side_effect(service_library,native,bundle):
  r=Recovery(service_library,native,bundle)
  r.authorize()
  valid=r.client.request(0x82)
  for size in range(len(valid)):
    assert r.wire(valid[:size]) is None
  for index in range(len(valid)):
    corrupt=bytearray(valid)
    corrupt[index]^=1
    assert r.wire(bytes(corrupt)) is None
  assert r.slot.erases==r.slot.writes==0
  assert r.client.accept(r.wire(valid))==b'\0'


def test_session_expiry_inside_erase_stops_remaining_mutations(service_library,native,bundle,monkeypatch):
  r=Recovery(service_library,native,bundle)
  r.authorize(lease=3600000)
  original=r.slot.erase
  def expire_after_one(*args):
    original(*args)
    r.env.now=3600020  # session expired, operator grant not yet expired
  monkeypatch.setattr(r.slot,'erase',expire_after_one)
  assert r.command(0x82)==b'\1'
  assert r.slot.erases==1 and r.slot.writes==0


def test_entropy_failure_before_operator_challenge_disables_service(service_library,native,bundle):
  r=Recovery(service_library,native,bundle)
  r.entropy_ok=False
  assert r.wire(r.client.request(0x80,r.manifest.pack())) is None
  assert r.wire(HELLO_REQUEST,2000) is None
  assert r.slot.erases==r.slot.writes==0
