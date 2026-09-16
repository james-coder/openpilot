"""Host measurements and transparent wire-cost models. Never accesses a device."""

import json
import platform
import statistics
import struct
import time
import tracemalloc
import zlib

from openpilot.tools.volt_gateway.protocol import (COMPACT, FRAME, HEADER, TAG_SIZE, IsoTpReceiver, Observation, ProtocolError, StreamReceiver, Subscription,
                       compact_encode, flow_frames, isotp_encode, stream_encode)
from openpilot.tools.volt_gateway.simulation import Scheduler


def batch(records: list[Observation]) -> bytes:
  body = struct.pack('!4sIII', b'OBS1', 1, 0, len(records)) + b''.join(r.pack() for r in records)
  return body + struct.pack('!I', zlib.crc32(body))


def update_model(host_fps: int, gateway_fps: int, size: int) -> dict:
  chunk_size = 256
  request_size = HEADER.size + TAG_SIZE + 4 + chunk_size
  ack_size = HEADER.size + TAG_SIZE + 4
  request = len(isotp_encode(bytes(request_size)))
  ack = len(isotp_encode(bytes(ack_size)))
  gateway_frames = flow_frames(request) + ack
  host_frames = request + flow_frames(ack)
  seconds = host_frames / host_fps + gateway_frames / gateway_fps
  chunks = (size + chunk_size - 1) // chunk_size
  return {'host_fps': host_fps, 'gateway_fps': gateway_fps, 'image_bytes': size,
          'request_pdu_bytes': request_size, 'ack_pdu_bytes': ack_size,
          'host_frames_per_chunk': host_frames, 'gateway_frames_per_chunk': gateway_frames,
          'paced_transfer_minutes': round(seconds * chunks / 60, 3), 'payload_bytes_per_second': round(chunk_size / seconds, 2),
          'aggregate_budget_ceiling_percent_500k': (host_fps + gateway_fps) * 135 / 5000,
          'excluded': ['erase/program time', 'signature verification', 'bus arbitration', 'retries', 'handshake/renewal', 'power pauses']}


def measure(codec: str, n: int, iterations: int) -> dict:
  record = Observation(3, 0x10734099, True, False, 8, b'12345678', 123456789, 123)
  sub = Subscription(1, record.bus, record.address, record.extended, record.timestamp_us - 1000)

  def run():
    if codec == 'isotp':
      packets = isotp_encode(batch([record] * n))
      receiver = IsoTpReceiver()
    else:
      packets = stream_encode(record) if codec == 'full-stream' else compact_encode(record, sub)
      receiver = StreamReceiver((sub,))
    result = None
    for i, packet in enumerate(packets):
      result = receiver.feed(packet, i * .001)
    assert result is not None
    if codec != 'isotp':
      assert result == record
    else:
      assert result == batch([record] * n)
    return len(packets)

  counts = run()
  times = []
  for _ in range(iterations):
    start = time.process_time_ns()
    run()
    times.append(time.process_time_ns() - start)
  tracemalloc.start()
  tracemalloc.reset_peak()
  run()
  _, peak = tracemalloc.get_traced_memory()
  tracemalloc.stop()
  reverse = flow_frames(counts) if codec == 'isotp' else 0
  total = (counts + reverse) / n
  # Compact: explicitly charge one 32-byte mapping/anchor payload in an
  # authenticated envelope per 60 seconds PER subscription. Not a frozen format.
  mapping_frames = len(isotp_encode(bytes(HEADER.size + TAG_SIZE + 32))) if codec == 'compact-stream' else 0
  mapping_total = mapping_frames + flow_frames(mapping_frames) if mapping_frames else 0
  return {'codec': codec, 'records_per_packet': n, 'forward_frames': counts, 'reverse_flow_frames': reverse,
          'total_frames_per_record': total, 'wire_bits_per_record_upper': total * 135,
          'at_10_source_hz_bus_percent_without_mapping': total * 135 * 10 / 5000,
          'compact_mapping_frames_per_60s_per_subscription': mapping_total,
          'at_10_source_hz_bus_percent_with_mapping': (total * 10 + mapping_total / 60) * 135 / 5000,
          'host_cpu_us_median_per_record': round(statistics.median(times) / n / 1000, 3),
          'host_cpu_us_p99_per_record': round(sorted(times)[min(len(times) - 1, int(len(times) * .99))] / n / 1000, 3),
          'host_python_peak_allocation_bytes': peak,
          'logical_reassembly_payload_bound': (len(batch([record] * n)) if codec == 'isotp' else 35 if codec == 'full-stream' else 21),
          'paced_packet_seconds_at_20_telemetry_20_host_fps': counts / 20 + reverse / 20,
          'oldest_record_batch_fill_seconds_at_10hz': (n - 1) / 10,
          'mcu_cycles_ram_stack_and_latency': 'UNMEASURED; Python allocations/times are not firmware measurements'}


def contention(codec: str, source_hz: int, busy_fraction: float) -> dict:
  """1 ms discrete sender model; no CAN hardware/ISO-TP peer claim.

  ISO-TP datagrams cannot interleave in one direction. Compact records can be
  interrupted by separately demultiplexed control frames. Flow control is
  accounted in wire costs, but omitted here; this is a scheduling comparison.
  """
  s = Scheduler()
  record = Observation(3, 0x123, False, False, 8, b'12345678', 1000, 1)
  sub = Subscription(1, 3, 0x123, False, 0)
  frames = isotp_encode(batch([record])) if codec == 'isotp' else compact_encode(record, sub)
  delays, completions, observation_delays = [], [], []
  pending, next_source, source_sequence = {}, 0., 0
  receiver = IsoTpReceiver() if codec == 'isotp' else StreamReceiver((sub,))
  decode_errors = 0
  for tick in range(5000):
    now = tick / 1000
    if now + 1e-9 >= next_source:
      record = Observation(3, 0x123, False, False, 8, b'12345678', tick * 1000, source_sequence)
      source_sequence += 1
      frames = isotp_encode(batch([record])) if codec == 'isotp' else compact_encode(record, sub)
      s.offer(frames, now, control=False, indivisible=codec == 'isotp')
      next_source += 1 / source_hz
    if tick % 500 == 100:
      s.offer([struct.pack('!II', tick, i) for i in range(8)], now, control=True, indivisible=True)
      pending[tick] = now
    blocked = (tick % 10) < round(busy_fraction * 10)
    out = s.step(now, busy=blocked)
    if out and out[1]:
      transaction, index = struct.unpack('!II', out[0])
      if index == 0:
        delays.append(now - pending[transaction])
      if index == 7:
        completions.append(now - pending.pop(transaction))
    elif out:
      try:
        observation = receiver.feed(out[0], now)
        if observation is not None:
          if codec == 'isotp':
            observation = Observation.unpack(observation[16:44])
          observation_delays.append(now - observation.timestamp_us / 1e6)
      except ProtocolError:
        decode_errors += 1
  return {'codec': codec, 'source_hz': source_hz, 'synthetic_busy_fraction': busy_fraction,
          'control_first_frame_delay_ms_max': round(max(delays) * 1000, 3) if delays else None,
          'control_first_frame_delay_ms_p50': round(statistics.median(delays) * 1000, 3) if delays else None,
          'control_complete_delay_ms_max': round(max(completions) * 1000, 3) if completions else None,
          'observation_complete_delay_ms_max': round(max(observation_delays) * 1000, 3) if observation_delays else None,
          'source_records': source_sequence, 'completed_observations': len(observation_delays), 'decode_errors': decode_errors,
          'control_responses_started': len(delays), 'counters': dict(s.counters),
          'limitation': '1ms synthetic sender scheduling only; not measured CAN delivery or a bus latency guarantee'}


def report(iterations: int = 1000) -> dict:
  if not 10 <= iterations <= 100000:
    raise ValueError('iterations must be between 10 and 100000')
  return {'schema': 1, 'transport_ids': None, 'production_ready': False,
          'environment': {'python': platform.python_version(), 'system': platform.platform()},
          'warning': 'Offline experimental codecs. No hardware transport. No telemetry format or update rate selected.',
          'record_bytes': FRAME.size, 'compact_body_bytes': COMPACT.size,
          'telemetry': [measure('isotp', n, iterations) for n in (1, 2, 4)] +
                       [measure(c, 1, iterations) for c in ('full-stream', 'compact-stream')],
          'contention': [contention(c, hz, busy) for c in ('isotp', 'compact-stream') for hz in (1, 10, 50) for busy in (0., .8)],
          'update': [update_model(h, g, size) for h, g in ((20, 40), (80, 40), (160, 80)) for size in (131072, 262144)]}


if __name__ == '__main__':
  print(json.dumps(report(), indent=2))
