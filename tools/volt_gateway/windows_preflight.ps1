param()
# Read-only host/device inventory. No reset, USB OUT, driver install, or flash.
$ErrorActionPreference = 'Stop'
$panda = @(Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match '^USB\\VID_(BBAA|0483)&PID_' })
$devices = @($panda | ForEach-Object {
  $p = Get-ItemProperty -LiteralPath ('HKLM:\SYSTEM\CurrentControlSet\Enum\' + $_.InstanceId)
  [ordered]@{ instance_id=$_.InstanceId; status=$_.Status; name=$_.FriendlyName; service=$p.Service }
})
$cli = 'C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe'
$disk = Get-PSDrive C
$tool = $null
if (Test-Path -LiteralPath $cli) {
  $sig = Get-AuthenticodeSignature -LiteralPath $cli
  $tool = [ordered]@{
    path=$cli; sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $cli).Hash
    version=(Get-Item -LiteralPath $cli).VersionInfo.FileVersion
    authenticode_status=[string]$sig.Status
  }
}
[ordered]@{
  schema=1; usb_owner='Windows'; devices=$devices; cube_programmer=$tool
  c_free_bytes=$disk.Free
  installation_space_gate_bytes=5GB
  installation_space_gate_met=($disk.Free -ge 5GB)
  device_changed=$false
} | ConvertTo-Json -Depth 5
