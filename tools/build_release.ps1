# -*- coding: utf-8 -*-  (이 파일은 **UTF-8 BOM** 으로 저장한다.
#  BOM 이 없으면 Windows PowerShell 5.1 이 ANSI 로 읽어 한글이 깨지고
#  따옴표 짝이 어긋나 ParserError 로 죽는다 — 2026-09-14 실제로 겪음)
# 배포 인스톨러 빌드
#
#   npm run installer
#
# 이 저장소는 **YSG Audio Labs 판 전용**이다 (사용자 결정 2026-09-15).
# 소속 문구·로고는 이 저장소의 것만 쓴다.
#
# 왜 스크립트로 묶는가 — 세 곳이 동시에 맞아야 한다.
#   1) 프런트 빌드 결과 (타이틀바·시작화면)
#   2) 실행 파일의 작업표시줄 식별자(AUMID)
#   3) ISCC 가 읽는 설치 정보
# 하나라도 어긋나면 **아무 오류 없이** 이상한 설치본이 나온다. 손으로 돌리지 말 것.
#
# ⚠ 유사 사운드 검색은 여기서 나오는 결과물에 들어가지 않는다 (사용자 결정 2026-09-14).
#   격리는 build_bridge.ps1 의 --exclude-module 과 그 스크립트의 번들 검사가 한다.
#   이 스크립트는 브리지를 다시 만들지 않는다 — app\bridge\ 에 있는 것을 그대로 담는다.
#   브리지를 새로 만들어야 하면 .\build_bridge.ps1 을 먼저 돌린다.
#
# ⚠ 릴리스 빌드는 반드시 `tauri build` 로 한다. `cargo build --release` 는 개발
#   URL(localhost)을 바라보는 실행 파일을 만들어 빈 창이 뜬다 (2026-09-08 실제 사고).

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$ISCC = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $ISCC)) { throw "Inno Setup 6 을 찾을 수 없습니다: $ISCC" }

$aumid = "YsgAudioTools.SoundField"
$publisher = "YSG Audio Labs"

# ── 브리지 동기화 ────────────────────────────────────────────────────────────
# build_bridge.ps1 은 src-tauri\bridge-dist\sf_bridge 에 만들고, 인스톨러는
# app\bridge\* 를 포장한다. **두 곳이 달라서** 브리지를 새로 만들어도 설치본에는
# 옛것이 그대로 들어가는 사고가 있었다 (2026-09-14: 9/8 빌드가 계속 포장됨).
# 여기서 매번 맞춘다. 브리지를 다시 만들지 않았으면 아무 일도 하지 않는다.
$BridgeSrc = "src-tauri\bridge-dist\sf_bridge"
$BridgeDst = "app\bridge"
if (Test-Path "$BridgeSrc\sf_bridge.exe") {
    $srcTime = (Get-Item "$BridgeSrc\sf_bridge.exe").LastWriteTime
    $dstTime = if (Test-Path "$BridgeDst\sf_bridge.exe") {
        (Get-Item "$BridgeDst\sf_bridge.exe").LastWriteTime
    } else { [datetime]::MinValue }
    if ($srcTime -gt $dstTime) {
        Write-Host "브리지 갱신: $($dstTime.ToString('MM-dd HH:mm')) -> $($srcTime.ToString('MM-dd HH:mm'))" -ForegroundColor Yellow
        & robocopy $BridgeSrc $BridgeDst /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "브리지 복사 실패 (robocopy $LASTEXITCODE)" }
    } else {
        Write-Host "브리지 최신 ($($dstTime.ToString('MM-dd HH:mm')))" -ForegroundColor DarkGray
    }
}
# 유사 사운드 검색이 브리지에 섞여 들어가지 않았는지 확인한다.
# ⚠ 이건 **파일 이름만** 보는 검사다. 모듈은 exe 안 PYZ 로 압축돼 들어가므로
#   이것만으로는 못 잡는다 — 진짜 방어는 build_bridge.ps1 의 번들 목록 검사다.
$simLeak = Get-ChildItem $BridgeDst -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match "similarity" }
if ($simLeak) { throw "브리지에 유사 검색 파일이 들어 있습니다: $($simLeak[0].Name)" }

Write-Host ""
Write-Host "=== 프런트 빌드 ===" -ForegroundColor Cyan
& npm run build
if ($LASTEXITCODE -ne 0) { throw "프런트 빌드 실패" }

# 소속 로고가 실제로 담겼는지 확인한다. 파일이 없거나 경로가 틀려도 vite 는
# 아무 말 없이 빌드에 성공하고 **화면에서 로고만 사라진다** (2026-09-14 실제 사고).
$splash = Get-Content "dist\splash.html" -Raw
$markFile = "ysg-lockup-2026.png"
if (-not (Test-Path "dist\$markFile")) { throw "소속 로고 파일이 빌드에 없습니다: $markFile" }
if ($splash -notmatch [regex]::Escape($markFile)) { throw "시작 화면이 소속 로고를 안 씁니다: $markFile" }

# 시작 화면 배경 사진
$bgFile = "ysg-splash-bg.jpg"
if (-not (Test-Path "dist\$bgFile")) { throw "시작 화면 배경이 빌드에 없습니다: $bgFile" }
if ($splash -notmatch [regex]::Escape($bgFile)) { throw "시작 화면이 배경 사진을 안 씁니다: $bgFile" }

# 타이틀바 HUB 버튼 — 주소와 컬러 엠블럼이 **둘 다** 있어야 한다.
$hubUrl = "samter96.github.io/YSGAudioTools"
$hasHub = Select-String -Path "dist\assets\*.js" -SimpleMatch $hubUrl -List -ErrorAction SilentlyContinue
if (-not $hasHub) { throw "타이틀바 HUB 버튼이 빌드에 없습니다" }
if (-not (Test-Path "dist\ysg-emblem-2026.png")) { throw "HUB 버튼 로고가 빌드에 없습니다: ysg-emblem-2026.png" }

# 타이틀바 마크 — 매듭 마스크 id 로 확인한다 (src/icons.tsx 의 YsgMark).
$hasMark = Select-String -Path "dist\assets\*.js" -SimpleMatch "ysg-mark-2026" -List -ErrorAction SilentlyContinue
if (-not $hasMark) { throw "타이틀바 마크가 빌드에 없습니다: ysg-mark-2026" }

Write-Host "=== 실행 파일 빌드 (AUMID=$aumid) ===" -ForegroundColor Cyan
$env:SOUNDFIELD_AUMID = $aumid
# option_env! 는 값이 바뀌어도 캐시가 남을 수 있어 크레이트를 강제로 다시 만든다
(Get-Item "src-tauri\src\lib.rs").LastWriteTime = Get-Date
& npx tauri build --no-bundle
if ($LASTEXITCODE -ne 0) { throw "tauri build 실패" }

$exe = "src-tauri\target\release\soundfield.exe"
if (-not (Test-Path $exe)) { throw "실행 파일이 없습니다: $exe" }

# 작업표시줄 식별자가 실제로 박혔는지 확인 (환경변수를 놓치면 조용히 기본값이 된다)
$bytes = [IO.File]::ReadAllBytes($exe)
$text = [Text.Encoding]::ASCII.GetString($bytes)
if ($text -notlike "*$aumid*") { throw "실행 파일에 AUMID($aumid) 가 없습니다" }

# 실행 파일 속성의 '회사' 확인 (tauri.conf.json 의 bundle.publisher)
$got = (Get-Item $exe).VersionInfo.CompanyName
if ($got -ne $publisher) { throw "실행 파일 제작사가 '$got' 입니다 (기대: '$publisher')" }

Copy-Item $exe "app\soundfield.exe" -Force
# ISCC 가 "손으로 돌린 것이 아닌지" 확인하는 표시 (soundfield_installer.iss 참고)
Set-Content -Path "app\.brand" -Value "ysg" -Encoding ascii -NoNewline
Write-Host "app\soundfield.exe 갱신" -ForegroundColor Green

Write-Host "=== 인스톨러 ===" -ForegroundColor Cyan
# ⚠ ISCC 의 마지막 단계(EndUpdateResource)가 백신 때문에 간헐적으로 실패한다
#   ("Resource update error ... (110)"). 방금 지운 출력 파일을 백신이 아직
#   잡고 있을 때 특히 잘 난다. 코드 문제가 아니라 타이밍이라 몇 초 뒤 다시
#   하면 된다 — 2026-09-14 에 두 번 겪어서 재시도를 넣었다.
$ok = $false
for ($try = 1; $try -le 3; $try++) {
    & $ISCC "soundfield_installer.iss"
    if ($LASTEXITCODE -eq 0) { $ok = $true; break }
    if ($try -lt 3) {
        Write-Host "ISCC 실패 ($try/3) — 4초 뒤 다시 시도" -ForegroundColor Yellow
        Start-Sleep -Seconds 4
    }
}
if (-not $ok) { throw "ISCC 3회 모두 실패" }

$env:SOUNDFIELD_AUMID = $null

Write-Host ""
Write-Host "완료 — installer\ 확인" -ForegroundColor Green
Get-ChildItem "installer\*.exe" | Sort-Object LastWriteTime -Descending |
    Select-Object -First 2 Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } }, LastWriteTime
