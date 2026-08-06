$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"

function Assert-NativeSuccess([string]$Operation) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE."
    }
}

$projectRoot = $PSScriptRoot
$backendDir = Join-Path $projectRoot "backend"
$frontendDir = Join-Path $projectRoot "frontend"
$venvDir = Join-Path $backendDir ".venv"
$pythonExe = Join-Path $venvDir "Scripts\python.exe"

foreach ($command in @("python", "node", "npm.cmd", "ffmpeg", "ffprobe")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required command '$command' is not installed or not on PATH."
    }
}

$pythonVersion = [version]((& python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))").Trim())
Assert-NativeSuccess "Python version check"
if ($pythonVersion -lt [version]"3.11.0") {
    throw "Python 3.11+ is required; found $pythonVersion."
}

$nodeVersion = [version]((& node -p "process.versions.node").Trim())
Assert-NativeSuccess "Node version check"
if ($nodeVersion -lt [version]"20.9.0") {
    throw "Node 20.9+ is required by Next.js 16; found $nodeVersion."
}

if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    Write-Host "Creating the backend virtual environment..." -ForegroundColor Cyan
    python -m venv $venvDir
    Assert-NativeSuccess "Virtual environment creation"
}

$requirementsFile = Join-Path $backendDir "requirements.txt"
$requirementsMarker = Join-Path $venvDir ".requirements.sha256"
$requirementsHash = (Get-FileHash -LiteralPath $requirementsFile -Algorithm SHA256).Hash
$installedRequirementsHash = if (Test-Path -LiteralPath $requirementsMarker) {
    (Get-Content -LiteralPath $requirementsMarker -Raw).Trim()
} else { "" }
if ($requirementsHash -ne $installedRequirementsHash) {
    Write-Host "Synchronizing backend dependencies..." -ForegroundColor Cyan
    & $pythonExe -m pip install --disable-pip-version-check -r $requirementsFile
    Assert-NativeSuccess "Backend dependency installation"
    Set-Content -LiteralPath $requirementsMarker -Value $requirementsHash -Encoding ascii
}

$nodeModulesDir = Join-Path $frontendDir "node_modules"
$packageLock = Join-Path $frontendDir "package-lock.json"
$packageMarker = Join-Path $nodeModulesDir ".package-lock.sha256"
$packageHash = (Get-FileHash -LiteralPath $packageLock -Algorithm SHA256).Hash
$installedPackageHash = if (Test-Path -LiteralPath $packageMarker) {
    (Get-Content -LiteralPath $packageMarker -Raw).Trim()
} else { "" }
if (-not (Test-Path -LiteralPath $nodeModulesDir -PathType Container) -or $packageHash -ne $installedPackageHash) {
    Write-Host "Installing frontend dependencies..." -ForegroundColor Cyan
    Push-Location $frontendDir
    try {
        npm ci
        Assert-NativeSuccess "Frontend dependency installation"
        Set-Content -LiteralPath $packageMarker -Value $packageHash -Encoding ascii
    }
    finally {
        Pop-Location
    }
}

$backendProcess = $null
$frontendProcess = $null

Write-Host "Starting Music Video Studio in this terminal..." -ForegroundColor Green
Write-Host "Frontend: http://localhost:3000"
Write-Host "Backend:  http://localhost:8010"
Write-Host "API docs: http://localhost:8010/docs"
Write-Host "Press Ctrl+C to stop both services."
Write-Host ""

try {
    $backendProcess = Start-Process `
        -FilePath $pythonExe `
        -ArgumentList @(
            "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1",
            "--port", "8010",
            "--reload",
            "--timeout-graceful-shutdown", "300"
        ) `
        -WorkingDirectory $backendDir `
        -NoNewWindow `
        -PassThru

    $frontendProcess = Start-Process `
        -FilePath "npm.cmd" `
        -ArgumentList @("run", "dev") `
        -WorkingDirectory $frontendDir `
        -NoNewWindow `
        -PassThru

    while (-not $backendProcess.HasExited -and -not $frontendProcess.HasExited) {
        Start-Sleep -Milliseconds 500
        $backendProcess.Refresh()
        $frontendProcess.Refresh()
    }

    if ($backendProcess.HasExited -and $backendProcess.ExitCode -ne 0) {
        throw "Backend exited with code $($backendProcess.ExitCode)."
    }
    if ($frontendProcess.HasExited -and $frontendProcess.ExitCode -ne 0) {
        throw "Frontend exited with code $($frontendProcess.ExitCode)."
    }
}
finally {
    foreach ($process in @($frontendProcess, $backendProcess)) {
        if ($null -ne $process -and -not $process.HasExited) {
            # Both dev servers spawn child processes (Next and uvicorn's
            # reloader). Stop the exact process tree so Ctrl+C cannot leave a
            # hidden server occupying the port.
            & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
        }
    }
}
