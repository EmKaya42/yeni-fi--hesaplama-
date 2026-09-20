param([switch]$Stop, [switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectDir 'data\deneme'
$statePath = Join-Path $runtimeDir 'server.json'
$appPath = Join-Path $projectDir 'app.py'
$pythonPath = Join-Path $projectDir '.venv\Scripts\python.exe'
$ocrModelDir = Join-Path $projectDir '.local-tools\paddle-models'
$port = 5055
$baseUrl = "http://127.0.0.1:$port"

function Get-PreviewProcess {
    if (-not (Test-Path -LiteralPath $statePath)) { return $null }
    $saved = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    $running = Get-Process -Id $saved.processId -ErrorAction SilentlyContinue
    if (-not $running) { return $null }
    # A reused Windows PID must never stop an unrelated process.
    if ($running.StartTime.ToUniversalTime().Ticks.ToString() -ne $saved.startTicks) { return $null }
    $details = Get-CimInstance Win32_Process -Filter "ProcessId = $($saved.processId)"
    if (-not $details.CommandLine -or -not $details.CommandLine.Contains($appPath)) { return $null }
    return $running
}

try {
    $running = Get-PreviewProcess
    if ($Stop) {
        if ($running) {
            # Python's virtual-environment launcher can start a child interpreter.
            $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($running.Id)" |
                Where-Object { $_.CommandLine -and $_.CommandLine.Contains($appPath) })
            foreach ($child in $children) { Stop-Process -Id $child.ProcessId -ErrorAction SilentlyContinue }
            Stop-Process -Id $running.Id -ErrorAction SilentlyContinue
            Write-Host 'Deneme uygulamasi kapatildi. Belgeleriniz saklandi.'
        } else { Write-Host 'Deneme uygulamasi zaten kapali.' }
        if (Test-Path -LiteralPath $statePath) { Remove-Item -LiteralPath $statePath }
        exit 0
    }

    if (-not $running) {
        if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Python ortami bulunamadi: .venv' }
        if (-not (Test-Path -LiteralPath (Join-Path $ocrModelDir 'PP-OCRv6_rec_small.onnx'))) { throw 'Once python -m scripts.setup_paddle_ocr komutunu calistirin.' }
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $port)
        try { $listener.Start() } catch { throw "Port $port baska bir uygulama tarafindan kullaniliyor." }
        finally { $listener.Stop() }

        New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
        $env:ALLOW_LOCAL_AUTH = '1'
        $env:DATA_DIR = $runtimeDir
        $env:PORT = $port.ToString()
        $env:OCR_ENGINE = 'paddle'
        $env:OCR_MODEL_DIR = $ocrModelDir
        $env:OMP_THREAD_LIMIT = '1'
        $env:PYTHONUTF8 = '1'
        $running = Start-Process -FilePath $pythonPath -ArgumentList @('"' + $appPath + '"') -WorkingDirectory $projectDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeDir 'server.log') -RedirectStandardError (Join-Path $runtimeDir 'server-error.log')
        @{ processId = $running.Id; startTicks = $running.StartTime.ToUniversalTime().Ticks.ToString(); url = "$baseUrl/app" } |
            ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
    }

    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri "$baseUrl/health" -TimeoutSec 2
            if ($health.status -eq 'ok') { $ready = $true; break }
        } catch { Start-Sleep -Milliseconds 300 }
        if ($running.HasExited) { break }
    }
    if (-not $ready) { throw "Uygulama acilamadi. Ayrinti: $runtimeDir\server-error.log" }
    $settings = Invoke-RestMethod -Uri "$baseUrl/api/settings" -TimeoutSec 5
    if (-not $settings.selected) {
        Invoke-RestMethod -Uri "$baseUrl/api/settings" -Method Put -ContentType 'application/json' -Body '{"program":"custom"}' -TimeoutSec 5 | Out-Null
    }
    Write-Host "Deneme hazir: $baseUrl/app"
    Write-Host "Deneme belgeleri: $runtimeDir"
    if (-not $NoBrowser) { Start-Process "$baseUrl/app" }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
