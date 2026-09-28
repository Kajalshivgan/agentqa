# Records the scripted demo flow end to end on the emulator (no human needed):
# opens Settings, starts the recorder, waits for "ready", replays tools/simulate_user.py, stops.
param([string]$Name = "display_search_demo", [string]$Generator = "both", [int]$Duration = 45)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue
$py = Join-Path $root ".venv\Scripts\python.exe"
$adb = Join-Path $env:LOCALAPPDATA "Android\Sdk\platform-tools\adb.exe"
New-Item -ItemType Directory -Force results | Out-Null

# Clean start: close the keyboard/search overlay and both Settings processes, go home.
& $adb shell input keyevent KEYCODE_BACK
& $adb shell input keyevent KEYCODE_HOME
& $adb shell am force-stop com.android.settings.intelligence
& $adb shell am force-stop com.android.car.settings
& $adb shell monkey -p com.android.car.settings -c android.intent.category.LAUNCHER 1 | Out-Null
Start-Sleep 3

$log = "results\recorder_$Name.log"
$rec = Start-Process -FilePath $py -NoNewWindow -PassThru -RedirectStandardOutput $log -RedirectStandardError "$log.err" `
    -ArgumentList "-u", "-m", "agents.recorder_agent", "record", "--name", $Name, "--duration", $Duration, "--generator", $Generator

for ($i = 0; $i -lt 60 -and -not (Select-String -Path $log -Pattern "ready" -Quiet); $i++) { Start-Sleep -Milliseconds 500 }
& $py tools\simulate_user.py demo_display_flow
$rec.WaitForExit()
Get-Content $log
Get-Content "$log.err"
