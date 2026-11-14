$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
# Settings this launcher may set; the caller's values are restored on exit.
$managed = 'AUTH_LOCAL_HTTP', 'CLASSIFIER', 'STORAGE_DRIVER', 'DATABASE_URL', 'REDIS_URL', 'DEV_TOOLS'
$previous = @{}
foreach ($name in $managed) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
$locationPushed = $false
$exitCode = 1

try {
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Virtual environment not found. In '$PSScriptRoot', run 'python -m venv .venv', '.venv/Scripts/python.exe -m pip install -r requirements.txt -r requirements-model.txt' and '.venv/Scripts/python.exe -m pip install --no-deps -e ../sereno-model', then retry .\start.ps1."
    }

    Push-Location -LiteralPath $PSScriptRoot -ErrorAction Stop
    $locationPushed = $true
    $env:AUTH_LOCAL_HTTP = '1'
    # Serve the trained model (MODEL_PATH defaults to the sibling sereno-model
    # artifact) unless the caller already chose a classifier, e.g. CLASSIFIER=fake.
    if (-not $previous['CLASSIFIER']) {
        $env:CLASSIFIER = 'model'
    }
    # Store in PostgreSQL and cache in Redis (docker-compose.yml) unless the
    # caller already chose a driver, e.g. STORAGE_DRIVER=json for the JSON files.
    if (-not $previous['STORAGE_DRIVER']) {
        $env:STORAGE_DRIVER = 'postgres'
    }
    if ($env:STORAGE_DRIVER -eq 'postgres') {
        # Development-only user administration at /dev/users (no session,
        # loopback only) unless the caller already decided, e.g. DEV_TOOLS=0.
        # It needs the postgres driver. Never enable it outside this machine.
        if (-not $previous['DEV_TOOLS']) {
            $env:DEV_TOOLS = '1'
        }
        # docker compose reads .env by itself; the backend takes its two URLs
        # from the same file unless the caller already set them.
        $envFile = Join-Path $PSScriptRoot '.env'
        if (Test-Path -LiteralPath $envFile -PathType Leaf) {
            foreach ($line in Get-Content -LiteralPath $envFile) {
                if ($line -match '^\s*(DATABASE_URL|REDIS_URL)\s*=\s*(.+?)\s*$' -and -not $previous[$Matches[1]]) {
                    [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
                }
            }
        }
        $jsonHint = "To run on the JSON files instead: `$env:STORAGE_DRIVER = 'json'; .\start.ps1"
        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
            throw "Docker was not found. Install Docker Desktop, then retry .\start.ps1. $jsonHint"
        }
        & docker info *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "Docker is not running. Start Docker Desktop, wait until it is ready, then retry .\start.ps1. $jsonHint"
        }
        # Starts PostgreSQL and Redis if needed and waits until both are healthy.
        & docker compose up -d --wait
        if ($LASTEXITCODE -ne 0) {
            throw "PostgreSQL and Redis did not become healthy. Check 'docker compose ps' and 'docker compose logs' in '$PSScriptRoot'. $jsonHint"
        }
    }
    & $python -m flask --app app:create_app run --host 127.0.0.1 --port 5000 --no-debugger --no-reload
    $exitCode = $LASTEXITCODE
}
catch {
    Write-Error $_ -ErrorAction Continue
}
finally {
    foreach ($name in $managed) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
    if ($locationPushed) {
        Pop-Location
    }
}

exit $exitCode
