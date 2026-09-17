"""Exact-target gateway USB transport; never opens Panda's generic TX/flash API."""
from contextlib import closing
from collections import deque
import time

from openpilot.tools.volt_gateway.protocol import IsoTpReceiver, isotp_encode, ProtocolError
from openpilot.tools.volt_gateway.telemetry import TelemetryReceiver


class UsbTransport:
  def __init__(self,serial: str):
    if len(serial)!=24 or any(c not in '0123456789abcdef' for c in serial):
      raise ValueError('exact lowercase White Panda serial required')
    import usb1
    self.usb=usb1
    self.context=usb1.USBContext()
    self.handle=None
    self.frames=deque(maxlen=64)
    self.telemetry=TelemetryReceiver()
    self.observations=deque(maxlen=128)
    self.observation_drops=0
    try:
      matches=[]
      for device in self.context.getDeviceList(skip_on_error=False):
        if (device.getVendorID(),device.getProductID())==(0xbbaa,0xddcc):
          with closing(device.open()) as h:
            if h.getASCIIStringDescriptor(device.getSerialNumberDescriptor())==serial:
              matches.append(device)
      if len(matches)!=1:
        raise ProtocolError('exact gateway USB target not present')
      self.handle=matches[0].open()
      version=bytes(self.handle.controlRead(0xc0,0xd6,0,0,64,timeout=1000))
      if version!=b'voltgw-v1':
        raise ProtocolError('not running gateway application; no command sent')
      self.handle.claimInterface(0)
    except BaseException:
      self.close()
      raise

  def close(self):
    if self.handle is not None:
      try:
        self.handle.releaseInterface(0)
      except self.usb.USBError:
        pass
      self.handle.close()
      self.handle=None
    self.context.close()

  def __enter__(self):
    return self

  def __exit__(self,*args):
    self.close()

  def send(self,frame: bytes):
    if len(frame)!=8 or self.handle.bulkWrite(2,frame,timeout=1000)!=8:
      raise ProtocolError('short USB gateway transfer')

  def receive(self,deadline):
    while time.monotonic()<deadline:
      if self.frames:
        frame=self.frames.popleft()
        if frame[0]>>4 in (10,11,12):
          observation=self.telemetry.feed(frame,time.monotonic())
          if observation is not None:
            if len(self.observations)==self.observations.maxlen:
              self.observation_drops+=1
            self.observations.append(observation)
          continue
        return frame
      try:
        data=bytes(self.handle.bulkRead(0x81,64,timeout=100))
      except self.usb.USBErrorTimeout:
        continue
      if not data:
        continue
      if len(data)>64 or len(data)%8:
        raise ProtocolError('malformed USB gateway frame batch')
      self.frames.extend(data[i:i+8] for i in range(0,len(data),8))
    raise TimeoutError('gateway response deadline')

  def exchange(self,pdu: bytes,*,timeout=10):
    if not 0<timeout<=60:
      raise ValueError('bounded exchange timeout required')
    deadline=time.monotonic()+timeout
    frames=isotp_encode(pdu)
    remaining=0
    separation=.01
    for index,frame in enumerate(frames):
      if index and remaining==0:
        flow=self.receive(deadline)
        if flow[:1]!=b'\x30' or flow[1]==0 or not 1<=flow[2]<=127:
          raise ProtocolError('unexpected gateway flow control')
        remaining=flow[1]
        separation=flow[2]/1000
      if index:
        time.sleep(separation)
        remaining-=1
      self.send(frame)
    receiver=IsoTpReceiver()
    for _ in range(128):
      raw=self.receive(deadline)
      result=receiver.feed(raw,time.monotonic())
      if result is not None:
        return result
      if raw[0]>>4==1:
        self.send(b'\x30\0\x0a'+bytes(5))
    raise ProtocolError('excessive reply fragments')
