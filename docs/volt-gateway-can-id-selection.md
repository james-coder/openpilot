# CAN-ID selection — two IDs selected for a bounded parked trial

## Owner-approved trial, 2026-09-17 UTC

Selected standard IDs: `0x6F0` host request, `0x6F1` White response, Object bus
only (Tres logical bus 1 / White physical CAN2). Owner approved these candidates
after reviewing the evidence below. This supersedes the earlier deferred trial
decision, not the remaining production coverage gaps. No third ID is enabled.

The trial is an authenticated status exchange, not vehicle control. Primary TX
requires explicit GM safety flag 64, ASCM+EV configuration, fresh stationary
wheel-speed evidence, controls disallowed, Classic standard DLC8 framing and a
10 ms minimum TX interval. Host additionally requires Park and a finite 60 s /
512-frame budget. White uses its existing fixed-ID, bounded response path;
vehicle-side write rules remain empty. Both IDs have low arbitration priority,
but still occupy bus time when transmitting. Removal of the trial environment
opt-in and restart returns the host to no gateway TX permission. White's saved
CAN-silent full readback is the bench rollback.

## Additional screening, 2026-09-17 UTC

Offline screening of seven full rlogs (route 74 segments 0/5/10/15 and route 76
segments 2/3/4), local GM DBC files, and the simultaneous startup observation
found no conflicts for `0x600–0x602` or `0x6F0–0x6F2` on the screened HSCAN buses.
Evidence with per-file hashes and counts is preserved at
`/home/james/diagnostics/volt-gateway/traffic/id-screen-20260917.json`, SHA-256
`8c84eeb7dcadfc9d8a7124f58ac5ae95f2b178eabf75e56ae5a232b07e9cc67e`.

The [startup comparison](volt-gateway-startup-comparison-20260916.md) now provides
positive simultaneous CAN2 Object / CAN3 SWCAN receive evidence through the
connected splitter. This strengthens Object as a backhaul candidate, but does
not approve transport IDs. Enhanced diagnostic traffic and unrepresented vehicle
states remain unresolved. Among otherwise equally supported IDs, the `0x6F0`
group has lower arbitration priority and is preferred on that criterion alone.

## Earlier evidence

### Diagnostic-range review

The public mirror of GM's February 2010 GMW3110, sections 4.4.3–4.4.5, lists
normal enhanced diagnostic request/responses in `0x240–0x25F`, `0x540–0x55F`,
`0x640–0x65F`, optional emissions responses `0x5E8–0x5EF`, and OBD
`0x7DF–0x7EF`, with functional/wake IDs `0x100–0x102`. It explicitly does not
reserve all of `0x6xx` merely because some USDT responses use that prefix.
Thus `0x6F0–0x6F2` do not intersect those listed normal diagnostic ranges.
This narrows one concern; it does not establish absence of OEM normal traffic,
newer assignments or programming traffic for this installation. IDs remain
deferred. [GM specification mirror](https://studylib.net/doc/26162849/gmw3110-2010).

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
