$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$projectDir = Split-Path -Parent $PSScriptRoot
$toolsDir = Join-Path $projectDir '.local-tools'
$ocrDir = Join-Path $toolsDir 'tesseract'
$ocrExe = Join-Path $ocrDir 'tesseract.exe'
New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null

if (-not (Test-Path -LiteralPath $ocrExe)) {
    $installerPath = Join-Path $toolsDir 'tesseract-5.5.3-setup.exe'
    $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/tesseract-ocr/tesseract/releases/tags/5.5.3'
    $asset = $release.assets | Where-Object name -eq 'tesseract-ocr-w64-setup-5.5.3.20260724.exe' | Select-Object -First 1
    if (-not $asset) { throw 'Official Tesseract installer was not found.' }
    Write-Host 'Downloading the official Tesseract Windows installer...'
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $installerPath
    if ($asset.digest -and $asset.digest.StartsWith('sha256:')) {
        $actualHash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -ne $asset.digest.Substring(7)) { throw 'Installer checksum mismatch.' }
    }
    Write-Host 'Installing Tesseract into the project local tools directory...'
    $installer = Start-Process -FilePath $installerPath -ArgumentList @('/S', '/CURRENTUSER', "/D=$ocrDir") -WindowStyle Hidden -Wait -PassThru
    if ($installer.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $ocrExe)) {
        throw "Tesseract installation failed (exit code $($installer.ExitCode))."
    }
}

$tessdata = Join-Path $ocrDir 'tessdata'
New-Item -ItemType Directory -Path $tessdata -Force | Out-Null
foreach ($language in @('tur', 'eng', 'osd')) {
    $languagePath = Join-Path $tessdata "$language.traineddata"
    if (-not (Test-Path -LiteralPath $languagePath) -or (Get-Item -LiteralPath $languagePath).Length -lt 100000) {
        Write-Host "Downloading OCR language: $language"
        Invoke-WebRequest -Uri "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/$language.traineddata" -OutFile $languagePath
    }
}
& $ocrExe --version
if ($LASTEXITCODE -ne 0) { throw 'Tesseract did not start.' }
Push-Location $projectDir
try {
    # A relative ASCII path also works when the project directory contains Turkish letters.
    $languages = & $ocrExe --tessdata-dir '.local-tools/tesseract/tessdata' --list-langs
    if ($LASTEXITCODE -ne 0 -or 'tur' -notin $languages -or 'eng' -notin $languages) {
        throw 'OCR language validation failed.'
    }
    $languages | Write-Host
} finally {
    Pop-Location
}
Write-Host 'Local OCR installation is ready.'
