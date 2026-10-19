$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectRoot '.runtime'
$mineruDir = Join-Path $runtimeDir 'mineru'
New-Item -ItemType Directory -Force $mineruDir | Out-Null
$env:HF_HOME = Join-Path $mineruDir 'huggingface'
$env:MODELSCOPE_CACHE = Join-Path $mineruDir 'modelscope'
$env:TORCH_HOME = Join-Path $mineruDir 'torch'
$env:MINERU_TOOLS_CONFIG_JSON = Join-Path $mineruDir 'mineru.json'
$env:MINERU_API_OUTPUT_ROOT = Join-Path $mineruDir 'outputs'
$env:MINERU_MODEL_SOURCE = 'local'
$env:MINERU_API_MAX_CONCURRENT_REQUESTS = '1'
$env:TEMP = Join-Path $runtimeDir 'tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
$mineruExe = Join-Path $runtimeDir 'mineru-venv\Scripts\mineru-api.exe'
if (-not (Test-Path -LiteralPath $mineruExe)) { throw 'Install MinerU in .runtime/mineru-venv first.' }
$existing = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
if ($existing) { throw 'Port 8001 is already listening. Verify the existing service before starting another.' }
$service = Start-Process -FilePath $mineruExe -ArgumentList '--host', '127.0.0.1', '--port', '8001' `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runtimeDir 'mineru.stdout.log') `
    -RedirectStandardError (Join-Path $runtimeDir 'mineru.stderr.log')
Set-Content -LiteralPath (Join-Path $runtimeDir 'mineru.pid') -Value $service.Id
Write-Output "MinerU process started: $($service.Id). Check http://127.0.0.1:8001/docs and the logs."
