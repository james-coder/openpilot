"""Bounded unauthenticated observational stream. Never an actuation input.

Three Classic-CAN frames per source record. Shares the response ID with ISO-TP,
using disjoint PCI nibbles A/B/C. No buffering across an interrupted record.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Observation:
  timestamp_us_low: int
  address: int
  bus: int
  flags: int
  dlc: int
  data: bytes
  handle: int
  sequence: int


class TelemetryReceiver:
  def __init__(self):
    self.pending=None
    self.previous=None
    self.drops=0

  def feed(self,data: bytes,now: float) -> Observation | None:
    if not math.isfinite(now) or (self.previous is not None and now<self.previous):
      self.pending=None
      self.drops+=1
      return None
    self.previous=now
    if len(data)!=8 or data[0]>>4 not in (10,11,12):
      if self.pending is not None:
        self.pending=None
        self.drops+=1
      return None
    kind=data[0]>>4
    seq=((data[0]&15)<<8)|data[1]
    if kind==10:
      if self.pending is not None:
        self.drops+=1
      self.pending=(seq,11,now,data[2:])
      return None
    if self.pending is None:
      self.drops+=1
      return None
    sequence,expected,started,body=self.pending
    if sequence!=seq or kind!=expected or now-started>=0.1:
      self.pending=None
      self.drops+=1
      return None
    body+=data[2:]
    if kind==11:
      self.pending=(seq,12,started,body)
      return None
    self.pending=None
    stamp=int.from_bytes(body[:4],'big')
    address=int.from_bytes(body[4:8],'big')
    bus,flags,dlc=body[16]>>6,(body[16]>>4)&3,body[16]&15
    if dlc>8 or body[17]>=32 or address>(0x1fffffff if flags&1 else 0x7ff):
      self.drops+=1
      return None
    return Observation(stamp,address,bus,flags,dlc,body[8:8+dlc],body[17],seq)
