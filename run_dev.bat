@echo off
rem SoundField dev launcher - hear py\app\ changes without rebuilding the bridge.
rem
rem ASCII ONLY. cmd reads .bat in the OEM codepage; UTF-8 Korean here is garbled
rem and the rem lines get executed as commands (hit on 2026-09-16).
rem
rem Why this exists:
rem   run_poc.bat starts app\soundfield.exe together with app\bridge\, the FROZEN
rem   python. Edits under py\app\ do not show up until the bridge is rebuilt.
rem
rem What this does:
rem   Copies the release exe into devrun\, which has no bridge\ next to it, so
rem   bridge_dir() in src-tauri\src\lib.rs finds no packaged bridge and falls back
rem   to src-tauri\python\*.py -> py\app\ sources directly.
rem   Verify: %LOCALAPPDATA%\SoundField\tauri_debug.log shows
rem   a line meaning "packaged bridge not found - using python instead".
rem
rem Limits:
rem   * Only python (py\app\) edits apply immediately.
rem   * The UI (src\*.tsx) is baked into the release exe. For UI changes run
rem     "npx tauri build --no-bundle" first, then this file again.
rem   * Dev python needs PyQt6 / numpy / sounddevice / soundfile / mutagen.
cd /d "%~dp0"

if not exist "src-tauri\target\release\soundfield.exe" (
  echo No release build. Build one first:
  echo   npx tauri build --no-bundle
  pause
  exit /b 1
)

rem The app is SINGLE INSTANCE (tauri_plugin_single_instance, lib.rs:2625).
rem If one is already running, a newly started one only focuses the old window and
rem exits - so this launcher looks like it did nothing while the OLD app (frozen
rem python) stays on screen. That is exactly what happened on 2026-09-16.
rem So close any running instance first. Only SoundField and its sidecars.
tasklist /fi "imagename eq soundfield.exe" 2>nul | find /i "soundfield.exe" >nul
if not errorlevel 1 (
  echo Closing the running SoundField first ^(single instance^).
  taskkill /f /im soundfield.exe >nul 2>&1
  taskkill /f /im sf_bridge.exe >nul 2>&1
  taskkill /f /im sf_audio.exe >nul 2>&1
  taskkill /f /im sf_query.exe >nul 2>&1
  taskkill /f /im sf_admin.exe >nul 2>&1
  taskkill /f /im sf_waveform.exe >nul 2>&1
  ping -n 4 127.0.0.1 >nul
)

if not exist "devrun" mkdir "devrun"
rem xcopy /D copies only when the source is newer. An unconditional copy would
rem fail silently while the app holds the file and keep launching a stale exe -
rem the trap run_poc.bat already hit (2026-09-09, a logo change stayed invisible).
xcopy /D /Y /Q "src-tauri\target\release\soundfield.exe" "devrun\" >nul 2>&1

rem Probe again with /L (list only): a non-zero count means the copy is still behind.
for /f %%n in ('xcopy /D /L /Y "src-tauri\target\release\soundfield.exe" "devrun\" 2^>nul ^| find /c "soundfield.exe"') do (
  if not "%%n"=="0" (
    echo devrun\soundfield.exe is stale and could not be refreshed.
    echo Close every running SoundField and try again.
    pause
    exit /b 1
  )
)

rem A leftover SOUNDFIELD_BRIDGE_EXE would defeat the whole point.
set "SOUNDFIELD_BRIDGE_EXE="

rem Binaural sidecar. locate_sidecar() in py\app\binaural.py looks next to
rem sys.executable - under dev python that is the PYTHON INSTALL folder - and at an
rem old sibling path that no longer exists after the folder merge. It does not know
rem py\sidecar\, so binaural showed "error" in this launcher only (seen 2026-09-16).
rem Point it there explicitly. The packaged bridge bundles the sidecar, so this
rem only matters here.
if exist "%~dp0py\sidecar\scsearch-monitor-fixed.exe" (
  set "SOUNDFIELD_BINAURAL_SIDECAR=%~dp0py\sidecar\scsearch-monitor-fixed.exe"
) else (
  echo WARNING: py\sidecar\scsearch-monitor-fixed.exe missing - binaural will error.
)

start "" "devrun\soundfield.exe"
exit /b 0
