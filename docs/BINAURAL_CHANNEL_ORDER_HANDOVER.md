# 바이노럴 채널 순서 교정 핸드오버

> 대상: `SoundField-Tauri-PoC`  
> 기준 구현: `C:\Users\samter96\Desktop\Sound_Search_program` 원본 PyQt 앱  
> 작성일: 2026-09-01  
> 상태: 원본 구현 및 단위 테스트 완료, Tauri 적용 전

## 1. 한 줄 요약

멀티채널 WAV의 **채널 수와 채널 순서는 서로 다른 정보**다. 5.1 파일도 실제 샘플 순서가
`L R C LFE Ls Rs`일 수도 있고 `L C R Ls Rs LFE`일 수도 있다. 순서를 잘못 해석하면
센터·후방·LFE가 엉뚱한 방향으로 가거나 후방음이 LFE로 오인되어 사라진다.

원본 앱은 이제 다음 순서로 안전하게 판정한다.

1. 사용자가 저장한 파일 설정
2. 사용자가 명시적으로 저장한 **같은 폴더·같은 채널 수** 설정
3. WAV 내부 iXML `TRACK_LIST`
4. WAV `dwChannelMask`
5. 파일명의 명시적 포맷 표기
6. 오탐 여지가 작은 채널 수 기본값
7. 끝까지 모호하면 일반 스테레오로 재생하고 선택 안내

일반 사용자에게 `WAVE order`, `SMPTE/film order` 같은 기술 용어를 고르게 하지 않는다.
필요할 때만 **채널 배치 비교해서 듣기**를 열어 `현재 방식`과 `다른 방식`을 처음부터
번갈아 재생하고, 자연스러운 쪽을 저장한다.

## 2. 원본의 기준 파일

Tauri에서 판별 로직을 다시 만들지 말고 아래 구현을 기준으로 옮긴다.

| 원본 파일 | 기준 구현 |
|---|---|
| `app/binaural.py` | `BinauralLayout`, `LayoutOverrideStore`, `read_wave_layout_metadata`, `resolve_layout`, `build_load_payload` |
| `app/ui/playback.py` | `HybridPlayer.reloadBinauralLayout`, `binauralLayoutResolved` |
| `app/ui/player_widget.py` | `_ChannelOrderDialog`, `_show_channel_order_comparison`, `_apply_layout_to_current_folder` |
| `tests/test_binaural.py` | iXML 우선순위, 실제 인터리브 재배열, 저장 스키마, 비교 재생 UI 테스트 |

현재 PoC의 `src-tauri/python/sf_waveform_service.py`와 `sf_audio_service.py`는
`SOUNDFIELD_PY_ROOT`를 통해 원본 Python 모듈을 가져온다. PoC 검증 단계에서는 이 구조를
활용해도 되지만, 독립 배포 단계에서는 위 로직을 PoC 소유 모듈로 복사하고 동일 테스트를
함께 가져와야 한다.

## 3. 절대 바꾸면 안 되는 판정 규칙

### 3.1 iXML

- `<TRACK_LIST>` 안의 `<TRACK>`만 읽는다.
- 채널 역할명은 `FUNCTION`을 우선하고, 없을 때만 `NAME`을 쓴다.
- 실제 인터리브 위치는 `INTERLEAVE_INDEX`를 쓴다.
- `L (1)`, `R (3)`처럼 이름 끝 괄호 숫자가 있으면 보조 근거로 쓴다.
- **`CHANNEL_INDEX`는 인터리브 위치로 쓰지 않는다.** 프로젝트나 DAW가 부여한 논리 번호일
  수 있어 실제 파일 샘플 순서와 다를 수 있다.
- 채널 이름/인덱스가 일부만 있거나 중복되면 iXML 판정을 버리고 다음 근거로 내려간다.
- 유효한 iXML과 `dwChannelMask`가 충돌하면 iXML이 이긴다.

### 3.2 사용자 설정

- 파일 설정이 폴더 설정보다 우선한다.
- 폴더 설정은 사용자가 직접 `현재 폴더의 N채널 파일에 적용`을 선택했을 때만 만든다.
- 하위 폴더로 전파하지 않는다.
- 다른 채널 수에 전파하지 않는다.
- 주변 파일을 자동 검사해서 폴더 규칙을 추정하지 않는다.

### 3.3 채널 수·파일명 특례

| 조건 | 처리 |
|---|---|
| 3ch | LCR 기본값 |
| 정보 없는 4ch | Quad 기본값, 위험 팝업 없음 |
| 4ch + `AmbiX` | 1차 AmbiX |
| 4ch + `FuMa` | 1차 FuMa |
| 4ch + 일반 `Ambisonic/B-format` | AmbiX/FuMa 비교 선택 |
| 4ch + `AMBEO`만 존재 | A-format 원본 가능성 때문에 자동 바이노럴 금지 |
| 6ch | 5.1 기본값 |
| `0x13F` 7ch 마스크 | 6.1 지원 |
| 7ch | 7.0 기본값 |
| 8ch | 7.1 기본값 |
| 9ch + `9Ch` 파일명 | 7.0.2 |
| 정보 없는 9ch | 7.0.2 / 2차 AmbiX 판정 보류 |
| 16/25/36ch | 채널 수만으로 AmbiX 확정 금지; Ambisonic 파일명 근거 필요 |

FuMa는 디코더에 그대로 던지는 것이 아니다. 원본 `build_load_payload` 경로에서 FuMa를
AmbiX가 기대하는 채널 순서와 W 채널 스케일로 변환한 뒤 디코딩한다. 이 변환을 프런트엔드에서
별도로 흉내 내지 않는다.

## 4. 데이터 계약

### 4.1 Python 판정 결과

현재 PoC의 `LayoutInfo`는 실제 재생 상태를 표현하기에 부족하다. 최소한 다음 필드를 반환한다.

```ts
export type LayoutInfo = {
  channels: number;
  channel_mask: number;
  automatic_preset: string;
  automatic_topology: "surround" | "ambisonic" | "unknown";
  automatic_channel_order: "wave" | "film" | "";
  automatic_source: "manual" | "ixml" | "mask" | "filename" | "channels" | "";
  can_auto_play: boolean;
  candidates: string[];
  reason: string;
  applied_preset: string;
  applied_topology: "surround" | "ambisonic" | "unknown";
  applied_channel_order: "wave" | "film" | "";
  applied_source: string;
  applied_can_play: boolean;
};
```

`sf_waveform_service.py::_layout()`에서 `resolve_layout()`의 결과를 그대로 위 필드로 직렬화한다.
UI용 조회는 별도 Python 프로세스에서 실행되므로 WebView를 직접 막지는 않지만, 메뉴를 열 때마다
두 번 스캔하지 않도록 `read_wave_layout_metadata()` 결과 캐시를 유지한다.

### 4.2 저장 토큰

내부 토큰은 다음과 같다. 이 문자열을 사용자 화면에 그대로 표시하지 않는다.

```text
5.1|wave
5.1|film
7.1|wave
7.1|film
ambix
fuma
quad
```

저장 JSON 스키마는 원본 `LayoutOverrideStore` 버전 2와 맞춘다.

```json
{
  "version": 2,
  "files": {
    "정규화된 파일 경로": { "preset": "5.1", "order": "film" }
  },
  "folders": {
    "정규화된 정확한 폴더 경로": {
      "6": { "preset": "5.1", "order": "film" }
    }
  }
}
```

PoC 검증 중에는 원본 설정을 오염시키지 않도록
`%LOCALAPPDATA%\SoundField\poc\binaural_layouts.json`을 사용한다. 실제 제품 전환 시에만
`%LOCALAPPDATA%\SoundField\binaural_layouts.json`으로 합친다.

## 5. Tauri 파일별 적용 지침

### 5.1 `src-tauri/python/sf_waveform_service.py`

현재 `_layout()`은 마스크와 프리셋만 반환한다. 다음을 반영한다.

1. `read_wave_channel_mask()`를 따로 호출한 뒤 `resolve_layout()`이 다시 파일을 읽는 중복을 제거한다.
2. 자동/적용 결과의 `topology`, `channel_order`, `source`, `reason`, `candidates`를 모두 반환한다.
3. 판정 실패를 빈 프리셋으로 숨기지 말고 `can_auto_play=false`로 명확히 반환한다.
4. 네트워크 WAV 접근은 이 Python 서비스 안에서만 일어나게 한다.

### 5.2 `src/backend.ts`

- 위 `LayoutInfo` 계약으로 확장한다.
- `AudioCommand`에 아래 명령을 추가한다.

```ts
type AudioCommandName =
  | "load" | "play" | "restart" | "pause" | "stop"
  | "seek" | "rate" | "volume" | "binaural"
  | "binaural_layout" | "quit";

// 비교 재생 또는 저장 직후
{ command: "binaural_layout", override: "5.1|film", restart: true, play: true }
```

- `audioBridge.binauralLayout(override, restart, play)` 래퍼를 추가한다.
- `load`의 `meta.binaural_layout`에도 현재 파일/폴더 설정 토큰을 항상 포함한다.

### 5.3 `src-tauri/python/sf_audio_service.py`

`binaural_layout` 명령을 받으면 다음 순서를 **한 동작으로** 수행한다.

1. 현재 파일 메타의 `binaural_layout` 갱신
2. `HybridPlayer.setMediaMetadata(meta)`
3. `HybridPlayer.reloadBinauralLayout(0, True)`

이 순서를 쪼개서 `stop → load → play` 명령 세 개로 보내면 로드 경쟁 때문에 무음이 생길 수 있다.
원본의 `reloadBinauralLayout`을 그대로 사용한다.

또한 현재 Rust는 오디오 서비스의 stdout을 `Stdio::null()`로 버리고 있다. 실제 판정 결과와
오류를 UI에 보여 주려면 아래 신호를 JSON line으로 내보내고 Rust에서 Tauri 이벤트로 중계해야 한다.

```json
{"type":"binaural_layout_resolved","layout":{...}}
{"type":"binaural_status","status":"바이노럴 재생 중"}
{"type":"binaural_availability","available":false,"reason":"..."}
{"type":"error","message":"..."}
```

연결 대상은 `HybridPlayer.binauralLayoutResolved`, `binauralStatusChanged`,
`binauralAvailabilityChanged`다.

#### 모든 재생 제어의 틱 방지

2026-09-02 원본 사이드카의 `AudioEngine`을 본체 `app/audio_engine.py`의 재생 정책과
동일하게 맞췄다. 바이노럴 경로만 별도 즉시 전환을 사용하면 seek뿐 아니라 pause,
stop, volume, 파일 교체에서도 파형이 수직으로 잘려 틱이 생긴다. Tauri에서도
프런트엔드 지연 처리로 우회하지 말고 수정된 사이드카를 그대로 사용한다.

```text
현재 위치 5ms fade-out
→ decoder seek + ring prebuffer 동안 silence
→ 새 위치 18ms fade-in
```

- 연속 seek는 페이드아웃을 반복해서 처음부터 시작하지 않고 최신 목표 위치만 교체한다.
- 페이드인 도중 다시 seek하면 현재 gain에서 페이드아웃으로 부드럽게 방향을 바꾼다.
- 정지 중 seek는 즉시 위치를 적용하되 다음 재생은 0 gain에서 올라온다.
- 재생/일시정지/정지는 18ms transport fade를 사용한다. stop은 0에 도달한 뒤 상태를 해제한다.
- 볼륨 변경은 18ms ramp를 사용해 슬라이더 이동 시 zipper noise를 막는다.
- 파일/레이아웃 교체는 55ms fade-out 후 교체한다. 파일 시작점의 원래 어택은 보존한다.
- prebuffer/seek 대기처럼 실제 출력이 이미 무음이면 pause/stop ACK를 즉시 완료한다.
- 소스: 원본 `sidecar/src/AudioEngine.cpp`, `AudioEngine.h`
- 회귀: `Tests.cpp`의 seek/playback generation/55ms swap/audio callback allocation 테스트
- 반영 실행 파일: 원본 `sidecar/prebuilt/scsearch-monitor-fixed.exe`

### 5.4 `src-tauri/src/lib.rs`

- `AudioCommand`에 `override`, `restart`, `play` 필드를 추가한다.
- `ensure_audio_service()`의 stdout을 `piped()`로 바꾸고 전용 읽기 스레드에서 JSON line을 읽는다.
- 읽은 이벤트를 `app.emit("sf-audio-event", payload)`로 WebView에 전달한다.
- stderr는 개발 빌드에서 로그 파일 또는 콘솔로 보존한다. 지금처럼 둘 다 버리면 무음 원인을
  확인할 수 없다.
- 서비스 재시작 시 stdout 읽기 스레드와 child 세대가 섞이지 않도록 세대 번호를 둔다.

### 5.5 `src/components/BinauralMenu.tsx`

- 스피커 프리셋에 `6.1`을 추가한다.
- 일반 메뉴에는 `wave`, `film`, `SMPTE` 텍스트를 추가하지 않는다.
- 현재 자동 판정 결과와 `개별 저장됨`/`폴더 설정`을 표시한다.
- 스피커 배치가 선택된 경우 `채널 배치 비교해서 듣기` 항목을 추가한다.
- 비교 창은 `현재 방식으로 들어보기`, `다른 방식으로 들어보기`만 보여 준다.
- 두 버튼 모두 `audioBridge.binauralLayout(token, true, true)`를 호출하여 **0초부터 즉시 재생**한다.
- 저장 버튼은 현재 파일 설정으로 저장한다. 별도 명령으로 동일 폴더·동일 채널 수 적용도 제공한다.
- 자동 판정 버튼은 클릭 가능해야 하며 현재 범위의 수동 저장을 지우고 자동 결과로 돌아간다.

비교 안내 문구는 다음처럼 사용한다.

> 대사나 중심음이 정면에 안정적으로 들리고, 뒤쪽 효과가 실제 뒤에서 들리는 쪽을 선택하세요.
> 저음 효과가 사라지거나 후방음이 한쪽으로 몰리는 방식은 잘못된 배치일 가능성이 높습니다.

### 5.6 `src/components/Player.tsx`

- 출력 라벨을 채널 수만으로 계산하지 않는다.
- `sf-audio-event`의 실제 `layout_resolved` 결과를 기준으로 표시한다.
- 바이노럴 버튼은 스테레오 파일에서 우회 재생되더라도 켜진 상태를 유지한다.
- 판정 보류 상태에서는 `출력 (판정된 포맷) → 2ch(Binaural)` 대신 일반 스테레오 출력과
  `채널 배치 확인 필요` 안내를 표시한다.
- 메뉴가 열린 상태에서도 비교 재생 버튼 자체가 오디오 명령을 보내므로 하단 재생 버튼을 누를
  필요가 없어야 한다.

## 6. 사용자에게 보이는 정책

| 상황 | 화면/재생 동작 |
|---|---|
| 자동 판정 성공 | 별도 팝업 없이 바이노럴 재생 |
| 스테레오 파일 | 바이노럴 버튼은 켜진 채로 원본 스테레오 재생 |
| 4ch 정보 없음 | Quad로 자동 처리 |
| 9ch 정보 없음 | 일반 스테레오 재생 + 7.0.2/AmbiX 선택 안내 |
| Ambisonic 표기만 있고 규격 없음 | 일반 스테레오 재생 + AmbiX/FuMa 비교 |
| AMBEO A-format 가능성 | 자동 변환 금지 + 원본/변환본 확인 안내 |
| 수동 파일 설정 존재 | `개별 저장됨` 표시, 다음 실행에도 유지 |
| 수동 폴더 설정 존재 | `폴더 설정` 표시, 정확히 같은 폴더·채널 수에만 적용 |
| 자동으로 되돌리기 | 해당 범위 설정 삭제 후 판정 결과 재적용 |

## 7. 성능 원칙

- DB 컬럼 추가 금지.
- 전체 라이브러리 재인덱싱 금지.
- 파일 선택 UI 스레드에서 네트워크 WAV 열기 금지.
- iXML/마스크는 재생 워커 또는 별도 waveform Python 서비스에서 현재 파일만 읽는다.
- 원본의 `read_wave_layout_metadata()`는 경로·크기·mtime 기준 LRU 캐시를 사용한다.
- LFE 주파수 분석은 자동 판정에 넣지 않는다. 추가 오디오 읽기 비용 대비 이득이 작고 오탐 시
  더 위험하다.

## 8. 필수 테스트

원본 `tests/test_binaural.py`의 아래 시나리오를 Tauri 브리지 테스트에도 복제한다.

1. iXML 영화형 순서 5.1 → 각도 `(30, 0, -30, 110, -110, 0)`, mute `(5,)`
2. iXML 영화형 + 충돌하는 `0x3F` → iXML 승리
3. `CHANNEL_INDEX`만 존재 → 인터리브 근거로 사용하지 않음
4. `0x13F` → 6.1
5. 파일 설정 > 정확한 폴더·동일 채널 수 설정
6. 일반 메뉴에 `WAVE`, `film`, `SMPTE` 문자열 없음
7. 비교 버튼 클릭 → override 변경 + 0초부터 play
8. 비교 창 취소 → 저장된 설정 또는 자동 판정으로 복원
9. 새 파일 선택 중 이전 비교 창이 닫혀도 이전 파일이 다시 로드되지 않음
10. 스테레오 재생 시 바이노럴 버튼의 사용자 ON 상태 유지
11. 재생 중 seek 직전 마지막 샘플은 5ms로 0에 수렴하고 새 위치는 18ms로 상승하며 틱이 없음
12. 바이노럴 pause/resume/stop/volume/file switch에서도 본체와 같은 ramp가 적용됨

실제 corpus 확인 기준:

- `DSGNBass_Futuristic...Surround 036a...wav`: iXML `L,C,R,Ls,Rs,LFE` → 5.1 다른 방식
- `WINDDsgn_894 Shrieking Wind_PSE_SL-ATX_dvW39.wav`: 마스크 `0x637` → 7.0 기본 방식

## 9. 완료 조건

- 사용자는 정상 사용 중 채널 순서 전문 용어를 고르지 않는다.
- 자동 판정된 실제 순서가 Python 오디오 그래프의 azimuth/elevation/mute에 반영된다.
- 비교 버튼 두 개 모두 팝업을 닫지 않고 0초부터 들린다.
- 저장·초기화·폴더 적용이 재실행 뒤에도 정확히 유지된다.
- 판정 보류 파일은 임의 바이노럴 변환 없이 안전하게 스테레오로 들린다.
- 기존 파일 선택·검색·인덱싱 성능에 추가 전수 검사가 생기지 않는다.
