# windows_metrics.ps1
# Gathers CPU, memory, and disk utilization from a Windows host and prints
# a single-line JSON object. Designed to run either locally (pwsh / powershell.exe)
# or as the ScriptBlock body of Invoke-Command from powershell_check.py.
#
# You can test this directly on a Windows box with:
#   powershell.exe -File windows_metrics.ps1
#
# Or test the remote path from any machine with WinRM access:
#   Invoke-Command -ComputerName <host> -Credential (Get-Credential) -FilePath windows_metrics.ps1

$ErrorActionPreference = "Stop"

try {
    $cpuLoad = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average

    $os = Get-CimInstance Win32_OperatingSystem
    $totalMemKb = $os.TotalVisibleMemorySize
    $freeMemKb  = $os.FreePhysicalMemory
    $memUsedPct = [math]::Round((($totalMemKb - $freeMemKb) / $totalMemKb) * 100, 1)

    $disks = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3"
    $diskList = @()
    $worstDiskPct = 0
    foreach ($d in $disks) {
        if ($d.Size -gt 0) {
            $usedPct = [math]::Round((($d.Size - $d.FreeSpace) / $d.Size) * 100, 1)
            $diskList += @{ drive = $d.DeviceID; used_pct = $usedPct }
            if ($usedPct -gt $worstDiskPct) { $worstDiskPct = $usedPct }
        }
    }

    $uptime = (Get-Date) - $os.LastBootUpTime

    $result = @{
        cpu_pct          = [math]::Round($cpuLoad, 1)
        memory_pct       = $memUsedPct
        disk_pct         = $worstDiskPct
        disks            = $diskList
        uptime_hours     = [math]::Round($uptime.TotalHours, 1)
        hostname         = $env:COMPUTERNAME
    }

    $result | ConvertTo-Json -Compress
}
catch {
    $err = @{ error = $_.Exception.Message }
    $err | ConvertTo-Json -Compress
    exit 1
}
