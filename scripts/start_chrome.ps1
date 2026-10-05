# start_chrome.ps1 - open a normal (non-automated) Chrome with a dedicated profile
# for this project, with a debugging port so Playwright can attach later.
# Usage (from the project root):
#   powershell -ExecutionPolicy Bypass -File scripts\start_chrome.ps1

$candidates = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chrome = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) { Write-Error "chrome.exe not found in the usual locations."; exit 1 }

$profileDir = Join-Path (Get-Location) ".chrome-profile"
Write-Host "Starting Chrome with profile: $profileDir"
Start-Process $chrome -ArgumentList @(
    "--remote-debugging-port=9222",
    "--user-data-dir=`"$profileDir`"",
    "https://resumeworded.com"
)
Write-Host "Log into Resume Worded in that window, then run: python scripts\save_auth.py"
