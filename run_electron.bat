@echo off
setlocal
cd /d "%~dp0"
set "DSAKIKO_ELECTRON_MODE=1"
set "DSAKIKO_ELECTRON_SESSION_FILE=%~dp0.electron-bridge-session.json"
if "%DSAKIKO_ELECTRON_BRIDGE_READY_TIMEOUT_SECONDS%"=="" set "DSAKIKO_ELECTRON_BRIDGE_READY_TIMEOUT_SECONDS=45"
set "DSAKIKO_ELECTRON_LAUNCH_MODE=production"
if /I "%~1"=="--dev" set "DSAKIKO_ELECTRON_LAUNCH_MODE=development"

pushd "%~dp0electron_frontend"
node scripts\ensure-production-runtime.mjs --dependencies-current
if errorlevel 1 (
  echo Installing the lockfile-pinned Electron runtime and build dependencies...
  call npm ci --include=dev
  if errorlevel 1 (
    echo Electron dependency installation failed. Install Node.js LTS and retry.
    popd
    exit /b 1
  )
  rem Electron 44 no longer declares a package postinstall hook; fetch its
  rem platform binary explicitly before accepting the dependency install.
  call node node_modules\electron\install.js
  if errorlevel 1 (
    echo Electron binary download failed. Check network/mirror settings and retry.
    popd
    exit /b 1
  )
  node scripts\ensure-production-runtime.mjs --dependencies-write
  if errorlevel 1 (
    echo Electron dependency freshness stamp could not be written.
    popd
    exit /b 1
  )
  node scripts\ensure-production-runtime.mjs --dependencies-current
  if errorlevel 1 (
    echo Electron runtime executable is missing after npm ci. Check network/mirror settings and retry.
    popd
    exit /b 1
  )
)
if "%DSAKIKO_ELECTRON_LAUNCH_MODE%"=="production" (
  node scripts\ensure-production-runtime.mjs
  if errorlevel 1 (
    echo Electron production output is missing or stale. Run "npm run build" in electron_frontend after updating the application.
    popd
    exit /b 1
  )
) else (
  node scripts\ensure-production-runtime.mjs
  if errorlevel 1 (
    echo Building Electron development output from the synchronized source...
    call npm run build
    if errorlevel 1 (
      echo Electron development build failed.
      popd
      exit /b 1
    )
    node scripts\ensure-production-runtime.mjs --write
    if errorlevel 1 (
      echo Electron development build verification failed.
      popd
      exit /b 1
    )
  )
)
popd

echo ========================================
echo   D_sakiko - Qt backend + Electron pet
echo ========================================
echo.

if exist "runtime\python.exe" (
  set "DSAKIKO_ELECTRON_PYTHON=%~dp0runtime\python.exe"
) else if exist ".venv\Scripts\python.exe" (
  set "DSAKIKO_ELECTRON_PYTHON=%~dp0.venv\Scripts\python.exe"
) else (
  set "DSAKIKO_ELECTRON_PYTHON=python"
)

if exist "%DSAKIKO_ELECTRON_SESSION_FILE%" (
  "%DSAKIKO_ELECTRON_PYTHON%" "%~dp0tools\probe_electron_bridge.py" --session "%DSAKIKO_ELECTRON_SESSION_FILE%" --timeout 0.75 >nul 2>&1
  if not errorlevel 1 (
    set "DSAKIKO_ELECTRON_REUSE_BACKEND=1"
    echo Reusing authenticated Electron bridge backend.
  )
)

if not defined DSAKIKO_ELECTRON_REUSE_BACKEND (
  if exist "runtime\python.exe" (
    start "D_sakiko backend" /d "%~dp0GPT_SoVITS" cmd /k "..\runtime\python.exe main2.py"
  ) else if exist ".venv\Scripts\python.exe" (
    start "D_sakiko backend" /d "%~dp0GPT_SoVITS" cmd /k ""%~dp0.venv\Scripts\python.exe" main2.py"
  ) else (
    start "D_sakiko backend" /d "%~dp0GPT_SoVITS" cmd /k "python main2.py"
  )
)

if "%DSAKIKO_ELECTRON_LAUNCH_MODE%"=="development" (
  start "D_sakiko Electron" /d "%~dp0electron_frontend" cmd /k "npm run dev"
) else (
  start "D_sakiko Electron" /d "%~dp0electron_frontend" cmd /k "npm run start"
)

echo Electron started immediately and will authenticate/reconnect when the bridge is ready.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$session = $env:DSAKIKO_ELECTRON_SESSION_FILE; $probe = Join-Path $PWD 'tools\probe_electron_bridge.py'; $python = $env:DSAKIKO_ELECTRON_PYTHON; $seconds = 45; $parsed = 0; if ([int]::TryParse($env:DSAKIKO_ELECTRON_BRIDGE_READY_TIMEOUT_SECONDS, [ref]$parsed) -and $parsed -gt 0) { $seconds = $parsed }; $deadline = [DateTime]::UtcNow.AddSeconds($seconds); do { try { if ((Test-Path -LiteralPath $session) -and (Test-Path -LiteralPath $probe)) { & $python $probe --session $session --timeout 0.75 *> $null; if ($LASTEXITCODE -eq 0) { Write-Output 'Electron bridge authenticated readiness confirmed.'; exit 0 } } } catch {} ; Start-Sleep -Milliseconds 250 } while ([DateTime]::UtcNow -lt $deadline); Write-Warning 'Bridge authenticated readiness was not observed before the upper bound; Electron remains open and will continue authenticated reconnects.'; exit 0"

echo Electron uses only the backend business-event stream; Pygame is disabled in this mode.
endlocal
