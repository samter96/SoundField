; SoundField 설치 스크립트 (Inno Setup 6)
;
; ⚠ **이 파일을 직접 컴파일하지 말 것.** 인스톨러는 항상 스크립트로 만든다
;   (사용자 지시 2026-09-14: "인스톨러 빌드해" = npm run installer).
;
;       npm run installer            ← 이것 하나면 된다 (프런트 → exe → 인스톨러)
;
;   내부적으로 tools\build_release.ps1 이 프런트 → exe → 인스톨러를
;   순서대로 돌린다. 손으로 ISCC 를 돌리면 **직전에 만들어 둔 옛 실행
;   파일이 그대로 포장된다** — 아래 표시 검사가 그걸 막는다.
;
;   파이썬을 고쳤으면 브리지를 먼저 다시 만든다:
;     powershell -ExecutionPolicy Bypass -File .\build_bridge.ps1
;
; ⚠ exe 는 반드시 `tauri build` 로 만든다. `cargo build --release` 로 만든 것은
;   화면 주소가 devUrl(localhost:1420) 로 남아 개발 서버 없이는 실행되지 않는다.
;
; ⚠ 아이콘만 바꿨을 때: `src-tauri/icons/icon.ico` 를 바꿔도 cargo 는 build.rs 를
;   다시 돌리지 않아 exe 에 **옛 아이콘이 그대로 남는다** (실측으로 확인).
;   tauri.conf.json 의 수정 시각을 건드려 build.rs 재실행을 강제한 뒤 빌드한다:
;     (Get-Item src-tauri\tauri.conf.json).LastWriteTime = Get-Date
;   확인: [System.Drawing.Icon]::ExtractAssociatedIcon(exe) 로 뽑아 눈으로 본다.
;   아이콘 재생성은 `python tools/make_icon.py`.
;
; 독립 실행: 설치본은 파이썬도, 소스 트리도 필요 없다. 파이썬 `app.*` 모듈과
; 바이노럴 사이드카(scsearch-monitor-fixed.exe)가 bridge\ 안에 함께 들어 있고,
; 실행 파일이 포장 브리지에 SOUNDFIELD_PY_ROOT 를 없는 경로로 못박아
; 항상 번들 모듈만 쓰게 한다 (개발 PC 와 설치 PC 동작 일치).


; ── 실행 파일 확인 ──────────────────────────────────────────────────────────
; tools\build_release.ps1 이 app\soundfield.exe 를 복사하면서 그 옆에 표시를
; 남긴다. 그게 없으면 ISCC 를 직접 돌린 것이므로 세운다 — 손으로 돌리면 프런트
; 빌드·작업표시줄 식별자·제작사가 어긋난 채 **오류 없이** 포장된다.
#define StampPath AddBackslash(SourcePath) + "app\.brand"
#if !FileExists(StampPath)
  #error app\.brand 가 없습니다. ISCC 를 직접 돌리지 말고 `npm run installer` 를 쓰세요.
#endif

#define MyAppVersion "2.1.1"
#define MyAppExe "SoundField.exe"

; 이름에 접두사를 붙이지 않는다 — 앱 "SoundField" / SoundField_Installer_v*.exe
; (사용자 지시 2026-09-14).
#define MyAppName "SoundField"
#define MyPublisher "YSG Audio Labs"
; AUMID 와 AppGuid 는 **바꾸지 말 것** — 바꾸면 작업표시줄에 고정해 둔 아이콘이
; 다른 앱으로 인식돼 끊기고, 설치 항목도 새로 생겨 이전 버전이 남는다.
#define MyAumid "YsgAudioTools.SoundField"
#define AppGuid "{7C1E4F52-9A38-4D6B-8E21-5B0C7F4A9D13}"
#define OutBase "SoundField_Installer_v"
; 이름에 아무것도 붙이지 않는다 — 폴더까지 포함해서다 (사용자 지시 2026-09-14).
#define DirName "SoundField"

; 앱 GUID — **중괄호를 포함한 실제 값**을 한 곳에만 적는다.
;
; ⚠ 레지스트리 조회에 SetupSetting("AppId") 를 쓰지 말 것.
;   AppId 지시문에는 `{{GUID}` 로 적어야 한다 (`{{` 는 리터럴 `{` 하나를 뜻하는
;   Inno 이스케이프). 그런데 SetupSetting 은 **원문을 그대로** 돌려줘서
;   `{{GUID}` 가 되고, 실제 레지스트리 키 `{GUID}_is1` 와 맞지 않는다.
;   전처리 결과로 확인함 (2026-09-07):
;     Result := '...\Uninstall\{{D3B3A3A3-...}_is1';   ← 중괄호 2개
;   이 때문에 이전 버전(1.3.0)을 못 찾아 삭제 안내가 뜨지 않고 그대로 덮어써졌다.
;   레지스트리 뷰(WOW6432Node)를 의심해 HKLM32 를 추가했지만 그건 원인이 아니었다.
#ifndef AppGuid
  #define AppGuid "{D3B3A3A3-A3A3-4A3A-A3A3-A3A3A3A3A3A3}"
#endif

[Setup]
; ⚠ AppId 는 기존 SoundField(1.x) 와 **같은 값**이다. 그래야 설치되어 있던 이전
; 버전을 "이전 버전"으로 인식해 삭제 후 설치할 수 있다.
; 여기서는 `{` 를 `{{` 로 이스케이프해야 하므로 AppGuid 앞에 `{` 를 한 번 더 붙인다.
AppId={{#AppGuid}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyPublisher}
; ── 설치 폴더에 버전을 넣는다 (사용자 결정 2026-09-07) ──────────────────────
; 예: C:\Program Files (x86)\SoundField 2.1.0
;
; ⚠ 왜 버전을 넣는가 — **작업표시줄 아이콘이 옛 것으로 나오는 문제** 때문이다.
;   윈도우는 exe 경로별로 아이콘 그림을 캐시 파일에 저장해 두는데
;   (%LOCALAPPDATA%\Microsoft\Windows\Explorer\iconcache_*.db),
;   1.x 가 쓰던 경로(...\SoundField)에는 옛 로고가 저장돼 있다. 새 exe·새 icon.ico
;   를 같은 경로에 덮어도 그 저장물이 계속 그려졌다 (실측 2026-09-07: 탐색기
;   재시작·ie4uinit -show 로도 안 풀렸고, 캐시 파일을 지워야 풀렸다).
;   팀원 PC 30대의 캐시를 지우게 만들 수는 없으므로, **경로 자체를 새로 잡아**
;   옛 저장물을 만나지 않게 한다. 버전이 올라갈 때마다 새 경로가 되어 재발도 없다.
;   자세한 실측은 src-tauri/src/lib.rs 의 set_app_user_model_id 주석에 있다.
;
; 사용자 데이터는 이 경로와 무관하다 — 아래 "사용자 데이터는 건드리지 않는다"
; 주석 참고. 앱이 데이터 폴더를 정하는 코드도 exe 위치를 보지 않는다.
; 이전 버전 삭제는 레지스트리 등록 정보로 찾으므로 경로가 달라져도 그대로 동작한다.
;
; ⚠ UsePreviousAppDir=no 가 **반드시** 필요하다. Inno 기본값은 "이전에 설치했던
;   폴더를 다시 쓴다" 라서, 그대로 두면 여기서 새 경로를 잡아도 옛 폴더로
;   되돌아가고 위의 캐시 문제가 그대로 남는다.
;
; ⚠ 대가: 사용자가 **직접 작업표시줄에 고정해 둔 항목은 끊긴다.** 고정 항목은 옛
;   exe 경로를 가리키고 있고 그 경로가 없어지기 때문이다. 시작 메뉴/바탕화면
;   바로가기는 설치할 때 새로 만들어지므로 문제없고, 거기서 다시 고정하면 된다.
;   (버전이 올라갈 때마다 반복되는 대가다 — 아이콘 캐시 문제와 맞바꾼 것이다.)
; (64비트 앱이 (x86) 폴더에 있는 건 표시상의 문제일 뿐 동작에 영향이 없다.)
DefaultDirName={commonpf32}\{#DirName} {#MyAppVersion}
UsePreviousAppDir=no
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
; Inno 기본 "폴더가 이미 존재합니다" 경고를 끈다.
; InitializeSetup 이 이전 버전을 찾으면 이미 "기존 버전을 삭제하고 설치할까요?" 를
; 명시적으로 묻는다. 그 뒤 제거를 해도 제거 프로그램(unins000.exe)은 자기 자신을
; 지우지 못해 폴더가 남는 경우가 있어, 같은 뜻의 경고가 **두 번** 뜬다.
; (사용자 결정 2026-09-07 — 중복 확인이 혼란스럽다.)
DirExistsWarning=no
OutputDir=installer
OutputBaseFilename={#OutBase}{#MyAppVersion}
SetupIconFile=src-tauri\icons\icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 64비트 앱이므로 Program Files (x86) 이 아니라 Program Files 에 설치한다.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
; 인스톨러 exe 자체의 파일 속성에도 버전을 박는다 (버전 노출 지점 중 하나)
VersionInfoVersion={#MyAppVersion}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoCompany={#MyPublisher}
VersionInfoDescription={#MyAppName} {#MyAppVersion} 설치
DisableProgramGroupPage=yes

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; 본체 — 원본과 같은 실행 파일 이름을 쓴다 (기존 바로가기/작업표시줄 고정 유지)
Source: "app\soundfield.exe"; DestDir: "{app}"; DestName: "{#MyAppExe}"; Flags: ignoreversion
; 파이썬 브리지 (검색/관리/오디오/파형) + 바이노럴 사이드카.
; 실행 파일이 resource_dir\bridge\sf_bridge.exe 를 찾으므로 이 배치를 유지해야 한다.
; `_internal` 폴더 구조가 깨지면 브리지가 자기 python DLL 을 못 찾아 전부 실패한다.
Source: "app\bridge\*"; DestDir: "{app}\bridge"; Flags: ignoreversion recursesubdirs createallsubdirs
; 시작 메뉴 검색/바로가기 아이콘용 (PE 임베드 아이콘만 있으면 검색 결과에서 누락되는 경우가 있다)
Source: "src-tauri\icons\icon.ico"; DestDir: "{app}"; Flags: ignoreversion

; ── WebView2 런타임 설치 도우미 (1.7MB) ─────────────────────────────────────
; 화면은 Microsoft Edge WebView2 런타임이 그린다 (엣지 브라우저와는 **별개 부품**).
; 없으면 앱 창이 아예 뜨지 않으므로 설치 중에 자동 설치를 시도한다.
; `dontcopy` = 설치 폴더에 남기지 않고 필요할 때만 임시 폴더로 풀어 실행한다.
; ⚠ 오프라인 설치본(246MB)은 **일부러 넣지 않았다** — 인스톨러가 5배로 커진다.
;   이 부트스트래퍼는 설치 시 인터넷이 필요하고, 안 되면 아래에서 안내로 넘어간다.
; Microsoft Corporation 서명 확인 완료 (Authenticode Valid).
Source: "redist\MicrosoftEdgeWebview2Setup.exe"; Flags: dontcopy noencryption

; {autodesktop} — 관리자 설치면 모든 사용자 바탕화면, 사용자 설치면 본인 바탕화면으로
; 해석된다. {userdesktop} 은 관리자 권한으로 올라갈 때 엉뚱한 계정 바탕화면에 만들어질
; 수 있어 Inno 가 경고한다.
; AppUserModelID 는 앱이 직접 지정하는 이름과 반드시 같아야 한다
; (src-tauri/src/lib.rs 의 APP_USER_MODEL_ID). 다르면 작업표시줄에 고정한 항목과
; 실행 중인 창이 따로 잡혀 버튼이 두 개로 보인다.
; ⚠ 이 이름은 "작업표시줄에 옛 아이콘이 나온다" 의 해결책이 **아니다.**
;   그 원인은 윈도우 아이콘 캐시 파일이었다 — lib.rs set_app_user_model_id 주석 참고.
[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; IconFilename: "{app}\icon.ico"; AppUserModelID: "{#MyAumid}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon; IconFilename: "{app}\icon.ico"; AppUserModelID: "{#MyAumid}"

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

; ── 사용자 데이터는 건드리지 않는다 ─────────────────────────────────────────
; 인덱스 DB      %LOCALAPPDATA%\SoundField\index.db
; 파형 피크 캐시  %LOCALAPPDATA%\SoundField\peaks_cache\
; 재생 히스토리   %LOCALAPPDATA%\SoundField\history.json
; 채널 배치 저장  %LOCALAPPDATA%\SoundField\binaural_layouts.json
; 개인 설정      %USERPROFILE%\.soundfield_config.json
; 전부 설치 폴더 밖이고 [UninstallDelete] 항목이 없으므로 삭제/재설치에도 그대로 남는다.
; (이 목록을 지우는 코드를 여기에 추가하지 말 것.)

[Code]
const
  WebView2Key64 = 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WebView2Key32 = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function WebView2Installed: Boolean;
var
  Version: string;
begin
  Result := RegQueryStringValue(HKLM, WebView2Key64, 'pv', Version)
         or RegQueryStringValue(HKLM, WebView2Key32, 'pv', Version)
         or RegQueryStringValue(HKCU, WebView2Key32, 'pv', Version);
end;

{ 이전 버전의 제거 프로그램 경로를 찾는다.

  ⚠ 레지스트리 **두 뷰를 모두** 봐야 한다. 이 설치 프로그램은
  ArchitecturesInstallIn64BitMode=x64compatible 이라 64비트 모드로 돌고, 그때
  HKLM\Software 는 **64비트 뷰**를 가리킨다. 그런데 1.x 는 그 설정이 없어 32비트
  앱으로 설치됐고 등록 정보가 **WOW6432Node** 아래로 리다이렉트됐다.

  실측(2026-09-07):
    없음 : HKLM\SOFTWARE\Microsoft\...\Uninstall\  (AppId)_is1
    발견 : HKLM\SOFTWARE\WOW6432Node\Microsoft\...\Uninstall\  (AppId)_is1
           DisplayName "SoundField 버전 1.3.0"
  ⚠ 이 주석에 중괄호를 쓰지 말 것 — Inno 의 중괄호 주석은 **첫 닫는 괄호에서
    끝나서** 뒷부분이 코드로 해석돼 컴파일이 깨진다 (실제로 한 번 겪었다).
  한쪽만 보던 이전 코드는 1.3.0 을 못 찾았다. 그래서 삭제 안내가 뜨지 않고 Inno
  기본 "폴더가 이미 존재합니다" 만 나와 그대로 덮어쓰기로 진행됐다 (사용자 신고).

  ⚠ 이 목록에서 32비트 뷰(HKLM32/HKCU32)를 빼지 말 것.

  ⚠ 그리고 **첫 적중만 보고 끝내지 말 것.** 덮어쓰기로 설치되면 등록 항목이 두
    뷰에 동시에 남는다. 실측(2026-09-07):
      HKLM   (64비트 뷰) → SoundField 2.1.0 (64비트)   unins001.exe
      HKLM32 (32비트 뷰) → SoundField 버전 1.3.0        unins000.exe
    하나만 제거하면 나머지가 제어판에 유령 항목으로 남고 1.x 의 _internal(165MB)
    도 그대로 남는다. 네 뷰를 모두 돌아야 한다. }
function RootOf(Index: Integer): Integer;
begin
  case Index of
    0: Result := HKLM;
    1: Result := HKLM32;
    2: Result := HKCU;
  else
    Result := HKCU32;
  end;
end;

function UninstallKey: string;
begin
  Result := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppGuid}_is1';
end;

{ 등록된 버전을 모두 찾아 목록 문구를 만든다. 반환: 찾은 개수. }
function ScanPreviousVersions(var Summary: string): Integer;
var
  I: Integer;
  Name, Ver, Path: string;
begin
  Result := 0;
  Summary := '';
  for I := 0 to 3 do
  begin
    if RegQueryStringValue(RootOf(I), UninstallKey, 'UninstallString', Path) then
    begin
      if not RegQueryStringValue(RootOf(I), UninstallKey, 'DisplayName', Name) then
        Name := 'SoundField';
      if not RegQueryStringValue(RootOf(I), UninstallKey, 'DisplayVersion', Ver) then
        Ver := '';
      if Ver <> '' then
        Summary := Summary + '  · ' + Name + ' (' + Ver + ')' + #13#10
      else
        Summary := Summary + '  · ' + Name + #13#10;
      Result := Result + 1;
    end;
  end;
end;

{ 1.x 가 남긴, 새 버전이 **쓰지 않는** 잔여본을 지운다.

  1.x 는 PyQt 번들이라 `_internal\` 에 165MB 를 깔았다. 2.x 는 Tauri 라 그 폴더를
  전혀 쓰지 않는다. 제거 프로그램이 자기가 깐 파일만 지우므로, 설치 후 생성된
  파일이 섞여 있으면 폴더가 남아 용량만 차지한다 (실측 2026-09-07: 271개 172MB).
  (사용자 결정 2026-09-07 — 제거 후 확인해서 지운다.)

  ⚠ 지우는 대상은 **설치 폴더 안의, 2.x 가 쓰지 않는 항목**뿐이다.
    사용자 데이터(인덱스 DB / 파형 캐시 / 히스토리 / 개인 설정)는 전부 설치 폴더
    **밖**(%LOCALAPPDATA%\SoundField, %USERPROFILE%)이라 여기서 닿지 않는다.
    이 함수에 그 경로를 추가하는 코드를 넣지 말 것.
  폴더가 **완전히 비었을 때만** 폴더 자체도 지운다. 설치 경로에 버전이 들어가면서
  이전 버전 폴더는 더 이상 쓰지 않게 되어 그냥 빈 껍데기로 남기 때문이다.
  ⚠ RemoveDir 는 비어 있을 때만 성공한다 — 뭐가 남아 있으면 아무 일도 안 한다.
    DelTree 로 통째로 지우지 말 것. 제거 프로그램(unins000.exe)이 아직 자기 자신을
    붙잡고 있을 수 있고, 우리가 모르는 파일을 지울 위험도 있다. }
procedure RemoveLegacyLeftovers(Dir: string);
begin
  if Dir = '' then
    Exit;
  { 1.x PyQt 번들 — 2.x 는 bridge\_internal 을 쓰고 이 경로는 쓰지 않는다 }
  if DirExists(Dir + '_internal') then
    DelTree(Dir + '_internal', True, True, True);
  { 비었으면 껍데기 폴더도 정리 (아니면 그대로 둔다) }
  RemoveDir(Dir);
end;

{ 설치 직전에 불린다 (Inno 가 사전 요구사항 설치용으로 제공하는 훅).
  런타임이 없으면 부트스트래퍼로 자동 설치를 시도한다. 실패하면(인터넷 없음 등)
  **설치를 막지 않고** 안내만 한다 — 사용자가 나중에 런타임만 깔면 바로 동작하고,
  그때까지는 앱이 실행 시 한글 안내를 띄운다 (lib.rs 의 ensure_webview2). }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  Installed: Boolean;
begin
  Result := '';
  if WebView2Installed then
    Exit;

  WizardForm.StatusLabel.Caption := 'Microsoft Edge WebView2 런타임을 설치하는 중...';
  Installed := False;
  ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
  if Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'),
          '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Installed := WebView2Installed;

  if not Installed then
    MsgBox('Microsoft Edge WebView2 런타임을 자동으로 설치하지 못했습니다.' + #13#10 +
           '(설치에 인터넷 연결이 필요합니다.)' + #13#10 + #13#10 +
           'SoundField 설치는 계속 진행됩니다. 다만 이 런타임이 없으면' + #13#10 +
           '프로그램 창이 뜨지 않습니다.' + #13#10 + #13#10 +
           '아래 중 하나로 해결할 수 있습니다.' + #13#10 +
           '  · 인터넷이 되는 상태에서 이 설치 프로그램을 다시 실행' + #13#10 +
           '  · "Microsoft Edge WebView2 런타임"을 직접 설치' + #13#10 +
           '  · 그래도 안 되면 문의해 주세요',
           mbInformation, MB_OK);
end;

{ 등록된 버전을 **전부** 제거한다. 하나라도 실패하면 False. }
function RemoveAllPreviousVersions: Boolean;
var
  I, ResultCode, Failed: Integer;
  Path, Dir: string;
begin
  Result := True;
  Failed := 0;
  for I := 0 to 3 do
  begin
    if RegQueryStringValue(RootOf(I), UninstallKey, 'UninstallString', Path) then
    begin
      Dir := ExtractFilePath(RemoveQuotes(Path));
      if Exec(RemoveQuotes(Path), '/SILENT /NORESTART /SUPPRESSMSGBOXES',
              '', SW_SHOW, ewWaitUntilTerminated, ResultCode) then
        RemoveLegacyLeftovers(Dir)
      else
        Failed := Failed + 1;
    end;
  end;
  if Failed > 0 then
  begin
    MsgBox('이전 버전 삭제에 실패했습니다 (' + IntToStr(Failed) + '개).' + #13#10 +
           '설치를 중단합니다. SoundField 가 실행 중이면 종료한 뒤 다시 시도해 주세요.',
           mbError, MB_OK);
    Result := False;
  end;
end;

function InitializeSetup: Boolean;
var
  Found: Integer;
  Summary: string;
begin
  Result := True;

  { WebView2 런타임은 설치 단계(PrepareToInstall)에서 자동 설치를 시도한다.
    설치 전에 경고만 띄우면 "설치는 됐는데 실행이 안 된다"로 끝난다 — 창이 조용히 안 뜬다. }

  { 이전 버전 — 삭제 후 설치. 인덱스 DB 와 개인 설정은 설치 폴더 밖이라 보존된다. }
  Found := ScanPreviousVersions(Summary);
  if Found > 0 then
  begin
    if MsgBox('이미 설치된 SoundField 가 있습니다.' + #13#10 + #13#10 +
              Summary + #13#10 +
              '위 버전을 삭제하고 {#MyAppVersion} 을 새로 설치할까요?' + #13#10 + #13#10 +
              '인덱스 데이터베이스·파형 캐시·개인 설정은 그대로 유지됩니다.',
              mbConfirmation, MB_YESNO) = IDYES then
      Result := RemoveAllPreviousVersions
    else
      Result := False;
  end;
end;
