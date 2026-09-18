# SoundField

사운드 라이브러리를 빠르게 찾고 바로 들어보는 윈도우용 도구입니다.
수십만 개 규모의 라이브러리를 색인해 두고, 파일명·메타데이터·UCS 카테고리로 좁혀가며
파형을 보면서 바로 재생하고, 원하는 구간만 잘라 DAW 로 끌어다 놓을 수 있습니다.

**내려받기 → [Releases](https://github.com/samter96/SoundField/releases/latest)** (Windows x64)

색인·설정·재생 히스토리는 설치 폴더 밖(`%LOCALAPPDATA%\SoundField`)에 있어서
새 버전을 설치해도 그대로 남습니다.

소개 페이지: <https://samter96.github.io/YSGAudioTools/soundfield/>

## 무엇으로 만들었나

| 층 | 쓰는 것 |
|---|---|
| 화면 | Tauri 2 + React 19 + TypeScript |
| 창·프로세스·파일 | Rust |
| 검색·색인·재생·파형·바이노럴 | Python (`py/app`, PyInstaller 로 묶어 동봉) |
| 색인 저장소 | SQLite + FTS5 |

설치본은 독립 실행입니다 — 파이썬을 따로 깔 필요가 없습니다.
화면을 그리는 데 Microsoft Edge WebView2 런타임이 필요하고, 없으면 설치 중에 함께 깝니다.

## 직접 빌드하기

```bash
npm install
.\build_bridge.ps1     # 파이썬 브리지 (한 번만, 또는 py/ 를 고쳤을 때)
npm run installer      # 프런트 → 실행 파일 → 설치본
```

필요한 것: Node 20+, Rust (MSVC), Python 3.12+ (PyInstaller·numpy·soundfile·
sounddevice·mutagen·PyQt6), Inno Setup 6.

⚠ 바이노럴 모니터 실행 파일(`py/sidecar/`)은 배포 허락이 확인되지 않아 이 저장소에
포함하지 않았습니다. 없으면 `build_bridge.ps1` 이 멈추고 어떤 파일이 필요한지 알려줍니다.
바이노럴 기능을 빼고 쓰려면 그 단계만 건너뛰도록 고치면 됩니다.

## 라이선스

글꼴은 각자의 라이선스를 따릅니다 — Pretendard·Aldrich 모두 SIL Open Font License 1.1
(`public/fonts/LICENSE-NOTICE.txt`). 그 밖의 코드에 대한 라이선스는 아직 정하지 않았습니다.

---

만든 사람: You Seung Gyun (YSG Audio Labs) · <samter96@naver.com>
