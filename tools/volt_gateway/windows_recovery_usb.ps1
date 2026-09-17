# Elevated host-only preflight. Allow the two expected recovery personalities
# solely at the verified physical Panda port. No device command or force bind.
$ErrorActionPreference = 'Stop'
$tool = 'C:\Program Files\usbipd-win\usbipd.exe'
$s = (& $tool state | ConvertFrom-Json).Devices
$p = @($s | Where-Object { $_.InstanceId -eq 'USB\VID_BBAA&PID_DDCC\370022000651363038363036' })
if ($p.Count -ne 1 -or $p[0].BusId -ne '2-2' -or $p[0].IsForced) { throw 'Target identity/physical port mismatch.' }
$rule = @(Get-NetFirewallRule -DisplayName usbipd)
if ($rule.Count -ne 1) { throw 'Unexpected firewall rules.' }
$remote = @(($rule[0] | Get-NetFirewallAddressFilter).RemoteAddress)
if ($remote.Count -ne 1 -or $remote[0] -ne '172.21.244.169') { throw 'WSL-only firewall precondition failed.' }
foreach ($hardware in @('bbaa:ddee', '0483:df11')) {
  & $tool policy add --effect Allow --operation AutoBind --busid 2-2 --hardware-id $hardware
  if ($LASTEXITCODE -ne 0) { throw ('Failed recovery policy for ' + $hardware) }
}
