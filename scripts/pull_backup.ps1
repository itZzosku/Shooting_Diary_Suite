# Pulls a fresh backup of the shooting diary from the server onto this Windows PC (run daily by Task Scheduler).
# Each run: one dated folder under $Dest with every account's JSON + .db, users.db, clubs.db and library.db;
# older ones are thinned out by prune_backup.ps1 (14 daily, 8 weekly, 12 monthly). The server side is scripts/backup_stream.sh.
#
# One-time setup (PowerShell):
#   1. ssh-keygen -t ed25519 -N '""' -C diary-backup-pull -f $env:USERPROFILE\.ssh\diary_backup
#   2. On the server, add the .pub key to /root/.ssh/authorized_keys with the restriction:
#        restrict,command="/root/Shooting_Diary_Suite/scripts/backup_stream.sh" ssh-ed25519 AAAA… diary-backup-pull
#   3. Run this script once by hand to check it: a dated folder appears under $Dest, pull.log says OK.
#   4. Schedule it daily at 04:00:
#        $a = New-ScheduledTaskAction -Execute powershell.exe -Argument '-NoProfile -ExecutionPolicy Bypass -File "C:\path\to\pull_backup.ps1"'
#        Register-ScheduledTask -TaskName "Shooting diary backup" -Action $a -Trigger (New-ScheduledTaskTrigger -Daily -At 4am) `
#          -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable)
#      (-StartWhenAvailable: if the PC was off at 04:00, it runs when the PC is next on.)
param(
  [string]$Server = "root@10.30.2.2",
  [string]$Key = "$env:USERPROFILE\.ssh\diary_backup",
  [string]$Dest = "$env:USERPROFILE\Backups\ShootingDiary",
  [int]$Daily = 14, [int]$Weekly = 8, [int]$Monthly = 12   # kept: see prune_backup.ps1
)
$ErrorActionPreference = "Stop"
$stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
$out = Join-Path $Dest $stamp
$tar = Join-Path $Dest "$stamp.tar"
$log = Join-Path $Dest "pull.log"
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
function Log($m) { "$(Get-Date -Format s) $m" | Tee-Object -FilePath $log -Append }

try {
  # cmd's > keeps the bytes as they are (PowerShell's own redirection would re-encode the tar)
  # accept-new: the server's key is learned on the first run, a changed key later is refused
  cmd /c "ssh -i `"$Key`" -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new $Server backup > `"$tar`" 2> `"$tar.err`""
  if ($LASTEXITCODE -ne 0) { throw "ssh failed ($LASTEXITCODE): $(Get-Content "$tar.err" -Raw)" }
  New-Item -ItemType Directory -Force -Path $out | Out-Null
  tar -xf $tar -C $out
  if ($LASTEXITCODE -ne 0) { throw "tar could not unpack $tar" }
  # a backup without the accounts file or any account is not a backup
  if (-not (Get-ChildItem $out -Filter "users-*.db") -or -not (Get-ChildItem $out -Filter "diary-*.json")) {
    throw "backup incomplete: $((Get-ChildItem $out).Name -join ', ')"
  }
  Remove-Item $tar, "$tar.err" -Force
  $mb = "{0:N1}" -f ((Get-ChildItem $out -Recurse | Measure-Object Length -Sum).Sum / 1MB)
  Log "OK $stamp ($mb MB, $((Get-ChildItem $out).Count) files)"
  # thin out older pulls (only after a good one): see prune_backup.ps1
  & (Join-Path $PSScriptRoot "prune_backup.ps1") -Dest $Dest -Daily $Daily -Weekly $Weekly -Monthly $Monthly |
    ForEach-Object { Log $_ }
} catch {
  Log "FAILED: $_"
  if (Test-Path $out) { Remove-Item $out -Recurse -Force }
  exit 1
}
