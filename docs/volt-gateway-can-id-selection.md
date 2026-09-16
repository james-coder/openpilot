# CAN-ID selection — no IDs approved

2026-09-16 additional evidence: full driving rlogs were copied and compared with
stationary traffic. Object-bus estimates were 22.0–23.6% during three highway
minutes with openpilot active and radar-reported targets, versus 24.1% stationary.
See [real traffic report](volt-gateway-real-traffic.md). This adds utilization
evidence, **not** a new ID-conflict survey or authorization of any transport ID.

Prior discovery screened eight GM DBCs and approximately 480 seconds of the
stationary route `00000069--97e393bf5e`, segments 10–17, preserved under
`/home/james/diagnostics/volt-hvac-20260914`. This is limited parked evidence,
not comprehensive vehicle-state coverage. The following prior results have
not been reprocessed in this implementation increment.

| Candidate | Bus | Evidence/conflict | Priority | Decision |
| --- | --- | --- | --- | --- |
| 0x409 | Object/Powertrain | Historical ASCM keepalive/forwarding | Not relevant | Reject |
| 0x7DF, 0x7E0–0x7EF | Any shared bus | Standard diagnostic concerns | Low | Reject |
| 0x600–0x602 | Not selected | Absent in limited screened data; GM diagnostic coverage unresolved | Relatively low | Defer |
| 0x6F0–0x6F2 | Not selected | Same limited absence, not proof of availability | Lower than 0x600 | Defer |

Choose low arbitration priority only AFTER excluding observed, DBC, diagnostic
and historical conflicts. Numerically high is not synonymous with safe. Prefer
two IDs, but no production identifier appears in code yet.

Prior conservative stuffed-frame estimates at 500 kbit/s:

| Logical bus | Frames / unique IDs | Mean / highest 1-s estimate |
| --- | --- | --- |
| 0 | 1,270,396 / 111 | 62.196% / 62.333% |
| 1 | 439,594 / 67 | 24.090% / 24.466% |
| 2 | 864,795 / 38 | 44.066% / 44.205% |

These are not electrical utilization measurements and cannot quantify missing
frames/error traffic. Object bus 1 is a candidate, not a chosen backhaul.
Require wiring proof plus captures for startup, READY, driving/braking,
charging, remote start, lock/unlock, shutdown and service activity before final
selection. Absence still cannot establish a mathematical guarantee of no use.

Recheck current fingerprint logic before integration: an extra standard ID can
eliminate a vehicle candidate. Default gateway silence until authentication is
necessary but does not solve warm host restarts by itself. Test those explicitly;
any exception must be exact-ID and installation-specific, not a blanket bypass.
