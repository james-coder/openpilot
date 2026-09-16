# Gateway security and key lifecycle

Boundary: an untrusted node on the shared HSCAN must not reconfigure the gateway,
enable body TX, replay privileged commands or install an unsigned application.
The White Panda must enforce this independently of Linux and the primary Panda.

Candidate pairing: per-device random 256-bit secret, healthy fresh boot/session
challenges, HKDF-SHA256 directional keys and HMAC-SHA256 truncated to 128 bits.
Prototype envelope checks exist; MCU RNG health, pairing and session negotiation
are NOT implemented. Fail closed without freshness/entropy; no fixed session
fallback. Expiry/reboot invalidates runtime permissions and pending fragments.

## Lifecycle

- Initial provisioning: physical bench only, bind recorded gateway identity to
  the specific host, store secrets in restricted files/MCU provisioning area.
- Backup: encrypted off-device copy, separate decryption material. Never Git,
  route logs, diagnostics, packet dumps or crash reports.
- Host reinstall: restore identity/secret from backup; verify the gateway.
- Host replacement: retire old credentials and perform deliberate bench pairing.
- Gateway replacement: new identity and secret; never clone an old device's
  private identity as a shortcut.
- Rotation: renew session keys routinely; v1 long-term pairing-key rotation and
  verification-key replacement are bench-only operations.
- Lost key: recover encrypted backup or deliberately re-pair physically. No
  unauthenticated CAN factory reset or universal recovery password.

## Two independent authorities

The routine 256-bit pairing credential belongs on this comma and this gateway.
It authorizes only the documented operational commands. It does NOT authorize
update-mode entry, flash erase/program, image activation or trust-key replacement.

Firmware/update authority uses a separate per-device ECDSA-P256/SHA-256 pair.
The private key stays encrypted in this development checkout's Git-ignored
`.voltgw-private/<device>/firmware-key.pem`, never in Git. The comma only relays
public signed artifacts: do not copy the private key there even temporarily,
forward a signing agent, or expose a general-purpose remote signing service.
The gateway loader contains the verification/public key, not the signing key.
Public verification keys may be version controlled. Back up private material
encrypted off-device; a Git clone must not recover signing authority.

Image authenticity is not installation permission. Require a fresh single-use
operator authorization binding device, phase, random boot session/challenge,
manifest digest (including image hash, layout, size, version and build), and a
bounded lease. Application ENTER and loader PROGRAM are distinct phases. The
loader obtains fresh authorization after reset and independently checks it.
An old signed image plus the compromised routine key cannot start an update.
Expired/rebooted sessions need new operator authorization; identical transport
retries return cached replies instead of repeating erase/program effects.
Automatic revert to the previously confirmed image needs no operator secret.

Implemented off-device: fixed-size signed records, public-only Python verifier,
portable C authority gate, interactive encrypted-key tooling and allowlisted
public-release packaging. No real signing/pairing secrets have been generated or
provisioned. C signature/RNG callbacks still need a vetted board implementation.
Do not treat a compiled gate with host crypto as a secure bootloader deployment.
Corrupt provisioning must fail closed even when both application slots are invalid.

`operator.py` prompts on a terminal, disables core dumps and rejects unencrypted
key files, symlinks, permissive modes and multiple hard links. Python does not
guarantee complete RAM zeroization; the development machine remains trusted.
`release.py check-staged` examines index contents, not just working-tree files.
It is an explicit check, not an installed Git hook or universal secret detector.
Release packaging accepts only the image, signed manifest and public key; it
does not recursively copy this checkout or the private directory.

## Residual risks

Physical CAN jamming, arbitration starvation and OEM signal spoofing remain.
Unsigned telemetry is intentionally spoofable: logging/UI only. A gateway MAC
cannot prove that an original OEM sensor was truthful. Debug/flash extraction
and physical key theft require a separate MCU protection design; do not enable
irreversible RDP2 while exploring it. No software scheme makes a hostile shared
CAN node electrically harmless.

The simulator uses public deterministic fixture keys. They must never be used
for provisioning. No real pairing/signing secrets have been generated here.
