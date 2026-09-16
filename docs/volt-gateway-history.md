# Recovered Volt forwarding history

## Major finding

Vasily Tarasov's `vntarasov/panda` branch `legacy-gm-forwarding`, commit
`4e85803018ba8cfe20d7e1eb47bc7171ee38faf4` (2018-08-15), explicitly describes
a Panda at the Volt DLC forwarding Powertrain traffic to Object CAN and
openpilot Object-CAN commands to Powertrain/Chassis. This plausibly explains
the physical label, but is NOT proven to be installed firmware.

At that commit, `board/safety/safety_gm.h:237` contains the forwarding hook:
Object bus 1 sends 0x180/0x409/0x2CB/0x370 to Powertrain bus 0, and 0x315 to
Chassis bus 2. Selected Powertrain observations go to Object bus 1. This was
an actuation-routing arrangement, NOT the proposed passive SWCAN probe. Never
enable it as a convenient gateway baseline.

Preserved outside this repository:

- `/home/james/diagnostics/volt-gateway/history/legacy-gm-forwarding.bundle`;
  verified complete-history bundle, SHA-256
  `bda9c8802ca47a26bbc730604820e2cabd786ab9c9a4cfbd85d19d90fa143daf`.
- `history/panda-3b356216.tar` under the same diagnostics root; SHA-256
  `9d603db52c46930615795f16d4cca82486f38dc2637cfde1540a935353396ac8`.

## Selected bring-up reference, not installed provenance

Use historical F4 reference `3b35621671aaa6de3fc66d85d30e4208a77e2489` for
board/USB/compiler work because its v1.7.3 EON application and White/GMLAN
implementation match the observed generation. Do not retrofit H725/Tres master.

Two clean GCC 13.2.1 builds produced identical 32,504-byte unsigned code, SHA-256
`2d8fae01d890357fa9515774cee3850ff072e48b28a5684202f14a601e9617f5`.
Their signed-body SHA-1 was `53a91a9ae3744249de5ed08f03758341890ecf37`, not
the device signature's `87e3936657b7121a199d649f37f48867b7b17a22`.
The original toolchain is not established; this mismatch neither proves nor
disproves source ancestry. Stop provenance hunting as a prerequisite: preserve
the binary instead, as requested. Build logs are in `provenance/3b356216-gcc13/`.

Public source: [historical forwarding commit](https://github.com/vntarasov/panda/commit/4e85803018ba8cfe20d7e1eb47bc7171ee38faf4).
Cyan-Panda's different MCU design is a research lead, not this board's firmware.
