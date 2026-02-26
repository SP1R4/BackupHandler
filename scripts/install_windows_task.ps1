# Backup Handler - Windows Task Scheduler Registration
# Run this script in PowerShell as Administrator

$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$PythonExe = Join-Path $ProjectDir "venv\Scripts\python.exe"
$MainScript = Join-Path $ProjectDir "main.py"
$TaskName = "BackupHandler"

Write-Host "Backup Handler - Windows Task Scheduler Setup" -ForegroundColor Green
Write-Host "Project directory: $ProjectDir"
Write-Host ""

# Verify venv exists
if (-not (Test-Path $PythonExe)) {
    Write-Host "Error: Python venv not found at $PythonExe" -ForegroundColor Red
    Write-Host "Run 'python -m venv venv' in the project directory first." -ForegroundColor Red
    exit 1
}

# Verify main.py exists
if (-not (Test-Path $MainScript)) {
    Write-Host "Error: main.py not found at $MainScript" -ForegroundColor Red
    exit 1
}

# Remove existing task if present
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removing existing task '$TaskName'..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Create the scheduled task
$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "--scheduled" `
    -WorkingDirectory $ProjectDir

$Trigger = New-ScheduledTaskTrigger -AtStartup

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Backup Handler - Scheduled backup service" `
    -RunLevel Highest

Write-Host ""
Write-Host "Task '$TaskName' registered successfully." -ForegroundColor Green
Write-Host ""
Write-Host "Useful commands:" -ForegroundColor Green
Write-Host "  Get-ScheduledTask -TaskName $TaskName"
Write-Host "  Start-ScheduledTask -TaskName $TaskName"
Write-Host "  Stop-ScheduledTask -TaskName $TaskName"
Write-Host "  Unregister-ScheduledTask -TaskName $TaskName"
