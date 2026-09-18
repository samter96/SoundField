@echo off
rem SoundField PoC launcher.
rem
rem app\soundfield.exe is the release build: the UI is bundled inside the exe, so
rem it needs no dev server and shows no console window. It also lives outside
rem target\ so cargo clean cannot wipe it. Always prefer it.
rem
rem The debug build loads http://localhost:1420 instead. Without Vite running it
rem shows Chrome's "connection refused" page inside the app window, which looks
rem like the app is broken. That is why the release build comes first here.
cd /d "%~dp0"

rem Keep app\soundfield.exe up to date with the newest release build.
rem
rem Why: app\ is the installer staging copy, and the release build lands in
rem src-tauri\target\release. After "npx tauri build --no-bundle" the two drift
rem apart, and because this launcher prefers app\ it would start the OLD exe.
rem That actually happened - a logo change was invisible for a whole session
rem (2026-09-09). xcopy /D copies only when the source is newer, so this is a
rem no-op on an already-current file.
if exist "src-tauri\target\release\soundfield.exe" (
  xcopy /D /Y /Q "src-tauri\target\release\soundfield.exe" "app\" >nul 2>&1
)

rem Same for the Python sidecars. build_bridge.ps1 writes them to
rem src-tauri\bridge-dist\sf_bridge, and app\bridge is only the staging copy,
rem so a rebuilt sidecar goes unnoticed exactly like the exe did. /E keeps
rem _internal, /D copies only newer files - a no-op when app\bridge is current.
if exist "src-tauri\bridge-dist\sf_bridge\sf_bridge.exe" (
  xcopy /D /Y /Q /E /I "src-tauri\bridge-dist\sf_bridge\*" "app\bridge\" >nul 2>&1
)

rem Both copies above can fail SILENTLY. If the app is already running Windows
rem locks app\soundfield.exe (and the sidecars under app\bridge), xcopy prints
rem "Sharing violation", and >nul hides it - so the launcher would start the OLD
rem exe again, which is the very thing this file exists to prevent. Measured on
rem 2026-09-09 with the app open.
rem
rem So probe once more with /L (list only, no copy): a non-zero count means the
rem staging copy is still behind, and we run the newest file from where it was
rem built instead.
rem
rem target\release has no bridge\ folder next to it, so that exe would fall back
rem to a dev python for its sidecars (see bridge_dir in src-tauri/src/lib.rs).
rem SOUNDFIELD_BRIDGE_EXE names the bridge explicitly, so whichever exe starts,
rem it uses the newest packaged sidecars.
set "SF_EXE=app\soundfield.exe"
set "SF_BRIDGE="
if exist "app\bridge\sf_bridge.exe" set "SF_BRIDGE=%~dp0app\bridge\sf_bridge.exe"

if exist "src-tauri\bridge-dist\sf_bridge\sf_bridge.exe" (
  for /f %%n in ('xcopy /D /L /Y "src-tauri\bridge-dist\sf_bridge\sf_bridge.exe" "app\bridge\" 2^>nul ^| find /c "sf_bridge.exe"') do (
    if not "%%n"=="0" set "SF_BRIDGE=%~dp0src-tauri\bridge-dist\sf_bridge\sf_bridge.exe"
  )
)
if exist "src-tauri\target\release\soundfield.exe" (
  for /f %%n in ('xcopy /D /L /Y "src-tauri\target\release\soundfield.exe" "app\" 2^>nul ^| find /c "soundfield.exe"') do (
    if not "%%n"=="0" set "SF_EXE=src-tauri\target\release\soundfield.exe"
  )
)
if not exist "%SF_EXE%" set "SF_EXE=src-tauri\target\release\soundfield.exe"
if defined SF_BRIDGE set "SOUNDFIELD_BRIDGE_EXE=%SF_BRIDGE%"
if exist "%SF_EXE%" (
  start "" "%SF_EXE%"
  exit /b 0
)
if not exist "src-tauri\target\debug\soundfield.exe" (
  echo No app build found. Build one first:
  echo   npm run build
  echo   cd src-tauri ^&^& cargo build --release
  pause
  exit /b 1
)

netstat -ano | findstr /r /c:":1420 .*LISTENING" >nul
if not errorlevel 1 goto ready
start "SoundField dev server" /min cmd /c "npm run dev"
rem poll instead of a blind fixed wait
for /l %%i in (1,1,40) do (
  netstat -ano | findstr /r /c:":1420 .*LISTENING" >nul
  if not errorlevel 1 goto ready
  ping -n 2 127.0.0.1 >nul
)
:ready
start "" "src-tauri\target\debug\soundfield.exe"
exit /b 0
