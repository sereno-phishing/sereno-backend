$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
$previousLocalHttp = [Environment]::GetEnvironmentVariable('AUTH_LOCAL_HTTP', 'Process')
$previousClassifier = [Environment]::GetEnvironmentVariable('CLASSIFIER', 'Process')
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
    if (-not $previousClassifier) {
        $env:CLASSIFIER = 'model'
    }
    & $python -m flask --app app:create_app run --host 127.0.0.1 --port 5000 --no-debugger --no-reload
    $exitCode = $LASTEXITCODE
}
catch {
    Write-Error $_ -ErrorAction Continue
}
finally {
    [Environment]::SetEnvironmentVariable('AUTH_LOCAL_HTTP', $previousLocalHttp, 'Process')
    [Environment]::SetEnvironmentVariable('CLASSIFIER', $previousClassifier, 'Process')
    if ($locationPushed) {
        Pop-Location
    }
}

exit $exitCode
