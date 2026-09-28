# End-to-end check of the Recorder Agent on the emulator: record the scripted flow, generate a
# test, replay it with Robot Framework. Repeats N times to catch flakiness.
param([int]$Runs = 2, [string]$Generator = "rules")

$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue
$robot = Join-Path $root ".venv\Scripts\robot.exe"
$summary = @()

for ($run = 1; $run -le $Runs; $run++) {
    $name = "verify_run$run"
    Write-Host "===== run ${run}: record"
    powershell -ExecutionPolicy Bypass -File tools\record_demo.ps1 -Name $name -Generator $Generator -Duration 45 2>&1 |
        Select-String "\[(recorder|generator)\] (recorded:|>>|input|\(|wrote|\d)" | ForEach-Object { $_.Line }
    Write-Host "===== run ${run}: replay"
    & $robot --pythonpath . --outputdir "results\$name" --console dotted "tests\robot\$name.robot" | Select-String "passed|FAIL|Error" | ForEach-Object { $_.Line }
    $summary += "run ${run}: robot exit code $LASTEXITCODE"
}
$summary
