import select
import time
from serial import Serial
from crcmod import mkCrcFun
from struct import pack, unpack_from, calcsize

class ModemDiag:
  MAX_FRAME_BYTES = 131072  # encoded HDLC, including escaping and CRC

  def __init__(self):
    self.serial = self.open_serial()
    self.pend = b''

  def open_serial(self):
    serial = Serial("/dev/ttyUSB0", baudrate=115200, rtscts=True, dsrdtr=True, timeout=0, write_timeout=5, exclusive=True)
    serial.flush()
    serial.reset_input_buffer()
    serial.reset_output_buffer()
    return serial

  ccitt_crc16 = mkCrcFun(0x11021, initCrc=0, xorOut=0xffff)
  ESCAPE_CHAR = b'\x7d'
  TRAILER_CHAR = b'\x7e'

  def hdlc_encapsulate(self, payload):
    payload += pack('<H', ModemDiag.ccitt_crc16(payload))
    payload = payload.replace(self.ESCAPE_CHAR, bytes([self.ESCAPE_CHAR[0], self.ESCAPE_CHAR[0] ^ 0x20]))
    payload = payload.replace(self.TRAILER_CHAR, bytes([self.ESCAPE_CHAR[0], self.TRAILER_CHAR[0] ^ 0x20]))
    payload += self.TRAILER_CHAR
    return payload

  def hdlc_decapsulate(self, payload):
    if not 4 <= len(payload) <= self.MAX_FRAME_BYTES or payload[-1:] != self.TRAILER_CHAR:
      raise ValueError('Invalid DIAG frame length or terminator')
    payload = payload[:-1]
    payload = payload.replace(bytes([self.ESCAPE_CHAR[0], self.TRAILER_CHAR[0] ^ 0x20]), self.TRAILER_CHAR)
    payload = payload.replace(bytes([self.ESCAPE_CHAR[0], self.ESCAPE_CHAR[0] ^ 0x20]), self.ESCAPE_CHAR)
    if len(payload) < 3 or payload[-2:] != pack('<H', ModemDiag.ccitt_crc16(payload[:-2])):
      raise ValueError('Invalid DIAG CRC or empty message')
    return payload[:-2]

  def recv(self, deadline=None):
    # self.serial.read_until makes tons of syscalls!
    raw_payload = [self.pend]
    size = len(self.pend)
    while self.TRAILER_CHAR not in raw_payload[-1]:
      remaining = None if deadline is None else deadline - time.monotonic()
      if remaining is not None and remaining <= 0:
        raise TimeoutError('DIAG response deadline exceeded')
      ready, _, _ = select.select([self.serial.fd], [], [], remaining)
      if not ready:
        raise TimeoutError('DIAG response deadline exceeded')
      raw = self.serial.read(0x10000)
      if not raw:
        raise OSError('DIAG serial stream ended')
      size += len(raw)
      if size > self.MAX_FRAME_BYTES + 0x10000:
        raise ValueError('DIAG stream exceeds frame bound')
      raw_payload.append(raw)
    raw_payload = b''.join(raw_payload)
    raw_payload, self.pend = raw_payload.split(self.TRAILER_CHAR, 1)
    raw_payload += self.TRAILER_CHAR
    unframed_message = self.hdlc_decapsulate(raw_payload)
    return unframed_message[0], unframed_message[1:]

  def send(self, packet_type, packet_payload):
    self.serial.write(self.hdlc_encapsulate(bytes([packet_type]) + packet_payload))

# *** end class ***

DIAG_LOG_F = 16
DIAG_LOG_CONFIG_F = 115
LOG_CONFIG_RETRIEVE_ID_RANGES_OP = 1
LOG_CONFIG_SET_MASK_OP = 3
LOG_CONFIG_SUCCESS_S = 0

def send_recv(diag, packet_type, packet_payload):
  diag.send(packet_type, packet_payload)
  deadline = time.monotonic() + 5
  while 1:
    opcode, payload = diag.recv(deadline=deadline)
    if opcode != DIAG_LOG_F:
      break
  return opcode, payload

def setup_logs(diag, types_to_log):
  opcode, payload = send_recv(diag, DIAG_LOG_CONFIG_F, pack('<3xI', LOG_CONFIG_RETRIEVE_ID_RANGES_OP))

  header_spec = '<3xII'
  if opcode != DIAG_LOG_CONFIG_F or len(payload) != calcsize(header_spec) + 16 * 4:
    raise ValueError('Invalid DIAG log range response')
  operation, status = unpack_from(header_spec, payload)
  if operation != LOG_CONFIG_RETRIEVE_ID_RANGES_OP or status != LOG_CONFIG_SUCCESS_S:
    raise ValueError('DIAG log range request failed')

  log_masks = unpack_from('<16I', payload, calcsize(header_spec))
  # Log IDs allocate twelve bits to the item and four to the equipment ID.
  if any(bits > 4096 for bits in log_masks):
    raise ValueError('DIAG log mask exceeds item-ID space')

  for log_type, log_mask_bitsize in enumerate(log_masks):
    if log_mask_bitsize:
      log_mask = [0] * ((log_mask_bitsize+7)//8)
      for i in range(log_mask_bitsize):
        if ((log_type<<12)|i) in types_to_log:
          log_mask[i//8] |= 1 << (i%8)
      opcode, payload = send_recv(diag, DIAG_LOG_CONFIG_F, pack('<3xIII',
          LOG_CONFIG_SET_MASK_OP,
          log_type,
          log_mask_bitsize
      ) + bytes(log_mask))
      if opcode != DIAG_LOG_CONFIG_F or len(payload) < calcsize(header_spec):
        raise ValueError('Invalid DIAG log mask response')
      operation, status = unpack_from(header_spec, payload)
      if operation != LOG_CONFIG_SET_MASK_OP or status != LOG_CONFIG_SUCCESS_S:
        raise ValueError('DIAG log mask request failed')
