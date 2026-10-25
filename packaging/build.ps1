<#
  se7en — full Windows build.

    powershell -ExecutionPolicy Bypass -File packaging\build.ps1
    powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Repo "moncompte/se7en"   # enables automatic updates

  Outputs (dist\):
    se7en\                      unpacked app (se7en.exe, se7en-cli.exe, bin\ with every component)
    se7en-Censored-Setup-<ver>.exe       full installer: send this one to people (works offline)
    se7en-Censored-Update-<ver>.exe      program-only package downloaded by automatic updates
    se7en-Censored-Portable-<ver>.zip    portable edition (data stays next to the exe)
    latest.json, SHA256SUMS.txt update feed + checksums (with -Repo)

  The update repository is remembered in packaging\update.json.
#>
param([switch]$SkipUI, [switch]$SkipTests, [switch]$NoInstaller, [switch]$NoPortable, [string]$Repo = "", [string]$Notes = "")
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Py = "backend\.venv\Scripts\python.exe"

function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }

Step "Python environment"
if (-not (Test-Path $Py)) { python -m venv backend\.venv }
& $Py -m pip install --upgrade pip -q
& $Py -m pip install -q -r backend\requirements-dev.txt  # "python -m": the pip.exe launcher breaks when the folder moves
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
$Version = (& $Py packaging\release.py version).Trim()
Write-Host "se7en Censored $Version"

if (-not $SkipTests) {
  Step "Backend tests"
  Push-Location backend
  & .\.venv\Scripts\python.exe -m pytest -q -p no:warnings
  if ($LASTEXITCODE -ne 0) { throw "Backend tests failed" }
  Pop-Location
}

if (-not $SkipUI) {
  Step "Frontend build"
  Push-Location frontend
  npm ci --no-fund --no-audit
  if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
  if (-not $SkipTests) { npm test; if ($LASTEXITCODE -ne 0) { throw "Frontend tests failed" } }
  npm run build
  if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" }
  Pop-Location
}

Step "Icon and installer artwork"
& $Py packaging\make_icon.py

Step "PyInstaller"
if (Test-Path "dist\se7en-censored") { [System.IO.Directory]::Delete((Resolve-Path "dist\se7en-censored"), $true) }
& $Py -m PyInstaller packaging\medialab.spec --noconfirm --clean --distpath dist --workpath build --log-level WARN
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Copy-Item -Force packaging\se7en-censored.exe.config dist\se7en-censored\se7en-censored.exe.config
Copy-Item -Force packaging\LISEZ-MOI.txt dist\se7en-censored\LISEZ-MOI.txt
foreach ($item in @("plugins", "integrations", "README.md", "LICENSE")) {
  if (Test-Path $item) { Copy-Item -Recurse -Force $item "dist\se7en-censored\$item" }
}

Step "Update feed"
$cfg = "packaging\update.json"
# written without a BOM: Set-Content -Encoding utf8 adds one, which se7en 1.1.0 - 1.2.0 could not read
# (their automatic updates were silently off)
$noBom = New-Object System.Text.UTF8Encoding($false)
if ($Repo) { [IO.File]::WriteAllText((Join-Path $Root $cfg), (@{ repo = $Repo } | ConvertTo-Json), $noBom) }
if (Test-Path $cfg) {
  $Repo = (Get-Content $cfg -Raw | ConvertFrom-Json).repo
  [IO.File]::WriteAllText((Join-Path $Root "dist\se7en-censored\update.json"), (@{ repo = $Repo } | ConvertTo-Json), $noBom)
}
if ($Repo) { Write-Host "Automatic updates from github.com/$Repo" -ForegroundColor Green }
else { Write-Host "No update repository configured (pass -Repo owner/name)" -ForegroundColor Yellow }

Step "Bundling components (FFmpeg, Deno, Whisper, llama.cpp, face detector)"
& $Py packaging\fetch_components.py
if ($LASTEXITCODE -ne 0) { throw "Component bundling failed" }

Step "Smoke test (frozen app, API only)"
$env:SE7ENCENSORED_DATA_DIR = Join-Path $env:TEMP "se7en-smoke"   # never touch the user's real data
$p = Start-Process -FilePath "dist\se7en-censored\se7en-censored.exe" -ArgumentList "--server","--port","8799" -PassThru -WindowStyle Hidden
try {
  $ok = $false
  for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 500
    try { $r = Invoke-RestMethod "http://127.0.0.1:8799/api/health"; if ($r.ok) { $ok = $true; break } } catch {}
  }
  if (-not $ok) { throw "Frozen app did not answer /api/health" }
  Write-Host "Frozen app healthy: $($r.app) $($r.version)" -ForegroundColor Green
} finally {
  Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
  Remove-Item Env:\SE7ENCENSORED_DATA_DIR
}

if (-not $NoPortable) {
  Step "Portable edition"
  & $Py packaging\release.py portable
}

if (-not $NoInstaller) {
  Step "Installers"
  $iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }
  & $iscc /Q "/DAppVersion=$Version" packaging\installer.iss
  if ($LASTEXITCODE -ne 0) { throw "Inno Setup (full) failed" }
  & $iscc /Q "/DAppVersion=$Version" /DUpdateOnly packaging\installer.iss
  if ($LASTEXITCODE -ne 0) { throw "Inno Setup (update) failed" }
  Get-ChildItem dist\se7en-Censored-*.exe | ForEach-Object { Write-Host ("{0}  {1:N0} MB" -f $_.Name, ($_.Length / 1MB)) -ForegroundColor Green }
}

if ($Repo) {
  Step "Update manifest"
  & $Py packaging\release.py manifest $Repo $Notes
}
Write-Host "`nDone: se7en Censored $Version" -ForegroundColor Green
