# Switch Windows Spectre v2 / Meltdown mitigations using the registry values
# documented by Microsoft (KB4072698 / KB4073119):
#   off     : FeatureSettingsOverride = 3, FeatureSettingsOverrideMask = 3
#   restore : remove both values = Windows default (mitigations on)
#   status  : only show the current values
# Needs an administrator PowerShell. A reboot is required after a change.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File set_mitigations.ps1 off|restore|status

param(
    [ValidateSet('off', 'restore', 'status')]
    [string]$Mode = 'status'
)

$key = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management'
$names = 'FeatureSettingsOverride', 'FeatureSettingsOverrideMask'

function Show-Values {
    $p = Get-ItemProperty -Path $key
    foreach ($n in $names) {
        $v = $p.$n
        if ($null -eq $v) { $v = '(not set = Windows default)' }
        Write-Host ("  {0} = {1}" -f $n, $v)
    }
}

Write-Host 'Current registry values:'
Show-Values
if ($Mode -eq 'status') { exit 0 }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Host ''
    Write-Host 'ERROR: run this as administrator (right click the .bat file, Run as administrator).'
    exit 1
}

if ($Mode -eq 'off') {
    Set-ItemProperty -Path $key -Name 'FeatureSettingsOverride' -Value 3 -Type DWord
    Set-ItemProperty -Path $key -Name 'FeatureSettingsOverrideMask' -Value 3 -Type DWord
    Write-Host ''
    Write-Host 'Mitigations will be OFF after reboot. The PC is less protected until you restore them.'
}
else {
    foreach ($n in $names) {
        if ($null -ne (Get-ItemProperty -Path $key).$n) {
            Remove-ItemProperty -Path $key -Name $n
        }
    }
    Write-Host ''
    Write-Host 'Mitigations restored to the Windows default (ON) after reboot.'
}

Write-Host 'New registry values:'
Show-Values
Write-Host ''
Write-Host 'Now REBOOT the computer.'
