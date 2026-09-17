"""Finite parked-trial transport via boardd, never direct primary Panda access.

No safety-mode changes, manager process, raw-TX CLI, or persistent service.
Both primary firmware and its live safety parameter must explicitly permit it.
"""
from collections import deque
import time

from openpilot.tools.volt_gateway.protocol import ProtocolError, OBJECT_REQUEST_ID, OBJECT_RESPONSE_ID
from openpilot.tools.volt_gateway.telemetry import TelemetryReceiver
from openpilot.tools.volt_gateway.usb_transport import UsbTransport


class ObjectTransport:
  # Same tested bounded ISO-TP exchange as USB; only frame IO differs.
  exchange = UsbTransport.exchange

  def __init__(self, send_frame, receive_batch, safe, *, clock=time.monotonic, sleep=time.sleep):
    self._send = send_frame
    self._receive = receive_batch
    self._safe = safe
    self.clock = clock
    self.sleep = sleep
    self.last_tx = None
    self.sent = 0
    self.started = clock()
    self.frames = deque()
    self.telemetry = TelemetryReceiver()
    self.observations = deque(maxlen=128)
    self.observation_drops = 0

  def check(self):
    if self.clock()-self.started >= 60 or not self._safe():
      raise ProtocolError('parked gateway trial expired or live safety state unavailable/unsafe')

  def send(self, frame):
    self.check()
    if len(frame) != 8 or self.sent >= 512:
      raise ProtocolError('gateway trial frame/total budget')
    if self.last_tx is not None:
      self.sleep(max(0, .012-(self.clock()-self.last_tx)))
    self.check()
    self._send(OBJECT_REQUEST_ID, bytes(frame), 1)
    self.last_tx = self.clock()
    self.sent += 1

  def receive(self, deadline):
    while self.clock() < deadline:
      self.check()
      if not self.frames:
        # Adapter supplies (address, payload, source bus); exclude TX echoes.
        for address, data, bus in self._receive():
          if address == OBJECT_RESPONSE_ID and bus == 1:
            if len(data) != 8 or len(self.frames) >= 64:
              raise ProtocolError('gateway response malformed or queue overflow')
            self.frames.append(bytes(data))
        if not self.frames:
          continue
      frame = self.frames.popleft()
      if frame[0] >> 4 in (10, 11, 12):
        observation = self.telemetry.feed(frame, self.clock())
        if observation is not None:
          if len(self.observations) == self.observations.maxlen:
            self.observation_drops += 1
          self.observations.append(observation)
        continue
      return frame
    raise TimeoutError('gateway Object CAN response deadline')


def boardd_transport():
  import socket
  from cereal import messaging
  from openpilot.selfdrive.car.volt_gateway_mailbox import ADDRESS
  sm = messaging.SubMaster(['carState', 'selfdriveState', 'pandaStates'])
  incoming = messaging.sub_sock('can', timeout=50)
  outgoing = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_NONBLOCK)

  def safe():
    sm.update(0)
    if not sm.all_checks():
      return False
    car, state = sm['carState'], sm['selfdriveState']
    pandas = sm['pandaStates']
    return (str(car.gearShifter) == 'park' and abs(car.vEgo) < .01 and not state.enabled and not state.active and
            len(pandas) == 1 and str(pandas[0].safetyModel) == 'gm' and
            (pandas[0].safetyParam & 69) == 68 and not pandas[0].controlsAllowed)

  def receive():
    event = messaging.recv_one(incoming)
    if event is None:
      return ()
    if not event.valid:
      raise ProtocolError('invalid boardd CAN publication')
    return ((int(f.address), bytes(f.dat), int(f.src)) for f in event.can)

  def send(address, data, bus):
    assert address == OBJECT_REQUEST_ID and bus == 1
    outgoing.sendto(time.monotonic_ns().to_bytes(8, 'big')+data, ADDRESS)

  deadline = time.monotonic()+3
  while time.monotonic() < deadline:
    sm.update(100)
    if safe():
      return ObjectTransport(send, receive, safe)
  raise ProtocolError('no fresh parked/disengaged state with gateway-capable GM safety')
