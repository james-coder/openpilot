# Simultaneous Tres / White Panda startup observation

The owner started the car, left it parked, then switched it off. The White
Panda remained connected via the OBD splitter and laptop USB. No firmware,
safety mode, mux, vehicle TX policy or manager configuration was changed.

The owner subsequently confirmed the hood was open during this startup, causing
the gasoline engine to run. The car was then switched off and the hood closed.
This is not evidence of a battery-only READY test.

`startup_probe.py --side tres` ran as a finite, nice-19 Python process supplied
over SSH stdin using the comma's existing `/usr/local/venv/bin/python`. It
subscribed to boardd's existing CAN publication, without opening Panda USB or
publishing sendcan. Its stdout was saved on the laptop, not installed as a
device service. The White-side process used paired USB queries only. Neither
observer is a manager/engagement dependency. No keys were sent to the comma.

## Result

At 02:21:53 UTC on 2026-09-17, after the observed burst had stopped:

| Network / observed interface | Tres subscriber RX | White RX during interval | White FIFO-overflow increase |
| --- | ---: | ---: | ---: |
| Primary HSCAN / logical 0 | 238,227 | 238,057 | 56 |
| Object HSCAN / logical 1 | 39,803 | 39,779 | 10 |
| Chassis HSCAN / Tres logical 2 | 162,910 | Not selected on White | Not applicable |
| SWCAN / White logical 3 | Not available on Tres | 14,709 | 0 |

White primary RX is a delta: 542,547 minus the initial 304,490. Object and
SWCAN started at zero. The Tres observer started before the traffic burst and
saw 121/67/38 unique IDs on its three HSCAN buses. White firmware counters and
Tres userspace publication counts are different observation points, and host
UTC clocks/sampling instants are not hardware-synchronized. Differences are
not an exact cross-device lost-frame measurement. The increasing hardware FIFO
overflow counters independently establish White HSCAN receive loss.

The first nonzero object count appeared at 02:20:00.398 on Tres and at the
White's next sample, 02:20:01.575. White SWCAN also became nonzero in that sample.
Those timestamps have different sampling resolution, not a measured 1.18-second
gateway propagation delay. All White sampled transmitted/TX-error/ESR values
remained zero; its uptime remained increasing with unchanged reset flags.

## What this establishes

- The previously quiet White interfaces are capable of receiving traffic through
  this connected splitter when the vehicle is awake.
- White CAN2 HSCAN and CAN3 SWCAN operate simultaneously in the current mapping.
- Object-bus traffic appeared and ceased on both devices; car-off silence was
  not proof of a disconnected interface. This supersedes the earlier lack of
  positive object/SWCAN reception evidence.
- Receive loss on White HSCAN still needs diagnosis and an off-device-tested
  fix. SWCAN zero overflows here is not proof of zero loss under every condition.
- This does not select transport IDs, authorize vehicle CAN writes, prove
  lossless forwarding or complete installed gateway integration.

## Reproduction and evidence

The reusable probe emits bounded statistics/ID sets, not a duplicate raw-CAN
recorder. Duration is limited to 300 seconds, and the paired White mode accepts
only the expected CAN-silent mapping. Three unit tests cover query-only command
selection, close-on-fault and exclusion of CAN TX echo/rejection source values;
syntax and lint checks also passed. These do not substitute for the physical
results above or constitute a fresh full firmware-validation run.

Private JSONL output lives outside `/tmp` and Git at:
`/home/james/diagnostics/volt-gateway/vehicle-observations/startup-20260917T021929Z/`.
The parent capture process limits each log to 1 MiB and its overall run to 280
seconds. Original openpilot route logs remain the preferred raw CAN evidence.
