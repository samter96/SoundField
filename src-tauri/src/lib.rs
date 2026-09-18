use tauri::{Emitter, Manager};
use std::io::{BufRead, BufReader, Write};
use std::collections::{HashMap, HashSet};
use std::process::{Child, ChildStdin, ChildStdout};
use std::sync::Mutex;

struct AudioState {
    child: Mutex<Option<Child>>,
    stdin: Mutex<Option<ChildStdin>>,
}

/// 파형 서비스(sf_waveform_service.py)는 상주 프로세스다.
/// 매 요청마다 python 을 새로 띄우면 player_widget import 만 1초씩 들어서
/// 확대/스크롤 중 raw 창을 다시 읽는 경로가 사실상 못 쓴다.
struct WaveInner {
    child: Mutex<Option<Child>>,
    io: Mutex<Option<(ChildStdin, BufReader<ChildStdout>)>>,
    seq: Mutex<u64>,
    /* 지금 요청을 처리 중인가. 취소(=프로세스 kill)를 **처리 중일 때만** 하기 위해
       필요하다. 예전에는 파일 전환마다 무조건 kill 해서, 다음 요청이 파이썬
       프로세스 기동(실측 약 578ms)을 기다렸다 → "파형 로딩 중" 이 떴다. */
    busy: std::sync::atomic::AtomicBool,
}

struct WaveState {
    inner: std::sync::Arc<WaveInner>,
}

#[derive(serde::Serialize)]
struct DbRow {
    id: String,
    file_path: String,
    file_name: String,
    file_size: i64,
    duration: f64,
    sample_rate: i64,
    channels: i64,
    bit_depth: i64,
    codec: String,
    bitrate: i64,
    title: String,
    artist: String,
    album: String,
    genre: String,
    comments: String,
}

#[derive(serde::Serialize)]
struct LibraryRoot {
    path: String,
    last_indexed_at: Option<f64>,
    sort_order: Option<i64>,
    count: i64,
    pending: i64,
    failed: i64,
    exists: bool,
}

#[derive(serde::Serialize)]
struct BlacklistPath {
    path: String,
    description: String,
}

#[derive(serde::Serialize, Clone)]
struct FolderTreeNode {
    id: String,
    label: String,
    path: String,
    count: i64,
    incomplete: i64,
    children: Vec<FolderTreeNode>,
}

#[derive(serde::Deserialize, serde::Serialize)]
struct SearchMatcher {
    field: String,
    operator: Option<String>,
    value: String,
}

#[derive(serde::Deserialize, serde::Serialize)]
struct SearchRequest {
    matchers: Vec<SearchMatcher>,
    min_duration: f64,
    max_duration: Option<f64>,
    sample_rate: Option<i64>,
    channels: Option<i64>,
    min_channels: Option<i64>,
    path_prefixes: Option<Vec<String>>,
    precise: bool,
    limit: i64,
}

#[derive(serde::Deserialize, serde::Serialize)]
struct AudioCommand {
    command: String,
    path: Option<String>,
    meta: Option<serde_json::Value>,
    position_ms: Option<i64>,
    rate: Option<f64>,
    volume: Option<f64>,
    enabled: Option<bool>,
    layout: Option<String>,
    restart: Option<bool>,
    play: Option<bool>,
}

#[derive(serde::Deserialize, serde::Serialize)]
struct WaveformRequest {
    path: String,
    /// "peaks"(기본) / "quick" / "region" / "layout".
    /// (가로 줌 폐지로 샘플 단위 "raw" 모드는 제거됐다 — start/count 필드도 함께.)
    #[serde(default)]
    mode: Option<String>,
    /// "layout" 모드의 채널 수
    #[serde(default)]
    channels: Option<i64>,
    /// "layout" 모드에서 쓰는 사용자 선택 프리셋 (원본 _layout_overrides)
    #[serde(default)]
    r#override: Option<String>,
    /// "region" 모드 — 잘라낼 구간(초)과 배속 (원본 _resolve_export_path)
    #[serde(default)]
    start_sec: Option<f64>,
    #[serde(default)]
    end_sec: Option<f64>,
    #[serde(default)]
    rate: Option<f64>,
}

/* 원본과 동일한 로컬 상태 경로. 오디오 라이브러리(Y:\)에는 절대 쓰지 않고,
   인덱스·설정·히스토리만 %LOCALAPPDATA%/사용자 프로필 아래에서 공유한다. */
fn local_appdata() -> Result<std::path::PathBuf, String> {
    let local = std::env::var_os("LOCALAPPDATA")
        .ok_or_else(|| "LOCALAPPDATA 환경변수를 찾을 수 없습니다".to_string())?;
    Ok(std::path::PathBuf::from(local).join("SoundField"))
}

fn original_db_path() -> Result<std::path::PathBuf, String> {
    Ok(local_appdata()?.join("index.db"))
}

fn poc_dir() -> Result<std::path::PathBuf, String> {
    let dir = local_appdata()?.join("poc");
    if !dir.exists() {
        std::fs::create_dir_all(&dir).map_err(|e| format!("PoC 폴더를 만들 수 없습니다: {e}"))?;
    }
    Ok(dir)
}

fn db_path() -> Result<std::path::PathBuf, String> {
    original_db_path()
}

/* ── 마지막 창 위치·크기·최대화 상태 (원본 _load_config/_actual_save_config 의
   "geometry" 항목, main_window.py:4858·4975) ────────────────────────────────
   원본은 Qt saveGeometry() 의 이진 덩어리를 설정 파일에 넣는다. 그 형식은 Qt 만
   읽을 수 있어 여기서는 같은 값(위치·크기·최대화)을 우리 형식으로 적는다.

   ⚠ 설정 파일(.soundfield_config.json)에 넣지 않는다. 그 파일은 화면 쪽에서
     읽고-고치고-쓰기 때문에, 창을 움직이는 동안 여기서도 같은 파일을 쓰면 방금
     바꾼 설정이 지워질 수 있다. 창 상태만 담는 별도 파일을 쓴다. */
fn win_state_path() -> Result<std::path::PathBuf, String> {
    Ok(local_appdata()?.join("poc_window.json"))
}

#[derive(Clone, Copy, Default, serde::Serialize, serde::Deserialize)]
struct WinState {
    x: i32,
    y: i32,
    w: u32,
    h: u32,
    #[serde(default)]
    maximized: bool,
}

fn read_win_state() -> Option<WinState> {
    let path = win_state_path().ok()?;
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str::<WinState>(&text).ok()
}

fn write_win_state(state: &WinState) {
    if let Ok(path) = win_state_path() {
        if let Some(dir) = path.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        if let Ok(text) = serde_json::to_string(state) {
            let tmp = path.with_extension("tmp");
            if std::fs::write(&tmp, text).is_ok() {
                let _ = std::fs::rename(&tmp, &path);
            }
        }
    }
}

/* 지금 창 상태를 읽어 파일에 적는다.
   ⚠ 최대화 상태에서는 위치·크기를 덮어쓰지 않는다. 최대화된 창의 크기는 화면
     크기라서, 그걸 저장하면 최대화를 풀었을 때 돌아갈 원래 크기가 사라진다
     (원본 saveGeometry 도 원래 크기를 따로 들고 있다). */
fn save_win_state(win: &tauri::Window) {
    let maximized = win.is_maximized().unwrap_or(false);
    if win.is_minimized().unwrap_or(false) {
        return; /* 최소화는 기억하지 않는다 — 다음 실행에 창이 안 보이면 고장으로 보인다 */
    }
    let mut state = read_win_state().unwrap_or_default();
    if !maximized {
        if let (Ok(pos), Ok(size)) = (win.outer_position(), win.outer_size()) {
            state.x = pos.x;
            state.y = pos.y;
            state.w = size.width;
            state.h = size.height;
        }
    }
    state.maximized = maximized;
    if state.w == 0 || state.h == 0 {
        return;
    }
    write_win_state(&state);
}

/* 저장된 상태로 창을 되돌린다. 창이 아직 감춰져 있는 시작 시점에 부른다.

   원본 _constrain_to_screen (main_window.py:5123) 과 같은 안전장치를 함께 둔다:
   모니터 구성이 바뀌어(노트북만 쓰게 되는 등) 저장된 위치가 화면 밖이면 창을 잡을
   수 없게 되므로, 모든 모니터를 합친 범위 안으로 끌어들이고 크기도 줄인다. */
fn restore_win_state(win: &tauri::WebviewWindow) {
    let Some(state) = read_win_state() else { return };
    if state.w < 200 || state.h < 200 {
        return;
    }
    let monitors = win.available_monitors().unwrap_or_default();
    let (mut x, mut y, mut w, mut h) = (state.x, state.y, state.w, state.h);
    if !monitors.is_empty() {
        /* 모든 모니터를 합친 범위 */
        let mut left = i32::MAX;
        let mut top = i32::MAX;
        let mut right = i32::MIN;
        let mut bottom = i32::MIN;
        let mut widest = 0u32;
        let mut tallest = 0u32;
        for m in &monitors {
            let p = m.position();
            let sz = m.size();
            left = left.min(p.x);
            top = top.min(p.y);
            right = right.max(p.x + sz.width as i32);
            bottom = bottom.max(p.y + sz.height as i32);
            widest = widest.max(sz.width);
            tallest = tallest.max(sz.height);
        }
        w = w.min(widest);
        h = h.min(tallest);
        /* 제목 표시줄이 잡을 수 있는 자리에 있는지 — 최소 40px 는 보이게 */
        const MARGIN: i32 = 40;
        if x + (w as i32) < left + MARGIN {
            x = left;
        } else if x > right - MARGIN {
            x = right - w as i32;
        }
        if y < top {
            y = top;
        } else if y > bottom - MARGIN {
            y = bottom - h as i32;
        }
    }
    let _ = win.set_size(tauri::PhysicalSize::new(w, h));
    let _ = win.set_position(tauri::PhysicalPosition::new(x, y));
    if state.maximized {
        let _ = win.maximize();
    }
}

/// 원본과 공유하는 사용자 설정.
fn config_path() -> Result<std::path::PathBuf, String> {
    let home = std::env::var_os("USERPROFILE")
        .ok_or_else(|| "USERPROFILE 환경변수를 찾을 수 없습니다".to_string())?;
    let home = std::path::PathBuf::from(home);
    Ok(home.join(".soundfield_config.json"))
}

fn open_readonly() -> Result<rusqlite::Connection, String> {
    let path = db_path()?;
    let conn = rusqlite::Connection::open_with_flags(
        path,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_URI,
    ).map_err(|e| format!("SoundField 인덱스를 읽을 수 없습니다: {e}"))?;
    /* 원본 Database._connect (database.py:300) 와 같은 읽기 측 PRAGMA.
       read_only 연결에서는 원본도 journal_mode/synchronous 를 건드리지 않는다
       (쓰기 경합 회피). 158만 행 스캔이 잦아 캐시/temp_store 는 그대로 맞춘다. */
    /* ⚠ 원본은 **읽기 전용 연결에 mmap 을 걸지 않는다** (쓰기 분기에만 있다,
       database.py:322). 읽기 측에도 걸면 실DB(16.7GB/158만 행)에서 FTS 검색이
       162.9ms → 28.3ms (5.8배)였다. 읽기 매핑은 쓰기 경합과 무관해 안전하다. */
    let _ = conn.execute_batch(
        "PRAGMA cache_size=-128000; PRAGMA temp_store=MEMORY; PRAGMA mmap_size=4294967296;");
    let _ = conn.busy_timeout(std::time::Duration::from_secs(30));
    Ok(conn)
}

/// 검색 브리지도 **상주 프로세스**다.
/// 요청마다 새 python 을 띄우면 실측 315ms (python 시작 + app.database import +
/// Database 생성). 상주로 바꾸면 23ms, 파이썬 쪽에서 연결까지 유지하면 2.8ms.
/// 검색은 입력 40ms 디바운스마다 도는 가장 뜨거운 경로다.
///
/// 프로토콜: 요청 한 줄 `{"id":N, ...}` → 응답 한 줄 `{"id":N,"rows":[...]}`.
/// 파이썬은 **받은 순서대로** 응답하므로(취소된 요청도 `{"aborted":true}` 로 응답)
/// 호출자는 자기 id 가 나올 때까지 줄을 읽으면 된다.
struct SearchInner {
    child: Mutex<Option<Child>>,
    stdin: Mutex<Option<ChildStdin>>,
    stdout: Mutex<Option<BufReader<ChildStdout>>>,
}

struct SearchState {
    next_id: std::sync::atomic::AtomicU64,
    inner: std::sync::Arc<SearchInner>,
}

fn ensure_search_service(inner: &SearchInner) -> Result<(), String> {
    let mut child_guard = inner.child.lock().map_err(|_| "검색 상태 잠금 실패".to_string())?;
    let mut in_guard = inner.stdin.lock().map_err(|_| "검색 입력 잠금 실패".to_string())?;
    let mut out_guard = inner.stdout.lock().map_err(|_| "검색 출력 잠금 실패".to_string())?;
    let dead = child_guard.as_mut()
        .map(|c| c.try_wait().ok().flatten().is_some())
        .unwrap_or(true);
    if !dead && in_guard.is_some() && out_guard.is_some() {
        return Ok(());
    }
    if let Some(mut old) = child_guard.take() { let _ = old.kill(); }
    let mut child = python_bridge("query", "sf_query.py")
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn().map_err(|e| format!("검색 브리지를 시작할 수 없습니다: {e}"))?;
    *in_guard = child.stdin.take();
    *out_guard = child.stdout.take().map(BufReader::new);
    *child_guard = Some(child);
    Ok(())
}

/// 관리(인덱싱) 브리지는 **상주 프로세스**다.
/// LibraryManager 생성이 스키마 마이그레이션 + 인덱스 검증으로 실측 2.04초 걸린다
/// (실DB 150만 행). 요청마다 새로 띄우면 그 2초가 매번 붙는다 —
/// 원본은 앱 시작 때 한 번만 만든다.
struct AdminInner {
    child: Mutex<Option<Child>>,
    stdin: Mutex<Option<ChildStdin>>,
    stdout: Mutex<Option<BufReader<ChildStdout>>>,
}

struct AdminState {
    busy: std::sync::Arc<std::sync::atomic::AtomicBool>,
    current_pid: std::sync::Arc<std::sync::atomic::AtomicU32>,
    inner: std::sync::Arc<AdminInner>,
}

fn ensure_admin_service(inner: &AdminInner) -> Result<(), String> {
    let mut child_guard = inner.child.lock().map_err(|_| "관리 상태 잠금 실패".to_string())?;
    let mut in_guard = inner.stdin.lock().map_err(|_| "관리 입력 잠금 실패".to_string())?;
    let mut out_guard = inner.stdout.lock().map_err(|_| "관리 출력 잠금 실패".to_string())?;
    let dead = child_guard.as_mut()
        .map(|c| c.try_wait().ok().flatten().is_some())
        .unwrap_or(true);
    if !dead && in_guard.is_some() && out_guard.is_some() {
        return Ok(());
    }
    if let Some(mut old) = child_guard.take() { let _ = old.kill(); }
    let mut child = python_bridge("admin", "sf_admin.py")
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn().map_err(|e| format!("관리 작업을 시작할 수 없습니다: {e}"))?;
    *in_guard = child.stdin.take();
    *out_guard = child.stdout.take().map(BufReader::new);
    *child_guard = Some(child);
    Ok(())
}

#[derive(serde::Deserialize)]
struct AdminRequest {
    op: String,
    path: Option<String>,
    paths: Option<Vec<String>>,
    force: Option<bool>,
    purge: Option<bool>,
    include_pending: Option<bool>,
    include_failed: Option<bool>,
}

fn open_writable() -> Result<rusqlite::Connection, String> {
    let conn = rusqlite::Connection::open(db_path()?)
        .map_err(|e| format!("SoundField 인덱스를 열 수 없습니다: {e}"))?;
    let _ = conn.execute_batch(
        "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=30000;");
    Ok(conn)
}

/* 자식 프로세스의 **검은 콘솔 창**을 띄우지 않는다.
   포장 브리지(sf_bridge.exe)는 PyInstaller 콘솔 앱이라 spawn 할 때마다 콘솔 창이
   하나씩 생긴다 — 사용자 신고: 시작 시 3개(audio/admin/query) + 재생마다 1개
   (waveform). stdio 파이프는 그대로 동작하고 창만 안 생긴다. */
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[cfg(windows)]
fn hide_console(command: &mut std::process::Command) {
    use std::os::windows::process::CommandExt as _;
    command.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn hide_console(_command: &mut std::process::Command) {}

/* 포장 브리지 경로 — **여기서 직접 해석한다.**
   ⚠ 예전에는 `setup()` 에서 resource_dir 로 찾아 환경변수에 넣었다. 그런데 Tauri 는
   setup 보다 **먼저** 창을 띄운다 (실측 로그: `READY 메인 첫 페인트` → `BOOT 앱 setup
   진입`). 그래서 프런트가 먼저 요청하는 오디오·파형 사이드카는 변수가 채워지기 전에
   떠서 **개발용 python 으로 떨어졌다** — 파이썬이 없는 PC 에서는 그 둘이 통째로 죽는다
   (실측: 검색만 sf_bridge.exe, 오디오·파형은 python.exe 로 떴다).
   current_exe 기준으로 한 번만 해석해 캐시하면 호출 순서와 무관해진다.
   (윈도우에서 Tauri 의 resource_dir 은 실행 파일이 있는 폴더와 같다.) */
fn bridge_dir() -> Option<&'static std::path::PathBuf> {
    static BRIDGE: std::sync::OnceLock<Option<std::path::PathBuf>> = std::sync::OnceLock::new();
    BRIDGE.get_or_init(|| {
        /* 개발 빌드는 설치된 python 을 쓴다 — target/debug 에는 리소스가 일부만
           복사돼 깨진 브리지를 물게 된다. 강제로 쓰려면 SOUNDFIELD_USE_BRIDGE=1. */
        if cfg!(debug_assertions)
            && std::env::var("SOUNDFIELD_USE_BRIDGE").ok().as_deref() != Some("1") {
            return None;
        }
        if let Some(path) = std::env::var_os("SOUNDFIELD_BRIDGE_EXE") {
            let path = std::path::PathBuf::from(path);
            if path.is_file() {
                if let Some(dir) = path.parent() { return Some(dir.to_path_buf()); }
            }
        }
        let base = std::env::current_exe().ok()?.parent()?.to_path_buf();
        /* PyInstaller onedir 브리지는 `_internal/` 이 옆에 있어야 자기 python DLL 을
           찾는다. 복사 방식에 따라 한 겹 더 들어갈 수 있어 두 후보를 본다. */
        let found = [
            base.join("bridge").join("sf_bridge"),
            base.join("bridge"),
        ].into_iter().find(|dir| {
            dir.join("sf_bridge.exe").is_file() && dir.join("_internal").is_dir()
        });
        match &found {
            Some(dir) => { let _ = sf_debug(format!("BRIDGE 포장 사용 {}", dir.display())); }
            None => { let _ = sf_debug(format!("BRIDGE 포장 없음 — python 대체 (기준 {})",
                                               base.display())); }
        }
        found
    }).as_ref()
}

/* 역할별 실행 파일 — 작업 관리자에 "SoundField 검색 엔진" 처럼 역할이 보이게 하려고
   `_internal` 을 공유하는 사본을 역할마다 두고 파일 설명을 다르게 박아 뒀다
   (build_bridge.ps1 의 $Roles). 사본이 없으면 예전처럼 sf_bridge.exe 로 돈다. */
fn role_exe_name(kind: &str) -> &'static str {
    match kind {
        "query" => "sf_query.exe",
        "admin" => "sf_admin.exe",
        "audio" => "sf_audio.exe",
        "waveform" => "sf_waveform.exe",
        _ => "sf_bridge.exe",
    }
}

fn python_bridge(kind: &str, script_name: &str) -> std::process::Command {
    if let Some(dir) = bridge_dir() {
        let mut path = dir.join(role_exe_name(kind));
        if !path.is_file() { path = dir.join("sf_bridge.exe"); }
        let mut command = std::process::Command::new(&path);
        command.arg(kind);
            /* ⚠ 포장 브리지는 원본 `app.*` 모듈을 **자기 안에** 얼려 갖고 있다.
               그런데 스크립트가 `sys.path.insert(0, SOUNDFIELD_ORIGINAL_ROOT)` 를 하므로,
               개발 PC 처럼 원본 소스 트리가 존재하면 **소스 쪽이 먼저** 잡혀 설치 PC 와
               다른 코드가 돌았다 (실측: 그 차이로 바이노럴 설치 판정이 True/False 로
               갈렸다). 포장 브리지를 쓸 때는 존재하지 않는 경로로 못박아 항상 번들
               모듈만 쓰게 한다 — 어디서 실행해도 같은 동작이 된다. */
        command.env("SOUNDFIELD_ORIGINAL_ROOT", path.with_file_name("_bundled"));
        hide_console(&mut command);
        return command;
    }
    let script = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("python").join(script_name);
    let mut command = std::process::Command::new("python");
    command.arg(script);
    hide_console(&mut command);
    command
}

fn ensure_audio_service(app: &tauri::AppHandle, state: &tauri::State<AudioState>)
    -> Result<(), String> {
    let mut child_guard = state.child.lock().map_err(|_| "오디오 상태 잠금 실패".to_string())?;
    let mut stdin_guard = state.stdin.lock().map_err(|_| "오디오 입력 잠금 실패".to_string())?;
    let restart = child_guard.as_mut()
        .map(|child| child.try_wait().ok().flatten().is_some())
        .unwrap_or(true);
    if !restart && stdin_guard.is_some() {
        return Ok(());
    }

    let mut child = python_bridge("audio", "sf_audio_service.py")
        .stdin(std::process::Stdio::piped())
        /* 원본 _on_tick 은 엔진 위치를 직접 폴링한다. PoC 는 엔진이 별 프로세스에
           있으므로 사이드카가 보고하는 위치를 받아 프런트로 넘긴다.
           (이전에는 stdout 을 null 로 버려서 프런트가 위치를 전혀 몰랐고,
            재생헤드가 자체 시계로 굴러 소리보다 앞서 나갔다) */
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|e| format!("오디오 브리지를 시작할 수 없습니다: {e}"))?;
    *stdin_guard = child.stdin.take();
    if let Some(stdout) = child.stdout.take() {
        let app = app.clone();
        std::thread::spawn(move || {
            use std::io::BufRead as _;
            let reader = std::io::BufReader::new(stdout);
            for line in reader.lines() {
                let Ok(line) = line else { break };
                let line = line.trim();
                if line.is_empty() { continue; }
                if let Ok(value) = serde_json::from_str::<serde_json::Value>(line) {
                    if value.get("type").and_then(|v| v.as_str()) == Some("pos") {
                        let _ = app.emit("audio-pos", value);
                    } else {
                        let _ = app.emit("audio-event", value);
                    }
                }
            }
        });
    }
    *child_guard = Some(child);
    Ok(())
}

#[tauri::command]
fn sf_audio(app: tauri::AppHandle, cmd: AudioCommand, state: tauri::State<AudioState>)
    -> Result<(), String> {
    ensure_audio_service(&app, &state)?;
    let mut stdin_guard = state.stdin.lock().map_err(|_| "오디오 입력 잠금 실패".to_string())?;
    let stdin = stdin_guard.as_mut()
        .ok_or_else(|| "오디오 브리지 stdin을 열 수 없습니다".to_string())?;
    let mut payload = serde_json::to_vec(&cmd).map_err(|e| e.to_string())?;
    payload.push(b'\n');
    stdin.write_all(&payload).map_err(|e| format!("오디오 명령 전송 실패: {e}"))?;
    stdin.flush().map_err(|e| format!("오디오 명령 flush 실패: {e}"))
}

fn norm_path(path: &str) -> String {
    path.replace('/', "\\").trim_end_matches(['\\', '/']).to_string()
}

fn key_path(path: &str) -> String {
    norm_path(path).to_lowercase()
}

fn parent_path(path: &str) -> Option<String> {
    let p = norm_path(path);
    let idx = p.rfind('\\')?;
    if idx == 0 {
        return None;
    }
    if idx == 2 && p.as_bytes().get(1) == Some(&b':') {
        return Some(format!("{}\\", &p[..2]));
    }
    Some(p[..idx].to_string())
}

fn base_name(path: &str) -> String {
    let trimmed = norm_path(path);
    trimmed
        .rsplit('\\')
        .find(|part| !part.is_empty())
        .map(|part| part.to_string())
        .unwrap_or(trimmed)
}

fn common_parent(paths: &[String]) -> Option<String> {
    if paths.len() < 2 {
        return None;
    }
    let split: Vec<Vec<String>> = paths.iter()
        .map(|path| norm_path(path).split('\\').map(|part| part.to_string()).collect::<Vec<_>>())
        .collect();
    let mut common: Vec<&str> = Vec::new();
    for (idx, value) in split[0].iter().enumerate() {
        if split.iter().all(|segments| {
            segments.get(idx)
                .map(|segment| segment.eq_ignore_ascii_case(value))
                .unwrap_or(false)
        }) {
            common.push(value.as_str());
        } else {
            break;
        }
    }
    if common.is_empty() {
        None
    } else if common.len() == 1 && common[0].ends_with(':') {
        Some(format!("{}\\", common[0].to_uppercase()))
    } else {
        Some(common.join("\\"))
    }
}

/* 폴더 키 -> 자식 키 맵으로 트리를 한 번에 만든다.
   이전 구현은 폴더마다 트리를 재귀 탐색(find_node_mut)하고 형제마다 소문자 String 을
   새로 만들어 비교해 O(F^2) 였다. 실측: 폴더 36,876개에서 2분 이상 + 1.3GB.
   원본 FolderTree.load_folders 는 정렬된 폴더 목록을 한 번 순회하며 cache dict 로
   O(F) 로 만든다 — 같은 복잡도로 맞춘다. */
fn build_subtree(key: &str,
                 children_of: &HashMap<String, Vec<String>>,
                 display: &HashMap<String, String>,
                 counts: &HashMap<String, i64>,
                 incomplete: &HashMap<String, i64>) -> Vec<FolderTreeNode> {
    let Some(kids) = children_of.get(key) else { return Vec::new() };
    kids.iter().map(|child_key| {
        let path = display.get(child_key).cloned().unwrap_or_else(|| child_key.clone());
        FolderTreeNode {
            id: child_key.clone(),
            label: base_name(&path),
            path: path.clone(),
            count: *counts.get(child_key).unwrap_or(&0),
            incomplete: *incomplete.get(child_key).unwrap_or(&0),
            children: build_subtree(child_key, children_of, display, counts, incomplete),
        }
    }).collect()
}

/// 라이브러리 루트(또는 그룹) 노드 아래를 실제 폴더 트리로 채운다.
fn fill_subtrees(node: &mut FolderTreeNode,
                 children_of: &HashMap<String, Vec<String>>,
                 display: &HashMap<String, String>,
                 counts: &HashMap<String, i64>,
                 incomplete: &HashMap<String, i64>) {
    if node.children.is_empty() {
        if !node.path.is_empty() {
            node.children = build_subtree(&key_path(&node.path), children_of, display, counts, incomplete);
        }
        return;
    }
    for child in node.children.iter_mut() {
        fill_subtrees(child, children_of, display, counts, incomplete);
    }
}

fn folder_tree_blocking() -> Result<FolderTreeNode, String> {
    let conn = open_readonly()?;
    let roots: Vec<(String, i64)> = {
        let mut stmt = conn.prepare(
            "SELECT path, COALESCE(sort_order,0) FROM library_roots ORDER BY sort_order, path"
        ).map_err(|e| e.to_string())?;
        let rows = stmt.query_map([], |row| {
            Ok((norm_path(&row.get::<_, String>(0)?), row.get::<_, i64>(1)?))
        }).map_err(|e| e.to_string())?;
        rows.collect::<Result<Vec<_>, _>>().map_err(|e| e.to_string())?
    };

    let mut display: HashMap<String, String> = HashMap::new();
    let mut folder_keys: HashSet<String> = HashSet::new();
    let mut direct: HashMap<String, i64> = HashMap::new();
    let mut direct_incomplete: HashMap<String, i64> = HashMap::new();

    {
        let mut stmt = conn.prepare(
            "SELECT file_path, COALESCE(meta_extracted,0),
                    IFNULL(hidden,0), IFNULL(removed,0)
             FROM audio_files"
        ).map_err(|e| e.to_string())?;
        let mut rows = stmt.query([]).map_err(|e| e.to_string())?;
        /* ⚠ 이 루프는 158만 행을 돈다. 예전 구현은 행마다
             경로 String 복사 → norm_path 2회(각각 replace + to_string) →
             to_lowercase → key.clone() 3~4회
           로 **행당 10여 번 힙 할당**을 했다 (약 1,600만 회). 디버그 빌드 실측
           10,406ms. 결과를 그대로 유지하면서 할당만 없앤다:
             · 경로는 get_ref 로 **빌려서** 쓴다 (복사 없음)
             · 부모 폴더는 슬라이스로 잡는다 (원본 파이썬과 같은 판정 —
               `max(rfind("\\"), rfind("/"))`, database.py:2793)
             · 키는 재사용 버퍼(scratch)에 만들고, HashMap 은 &str 로 조회한다
               → **새 폴더를 처음 만났을 때만** 할당한다 (약 3만 회) */
        let mut scratch = String::with_capacity(320);
        while let Some(row) = rows.next().map_err(|e| e.to_string())? {
            let path_ref = match row.get_ref(0) {
                Ok(value) => value.as_str().unwrap_or(""),
                Err(_) => continue,
            };
            let cut = match (path_ref.rfind('\\'), path_ref.rfind('/')) {
                (Some(a), Some(b)) => a.max(b),
                (Some(a), None) => a,
                (None, Some(b)) => b,
                (None, None) => continue,
            };
            if cut == 0 { continue; }
            let folder = path_ref[..cut].trim_end_matches(['\\', '/']);
            if folder.is_empty() { continue; }

            /* key_path(norm_path(folder)) 와 같은 문자열을 버퍼에 만든다 */
            scratch.clear();
            for ch in folder.chars() {
                if ch == '/' { scratch.push('\\'); }
                else if ch.is_uppercase() { scratch.extend(ch.to_lowercase()); }
                else { scratch.push(ch); }
            }

            let hidden: i64 = row.get(2).unwrap_or(0);
            let removed: i64 = row.get(3).unwrap_or(0);
            let visible = hidden == 0 && removed == 0;

            if !folder_keys.contains(scratch.as_str()) {
                let key = scratch.clone();
                /* display 값은 예전과 같이 **정규화된(역슬래시) 원래 대소문자** 경로 */
                let shown: String = folder.chars()
                    .map(|ch| if ch == '/' { '\\' } else { ch }).collect();
                display.entry(key.clone()).or_insert(shown);
                folder_keys.insert(key);
            }
            if visible {
                match direct.get_mut(scratch.as_str()) {
                    Some(slot) => *slot += 1,
                    None => { direct.insert(scratch.clone(), 1); }
                }
                let meta: i64 = row.get(1).unwrap_or(0);
                if meta == 0 || meta == 2 {
                    match direct_incomplete.get_mut(scratch.as_str()) {
                        Some(slot) => *slot += 1,
                        None => { direct_incomplete.insert(scratch.clone(), 1); }
                    }
                }
            }
        }
    }

    let mut all_keys: HashSet<String> = folder_keys.clone();
    for root in roots.iter().map(|(path, _)| path) {
        let key = key_path(root);
        display.entry(key.clone()).or_insert(root.clone());
        all_keys.insert(key);
    }
    for folder in folder_keys.iter() {
        let mut current = display.get(folder).cloned().unwrap_or_else(|| folder.clone());
        while let Some(parent) = parent_path(&current) {
            let parent_key = key_path(&parent);
            display.entry(parent_key.clone()).or_insert(parent.clone());
            if !all_keys.insert(parent_key.clone()) {
                break;
            }
            current = parent;
        }
    }

    let mut counts: HashMap<String, i64> = all_keys.iter()
        .map(|key| (key.clone(), *direct.get(key).unwrap_or(&0)))
        .collect();
    let mut incomplete: HashMap<String, i64> = all_keys.iter()
        .map(|key| (key.clone(), *direct_incomplete.get(key).unwrap_or(&0)))
        .collect();
    let mut sorted_keys: Vec<String> = all_keys.iter().cloned().collect();
    sorted_keys.sort_by_key(|key| std::cmp::Reverse(key.len()));
    for key in sorted_keys {
        let path = display.get(&key).cloned().unwrap_or_else(|| key.clone());
        if let Some(parent) = parent_path(&path) {
            let parent_key = key_path(&parent);
            let child_count = *counts.get(&key).unwrap_or(&0);
            let child_incomplete = *incomplete.get(&key).unwrap_or(&0);
            *counts.entry(parent_key.clone()).or_insert(0) += child_count;
            *incomplete.entry(parent_key).or_insert(0) += child_incomplete;
        }
    }

    let total = conn.query_row(
        "SELECT COUNT(*) FROM audio_files WHERE IFNULL(hidden,0)=0 AND IFNULL(removed,0)=0",
        [],
        |row| row.get::<_, i64>(0),
    ).unwrap_or(0);
    let total_incomplete = conn.query_row(
        "SELECT COUNT(*) FROM audio_files
         WHERE meta_extracted IN (0,2) AND IFNULL(hidden,0)=0 AND IFNULL(removed,0)=0",
        [],
        |row| row.get::<_, i64>(0),
    ).unwrap_or(0);

    let mut root = FolderTreeNode {
        id: "all".to_string(),
        label: "전체".to_string(),
        path: "".to_string(),
        count: total,
        incomplete: total_incomplete,
        children: Vec::new(),
    };

    let mut normalized_roots: Vec<(String, i64)> = Vec::new();
    let mut seen = HashSet::new();
    for (path, sort_order) in roots {
        let key = key_path(&path);
        if seen.insert(key) {
            normalized_roots.push((path, sort_order));
        }
    }
    normalized_roots.sort_by_key(|(_, order)| *order);

    let mut top_level: Vec<(String, i64)> = Vec::new();
    let mut nested: Vec<String> = Vec::new();
    let mut by_depth = normalized_roots.clone();
    by_depth.sort_by_key(|(path, _)| path.len());
    for (path, order) in by_depth {
        let p_key = key_path(&path);
        let is_nested = top_level.iter().any(|(top, _)| {
            let t_key = key_path(top);
            p_key.starts_with(&(t_key + "\\"))
        });
        if is_nested {
            nested.push(path);
        } else {
            top_level.push((path, order));
        }
    }
    top_level.sort_by_key(|(_, order)| *order);

    let mut drive_groups: HashMap<String, Vec<(String, i64)>> = HashMap::new();
    for item in top_level {
        let drive = if item.0.len() >= 2 && item.0.as_bytes().get(1) == Some(&b':') {
            item.0[..2].to_lowercase()
        } else {
            "".to_string()
        };
        drive_groups.entry(drive).or_default().push(item);
    }

    let mut drives: Vec<String> = drive_groups.keys().cloned().collect();
    drives.sort_by_key(|drive| drive_groups.get(drive)
        .and_then(|items| items.iter().map(|(_, order)| *order).min())
        .unwrap_or(0));

    let mut root_paths_to_expand: Vec<String> = Vec::new();
    for drive in drives {
        let mut group_roots = drive_groups.remove(&drive).unwrap_or_default();
        group_roots.sort_by_key(|(_, order)| *order);
        let common = if !drive.is_empty() && group_roots.len() >= 2 {
            common_parent(&group_roots.iter().map(|(path, _)| path.clone()).collect::<Vec<_>>())
        } else {
            None
        };

        if let Some(common_path) = common {
            let common_key = key_path(&common_path);
            let mut group = FolderTreeNode {
                id: common_key.clone(),
                label: common_path.clone(),
                path: common_path.clone(),
                count: *counts.get(&common_key).unwrap_or(&0),
                incomplete: *incomplete.get(&common_key).unwrap_or(&0),
                children: Vec::new(),
            };
            for (path, _) in group_roots {
                let key = key_path(&path);
                let rel = path[common_path.len()..].trim_start_matches(['\\', '/']).to_string();
                group.children.push(FolderTreeNode {
                    id: key.clone(),
                    label: if rel.is_empty() { base_name(&path) } else { rel },
                    path: path.clone(),
                    count: *counts.get(&key).unwrap_or(&0),
                    incomplete: *incomplete.get(&key).unwrap_or(&0),
                    children: Vec::new(),
                });
                root_paths_to_expand.push(path);
            }
            root.children.push(group);
        } else {
            for (path, _) in group_roots {
                let key = key_path(&path);
                root.children.push(FolderTreeNode {
                    id: key.clone(),
                    label: base_name(&path),
                    path: path.clone(),
                    count: *counts.get(&key).unwrap_or(&0),
                    incomplete: *incomplete.get(&key).unwrap_or(&0),
                    children: Vec::new(),
                });
                root_paths_to_expand.push(path);
            }
        }
    }

    /* nested 루트는 상위 루트 구조 안에서 자연히 나타난다 (원본도 별도 top-level 로
       올리지 않고 ROLE_IS_ROOT 만 표시한다) — 부착은 부모-자식 맵이 처리한다. */
    let _ = root_paths_to_expand;
    let _ = nested;

    let mut children_of: HashMap<String, Vec<String>> = HashMap::new();
    for key in all_keys.iter() {
        let Some(path) = display.get(key) else { continue };
        if let Some(parent) = parent_path(path) {
            let parent_key = key_path(&parent);
            if all_keys.contains(&parent_key) {
                children_of.entry(parent_key).or_default().push(key.clone());
            }
        }
    }
    /* 형제 정렬 — 원본은 sorted(folders) 순서로 항목을 만들어 결과적으로 이름순이 된다 */
    for list in children_of.values_mut() {
        list.sort_by_cached_key(|key| display.get(key)
            .map(|path| base_name(path).to_lowercase())
            .unwrap_or_else(|| key.clone()));
    }

    fill_subtrees(&mut root, &children_of, &display, &counts, &incomplete);

    Ok(root)
}

/* 원본 refresh_async (main_window.py:1469) 1단계 — 루트 목록만. library_roots 테이블만
   읽으므로 즉시 끝난다. count/pending/failed 는 -1(계산 중) 로 두고 2단계에서 채운다. */
fn library_roots_basic_blocking() -> Result<Vec<LibraryRoot>, String> {
    let conn = open_readonly()?;
    let mut stmt = conn.prepare(
        "SELECT path, last_indexed_at, sort_order FROM library_roots ORDER BY sort_order, path"
    ).map_err(|e| e.to_string())?;
    let rows = stmt.query_map([], |row| {
        Ok(LibraryRoot {
            path: row.get::<_, String>(0)?,
            last_indexed_at: row.get::<_, Option<f64>>(1).ok().flatten(),
            sort_order: row.get::<_, Option<i64>>(2).ok().flatten(),
            count: -1,
            pending: -1,
            failed: -1,
            exists: true,
        })
    }).map_err(|e| e.to_string())?;
    rows.collect::<Result<Vec<_>, _>>().map_err(|e| e.to_string())
}

/* 라이브러리 표시 순서 저장 — 원본 Database.reorder_library_roots (database.py:474).
   ⚠ 원본은 넘어온 목록으로 앞쪽 순서를 정하고, **목록에 없는 나머지 루트까지**
   현재 순서(`COALESCE(sort_order,999999999), path`) 뒤에 이어 붙여 0..N-1 을
   **전부 다시 매긴다.** 예전 PoC 는 넘어온 것만 0..k-1 로 덮어써서, 빠진 루트가
   옛 sort_order 를 그대로 들고 있으면 번호가 겹쳐 순서가 뒤섞였다
   (새로 추가한 루트는 sort_order 가 NULL 이라 특히 어긋난다). */
#[tauri::command]
async fn sf_root_order_set(paths: Vec<String>) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let mut conn = open_writable()?;
        let tx = conn.transaction().map_err(|e| e.to_string())?;

        /* 현재 등록된 루트를 원본과 같은 순서로 읽어둔다 */
        let current: Vec<String> = {
            let mut stmt = tx.prepare(
                "SELECT path FROM library_roots
                 ORDER BY COALESCE(sort_order, 999999999), path"
            ).map_err(|e| e.to_string())?;
            let rows = stmt.query_map([], |r| r.get::<_, String>(0))
                .map_err(|e| e.to_string())?;
            rows.collect::<Result<Vec<_>, _>>().map_err(|e| e.to_string())?
        };
        /* 대소문자 무시 매칭 — 원본도 lower() 로 맞춘다 */
        let mut by_key: std::collections::HashMap<String, String> =
            std::collections::HashMap::new();
        for path in &current {
            by_key.insert(key_path(path), path.clone());
        }

        let mut ordered: Vec<String> = Vec::with_capacity(current.len());
        let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
        for path in &paths {
            let key = key_path(path);
            if let Some(actual) = by_key.get(&key) {
                if seen.insert(key) {
                    ordered.push(actual.clone());
                }
            }
        }
        for path in &current {
            if seen.insert(key_path(path)) {
                ordered.push(path.clone());
            }
        }

        for (i, path) in ordered.iter().enumerate() {
            tx.execute("UPDATE library_roots SET sort_order=?1 WHERE path=?2",
                       rusqlite::params![i as i64, path])
                .map_err(|e| e.to_string())?;
        }
        tx.commit().map_err(|e| e.to_string())?;
        Ok(())
    }).await.map_err(|e| e.to_string())?
}

/* 원본 get_blacklist_paths (database.py:529) — ORDER BY path. */
fn blacklist_paths_blocking() -> Result<Vec<BlacklistPath>, String> {
    let conn = open_readonly()?;
    let out: Vec<BlacklistPath> = {
        let mut stmt = conn.prepare(
            "SELECT path, IFNULL(description,'') FROM blacklist_paths ORDER BY path"
        ).map_err(|e| e.to_string())?;
        let rows = stmt.query_map([], |row| {
            Ok(BlacklistPath { path: row.get(0)?, description: row.get(1)? })
        }).map_err(|e| e.to_string())?;
        rows.collect::<Result<Vec<_>, _>>().map_err(|e| e.to_string())?
    };

    Ok(out)
}

/* 블랙리스트 추가/제거 — 원본 Database와 같은 실제 DB 갱신. */
#[tauri::command]
async fn sf_blacklist_add(path: String, description: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let conn = open_writable()?;
        let norm = norm_path(&path);
        /* ⚠ blacklist_paths.path 는 TEXT PRIMARY KEY = **대소문자 구분**이다.
           ON CONFLICT 만 믿으면 `D:\Lib` 와 `d:\lib` 가 두 줄로 들어간다.
           원본은 넣기 전에 `path.lower()` 집합으로 걸러낸다
           (blacklist_panel.add_paths:197). 같은 판정을 SQL 로 옮겼다. */
        let existing: Option<String> = conn.query_row(
            "SELECT path FROM blacklist_paths WHERE path = ?1 COLLATE NOCASE",
            [&norm], |r| r.get(0)).ok();
        match existing {
            Some(found) => {
                conn.execute("UPDATE blacklist_paths SET description = ?2 WHERE path = ?1",
                             rusqlite::params![found, description])
                    .map_err(|e| e.to_string())?;
            }
            None => {
                conn.execute(
                    "INSERT INTO blacklist_paths(path, description) VALUES(?1, ?2)
                     ON CONFLICT(path) DO UPDATE SET description = excluded.description",
                    rusqlite::params![norm, description],
                ).map_err(|e| e.to_string())?;
            }
        }
        Ok(())
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn sf_blacklist_remove(path: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let conn = open_writable()?;
        let norm = norm_path(&path);
        conn.execute("DELETE FROM blacklist_paths WHERE path = ?1 COLLATE NOCASE", [&norm])
            .map_err(|e| e.to_string())?;
        Ok(())
    }).await.map_err(|e| e.to_string())?
}

/* 설정 읽기/쓰기 — Tauri 버전이 메인 앱이 되면서 원본과 같은
   ~/.soundfield_config.json 을 읽고 쓴다. */
/* 파일의 크기·수정시각을 "크기|밀리초" 로 돌려준다. 없으면 빈 문자열.
   파형 메모리 캐시가 **같은 경로의 바뀐 파일**을 그대로 쓰지 않게 하는 데 쓴다
   (조사 Q15). 원본은 파형 디스크 캐시 파일 이름에 크기·수정시각을 넣어 같은 일을
   한다 (player_widget.py:_peaks_cache_path). */
#[tauri::command]
async fn sf_file_stamp(path: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let meta = match std::fs::metadata(&path) {
            Ok(meta) => meta,
            Err(_) => return Ok(String::new()),
        };
        let ms = meta.modified().ok()
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|d| d.as_millis())
            .unwrap_or(0);
        Ok(format!("{}|{}", meta.len(), ms))
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn sf_config_read() -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let path = config_path()?;
        if !path.exists() {
            return Ok(String::new());
        }
        std::fs::read_to_string(&path).map_err(|e| format!("설정을 읽을 수 없습니다: {e}"))
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn sf_config_write(json: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let path = config_path()?;
        std::fs::write(&path, json).map_err(|e| format!("설정을 저장할 수 없습니다: {e}"))
    }).await.map_err(|e| e.to_string())?
}

/* ── 바이노럴 채널 배치 수동 저장 (원본 app/binaural.py:LayoutOverrideStore) ──
   파일은 %LOCALAPPDATA%\\SoundField\\binaural_layouts.json 이고 모양은
     {"version":2, "files":{키:{preset,order?,source?}}, "folders":{폴더키:{채널수:{...}}}}
   PoC 가 원본을 대체하므로(2026-09-02 결정) **원본과 같은 파일**을 읽고 쓴다.
   (layout_store_path 가 %LOCALAPPDATA%\\SoundField 아래 그 파일을 가리킨다) */
fn layout_store_path() -> Result<std::path::PathBuf, String> {
    Ok(local_appdata()?.join("binaural_layouts.json"))
}

#[tauri::command]
async fn sf_layout_store_read() -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let path = layout_store_path()?;
        if !path.exists() {
            return Ok(String::new());
        }
        std::fs::read_to_string(&path)
            .map_err(|e| format!("채널 배치 설정을 읽을 수 없습니다: {e}"))
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn sf_layout_store_write(json: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let path = layout_store_path()?;
        /* 원본 _save_locked 과 같이 .tmp 에 쓰고 os.replace 로 갈아끼운다 */
        let tmp = path.with_extension("tmp");
        std::fs::write(&tmp, json)
            .map_err(|e| format!("채널 배치 설정을 저장할 수 없습니다: {e}"))?;
        std::fs::rename(&tmp, &path)
            .map_err(|e| format!("채널 배치 설정을 저장할 수 없습니다: {e}"))
    }).await.map_err(|e| e.to_string())?
}

/* ── 중복 검수 결과 보관 (사용자 지시 2026-09-15) ────────────────────────────
   창을 닫았다 다시 열면 **직전 결과를 그대로** 보여준다. 다시 스캔하기 전까지는
   목록도 시각도 바뀌지 않는다. 제외를 실행해 목록이 갱신되면 그 갱신된 목록과
   시각으로 덮어쓴다.
   파일은 %LOCALAPPDATA%\SoundField\dup_scan.json.
   ⚠ 크기 상한이 있다 (프런트에서 자른다). 첫 스캔은 35만 그룹까지 나온 적이 있어
     통째로 보관하면 100MB 급 파일이 된다 — 상한을 넘으면 결과 없이 시각만 남기고
     화면이 "다시 스캔이 필요합니다" 로 안내한다. */
fn dup_cache_path() -> Result<std::path::PathBuf, String> {
    Ok(local_appdata()?.join("dup_scan.json"))
}

#[tauri::command]
async fn sf_dup_cache_read() -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let path = dup_cache_path()?;
        if !path.exists() {
            return Ok(String::new());
        }
        std::fs::read_to_string(&path)
            .map_err(|e| format!("중복 검수 기록을 읽을 수 없습니다: {e}"))
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn sf_dup_cache_write(json: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let path = dup_cache_path()?;
        /* .tmp 에 쓰고 갈아끼운다 — 쓰는 도중 앱이 죽어도 반쪽 파일이 남지 않는다 */
        let tmp = path.with_extension("tmp");
        std::fs::write(&tmp, json)
            .map_err(|e| format!("중복 검수 기록을 저장할 수 없습니다: {e}"))?;
        std::fs::rename(&tmp, &path)
            .map_err(|e| format!("중복 검수 기록을 저장할 수 없습니다: {e}"))
    }).await.map_err(|e| e.to_string())?
}

/* 시작 목록 — 원본 Database.query 는 전역 ORDER BY 를 쓰지 않는다 (LIMIT 로 후보를 자르고
   파이썬에서 관련도 정렬). 여기서도 정렬 없이 LIMIT 만 쓴다. hidden/removed 제외. */
fn initial_rows_blocking(limit: i64) -> Result<Vec<DbRow>, String> {
    let conn = open_readonly()?;
    let limit = limit.clamp(100, 5000);
    let sql = "
        SELECT id, file_path, file_name,
               COALESCE(file_size,0), COALESCE(duration,0),
               COALESCE(sample_rate,0), COALESCE(channels,0),
               COALESCE(bit_depth,0), COALESCE(codec,''),
               COALESCE(bitrate,0), COALESCE(title,''), COALESCE(artist,''),
               COALESCE(album,''), COALESCE(genre,''), COALESCE(comments,'')
        FROM audio_files
        WHERE IFNULL(hidden,0)=0 AND IFNULL(removed,0)=0
        LIMIT ?";
    let mut stmt = conn.prepare(sql).map_err(|e| e.to_string())?;
    let rows = stmt.query_map([limit], |row| {
        Ok(DbRow {
            id: row.get(0)?,   // audio_files.id 는 TEXT (해시 문자열)
            file_path: row.get(1)?,
            file_name: row.get(2)?,
            file_size: row.get(3)?,
            duration: row.get(4)?,
            sample_rate: row.get(5)?,
            channels: row.get(6)?,
            bit_depth: row.get(7)?,
            codec: row.get(8)?,
            bitrate: row.get(9)?,
            title: row.get(10)?,
            artist: row.get(11)?,
            album: row.get(12)?,
            genre: row.get(13)?,
            comments: row.get(14)?,
        })
    }).map_err(|e| e.to_string())?;
    rows.collect::<Result<Vec<_>, _>>().map_err(|e| e.to_string())
}

/* 원본 count_files() 상당 — 상태바 "인덱싱됨 N" */
fn indexed_count_blocking() -> Result<i64, String> {
    let conn = open_readonly()?;
    conn.query_row(
        "SELECT COUNT(*) FROM audio_files WHERE IFNULL(hidden,0)=0 AND IFNULL(removed,0)=0",
        [], |r| r.get(0)
    ).map_err(|e| e.to_string())
}

/* 검색은 원본 Database.query() 를 그대로 호출한다 (FTS5 trigram + 단어색인 + UCS 동의어 +
   짧은 토큰 LIKE 폴백 + 블랙리스트 제외). 여기서 재구현하면 정책이 갈라진다. */
fn search_rows_blocking(req: SearchRequest, inner: &SearchInner, id: u64)
    -> Result<serde_json::Value, String> {
    /* 검색은 가장 뜨거운 경로다 — 왕복 시간을 남겨 회귀를 바로 알아챈다
       (상주화 전 315ms → 후 한 자리 ms. 로그 한 줄/검색). */
    let __t0 = std::time::Instant::now();
    ensure_search_service(inner)?;

    /* 요청 전송 — stdin 락은 쓰는 동안만 잡는다. 읽기 락과 분리해야
       "직전 검색이 응답을 기다리는 중" 에도 새 검색을 밀어넣을 수 있다
       (파이썬이 이전 질의를 중단하고 최신 것만 계산한다). */
    {
        let mut guard = inner.stdin.lock().map_err(|_| "검색 입력 잠금 실패".to_string())?;
        let stdin = guard.as_mut().ok_or_else(|| "검색 브리지 stdin 없음".to_string())?;
        let mut payload = serde_json::to_value(&req).map_err(|e| e.to_string())?;
        if let Some(map) = payload.as_object_mut() {
            map.insert("id".into(), serde_json::Value::from(id));
        }
        let mut line = serde_json::to_vec(&payload).map_err(|e| e.to_string())?;
        line.push(b'\n');
        stdin.write_all(&line).map_err(|e| format!("검색 요청 전송 실패: {e}"))?;
        stdin.flush().map_err(|e| format!("검색 요청 전송 실패: {e}"))?;
    }

    /* 응답 수신 — 자기 id 가 나올 때까지 읽는다. 파이썬이 FIFO 로 응답하므로
       앞선(취소된) 응답만 지나가고 순서가 어긋나지 않는다. */
    let mut guard = inner.stdout.lock().map_err(|_| "검색 출력 잠금 실패".to_string())?;
    let reader = guard.as_mut().ok_or_else(|| "검색 브리지 stdout 없음".to_string())?;
    loop {
        let mut line = String::new();
        let read = reader.read_line(&mut line).map_err(|e| format!("검색 응답 수신 실패: {e}"))?;
        if read == 0 {
            return Err("검색 브리지가 종료되었습니다".to_string());
        }
        let value: serde_json::Value = match serde_json::from_str(line.trim()) {
            Ok(v) => v,
            Err(_) => continue,
        };
        let got = value.get("id").and_then(|v| v.as_u64()).unwrap_or(0);
        if got != id {
            continue;                       // 앞선 요청의 응답 — 버린다
        }
        if value.get("aborted").and_then(|v| v.as_bool()).unwrap_or(false) {
            /* 더 새 검색에 밀려 취소됨 — 호출자(backend.ts)는 null 로 받고 무시한다. */
            return Err("검색이 최신 요청으로 대체되었습니다".to_string());
        }
        if let Some(message) = value.get("error").and_then(|v| v.as_str()) {
            return Err(format!("검색 실패: {message}"));
        }
        let rows = value.get("rows").cloned().unwrap_or(serde_json::Value::Array(vec![]));
        let _ = sf_debug(format!("SEARCH {}ms rows={}", __t0.elapsed().as_millis(),
                                 rows.as_array().map(|a| a.len()).unwrap_or(0)));
        return Ok(rows);
    }
}

/* ── 명령 래퍼 ─────────────────────────────────────────────
   동기 #[tauri::command] 는 메인 스레드에서 실행돼 UI 를 멈춘다. 시작 질의만 해도
   전체 카운트 1.2s + 루트별 카운트 + 전체 경로 1.7s 라 창이 수십 초 얼어붙는다.
   원본은 이 작업들을 QThread 워커에서 돌린다 — 같은 정책으로 blocking 풀에 넘긴다. */
#[tauri::command]
async fn sf_folder_tree() -> Result<FolderTreeNode, String> {
    tauri::async_runtime::spawn_blocking(folder_tree_blocking)
        .await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn sf_library_roots_basic() -> Result<Vec<LibraryRoot>, String> {
    tauri::async_runtime::spawn_blocking(library_roots_basic_blocking)
        .await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn sf_library_roots() -> Result<Vec<LibraryRoot>, String> {
    tauri::async_runtime::spawn_blocking(library_roots_blocking)
        .await.map_err(|e| e.to_string())?
}

fn root_counts(path: &str) -> (i64, i64, i64) {
    /* 원본 count_files_under / count_metadata_status_under (database.py:914, 1050) 와 동일 SQL.
       루트마다 158만 행 LIKE 스캔이라 루트당 수 초가 걸린다. 원본은 워커 하나에서
       순차로 돌리지만, 여기서는 루트별로 병렬 처리한다 (읽기 전용이라 안전하고,
       정책/결과는 동일하고 대기 시간만 줄어든다). */
    let Ok(conn) = open_readonly() else { return (0, 0, 0) };
    let pat = format!("{path}%");
    /* 원본은 count_files_under 와 count_metadata_status_under 를 **따로** 호출한다
       (database.py:914, 1050) — 같은 LIKE 조건으로 158만 행을 **두 번** 훑는다.
       세 숫자 모두 "보이는 행"(hidden/removed 제외) 기준이라 한 번의 스캔으로
       똑같이 얻을 수 있다. 값은 동일하고 스캔만 절반이다. */
    let (count, pending, failed): (i64, i64, i64) = conn.query_row(
        "SELECT
            COUNT(*),
            SUM(CASE WHEN meta_extracted = 0 THEN 1 ELSE 0 END),
            SUM(CASE WHEN meta_extracted = 2 THEN 1 ELSE 0 END)
         FROM audio_files
         WHERE file_path LIKE ? COLLATE NOCASE
           AND IFNULL(hidden,0)=0 AND IFNULL(removed,0)=0",
        [&pat],
        |r| Ok((r.get::<_, Option<i64>>(0)?.unwrap_or(0),
                r.get::<_, Option<i64>>(1)?.unwrap_or(0),
                r.get::<_, Option<i64>>(2)?.unwrap_or(0))),
    ).unwrap_or((0, 0, 0));
    (count, pending, failed)
}

fn library_roots_blocking() -> Result<Vec<LibraryRoot>, String> {
    let roots = library_roots_basic_blocking()?;
    let mut out: Vec<LibraryRoot> = Vec::with_capacity(roots.len());
    std::thread::scope(|scope| {
        let handles: Vec<_> = roots.iter()
            .map(|root| {
                let path = root.path.clone();
                scope.spawn(move || {
                    let exists = std::path::Path::new(&path).exists();
                    let (count, pending, failed) = root_counts(&path);
                    (count, pending, failed, exists)
                })
            })
            .collect();
        for (root, handle) in roots.into_iter().zip(handles) {
            let (count, pending, failed, exists) = handle.join().unwrap_or((0, 0, 0, false));
            out.push(LibraryRoot { count, pending, failed, exists, ..root });
        }
    });
    Ok(out)
}

#[tauri::command]
async fn sf_blacklist_paths() -> Result<Vec<BlacklistPath>, String> {
    tauri::async_runtime::spawn_blocking(blacklist_paths_blocking)
        .await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn sf_initial_rows(limit: i64) -> Result<Vec<DbRow>, String> {
    tauri::async_runtime::spawn_blocking(move || initial_rows_blocking(limit))
        .await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn sf_indexed_count() -> Result<i64, String> {
    tauri::async_runtime::spawn_blocking(indexed_count_blocking)
        .await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn sf_search_rows(req: SearchRequest, state: tauri::State<'_, SearchState>)
    -> Result<serde_json::Value, String> {
    let inner = state.inner.clone();
    let id = state.next_id.fetch_add(1, std::sync::atomic::Ordering::SeqCst) + 1;
    tauri::async_runtime::spawn_blocking(move || search_rows_blocking(req, &inner, id))
        .await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn sf_admin(app: tauri::AppHandle, req: AdminRequest, state: tauri::State<'_, AdminState>)
    -> Result<serde_json::Value, String> {
    if state.busy.compare_exchange(
        false, true, std::sync::atomic::Ordering::SeqCst,
        std::sync::atomic::Ordering::SeqCst).is_err() {
        return Ok(serde_json::json!({"success": false, "message": "이미 인덱싱 중입니다"}));
    }
    let busy = state.busy.clone();
    let current_pid = state.current_pid.clone();
    let inner = state.inner.clone();
    tauri::async_runtime::spawn_blocking(move || {
        struct BusyGuard(std::sync::Arc<std::sync::atomic::AtomicBool>,
                         std::sync::Arc<std::sync::atomic::AtomicU32>);
        impl Drop for BusyGuard {
            fn drop(&mut self) {
                self.1.store(0, std::sync::atomic::Ordering::SeqCst);
                self.0.store(false, std::sync::atomic::Ordering::SeqCst);
            }
        }
        let _guard = BusyGuard(busy, current_pid.clone());
        ensure_admin_service(&inner)?;
        {
            /* 요청 한 줄만 쓰고 곧바로 잠금을 놓는다 — 진행 중에도 취소 명령이
               같은 stdin 으로 들어갈 수 있어야 한다 (협조적 취소). */
            let mut in_guard = inner.stdin.lock().map_err(|_| "관리 입력 잠금 실패".to_string())?;
            let stdin = in_guard.as_mut()
                .ok_or_else(|| "관리 브리지 stdin을 열 수 없습니다".to_string())?;
            let mut payload = serde_json::to_vec(&serde_json::json!({
                "op": req.op, "path": req.path, "paths": req.paths,
                "force": req.force, "purge": req.purge,
                "include_pending": req.include_pending,
                "include_failed": req.include_failed,
            })).map_err(|e| e.to_string())?;
            payload.push(b'\n');
            stdin.write_all(&payload).map_err(|e| format!("관리 명령 전송 실패: {e}"))?;
            stdin.flush().map_err(|e| e.to_string())?;
        }
        let mut out_guard = inner.stdout.lock().map_err(|_| "관리 출력 잠금 실패".to_string())?;
        let reader = out_guard.as_mut()
            .ok_or_else(|| "관리 브리지 stdout을 열 수 없습니다".to_string())?;
        let mut line = String::new();
        loop {
            line.clear();
            let read = reader.read_line(&mut line).map_err(|e| e.to_string())?;
            if read == 0 {
                /* 프로세스가 죽었다 — 다음 요청이 새로 띄우도록 핸들을 비운다 */
                *out_guard = None;
                if let Ok(mut g) = inner.stdin.lock() { *g = None; }
                if let Ok(mut g) = inner.child.lock() { if let Some(mut c) = g.take() { let _ = c.kill(); } }
                return Err("관리 브리지가 종료되었습니다".to_string());
            }
            let Ok(value) = serde_json::from_str::<serde_json::Value>(line.trim()) else { continue };
            match value.get("type").and_then(|v| v.as_str()) {
                Some("progress") => {
                    if let Some(data) = value.get("data") {
                        let _ = app.emit("index-event", data.clone());
                    }
                }
                /* 관리 작업 하나의 진행 상황 (검색 제외 등) — 인덱싱 표시와 섞이지
                   않게 별도 통로로 보낸다. sf_admin.emit_op_progress 주석 참고. */
                Some("op_progress") => {
                    if let Some(data) = value.get("data") {
                        let _ = app.emit("admin-op-progress", data.clone());
                    }
                }
                Some("result") => {
                    return value.get("data").cloned()
                        .ok_or_else(|| "관리 결과를 받지 못했습니다".to_string());
                }
                _ => {}
            }
        }
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
fn sf_admin_cancel(state: tauri::State<'_, AdminState>) -> Result<bool, String> {
    /* 상주 프로세스이므로 죽이지 않는다 — 원본과 같은 **협조적 취소**로,
       진행 중 작업만 멈추고 진행분은 보관된다 (다음 갱신이 이어서 진행).
       프로세스를 죽이면 2초 부트스트랩을 다시 물어야 한다. */
    let mut guard = state.inner.stdin.lock().map_err(|_| "관리 입력 잠금 실패".to_string())?;
    let Some(stdin) = guard.as_mut() else { return Ok(false) };
    stdin.write_all(b"{\"op\":\"cancel\"}\n")
        .map_err(|e| format!("인덱싱 중단 실패: {e}"))?;
    stdin.flush().map_err(|e| e.to_string())?;
    Ok(true)
}

fn ensure_wave_service(state: &WaveInner) -> Result<(), String> {
    let mut child_guard = state.child.lock().map_err(|_| "파형 상태 잠금 실패".to_string())?;
    let mut io_guard = state.io.lock().map_err(|_| "파형 입출력 잠금 실패".to_string())?;
    let dead = child_guard.as_mut()
        .map(|child| child.try_wait().ok().flatten().is_some())
        .unwrap_or(true);
    if !dead && io_guard.is_some() {
        return Ok(());
    }
    let mut child = python_bridge("waveform", "sf_waveform_service.py")
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|e| format!("파형 브리지를 시작할 수 없습니다: {e}"))?;
    let stdin = child.stdin.take()
        .ok_or_else(|| "파형 브리지 stdin을 열 수 없습니다".to_string())?;
    let stdout = child.stdout.take()
        .ok_or_else(|| "파형 브리지 stdout을 열 수 없습니다".to_string())?;
    *io_guard = Some((stdin, BufReader::new(stdout)));
    *child_guard = Some(child);
    Ok(())
}

#[tauri::command]
async fn sf_waveform(req: WaveformRequest, state: tauri::State<'_, WaveState>) -> Result<serde_json::Value, String> {
    /* 첫 호출은 파이썬 쪽 import 로 1초쯤 걸린다 — 원본도 WaveformRunnable(백그라운드)
       에서 읽으므로 여기서도 UI 스레드를 막지 않는다. */
    let inner = state.inner.clone();
    tauri::async_runtime::spawn_blocking(move || waveform_blocking(&inner, req))
        .await.map_err(|e| e.to_string())?
}

/* 조기 return 이 여러 곳이라 Drop 으로 busy 를 반드시 내린다 */
struct BusyGuard<'a>(&'a std::sync::atomic::AtomicBool);
impl Drop for BusyGuard<'_> {
    fn drop(&mut self) { self.0.store(false, std::sync::atomic::Ordering::SeqCst); }
}

fn waveform_blocking(state: &WaveInner, req: WaveformRequest) -> Result<serde_json::Value, String> {
    ensure_wave_service(state)?;
    let id = {
        let mut seq = state.seq.lock().map_err(|_| "파형 시퀀스 잠금 실패".to_string())?;
        *seq += 1;
        *seq
    };
    let payload = serde_json::json!({
        "id": id,
        "command": req.mode.clone().unwrap_or_else(|| "peaks".to_string()),
        "path": req.path,
        "channels": req.channels.unwrap_or(1),
        "override": req.r#override.clone().unwrap_or_default(),
        "start_sec": req.start_sec,
        "end_sec": req.end_sec,
        "rate": req.rate,
    });

    let mut io_guard = state.io.lock().map_err(|_| "파형 입출력 잠금 실패".to_string())?;
    /* 여기서부터 응답을 다 읽을 때까지가 '처리 중' 이다. 이 구간에서만 취소(kill)를
       허용한다 — 대기 중에 kill 하면 다음 요청이 프로세스 기동을 기다린다. */
    state.busy.store(true, std::sync::atomic::Ordering::SeqCst);
    let _busy_guard = BusyGuard(&state.busy);
    let (stdin, reader) = io_guard.as_mut()
        .ok_or_else(|| "파형 브리지가 준비되지 않았습니다".to_string())?;
    let mut line = serde_json::to_vec(&payload).map_err(|e| e.to_string())?;
    line.push(b'\n');
    stdin.write_all(&line).map_err(|e| format!("파형 요청 전송 실패: {e}"))?;
    stdin.flush().map_err(|e| format!("파형 요청 flush 실패: {e}"))?;

    let mut buf = String::new();
    let read = reader.read_line(&mut buf).map_err(|e| format!("파형 응답 읽기 실패: {e}"))?;
    if read == 0 {
        *io_guard = None;   // 서비스가 죽었다 — 다음 호출에서 재시작
        return Err("파형 브리지가 종료되었습니다".to_string());
    }
    let value: serde_json::Value = serde_json::from_str(buf.trim())
        .map_err(|e| format!("파형 응답 JSON 파싱 실패: {e}"))?;
    if value.get("ok").and_then(|v| v.as_bool()) != Some(true) {
        let err = value.get("error").and_then(|v| v.as_str()).unwrap_or("알 수 없는 오류");
        /* 이 파서는 파형 브리지의 모든 명령(peaks/quick/layout/region)이 함께 쓴다.
           예전엔 전부 "파형 추출 실패:" 를 붙여서, DAW 내보내기가 실패해도 배너에
           "파형 추출 실패" 라고 떠 사용자를 헷갈리게 했다 (2026-09-16). 명령별로 나눈다.
           region 은 파이썬이 이미 사람이 읽을 문장을 주고 프런트가 "내보내기를
           중단했습니다:" 를 앞에 붙이므로, 여기서는 예외 이름(RuntimeError: …)만 떼고
           그대로 넘긴다. */
        let mode = req.mode.as_deref().unwrap_or("peaks");
        let message = match mode {
            "region" => err.split_once(": ")
                .filter(|(kind, _)| kind.ends_with("Error"))
                .map(|(_, rest)| rest.to_string())
                .unwrap_or_else(|| err.to_string()),
            "layout" => format!("채널 배치 판정 실패: {err}"),
            _ => format!("파형 추출 실패: {err}"),
        };
        return Err(message);
    }
    Ok(value.get("data").cloned().unwrap_or(serde_json::Value::Null))
}

/* ── 탐색기에서 보기 (원본 results_table._reveal_in_explorer) ──
   **파일이 선택된 상태로** 상위 폴더를 연다.

   ⚠ `explorer /select,"경로"` 는 폴더만 열고 선택이 안 되는 경우가 잦다
   (사용자 신고 + 실측: 161자 경로 · 파일 존재 확인 상태에서도 선택 실패).
   그래서 셸 정식 API `SHOpenFolderAndSelectItems` 를 쓴다 — 파일 관리자들이
   같은 목적에 쓰는 API 다. 실패하면 예전 방식(explorer /select)으로 되돌아간다.

   원본의 긴 경로 정책은 그대로 유지한다: MAX_PATH(256) 이상이면 탐색기가 항목을
   못 찾으므로 **한계 안에 들어오는 가장 가까운 부모 폴더**를 대신 연다
   (results_table.py:356). */
#[cfg(windows)]
fn select_in_explorer(path: &std::path::Path) -> Result<(), String> {
    use windows::core::HSTRING;
    use windows::Win32::System::Com::{
        CoInitializeEx, CoUninitialize, COINIT_APARTMENTTHREADED,
    };
    use windows::Win32::UI::Shell::{
        ILFindLastID, ILFree, SHOpenFolderAndSelectItems, SHParseDisplayName,
    };

    let folder = path.parent().ok_or_else(|| "상위 폴더가 없습니다".to_string())?;
    let file_w = HSTRING::from(path.as_os_str());
    let folder_w = HSTRING::from(folder.as_os_str());

    unsafe {
        /* 셸 API 는 COM 초기화가 필요하다. 이미 초기화된 스레드면 그대로 진행한다
           (S_FALSE/RPC_E_CHANGED_MODE) — 그때는 CoUninitialize 를 부르지 않는다. */
        let init = CoInitializeEx(None, COINIT_APARTMENTTHREADED);
        let owns_com = init.is_ok();

        let mut result = Err("PIDL 생성 실패".to_string());
        let mut pidl_folder = std::ptr::null_mut();
        let mut pidl_file = std::ptr::null_mut();
        if SHParseDisplayName(&folder_w, None, &mut pidl_folder, 0, None).is_ok()
            && SHParseDisplayName(&file_w, None, &mut pidl_file, 0, None).is_ok()
        {
            /* 항목은 폴더 기준 **자식 PIDL** 로 넘긴다 (ILFindLastID) */
            let child = ILFindLastID(pidl_file);
            let items = [child as *const _];
            result = SHOpenFolderAndSelectItems(pidl_folder, Some(&items), 0)
                .map_err(|e| format!("탐색기에서 선택할 수 없습니다: {e}"));
        }
        if !pidl_file.is_null() { ILFree(Some(pidl_file)); }
        if !pidl_folder.is_null() { ILFree(Some(pidl_folder)); }
        if owns_com { CoUninitialize(); }
        result
    }
}

#[cfg(not(windows))]
fn select_in_explorer(_path: &std::path::Path) -> Result<(), String> {
    Err("이 플랫폼에서는 지원하지 않습니다".to_string())
}

fn open_folder(dir: &str) -> Result<(), String> {
    let mut command = std::process::Command::new("explorer");
    command.arg(dir);
    hide_console(&mut command);
    command.spawn().map(|_| ()).map_err(|e| format!("탐색기를 열 수 없습니다: {e}"))
}

#[tauri::command]
async fn sf_reveal(path: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        /* 경로 구분자를 역슬래시로 맞춘다 — 원본 os.path.normpath 와 같은 목적.
           탐색기는 `/` 가 섞인 경로에서 항목을 못 찾는다. */
        let norm = path.replace('/', "\\");
        let target = std::path::Path::new(&norm);

        /* 긴 경로 — 원본과 같이 한계 안의 가장 가까운 부모 폴더를 연다
           (results_table.py _reveal_in_explorer_worker). */
        if norm.chars().count() >= LONG_PATH_LIMIT {
            let mut parent = target.parent();
            while let Some(dir) = parent {
                if dir.to_string_lossy().chars().count() < LONG_PATH_LIMIT {
                    break;
                }
                let up = dir.parent();
                if up == Some(dir) { break; }
                parent = up;
            }
            let dir = parent.ok_or_else(|| "경로를 찾을 수 없습니다".to_string())?;
            let _ = sf_debug(format!("REVEAL 경로가 길어 상위 폴더만 엶 ({} 자)",
                                     norm.chars().count()));
            return open_folder(&dir.to_string_lossy());
        }

        /* ⚠ 순서를 바꾸지 말 것 — **셸 API 가 먼저**다 (측정 2026-09-15).
           같은 PC(Windows 11 22631)에서 두 방식을 돌리고, 열린 탐색기 창의
           선택 항목을 COM 으로 세어 확인한 결과다:

             explorer /select,"경로"        → 폴더는 열리고 **선택 0개**
             SHOpenFolderAndSelectItems     → 폴더 열림 + **선택 1개** (성공)

           사용자 신고와 정확히 일치한다: "최하위 폴더까진 들어가는데 사운드
           파일에 포커스가 안 된다" (2026-09-14, 결과표·중복검수 양쪽 모두).

           2026-09-08 에는 반대로 explorer 를 앞에 뒀다. 그때 셸 API 가 성공을
           돌려주면서 선택은 안 하는 경우를 만났기 때문인데, 지금은 그쪽이
           정상이고 explorer 쪽이 안 듣는다. 그래서 **둘 다 남기고 순서만** 바꿨다.
           한쪽이 안 들으면 다른 쪽이 받는다. 다시 뒤집기 전에 위와 같은 방식으로
           **측정부터 할 것.** */
        if select_in_explorer(target).is_ok() {
            return Ok(());
        }
        let _ = sf_debug("REVEAL 셸 API 실패 → explorer /select 시도".to_string());

        /* ⚠ 인자를 하나로 넘기면 OS 가 `"/select,PATH"` 로 통째로 감싸 explorer 가
             옵션을 인식하지 못해 기본 폴더만 열린다. `/select,` 는 quote 밖,
             경로만 quote 안이어야 하므로 raw 명령줄로 넘긴다.
           ⚠ 파일 존재 검사(exists)를 넣지 말 것 — 원본도 일부러 안 한다. NAS 는
             일시적으로 false 를 돌려줄 수 있고, 그러면 선택 없이 폴더만 열린다. */
        {
            use std::os::windows::process::CommandExt as _;
            let safe = norm.replace('"', "");
            let mut command = std::process::Command::new("explorer");
            command.raw_arg(format!("/select,\"{safe}\""));
            hide_console(&mut command);
            if command.spawn().is_ok() {
                return Ok(());
            }
        }

        /* 둘 다 안 되면 최소한 상위 폴더라도 연다 */
        let _ = sf_debug("REVEAL 두 방식 모두 실패 → 상위 폴더만 엶".to_string());
        match target.parent() {
            Some(parent) => open_folder(&parent.to_string_lossy()),
            None => Err("경로를 찾을 수 없습니다".to_string()),
        }
    }).await.map_err(|e| e.to_string())?
}

/* 원본 _LONG_PATH_LIMIT (results_table.py:325) — 이 길이 이상이면 탐색기/DAW 가
   경로를 열지 못한다 (앱 자체는 열 수 있어 재생/파형만 되는 비대칭이 생긴다). */
const LONG_PATH_LIMIT: usize = 256;

/* ── 임시파일 정리 (원본 region_export.cleanup_temp_files, app/main.py:212) ──
   시작할 때 %TEMP%\SoundField_regions (영역 crop) 과 %TEMP%\SoundField_longpath
   (MAX_PATH 초과 드래그 복사본) 에서 24시간 지난 파일을 지운다.
   두 폴더는 원본 앱과 공유하므로 정리 규칙도 같아야 한다.
   ⚠ 파형 피크 캐시(peaks_cache)의 세대 교체 정리는 하지 않는다 — 그건 원본 앱이
      자기 버전 마커로 판단해 지우는 것이고, PoC 가 끼어들면 사용자의 실제 캐시를
      날릴 수 있다. */
fn cleanup_temp_files() {
    let cutoff = std::time::SystemTime::now()
        .checked_sub(std::time::Duration::from_secs(24 * 3600));
    let Some(cutoff) = cutoff else { return };
    let temp = std::env::temp_dir();
    for name in ["SoundField_regions", "SoundField_longpath"] {
        let dir = temp.join(name);
        let Ok(entries) = std::fs::read_dir(&dir) else { continue };
        for entry in entries.flatten() {
            let Ok(meta) = entry.metadata() else { continue };
            if !meta.is_file() { continue; }
            if let Ok(modified) = meta.modified() {
                if modified < cutoff {
                    let _ = std::fs::remove_file(entry.path());
                }
            }
        }
    }
}

/* ── DAW 로 넘길 경로 만들기 (원본 _draggable_path, results_table.py:326) ──
   MAX_PATH 를 넘는 파일은 %TEMP%\SoundField_longpath 에 짧은 이름으로 복사해
   그 경로를 넘긴다. 복사본 이름 = 원본 stem + "_" + sha1 앞 8자 + 확장자
   (해시를 뒤에 둬 이름 앞부분이 원본과 같게 — 이름으로 재검색이 된다).
   같은 크기의 복사본이 이미 있으면 재사용한다. */
fn draggable_path(path: &str) -> String {
    if path.chars().count() < LONG_PATH_LIMIT {
        return path.to_string();
    }
    let src = std::path::Path::new(path);
    let (Some(stem), Ok(meta)) = (src.file_stem(), std::fs::metadata(src)) else {
        return path.to_string();
    };
    let ext = src.extension().map(|e| format!(".{}", e.to_string_lossy())).unwrap_or_default();
    /* 원본은 sha1 앞 8자를 쓴다. 여기서는 의존성을 늘리지 않도록 FNV-1a 64bit 의
       앞 8자를 쓴다 — 목적(충돌 방지 + 실행 간 동일한 이름으로 재사용)은 같다.
       ⚠ 표준 DefaultHasher 는 프로세스마다 시드가 달라 재사용이 깨지므로 쓰지 않는다. */
    let digest = {
        let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
        for byte in path.as_bytes() {
            hash ^= *byte as u64;
            hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
        }
        format!("{:08x}", (hash >> 32) as u32)
    };
    let dir = std::env::temp_dir().join("SoundField_longpath");
    let dst = dir.join(format!("{}_{}{}", stem.to_string_lossy(), digest, ext));
    let same = std::fs::metadata(&dst).map(|m| m.len() == meta.len()).unwrap_or(false);
    if !same {
        if std::fs::create_dir_all(&dir).is_err() { return path.to_string(); }
        if std::fs::copy(src, &dst).is_err() { return path.to_string(); }
    }
    dst.to_string_lossy().to_string()
}

/* ── 드래그 전 셸 확인 (크래시 원인 제거) ────────────────────────────────────
   ⚠ 이 확인을 빼면 **앱이 말없이 즉사한다.** 실측 로그:
       DRAG start n=1
       PANIC Option::unwrap() on None @ drag-2.1.1\...\windows\mod.rs:370
       PANIC Result::unwrap() on Err(RecvError) @ tauri-plugin-drag-2.1.1\...\commands.rs:162
   drag 크레이트 370행은 `get_shell_item_array(paths).unwrap()` 이다. 그 안의
   `SHCreateShellItemArrayFromIDLists` 가 실패하면 None → unwrap 패닉이고, 패닉이
   COM 드래그 콜백을 거슬러 올라가려 하므로 되돌리기가 불가능해 프로세스가 죽는다
   (실측: 패닉 두 줄을 남기고 PID 소멸. `panic="abort"` 제거로도 못 막는다).
   그래서 **크레이트가 실패하는 바로 그 호출을 우리가 먼저** 해 본다. 실패하면
   드래그를 시작하지 않고 프런트에 알린다 — 죽는 대신 아무 일도 안 일어난다.
   실패는 일시적인 경우가 많아(네트워크 드라이브가 바쁠 때) 짧게 재시도한다. */
#[cfg(windows)]
fn shell_drag_ready(paths: &[String]) -> bool {
    use std::os::windows::ffi::OsStrExt as _;
    use windows::Win32::UI::Shell::{ILCreateFromPathW, ILFree, SHCreateShellItemArrayFromIDLists};
    use windows::Win32::UI::Shell::Common::ITEMIDLIST;

    if paths.is_empty() { return false; }
    /* ⚠ **되돌리지 않는다(CoUninitialize 금지).** 이 검사는 드래그와 같은 스레드에서
       돌고, 바로 뒤에 drag 크레이트가 같은 초기화 상태를 쓴다. 되돌리면 검사만 통과하고
       실제 드래그가 다시 죽는다. S_FALSE(이미 초기화됨)·RPC_E_CHANGED_MODE 도 그대로 둔다. */
    use windows::Win32::System::Com::{CoInitializeEx, COINIT_APARTMENTTHREADED};
    let _ = unsafe { CoInitializeEx(None, COINIT_APARTMENTTHREADED) };
    unsafe {
        let mut ids: Vec<*mut ITEMIDLIST> = Vec::with_capacity(paths.len());
        let free = |ids: &Vec<*mut ITEMIDLIST>| {
            for id in ids { ILFree(Some(*id as *const ITEMIDLIST)); }
        };
        for path in paths {
            /* ⚠ 경로를 **정규화하지 않는다.** 사본 drag 크레이트에서도 정규화를 없앴다
               (Cargo.toml 의 `[patch.crates-io]` 주석과 vendor/drag 안 주석 참고).
               여기서 정규화하면 검사와 실제 드래그가 서로 다른 경로를 보게 되어,
               검사만 통과하고 드래그는 실패하는 상태가 된다 (실측으로 겪었다).
               원래 경로가 곧 크레이트에 넘어가는 값이다. */
            let wide: Vec<u16> = std::ffi::OsStr::new(path)
                .encode_wide().chain(std::iter::once(0)).collect();
            let id = ILCreateFromPathW(windows::core::PCWSTR::from_raw(wide.as_ptr()));
            if id.is_null() {
                free(&ids);
                let _ = sf_debug(format!(
                    "DRAG 셸 해석 실패 (ILCreateFromPathW null, {}자) {path}",
                    path.chars().count()));
                return false;
            }
            ids.push(id);
        }
        let list: Vec<*const ITEMIDLIST> = ids.iter().map(|id| *id as *const ITEMIDLIST).collect();
        let result = SHCreateShellItemArrayFromIDLists(&list);
        free(&ids);
        match result {
            Ok(_) => {
                let _ = sf_debug(format!("DRAG 검사 통과 n={} 첫경로={}",
                                         paths.len(), &paths[0]));
                true
            }
            Err(e) => {
                let _ = sf_debug(format!(
                    "DRAG 셸 항목배열 실패 hr=0x{:08X} n={} 첫경로={}",
                    e.code().0 as u32, paths.len(), &paths[0]));
                false
            }
        }
    }
}

#[cfg(not(windows))]
fn shell_drag_ready(paths: &[String]) -> bool { !paths.is_empty() }

#[tauri::command]
async fn sf_drag_paths(app: tauri::AppHandle, paths: Vec<String>) -> Result<Vec<String>, String> {
    let ready: Vec<String> = tauri::async_runtime::spawn_blocking(move || {
        paths.iter().map(|p| draggable_path(p)).collect::<Vec<String>>()
    }).await.map_err(|e| e.to_string())?;

    /* ⚠ 검사는 **메인 스레드**에서 해야 한다. tauri-plugin-drag 가 드래그를
       `run_on_main_thread` 로 실행하므로, 다른 스레드에서 검사하면 COM 상태가 달라
       **검사는 통과하는데 드래그는 죽는다** (실측: 워커에서 검사 통과 후 메인 스레드
       패닉으로 앱 사망). 같은 스레드에서 확인해야 예측이 맞는다.
       일시적 실패(바쁜 네트워크 드라이브)는 잠깐 뒤 성공하므로 3회까지 본다. */
    for attempt in 0..3 {
        let (tx, rx) = std::sync::mpsc::channel();
        let probe = ready.clone();
        app.run_on_main_thread(move || { let _ = tx.send(shell_drag_ready(&probe)); })
            .map_err(|e| e.to_string())?;
        let ok = tauri::async_runtime::spawn_blocking(move || {
            rx.recv_timeout(std::time::Duration::from_secs(3)).unwrap_or(false)
        }).await.map_err(|e| e.to_string())?;
        if ok { return Ok(ready); }
        if attempt < 2 { std::thread::sleep(std::time::Duration::from_millis(120)); }
    }
    Err("드래그를 시작할 수 없습니다 — 파일을 다시 확인해 주세요".to_string())
}

/* 원본 QDesktopServices.openUrl — IEM 무료 설치 페이지 안내에서만 쓴다.
   외부로 나가는 동작이라 앱이 아는 https 주소만 허용한다. */
#[tauri::command]
fn sf_open_url(url: String) -> Result<(), String> {
    if !url.starts_with("https://") {
        return Err("https 주소만 열 수 있습니다".to_string());
    }
    std::process::Command::new("rundll32")
        .args(["url.dll,FileProtocolHandler", &url])
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("브라우저를 열 수 없습니다: {e}"))
}

/* 검색 색인 어긋남 플래그 — 원본 Database.is_fts_stale (database.py:2824) 는
   index_meta 의 "fts_stale" 값이 "1" 인지만 본다. 읽기 전용이라 그대로 읽는다. */
#[tauri::command]
async fn sf_fts_stale() -> Result<bool, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let conn = open_readonly()?;
        let value: Option<String> = conn.query_row(
            "SELECT value FROM index_meta WHERE key = 'fts_stale'",
            [], |r| r.get(0)).ok();
        Ok(value.as_deref() == Some("1"))
    }).await.map_err(|e| e.to_string())?
}

/* ── 검색 제외(숨김) 목록 (원본 Database.get_hidden_paths / count_hidden,
      database.py:2112·2130) ──
   (file_path, dup_orphan) 을 file_path 순으로, 검색어가 있으면 숨김 **전체**에서
   부분문자열로 찾는다 (표시 상한과 무관). dup_orphan=1 = [중복 아님 · 미복원].
   원본은 표시 직전 verify_dup_orphans 로 태그가 여전히 유효한지 확인해
   쌍둥이가 돌아온 행은 태그를 자동 해제한다 — 같은 프로브를 그대로 옮겼다. */
#[derive(serde::Serialize)]
struct HiddenRow {
    path: String,
    orphan: bool,
}

#[derive(serde::Serialize)]
struct HiddenResult {
    rows: Vec<HiddenRow>,
    /// 검색어에 일치하는 전체 개수 (표시 상한 무관)
    matched: i64,
    /// 숨김 전체 개수
    total: i64,
}

#[tauri::command]
async fn sf_hidden(search: String, limit: i64) -> Result<HiddenResult, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let conn = open_readonly()?;
        let term = search.trim().to_string();
        /* 원본 _esc_like — LIKE 특수문자를 \\ 로 이스케이프한다 */
        let like = format!("%{}%", term
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_"));
        let mut rows: Vec<HiddenRow> = Vec::new();
        let sql = if term.is_empty() {
            "SELECT file_path, IFNULL(dup_orphan,0) FROM audio_files
             WHERE hidden = 1 ORDER BY file_path LIMIT ?1"
        } else {
            "SELECT file_path, IFNULL(dup_orphan,0) FROM audio_files
             WHERE hidden = 1 AND file_path LIKE ?2 ESCAPE '\\'
             ORDER BY file_path LIMIT ?1"
        };
        {
            let mut stmt = conn.prepare(sql).map_err(|e| e.to_string())?;
            let cap = limit.clamp(1, 20000);
            let args: Vec<&dyn rusqlite::ToSql> = if term.is_empty() {
                vec![&cap]
            } else {
                vec![&cap, &like]
            };
            let mapped = stmt.query_map(args.as_slice(), |r| {
                Ok(HiddenRow { path: r.get(0)?, orphan: r.get::<_, i64>(1)? != 0 })
            }).map_err(|e| e.to_string())?;
            for row in mapped.flatten() {
                rows.push(row);
            }
        }

        /* 원본 verify_dup_orphans — 보이는 사본이 다시 생겼으면 태그를 내린다 */
        let flagged: Vec<String> = rows.iter().filter(|r| r.orphan)
            .map(|r| r.path.clone()).collect();
        if !flagged.is_empty() {
            let mut still: std::collections::HashSet<String> = std::collections::HashSet::new();
            for chunk in flagged.chunks(400) {
                let ph = vec!["?"; chunk.len()].join(",");
                let q = format!(
                    "SELECT h.file_path,
                       EXISTS(SELECT 1 FROM audio_files v
                         WHERE v.file_name = h.file_name COLLATE NOCASE
                           AND v.file_size = h.file_size
                           AND IFNULL(v.hidden,0)=0 AND IFNULL(v.removed,0)=0)
                     FROM audio_files h WHERE h.file_path IN ({ph})");
                if let Ok(mut stmt) = conn.prepare(&q) {
                    let args: Vec<&dyn rusqlite::ToSql> =
                        chunk.iter().map(|v| v as &dyn rusqlite::ToSql).collect();
                    if let Ok(it) = stmt.query_map(args.as_slice(), |r| {
                        Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?))
                    }) {
                        for (path, has_visible) in it.flatten() {
                            if has_visible == 0 { still.insert(path); }
                        }
                    }
                }
            }
            /* ⚠ 원본은 태그가 무효해진 행을 **DB 에서도** 내린다
               (`UPDATE audio_files SET dup_orphan = 0`, database.py:2097).
               예전 PoC 는 화면에서만 내려서, 다음에 목록을 열 때마다 같은 프로브를
               다시 하고 다른 화면(중복 검수)에서는 여전히 "중복 아님" 으로 보였다.
               이 연결은 read-only 라 쓰기 연결로 따로 반영한다. */
            let back_to_dup: Vec<String> = rows.iter()
                .filter(|r| r.orphan && !still.contains(&r.path))
                .map(|r| r.path.clone()).collect();
            for row in rows.iter_mut() {
                if row.orphan && !still.contains(&row.path) { row.orphan = false; }
            }
            if !back_to_dup.is_empty() {
                if let Ok(write) = open_writable() {
                    for chunk in back_to_dup.chunks(500) {
                        let ph = vec!["?"; chunk.len()].join(",");
                        let q = format!(
                            "UPDATE audio_files SET dup_orphan = 0 WHERE file_path IN ({ph})");
                        let args: Vec<&dyn rusqlite::ToSql> =
                            chunk.iter().map(|v| v as &dyn rusqlite::ToSql).collect();
                        let _ = write.execute(&q, args.as_slice());
                    }
                }
            }
        }

        let total: i64 = conn.query_row(
            "SELECT COUNT(*) FROM audio_files WHERE hidden = 1", [], |r| r.get(0)).unwrap_or(0);
        let matched: i64 = if term.is_empty() {
            total
        } else {
            conn.query_row(
                "SELECT COUNT(*) FROM audio_files WHERE hidden = 1
                 AND file_path LIKE ?1 ESCAPE '\\'",
                rusqlite::params![like], |r| r.get(0)).unwrap_or(0)
        };
        Ok(HiddenResult { rows, matched, total })
    }).await.map_err(|e| e.to_string())?
}

fn history_path() -> Result<std::path::PathBuf, String> {
    let local = std::env::var_os("LOCALAPPDATA")
        .ok_or_else(|| "LOCALAPPDATA 환경변수를 찾을 수 없습니다".to_string())?;
    Ok(std::path::PathBuf::from(local).join("SoundField").join("history.json"))
}

/* ── 재생 히스토리 (원본 player_widget._load_history, HISTORY_FILE) ──
   %LOCALAPPDATA%\SoundField\history.json 은 "오래된 -> 최신" 순 문자열 배열이고
   최대 100개(HISTORY_MAX)다. 화면에서는 최신이 위로 간다 (history_panel.set_items).
   ⚠ 이 PoC 는 원본 앱이 쓰는 파일을 덮어쓰지 않는다 — 읽기만 하고, 이 세션에서
   재생한 항목은 화면 상태로만 얹는다. */
#[tauri::command]
async fn sf_history() -> Result<Vec<String>, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let path = history_path()?;
        if !path.exists() {
            return Ok(Vec::new());
        }
        let text = std::fs::read_to_string(&path)
            .map_err(|e| format!("히스토리를 읽을 수 없습니다: {e}"))?;
        let list: Vec<String> = serde_json::from_str(&text).unwrap_or_default();
        let start = list.len().saturating_sub(100);   // HISTORY_MAX
        Ok(list[start..].to_vec())
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
fn sf_waveform_cancel(state: tauri::State<'_, WaveState>) -> Result<bool, String> {
    /* 원본 WaveformRunnable.cancel 과 같은 용도. 상주 프로세스는 한 요청을 처리하는
       동안 stdin 명령을 못 받으므로, 파일 전환 시 프로세스를 종료해 현재 read를
       깨우고 다음 요청에서 깨끗하게 다시 띄운다. */
    /* ⚠ **처리 중이 아니면 아무것도 하지 않는다.**
       예전에는 파일을 바꿀 때마다 무조건 프로세스를 죽였다. 그러면 다음 요청이
       파이썬 기동(실측 약 578ms)을 기다려 "파형 로딩 중" 이 떴다 —
       사이드카가 살아 있으면 quick 0.5ms + peaks 10ms = 11ms 로 끝나는 일이었다.
       메모리 캐시에 있는 직전 파일만 이 비용을 피해서, 사용자에게는
       "직전 하나만 기억한다" 로 보였다 (신고 2026-09-04).
       kill 은 긴 추출을 실제로 버려야 할 때만 의미가 있다. */
    if !state.inner.busy.load(std::sync::atomic::Ordering::SeqCst) {
        return Ok(false);
    }
    let killed = {
        let mut child = state.inner.child.lock()
            .map_err(|_| "파형 상태 잠금 실패".to_string())?;
        if let Some(mut process) = child.take() {
            let _ = process.kill();
            true
        } else {
            false
        }
    };
    let mut io = state.inner.io.lock()
        .map_err(|_| "파형 입출력 잠금 실패".to_string())?;
    *io = None;
    let _ = sf_debug("WAVE 취소 — 처리 중이던 요청을 버리고 사이드카 재시작".into());
    Ok(killed)
}

fn write_history_items(items: &[String]) -> Result<(), String> {
    let path = history_path()?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("히스토리 폴더를 만들 수 없습니다: {e}"))?;
    }
    let tmp = path.with_extension("tmp");
    let json = serde_json::to_string_pretty(items).map_err(|e| e.to_string())?;
    std::fs::write(&tmp, json)
        .map_err(|e| format!("히스토리를 저장할 수 없습니다: {e}"))?;
    std::fs::rename(&tmp, &path)
        .map_err(|e| format!("히스토리를 저장할 수 없습니다: {e}"))
}

#[tauri::command]
async fn sf_history_record(path: String) -> Result<Vec<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let mut items = if history_path()?.exists() {
            serde_json::from_str::<Vec<String>>(
                &std::fs::read_to_string(history_path()?).unwrap_or_default())
                .unwrap_or_default()
        } else { Vec::new() };
        items.retain(|item| !item.eq_ignore_ascii_case(&path));
        items.push(path);
        if items.len() > 100 { items.drain(..items.len() - 100); }
        write_history_items(&items)?;
        Ok(items)
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn sf_history_clear() -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(|| write_history_items(&[]))
        .await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn sf_row_by_path(path: String) -> Result<Option<DbRow>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let conn = open_readonly()?;
        let sql = "SELECT id, file_path, file_name, COALESCE(file_size,0),
                          COALESCE(duration,0), COALESCE(sample_rate,0),
                          COALESCE(channels,0), COALESCE(bit_depth,0),
                          COALESCE(codec,''), COALESCE(bitrate,0),
                          COALESCE(title,''), COALESCE(artist,''),
                          COALESCE(album,''), COALESCE(genre,''), COALESCE(comments,'')
                   FROM audio_files WHERE lower(file_path)=lower(?1) LIMIT 1";
        let mut stmt = conn.prepare(sql).map_err(|e| e.to_string())?;
        let mut rows = stmt.query([path]).map_err(|e| e.to_string())?;
        let Some(row) = rows.next().map_err(|e| e.to_string())? else { return Ok(None) };
        Ok(Some(DbRow {
            id: row.get(0).map_err(|e| e.to_string())?,
            file_path: row.get(1).map_err(|e| e.to_string())?,
            file_name: row.get(2).map_err(|e| e.to_string())?,
            file_size: row.get(3).map_err(|e| e.to_string())?,
            duration: row.get(4).map_err(|e| e.to_string())?,
            sample_rate: row.get(5).map_err(|e| e.to_string())?,
            channels: row.get(6).map_err(|e| e.to_string())?,
            bit_depth: row.get(7).map_err(|e| e.to_string())?,
            codec: row.get(8).map_err(|e| e.to_string())?,
            bitrate: row.get(9).map_err(|e| e.to_string())?,
            title: row.get(10).map_err(|e| e.to_string())?,
            artist: row.get(11).map_err(|e| e.to_string())?,
            album: row.get(12).map_err(|e| e.to_string())?,
            genre: row.get(13).map_err(|e| e.to_string())?,
            comments: row.get(14).map_err(|e| e.to_string())?,
        }))
    }).await.map_err(|e| e.to_string())?
}

#[derive(serde::Serialize)]
struct IncompleteRow {
    file_path: String,
    file_name: String,
    status: String,      // "pending" | "failed"
    reason: String,
}

/* 원본 _FetchIncompleteWorker 는 목록과 **필터 전체 개수**를 함께 돌려준다
   (get_incomplete_metadata_under + count_incomplete_under). limit(5000)을 넘으면
   다이얼로그가 "표시 N / 필터 전체 T" 로 바꿔 표시하기 때문이다. */
#[derive(serde::Serialize)]
struct IncompleteResult {
    rows: Vec<IncompleteRow>,
    total: i64,
    /* 원본 header 의 대기/실패 총계 (count_metadata_status_under) */
    pending: i64,
    failed: i64,
}

/* ── 미완료 항목 (원본 Database.get_incomplete_metadata_under, database.py:1125) ──
   meta_extracted 0=대기 / 2=실패. 정렬 ORDER BY meta_extracted DESC, file_path
   (= 실패가 먼저), hidden/removed 제외, LIMIT 기본 5000. */
#[tauri::command]
async fn sf_incomplete(root: String, include_pending: bool, include_failed: bool,
                       limit: i64) -> Result<IncompleteResult, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let mut states: Vec<&str> = Vec::new();
        if include_pending { states.push("0"); }
        if include_failed { states.push("2"); }
        if states.is_empty() {
            return Ok(IncompleteResult { rows: Vec::new(), total: 0, pending: 0, failed: 0 });
        }
        let conn = open_readonly()?;
        let sql = format!(
            "SELECT file_path, file_name, COALESCE(meta_extracted,0), IFNULL(meta_error,'')
             FROM audio_files
             WHERE meta_extracted IN ({})
               AND file_path LIKE ? COLLATE NOCASE
               AND IFNULL(hidden,0)=0 AND IFNULL(removed,0)=0
             ORDER BY meta_extracted DESC, file_path
             LIMIT ?", states.join(","));
        let mut stmt = conn.prepare(&sql).map_err(|e| e.to_string())?;
        let pat = format!("{}%", norm_path(&root));
        let rows = stmt.query_map(rusqlite::params![pat, limit.clamp(1, 20000)], |row| {
            let meta: i64 = row.get(2)?;
            Ok(IncompleteRow {
                file_path: row.get(0)?,
                file_name: row.get(1)?,
                status: if meta == 2 { "failed".to_string() } else { "pending".to_string() },
                reason: row.get(3)?,
            })
        }).map_err(|e| e.to_string())?;
        let rows = rows.collect::<Result<Vec<_>, _>>().map_err(|e| e.to_string())?;

        /* 필터 전체 개수 (원본 count_incomplete_under) */
        let total: i64 = conn.query_row(
            &format!("SELECT COUNT(*) FROM audio_files
                      WHERE meta_extracted IN ({})
                        AND file_path LIKE ?1 COLLATE NOCASE
                        AND IFNULL(hidden,0)=0 AND IFNULL(removed,0)=0", states.join(",")),
            rusqlite::params![pat], |r| r.get(0)).unwrap_or(0);
        /* 헤더용 대기/실패 총계 (원본 count_metadata_status_under) — 필터와 무관.
           ⚠ `COALESCE(meta_extracted,0)=0` 처럼 컬럼을 함수로 감싸면 인덱스를 쓸 수
           없어 file_path 접두 인덱스로 떨어지고, 큰 라이브러리에서는 그 아래 전 행을
           훑는다 (실측: Y:\[Library] 694,594행 → 쿼리당 1.93초, 2개면 4초.
           그래서 목록이 2초 주기 갱신 안에 도착하지 못해 **영원히 안 뜨는** 것처럼
           보였다). 인덱스를 타는 `meta_extracted IN (0,2)` 한 방으로 묶는다
           (실측 0.00초, 1363배). NULL 은 따로 세어 대기에 더한다 (원본 COALESCE 동등). */
        let mut pending: i64 = 0;
        let mut failed: i64 = 0;
        if let Ok(mut stmt) = conn.prepare(
            "SELECT meta_extracted, COUNT(*) FROM audio_files
             WHERE meta_extracted IN (0,2) AND file_path LIKE ?1 COLLATE NOCASE
               AND IFNULL(hidden,0)=0 AND IFNULL(removed,0)=0
             GROUP BY meta_extracted") {
            if let Ok(mapped) = stmt.query_map(rusqlite::params![pat], |r| {
                Ok((r.get::<_, i64>(0)?, r.get::<_, i64>(1)?))
            }) {
                for got in mapped.flatten() {
                    if got.0 == 2 { failed += got.1; } else { pending += got.1; }
                }
            }
        }
        pending += conn.query_row(
            "SELECT COUNT(*) FROM audio_files
             WHERE meta_extracted IS NULL AND file_path LIKE ?1 COLLATE NOCASE
               AND IFNULL(hidden,0)=0 AND IFNULL(removed,0)=0",
            rusqlite::params![pat], |r| r.get::<_, i64>(0)).unwrap_or(0);
        Ok(IncompleteResult { rows, total, pending, failed })
    }).await.map_err(|e| e.to_string())?
}

/* 드래그 프리뷰 아이콘 — 플러그인이 파일 경로를 요구한다.
   개발/번들 어디서든 같은 경로를 쓰도록 PoC 폴더에 한 번 써 두고 그 경로를 준다.
   원본은 파일명 라벨 pixmap 을 그려 붙이지만(그리고 IgnoreAction 커서를 투명 처리),
   플러그인은 이미지 파일만 받으므로 작은 아이콘으로 대체한다. */
#[derive(serde::Serialize)]
struct DupGroup {
    file_name: String,
    file_size: i64,
    paths: Vec<String>,
}

/* 중복 검수 진행 상황 — 훑은 행 수 / 전체 행 수. 원본 progress_cb(done, total). */
#[derive(Clone, serde::Serialize)]
struct DupProgress {
    done: i64,
    total: i64,
}

/* ── 중복 검수 (원본 Database.find_duplicate_groups, database.py:1430) ──
   파일명 + 파일 크기가 같은 항목을 그룹으로 묶는다. 읽기 전용이라 그대로 옮겼다.
   원본도 (1) 전체를 훑어 (file_name, file_size) 카운트 → (2) 2개 이상인 키만
   TEMP TABLE 로 만들어 조인 → (3) ORDER BY file_name, file_size, file_path.
   "검색 제외"/"제외 관리 복원" 은 쓰기라서 sf_admin(hide_paths/unhide_*)으로 간다. */
#[tauri::command]
async fn sf_duplicates(app: tauri::AppHandle) -> Result<Vec<DupGroup>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let conn = open_readonly()?;
        let visible = "file_size IS NOT NULL AND IFNULL(hidden,0)=0 AND IFNULL(removed,0)=0";
        /* 전체 행 수를 먼저 센다 — 진행바의 분모. 원본도 같은 순서다
           (database.py find_duplicates: COUNT(*) 먼저, 그 다음 훑기). */
        let total: i64 = conn.query_row(
            &format!("SELECT COUNT(*) FROM audio_files WHERE {visible}"), [],
            |row| row.get(0),
        ).map_err(|e| e.to_string())?;
        let _ = app.emit("dup-progress", DupProgress { done: 0, total });

        let mut counts: HashMap<(String, i64), i64> = HashMap::new();
        {
            let mut stmt = conn.prepare(
                &format!("SELECT file_name, COALESCE(file_size,0) FROM audio_files
                          WHERE {visible}")
            ).map_err(|e| e.to_string())?;
            let mut rows = stmt.query([]).map_err(|e| e.to_string())?;
            let mut done: i64 = 0;
            while let Some(row) = rows.next().map_err(|e| e.to_string())? {
                let key = (row.get::<_, String>(0).unwrap_or_default(),
                           row.get::<_, i64>(1).unwrap_or(0));
                *counts.entry(key).or_insert(0) += 1;
                done += 1;
                /* 원본은 fetchmany(50000) 묶음마다 알린다. 같은 간격으로 보낸다 —
                   행마다 보내면 이벤트가 158만 번 날아가 화면이 오히려 멈춘다. */
                if done % 50_000 == 0 {
                    let _ = app.emit("dup-progress", DupProgress { done, total });
                }
            }
            let _ = app.emit("dup-progress", DupProgress { done, total });
        }
        let dup_keys: HashSet<(String, i64)> = counts.into_iter()
            .filter(|(_, n)| *n > 1)
            .map(|(key, _)| key)
            .collect();
        if dup_keys.is_empty() {
            return Ok(Vec::new());
        }

        /* 원본은 TEMP TABLE 조인으로 경로를 모으지만, 읽기 전용 연결에서는
           같은 결과를 얻도록 한 번 더 훑으며 중복 키만 담는다 (스캔 1회 추가). */
        let mut groups: HashMap<(String, i64), Vec<String>> = HashMap::new();
        {
            let mut stmt = conn.prepare(
                &format!("SELECT file_name, COALESCE(file_size,0), file_path FROM audio_files
                          WHERE {visible}
                          ORDER BY file_name, file_size, file_path")
            ).map_err(|e| e.to_string())?;
            let mut rows = stmt.query([]).map_err(|e| e.to_string())?;
            while let Some(row) = rows.next().map_err(|e| e.to_string())? {
                let key = (row.get::<_, String>(0).unwrap_or_default(),
                           row.get::<_, i64>(1).unwrap_or(0));
                if !dup_keys.contains(&key) { continue; }
                groups.entry(key).or_default().push(row.get::<_, String>(2).unwrap_or_default());
            }
        }
        let mut out: Vec<DupGroup> = groups.into_iter()
            .filter(|(_, paths)| paths.len() > 1)
            .map(|((file_name, file_size), paths)| DupGroup { file_name, file_size, paths })
            .collect();
        /* ⚠ 원본 정렬은 **사본 개수 내림차순**이 1차, 파일명 오름차순이 2차다
           (`key=lambda g: (-len(g["paths"]), g["file_name"])`, database.py:1483 —
           독스트링에도 "개수 내림차순, 파일명 오름차순" 으로 명시).
           예전 PoC 는 파일명만 기준으로 정렬해서, 상한(limit)에서 잘릴 때
           **검수 대상 그룹 집합 자체가 달라졌다** (원본은 사본이 많은 그룹부터
           보여준다). 그룹 안 경로 순서는 SQL 의 ORDER BY 가 이미 맞춰 둔다. */
        out.sort_by(|a, b| b.paths.len().cmp(&a.paths.len())
            .then_with(|| a.file_name.cmp(&b.file_name)));
        /* ⚠ 개수 상한을 두지 않는다. 원본 find_duplicates 에도 상한이 없다.
           예전 PoC 는 5,000 개로 잘랐는데, 실측(2026-09-07, 가시 행 1,582,033)
           중복 그룹이 **354,261 개**라 98.6% 가 화면에 아예 안 나왔다
           (사용자 보고: "왜 중복검수 항목이 5천개 리미트에 걸리는거야").
           목록은 화면에 보이는 부분만 그리므로(가상 스크롤) 개수가 많아도 된다. */
        Ok(out)
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
fn sf_drag_icon() -> Result<String, String> {
    let target = poc_dir()?.join("drag.png");
    if !target.exists() {
        std::fs::write(&target, include_bytes!("../icons/drag.png"))
            .map_err(|e| format!("드래그 아이콘을 만들 수 없습니다: {e}"))?;
    }
    Ok(target.to_string_lossy().to_string())
}

/* 드래그 중 커서에 붙는 그림을 **프런트가 그린 것으로** 바꾼다.

   startDrag 는 이미지를 **파일 경로**로만 받는다. 그래서 프런트가 캔버스에 그린
   PNG(base64)를 여기서 임시 파일로 쓰고 그 경로를 돌려준다.
     · 파일 드래그 → 결과표 행 모습 (여러 개면 겹친 카드 + "N개")
     · 영역 드래그 → 끌어다 준 구간의 파형 + 구간 길이
   (사용자 결정 2026-09-07. 이전에는 고정 아이콘 하나만 붙어 무엇을 끌고 있는지
    알 수 없었다.)

   ⚠ 파일명을 매번 새로 만들면 임시 폴더가 계속 늘어난다. 종류별로 고정 이름을
     써서 덮어쓴다 — 드래그는 한 번에 하나뿐이라 겹칠 일이 없다. */
#[tauri::command]
fn sf_drag_image(kind: String, png_base64: String) -> Result<String, String> {
    use base64::Engine as _;
    let name = match kind.as_str() {
        "region" => "drag_region.png",
        _ => "drag_row.png",
    };
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(png_base64.as_bytes())
        .map_err(|e| format!("드래그 그림 디코딩 실패: {e}"))?;
    if bytes.is_empty() {
        return Err("드래그 그림이 비어 있습니다".to_string());
    }
    let target = poc_dir()?.join(name);
    std::fs::write(&target, &bytes)
        .map_err(|e| format!("드래그 그림을 쓸 수 없습니다: {e}"))?;
    Ok(target.to_string_lossy().to_string())
}

/* 프런트가 첫 화면(스플래시)을 실제로 그린 뒤 호출한다 → 그때 창을 보여준다.
   JS 쪽 window.show() 는 @tauri-apps/api 로딩·권한에 의존해 조용히 실패할 수 있어
   (검은 첫 프레임이 남는다는 재보고) 표시 책임을 Rust 로 옮겼다. */
#[tauri::command]
async fn sf_ready(_window: tauri::Window) -> Result<(), String> {
    /* 메인 창의 첫 페인트 신호. **여기서 창을 띄우지 않는다** —
       스플래시(별도 작은 창)가 떠 있는 동안 큰 창이 함께 보이면 안 된다는
       사용자 지시 때문이다. 메인 창은 초기 데이터가 준비된 뒤 sf_loaded 에서 띄운다. */
    let _ = sf_debug("READY 메인 첫 페인트".to_string());
    Ok(())
}

/* 스플래시 창(splash.html)이 첫 프레임을 그렸다고 알리면 그때 보여준다.
   창을 먼저 띄우면 페인트 전 빈 창이 잠깐 보인다 (메인 창과 같은 문제).

   **버전 문자열을 돌려준다** — 스플래시가 자기 버전 표기를 이 값으로 채운다.
   ⚠ 스플래시에서 표준 API(getVersion)를 쓰지 않는 이유: 권한(capabilities)이
     `windows: ["main"]` 이라 메인 창에만 부여돼 있어 스플래시에서는 막힌다.
     우리가 만든 명령은 그 제한을 받지 않으므로 여기서 같이 넘긴다. */
#[tauri::command]
async fn sf_splash_ready(app: tauri::AppHandle) -> Result<String, String> {
    use tauri::Manager as _;
    if let Some(win) = app.get_webview_window("splash") {
        if !win.is_visible().unwrap_or(false) {
            let _ = sf_debug("SPLASH 표시".to_string());
            let _ = win.show();
        }
    }
    Ok(app.package_info().version.to_string())
}

/* 초기 데이터(설정·첫 결과·폴더 트리)가 준비되면 프런트가 호출한다.
   원본 splash.finish(win) 과 같은 자리다 — 메인 창을 띄우고 스플래시를 닫는다.
   ⚠ 순서 주의: 메인을 **먼저** 띄운 뒤 스플래시를 닫는다. 반대로 하면 잠깐
   창이 하나도 없는 순간이 생겨 앱이 종료로 판단될 수 있다. */
#[tauri::command]
async fn sf_loaded(app: tauri::AppHandle) -> Result<(), String> {
    use tauri::Manager as _;
    if let Some(win) = app.get_webview_window("main") {
        if !win.is_visible().unwrap_or(false) {
            win.show().map_err(|e| e.to_string())?;
        }
        let _ = win.set_focus();
    }
    if let Some(splash) = app.get_webview_window("splash") {
        let _ = splash.close();
    }
    let _ = sf_debug("LOADED 메인 표시 + 스플래시 닫기".to_string());
    Ok(())
}

#[tauri::command]
fn sf_debug(msg: String) -> Result<(), String> {
    use std::io::Write as _;
    /* 설치본에는 CARGO_MANIFEST_DIR(빌드 PC의 소스 경로)이 존재하지 않는다 —
       그 경로로 쓰면 로그가 사라지거나 엉뚱한 곳에 만들어진다.
       원본과 같은 데이터 폴더(%LOCALAPPDATA%\SoundField)에 남긴다. */
    let dir = local_appdata()?;
    if !dir.exists() {
        std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    }
    let path = dir.join("tauri_debug.log");
    let mut f = std::fs::OpenOptions::new().create(true).append(true).open(path)
        .map_err(|e| e.to_string())?;
    writeln!(f, "[pid {}] {msg}", std::process::id()).map_err(|e| e.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
/* ── WebView2 런타임 점검 ────────────────────────────────────────────────────
   화면은 Microsoft Edge **WebView2 런타임**이 그린다 (엣지 브라우저와는 별개 부품:
   실측으로 이 PC 에 Edge 149.0.4022.62 / WebView2 런타임 145.0.3800.82 가 서로 다른
   항목으로 설치돼 있다 — 브라우저를 지워도 런타임은 남는다).
   런타임이 없으면 창 생성이 실패하고, 예전에는 **아무 메시지도 없이 조용히 끝났다.**
   30명에게 배포하는 도구라 "실행이 안 되는데요" 만 남고 원인을 알 수 없다.
   그래서 시작 직후에 확인해 사유를 한글로 알린다.
   인스톨러가 설치 중에 자동 설치를 시도하지만(부트스트래퍼 1.7MB, 인터넷 필요)
   인터넷이 없으면 안내만 하고 설치는 계속된다 — 그 경우 여기서 걸린다.
   ⚠ 오프라인 설치본(246MB)은 일부러 번들하지 않았다 (인스톨러가 5배로 커진다). */
#[cfg(windows)]
fn ensure_webview2() -> bool {
    match tauri::webview_version() {
        Ok(version) => {
            let _ = sf_debug(format!("WEBVIEW2 {version}"));
            true
        }
        Err(err) => {
            let _ = sf_debug(format!("WEBVIEW2 없음 — 실행 중단 ({err})"));
            use windows::core::HSTRING;
            use windows::Win32::UI::WindowsAndMessaging::{
                MessageBoxW, MB_ICONERROR, MB_OK,
            };
            let body = HSTRING::from(concat!(
                "SoundField 를 실행할 수 없습니다.\n\n",
                "화면을 그리는 데 필요한 ",
                "Microsoft Edge WebView2 런타임이 설치되어 있지 않습니다.\n",
                "(엣지 브라우저와는 별개 부품입니다.)\n\n",
                "아래 중 하나로 해결할 수 있습니다.\n",
                "  · 인터넷이 되는 상태에서 SoundField 설치 프로그램을 다시 실행\n",
                "    (설치 중에 런타임을 자동으로 깔아 줍니다)\n",
                "  · \"Microsoft Edge WebView2 런타임\"을 직접 설치\n",
                "  · 그래도 안 되면 문의해 주세요"));
            let title = HSTRING::from("SoundField — 실행할 수 없습니다");
            unsafe { MessageBoxW(None, &body, &title, MB_OK | MB_ICONERROR); }
            false
        }
    }
}

/* ── 고아 사이드카 정리 (시작 직후) ──────────────────────────────────────────
   사이드카는 상주 파이썬 프로세스이고, **앱이 정상 종료될 때만** 정리된다
   (RunEvent::Exit 에서 quit 을 보낸다). 크래시나 작업 관리자로 강제 종료하면
   자식이 그대로 남는다. 특히 재생 엔진은 **오디오 출력 스트림을 계속 물고 있어서**,
   고아가 쌓이면 새로 켠 앱에서 소리가 안 난다 (실측: 고아 8개 누적 후 무음).
   개발 중 강제 종료를 반복하다 실제로 sf_audio 고아가 남는 것을 확인했다.

   **판정 규칙(보수적)**: 우리 사이드카 이름을 가진 프로세스 중,
     · 부모 PID 가 지금 목록에 없다 (부모가 죽었다)  → 고아
     · 부모가 있는데 그 이름이 soundfield.exe 가 아니다 (PID 재사용) → 고아
   부모가 살아 있는 soundfield.exe 면 **건드리지 않는다** — 다른 인스턴스의
   정상 사이드카를 죽이지 않기 위한 안전장치다. 우리 자신의 사이드카는 아직
   띄우지 않은 시점에 돌리므로 대상에 들어올 수 없다. */
#[cfg(windows)]
fn kill_orphan_sidecars() {
    use windows::Win32::Foundation::CloseHandle;
    use windows::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Process32FirstW, Process32NextW,
        PROCESSENTRY32W, TH32CS_SNAPPROCESS,
    };
    use windows::Win32::System::Threading::{OpenProcess, TerminateProcess, PROCESS_TERMINATE};

    const SIDECARS: [&str; 5] = [
        "sf_query.exe", "sf_admin.exe", "sf_audio.exe", "sf_waveform.exe", "sf_bridge.exe",
    ];

    unsafe {
        let Ok(snapshot) = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) else { return; };
        let mut entry = PROCESSENTRY32W::default();
        entry.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;
        let mut all: Vec<(u32, u32, String)> = Vec::new();   // (pid, 부모pid, 이름)
        if Process32FirstW(snapshot, &mut entry).is_ok() {
            loop {
                let end = entry.szExeFile.iter().position(|c| *c == 0)
                    .unwrap_or(entry.szExeFile.len());
                let name = String::from_utf16_lossy(&entry.szExeFile[..end]).to_lowercase();
                all.push((entry.th32ProcessID, entry.th32ParentProcessID, name));
                if Process32NextW(snapshot, &mut entry).is_err() { break; }
            }
        }
        let _ = CloseHandle(snapshot);

        let mut killed = 0usize;
        for (pid, parent, name) in &all {
            if !SIDECARS.contains(&name.as_str()) { continue; }
            let parent_alive_app = all.iter()
                .any(|(other, _, other_name)| other == parent && other_name == "soundfield.exe");
            if parent_alive_app { continue; }
            if let Ok(handle) = OpenProcess(PROCESS_TERMINATE, false, *pid) {
                if TerminateProcess(handle, 1).is_ok() { killed += 1; }
                let _ = CloseHandle(handle);
            }
        }
        if killed > 0 {
            let _ = sf_debug(format!("ORPHAN 고아 사이드카 {killed}개 정리"));
        }
    }
}

#[cfg(not(windows))]
fn kill_orphan_sidecars() {}

/* ── 창의 "큰 아이콘"을 직접 박는다 (윈도우 전용) ────────────────────────────
   Tauri 만 쓰면 창의 **큰 아이콘이 비어 있다.** 실측 2026-09-07 (설치본):
       WM_GETICON ICON_BIG   = 0   (없음)
       WM_GETICON ICON_SMALL = 있음
       창 클래스 아이콘      = 0   (없음)
   창을 크게 그리는 자리(Alt+Tab 등)는 큰 아이콘을 쓰므로 비워 둘 이유가 없다.
   여기서 두 크기를 채운다.

   ⚠ 정직한 기록: 이 함수는 **"작업표시줄에 옛 아이콘이 나온다"는 증상의 원인이
     아니었다.** 그 증상의 원인은 윈도우의 아이콘 캐시 파일이었고
     (set_app_user_model_id 주석 참고), 이 함수를 넣은 뒤에도 증상은 그대로였다.
     빈 값을 채운다는 점에서만 맞는 수정이다 — 아이콘 문제가 다시 생기면
     여기를 의심하지 말고 캐시부터 확인할 것.

   아이콘은 exe 안의 리소스에서 꺼낸다 (이름 32512 = 기본 앱 아이콘. tauri-winres
   가 이 이름으로 넣는다). 파일 경로를 읽지 않으므로 설치 위치와 무관하다. */
#[cfg(windows)]
fn force_taskbar_icon(window: &tauri::WebviewWindow) {
    use windows::core::PCWSTR;
    use windows::Win32::Foundation::{HWND, LPARAM, WPARAM};
    use windows::Win32::System::LibraryLoader::GetModuleHandleW;
    use windows::Win32::UI::WindowsAndMessaging::{
        GetSystemMetrics, LoadImageW, SendMessageW, HICON, ICON_BIG, ICON_SMALL,
        IMAGE_ICON, LR_DEFAULTCOLOR, SM_CXICON, SM_CXSMICON, SM_CYICON, SM_CYSMICON,
        WM_SETICON,
    };

    let Ok(handle) = window.hwnd() else { return; };
    let hwnd = HWND(handle.0 as *mut _);
    /* 32512 = IDI_APPLICATION 자리. 정수 리소스 이름은 포인터 자리에 값을 넣는다. */
    const APP_ICON_ID: u16 = 32512;
    unsafe {
        let Ok(module) = GetModuleHandleW(PCWSTR::null()) else { return; };
        let name = PCWSTR(APP_ICON_ID as usize as *const u16);
        for (which, cx, cy) in [
            (ICON_BIG, SM_CXICON, SM_CYICON),
            (ICON_SMALL, SM_CXSMICON, SM_CYSMICON),
        ] {
            let img = LoadImageW(
                Some(module.into()), name, IMAGE_ICON,
                GetSystemMetrics(cx), GetSystemMetrics(cy), LR_DEFAULTCOLOR,
            );
            let Ok(img) = img else { continue };
            if img.is_invalid() { continue; }
            SendMessageW(hwnd, WM_SETICON, Some(WPARAM(which as usize)),
                         Some(LPARAM(HICON(img.0).0 as isize)));
        }
    }
}

/* 작업표시줄이 이 앱을 가리키는 이름(앱 식별자, AppUserModelID).

   설치본마다 고유해야 한다 — 사용자 결정 2026-09-14 로
   다른 설치본과 한 PC 에 같이 설치할 수 있게 했는데, 식별자가 같으면 작업표시줄이
   두 앱을 한 칸으로 묶고 고정(핀)도 서로 덮어쓴다.
   빌드할 때 `SOUNDFIELD_AUMID` 환경변수로 넣는다 (tools/build_release.ps1).
   기본값은 예전부터 쓰던 값 그대로다 — 바꾸면 기존 설치본의 작업표시줄 고정이 끊긴다.
   ⚠ 설치 파일의 바로가기에도 **같은 이름**을 달아야 한다
     (soundfield_installer.iss 의 [Icons] AppUserModelID). */
#[cfg(windows)]
pub const APP_USER_MODEL_ID: &str =
    match option_env!("SOUNDFIELD_AUMID") {
        Some(v) => v,
        None => "YsgAudioTools.SoundField",
    };

/* ── 앱 식별자를 직접 지정한다 (작업표시줄이 이 앱을 부르는 이름) ─────────────
   지정하지 않으면 윈도우가 **exe 경로로 자동 생성**한다. 직접 지정하면
   설치 경로를 옮겨도 같은 앱으로 인식되고, 작업표시줄 고정도 어긋나지 않는다.
   설치 파일의 바로가기에도 **같은 이름**을 달아야 한다
   (soundfield_installer.iss 의 [Icons] AppUserModelID).
   **창이 만들어지기 전에** 불러야 한다 — 창이 생긴 뒤에는 판정이 끝나 있다.

   ── "작업표시줄에 옛 아이콘이 나온다" 의 실제 원인 (실측 2026-09-07) ─────────
   ⚠ 원인은 이 식별자가 아니었다. **윈도우의 아이콘 캐시 파일**이었다:
        %LOCALAPPDATA%\Microsoft\Windows\Explorer\iconcache_*.db
   옛 PyQt 버전이 지금과 **똑같은 경로**
   C:\Program Files (x86)\SoundField\SoundField.exe 에 레코드판 로고로 설치돼
   있었고, 그 그림이 위 캐시에 남아 새 exe 를 깔아도 작업표시줄만 계속 옛 로고를
   그렸다. 아래는 **전부 새 로고**였는데도 그랬다:
     · 창의 큰/작은 아이콘 (WM_GETICON)     · exe 안 아이콘 리소스 (그룹 1개뿐)
     · 설치 폴더 icon.ico (9개 이미지 전부)  · 시작 메뉴/바탕화면 바로가기
     · 셸이 그 경로들에 돌려주는 아이콘 (SHGetFileInfo / ExtractIconEx)
     · 작업표시줄 버튼이 생기는 창은 사이드카까지 합쳐 딱 1개, 그 창도 새 로고
   그리고 경로가 다른 것들(bat 실행본, 인스톨러 자신)은 정상이었다.

   듣지 않은 방법: 탐색기 재시작, ie4uinit.exe -show, 이 식별자 변경(새 이름이라
   캐시에 없을 텐데도 옛 로고가 그대로였다).
   해결한 방법: 탐색기를 멈추고 위 iconcache_*.db 를 지운 뒤 탐색기 재시작.

   => 앞으로 아이콘을 바꿨는데 작업표시줄만 옛것이면 **코드를 의심하기 전에
      이 캐시부터 지울 것.** 특히 옛 버전이 같은 경로에 있었던 PC 에서 그렇다. */
#[cfg(windows)]
fn set_app_user_model_id() {
    use windows::core::HSTRING;
    use windows::Win32::UI::Shell::SetCurrentProcessExplicitAppUserModelID;
    let id = HSTRING::from(APP_USER_MODEL_ID);
    unsafe {
        if let Err(err) = SetCurrentProcessExplicitAppUserModelID(&id) {
            let _ = sf_debug(format!("APPID 지정 실패 ({err})"));
        }
    }
}

pub fn run() {
    /* 창보다 먼저 — 위 주석의 함정 참고 */
    #[cfg(windows)]
    set_app_user_model_id();
    #[cfg(windows)]
    if !ensure_webview2() { return; }
    /* ⚠ 사이드카를 띄우기 **전에** 돌려야 한다. 창/웹뷰가 생기면 프런트가 바로
       검색을 요청해 사이드카가 뜨고, 그 뒤에 정리를 돌리면 판정이 복잡해진다. */
    kill_orphan_sidecars();
    tauri::Builder::default()
        /* ── 중복 실행 방지 (원본 main.py:229 QLocalServer 단일 인스턴스) ──
           두 번째 실행은 창을 앞으로 끌어올리고 바로 종료한다. 원본이 그렇게 동작하고,
           인덱스 DB/사이드카를 두 프로세스가 동시에 잡는 것도 막는다.
           ⚠ 반드시 첫 번째 플러그인으로 등록해야 한다 (플러그인 문서 규칙). */
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            use tauri::Manager;
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.unminimize();
                let _ = win.show();
                let _ = win.set_focus();
            }
        }))
        .manage(AudioState { child: Mutex::new(None), stdin: Mutex::new(None) })
        .manage(WaveState { inner: std::sync::Arc::new(WaveInner {
                child: Mutex::new(None),
                io: Mutex::new(None),
                seq: Mutex::new(0),
                busy: std::sync::atomic::AtomicBool::new(false),
            }) })
        .manage(SearchState {
            next_id: std::sync::atomic::AtomicU64::new(0),
            inner: std::sync::Arc::new(SearchInner {
                child: Mutex::new(None),
                stdin: Mutex::new(None),
                stdout: Mutex::new(None),
            }),
        })
        .manage(AdminState {
            busy: std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false)),
            current_pid: std::sync::Arc::new(std::sync::atomic::AtomicU32::new(0)),
            inner: std::sync::Arc::new(AdminInner {
                child: Mutex::new(None),
                stdin: Mutex::new(None),
                stdout: Mutex::new(None),
            }),
        })
        .plugin(tauri_plugin_dialog::init())
        /* DAW 로 파일 드래그 아웃 — 원본은 QMimeData.setUrls() 표준 방식으로
           Cubase/Nuendo/Pro Tools/Reaper 모두에 그대로 떨어진다.
           WebView 안에서는 OS 드래그를 시작할 수 없어 플러그인이 필요하다. */
        .plugin(tauri_plugin_drag::init())
        .plugin(tauri_plugin_fs::init())
        .invoke_handler(tauri::generate_handler![
            sf_ready,
            sf_splash_ready,
            sf_loaded,
            sf_audio,
            sf_folder_tree,
            sf_library_roots,
            sf_library_roots_basic,
            sf_blacklist_paths,
            sf_initial_rows,
            sf_indexed_count,
            sf_search_rows,
            sf_admin,
            sf_admin_cancel,
            sf_waveform,
            sf_waveform_cancel,
            sf_debug,
            sf_reveal,
            sf_open_url,
            sf_drag_paths,
            sf_fts_stale,
            sf_hidden,
            sf_history,
            sf_history_record,
            sf_history_clear,
            sf_row_by_path,
            sf_incomplete,
            sf_blacklist_add,
            sf_root_order_set,
            sf_blacklist_remove,
            sf_config_read,
            sf_file_stamp,
            sf_layout_store_read,
            sf_layout_store_write,
            sf_dup_cache_read,
            sf_dup_cache_write,
            sf_config_write,
            sf_drag_icon,
            sf_drag_image,
            sf_duplicates,
        ])
        /* 창 위치·크기·최대화 상태를 기억한다 (조사 Q14).
           ⚠ 이동 중에는 이벤트가 초당 수십 번 온다. 그때마다 파일을 쓰면 디스크를
             계속 두드리므로, 원본과 같이 잠깐 기다렸다가 한 번만 쓴다
             (원본 _save_config 는 1초 디바운스, main_window.py:4967). */
        .on_window_event(|win, event| {
            if win.label() != "main" {
                return;
            }
            let queue = match event {
                tauri::WindowEvent::Moved(_) | tauri::WindowEvent::Resized(_) => true,
                /* 닫을 때는 기다리지 않고 바로 쓴다 — 기다리면 프로세스가 먼저 끝난다 */
                tauri::WindowEvent::CloseRequested { .. } => {
                    save_win_state(win);
                    false
                }
                _ => false,
            };
            if !queue {
                return;
            }
            use std::sync::atomic::{AtomicU64, Ordering};
            static PENDING: AtomicU64 = AtomicU64::new(0);
            let ticket = PENDING.fetch_add(1, Ordering::SeqCst) + 1;
            let win = win.clone();
            std::thread::spawn(move || {
                std::thread::sleep(std::time::Duration::from_millis(700));
                /* 그동안 또 움직였으면 마지막 것만 저장한다 */
                if PENDING.load(Ordering::SeqCst) != ticket {
                    return;
                }
                save_win_state(&win);
            });
        })
        .setup(|app| {
            /* 패닉이 나면 사유를 로그에 남긴다. 예전에는 panic=abort + 로그 없음이라
               크래시 원인을 추적할 단서가 하나도 없었다 (사용자: "아무 말도 없이 꺼진다"). */
            {
                let previous = std::panic::take_hook();
                std::panic::set_hook(Box::new(move |info| {
                    let msg = info.payload().downcast_ref::<&str>().map(|s| s.to_string())
                        .or_else(|| info.payload().downcast_ref::<String>().cloned())
                        .unwrap_or_else(|| "알 수 없는 패닉".to_string());
                    let at = info.location()
                        .map(|l| format!("{}:{}", l.file(), l.line()))
                        .unwrap_or_default();
                    let _ = sf_debug(format!("PANIC {msg} @ {at}"));
                    previous(info);
                }));
            }
            let _ = sf_debug("BOOT 앱 setup 진입".into());

            /* 마지막에 쓰던 창 위치·크기·최대화 상태로 되돌린다 (조사 Q14).
               ⚠ 창이 아직 감춰져 있는 지금 해야 한다. 보여 준 뒤에 옮기면 기본
                 위치에서 저장된 위치로 창이 튀는 게 보인다. */
            if let Some(win) = app.get_webview_window("main") {
                restore_win_state(&win);
            }

            /* 작업표시줄 아이콘 — 위 force_taskbar_icon 주석의 함정 참고 */
            #[cfg(windows)]
            for label in ["main", "splash"] {
                if let Some(win) = app.get_webview_window(label) {
                    force_taskbar_icon(&win);
                }
            }

            /* 개발 빌드는 화면을 http://localhost:1420 (Vite) 에서 불러온다.
               개발 서버가 없으면 창 안에 브라우저의 "연결이 거부되었습니다" 페이지가
               뜨는데, 겉보기에는 **앱이 고장난 것처럼** 보인다 (사용자 실제 신고).
               그 상태로 두지 않고 무엇을 실행해야 하는지 알려주고 끝낸다.
               릴리스 빌드는 화면이 exe 안에 들어 있어 이 검사를 하지 않는다. */
            #[cfg(debug_assertions)]
            {
                use std::net::{Ipv4Addr, SocketAddr, TcpStream};
                let addr = SocketAddr::from((Ipv4Addr::LOCALHOST, 1420));
                let up = TcpStream::connect_timeout(&addr, std::time::Duration::from_millis(600))
                    .is_ok();
                if !up {
                    let _ = sf_debug("BOOT 개발 서버(1420) 없음 — 안내 후 종료".into());
                    use tauri_plugin_dialog::{DialogExt as _, MessageDialogKind};
                    app.dialog()
                        .message("개발 빌드는 개발 서버(localhost:1420)가 필요합니다.

바탕화면의 \"SoundField PoC\" 바로가기 또는 run_poc.bat 으로 실행하세요 (릴리스 빌드 — 개발 서버가 필요 없습니다).")
                        .title("SoundField PoC — 실행 방법")
                        .kind(MessageDialogKind::Warning)
                        .blocking_show();
                    std::process::exit(0);
                }
            }
            /* 검색 브리지를 **미리 띄운다.** 상주 프로세스라 한 번만 시작하면 되는데,
               첫 검색 때 시작하면 그 검색이 프로세스 시작 비용을 다 낸다
               (실측: 포장 브리지 첫 검색 1,997ms → 이후 10ms대).
               시작 직후 트리/카운트 스캔과 겹치지 않게 살짝 미룬다. */
            {
                use tauri::Manager as _;
                /* state 등록 순서에 의존하지 않게 try_state 로 확인한다 —
                   ⚠ 여기서 `return` 하면 setup 나머지(브리지 탐색·오디오 리더 스레드)가
                   전부 건너뛰어진다. 있을 때만 실행하는 형태로 둔다. */
                if let Some(state) = app.try_state::<SearchState>() {
                    let inner = state.inner.clone();
                    std::thread::spawn(move || {
                        std::thread::sleep(std::time::Duration::from_millis(120));
                        let t0 = std::time::Instant::now();
                        match ensure_search_service(&inner) {
                            Ok(()) => { let _ = sf_debug(
                                format!("SEARCH-WARM {}ms", t0.elapsed().as_millis())); }
                            Err(err) => { let _ = sf_debug(format!("SEARCH-WARM 실패 {err}")); }
                        }
                    });
                } else {
                    let _ = sf_debug("SEARCH-WARM 건너뜀 (state 없음)".into());
                }
                /* ── 파형 사이드카 예열 ────────────────────────────────────────
                   ⚠ 파형만 예열이 빠져 있었다. 그래서 앱을 켠 뒤 **첫 파형 요청이
                   사이드카 기동(수 초)을 기다리며 "파형 로딩 중..." 을 띄웠다**
                   (사용자 신고 2026-09-04: "껏다 킬 때마다 파형 로딩중이 뜬다").
                   캐시가 없어서가 아니었다 — 실측으로 디스크 캐시는 1.5ms 에 적중하고
                   사이드카가 떠 있으면 화면까지 왕복 8~10ms 다.
                   검색과 같은 방식으로 미리 띄운다. */
                if let Some(state) = app.try_state::<WaveState>() {
                    let inner = state.inner.clone();
                    std::thread::spawn(move || {
                        std::thread::sleep(std::time::Duration::from_millis(160));
                        let t0 = std::time::Instant::now();
                        match ensure_wave_service(&inner) {
                            Ok(()) => { let _ = sf_debug(
                                format!("WAVE-WARM {}ms", t0.elapsed().as_millis())); }
                            Err(err) => { let _ = sf_debug(format!("WAVE-WARM 실패 {err}")); }
                        }
                    });
                } else {
                    let _ = sf_debug("WAVE-WARM 건너뜀 (state 없음)".into());
                }
            }
            /* 창은 `visible: false` 로 만들어 두고, 프런트가 첫 페인트를 마친 뒤
               직접 show() 한다 (검은 첫 프레임 제거, main.tsx).
               ⚠ 안전망: 프런트가 아예 못 뜨는 상황에서 창이 영원히 숨겨지면
               앱이 "실행은 되는데 안 보이는" 상태가 된다 → 5초 뒤 강제로 보여준다. */
            {
                use tauri::Manager as _;
                /* 스플래시 안전망 — 신호가 못 와도 1.5초 뒤에는 보여준다 */
                if let Some(splash) = app.get_webview_window("splash") {
                    std::thread::spawn(move || {
                        std::thread::sleep(std::time::Duration::from_millis(1500));
                        if splash.is_visible().unwrap_or(true) { return; }
                        let _ = sf_debug("SPLASH 안전망 발동".into());
                        let _ = splash.show();
                    });
                }
                /* 메인 창 안전망 — 초기 로딩 신호(sf_loaded)가 못 와도 10초 뒤에는
                   메인을 띄우고 스플래시를 닫는다. 프런트 쪽 상한이 6초라 그보다
                   넉넉하게 둔다. 안 그러면 "실행은 되는데 안 보이는" 상태가 된다. */
                let handle = app.handle().clone();
                std::thread::spawn(move || {
                    std::thread::sleep(std::time::Duration::from_secs(10));
                    let Some(win) = handle.get_webview_window("main") else { return; };
                    if win.is_visible().unwrap_or(true) { return; }
                    let _ = sf_debug("SHOWWIN 안전망 발동 (초기 로딩 신호 없음)".into());
                    let _ = win.show();
                    if let Some(splash) = handle.get_webview_window("splash") {
                        let _ = splash.close();
                    }
                });
            }
            /* ── 메인 스레드 OLE 초기화 (드래그앤드롭 크래시의 근본 원인) ──────
               ⚠ 이 호출을 빼면 **DAW 로 드래그할 때 앱이 말없이 즉사한다.**
               tauri-plugin-drag 는 `run_on_main_thread` 로 드래그를 메인 스레드에서
               실행하고, 그 안에서 `SHCreateShellItemArrayFromIDLists` 를 호출한다.
               우리 창은 `dragDropEnabled: false` 라 wry 가 RegisterDragDrop/OLE
               초기화를 하지 않아, 메인 스레드가 COM 미초기화 상태로 남는다
               → 실측 hr=0x800401F0 (CO_E_NOTINITIALIZED) → drag 크레이트의
               `get_shell_item_array(...).unwrap()` 패닉(windows/mod.rs:370) → 패닉이
               COM 드래그 콜백을 거슬러 올라가 프로세스가 죽는다 (실측: PID 소멸).
               다른 작업이 우연히 COM 을 초기화해 두면 성공해서 **간헐적으로** 보였다
               (멀티채널 파일에서 잦았던 이유: 채널 판별·사이드카로 스레드 사용이 많다).
               DoDragDrop 은 CoInitialize 가 아니라 OleInitialize 를 요구한다. */
            #[cfg(windows)]
            {
                use windows::Win32::System::Ole::OleInitialize;
                use windows::Win32::System::Threading::GetCurrentThreadId;
                /* ⚠ setup 이 도는 스레드가 이벤트 루프 스레드와 같다고 **가정하지 않는다.**
                   플러그인은 `run_on_main_thread` 로 드래그를 실행하므로, 초기화도 반드시
                   그 경로로 넣어야 같은 스레드에 적용된다 (실측: setup 에서만 초기화했더니
                   드래그가 여전히 죽었다). 스레드 id 를 같이 남겨 확인할 수 있게 한다. */
                let setup_tid = unsafe { GetCurrentThreadId() };
                let _ = app.handle().run_on_main_thread(move || {
                    let loop_tid = unsafe { GetCurrentThreadId() };
                    let result = unsafe { OleInitialize(None) };
                    let _ = sf_debug(format!(
                        "OLE 초기화 setup_tid={setup_tid} loop_tid={loop_tid} 결과={}",
                        match result { Ok(()) => "새로 초기화".to_string(),
                                       Err(e) => format!("{e}") }));
                });
            }
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.set_zoom(1.0);
                let _ = win.set_shadow(false);
            }
            /* 원본 main.py 는 시작 직후 임시파일 정리를 돌린다 — UI 를 막지 않도록
               백그라운드에서 실행한다 (원본도 스레드로 돌린다). */
            std::thread::spawn(cleanup_temp_files);
            Ok(())
        })
        /* ── 종료 시 사이드카 정리 ────────────────────────────────────────────
           오디오/파형 사이드카는 상주 파이썬 프로세스다. 앱이 죽어도 자식이 남아
           **오디오 출력 스트림을 계속 물고 있다** (실측: 재시작을 반복하니 고아
           sf_audio_service 8개가 살아 있었다 → 새 인스턴스가 소리를 못 냄).
           원본은 같은 프로세스 안에서 재생하므로 이 문제가 없다. */
        .build(tauri::generate_context!())
        .expect("error while building SoundField")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                if let Some(state) = app.try_state::<AudioState>() {
                    if let Ok(mut stdin) = state.stdin.lock() {
                        if let Some(pipe) = stdin.as_mut() {
                            let _ = pipe.write_all(b"{\"command\":\"quit\"}
");
                            let _ = pipe.flush();
                        }
                        *stdin = None;
                    }
                    if let Ok(mut child) = state.child.lock() {
                        if let Some(mut c) = child.take() { let _ = c.kill(); }
                    }
                }
                if let Some(state) = app.try_state::<AdminState>() {
                    /* ⚠ 인덱싱 중에 그냥 kill 하면 Phase1 의 "등록(수시 커밋) →
                       삭제(마지막 별도 트랜잭션)" 사이에서 끊길 수 있다 — 원본 주석대로
                       옛 경로와 새 경로가 함께 남고 재스캔으로만 자가 회복한다.
                       먼저 협조적 취소를 보내 스스로 정리할 틈을 준다 (원본도 협조적 취소). */
                    if let Ok(mut g) = state.inner.stdin.lock() {
                        if let Some(pipe) = g.as_mut() {
                            let _ = pipe.write_all(b"{\"op\":\"cancel\"}\n");
                            let _ = pipe.flush();
                        }
                    }
                    std::thread::sleep(std::time::Duration::from_millis(300));
                    if let Ok(mut g) = state.inner.stdin.lock() { *g = None; }
                    if let Ok(mut g) = state.inner.stdout.lock() { *g = None; }
                    if let Ok(mut g) = state.inner.child.lock() {
                        if let Some(mut c) = g.take() { let _ = c.kill(); }
                    }
                }
                if let Some(state) = app.try_state::<SearchState>() {
                    /* stdin 을 먼저 놓아 파이프를 닫는다 — sf_query 는 EOF 에서 스스로 끝난다 */
                    if let Ok(mut g) = state.inner.stdin.lock() { *g = None; }
                    if let Ok(mut g) = state.inner.stdout.lock() { *g = None; }
                    if let Ok(mut g) = state.inner.child.lock() {
                        if let Some(mut c) = g.take() { let _ = c.kill(); }
                    }
                }
                if let Some(state) = app.try_state::<WaveState>() {
                    if let Ok(mut io) = state.inner.io.lock() { *io = None; }
                    if let Ok(mut child) = state.inner.child.lock() {
                        if let Some(mut c) = child.take() { let _ = c.kill(); }
                    }
                }
            }
        });
}
