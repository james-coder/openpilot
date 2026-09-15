"""Versioned receive-only local feed. No device-command API."""
import json
from pathlib import Path

SOCKET = Path('/run/aranet-bluetooth/feed.sock')
ASSETS = Path('/data/aranet-bluetooth')
MAX_PACKET = 4096


def encode(message):
  raw = json.dumps(dict(version=1, **message), allow_nan=False).encode()
  if len(raw) > MAX_PACKET:
    raise ValueError('Oversized cabin message')
  return raw


def decode_message(raw):
  if not raw or len(raw) > MAX_PACKET:
    raise ValueError('Invalid cabin message size')
  msg = json.loads(raw)
  if not isinstance(msg, dict) or msg.get('version') != 1 or msg.get('type') not in ('status', 'advertisement'):
    raise ValueError('Invalid cabin message')
  return msg
