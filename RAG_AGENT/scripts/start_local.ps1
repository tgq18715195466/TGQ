$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Project virtual environment is missing. Follow README installation steps.'
}
$frontEnd = Join-Path $projectRoot 'web'
if (-not (Test-Path -LiteralPath (Join-Path $frontEnd 'node_modules'))) {
    & npm.cmd ci --prefix $frontEnd --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Unable to install frontend dependencies.' }
}
& npm.cmd run build --prefix $frontEnd
if ($LASTEXITCODE -ne 0) { throw 'React frontend build failed.' }
& $projectPython -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
& $projectPython scripts/setup_local_login.py
if ($LASTEXITCODE -ne 0) { throw 'Local login setup failed.' }
$ready = $false
try {
    $schema = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/openapi.json' -TimeoutSec 3
    if ('/api/v1/auth/login' -notin $schema.paths.PSObject.Properties.Name) {
        throw 'Port 8000 is occupied by an older or unrelated API. Restart it before logging in.'
    }
    $ready = $true
} catch {
    if ($_.Exception.Message -like '*occupied*') { throw }
}
if (-not $ready) {
    $runtimeDir = Join-Path $projectRoot '.runtime'
    New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
    $apiProcess = Start-Process -FilePath $projectPython `
        -ArgumentList '-m', 'uvicorn', 'app.api:app', '--host', '127.0.0.1', '--port', '8000' `
        -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runtimeDir 'api.stdout.log') `
        -RedirectStandardError (Join-Path $runtimeDir 'api.stderr.log')
    Set-Content -LiteralPath (Join-Path $runtimeDir 'api.pid') -Value $apiProcess.Id
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 500
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health/ready' -TimeoutSec 2
            if ($health.status -eq 'ok') { $ready = $true; break }
        } catch {}
        if ($apiProcess.HasExited) { break }
    }
    if (-not $ready) { throw 'API did not become ready. Check .runtime/api.stderr.log and PostgreSQL.' }
}
& $projectPython scripts/open_workspace.py
if ($LASTEXITCODE -ne 0) { throw 'Local login failed. Check README and .runtime/demo.json.' }
