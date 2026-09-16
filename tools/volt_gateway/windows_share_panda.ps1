param(
  [Parameter(Mandatory=$true)]
  [string]$WslIPv4
)
# Elevated HOST configuration only: narrowly scope USB/IP, share exact Panda.
# No force-bind, auto-bind, Panda vendor command, reboot, or flash.
$ErrorActionPreference = 'Stop'
$identity = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw 'Administrator PowerShell required for firewall scope and USB sharing.'
}
$address = $null
if (-not [Net.IPAddress]::TryParse($WslIPv4, [ref]$address) -or
    $address.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or
    $WslIPv4 -notmatch '^172\.(1[6-9]|2[0-9]|3[01])\.\d+\.\d+$') {
  throw 'Expected the independently verified WSL NAT IPv4 address.'
}
$tool = 'C:\Program Files\usbipd-win\usbipd.exe'
$raw = & $tool state
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect USB/IP device state.' }
$state = $raw | ConvertFrom-Json
$target = @($state.Devices | Where-Object {
  $_.InstanceId -eq 'USB\VID_BBAA&PID_DDCC\370022000651363038363036'
})
if ($target.Count -ne 1 -or $target[0].BusId -notmatch '^\d+-\d+$' -or $target[0].IsForced) {
  throw 'Expected exactly one present, non-force-bound labeled Panda.'
}
$rules = @(Get-NetFirewallRule -DisplayName usbipd)
if ($rules.Count -ne 1 -or $rules[0].Direction -ne 'Inbound' -or $rules[0].Action -ne 'Allow') {
  throw 'Unexpected USB/IP firewall rules; review before changing anything.'
}
$old = $rules[0] | Get-NetFirewallAddressFilter
$report = [ordered]@{
  schema=1; timestamp_utc=[DateTime]::UtcNow.ToString('o')
  target=$target[0].InstanceId; busid=$target[0].BusId
  firewall_rule=$rules[0].Name; previous_remote_address=@($old.RemoteAddress)
  requested_remote_address=$WslIPv4; complete=$false
}
$log = Join-Path $env:LOCALAPPDATA ('Temp\voltgw-usbipd-' + [Guid]::NewGuid().ToString() + '.json')
try {
  Set-NetFirewallRule -Name $rules[0].Name -RemoteAddress $WslIPv4
  $actual = Get-NetFirewallRule -Name $rules[0].Name | Get-NetFirewallAddressFilter
  $remote = @($actual.RemoteAddress)
  if ($remote.Count -ne 1 -or $remote[0] -ne $WslIPv4) {
    throw 'Firewall scope verification failed.'
  }
  & $tool bind --busid $target[0].BusId
  if ($LASTEXITCODE -ne 0) { throw 'USB/IP bind failed; firewall remains restricted.' }
  $report.complete = $true
} catch {
  $report['error'] = $_.Exception.Message
  throw
} finally {
  $report | ConvertTo-Json -Depth 4 | Out-File -LiteralPath $log -Encoding utf8
  Write-Output ('Host setup report: ' + $log)
}
