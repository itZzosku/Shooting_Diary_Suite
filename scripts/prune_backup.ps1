# Thins out the pulled backups (the dated yyyy-MM-dd_HHmm folders pull_backup.ps1 makes); nothing else is touched.
# Keeps the newest folder of each of the last $Daily days, of each of the last $Weekly weeks and of each of the
# last $Monthly months – by default two weeks day by day, two months week by week, a year month by month.
# The newest folder is always kept. pull_backup.ps1 runs this after each pull; by hand:
#   .\prune_backup.ps1 -Dest C:\Users\me\Desktop\Diary_Backup -WhatIf     (shows what would go)
[CmdletBinding(SupportsShouldProcess)]
param(
  [Parameter(Mandatory)][string]$Dest,
  [int]$Daily = 14,
  [int]$Weekly = 8,
  [int]$Monthly = 12
)
$ErrorActionPreference = "Stop"
$all = Get-ChildItem $Dest -Directory | Where-Object Name -match '^\d{4}-\d{2}-\d{2}_\d{4}$' |
  ForEach-Object { [pscustomobject]@{ Dir = $_; When = [datetime]::ParseExact($_.Name, "yyyy-MM-dd_HHmm", $null) } } |
  Sort-Object When -Descending
if (-not $all) { return }

$keep = @{ $all[0].Dir.FullName = $true }
function KeepNewestPer($key, $count) {
  # $all is newest first: the first folder seen for a key is that period's newest
  $seen = @{}
  foreach ($b in $all) {
    $k = & $key $b.When
    if ($seen.ContainsKey($k)) { continue }
    if ($seen.Count -ge $count) { break }
    $seen[$k] = $true
    $keep[$b.Dir.FullName] = $true
  }
}
KeepNewestPer { param($d) $d.ToString("yyyy-MM-dd") } $Daily
KeepNewestPer { param($d) $d.Date.AddDays(-(([int]$d.DayOfWeek + 6) % 7)).ToString("yyyy-MM-dd") } $Weekly   # week from Monday
KeepNewestPer { param($d) $d.ToString("yyyy-MM") } $Monthly

foreach ($b in $all) {
  if (-not $keep.ContainsKey($b.Dir.FullName) -and $PSCmdlet.ShouldProcess($b.Dir.Name, "remove backup")) {
    Remove-Item $b.Dir.FullName -Recurse -Force
    Write-Output "Pruned $($b.Dir.Name)"
  }
}
