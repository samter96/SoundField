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
    #[cfg(target_os = "windows")]
    enable_per_monitor_v2_dpi();
    soundfield_lib::run()
}
