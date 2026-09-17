# Volt gateway topology — wiring remains unverified

The intended gateway is a parallel observer, never an inline required link.
The primary Tres continues handling its existing three HSCAN networks unchanged.

Historical reference `3b356216...`, `board/boards/white.h:98–150`, implements:

| Mode | SWCAN controller/pins | HSCAN controllers remaining |
| --- | --- | --- |
| GMLAN CAN2 | CAN2, PB12/PB13 | CAN1 and CAN3 |
| GMLAN CAN3 | CAN3, PB3/PB4 | CAN1 and CAN2 |

Normal CAN2 uses PB5/PB6; CAN3 PA8/PA15. HSCAN enable GPIOs are PC1, PC13,
PA0 (active-low), at `white.h:5`. CAN1 uses PB8/PB9 in the historical board
implementation. SWCAN needs the dedicated single-wire PHY, not just 33.3-kbit
timing on a differential transceiver. Hardware revision can restrict routing:
do not assume CAN3 GMLAN is supported on an unverified early board.

Typical White Panda/DLC mapping is CAN1=6/14, CAN2=3/11, CAN3=12/13, SWCAN=1.
The Volt harness/splitter can change what is actually reachable; this pin table
is NOT a continuity measurement of the installation.

## Later continuity work

With harness disconnected and unpowered, record continuity separately from each
secondary-Panda connector pin 1, 3, 11, 6, 14, 12, 13 to the corresponding
vehicle-side and primary-harness connector terminals. Record cross-connections,
opens, and any inline electronics. Never use resistance/continuity mode on a
powered vehicle bus. Inspect board markings/photos to establish revision/PHY.
Check bus termination/loading before connection; do not add another termination
merely because the probe has a connector.

Object HSCAN is a research candidate only: prior capture estimates show lower
load there. If SWCAN consumes CAN2, that choice may not be reachable at the
same time. Final bus selection waits for actual routing and capture evidence.

No mux, transceiver, CAN speed, vehicle forwarding or primary safety changes
have been made for this project.
