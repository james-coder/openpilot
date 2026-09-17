"""Recovery wire codec, no CAN IO or private firmware-signing keys.

The paired transport cannot authorize firmware by itself. PROGRAM additionally
requires a signature produced interactively by the off-vehicle operator tool.
"""
import hashlib
import hmac
import secrets

from openpilot.tools.volt_gateway.protocol import HEADER, ProtocolError, seal, session_key

HELLO_REQUEST = b'VGR1\0'
OPEN_DOMAIN = b'VOLT GW RECOVERY OPEN'


class RecoveryClient:
  def __init__(self, pairing: bytes, hello: bytes, *, device: bytes, layout: bytes, policy: bytes,
               host_nonce: bytes | None = None):
    if (len(pairing)!=32 or not any(pairing) or len(hello)!=147 or hello[:7]!=b'VGR1\0\x01\0' or
        len(device)!=12 or len(layout)!=32 or len(policy)!=32 or hello[7:19]!=device or
        hello[19:51]!=layout or hello[51:83]!=policy or not any(hello[83:115]) or not any(hello[115:])):
      raise ProtocolError('recovery target/version/safety-policy mismatch')
    self.host_nonce=host_nonce if host_nonce is not None else secrets.token_bytes(32)
    if len(self.host_nonce)!=32 or not any(self.host_nonce):
      raise ProtocolError('host nonce')
    transcript=hello+self.host_nonce
    self.session=hashlib.sha256(transcript).digest()[:8]
    self.host_key=session_key(pairing,self.host_nonce,hello[115:],transcript,b'host')
    self.gateway_key=session_key(pairing,self.host_nonce,hello[115:],transcript,b'gateway')
    self.open_request=b'VGR1\x01'+self.host_nonce+hmac.digest(pairing,OPEN_DOMAIN+transcript,'sha256')
    self.sequence=0
    self.ready=False
    self.pending=None

  def accept_open(self,reply: bytes):
    if (len(reply)!=56 or reply[:8]!=b'VGR1\x01\x01\0\0' or reply[8:16]!=self.session or
        int.from_bytes(reply[16:20],'big')!=3600000 or int.from_bytes(reply[20:24],'big')!=256 or
        not hmac.compare_digest(reply[24:],hmac.digest(self.gateway_key,reply[:24],'sha256'))):
      raise ProtocolError('unverified recovery handshake')
    self.ready=True

  def request(self,opcode: int,payload: bytes=b'',transaction: int=0) -> bytes:
    if not self.ready or self.pending is not None or not (1<=opcode<=0x3f or 0x80<=opcode<=0x88):
      raise ProtocolError('invalid recovery operation or pending transaction')
    raw=seal(self.host_key,self.session,self.sequence,transaction,opcode,payload)
    self.pending=raw
    return raw

  def retry(self) -> bytes:
    if self.pending is None:
      raise ProtocolError('no pending recovery transaction')
    return self.pending

  def accept(self,reply: bytes) -> bytes:
    if self.pending is None or not 49<=len(reply)<=512:
      raise ProtocolError('unexpected recovery response')
    body,tag=reply[:-16],reply[-16:]
    if not hmac.compare_digest(tag,hmac.digest(self.gateway_key,body,'sha256')[:16]):
      raise ProtocolError('invalid recovery response authentication')
    magic,major,minor,kind,opcode,session,seq,txn,size=HEADER.unpack(body[:HEADER.size])
    expected=HEADER.unpack(self.pending[:HEADER.size])
    if ((magic,major,minor,kind)!=(b'VGW1',1,0,2) or
        (opcode,session,seq,txn)!=expected[4:8] or size!=len(body)-HEADER.size):
      raise ProtocolError('recovery response transaction mismatch')
    self.pending=None
    self.sequence+=1
    return body[HEADER.size:]  # first byte is operation status, not transport status
