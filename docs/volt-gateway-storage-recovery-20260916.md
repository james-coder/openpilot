# Storage recovery and retained evidence

After the WSL restart, the current gateway sources, owner keys, original
firmware backups, signed private images and physical readbacks remained in the
repository/private directory or `/home/james/diagnostics/volt-gateway`, not
`/tmp`. The interrupted validation run is not accepted as a pass.

## Recovered temporary helper

The complete patch creating `/tmp/voltgw_bench_cycle.py` was recovered from the
local session record and preserved as
`/home/james/diagnostics/volt-gateway/recovered-session/voltgw_bench_cycle.py.txt`.
SHA-256: `818b200e40718bb748ece11e91d0da5d655ce9f3d5b8cecff30f046bd49951f5`.
Python's `compile(..., 'exec')` accepted its syntax without executing it or
loading credentials. Syntax validity does not validate flashing behavior.
An identical, non-executable reference copy is versioned at
`docs/recovered/voltgw_bench_cycle.py.txt`. It is historical exact-device bench
orchestration, not a general-purpose deployment entry point. Its later reviewed
use and required validation gates are recorded in the session LED fix report.

The recent session's recorded tool-call `/tmp` paths additionally referenced
pytest/generated validation scratch. No other reusable helper was found in
that bounded search. This is not a completeness claim about every historical
session, binary artifact or shell-generated file.

Three older deleted temporary worktrees retain their commit objects:

- `fix/aranet-device`: `a457bd91da2d44c684e83bfa1791c931ba91f549`
- `fix/vehicle-recognition-device`: `48fa10033779c6921de987031627e432d515ad6a`
- `deploy/volt-gm-egr`: `2f17a57eb1d20759d0853511c79994e464c8e6c8`

All three surviving worktree indexes compare equal to their HEAD commits, so
there are no additional staged changes to rescue there. Unstaged/untracked
contents cannot be established from that comparison. Their Git metadata has
not been pruned.

## Storage limits

Only disposable generated test/build scratch uses a temporary directory. The
validator removes its owned scratch on normal success/failure; retained reports
remain outside it. The runner refuses/halts commands below a 3-GiB free-space
reserve, including the Windows backing volume when `/mnt/c` is present. It also
bounds generated scratch to 1 GiB and an individual command log to 50 MiB.
Polling is not an instantaneous storage quota and unrelated writers can still
consume space. Abrupt termination/power loss can leave disposable scratch.

Cleanup removed pip cache entries and the reproducible MCUboot Cargo target
directory using their own cleanup tools. Source, keys, firmware backups and
physical evidence were retained. An in-use uv cache was not force-deleted.

## Deployment distinction

USB-connected White Panda firmware validation is separate from installation.
The bench provisioning uses SWCAN controller 3, HSCAN mask 3, backhaul disabled,
and no allowed vehicle transmissions. Current in-repository GM safety has no
gateway TX exception. The optional comma gateway daemon is not registered with
manager. Neither is represented as deployed or tested on the production car.

Before installed CAN control, resolve physical shared-bus wiring and transport
IDs, then implement/test the exact Tres whitelist and optional isolated host
service. Do not infer those gates passed from a working USB bench image. See
`volt-gateway-can-id-selection.md` and `volt-gateway-parked-rx-check.md`.
