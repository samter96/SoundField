$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
# 파이썬 코어(검색·인덱싱·재생·파형)는 2026-09-15 부터 **이 폴더 안** py\ 에 있다.
# 그 전에는 형제 폴더 Sound_Search_program 을 참조했다 — 폴더 두 개를 하나로 합쳤다.
$PyRoot = Join-Path $ProjectRoot "py"
$DistRoot = Join-Path $ProjectRoot "src-tauri\bridge-dist"
$WorkRoot = Join-Path $ProjectRoot "src-tauri\bridge-build"

# 바이노럴 사이드카 — 원본 locate_sidecar (app/binaural.py:295) 가 찾는 실행 파일.
# 원본 후보 목록에 **원본 저장소 안의 경로**가 들어 있어서, 소스 트리가 없는 PC 에서는
# 바이노럴이 "미설치"로 잡힌다 (실측: 원본 루트 있음 available=True / 없음 False).
# 설치본은 독립 실행이어야 하므로 번들 안(_internal\monitor)에 함께 넣는다 —
# locate_sidecar 의 `bundle_root / "monitor" / ...` 후보가 이걸 집는다.
$Sidecar = Join-Path $PyRoot "sidecar\scsearch-monitor-fixed.exe"
if (-not (Test-Path $Sidecar)) {
  throw "바이노럴 사이드카를 찾을 수 없습니다: $Sidecar"
}

# ── UCS 동의어 사전 (필수) ───────────────────────────────────────────────────
# ⚠ 이 파일을 번들에 안 넣으면 **검색 결과가 조용히 크게 줄어든다.**
# 검색은 "any" 필드 토큰을 UCS 동의어로 (원어 OR 동의어) 확장한다
# (원본 database.py _query_synonyms → app/thesaurus.py). 사전 로드가 실패하면
# thesaurus 는 **경고만 남기고 빈 사전으로 동작**하므로 아무 에러도 보이지 않는다.
# 실측 2026-09-07 (사용자 신고 "digital/notice 두 필터 검색 결과가 0"):
#   원본(사전 있음)  digital AND notice = 1,047 건  ('notice' → BELL/BUZZER/ALERT)
#   PoC (사전 없음)  digital AND notice = 0 건
# thesaurus._DATA_PATH 가 `<모듈폴더>/data/ucs_thesaurus.json` 이라 번들에서도
# `app\data` 위치를 그대로 맞춰야 한다.
$Thesaurus = Join-Path $PyRoot "app\data\ucs_thesaurus.json"
if (-not (Test-Path $Thesaurus)) {
  throw "UCS 동의어 사전을 찾을 수 없습니다: $Thesaurus"
}

# ⚠ --version-file / --icon 을 빼지 말 것.
# 작업 관리자의 "이름" 열은 exe 의 FileDescription 을, 아이콘은 exe 임베드 아이콘을
# 쓴다. 이걸 안 넣으면 브리지 프로세스가 `sf_bridge` 세 줄로 따로 나와서 SoundField
# 전체 점유량을 합산해 볼 수 없다 (사용자 요청 2026-09-03). 설명·아이콘을 본체와
# 맞추면 크롬 헬퍼처럼 앱 아래로 접히는 한 그룹으로 보인다.
python -m PyInstaller --noconfirm --onedir --name sf_bridge `
  --version-file (Join-Path $ProjectRoot "bridge_version.txt") `
  --icon (Join-Path $ProjectRoot "src-tauri\icons\icon.ico") `
  --paths $PyRoot `
  --paths (Join-Path $ProjectRoot "src-tauri\python") `
  --collect-submodules mutagen `
  --exclude-module app.similarity_schema `
  --exclude-module app.similarity `
  --exclude-module app.similarity_indexer `
  --exclude-module app.similarity_search `
  --add-binary "$Sidecar;monitor" `
  --add-data "$Thesaurus;app\data" `
  --distpath $DistRoot --workpath $WorkRoot `
  (Join-Path $ProjectRoot "src-tauri\python\sf_bridge.py")

# ── 역할별 실행 파일 사본 ────────────────────────────────────────────────────
# 작업 관리자의 "이름" 열은 exe 의 FileDescription 을 쓴다. exe 하나로는 프로세스마다
# 다른 설명을 줄 수 없어서, `_internal` 을 공유하는 사본을 역할 수만큼 만들고 사본마다
# 설명을 다르게 박는다 (사용자 요청 2026-09-03: 어떤 역할인지 보이게).
# 부트로더는 자기 옆의 `_internal` 만 찾고 파일 이름은 보지 않으므로 사본도 그대로 돈다.
# 파일명은 ASCII 로 둔다 — 화면에 보이는 건 설명이라 한글이 필요 없다.
$BridgeDir = Join-Path $DistRoot "sf_bridge"
$Roles = @(
  @{ File = "sf_query.exe";    Desc = "SoundField 검색 엔진" },
  @{ File = "sf_admin.exe";    Desc = "SoundField 인덱싱" },
  @{ File = "sf_audio.exe";    Desc = "SoundField 재생 엔진" },
  @{ File = "sf_waveform.exe"; Desc = "SoundField 파형 분석" }
)
# ⚠ 사본을 만든 뒤 **반드시 실행해 확인한다.** 설명을 박는 과정은 PE 리소스를
#   고쳐 쓰는 일이라, 백신이 끼어들면 뒤에 붙은 PyInstaller 아카이브가 떨어져
#   **파일이 잘린다.** 그러면 그 역할만 실행 즉시 죽는데, 스탬프 도구는 오류를
#   내지 않아 빌드가 성공으로 끝난다.
#   실측 2026-09-08: sf_query.exe 만 5,984,644 → 344,064 바이트로 잘렸고
#   ("Could not load PyInstaller's embedded PKG archive") 나머지 3개는 정상이었다.
#   검색이 아예 안 되는 빌드가 그대로 인스톨러까지 갈 수 있었다.
$SourceExe = Join-Path $BridgeDir "sf_bridge.exe"
$SourceLen = (Get-Item $SourceExe).Length
Write-Host "역할별 사본 생성:"
foreach ($role in $Roles) {
  $dest = Join-Path $BridgeDir $role.File
  $ok = $false
  foreach ($attempt in 1..3) {
    Copy-Item $SourceExe $dest -Force
    python (Join-Path $ProjectRoot "tools\stamp_role_exe.py") $dest $role.Desc `
           (Join-Path $ProjectRoot "bridge_version.txt")
    if ($LASTEXITCODE -ne 0) { Write-Host "  (설명 박기 실패 — 재시도 $attempt)"; continue }
    # ① 크기: 리소스만 바뀌므로 원본과 같아야 한다 (잘림 감지)
    $len = (Get-Item $dest).Length
    if ($len -ne $SourceLen) {
      Write-Host "  ($($role.File) 크기 이상 $len / $SourceLen — 재시도 $attempt)"
      continue
    }
    # ② 실제로 뜨는지: selftest 가 0 을 돌려주면 아카이브가 온전하다
    & $dest selftest > $null 2>&1
    if ($LASTEXITCODE -ne 0) {
      Write-Host "  ($($role.File) 실행 실패 — 재시도 $attempt)"
      continue
    }
    $ok = $true
    break
  }
  if (-not $ok) { throw "역할 사본이 깨졌습니다: $($role.File) (백신이 잠고 있는지 확인)" }
}

$Bundled = Join-Path $DistRoot "sf_bridge\_internal\monitor\scsearch-monitor-fixed.exe"
if (Test-Path $Bundled) {
  Write-Host "사이드카 포함 확인: $Bundled"
} else {
  throw "사이드카가 번들에 들어가지 않았습니다: $Bundled"
}

# ⚠ 이 검사를 지우지 말 것. 사전이 빠져도 앱은 아무 에러 없이 돌고 검색 결과만
# 조용히 줄어든다 (위 $Thesaurus 주석의 실측 참고). 빌드에서 잡는 게 유일한 방어다.
$BundledThesaurus = Join-Path $DistRoot "sf_bridge\_internal\app\data\ucs_thesaurus.json"
if (Test-Path $BundledThesaurus) {
  Write-Host "동의어 사전 포함 확인: $BundledThesaurus"
} else {
  throw "UCS 동의어 사전이 번들에 들어가지 않았습니다: $BundledThesaurus"
}

# ── 유사 사운드 검색 격리 검사 (필수) ────────────────────────────────────────
# 사용자 결정 2026-09-14: 유사 검색은 개발용이고 설치본에는 넣지 않는다.
# 격리는 **모듈이 번들에 없어서** 성립한다 — database.py 의 import 가 실패해야
# 관련 SQL 을 전부 건너뛴다. 모듈이 들어가면 사용자 DB 에 표·컬럼이 생긴다.
#
# ⚠ 파일 이름만 보는 검사로는 못 잡는다. 모듈은 exe 안 PYZ 로 압축돼 들어가서
#   app\bridge 에 similarity 라는 이름의 파일이 하나도 안 보인다.
#   실측 2026-09-15: build_release.ps1 의 이름 검사를 통과한 채 설치본에
#   app.similarity_schema 가 들어가 있었다 (Analysis TOC 로 확인).
#   원인은 이 스크립트가 sf_bridge.spec 을 쓰지 않는데(명령줄 빌드) 격리를
#   그 spec 의 excludes 에 적어 둔 것이었다 — 그래서 제외가 걸린 적이 없다.
# ⚠ 볼 파일은 **PYZ-00.toc** 다 (아카이브에 실제로 담긴 목록).
#   Analysis-00.toc 는 제외된 모듈까지 함께 적혀 있어서 그걸 보면 멀쩡한 빌드가
#   실패한다 — 2026-09-15 에 실제로 그렇게 오검출했다.
$Toc = Join-Path $WorkRoot 'sf_bridge\PYZ-00.toc'
if (-not (Test-Path $Toc)) { throw "빌드 목록을 찾을 수 없습니다: $Toc" }
$SimLeak = Select-String -Path $Toc -Pattern 'app\.similarity' -List -ErrorAction SilentlyContinue
if ($SimLeak) { throw "유사 검색 모듈이 번들에 들어갔습니다 — $Toc 확인" }
Write-Host "유사 검색 격리 확인: 번들에 app.similarity* 없음"

# ── 번들 자체 점검 (필수) ────────────────────────────────────────────────────
# ⚠ 이 단계를 지우지 말 것. 여기서 확인하는 것들은 빠져도 앱이 에러 없이 돌고
#   결과만 나빠진다 (sf_bridge.py selftest 주석의 실측 참고). 빌드에서 잡는 게
#   유일한 방어다. 항목을 늘릴 때는 sf_bridge.py 의 selftest 에 추가한다.
Write-Host ""
Write-Host "번들 자체 점검:"
$SelfTest = & (Join-Path $BridgeDir "sf_bridge.exe") selftest 2>&1
$SelfTest | ForEach-Object { Write-Host "  $_" }
if ($LASTEXITCODE -ne 0) {
  throw "번들 자체 점검 실패 — 위 '빠짐' 항목을 해결해야 한다."
}
