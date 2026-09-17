"""Optional parked-only datagram mailbox for card's sole sendcan publisher.

One nonblocking receive per control tick, no worker or driving-health dependency.
Abstract Linux socket disappears on exit; same-UID credentials required.
"""
import os
import socket
import struct
import time

ADDRESS = '\0voltgw-object-parked-v1'


class GatewayMailbox:
  def __init__(self):
    self.sock = None
    try:
      self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_NONBLOCK)
      self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
      self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
      self.sock.bind(ADDRESS)
    except OSError:
      self.close()

  def close(self):
    if self.sock is not None:
      self.sock.close()
      self.sock = None

  def step(self, safe, now_ns=None):
    if self.sock is None:
      return []
    try:
      data, ancillary, flags, _ = self.sock.recvmsg(17, socket.CMSG_SPACE(12))
      now = time.monotonic_ns() if now_ns is None else now_ns
      credentials = [v for level, kind, v in ancillary if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS]
      if (not safe or flags or len(data) != 16 or len(credentials) != 1 or len(credentials[0]) != 12 or
          struct.unpack('3i', credentials[0])[1] != os.getuid()):
        return []
      age = now-int.from_bytes(data[:8], 'big')
      return [(0x6F0, data[8:], 1)] if 0 <= age < 100_000_000 else []
    except BlockingIOError:
      return []
    except OSError:
      self.close()
      return []
