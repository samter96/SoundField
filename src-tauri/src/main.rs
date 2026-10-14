#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

#[cfg(target_os = "windows")]
fn enable_per_monitor_v2_dpi() {
    #[link(name = "user32")]
    extern "system" {
        fn SetProcessDpiAwarenessContext(value: isize) -> i32;
    }
    const PER_MONITOR_AWARE_V2: isize = -4;
    unsafe {
        let _ = SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2);
    }
}

fn main() {
    /* 자동 업데이트의 교체 도우미로 불린 경우 — 창을 띄우지 않고 파일만 바꾼 뒤 끝난다
       (src/updater.rs). 앱의 다른 초기화(단일 실행 검사 등)보다 반드시 먼저. */
    let args: Vec<String> = std::env::args().collect();
    if soundfield_lib::updater::is_helper_invocation(&args) {
        std::process::exit(soundfield_lib::updater::helper_main(&args));
    }
    #[cfg(target_os = "windows")]
    enable_per_monitor_v2_dpi();
    soundfield_lib::run()
}
