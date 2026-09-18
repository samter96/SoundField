fn main() {
    /* ⚠ 아이콘을 바꿨는데 화면에 반영되지 않는 함정 — 실측 2026-09-07.
       **창·작업표시줄 아이콘은 icons/icon.ico 안의 "첫 번째 이미지" 하나만** 쓴다
       (tauri-codegen 2.6.3 image.rs new_ico: entries()[0] 를 RGBA 로 박는다).
       탐색기의 파일 아이콘은 크기에 맞는 이미지를 골라 쓰므로,
       첫 이미지만 작으면 "파일 아이콘은 새것인데 실행한 창만 옛것처럼 보임"이 된다.
       실제로 첫 이미지가 16x16(177B)이라 확대되어 뭉개져 보였고,
       ico 안 이미지 순서를 256 -> 16 내림차순으로 바꿔 해결했다.
       => icon.ico 를 교체할 때는 **큰 이미지가 맨 앞**인지 반드시 확인할 것.

       아래 감시 목록은 아이콘/설정을 바꿨을 때 이 빌드 스크립트가 반드시 다시
       돌게 하려는 것이다 (cargo 는 입력이 안 바뀌면 이전 결과를 재사용한다). */
    println!("cargo:rerun-if-changed=icons/icon.ico");
    println!("cargo:rerun-if-changed=tauri.conf.json");
    println!("cargo:rerun-if-changed=windows-app-manifest.xml");

    let mut win = tauri_build::WindowsAttributes::new();
    win = win.app_manifest(include_str!("windows-app-manifest.xml"));
    tauri_build::try_build(tauri_build::Attributes::new().windows_attributes(win))
        .expect("failed to run tauri build script");
}
