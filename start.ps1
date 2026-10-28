$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
$previousLocalHttp = [Environment]::GetEnvironmentVariable('AUTH_LOCAL_HTTP', 'Process')
$locationPushed = $false
$exitCode = 1

try {
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Virtual environment not found. In '$PSScriptRoot', run 'python -m venv .venv' and '.venv/Scripts/python.exe -m pip install -r requirements.txt', then retry .\start.ps1."
    }

    Push-Location -LiteralPath $PSScriptRoot -ErrorAction Stop
    $locationPushed = $true
    $env:AUTH_LOCAL_HTTP = '1'
    & $python -m flask --app app:create_app run --host 127.0.0.1 --port 5000 --no-debugger --no-reload
    $exitCode = $LASTEXITCODE
}
catch {
    Write-Error $_ -ErrorAction Continue
}
finally {
    [Environment]::SetEnvironmentVariable('AUTH_LOCAL_HTTP', $previousLocalHttp, 'Process')
    if ($locationPushed) {
        Pop-Location
    }
}

exit $exitCode
