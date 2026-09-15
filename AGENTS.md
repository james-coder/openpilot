# Optional-feature safety requirements

- Optional cabin telemetry, Bluetooth, graphs, and convenience features must not become requirements for driving engagement or continued engagement.
- Before deployment, test the feature absent, stopped, crashed, permission-denied, and unavailable after a cold boot. Test engagement and disengagement behavior, not just the feature's happy path.
- A low CPU priority or lack of CAN transmissions does not prove independence: review manager process-health checks, readiness, alerts, shared resources, and lifecycle ownership.
- Preserve the normal blocking/disabling behavior of all driving processes. Any exemption must name only the verified optional process, not weaken the global watchdog.
- Do not claim device or driving validation from unit tests or synthetic previews. State exactly what passed and what remains unverified.
- Never deploy/restart driving software while the vehicle is moving. Require current verified safe state before device changes.
