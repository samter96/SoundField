/* ── 앱 자동 업데이트 (2026-10-14 준비 — 사용자 결정) ─────────────────────────────
   켤 때 "올리는 곳"의 최신 파일 목록(latest.json)과 설치 폴더의 목록(update_manifest.json)을
   비교해 **지문(sha256)이 다른 파일만** 받는다. 버전 번호가 아니라 지문으로 비교하므로 어느
   버전에서든 한 번에 최신으로 맞춰진다.

   올리는 곳 = 설치 폴더의 update.cfg 한 줄 (공유 폴더 경로 또는 https 주소).
     비어 있거나 없으면 **아무것도 하지 않는다** — 그래서 코드를 넣어 두고 원하는 버전부터
     켤 수 있다. 시험용으로 환경변수 SOUNDFIELD_UPDATE_SOURCE 가 그 값을 대신한다.
   올리는 곳의 모양 (tools/update_tool.py publish 가 만든다):
     <올리는 곳>\latest.json      최신 버전의 파일 목록
     <올리는 곳>\<sha256>         파일 본체 (이름 = 지문 — 버전끼리 같은 파일은 한 번만 올라간다)
     GitHub 릴리스 자산은 폴더를 못 가지므로 두 경우 모두 평평하게 둔다.

   순서
     1) sf_update_check   — 목록 비교, 받을 파일 계획(plan.json)을 남긴다
     2) sf_update_download — 받을 파일을 %LOCALAPPDATA%\SoundField\update\stage 로 받고 지문 검사
     3) sf_update_apply   — 이 실행 파일의 사본(sf_updater.exe)을 교체 도우미로 띄우고 앱 종료
     4) 도우미(helper_main) — 앱이 끝나길 기다렸다가 파일을 바꾸고 앱을 다시 켠다
   ⚠ 실행 중인 파일은 윈도우가 잠가 바꿀 수 없다. 그래서 교체는 앱이 꺼진 뒤 도우미가 한다.
   ⚠ 교체는 "새 파일 → .sfnew, 옛 파일 → .sfold, .sfnew → 제자리" 순서다. 하나라도 실패하면
     바꾼 것을 전부 되돌리고 옛 버전 그대로 켠다 (반쯤 바뀐 설치본이 남지 않게).
   ⚠ 설치 폴더에 쓸 권한이 없으면(Program Files) 그때만 관리자 권한 창을 띄운다. 앱 자체는
     **권한 없이** 다시 켠다 — 관리자 권한으로 뜬 앱에서는 DAW 로 끌어 놓기가 막힌다. */

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use tauri::Emitter;

const LATEST: &str = "latest.json";
const LOCAL_MANIFEST: &str = "update_manifest.json";
const CONFIG: &str = "update.cfg";
const HELPER_ARG: &str = "--sf-apply-update";

#[derive(Serialize, Deserialize, Clone)]
pub struct FileEntry {
    pub path: String,
    pub size: u64,
    pub sha256: String,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct Manifest {
    #[serde(default)]
    pub format: u32,
    pub app: String,
    pub brand: String,
    pub version: String,
    pub build: String,
    pub files: Vec<FileEntry>,
    /* 버전별 업데이트 내역 (UPDATE_NOTES.md → publish 가 latest.json 에만 넣는다) */
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub notes: Vec<Note>,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub struct Note {
    pub version: String,
    pub text: String,
}

/* "2.2.0" → [2, 2, 0]. 숫자가 아닌 꼬리(-test, +빌드)는 버린다. 비교만 쓴다. */
fn ver_key(v: &str) -> Vec<u64> {
    v.split(['+', '-']).next().unwrap_or("").split('.')
        .map(|p| p.trim().parse::<u64>().unwrap_or(0)).collect()
}

/* 지금 버전보다 새롭고 받을 버전 이하인 내역을 최신부터. 하나도 없으면(같은 번호 재빌드·되돌림)
   받을 버전 자신의 내역을 보여 준다. */
fn notes_between(all: &[Note], current: &str, target: &str) -> Vec<Note> {
    let (cur, new) = (ver_key(current), ver_key(target));
    let mut out: Vec<Note> = all.iter()
        .filter(|n| { let k = ver_key(&n.version); k > cur && k <= new })
        .cloned().collect();
    if out.is_empty() {
        out = all.iter().filter(|n| ver_key(&n.version) == new).cloned().collect();
    }
    out.sort_by(|a, b| ver_key(&b.version).cmp(&ver_key(&a.version)));
    out
}

#[derive(Serialize, Deserialize, Clone)]
struct Plan {
    manifest: Manifest,
    needed: Vec<FileEntry>,
    removed: Vec<String>,
    #[serde(default)]
    ready: bool,
}

#[derive(Serialize, Clone)]
pub struct UpdateInfo {
    version: String,
    current: String,
    build: String,
    files: usize,
    bytes: u64,
    notes: Vec<Note>,
}

#[derive(Serialize, Clone)]
struct Progress {
    done: u64,
    total: u64,
}

#[derive(Serialize, Clone)]
struct Finished {
    ok: bool,
    error: String,
}

static CANCEL: AtomicBool = AtomicBool::new(false);
static BUSY: AtomicBool = AtomicBool::new(false);

/* ───────── 경로 ───────── */

fn install_dir() -> Result<PathBuf, String> {
    let exe = std::env::current_exe().map_err(|e| format!("실행 파일 위치를 알 수 없습니다: {e}"))?;
    exe.parent().map(|p| p.to_path_buf()).ok_or_else(|| "설치 폴더를 알 수 없습니다".into())
}

fn update_root() -> Result<PathBuf, String> {
    let local = std::env::var_os("LOCALAPPDATA").ok_or("LOCALAPPDATA 환경변수를 찾을 수 없습니다")?;
    let dir = PathBuf::from(local).join("SoundField").join("update");
    std::fs::create_dir_all(&dir).map_err(|e| format!("업데이트 폴더를 만들 수 없습니다: {e}"))?;
    Ok(dir)
}

fn stage_in(root: &Path) -> Result<PathBuf, String> {
    let dir = root.join("stage");
    std::fs::create_dir_all(&dir).map_err(|e| format!("받기 폴더를 만들 수 없습니다: {e}"))?;
    Ok(dir)
}

fn stage_dir() -> Result<PathBuf, String> {
    stage_in(&update_root()?)
}

/* 업데이트 기록 — %LOCALAPPDATA%\SoundField\update\update.log (도우미도 같은 곳에 쓴다) */
fn log_to(root: &Path, msg: &str) {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(root.join("update.log")) {
        let _ = writeln!(f, "[{secs}] [pid {}] {msg}", std::process::id());
    }
}

fn log(msg: &str) {
    if let Ok(root) = update_root() { log_to(&root, msg); }
}

/* 목록 안의 경로("bridge/_internal/x.dll") → 설치 폴더 안 실제 경로. 목록이 설치 폴더 밖을
   가리키지 못하게 막는다 (.. · 드라이브 문자 · 맨 앞 구분자). */
fn target_path(install: &Path, rel: &str) -> Result<PathBuf, String> {
    let mut out = install.to_path_buf();
    for part in rel.split(['/', '\\']) {
        if part.is_empty() || part == "." || part == ".." || part.contains(':') {
            return Err(format!("목록에 잘못된 경로가 있습니다: {rel}"));
        }
        out.push(part);
    }
    Ok(out)
}

/* ───────── 올리는 곳 ───────── */

/* update.cfg — 첫 번째 유효한 줄. `#` 은 주석, `source = …` 꼴도 받는다. */
fn source() -> Option<String> {
    if let Ok(v) = std::env::var("SOUNDFIELD_UPDATE_SOURCE") {
        let v = v.trim().to_string();
        if !v.is_empty() { return Some(v); }
    }
    if let Some(dev) = dev_source() { return Some(dev); }
    let text = std::fs::read_to_string(install_dir().ok()?.join(CONFIG)).ok()?;
    for line in text.lines() {
        let line = line.trim().trim_start_matches('\u{feff}').trim();
        if line.is_empty() || line.starts_with('#') { continue; }
        let value = match line.split_once('=') {
            Some((key, v)) if key.trim().eq_ignore_ascii_case("source") => v.trim(),
            _ => line,
        };
        let value = value.trim_matches('"').trim();
        if !value.is_empty() { return Some(value.to_string()); }
    }
    None
}

/* ── 개발자 배포 (2026-10-15 사용자 결정) ────────────────────────────────────────
   정식 배포 = 검증 끝난 빌드를 모든 사용자에게, 개발자 배포 = 개발자 PC 에서만 검증·QA.
   개발자 PC 에만 `%LOCALAPPDATA%\SoundField\update\dev.cfg` 를 둔다 (설치본에 들어가지 않는다).
   한 줄 = `<갈래 이름> = <개발자 배포 위치>` (폴더 경로 또는 https 주소). 갈래 이름은 update_manifest.json 의 brand.
   설치본 갈래와 같은 줄만 쓴다 — 한 PC 에 갈래가 여럿 깔려 있어도 각자 제 위치를 본다.
   이 파일을 지우면 다음에 켤 때 정식 최신으로 되돌아간다 (지문 비교라 번호가 낮아도 맞춘다). */
const DEV_CONFIG: &str = "dev.cfg";

fn local_brand() -> Option<String> {
    let local: Manifest = read_json(&install_dir().ok()?.join(LOCAL_MANIFEST)).ok()?;
    Some(local.brand)
}

fn dev_source() -> Option<String> {
    let path = std::env::var_os("LOCALAPPDATA")
        .map(|p| PathBuf::from(p).join("SoundField").join("update").join(DEV_CONFIG))?;
    let text = std::fs::read_to_string(path).ok()?;
    let brand = local_brand()?;
    for line in text.lines() {
        let line = line.trim().trim_start_matches('\u{feff}').trim();
        if line.is_empty() || line.starts_with('#') { continue; }
        if let Some((key, value)) = line.split_once('=') {
            let value = value.trim().trim_matches('"').trim();
            if key.trim().eq_ignore_ascii_case(&brand) && !value.is_empty() {
                return Some(value.to_string());
            }
        }
    }
    None
}

/* "dev"(개발자 배포를 받는 중) / "official" / "off"(올리는 곳 없음) — 헤더 DEV 배지와 업데이트 창 제목 */
#[tauri::command]
pub fn sf_update_channel() -> String {
    if std::env::var("SOUNDFIELD_UPDATE_SOURCE").map(|v| !v.trim().is_empty()).unwrap_or(false)
        || dev_source().is_some() {
        return "dev".into();
    }
    if source().is_some() { "official".into() } else { "off".into() }
}

fn is_url(src: &str) -> bool {
    let low = src.to_ascii_lowercase();
    low.starts_with("https://") || low.starts_with("http://")
}

/* 올리는 곳의 파일 하나를 dest 로 받는다. on_bytes 로 받은 만큼 알린다.
   - 폴더: 1MB 씩 복사 (진행 표시 + 취소 확인)
   - 주소: 윈도우 내장 curl.exe (윈도우 10 1803+) — 새 HTTP 라이브러리를 들이지 않으려고.
     진행은 받는 중인 파일 크기를 재서 알린다. */
fn fetch_to(src: &str, name: &str, dest: &Path, on_bytes: &mut dyn FnMut(u64)) -> Result<(), String> {
    let _ = std::fs::remove_file(dest);
    if is_url(src) {
        let url = format!("{}/{}", src.trim_end_matches('/'), name);
        let mut cmd = std::process::Command::new("curl.exe");
        // 30초 동안 1바이트도 안 오면 끊는다 — 시작 정비·인덱싱이 업데이트 확인을 기다리므로
        // 확인이 멈춰 있으면 안 된다 (2026-10-16).
        cmd.args(["-L", "-f", "-s", "-S", "--connect-timeout", "15",
                  "--speed-limit", "1", "--speed-time", "30", "-o"])
            .arg(dest).arg(&url)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::piped());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
        }
        let mut child = cmd.spawn().map_err(|e| format!("curl 을 실행할 수 없습니다: {e}"))?;
        let mut seen = 0u64;
        loop {
            if CANCEL.load(Ordering::SeqCst) {
                let _ = child.kill();
                let _ = child.wait();
                return Err("취소했습니다".into());
            }
            let now = std::fs::metadata(dest).map(|m| m.len()).unwrap_or(0);
            if now > seen { on_bytes(now - seen); seen = now; }
            match child.try_wait() {
                Ok(Some(status)) => {
                    let now = std::fs::metadata(dest).map(|m| m.len()).unwrap_or(0);
                    if now > seen { on_bytes(now - seen); }
                    if status.success() { return Ok(()); }
                    let mut err = String::new();
                    if let Some(mut e) = child.stderr.take() { let _ = e.read_to_string(&mut err); }
                    return Err(format!("내려받기 실패 ({name}): {}", err.trim()));
                }
                Ok(None) => std::thread::sleep(std::time::Duration::from_millis(120)),
                Err(e) => return Err(format!("내려받기 실패 ({name}): {e}")),
            }
        }
    }
    let from = Path::new(src).join(name);
    let mut input = std::fs::File::open(&from)
        .map_err(|e| format!("올리는 곳의 파일을 열 수 없습니다 ({}): {e}", from.display()))?;
    let mut output = std::fs::File::create(dest)
        .map_err(|e| format!("받을 파일을 만들 수 없습니다: {e}"))?;
    let mut buf = vec![0u8; 1 << 20];
    loop {
        if CANCEL.load(Ordering::SeqCst) { return Err("취소했습니다".into()); }
        let n = input.read(&mut buf).map_err(|e| format!("읽기 실패 ({name}): {e}"))?;
        if n == 0 { break; }
        output.write_all(&buf[..n]).map_err(|e| format!("쓰기 실패 ({name}): {e}"))?;
        on_bytes(n as u64);
    }
    output.flush().map_err(|e| format!("쓰기 실패 ({name}): {e}"))?;
    Ok(())
}

fn sha256_file(path: &Path) -> Result<String, String> {
    let mut f = std::fs::File::open(path).map_err(|e| format!("파일을 열 수 없습니다: {e}"))?;
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = f.read(&mut buf).map_err(|e| format!("파일을 읽을 수 없습니다: {e}"))?;
        if n == 0 { break; }
        hasher.update(&buf[..n]);
    }
    Ok(hasher.finalize().iter().map(|b| format!("{b:02x}")).collect())
}

fn read_json<T: for<'de> Deserialize<'de>>(path: &Path) -> Result<T, String> {
    let text = std::fs::read_to_string(path).map_err(|e| format!("{} 를 읽을 수 없습니다: {e}", path.display()))?;
    serde_json::from_str(text.trim_start_matches('\u{feff}'))
        .map_err(|e| format!("{} 형식이 맞지 않습니다: {e}", path.display()))
}

fn write_json<T: Serialize>(path: &Path, value: &T) -> Result<(), String> {
    let tmp = path.with_extension("tmp");
    let text = serde_json::to_string_pretty(value).map_err(|e| e.to_string())?;
    std::fs::write(&tmp, text).map_err(|e| format!("{} 를 쓸 수 없습니다: {e}", tmp.display()))?;
    std::fs::rename(&tmp, path).map_err(|e| format!("{} 를 쓸 수 없습니다: {e}", path.display()))
}

/* ───────── 1) 확인 ───────── */

#[tauri::command(async)]
pub fn sf_update_check() -> Result<Option<UpdateInfo>, String> {
    let Some(src) = source() else { return Ok(None) };   // 꺼져 있음 — 조용히
    check_with(&install_dir()?, &src, &update_root()?)
        .map_err(|e| { log(&format!("확인 실패: {e}")); e })
}

/* 올리는 곳이 정해져 있는지 — 타이틀바 업데이트(렌치) 버튼을 보일지 정한다 */
#[tauri::command]
pub fn sf_update_enabled() -> bool {
    source().is_some()
}

#[derive(Serialize, Clone)]
pub struct ManualCheck {
    current: String,
    /** 새 버전이 있으면 켤 때 뜨는 창과 같은 정보 */
    update: Option<UpdateInfo>,
    /** 최신이면 지금 버전까지의 내역 전체 (최신부터) */
    notes: Vec<Note>,
}

/* 타이틀바 렌치 버튼 (2026-10-15 사용자 요청) — 켜 둔 동안 언제든 같은 창을 띄운다.
   새 버전이 있으면 업데이트 창, 없으면 "최신 버전" 안내와 지금 버전까지의 내역 전체. */
#[tauri::command(async)]
pub fn sf_update_manual() -> Result<ManualCheck, String> {
    let src = source().ok_or("업데이트를 받는 곳이 정해져 있지 않습니다")?;
    let install = install_dir()?;
    let root = update_root()?;
    let local: Manifest = read_json(&install.join(LOCAL_MANIFEST))?;
    let update = check_with(&install, &src, &root)
        .map_err(|e| { log(&format!("확인 실패(버튼): {e}")); e })?;
    let mut notes = Vec::new();
    if update.is_none() {
        let remote = fetch_remote(&src, &root)?;
        if remote.app.eq_ignore_ascii_case(&local.app) && remote.brand.eq_ignore_ascii_case(&local.brand) {
            let cur = ver_key(&local.version);
            notes = remote.notes.into_iter().filter(|n| ver_key(&n.version) <= cur).collect();
            notes.sort_by(|a, b| ver_key(&b.version).cmp(&ver_key(&a.version)));
        }
    }
    Ok(ManualCheck { current: local.version, update, notes })
}

fn fetch_remote(src: &str, root: &Path) -> Result<Manifest, String> {
    let latest_tmp = root.join("latest.download");
    fetch_to(src, LATEST, &latest_tmp, &mut |_| {})?;
    let remote: Manifest = read_json(&latest_tmp)?;
    let _ = std::fs::remove_file(&latest_tmp);
    Ok(remote)
}

/* 경로를 인자로 받는 본체 — 시험(tests)이 임시 폴더로 돌릴 수 있게 */
fn check_with(install: &Path, src: &str, root: &Path) -> Result<Option<UpdateInfo>, String> {
    let install = install.to_path_buf();
    let log = |msg: &str| log_to(root, msg);
    let local: Manifest = match read_json(&install.join(LOCAL_MANIFEST)) {
        Ok(m) => m,
        Err(e) => { log(&format!("확인 건너뜀 — 설치 폴더 목록 없음: {e}")); return Ok(None); }
    };
    let remote = fetch_remote(src, root)?;

    /* 다른 갈래(brand)나 다른 앱의 목록이면 절대 받지 않는다 */
    if !remote.app.eq_ignore_ascii_case(&local.app) || !remote.brand.eq_ignore_ascii_case(&local.brand) {
        log(&format!("확인 — 다른 판의 목록이라 무시 ({}/{} ≠ {}/{})",
                     remote.app, remote.brand, local.app, local.brand));
        return Ok(None);
    }
    if remote.build == local.build {
        return Ok(None);
    }

    let local_sha: std::collections::HashMap<String, String> = local.files.iter()
        .map(|f| (f.path.to_lowercase(), f.sha256.clone())).collect();
    let remote_paths: std::collections::HashSet<String> = remote.files.iter()
        .map(|f| f.path.to_lowercase()).collect();
    let mut needed = Vec::new();
    for f in &remote.files {
        let target = target_path(&install, &f.path)?;
        let same_sha = local_sha.get(&f.path.to_lowercase()).map(|s| s == &f.sha256).unwrap_or(false);
        let same_size = std::fs::metadata(&target).map(|m| m.len() == f.size).unwrap_or(false);
        if !(same_sha && same_size) { needed.push(f.clone()); }
    }
    /* 없어진 파일 — **옛 목록에 있던 것만** 지운다. 목록에 없는 파일(제거 프로그램 등)은 손대지 않는다. */
    let removed: Vec<String> = local.files.iter()
        .filter(|f| !remote_paths.contains(&f.path.to_lowercase()))
        .map(|f| f.path.clone()).collect();
    if needed.is_empty() && removed.is_empty() {
        log(&format!("확인 — 빌드 표시만 다르고 파일은 같음 ({} → {})", local.build, remote.build));
        return Ok(None);
    }

    /* 받던 것이 같은 빌드면 이어서 받는다 (다 받은 파일은 다시 안 받는다) */
    let stage = stage_in(root)?;
    let plan_path = stage.join("plan.json");
    let old_build = read_json::<Plan>(&plan_path).ok().map(|p| p.manifest.build);
    if old_build.as_deref() != Some(remote.build.as_str()) {
        let _ = std::fs::remove_dir_all(&stage);
        std::fs::create_dir_all(&stage).map_err(|e| format!("받기 폴더를 만들 수 없습니다: {e}"))?;
    }
    let bytes: u64 = unique_blobs(&needed).iter()
        .filter(|f| std::fs::metadata(stage.join(&f.sha256)).map(|m| m.len() != f.size).unwrap_or(true))
        .map(|f| f.size).sum();
    let info = UpdateInfo {
        version: remote.version.clone(),
        current: local.version.clone(),
        build: remote.build.clone(),
        files: needed.len(),
        bytes,
        notes: notes_between(&remote.notes, &local.version, &remote.version),
    };
    log(&format!("확인 — 새 빌드 {} (지금 {}) 바뀐 파일 {}개, 지울 파일 {}개, 받을 크기 {}바이트",
                 remote.build, local.build, needed.len(), removed.len(), bytes));
    write_json(&plan_path, &Plan { manifest: remote, needed, removed, ready: false })?;
    Ok(Some(info))
}

fn unique_blobs(files: &[FileEntry]) -> Vec<FileEntry> {
    let mut seen = std::collections::HashSet::new();
    files.iter().filter(|f| seen.insert(f.sha256.clone())).cloned().collect()
}

/* ───────── 2) 받기 ───────── */

#[tauri::command]
pub fn sf_update_download(app: tauri::AppHandle) -> Result<(), String> {
    if BUSY.swap(true, Ordering::SeqCst) { return Err("이미 받는 중입니다".into()); }
    CANCEL.store(false, Ordering::SeqCst);
    std::thread::spawn(move || {
        let result = (|| -> Result<(), String> {
            let src = source().ok_or("올리는 곳이 정해져 있지 않습니다")?;
            let root = update_root()?;
            download_with(&src, &root, &mut |p| { let _ = app.emit("sf-update-progress", p); })
        })();
        if let Err(e) = &result { log(&format!("받기 실패: {e}")); }
        BUSY.store(false, Ordering::SeqCst);
        let _ = app.emit("sf-update-done", Finished {
            ok: result.is_ok(),
            error: result.err().unwrap_or_default(),
        });
    });
    Ok(())
}

fn download_with(src: &str, root: &Path, emit: &mut dyn FnMut(Progress)) -> Result<(), String> {
    let stage = stage_in(root)?;
    let plan_path = stage.join("plan.json");
    let mut plan: Plan = read_json(&plan_path)?;
    let blobs = unique_blobs(&plan.needed);
    let total: u64 = blobs.iter().map(|f| f.size).sum();
    let mut done = 0u64;
    let mut last_emit = std::time::Instant::now();
    let started = std::time::Instant::now();
    emit(Progress { done, total });
    for f in &blobs {
        let blob = stage.join(&f.sha256);
        /* 빈 파일은 받지 않고 여기서 만든다 — GitHub 릴리스는 크기 0 자산을 거부한다 (HTTP 400,
           2026-10-14 실제로 겪음). 브리지 안에 빈 파일(__init__ 표시 등)이 실제로 있다. */
        if f.size == 0 {
            std::fs::write(&blob, b"").map_err(|e| format!("받기 폴더에 쓸 수 없습니다: {e}"))?;
            continue;
        }
        if std::fs::metadata(&blob).map(|m| m.len() == f.size).unwrap_or(false)
            && sha256_file(&blob).map(|s| s == f.sha256).unwrap_or(false) {
            done += f.size;
            continue;
        }
        let part = stage.join(format!("{}.part", f.sha256));
        let base = done;
        let mut got = 0u64;
        fetch_to(src, &f.sha256, &part, &mut |n| {
            got += n;
            if last_emit.elapsed().as_millis() >= 100 {
                last_emit = std::time::Instant::now();
                emit(Progress { done: base + got.min(f.size), total });
            }
        })?;
        let sha = sha256_file(&part)?;
        if sha != f.sha256 {
            let _ = std::fs::remove_file(&part);
            return Err(format!("받은 파일이 손상됐습니다: {}", f.path));
        }
        std::fs::rename(&part, &blob).map_err(|e| format!("받은 파일을 옮길 수 없습니다: {e}"))?;
        done += f.size;
    }
    emit(Progress { done: total, total });
    plan.ready = true;
    write_json(&plan_path, &plan)?;
    log_to(root, &format!("받기 완료 — {}개 {}바이트 {}ms", blobs.len(), total, started.elapsed().as_millis()));
    Ok(())
}

#[tauri::command]
pub fn sf_update_cancel() {
    CANCEL.store(true, Ordering::SeqCst);
}

/* ───────── 3) 적용 시작 — 도우미를 띄우고 앱을 끈다 ───────── */

#[tauri::command]
pub fn sf_update_apply(app: tauri::AppHandle) -> Result<(), String> {
    let stage = stage_dir()?;
    let plan: Plan = read_json(&stage.join("plan.json"))?;
    if !plan.ready { return Err("아직 다 받지 않았습니다".into()); }
    let install = install_dir()?;
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    /* 도우미 = 이 실행 파일의 사본. 원본은 교체 대상이라 원본으로 띄우면 자기 자신을 못 바꾼다. */
    let helper = update_root()?.join("sf_updater.exe");
    std::fs::copy(&exe, &helper).map_err(|e| format!("교체 도우미를 준비할 수 없습니다: {e}"))?;
    std::process::Command::new(&helper)
        .arg(HELPER_ARG)
        .arg(&stage)
        .arg(&install)
        .arg(std::process::id().to_string())
        .arg(&exe)
        .spawn()
        .map_err(|e| format!("교체 도우미를 실행할 수 없습니다: {e}"))?;
    log(&format!("적용 시작 — {} 로 교체, 앱 종료", plan.manifest.build));
    /* 응답을 프런트에 돌려준 뒤 끈다 (Exit 이벤트가 파이썬 브리지들을 정리한다) */
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(250));
        app.exit(0);
    });
    Ok(())
}

/* ───────── 4) 교체 도우미 (main.rs 가 창을 띄우기 전에 부른다) ─────────
   인자: --sf-apply-update <받은 폴더> <설치 폴더> <앱 pid> <앱 실행 파일> [--elevated] */

pub fn is_helper_invocation(args: &[String]) -> bool {
    args.get(1).map(|a| a == HELPER_ARG).unwrap_or(false)
}

pub fn helper_main(args: &[String]) -> i32 {
    if args.len() < 6 { return 2; }
    let stage = PathBuf::from(&args[2]);
    let install = PathBuf::from(&args[3]);
    let pid: u32 = args[4].parse().unwrap_or(0);
    let app_exe = PathBuf::from(&args[5]);
    let elevated = args.iter().any(|a| a == "--elevated");
    let root = stage.parent().map(|p| p.to_path_buf()).unwrap_or_else(|| stage.clone());
    let say = |msg: &str| log_to(&root, &format!("도우미{} {msg}", if elevated { "(관리자)" } else { "" }));

    if !elevated {
        wait_for_exit(pid, 30_000);
        if !can_write(&install) {
            say("설치 폴더 쓰기 권한 없음 — 관리자 권한으로 다시 실행");
            let code = run_elevated(&args[1..]);
            say(&format!("관리자 도우미 종료 코드 {code}"));
            launch_app(&app_exe, &say);
            return code;
        }
    }
    let code = match apply(&stage, &install, &say) {
        Ok(build) => { say(&format!("교체 완료 — {build}")); 0 }
        Err(e) => { say(&format!("교체 실패 — 옛 버전 유지: {e}")); 1 }
    };
    if !elevated { launch_app(&app_exe, &say); }
    code
}

fn apply(stage: &Path, install: &Path, say: &dyn Fn(&str)) -> Result<String, String> {
    let plan: Plan = read_json(&stage.join("plan.json"))?;
    if !plan.ready { return Err("받기가 끝나지 않은 계획입니다".into()); }
    /* 1) 새 파일을 옆에 .sfnew 로 내려놓는다 — 여기까지는 설치본이 그대로다 */
    let mut staged: Vec<PathBuf> = Vec::new();
    let cleanup_new = |list: &[PathBuf]| for t in list { let _ = std::fs::remove_file(sibling(t, "sfnew")); };
    for f in &plan.needed {
        let target = target_path(install, &f.path)?;
        if let Some(parent) = target.parent() {
            std::fs::create_dir_all(parent).map_err(|e| format!("폴더를 만들 수 없습니다: {e}"))?;
        }
        if let Err(e) = std::fs::copy(stage.join(&f.sha256), sibling(&target, "sfnew")) {
            cleanup_new(&staged);
            let _ = std::fs::remove_file(sibling(&target, "sfnew"));
            return Err(format!("새 파일을 놓을 수 없습니다 ({}): {e}", f.path));
        }
        staged.push(target);
    }
    /* 2) 바꾸기 — 옛 파일은 .sfold 로 비켜 둔다. 앱·브리지가 아직 안 끝났으면 잠겨 있으니 기다린다. */
    let mut swapped: Vec<(PathBuf, bool)> = Vec::new();
    let mut fail: Option<String> = None;
    for target in &staged {
        let had_old = target.exists();
        if had_old {
            if let Err(e) = retry_rename(target, &sibling(target, "sfold"), 20_000) {
                fail = Some(format!("옛 파일을 비킬 수 없습니다 ({}): {e}", target.display()));
                break;
            }
        }
        if let Err(e) = retry_rename(&sibling(target, "sfnew"), target, 5_000) {
            if had_old { let _ = std::fs::rename(sibling(target, "sfold"), target); }
            fail = Some(format!("새 파일을 넣을 수 없습니다 ({}): {e}", target.display()));
            break;
        }
        swapped.push((target.clone(), had_old));
    }
    let mut removed: Vec<PathBuf> = Vec::new();
    if fail.is_none() {
        for rel in &plan.removed {
            let target = target_path(install, rel)?;
            if !target.exists() { continue; }
            match retry_rename(&target, &sibling(&target, "sfold"), 20_000) {
                Ok(()) => removed.push(target),
                Err(e) => { fail = Some(format!("옛 파일을 지울 수 없습니다 ({rel}): {e}")); break; }
            }
        }
    }
    if fail.is_none() {
        if let Err(e) = write_json(&install.join(LOCAL_MANIFEST), &plan.manifest) {
            fail = Some(e);
        }
    }
    if let Some(e) = fail {
        /* 되돌리기 — 바꾼 순서의 반대로 */
        for target in removed.iter().rev() { let _ = std::fs::rename(sibling(target, "sfold"), target); }
        for (target, had_old) in swapped.iter().rev() {
            let _ = std::fs::remove_file(target);
            if *had_old { let _ = std::fs::rename(sibling(target, "sfold"), target); }
        }
        cleanup_new(&staged);
        say(&format!("되돌림 — 바꾼 {}개 원래대로", swapped.len()));
        return Err(e);
    }
    /* 3) 정리 — 실패해도 업데이트는 끝난 것이다 (다음 업데이트 때 다시 덮어쓴다) */
    for (target, had_old) in &swapped { if *had_old { let _ = std::fs::remove_file(sibling(target, "sfold")); } }
    for target in &removed { let _ = std::fs::remove_file(sibling(target, "sfold")); }
    let _ = std::fs::remove_dir_all(stage);
    say(&format!("바꾼 파일 {}개, 지운 파일 {}개", swapped.len(), removed.len()));
    Ok(plan.manifest.build)
}

fn sibling(target: &Path, ext: &str) -> PathBuf {
    let mut name = target.file_name().map(|n| n.to_os_string()).unwrap_or_default();
    name.push(format!(".{ext}"));
    target.with_file_name(name)
}

fn retry_rename(from: &Path, to: &Path, timeout_ms: u64) -> Result<(), String> {
    let start = std::time::Instant::now();
    loop {
        match std::fs::rename(from, to) {
            Ok(()) => return Ok(()),
            Err(e) if start.elapsed().as_millis() as u64 >= timeout_ms => return Err(e.to_string()),
            Err(_) => std::thread::sleep(std::time::Duration::from_millis(250)),
        }
    }
}

fn can_write(dir: &Path) -> bool {
    let probe = dir.join(".sf_write_test");
    let ok = std::fs::write(&probe, b"x").is_ok();
    let _ = std::fs::remove_file(&probe);
    ok
}

fn launch_app(exe: &Path, say: &dyn Fn(&str)) {
    let mut cmd = std::process::Command::new(exe);
    if let Some(dir) = exe.parent() { cmd.current_dir(dir); }
    match cmd.spawn() {
        Ok(_) => say("앱 다시 실행"),
        Err(e) => say(&format!("앱을 다시 실행하지 못함: {e}")),
    }
}

#[cfg(windows)]
fn wait_for_exit(pid: u32, timeout_ms: u32) {
    use windows::Win32::Foundation::CloseHandle;
    use windows::Win32::System::Threading::{OpenProcess, WaitForSingleObject, PROCESS_SYNCHRONIZE};
    if pid == 0 { return; }
    unsafe {
        if let Ok(handle) = OpenProcess(PROCESS_SYNCHRONIZE, false, pid) {
            let _ = WaitForSingleObject(handle, timeout_ms);
            let _ = CloseHandle(handle);
        }
    }
}

#[cfg(not(windows))]
fn wait_for_exit(_pid: u32, _timeout_ms: u32) {}

/* 같은 도우미를 관리자 권한으로 띄우고 끝날 때까지 기다린다. 권한 창에서 [아니요] 면 2. */
#[cfg(windows)]
fn run_elevated(args: &[String]) -> i32 {
    use windows::core::PCWSTR;
    use windows::Win32::Foundation::CloseHandle;
    use windows::Win32::System::Threading::{GetExitCodeProcess, WaitForSingleObject, INFINITE};
    use windows::Win32::UI::Shell::{ShellExecuteExW, SEE_MASK_NOCLOSEPROCESS, SHELLEXECUTEINFOW};
    use windows::Win32::UI::WindowsAndMessaging::SW_HIDE;
    let Ok(exe) = std::env::current_exe() else { return 2 };
    let wide = |s: &str| s.encode_utf16().chain(std::iter::once(0)).collect::<Vec<u16>>();
    /* 경로 끝의 \ 는 닫는 따옴표를 먹으므로 떼고 넣는다 */
    let params: String = args.iter()
        .map(|a| format!("\"{}\"", a.trim_end_matches('\\')))
        .chain(std::iter::once("--elevated".to_string()))
        .collect::<Vec<_>>().join(" ");
    let verb = wide("runas");
    let file = wide(&exe.to_string_lossy());
    let params = wide(&params);
    let mut info = SHELLEXECUTEINFOW {
        cbSize: std::mem::size_of::<SHELLEXECUTEINFOW>() as u32,
        fMask: SEE_MASK_NOCLOSEPROCESS,
        lpVerb: PCWSTR(verb.as_ptr()),
        lpFile: PCWSTR(file.as_ptr()),
        lpParameters: PCWSTR(params.as_ptr()),
        nShow: SW_HIDE.0,
        ..Default::default()
    };
    unsafe {
        if ShellExecuteExW(&mut info).is_err() || info.hProcess.is_invalid() { return 2; }
        let _ = WaitForSingleObject(info.hProcess, INFINITE);
        let mut code = 1u32;
        let _ = GetExitCodeProcess(info.hProcess, &mut code);
        let _ = CloseHandle(info.hProcess);
        code as i32
    }
}

#[cfg(not(windows))]
fn run_elevated(_args: &[String]) -> i32 { 2 }

/* ───────── 시험 — 임시 폴더에 가짜 설치본·올리는 곳을 만들어 확인 → 받기 → 교체 → 되돌리기 ───────── */
#[cfg(test)]
mod tests {
    use super::*;

    fn sha(bytes: &[u8]) -> String {
        Sha256::digest(bytes).iter().map(|b| format!("{b:02x}")).collect()
    }

    fn manifest(build: &str, files: &[(&str, &[u8])]) -> Manifest {
        Manifest {
            format: 1, app: "SoundField".into(), brand: "brand-a".into(),
            version: build.split('+').next().unwrap().into(), build: build.into(),
            files: files.iter().map(|(p, b)| FileEntry { path: p.to_string(), size: b.len() as u64, sha256: sha(b) }).collect(),
            notes: Vec::new(),
        }
    }

    /* 설치본 A 를 만들고, B 를 올리는 곳에 올린다 */
    fn setup(name: &str) -> (PathBuf, PathBuf, PathBuf) {
        let base = std::env::temp_dir().join(format!("sf_update_test_{name}_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let (install, share, root) = (base.join("install"), base.join("share"), base.join("root"));
        for d in [&install, &share, &root] { std::fs::create_dir_all(d).unwrap(); }
        let a: Vec<(&str, &[u8])> = vec![("SoundField.exe", b"exe-A"), ("bridge/keep.dll", b"same"),
                                          ("bridge/old_only.dll", b"gone"), ("bridge/sub/mod.pyd", b"mod-A")];
        for (p, b) in &a {
            let t = target_path(&install, p).unwrap();
            std::fs::create_dir_all(t.parent().unwrap()).unwrap();
            std::fs::write(t, b).unwrap();
        }
        std::fs::write(install.join("unins000.exe"), b"not ours").unwrap();
        write_json(&install.join(LOCAL_MANIFEST), &manifest("2.2.0+A", &a)).unwrap();
        let b: Vec<(&str, &[u8])> = vec![("SoundField.exe", b"exe-B!"), ("bridge/keep.dll", b"same"),
                                          ("bridge/sub/mod.pyd", b"mod-B"), ("bridge/new/added.bin", b"new file"),
                                          ("bridge/new/empty.txt", b"")];
        /* 빈 파일은 올리는 곳에 없다 (GitHub 이 받지 않으므로 도구가 건너뛴다) */
        for (_, bytes) in &b { if !bytes.is_empty() { std::fs::write(share.join(sha(bytes)), bytes).unwrap(); } }
        write_json(&share.join(LATEST), &manifest("2.3.0+B", &b)).unwrap();
        (install, share, root)
    }

    #[test]
    fn check_download_apply() {
        let (install, share, root) = setup("ok");
        let src = share.to_string_lossy().to_string();
        let info = check_with(&install, &src, &root).unwrap().expect("새 버전이 있어야 한다");
        assert_eq!(info.files, 4);                       // exe·mod 바뀜 + added·empty 새로 (keep 은 그대로)
        assert_eq!(info.bytes, 6 + 5 + 8);
        let mut last = Progress { done: 0, total: 0 };
        download_with(&src, &root, &mut |p| last = p).unwrap();
        assert_eq!((last.done, last.total), (19, 19));
        let build = apply(&root.join("stage"), &install, &|_| {}).unwrap();
        assert_eq!(build, "2.3.0+B");
        assert_eq!(std::fs::read(install.join("SoundField.exe")).unwrap(), b"exe-B!");
        assert_eq!(std::fs::read(install.join("bridge/sub/mod.pyd")).unwrap(), b"mod-B");
        assert_eq!(std::fs::read(install.join("bridge/new/added.bin")).unwrap(), b"new file");
        assert_eq!(std::fs::read(install.join("bridge/new/empty.txt")).unwrap(), b"", "빈 파일은 받지 않고 만든다");
        assert!(!install.join("bridge/old_only.dll").exists(), "옛 목록에만 있던 파일은 지운다");
        assert!(install.join("unins000.exe").exists(), "목록에 없는 파일은 손대지 않는다");
        let left: Vec<_> = walk(&install).into_iter()
            .filter(|p| p.ends_with(".sfold") || p.ends_with(".sfnew")).collect();
        assert!(left.is_empty(), "찌꺼기가 남으면 안 된다: {left:?}");
        let now: Manifest = read_json(&install.join(LOCAL_MANIFEST)).unwrap();
        assert_eq!(now.build, "2.3.0+B");
        assert!(check_with(&install, &src, &root).unwrap().is_none(), "같은 빌드면 다시 묻지 않는다");
    }

    #[test]
    fn corrupt_blob_is_rejected() {
        let (install, share, root) = setup("corrupt");
        std::fs::write(share.join(sha(b"mod-B")), b"mod-X").unwrap();   // 올리는 곳 파일이 깨짐
        let src = share.to_string_lossy().to_string();
        check_with(&install, &src, &root).unwrap().unwrap();
        let err = download_with(&src, &root, &mut |_| {}).unwrap_err();
        assert!(err.contains("손상"), "{err}");
        assert!(apply(&root.join("stage"), &install, &|_| {}).is_err(), "다 받지 않은 계획은 적용하지 않는다");
        assert_eq!(std::fs::read(install.join("SoundField.exe")).unwrap(), b"exe-A");
    }

    #[test]
    fn locked_file_rolls_back() {
        let (install, share, root) = setup("locked");
        let src = share.to_string_lossy().to_string();
        check_with(&install, &src, &root).unwrap().unwrap();
        download_with(&src, &root, &mut |_| {}).unwrap();
        /* 바꿔야 할 파일 하나를 지울 수 없게 잡아 둔다 (실행 중인 브리지 흉내) */
        let hold = {
            use std::os::windows::fs::OpenOptionsExt;
            std::fs::OpenOptions::new().read(true).share_mode(0).open(install.join("bridge/sub/mod.pyd")).unwrap()
        };
        let err = apply(&root.join("stage"), &install, &|_| {}).unwrap_err();
        drop(hold);
        assert!(err.contains("mod.pyd"), "{err}");
        assert_eq!(std::fs::read(install.join("SoundField.exe")).unwrap(), b"exe-A", "되돌려야 한다");
        assert_eq!(std::fs::read(install.join("bridge/sub/mod.pyd")).unwrap(), b"mod-A");
        assert!(install.join("bridge/old_only.dll").exists());
        assert!(!install.join("bridge/new/added.bin").exists());
        let now: Manifest = read_json(&install.join(LOCAL_MANIFEST)).unwrap();
        assert_eq!(now.build, "2.2.0+A");
    }

    #[test]
    fn other_brand_is_ignored() {
        let (install, share, root) = setup("brand");
        let mut m: Manifest = read_json(&share.join(LATEST)).unwrap();
        m.brand = "brand-b".into();
        write_json(&share.join(LATEST), &m).unwrap();
        assert!(check_with(&install, &share.to_string_lossy(), &root).unwrap().is_none());
    }

    #[test]
    fn notes_newer_than_current() {
        let n = |v: &str| Note { version: v.into(), text: format!("v{v}") };
        let all = vec![n("2.1.4"), n("2.2.0"), n("2.1.10"), n("2.1.7"), n("2.2.1")];
        let pick = |cur: &str, to: &str| notes_between(&all, cur, to).into_iter().map(|x| x.version).collect::<Vec<_>>();
        assert_eq!(pick("2.1.7", "2.2.1"), ["2.2.1", "2.2.0", "2.1.10"]);   // 2.1.10 > 2.1.7 (숫자 비교)
        assert_eq!(pick("2.1.3", "2.2.0"), ["2.2.0", "2.1.10", "2.1.7", "2.1.4"]);
        assert_eq!(pick("2.2.1", "2.2.0"), ["2.2.0"], "되돌림이면 받을 버전 자신의 내역");
        assert_eq!(pick("2.2.0", "2.2.0"), ["2.2.0"], "같은 번호 재빌드도 자신의 내역");
    }

    #[test]
    fn path_escape_is_refused() {
        let base = Path::new("C:/x");
        assert!(target_path(base, "../evil.dll").is_err());
        assert!(target_path(base, "C:/evil.dll").is_err());
        assert!(target_path(base, "/evil.dll").is_err());
        assert!(target_path(base, "bridge/ok.dll").is_ok());
    }

    fn walk(dir: &Path) -> Vec<String> {
        let mut out = Vec::new();
        for e in std::fs::read_dir(dir).unwrap().flatten() {
            let p = e.path();
            if p.is_dir() { out.extend(walk(&p)); } else { out.push(p.to_string_lossy().to_string()); }
        }
        out
    }
}
