import { useEffect, useRef, useState } from "react";
import type { Row } from "../data";
import { Waveform } from "./Waveform";
import { IcoPlay, IcoPause, IcoStop, IcoSkip, IcoLoop, IcoVolume, IcoDown, IcoSegment } from "../icons";
import { cancelWaveform, fileStamp, loadQuickPeaks, loadWavePeaks, type WavePeaks } from "../backend";
import { BinauralMenu, layoutChoiceLabel } from "./BinauralMenu";
import { AmbisonicFormatDialog, ChannelOrderDialog,
         IemInstallDialog, LayoutRequiredDialog } from "./LayoutDialogs";
import { EMPTY_LAYOUT_DATA, folderDisplayName, folderScopeKey, getOverride,
         loadLayoutStore, overrideScope, saveLayoutStore, setFileOverride,
         setFolderOverride, splitLayoutOverride,
         type LayoutData } from "../layoutStore";
import { audioBridge, exportRegion, loadLayoutInfo, onAudioEvent, onAudioPos,
         openExternal, type LayoutInfo } from "../backend";
import { startRegionDrag } from "../drag";

/* 바이노럴을 못 쓰는 이유가 **IEM 플러그인 설치 문제**인지 가린다.
   원본 app/binaural.py:is_iem_install_issue 의 토큰 목록을 그대로 옮긴 것이다.

   ⚠ "사용 불가"를 전부 설치 안내로 처리하지 말 것 (조사 Q02). 사이드카 실행 실패,
     오디오 장치 없음, 준비 응답 시간 초과도 사용 불가인데 그때 설치 페이지로 보내면
     엉뚱한 안내가 된다. 원본은 이 판정으로 구분하고, 설치 문제가 아니면 안내창을
     띄우지 않는다 (사유는 버튼 툴팁에 이미 나온다 — binauralTip). */
const IEM_INSTALL_TOKENS = [
  "IEM MultiEncoder",
  "IEM Plug-in Suite",
  "IEM 플러그인 버전",
  "지원하는 IEM 플러그인 버전",
];

function isIemInstallIssue(reason: string) {
  const text = String(reason || "");
  return IEM_INSTALL_TOKENS.some((token) => text.includes(token));
}
import { makeRegionDragImage } from "../dragImage";

type Props = {
  row: Row | null;
  playing: boolean;
  onToggle: () => void;
  onStop: () => void;
  onSeek?: (positionMs: number) => void;
  /* 원본 _on_drag_region — 영역/세그먼트를 DAW 로 떨어뜨렸을 때.
     상위가 stop_on_drag 설정을 보고 재생을 멈춘다 (선택 영역은 남긴다). */
  onRegionDropped?: () => void;
  onRate?: (rate: number) => void;
  onVolume?: (gain: number) => void;
  /* 원본 config volume_pos1k / speed_rate 저장용 */
  onPersist?: (patch: { volumePos1k: number; speedRate: number }) => void;
  onBinaural?: (enabled: boolean) => void;
  onForcePlay?: () => void;
  /* 원본 config 의 저장값 — 볼륨 슬라이더 위치(750=0dB)와 재생 속도 */
  initialVolPos?: number;
  initialSpeedRate?: number;
  /* 단축키(R/S/Home)는 원본에서 ApplicationShortcut 으로 player 메서드를 직접 부른다.
     여기서는 App 이 카운터를 올려 같은 동작을 트리거한다. */
  cmdToggleLoop?: number;
  cmdToggleSegments?: number;
  cmdSeekToStart?: number;
  /** 바이노럴 켜기/끄기 (단축키 기본 B) */
  cmdToggleBinaural?: number;
  /** 채널 배치 판별이 진행 중임을 알린다 — 중앙 로더로 표시한다 (사용자 지시) */
  onLayoutProbe?: (active: boolean, label: string) => void;
  /** 이 패널이 직접 띄운 창/메뉴가 열렸는지 알린다 — 그동안 단축키를 막는다 (조사 Q10) */
  onOverlayChange?: (open: boolean) => void;
};

const DRAG_THRESHOLD_PX = 6;
const SEG_SNAP_OFFSET_SEC = 0.1;
/* 원본 _paint_ruler 의 눈금 간격 후보 (초) */
const RULER_STEPS = [0.0001, 0.0002, 0.0005, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05,
                     0.1, 0.2, 0.5, 1, 2, 5, 10, 30, 60, 300, 600];
/* 원본이 파형을 그릴 수 있는 확장자 (WAVEFORM_EXTS) */
const WAVEFORM_EXTS = new Set(["wav", "flac", "aif", "aiff", "ogg", "oga", "w64", "rf64", "mp3"]);

/* 원본 _fmt_ruler_label — step 크기에 따라 분/초/ms 전환 */
function fmtRulerLabel(t: number, step: number) {
  if (step >= 60) return `${Math.floor(t / 60)}:${String(Math.floor(t) % 60).padStart(2, "0")}`;
  if (step >= 1) {
    if (t >= 60) {
      const m = Math.floor(t / 60);
      return `${m}:${String(Math.floor(t - m * 60)).padStart(2, "0")}`;
    }
    return `${Math.floor(t)}s`;
  }
  if (step >= 0.1) return `${t.toFixed(1)}s`;
  if (step >= 0.001) return `${Math.round(t * 1000)}ms`;
  return `${(t * 1000).toFixed(1)}ms`;
}
const VOL_MAX_DB = 6.02;
const VOL_FLOOR_DB = -50;
const VOL_UNITY_POS = 750;
const VOL_TOP_POS = 1000;
const VOL_WHEEL_DB_STEP = 0.25;

function speedFromPos(pos: number) {
  return Math.round((2 ** (pos / 100)) * 100) / 100;
}

function speedPosFromText(text: string) {
  const value = Number.parseFloat(text.replace(/x$/i, "").trim());
  if (Number.isNaN(value)) return null;
  return Math.round(100 * Math.log2(Math.max(0.5, Math.min(2, value))));
}

function fmtSpeed(rate: number) {
  return Math.round(rate * 100) % 10 === 0 ? `${rate.toFixed(1)}x` : `${rate.toFixed(2)}x`;
}

/* 원본 _fmt_ms (player_widget.py:2786) — 초 단위 M:SS 만 표시한다 (센티초 없음).
   원본은 텍스트가 실제로 바뀔 때만 setText 하여 Qt 부담을 줄인다. */
function fmtClock(seconds: number) {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function volPosToDb(pos: number) {
  if (pos <= 0) return -Infinity;
  if (pos >= VOL_UNITY_POS) return (pos - VOL_UNITY_POS) / (VOL_TOP_POS - VOL_UNITY_POS) * VOL_MAX_DB;
  return VOL_FLOOR_DB * (1 - (pos - 1) / (VOL_UNITY_POS - 1));
}

function volDbToPos(db: number) {
  if (!Number.isFinite(db) || db <= VOL_FLOOR_DB) return 0;
  const clamped = Math.min(db, VOL_MAX_DB);
  const pos = clamped >= 0
    ? VOL_UNITY_POS + clamped / VOL_MAX_DB * (VOL_TOP_POS - VOL_UNITY_POS)
    : 1 + (1 - clamped / VOL_FLOOR_DB) * (VOL_UNITY_POS - 1);
  return Math.round(Math.max(0, Math.min(VOL_TOP_POS, pos)));
}

function volPosToGain(pos: number) {
  const db = volPosToDb(pos);
  if (db === -Infinity) return 0;
  return 10 ** (db / 20);
}

function fmtDb(pos: number) {
  const db = volPosToDb(pos);
  if (db === -Infinity) return "−∞ dB";
  if (Math.abs(db) < 0.005) return "0.00 dB";
  return `${db >= 0 ? "+" : ""}${db.toFixed(2)} dB`;
}

function parseDbText(text: string) {
  const s = text.trim().toLowerCase().replace("db", "").replace("−", "-").trim();
  if (!s) return null;
  if (["-inf", "-infinity", "inf", "mute", "음소거", "-∞", "∞"].includes(s)) return -Infinity;
  const value = Number.parseFloat(s.replace(/^\+/, ""));
  return Number.isNaN(value) ? null : value;
}

function EditableValue({
  value,
  className,
  title,
  onCommit,
}: {
  value: string;
  className: string;
  title: string;
  onCommit: (text: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!editing) setDraft(value);
  }, [editing, value]);

  useEffect(() => {
    if (!editing) return;
    inputRef.current?.focus();
    inputRef.current?.select();
  }, [editing]);

  const finish = (commit: boolean) => {
    const text = draft.trim();
    setEditing(false);
    if (commit && text) onCommit(text);
  };

  if (editing) {
    return (
      <input
        ref={inputRef}
        className={`value-edit ${className}`}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => finish(true)}
        onKeyDown={(event) => {
          if (event.key === "Enter") finish(true);
          if (event.key === "Escape") finish(false);
        }}
      />
    );
  }

  return (
    <span className={`value-label ${className}`} data-tip={title} onDoubleClick={() => setEditing(true)}>
      {value}
    </span>
  );
}

export function Player({ row, playing, onToggle, onStop, onSeek, onRegionDropped,
                        onRate, onVolume, onPersist, onBinaural, onForcePlay,
                        initialVolPos, initialSpeedRate,
                        cmdToggleLoop = 0, cmdToggleSegments = 0, cmdSeekToStart = 0,
                        cmdToggleBinaural = 0,
                        onLayoutProbe, onOverlayChange }: Props) {
  const [pos, setPos] = useState(0);          /* 0~1 */
  /* ── 세로(진폭) 줌 (원본 WaveformView) ─────────────────────────────────
     가로 줌·팬은 폐지했다 — 파형 캐시가 줌 단계별 미리계산 때문에 파일당 1.33MB
     였고 그 중 75%가 최고 확대 레벨 하나였다 (17.58GB). 이제 단일 레벨만 저장해
     파일당 64KB (-95%). 파형은 항상 전체가 폭에 꽉 차게 그려진다.
     세로 줌은 그리는 배율만 바꿔 캐시와 무관하므로 그대로 남긴다.
     원본은 target 과 current 를 따로 두고 매 프레임 current 를 target 으로
     **지수 보간**한다 (_tick_zoom_anim, ZOOM_ANIM_T 0.4, log-space lerp —
     1.5x 단계가 같은 시각 속도로 보인다). 같은 보간을 쓴다. */
  const [amp, setAmp] = useState(1);
  const target = useRef({ amp: 1 });
  const zoomRaf = useRef(0);

  const ZOOM_ANIM_T = 0.4;      // 원본 ZOOM_ANIM_T
  const ZOOM_DONE_EPS = 0.001;  // 원본 ZOOM_DONE_EPS 상당

  const startZoomAnim = () => {
    if (zoomRaf.current) return;
    const step = () => {
      let done = true;
      setAmp((cur) => {
        const t = target.current.amp;
        if (Math.abs(cur - t) < ZOOM_DONE_EPS) return t;
        done = false;
        return Math.exp(Math.log(cur) + (Math.log(t) - Math.log(cur)) * ZOOM_ANIM_T);
      });
      if (done) { zoomRaf.current = 0; return; }
      zoomRaf.current = requestAnimationFrame(step);
    };
    zoomRaf.current = requestAnimationFrame(step);
  };

  useEffect(() => () => { if (zoomRaf.current) cancelAnimationFrame(zoomRaf.current); }, []);
  const [loop, setLoop] = useState(false);
  const [segmentsVisible, setSegmentsVisible] = useState(true);
  /* 실제 파형 피크 + 세그먼트 — 원본 peaks_cache / _extract_peaks 결과.
     원본은 WaveformRunnable 이 백그라운드에서 읽어 done 시그널로 넘긴다. */
  const [wave, setWave] = useState<WavePeaks | null>(null);
  /* 원본 begin_loading → paintEvent 의 _loading 분기: 파일 전환 즉시 이전 파형을
     비우고 가운데에 "파형 로딩 중..." 만 그린다 (player_widget.py:2652).
     PoC 는 피크가 오기 전에 합성 파형을 그려 **무관한 파형**이 보였다. */
  const [waveLoading, setWaveLoading] = useState(false);
  /* ── 경로별 파형 기억 (되돌아올 때 '파형 로딩 중' 이 다시 뜨지 않게) ──────────
     ⚠ 상한은 **개수가 아니라 용량**이다. 실측: 피크 payload 는 파일당 중간값 1.33MB,
     최대 6.0MB (디스크 캐시 17,851개 / 17.55GB 기준). 개수로 자르면 메모리 사용량이
     파일에 따라 8~48MB 로 들쭉날쭉해서 상한 의미가 없다.
     **원본의 디스크 캐시(%LOCALAPPDATA%\SoundField\peaks_cache, 영구 보관)는 그대로다.**
     이건 그 위에 얹는 메모리 층일 뿐이고, 넘치면 디스크 캐시에서 다시 읽는다
     (재추출이 아니라 캐시 읽기라서 빠르다). */
  const WAVE_CACHE_MAX_BYTES = 256 * 1024 * 1024;
  /* 이 시간 안에 파형이 도착하면 '로딩 중' 문구를 아예 띄우지 않는다.
     실측(로그 22건): 캐시 적중 시 파형 로드는 18~83ms 다. 220ms 는 그 최대값과
     너무 가까워 NAS/부하 상황에서 문구가 번쩍일 수 있다. 실측 최대의 7배로 둔다 —
     진짜 추출이 필요한 파일(수 초)에서는 그대로 뜬다. */
  const waveCache = useRef<Map<string, WavePeaks>>(new Map());
  /* 파형을 받을 때 그 파일의 도장(크기|수정시각) — 캐시 재사용 검사용 (조사 Q15) */
  const waveStamp = useRef<Map<string, string>>(new Map());
  /* 값이 오르면 파형 훅을 다시 돌린다 (캐시를 버린 직후) */
  const [waveReload, setWaveReload] = useState(0);
  const waveCacheBytes = useRef(0);
  const wavePeaksBytes = (peaks: WavePeaks) =>
    peaks.levels.reduce((sum, level) => sum + (level.data?.byteLength ?? 0), 0);
  const [waveFailed, setWaveFailed] = useState(false);
  const [speedPos, setSpeedPos] = useState(() =>
    initialSpeedRate && initialSpeedRate > 0
      ? Math.round(Math.log2(initialSpeedRate) * 100)
      : 0);
  const [volPos, setVolPos] = useState(initialVolPos ?? 750);
  const [binaural, setBinaural] = useState(false);
  const [binauralAvailable, setBinauralAvailable] = useState<boolean | null>(null);
  /* ── 엔진이 실제로 바이노럴로 내보내고 있는가 (조사 B01) ────────────────────
     원본 _update_output_control 은 **player.isBinauralActive()** 와 상태 기계
     (loading/active/error/bypass/needs_layout)를 함께 보고 라벨과 표시등을 정한다
     (player_widget.py:3956). PoC 는 토글·채널수·배속·판정 프리셋만 보고 정해서,
     엔진이 실패해 스테레오로 되돌아갔는데도 화면은 "→ Binaural 2ch" 로 보였다.
     · engineBinaural  : 위치 보고에 실린 isBinauralActive 값
     · binauralVisual  : 상태 문구로 판정한 상태 (원본 _on_binaural_status 와 같은 표) */
  const [engineBinaural, setEngineBinaural] = useState(false);
  const [binauralVisual, setBinauralVisual] =
    useState<"off" | "loading" | "active" | "error" | "bypass" | "needs_layout">("off");
  const [binauralUnavailableReason, setBinauralUnavailableReason] = useState("");
  /* 엔진이 보내온 마지막 상태 문구 — 바이노럴 버튼 툴팁에 그대로 쓴다
     (원본 _binaural_status_text, player_widget.py:3152) */
  const [binauralStatus, setBinauralStatus] = useState("");
  /* 원본 _layout_overrides (app/binaural.py:LayoutOverrideStore) — 파일별/폴더별
     사용자 지정 채널 배치. 메뉴에서 고르면 저장되고 "자동" 을 고르면 지운다
     (_apply_binaural_layout("")). 원본은 로컬 JSON 에 즉시 저장하므로 여기서도
     사본(poc\binaural_layouts.json)에 바로 저장한다. */
  const [layoutData, setLayoutData] = useState<LayoutData>(EMPTY_LAYOUT_DATA);
  /* 저장값을 다 읽었는지. 이걸 보기 전에 판정(loadLayoutInfo)을 시작하면 저장한 파일도
     "저장 안 됨"으로 판정돼 앱을 켠 직후 첫 파일에서 순서 확인 팝업이 또 뜬다
     (2026-09-16 사용자 보고: "껐다 켜면 같은 소리에 다시 묻는다"). 읽기에 실패해도
     true 로 올려 예전처럼 저장값 없이 진행한다 — 화면이 멈추지 않게. */
  const [layoutReady, setLayoutReady] = useState(false);
  useEffect(() => {
    let alive = true;
    loadLayoutStore()
      .then((got) => { if (alive) setLayoutData(got); })
      .finally(() => { if (alive) setLayoutReady(true); });
    return () => { alive = false; };
  }, []);

  const [operationError, setOperationError] = useState("");
  const savingLayout = useRef(false);
  const putLayout = async (next: LayoutData): Promise<boolean> => {
    if (savingLayout.current) return false;
    savingLayout.current = true;
    const targetPath = row?.fullPath;
    try {
      if (!await saveLayoutStore(next)) {
        setOperationError("채널 배치를 저장하지 못했습니다. 다시 시도하세요.");
        return false;
      }
      setLayoutData(next);
      setOperationError("");
      return targetPath === rowPathRef.current;
    } finally { savingLayout.current = false; }
  };
  const [layoutMenuAt, setLayoutMenuAt] = useState<DOMRect | null>(null);
  /* 원본 _show_channel_order_comparison — 채널 순서 A/B 비교 창 */
  const [compareOpen, setCompareOpen] = useState(false);
  /* 원본 _show_layout_required_popup 의 앰비소닉 분기 (_AmbisonicFormatDialog).
     원본은 판정이 {ambix, fuma} 로 갈릴 때 파일당 한 번 자동으로 띄우고,
     그 뒤에는 오른쪽 화살표 메뉴에서 다시 열 수 있다. */
  const [formatDialog, setFormatDialog] = useState<{ reason: string } | null>(null);
  /* 원본 _show_layout_required_popup 의 일반(스피커 배치) 분기 */
  const [requiredDialog, setRequiredDialog] = useState<{ reason: string } | null>(null);
  /* 원본 _show_iem_install_popup — 바이노럴 처리 구성 요소가 없을 때 */
  const [iemOpen, setIemOpen] = useState(false);
  /* 원본 _preview_binaural_layout — 저장하지 않고 그 배치로 처음부터 재생 */
  const [previewPreset, setPreviewPreset] = useState("");
  /* 원본 _resolved_binaural_layout / _update_output_control 이 쓰는 판정 결과.
     판정은 원본 app.binaural.resolve_layout 을 브리지로 그대로 호출한다. */
  const [layoutInfo, setLayoutInfo] = useState<LayoutInfo | null>(null);
  const [sel, setSel] = useState<[number, number] | null>(null);
  const [activeSeg, setActiveSeg] = useState(-1);
  const [playerWidth, setPlayerWidth] = useState(1280);
  const playerRef = useRef<HTMLDivElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);

  /* ── 재생 헤드 (원본 _on_tick, player_widget.py:4704 그대로) ────────────────
     원본은 8ms(FRAME_MS) 타이머로 **엔진 위치**(player.position())를 읽고 이렇게 그린다:
       · real 이 이전 값보다 **커지기 전까지** _playhead_smoothing_ready = False →
         이 동안 재생헤드는 real(=0) 에 붙어 있는다. 파형 로딩/디코딩으로 실제 소리가
         늦게 시작해도 헤드가 앞서 나가지 않는다.
       · 보고 사이는 real + (경과 * 배속) 으로 보간하되 **lead cap** 을 넘지 못한다
         (바이노럴 125ms / 길이 1초 미만 12ms / 그 외 32ms).
       · 재생 중에는 max(직전 표시값, 목표) 로 뒤로 가지 않는다.
       · 반복 재생은 loop_end 30ms 전에 loop_start 로 되감는다.
     PoC 는 이 모델 없이 playing 이 켜진 순간의 performance.now() 부터 자체 시계로
     굴러가서 소리보다 앞섰다 (사용자 보고: "파형 로딩 중 재생하면 타이밍이 밀린다"). */
  const engineRef = useRef({ pos: 0, dur: 0, playing: false, binaural: false });
  const lastKnownRef = useRef(0);
  const lastTRef = useRef(0);
  const readyRef = useRef(false);
  const displayRef = useRef(0);
  const sawPlayingRef = useRef(false);
  /* 실측 보고 간격 (ms) — lead cap 이 한 주기를 덮게 하려면 필요하다.
     실측: 엔진 위치 갱신 단위 23ms(1024프레임 @44.1kHz)가 바닥이고, 파이프/GIL
     지터로 드물게 120ms 까지 벌어진다. 평균만 쓰면 그 스파이크에서 톱니가 남으므로
     **감쇠 최대값**을 쓴다 (스파이크 직후엔 넉넉히, 안정되면 다시 좁아진다). */
  const gapMaxRef = useRef(30);
  /* 배속은 아래에서 선언되므로 ref 로 최신값을 들고 있는다 (원본 _playback_rate) */
  const speedRef = useRef(1);

  /** 사용자 조작(클릭 seek / 파일 전환)으로 위치를 강제할 때 (원본도 seek 시 초기화) */
  /* ── 시크 착지 대기 (원본에는 없어도 되는 보정) ─────────────────────────────
     원본은 엔진이 같은 프로세스라 setPosition() 직후 player.position() 이 곧바로 새
     위치를 준다. PoC 는 파이프를 거치므로 시크 뒤 한동안 **이전(더 큰) 위치**가 계속
     도착하고, "재생 중에는 뒤로 가지 않는다"는 원본 규칙(max)을 그대로 지키면 재생바가
     이전 위치에 박혀 멈춘다 (실측 증상: 세그먼트를 왔다갔다 하거나 파형을 클릭할 때).
     → 요청 위치 근처가 도착할 때까지 화면은 요청 위치에 고정하고, 그 사이 보고는
       비교 기준만 갱신한다. 안 오면 1.5초 뒤 포기하고 정상 경로로 돌아간다. */
  const pendingSeekRef = useRef<{ ms: number; t: number } | null>(null);
  /* 시크 요청과 착지 판정이 **같은 길이 기준**을 써야 한다. DB 길이(row.dur)와 엔진이
     보고한 길이가 다르면(실측 2133ms vs 2033ms) 요청 위치와 판정 위치가 어긋나
     착지를 못 알아채고 1.5초 포기 경로로 빠진다. */
  const durMsNow = () => (engineRef.current.dur > 0
    ? engineRef.current.dur : (row ? row.dur * 1000 : 0));
  const SEEK_LAND_MS = 150;   /* 엔진 위치 갱신 단위 23ms + 파이프 지터 여유 */

  const resetPlayhead = (ratio: number) => {
    const ms = Math.max(0, ratio * durMsNow());
    lastKnownRef.current = ms;
    lastTRef.current = performance.now();
    readyRef.current = false;
    displayRef.current = ms;
    pendingSeekRef.current = { ms, t: performance.now() };
    /* ⚠ 끝 감지 무장을 해제한다. `sawPlayingRef` 는 한 번 true 가 되면 예전에는
       **다시 false 가 되는 곳이 없었다.** 그래서 파일이 한 번 끝까지 가면
         · sawPlayingRef = true (영구)
         · eng.playing = false (아직 재생 보고 전)
         · 위치가 끝에 멈춰 있음
       이 세 조건이 계속 성립해서, 세그먼트 헤더로 재생을 걸어도 **매 프레임 onStop()
       이 불려 즉시 죽었다** (사용자 신고: "세그먼트 헤더 클릭하면 멈춤", 3회 재현).
       의미를 "지난 탐색 이후 재생을 본 적 있는가" 로 바꾼다 — 탐색하면 무장 해제,
       엔진이 재생을 보고하면 다시 무장. 진짜 끝에서는 그대로 한 번 발동한다. */
    sawPlayingRef.current = false;
  };

  /* 현재 파일 경로 — 이벤트 핸들러에서 최신값이 필요하다 */
  const rowPathRef = useRef("");
  rowPathRef.current = row?.fullPath ?? "";
  const samePath = (a: string, b: string) =>
    a.replace(/\//g, "\\").toLowerCase() === b.replace(/\//g, "\\").toLowerCase();

  useEffect(() => onAudioPos((info) => {
    /* ⚠ 파일을 연속으로 넘기면 **이전 파일의 위치 보고가 늦게 도착**한다.
       그대로 받으면 real 이 이전 파일의 큰 값으로 튀고, 재생 중 단조 증가 규칙
       (max) 때문에 재생바가 그 위치에 박혀 움직이지 않는다 (사용자 보고 증상).
       보고에 실린 경로가 현재 파일과 다르면 버린다. */
    if (info.path && rowPathRef.current && !samePath(info.path, rowPathRef.current)) return;
    engineRef.current = {
      pos: info.position_ms,
      dur: info.duration_ms,
      playing: info.playing,
      binaural: info.binaural,
    };
    setEngineBinaural(Boolean(info.binaural));
    if (info.playing) sawPlayingRef.current = true;
  }), []);

  useEffect(() => onAudioEvent((event) => {
    if (event.type === "availability") {
      setBinauralAvailable(event.available);
      setBinauralUnavailableReason(event.reason || "");
      if (!event.available) {
        setBinaural(false);
        /* 원본 _on_binaural_availability: 사용 불가로 바뀌면 상태는 '오류' 다 */
        setBinauralVisual("error");
      }
      return;
    }
    if (event.type === "binaural-status") {
      /* 원본 _on_binaural_status (player_widget.py:3151) 의 문구 판정표를 그대로
         옮긴 것이다 — 순서까지 같아야 한다 ("기존 재생" 은 더 구체적인 문구들을
         먼저 걸러낸 뒤에 검사한다). */
      const text = String(event.message || "");
      setBinauralStatus(text);
      if (text.includes("재생 중")) setBinauralVisual("active");
      else if (text.includes("준비 중")) setBinauralVisual("loading");
      else if (text.includes("사용자가 바이노럴을 껐습니다")) setBinauralVisual("off");
      else if (text.includes("채널 배치 선택 필요")) setBinauralVisual("needs_layout");
      else if (text.includes("현재 파일은 기존 재생")
               || text.includes("배속 재생은 기존 방식")
               || text.includes("현재 파일은 변환 없이 재생")) setBinauralVisual("bypass");
      else if (text.includes("기존 재생")) setBinauralVisual("error");
      else setBinauralVisual("off");
    }
  }), []);

  useEffect(() => {
    if (!playing || !row) return;
    let raf = 0;
    const tick = () => {
      const eng = engineRef.current;
      const durMs = eng.dur > 0 ? eng.dur : row.dur * 1000;
      if (durMs > 0) {
        let real = eng.pos;
        const now = performance.now();

        /* 반복 — 원본은 loop_end 30ms 전에 loop_start 로 되감는다 */
        if (loop && eng.playing) {
          const startMs = (sel ? sel[0] : 0) * durMs;
          const endMs = (sel ? sel[1] : 1) * durMs;
          if (endMs > startMs && real >= endMs - 30) {
            onSeek?.(startMs);
            real = startMs;
            lastKnownRef.current = startMs;
            lastTRef.current = now;
            displayRef.current = startMs;
            readyRef.current = false;
            /* 되감기도 시크다 — 착지를 기다리지 않으면 매 반복마다 같은 멈춤이 생긴다 */
            pendingSeekRef.current = { ms: startMs, t: now };
          }
        }

        /* 시크 착지 대기 */
        const seek = pendingSeekRef.current;
        if (seek) {
          const landed = Math.abs(real - seek.ms) <= SEEK_LAND_MS;
          const gaveUp = now - seek.t > 1500;
          if (!landed && !gaveUp) {
            /* 아직 이전 위치가 도착하는 중 — 화면은 요청 위치에 둔다 */
            displayRef.current = seek.ms;
            lastKnownRef.current = real;
            lastTRef.current = now;
            readyRef.current = false;
            setPos(Math.min(1, seek.ms / durMs));
            raf = requestAnimationFrame(tick);
            return;
          }
          pendingSeekRef.current = null;
          lastKnownRef.current = real;
          lastTRef.current = now;
          displayRef.current = real;
          readyRef.current = false;
        }

        let target: number;
        if (real !== lastKnownRef.current) {
          if (real > lastKnownRef.current) {
            readyRef.current = true;
            /* 직전 갱신까지 걸린 시간 = 실제 보고 간격. 완만하게 따라가 스파이크
               하나로 cap 이 튀지 않게 한다. */
            const gap = now - lastTRef.current;
            if (gap > 0 && gap < 400) {
              /* 감쇠 최대값: 매 갱신마다 3% 줄이고, 더 큰 간격이 오면 그 값으로 올린다 */
              gapMaxRef.current = Math.max(gap, gapMaxRef.current * 0.97);
            }
          }
          lastKnownRef.current = real;
          lastTRef.current = now;
          target = real;
        } else if (eng.playing && readyRef.current) {
          target = Math.min(real + (now - lastTRef.current) * speedRef.current, durMs);
        } else {
          lastTRef.current = now;
          target = real;
        }

        if (eng.playing) {
          /* 원본 lead_cap_ms 의 **원칙**: "한 보고 주기를 충분히 덮도록" 보간한다.
             원본은 엔진을 같은 프로세스에서 8ms 로 폴링하므로 32ms 고정으로 충분하고,
             자기 바이노럴 사이드카(100ms 보고)에는 125ms 를 쓴다.
             PoC 는 모든 경로가 파이프라 간격이 실측 평균 26ms·최대 120ms → 원본 값을
             **바닥**으로 두고 최근 최대 간격의 1.2배까지 넓힌다 (상한 250ms).
             고정 32ms 로 두면 32ms 만 가고 멈췄다 점프하는 톱니가 생긴다. */
          const base = eng.binaural ? 125 : (durMs < 1000 ? 12 : 32);
          const cap = Math.min(250, Math.max(base, gapMaxRef.current * 1.2));
          target = Math.min(target, real + cap, durMs);
          displayRef.current = Math.max(displayRef.current, target);
        } else {
          displayRef.current = target;
        }
        setPos(displayRef.current / durMs);

        /* 끝까지 갔고 엔진이 멈췄으면 정지 (원본은 EndOfMedia 신호) */
        /* ⚠ 탐색이 아직 착지하지 않았으면 끝 감지를 **하지 않는다.**
           로그로 확정된 증상: 세그먼트 헤더 클릭 135ms 뒤에 앱이 stop 을 보냈다.
             11:07:34,472 seek 5320 / play  → 11:07:34,607 stop / pause
           원인은 이 판정이 **오래된 위치값**을 보고 "끝났다" 고 오판한 것이다.
           엔진 위치 보고는 값이 바뀔 때만 오고, 탐색 직후에는 직전(끝 근처) 값이
           남아 있다. 게다가 displayRef 는 단조 증가라 한 번 끝으로 튀면 안 내려온다.
           pendingSeekRef 가 비어 있을 때(=탐색 착지 확인됨)만 판정한다. */
        if (!loop && !pendingSeekRef.current && sawPlayingRef.current && !eng.playing
            && real >= durMs - 120 && displayRef.current >= durMs - 120) {
          setPos(1);
          /* 한 번만 발동시킨다 — 안 그러면 다음 프레임에서 같은 조건이 또 성립해
             재생을 다시 걸어도 계속 정지시킨다 (위 resetPlayhead 주석 참고). */
          sawPlayingRef.current = false;
          onStop();
          return;
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onSeek, onStop, playing, row, loop, sel]);

  /* 원본 _file_switch_zoom: 파일이 바뀌면 세로(진폭) 줌을 1.0 으로 되돌린다
     (줌 메모리 폐지 — 이전 사운드에 맞춰 키운 배율이 음량이 다른 새 사운드에서
     잘려 보이던 문제 때문). */
  useEffect(() => {
    setPos(0); setAmp(1); setSel(null); setActiveSeg(-1);
    target.current = { amp: 1 };
  }, [row?.id]);

  useEffect(() => {
    const element = playerRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setPlayerWidth(entry.contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const editing = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement ||
        target instanceof HTMLSelectElement || Boolean(target?.isContentEditable);
      if (editing || event.repeat || event.defaultPrevented) return;
      const key = event.key.toLowerCase();
      /* 원본 WaveformView.keyPressEvent (player_widget.py:2368):
         파형에 포커스가 있고 선택 영역이 있으면 Esc 로 영역을 지운다.
         PoC 는 파형이 포커스를 받지 않으므로 영역이 있을 때만 전역에서 받는다. */
      if (key === "escape") {
        if (!sel) return;
        event.preventDefault();
        setSel(null);
        return;
      }
      /* 여기서 Home / R / S 를 처리하지 말 것 (조사 K01).
         단축키는 App 이 환경설정 값으로 판정하고, 결과를 cmdSeekToStart /
         cmdToggleLoop / cmdToggleSegments 로 이 컴포넌트에 내려 준다.
         여기서 또 고정 키를 잡으면 사용자가 단축키를 바꿔도 R 이 계속 반복 재생을
         토글하고, 창이 열려 있어도 키가 먹는다 (원본은 단축키 등록이 한 곳뿐이다).
         Esc(영역 지우기)만 남긴다 — 파형 전용 동작이라 설정 대상이 아니다. */
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [sel]);


  /* 이 패널이 띄운 창/메뉴가 열려 있는 동안은 위쪽(App)의 재생 단축키를 막는다
     (조사 Q10 — 원본의 이 창들은 모두 모달이라 뒤쪽 단축키가 안 먹는다).

     ⚠ **여기에는 "떠 있는 창·메뉴" 만 넣는다.** 판정 결과나 상태 값을 넣지 말 것.
       실제로 `layoutInfo`(채널 배치 판정 결과)를 넣는 실수를 했고, 그 결과
       멀티채널 파일을 한 번 고르면 값이 채워져 **Space·H·S·R·B·Home 전역 단축키가
       전부 죽었다** (사용자 신고 2026-09-08: "스페이스바가 전역으로 안 먹는다").
       결과표 안에서는 표 자체 키 처리라 Space 가 먹어서 더 헷갈렸다.
       layoutInfo 는 3채널 이상 파일이면 항상 값이 있는 **데이터**다 — 창이 아니다. */
  const overlayOpen = Boolean(compareOpen || formatDialog || requiredDialog || iemOpen
    || layoutMenuAt);
  useEffect(() => { onOverlayChange?.(overlayOpen); }, [overlayOpen]);

  /* 선택 영역을 ref 로도 들고 있는다 — mouseup 콜백이 최신 값을 봐야 한다 */
  const selRef = useRef<[number, number] | null>(null);
  useEffect(() => { selRef.current = sel; }, [sel]);
  const dragging = useRef(false);

  /* ── 영역 드래그 아웃 (원본 _on_drag_region + _resolve_export_path) ──
     우선순위: 세그먼트 헤더 드래그 범위 → 사용자 선택 영역 → 원본 전체.
     배속이 1.0x 가 아니면 배리스피드 렌더 파일(`이름_0.45x.wav`)로 내보낸다.
     드롭이 성사되고 stop_on_drag 가 켜져 있으면 재생을 멈춘다
     (시각 정보인 선택 영역은 지우지 않는다). */
  /* ── 왼쪽 버튼 눌림 추적 (가짜 드롭 방지) ────────────────────────────────────
     ⚠ 없으면 **재생이 제멋대로 멈춘다.** 확정된 사슬(로그 STOP-SRC 영역드롭):
       헤더를 10px 이상 움직여 클릭 → dragRegion → exportRegion(비동기, WAV 자르기)
       → 그 사이 사용자가 버튼을 뗌 → 버튼이 떨어진 상태로 OS 드래그를 시작하면
       드래그 소스가 "왼쪽 버튼 없음 = 드롭 완료" 로 **즉시** 보고 → onRegionDropped
       → stop_on_drag → 정지. 손도 안 뗀 드래그가 시작 즉시 드롭으로 끝난 것이다.
     드래그 임계값(10px)을 올리는 건 해결이 아니다 — 빈도만 줄고 조건은 남는다. */
  const pressedRef = useRef(false);
  useEffect(() => {
    const down = (event: MouseEvent) => { if (event.button === 0) pressedRef.current = true; };
    const up = (event: MouseEvent) => { if (event.button === 0) pressedRef.current = false; };
    window.addEventListener("mousedown", down, true);
    window.addEventListener("mouseup", up, true);
    return () => {
      window.removeEventListener("mousedown", down, true);
      window.removeEventListener("mouseup", up, true);
    };
  }, []);

  const dragRegion = (from: number, to: number) => {
    if (!row?.fullPath) return;
    /* 버튼이 이미 떨어져 있으면 드래그 의사가 없었다 — 내보내기도 하지 않는다.
       (헤더를 흔든 클릭마다 WAV 를 잘라 임시 폴더에 쓰고 있었다) */
    if (!pressedRef.current) return;
    const dur = row.dur || 0;
    const startSec = dur > 0 ? from * dur : null;
    const endSec = dur > 0 ? to * dur : null;
    setOperationError("");
    void exportRegion(row.fullPath, startSec, endSec, speed).then((exportPath) => {
      /* 자르기가 끝났을 때 이미 손을 뗐으면 드래그를 시작하지 않는다 — 시작하면
         가짜 드롭이 되어 재생이 멈춘다 (위 주석). */
      if (!pressedRef.current) return;
      /* 드래그 그림 — 끌어다 준 **구간의 파형** + 구간 길이
         (사용자 결정 2026-09-07). 이미 받아 둔 피크에서 그 구간만 잘라 쓴다.
         파일을 다시 읽지 않는다 — 드래그 시작 직전이라 늦으면 안 된다. */
      startRegionDrag(exportPath, () => onRegionDropped?.(), () => {
        const level = wave?.levels?.[wave.levels.length - 1];
        if (!level || !level.slices) return Promise.resolve(null);
        const ch = wave.channels || 1;
        const s0 = Math.max(0, Math.floor(from * level.slices));
        const s1 = Math.min(level.slices, Math.ceil(to * level.slices));
        if (s1 <= s0) return Promise.resolve(null);
        /* 그림 폭에 맞춰 최대 220개로 줄인다 (구간이 길면 통째로 그릴 필요 없다) */
        const want = Math.min(220, s1 - s0);
        const out: Array<[number, number]> = [];
        for (let i = 0; i < want; i++) {
          const a = s0 + Math.floor((i / want) * (s1 - s0));
          const b = s0 + Math.max(a + 1, Math.floor(((i + 1) / want) * (s1 - s0)));
          let mn = 0;
          let mx = 0;
          let seeded = false;
          for (let k = a; k < b && k < level.slices; k++) {
            /* (slices, channels, 2) C-order — 채널을 합쳐 하나로 본다 */
            for (let c = 0; c < ch; c++) {
              const base = (k * ch + c) * 2;
              const lo = level.data[base] / 32767;
              const hi = level.data[base + 1] / 32767;
              if (!seeded) { mn = lo; mx = hi; seeded = true; continue; }
              if (lo < mn) mn = lo;
              if (hi > mx) mx = hi;
            }
          }
          out.push([mn, mx]);
        }
        return makeRegionDragImage(out, Math.max(0, (to - from) * dur));
      });
    }).catch((error: unknown) => {
      setOperationError(`내보내기를 중단했습니다: ${error instanceof Error ? error.message : String(error)}`);
    });
  };

  const seekFromEvent = (e: React.MouseEvent) => {
    const el = wrapRef.current;
    if (!el || !row) return 0;
    const r = el.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    return x;
  };

  /* 원본 wheelEvent: Ctrl+Alt+휠 = 세로(진폭) 줌 1.5x/틱, 1~80 (MIN/MAX_AMP_ZOOM).
     가로 줌(Ctrl+휠)과 가로 스크롤(Shift+휠)은 폐지했다. */
  const onWheel = (e: React.WheelEvent) => {
    if (!row) return;
    if (!(e.ctrlKey && e.altKey)) return;
    e.preventDefault();
    if (e.deltaY === 0) return;
    target.current.amp = Math.min(80, Math.max(1, target.current.amp * (e.deltaY < 0 ? 1.5 : 1 / 1.5)));
    startZoomAnim();
  };

  /* 원본 _paint_ruler 규칙 그대로: 후보 간격 중 픽셀 간격이 60px 이상이 되는 첫 값을
     쓰고, 라벨 단위는 step 크기에 따라 분/초/ms 로 자동 전환한다. */
  const ticks = () => {
    if (!row || playerWidth <= 0) return [];
    const total = row.dur;
    const vpStart = 0;
    const vpSpan = Math.max(1e-6, total);
    /* 파형 영역 실제 폭 기준 (컨트롤 폭이 아니라) */
    const w = wrapRef.current?.clientWidth || playerWidth;
    const pxPerSec = w / vpSpan;
    let step = RULER_STEPS[RULER_STEPS.length - 1];
    for (const cand of RULER_STEPS) {
      if (pxPerSec * cand >= 59.999999) { step = cand; break; }
    }
    const out: { x: number; label: string }[] = [];
    let t0 = Math.floor(vpStart / step) * step;
    if (t0 < vpStart - 1e-9) t0 += step;
    for (let t = t0; t <= vpStart + vpSpan + 1e-6; t += step) {
      const x = ((t - vpStart) * pxPerSec / w) * 100;
      if (x >= 0 && x < 100) out.push({ x, label: fmtRulerLabel(t, step) });
    }
    return out;
  };

  const dur = row?.dur ?? 0;
  /* 파일이 바뀌면 원본과 같은 캐시/추출기로 파형을 읽어온다.
     실패(포맷 미지원/네트워크 지연)하면 null 로 두고 합성 파형이 대신 그려진다. */
  /* 원본 load_and_play: **새 파일**이면 _display_pos_ms=0, wave.set_position_ratio(0),
     시간 라벨 "0:00 / 0:00" 으로 되돌린다 (같은 파일 재요청도 setPosition(0)).
     PoC 는 이전 위치가 남아 새 사운드가 중간부터 재생되는 것처럼 보였다. */
  useEffect(() => {
    /* 경로가 다른 늦은 이벤트를 버리는 것만으로는 부족하다. 다음 이벤트가 오기
       전까지 engineRef 에는 직전 파일의 마지막 위치가 그대로 남아 있고, 위 tick의
       단조 증가(max) 규칙이 그 값을 다시 displayRef 에 넣으면 새 파일 재생바가
       그 지점에서 멈춘다. 파일 전환 자체를 하나의 새 위치 세션으로 취급해 엔진
       스냅샷과 보간 이력을 함께 비운다. */
    const keepBinaural = engineRef.current.binaural;
    engineRef.current = {
      pos: 0,
      dur: row ? Math.max(0, row.dur * 1000) : 0,
      playing: false,
      binaural: keepBinaural,
    };
    setPos(0);
    setSel(null);
    setActiveSeg(-1);
    resetPlayhead(0);
    sawPlayingRef.current = false;
    gapMaxRef.current = 30;
  }, [row?.fullPath]);

  /* ── 파형 로딩 (원본 begin_loading + load, player_widget.py:1650) ──
     원본 3단 정책을 그대로 옮겼다:
       ① 파일이 바뀌면 즉시 이전 파형을 비우고 "파형 로딩 중..." 으로 둔다.
          실제 추출은 **650ms(WAVEFORM_LOAD_DELAY_MS) 디바운스** 뒤에 시작한다 —
          화살표로 목록을 훑을 때 NAS 를 두드리지 않기 위한 정책이다.
       ② .wav 이고 30MB 이상이면 먼저 러프 파형(16 슬라이스)을 띄운다
          (quick). 그러면 큰 파일도 곧바로 모양이 보인다.
       ③ quick 이 떴으면 1800ms(WAVEFORM_FULL_AFTER_QUICK_DELAY_MS) 뒤에 full 추출로
          바꿔치기한다 — 초기 재생 버퍼링과 NAS 경합을 피하려는 원본 간격이다.
          quick 을 건너뛴 파일은 곧바로 full 추출로 간다. */
  useEffect(() => {
    let alive = true;
    let timers: number[] = [];
    const full = row?.fullPath;
    if (!full || !row || !WAVEFORM_EXTS.has(row.fmt.toLowerCase())) {
      setWave(null);
      setWaveFailed(false);
      setWaveLoading(false);
      return;
    }

    /* ⚠ 이미 받아 둔 파형이면 **로딩 표시 없이 그대로 쓴다.**
       예전에는 이 훅이 돌 때마다 setWave(null) + setWaveLoading(true) 부터 했다.
       그래서 방금 들었던 사운드를 다시 고르면 파형이 사라지고 "파형 로딩 중..."
       이 다시 떴다 (사용자 신고 2026-09-03). 피크는 디스크 캐시가 있어도
       왕복이 필요하고, 그 사이 화면이 비어 깜빡였다.
       경로별로 마지막 full 결과를 기억해 두면 되돌아올 때 즉시 보인다.
       메모리 상한: 최근 8개만 유지 (피크 배열이 커서 무제한 보관은 위험). */
    const cached = waveCache.current.get(full);
    if (cached) {
      /* 최근 사용으로 올린다 — 다시 넣으면 Map 삽입 순서 끝으로 간다 (LRU) */
      waveCache.current.delete(full);
      waveCache.current.set(full, cached);
      setWave(cached);
      setWaveFailed(false);
      setWaveLoading(false);
      /* ⚠ 경로가 같아도 **파일이 바뀌었으면** 옛 파형을 계속 보여줘선 안 된다
         (조사 Q15: 다른 도구에서 편집하고 돌아오면 이전 모양·구간이 남았다).
         원본은 디스크 캐시 이름에 크기·수정시각이 들어가 자동으로 걸러진다.
         여기서는 먼저 있는 파형을 그대로 보여 준 뒤(깜빡임 금지 규칙) 도장을
         확인하고, 달라졌을 때만 캐시를 버리고 이 훅을 다시 돌린다. */
      void fileStamp(full).then((stamp) => {
        if (!alive || !stamp) return;
        const known = waveStamp.current.get(full);
        if (!known || known === stamp) return;
        waveCacheBytes.current -= wavePeaksBytes(cached);
        waveCache.current.delete(full);
        waveStamp.current.delete(full);
        setWaveReload((value) => value + 1);
      });
      return;
    }
    setWave(null);
    setWaveFailed(false);
    /* 파형이 아직 없으면 **즉시** "파형 로딩 중..." 을 띄운다 (사용자 지시
       2026-09-07: "파형이 바로 안뜨면 즉시 파형로딩중이 떠야지, 빈창으로 있다가
       일정시간 지나고 뜨면 안 된다").
       ⚠ 예전에 지연(600ms)을 뒀다가 **문구가 뜰 때까지 빈 창**이 되는 이 문제를
         만들었다. 지연을 다시 넣지 말 것.
       여기 도달했다는 것은 메모리 캐시에 없다는 뜻이다 — 위에서 캐시 적중이면
       로딩 상태 없이 곧바로 return 한다. 그래서 같은 파일 재생에는 문구가 안 뜬다.
       ⚠ 문구를 켜는 곳은 **여기 한 곳뿐**이어야 한다. 예전엔 타이머가 뒤늦게
         켜서, 파형이 보이는데 "파형 로딩 중" 으로 바뀌고 끌 주체가 없어 정지
         상태에서도 계속 떠 있었다 (사용자 신고 2026-09-04). 지연 타이머 금지. */
    setWaveLoading(true);
    const finishLoading = () => setWaveLoading(false);

    const remember = (got: WavePeaks | null) => {
      if (!got) return;
      const prev = waveCache.current.get(full);
      if (prev) waveCacheBytes.current -= wavePeaksBytes(prev);
      waveCache.current.set(full, got);
      waveCacheBytes.current += wavePeaksBytes(got);
      /* 이 파형이 어느 시점의 파일인지 기록해 둔다 (위 재사용 검사용) */
      void fileStamp(full).then((stamp) => { if (stamp) waveStamp.current.set(full, stamp); });
      /* 넘치면 오래 쓰지 않은 것부터 버린다 (Map 은 삽입 순서를 지킨다) */
      while (waveCacheBytes.current > WAVE_CACHE_MAX_BYTES && waveCache.current.size > 1) {
        const oldest = waveCache.current.keys().next().value;
        if (oldest === undefined) break;
        const dropped = waveCache.current.get(oldest);
        if (dropped) waveCacheBytes.current -= wavePeaksBytes(dropped);
        waveCache.current.delete(oldest);
      }
    };

    const startFull = () => {
      void loadWavePeaks(full).then((got) => {
        if (!alive) return;
        remember(got);
        setWave(got);
        setWaveFailed(!got);
        finishLoading();
      });
    };

    /* 원본 _schedule_wave_load (player_widget.py:4382):
       지연은 **파일 크기 기준**이다 — WAVEFORM_IMMEDIATE_MAX_BYTES(100MB) 미만이면
       지연 0 (즉시 로드), 그 이상이거나 크기를 모르면 WAVEFORM_LOAD_DELAY_MS(650).
       PoC 는 모든 파일에 650ms 를 걸어 작은 파일도 파형이 늦게 떴다. */
    const IMMEDIATE_MAX_BYTES = 100 * 1024 * 1024;
    const size = row.size > 0 ? row.size : null;
    const delay = size !== null && size < IMMEDIATE_MAX_BYTES ? 0 : 650;

    timers.push(window.setTimeout(() => {
      void loadQuickPeaks(full).then((quick) => {
        if (!alive) return;
        if (quick) {
          setWave(quick);
          finishLoading();
          timers.push(window.setTimeout(startFull, 1800));
        } else {
          startFull();
        }
      });
    }, delay));

    return () => {
      alive = false;
      timers.forEach((id) => window.clearTimeout(id));
      timers = [];
      /* 원본은 파일 전환 시 이전 WaveformRunnable.cancel(). 대용량/NAS 파일의
         full 추출이 새 파일 파형을 직렬로 가로막지 않도록 같은 시점에 중단한다. */
      void cancelWaveform();
    };
  }, [row?.fullPath, row?.fmt, waveReload]);

  /* config 는 비동기로 읽힌다 — 값이 도착하면 반영한다 (원본은 시작 시 이미 반영된 상태).
     ⚠ **최초 한 번만** 반영해야 한다. 볼륨·배속은 아래에서 바뀔 때마다 설정에
       저장(onPersist)하고, 그 설정이 다시 initialVolPos/initialSpeedRate 로 내려온다.
       매번 반영하면  값 변경 → 저장 → config 갱신 → 값 다시 설정  이 서로를 물고
       도는 **양방향 고리**가 된다. 두 값이 딱 떨어지면 React 가 알아서 멈추지만,
       드래그 중에 우클릭(볼륨 초기화 750)처럼 다른 경로가 끼어들면 두 값이 서로를
       밀어내며 무한 루프가 되어 **앱 화면이 통째로 죽었다**
       (사용자 신고 2026-09-14, 설치본 콘솔: React error #185 = 갱신 깊이 초과).
       한 번 받은 뒤로는 이 컴포넌트가 값의 주인이다. */
  const volInitRef = useRef(false);
  useEffect(() => {
    if (volInitRef.current || initialVolPos === undefined) return;
    volInitRef.current = true;
    setVolPos(initialVolPos);
  }, [initialVolPos]);
  const speedInitRef = useRef(false);
  useEffect(() => {
    if (speedInitRef.current) return;
    if (initialSpeedRate === undefined || !(initialSpeedRate > 0)) return;
    speedInitRef.current = true;
    setSpeedPos(Math.round(Math.log2(initialSpeedRate) * 100));
  }, [initialSpeedRate]);

  const currentOverride = row?.fullPath
    ? getOverride(layoutData, row.fullPath, wave?.channels ?? row?.ch ?? 0) : "";
  /* 원본 _layout_overrides.scope — "file" | "folder" | "" */
  const overrideKind = row?.fullPath
    ? overrideScope(layoutData, row.fullPath, wave?.channels ?? row?.ch ?? 0) : "";

  useEffect(() => {
    let alive = true;
    setLayoutInfo(null);
    const full = row?.fullPath;
    const ch = wave?.channels ?? row?.ch ?? 0;
    if (!full || ch <= 2) return;   // 원본도 채널 2 이하면 판정하지 않는다
    if (!layoutReady) return;       // 저장값(파일/폴더 지정)을 읽기 전엔 판정하지 않는다 — 위 layoutReady 주석
    loadLayoutInfo(full, ch, currentOverride).then((got) => { if (alive) setLayoutInfo(got); });
    return () => { alive = false; };
  }, [row?.fullPath, wave?.channels, currentOverride, layoutReady]);

  /* 원본 _show_layout_required_popup: 판정이 AmbiX/FuMa 둘로만 갈리면 그 파일에서
     한 번만 자동으로 규격 선택 창을 띄운다 (_layout_prompted_paths 로 중복 차단).
     사유 문구는 판정기가 준 것을 그대로 보여준다. */
  const promptedRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    const full = row?.fullPath;
    if (!full || !binaural || !layoutInfo) return;
    if (currentOverride || layoutInfo.can_auto_play) return;
    const cand = layoutInfo.candidates ?? [];
    if (!(cand.length === 2 && cand.includes("ambix") && cand.includes("fuma"))) return;
    if (promptedRef.current.has(full)) return;
    promptedRef.current.add(full);
    setFormatDialog({ reason: layoutInfo.reason || "" });
  }, [row?.fullPath, binaural, layoutInfo, currentOverride]);

  /* 원본 _show_layout_required_popup 의 일반 분기 — 후보가 있는데 {ambix,fuma} 조합이
     아니면 경고창을 파일당 한 번 띄운다 (후보가 아예 없으면 띄우지 않는다). */
  useEffect(() => {
    const full = row?.fullPath;
    if (!full || !binaural || !layoutInfo) return;
    if (currentOverride || layoutInfo.can_auto_play) return;
    const cand = layoutInfo.candidates ?? [];
    if (!cand.length) return;
    if (cand.length === 2 && cand.includes("ambix") && cand.includes("fuma")) return;
    if (promptedRef.current.has(full)) return;
    promptedRef.current.add(full);
    setRequiredDialog({ reason: layoutInfo.reason || "" });
  }, [row?.fullPath, binaural, layoutInfo, currentOverride]);

  /* ── 채널 순서 미확인 안내 (BINAURAL_CHANNEL_ORDER_SPEC.md 3절) ────────────
     파일 안에 채널 이름표(iXML TRACK_LIST)가 있으면 순서를 **안다**. 없으면 마스크나
     채널 수로 규격 기본값을 **가정**할 뿐이고, 그 가정이 틀린 파일이 실제로 많다
     (6ch 869개, 5ch 574개가 마스크를 달고도 film 순서였다).
     그래서 판정 근거가 ixml/manual 이 아니면 사용자에게 확인을 받는다.
     ⚠ 대상이 약 5,900개라 파일마다 물으면 못 쓴다 → **폴더당 한 번**만 묻는다.
     ⚠ 묻는 동안에도 재생은 기본값으로 계속된다 (스테레오로 떨어뜨리지 않는다). */
  const orderAskedRef = useRef<Set<string>>(new Set());
  const orderUnverified = Boolean(
    binaural && layoutInfo && !currentOverride
    && layoutInfo.can_auto_play
    && layoutInfo.applied_topology === "surround"
    /* 순서가 갈리는 프리셋일 때만 — quad·6.1 은 film 규약이 없어 물을 게 없다 */
    && (layoutInfo.order_choices?.length ?? 0) > 1
    && !["ixml", "manual"].includes(layoutInfo.applied_source || ""));

  useEffect(() => {
    const full = row?.fullPath;
    if (!full || !orderUnverified || compareOpen) return;
    /* 채널 수는 이 파일의 다른 곳과 **같은 출처**를 써야 한다. layoutInfo 는 비동기라
       파일을 바꾼 직후 잠깐 이전 파일 값이고, 그 값으로 폴더 칸을 잡으면 조회 때
       쓰는 칸과 어긋나 사용자의 선택이 조용히 무시된다. */
    const key = folderScopeKey(full, wave?.channels ?? row?.ch ?? 0);
    if (orderAskedRef.current.has(key)) return;
    orderAskedRef.current.add(key);
    setCompareOpen(true);
  }, [row?.fullPath, orderUnverified, compareOpen, wave?.channels, row?.ch]);

  /* 단축키 트리거 — 원본 player.toggle_loop / toggle_segments / seek_to_start.
     ⚠ "첫 실행이면 무시" 방식은 개발 모드(StrictMode)에서 효과가 **두 번** 실행될 때
     두 번째를 막지 못한다 → 앱을 켜면 반복재생이 저절로 켜지고 세그먼트도 토글됐다
     (사용자 보고: "반복재생을 끄는데도 켤 때마다 다시 켜져").
     카운터 **값이 실제로 변했을 때만** 동작하도록 바꾼다. */
  const lastLoopCmd = useRef(cmdToggleLoop);
  useEffect(() => {
    if (lastLoopCmd.current === cmdToggleLoop) return;
    lastLoopCmd.current = cmdToggleLoop;
    setLoop((v) => !v);
  }, [cmdToggleLoop]);
  const lastSegCmd = useRef(cmdToggleSegments);
  useEffect(() => {
    if (lastSegCmd.current === cmdToggleSegments) return;
    lastSegCmd.current = cmdToggleSegments;
    setSegmentsVisible((v) => !v);
  }, [cmdToggleSegments]);
  /* 단축키로 바이노럴 토글 — 버튼 클릭과 같은 경로를 쓴다 (구성 요소 미설치면 안내) */
  const lastBinauralCmd = useRef(cmdToggleBinaural);
  useEffect(() => {
    if (lastBinauralCmd.current === cmdToggleBinaural) return;
    lastBinauralCmd.current = cmdToggleBinaural;
    if (binauralAvailable === false) {
      /* 설치 문제일 때만 안내창 (Q02) — 그 외 사유는 툴팁에 이미 나온다 */
      if (isIemInstallIssue(binauralUnavailableReason)) setIemOpen(true);
      return;
    }
    setBinaural((v) => !v);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cmdToggleBinaural]);
  const lastSeekCmd = useRef(cmdSeekToStart);
  useEffect(() => {
    if (lastSeekCmd.current === cmdSeekToStart) return;
    lastSeekCmd.current = cmdSeekToStart;
    setPos(0);
    /* ⚠ 재생헤드 상태도 함께 되돌려야 한다. 안 하면 끝까지 재생된 뒤 다시 재생할 때
       표시값이 끝에 남아 있고 "재생 중에는 뒤로 가지 않는다"(max) 규칙에 걸려
       재생바가 끝에 박힌 채 갱신되지 않는다 (실측 증상). 착지 대기도 함께 걸린다. */
    resetPlayhead(0);
    onSeek?.(0);
  }, [cmdSeekToStart, onSeek]);

  /* 원본 세그먼트는 **이미 0..1 비율**이다 — _segments_from_envelope 가 마지막에
     `/ np.float32(nf)` 로 나눠서 반환한다 (player_widget.py:412).
     PoC 가 이걸 "초 단위"로 착각해 duration 으로 한 번 더 나눠서, 10초 파일이면
     세그먼트가 앞 10% 에 몰리고 나머지가 텅 비어 보였다 (사용자 보고).
     원본 _has_visible_segments: 토글 ON + 세그먼트 1개 이상이면 헤더 표시
     (단일 세그먼트도 노출해 ON/OFF 가 구분되게 한다). */
  const segRatios: Array<[number, number]> = (wave?.segments ?? []).map(
    ([a, b]) => [Math.max(0, Math.min(1, a)), Math.max(0, Math.min(1, b))] as [number, number]);

  const speed = speedFromPos(speedPos);
  const volLabel = fmtDb(volPos);
  speedRef.current = speed;
  /* ⚠ 의존성에서 콜백(onRate/onVolume/onBinaural)을 **뺀다.**
     App 이 이 콜백들을 인라인 화살표로 넘기므로 **매 렌더마다 새 객체**가 된다.
     의존성에 넣어두면 값이 그대로여도 렌더마다 오디오 사이드카로 명령이 나간다.
     인덱싱 중에는 진행 이벤트가 300ms 주기로 오면서 App 이 계속 다시 그려져
     **초당 10개 명령**이 쏟아졌다 (실측 2026-09-07: rate/volume/binaural 3종이
     300ms 간격 무한 반복, poc_audio.log 가 9.17MB 까지 부풀고 binaural 은
     매번 no-media 로 거부 + WARNING 기록. 인덱싱 중 클릭하면 먹통 신고의 원인).
     보내야 하는 시점은 **값이 바뀔 때**뿐이다. 콜백을 다시 넣지 말 것. */
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { onRate?.(speed); }, [speed]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { onVolume?.(volPosToGain(volPos)); }, [volPos]);
  /* 원본 _actual_save_config 는 볼륨 슬라이더 위치(0~1000)와 배속을 저장한다
     (main_window.py:4981). 값이 바뀔 때마다 상위에 알려 설정 파일에 저장한다. */
  useEffect(() => { onPersist?.({ volumePos1k: volPos, speedRate: speed }); },
            [volPos, speed]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { onBinaural?.(binaural); }, [binaural]);
  /* 판별 중 표시는 **버튼 안에서** 한다 (사용자 지시 2026-09-03).
     예전에는 재생 중 바이노럴을 켜면 소리를 멈추고 중앙 로더를 띄웠다 — 중앙 로더가
     입력을 막으니 소리까지 멈춰야 했던 것인데, 그 흐름 자체가 불쾌했다.
     이제 판별은 버튼 왼쪽 원의 원형 로딩 + "채널 배치 판별 중" 문구로만 알리고
     **재생은 끊지 않는다.** */
  /* ── 출력 라벨/상태등 (원본 _update_output_control, player_widget.py:4271) ──
     문구를 원본과 정확히 맞춘다 (PoC 는 "출력 · 스테레오 · 2ch" 식으로 달랐다):
       파일 없음/채널 0 → "출력 대기"
       1채널            → "모노 1ch"
       2채널            → "스테레오 2ch"
       3채널 이상 + 바이노럴 동작 → "{원본형식} → Binaural 2ch"
       그 외            → 판정되면 "{원본형식} → 스테레오 2ch", 미판정이면 원본형식만
     원본형식 = 판정 프리셋 라벨("4채널 쿼드"→"4ch 쿼드", "3채널 좌·중·우"→"3ch 좌·중·우"),
                판정 불가면 "{채널}ch · 배치 확인 필요"
     indicator: off / bypass / needs-layout / active (원본 state_chip: 오류/선택/켜짐/꺼짐) */
  const chCount = wave?.channels ?? row?.ch ?? 0;
  const resolvedPreset = currentOverride
    || (layoutInfo?.can_auto_play ? layoutInfo.automatic_preset : "");
  /* ⚠ 저장값은 순서까지 담을 수 있다 ("5.1|film"). 라벨을 만들 때는 **프리셋만**
     떼서 넘겨야 한다 (조사 B02) — 통째로 넘기면 출력 형식 자리에 내부 토큰이
     그대로 보인다. 원본도 split_layout_override 로 나눠 쓴다
     (player_widget.py:3257). */
  const resolvedPresetOnly = splitLayoutOverride(resolvedPreset)[0];
  const sourceFormat = (() => {
    if (!resolvedPresetOnly || resolvedPresetOnly === "unknown") return `${chCount}ch · 배치 확인 필요`;
    const label = layoutChoiceLabel(resolvedPresetOnly, chCount);
    return ({ "4채널 쿼드": "4ch 쿼드", "3채널 좌·중·우": "3ch 좌·중·우" } as Record<string, string>)[label] ?? label;
  })();
  /* ⚠ 켜짐 조건(토글·채널수·배속·판정)만으로 '바이노럴로 나가는 중' 을 단정하던
     계산은 없앴다 (조사 B01). 실제 판단은 아래 engineOn(엔진이 보고한 상태) 과
     binauralVisual 로 한다 — 엔진이 실패해 스테레오로 되돌아간 경우를 잡아야 한다. */
  /* ⚠ 판정이 **아직 진행 중**인 동안(layoutInfo 가 오기 전)에는 경고가 아니다.
     예전에는 resolvedPreset 이 비어 있다는 이유로 "채널 배치 확인 필요" 를 띄워,
     판별 중인 파일을 오류처럼 보이게 했다 (사용자 지적). 진행 중 표시는 중앙 로더가
     맡고, 버튼 아래 경고는 판정이 끝난 뒤에만 나온다. */
  /* 사용자 지시(2026-09-02): **바이노럴이 꺼져 있으면 판별 중 표시를 하지 않는다**
     (그때는 배치를 몰라도 일반 스테레오로 그냥 재생하면 되므로 알릴 이유가 없다).
     켜져 있을 때만 중앙 로더로 알린다. */
  const layoutProbing = chCount > 2 && binaural
    && Boolean(row?.fullPath) && layoutInfo === null;
  const needsLayout = chCount > 2 && binaural && !layoutProbing
    && !(resolvedPreset && resolvedPreset !== "unknown");
  /* 원본 show_saved = (경고 상태가 아니고) 사용자 지정이 있을 때 */
  const showSaved = !needsLayout && Boolean(currentOverride);
  /* 5.1·7.1 처럼 LFE 가 있는 배치는 바이노럴에서 LFE 를 빼고 듣는다 — 상세 줄 꼬리표 */
  const lfeTail = layoutInfo?.applied_topology === "surround"
    && layoutInfo.applied_preset.includes(".1") ? " · LFE 제외" : "";
  useEffect(() => {
    onLayoutProbe?.(layoutProbing, row?.name ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layoutProbing, row?.name]);
  /* 원본 format_candidates = 후보가 정확히 {ambix, fuma} 일 때 */
  const formatCandidates = (() => {
    const cand = layoutInfo?.candidates ?? [];
    return cand.length === 2 && cand.includes("ambix") && cand.includes("fuma");
  })();
  /* ⚠ 라벨과 표시등은 **엔진이 실제로 바이노럴을 내보내는 중일 때만**
     "→ Binaural 2ch" 로 쓴다 (조사 B01). 원본 _update_output_control 의 순서를
     그대로 따른다 (player_widget.py:3956):
       파일 없음 → 1ch → 2ch → (엔진 켜짐 & 준비 중) → (엔진 켜짐 & 재생 중) → 그 외
     그 외 갈래의 표시등은 오류 → 배치 필요 → 우회 → 꺼짐 순으로 정한다. */
  const engineOn = engineBinaural && binaural;
  const outputLabel = !row || chCount < 1
    ? "출력 대기"
    : chCount === 1
      ? "모노 1ch"
      : chCount === 2
        ? "스테레오 2ch"
        : engineOn && (binauralVisual === "loading" || binauralVisual === "active")
          ? `${sourceFormat} → Binaural 2ch`
          : (resolvedPresetOnly && resolvedPresetOnly !== "unknown"
              ? `${sourceFormat} → 스테레오 2ch`
              : sourceFormat);
  const indicator = !row || chCount < 1
    ? "off"
    : layoutProbing
      ? "probing"
      : chCount <= 2
        ? (binaural ? "bypass" : "off")
        : engineOn && binauralVisual === "loading"
          ? "loading"
          : engineOn && binauralVisual === "active"
            ? "active"
            : binauralVisual === "error"
              ? "error"
              : (binauralVisual === "needs_layout" || needsLayout)
                ? "needs-layout"
                : (binaural ? "bypass" : "off");

  /* ── 채널 배치 버튼(▾) 툴팁 (원본 _update_binaural_layout_control, 4317) ──
     채널 2 이하 / 파일 없음:
       "바이노럴 입력 채널 배치 선택\n현재 파일에는 배치 설정이 필요하지 않습니다"
     그 외:
       "채널 배치: {모드} · {프리셋}\n{상세}\n클릭하여 변경"
       모드   = 직접(사용자 지정) / 미리듣기(저장 전 청취) / 자동
       프리셋 = 앰비소닉이면 "{차수}차 AmbiX|FuMa", 아니면 프리셋 이름 (불가면 "선택 필요")
       상세   = 판정 사유 → 판정 근거 문구 → "현재 판정: {프리셋}" 순으로 첫 값 */
  const layoutMenuTip = (() => {
    if (!row || chCount <= 2) {
      return "바이노럴 입력 채널 배치 선택\n현재 파일에는 배치 설정이 필요하지 않습니다";
    }
    const mode = currentOverride ? "직접" : previewPreset ? "미리듣기" : "자동";
    const applied = layoutInfo?.applied_preset ?? "";
    const preset = layoutInfo?.applied_topology === "ambisonic"
      ? `${layoutInfo?.applied_order ?? 1}차 ${applied === "fuma" ? "FuMa" : "AmbiX"}`
      : (applied && applied !== "unknown" ? applied : "선택 필요");
    const sourceDetail = ({
      ixml: "파일 내부 채널 정보로 자동 배치",
      mask: "WAV 채널 정보로 자동 배치",
      filename: "파일명 정보로 자동 배치",
      channels: "채널 수의 기본 배치 사용",
      manual: "사용자가 저장한 배치 사용",
    } as Record<string, string>)[layoutInfo?.applied_source ?? ""] ?? "";
    const detail = layoutInfo?.applied_reason || sourceDetail || `현재 판정: ${preset}`;
    return `채널 배치: ${mode} · ${preset}\n${detail}\n클릭하여 변경`;
  })();
  const chipText = indicator === "probing" ? "판별"
    : indicator === "loading" ? "준비"
    : indicator === "error" ? "오류"
    : indicator === "needs-layout" ? "선택" : binaural ? "켜짐" : "꺼짐";
  /* 뱃지 색 (사용자 지시 2026-09-08) — 꺼짐 빨강 / 켜짐 초록.
     판별·선택·준비는 아직 결정된 상태가 아니라 무채색 그대로 둔다.
     오류는 실제로 바이노럴이 안 나가는 상태라 꺼짐과 같은 빨강이다 (조사 B01). */
  const chipTone = indicator === "probing" || indicator === "needs-layout"
    || indicator === "loading"
    ? "" : (indicator === "error" || !binaural) ? " off" : " on";

  /* ── 바이노럴 버튼 툴팁 (원본 _binaural_policy, player_widget.py:4226) ──
     위에서부터 먼저 걸리는 것 하나만 쓴다 (원본 조건 순서 그대로):
       구성 요소 없음         → "바이노럴 처리 구성 요소가 설치되지 않았습니다"
       꺼짐                   → "바이노럴 모니터링 꺼짐 · 클릭하여 켭니다"
       파일 없음              → "… 켜짐 · 멀티채널 파일을 기다리는 중입니다"
       채널 2 이하            → "… 켜짐 · 모노·스테레오는 변환 없이 재생합니다"
       WAV 아님               → "… 켜짐 · 현재 형식은 변환 없이 재생합니다"
       1.0배속 아님           → "… 켜짐 · 1.0배속이 아니어서 변환 없이 재생합니다"
       판정 불가 + ambix/fuma → "… 켜짐 · 오른쪽 화살표에서 앰비소닉 규격을 선택하세요"
       판정 불가              → "… 켜짐 · 오른쪽 화살표에서 채널 배치를 선택하세요"
       그 외                  → "바이노럴 모니터링 켜짐 · 클릭하여 끕니다" */
  const binauralTip = (() => {
    /* 원본 _binaural_policy 끝 (player_widget.py:3950): 준비/재생/오류 상태이고
       엔진이 보낸 상태 문구가 있으면 **그 문구를 그대로** 툴팁으로 쓴다. */
    if (binauralStatus
        && (binauralVisual === "loading" || binauralVisual === "active"
            || binauralVisual === "error")) return binauralStatus;
    if (binauralAvailable === false)
      return binauralUnavailableReason || "바이노럴 처리 구성 요소가 설치되지 않았습니다";
    if (!binaural) return "바이노럴 모니터링 꺼짐 · 클릭하여 켭니다";
    if (!row) return "바이노럴 모니터링 켜짐 · 멀티채널 파일을 기다리는 중입니다";
    if (chCount <= 2) return "바이노럴 모니터링 켜짐 · 모노·스테레오는 변환 없이 재생합니다";
    if (!/\.wav$/i.test(row.name))
      return "바이노럴 모니터링 켜짐 · 현재 형식은 변환 없이 재생합니다";
    if (Math.abs(speed - 1) >= 0.005)
      return "바이노럴 모니터링 켜짐 · 1.0배속이 아니어서 변환 없이 재생합니다";
    if (!(resolvedPreset && resolvedPreset !== "unknown")) {
      return formatCandidates
        ? "바이노럴 모니터링 켜짐 · 오른쪽 화살표에서 앰비소닉 규격을 선택하세요"
        : "바이노럴 모니터링 켜짐 · 오른쪽 화살표에서 채널 배치를 선택하세요";
    }
    return "바이노럴 모니터링 켜짐 · 클릭하여 끕니다";
  })();
  const wide = playerWidth >= 1280;
  const narrowInfo = playerWidth < 820;

  const monitoringControls = (pushRight: boolean) => (
    <>
      <div className="split-output">
        {operationError && <div role="alert" className="binaural-warn">
          {operationError} <button className="btn" onClick={() => setOperationError("")}>닫기</button>
        </div>}
        <div className="binaural-row">
        <button className={"binaural-main" + (binaural ? " on" : "")
                  + (indicator === "needs-layout" ? " needs-layout" : "")
                  + (indicator === "probing" || indicator === "loading" ? " probing" : "")
                  + (indicator === "error" ? " error" : "")
                  + (indicator === "active" ? " active" : "")}
                data-tip={binauralTip}
                onClick={() => {
                  if (binauralAvailable === false) {
                    /* 설치 문제일 때만 안내창 (Q02) */
                    if (isIemInstallIssue(binauralUnavailableReason)) setIemOpen(true);
                    return;
                  }
                  setBinaural((v) => !v);
                }}>
          {/* 판별 중에는 점 자리에 **아크 스피너**를 놓는다.
              예전엔 점의 테두리 한쪽만 색을 줘 돌렸는데, 기본(무채색) 테마의
              --accent 가 #9a9a9a 회색이라 9px 링이 사실상 보이지 않았다 (사용자 신고).
              로딩 표현은 스플래시 진행선·중앙 로더처럼 **파랑 고정**으로 두고,
              회전 + 호 길이 변화(dash)를 겹쳐 확실히 보이게 한다. */}
          {(indicator === "probing" || indicator === "loading") ? (
            <span className="state-probe" aria-hidden="true">
              <svg viewBox="0 0 24 24">
                <circle className="probe-track" cx="12" cy="12" r="9" />
                <circle className="probe-arc" cx="12" cy="12" r="9" />
              </svg>
            </span>
          ) : (
            <span className="state-dot" />
          )}
          <span className={"state-chip" + chipTone}>{chipText}</span>
          <span className="binaural-label">
            {indicator === "probing" ? "채널 배치 판별 중" : outputLabel}
          </span>
        </button>
        <button className="binaural-menu" data-tip={layoutMenuTip}
                aria-label="바이노럴 입력 채널 배치 선택"
                onMouseDown={(event) => event.stopPropagation()}
                onClick={(event) => {
                  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
                  setLayoutMenuAt((prev) => (prev ? null : rect));
                }}>
          <IcoDown size={14} />
        </button>
        </div>

        {/* ── 상세 줄 (원본 _set_layout_warning_visible, player_widget.py:3822) ──
            두 가지 상태만 있고, 둘 다 아니면 줄 자체가 숨는다:
              ① 확인 필요 : 후보가 {ambix,fuma} 뿐이면 "앰비소닉 규격 확인 필요",
                            그 외에는 "채널 배치 확인 필요" — 가운데 정렬, #e45b64 8pt 700
                            툴팁도 각각 다르다. **초기화 버튼은 나오지 않는다.**
              ② 저장됨   : "{폴더 설정|개별 저장됨} · {배치 라벨}" — 오른쪽 정렬,
                            #9fc4ff 8pt 700, 초기화 버튼이 함께 나온다.
            (PoC 는 ①에서 초기화를 함께 보여주고 ②가 아예 없었다 — 원본과 달랐다.) */}
        {/* 2026-09-16: 판정 근거·LFE 줄을 따로 두었더니 두 줄이 꽉 껴 보였다 (사용자 지적)
            → 한 줄로 합친다. 우선순위 ① 확인 필요(빨강) ② 저장됨(파랑+초기화) ③ 판정 근거(회색).
            "모니터링 설정 (원본 파일 유지)" 는 툴팁으로 내린다. */}
        {(needsLayout || showSaved || (binaural && layoutInfo)) && (
        <div className={"binaural-detail" + (showSaved ? " saved" : "")}>
          <span className={"binaural-warn" + (showSaved ? " saved" : needsLayout ? "" : " info")}
                data-tip={(needsLayout
                  ? (formatCandidates
                      ? "오른쪽 화살표를 눌러 AmbiX와 FuMa를 번갈아 들어보세요"
                      : "오른쪽 화살표를 눌러 가능한 채널 배치 중 하나를 선택하세요")
                  : showSaved
                  ? (overrideKind === "folder"
                      ? "현재 폴더에 저장한 채널 배치입니다"
                      : "자동 판정 결과를 덮어써 이 파일에 직접 저장한 채널 배치입니다")
                  : "자동 판정 근거입니다")
                  + " · 바이노럴 모니터링 설정 (원본 파일은 바꾸지 않음)"}>
            {needsLayout
              ? (formatCandidates ? "앰비소닉 규격 확인 필요" : "채널 배치 확인 필요")
              : showSaved
              ? `${overrideKind === "folder" ? "폴더 설정" : "개별 저장됨"} · `
                + layoutChoiceLabel(currentOverride.split("|")[0],
                                    wave?.channels ?? row?.ch ?? 0)
                + lfeTail
              : (({ ixml: "파일 내부 채널 정보", mask: "WAV 채널 마스크", manual: "사용자 지정",
                   filename: "파일명 추정", channels: "채널 수 추정" } as Record<string, string>)
                   [layoutInfo?.applied_source ?? ""] || "배치 확인 필요") + lfeTail}
          </span>
          {showSaved && (
            <button className="binaural-reset"
                    data-tip="이 파일에 저장된 채널 배치 선택을 지우고 다시 판정합니다"
                    onClick={async () => {
                      if (!row?.fullPath) return;
                      const ch = wave?.channels ?? row?.ch ?? 0;
                      /* ⚠ **지금 유효한 지정 하나만** 지운다 (조사 B06).
                         원본 _reset_saved_binaural_layout (player_widget.py:3674) 은
                         범위가 폴더면 그 폴더·그 채널 설정만, 아니면 이 파일 지정만
                         지운다. PoC 는 둘 다 지워서, 파일 하나를 초기화했는데 같은
                         폴더의 같은 채널 파일들까지 설정이 날아갔다. */
                      if (!await putLayout(overrideKind === "folder"
                        ? setFolderOverride(layoutData, row.fullPath, ch, "")
                        : setFileOverride(layoutData, row.fullPath, ""))) return;
                      /* 안내를 다시 띄울 수 있게 '이미 물어봤다' 기록도 지운다
                         (원본 _layout_prompted_paths.discard / _layout_required_paths.discard).
                         미리듣기 중이던 임시 배치도 함께 비운다. */
                      promptedRef.current.delete(row.fullPath);
                      setPreviewPreset("");
                      void audioBridge.layout("", false, playing);
                    }}>
              초기화
            </button>
          )}
        </div>
        )}
      </div>

      {pushRight && <div className="monitoring-spacer" />}

      <div className="control-group speed">
        <span className="caps">SPD</span>
        <input className="slider" data-tip="재생속도 0.5x ~ 2.0x (1.0x 중앙, 더블클릭으로 직접입력)"
               type="range" min={-100} max={100} step={1} value={speedPos}
               /* 원본 QSlider::sub-page — 손잡이 왼쪽이 accent 로 채워진다 */
               style={{ ["--fill" as string]: `${((speedPos + 100) / 200) * 100}%` }}
               onContextMenu={(e) => { e.preventDefault(); setSpeedPos(0); }}
               onClick={(e) => { if (e.ctrlKey) setSpeedPos(0); }}
               onWheel={(e) => { e.preventDefault(); setSpeedPos((v) => Math.max(-100, Math.min(100, v + (e.deltaY < 0 ? 1 : -1)))); }}
               onChange={(e) => setSpeedPos(+e.target.value)} />
        <EditableValue
          className={"speed-value" + (Math.abs(speed - 1) > 0.005 ? " changed" : "")}
          title="더블클릭으로 직접 입력 (예: 1.5)"
          value={fmtSpeed(speed)}
          onCommit={(text) => {
            const next = speedPosFromText(text);
            if (next !== null) setSpeedPos(next);
          }}
        />
      </div>
      <div className="control-group volume">
        <span className="caps">VOL</span>
        <IcoVolume size={15} />
        <input className="slider" data-tip={"볼륨 (dB) — 0 dB=유니티, 최대 +6.02 dB, 최소 −∞\n우클릭/Ctrl+클릭: 0 dB로 리셋"}
               type="range" min={0} max={1000} step={1} value={volPos}
               /* 원본 #volumeFader::sub-page — accent_teal → accent(0.62) → accent_warm */
               style={{ ["--fill" as string]: `${(volPos / 1000) * 100}%` }}
               onContextMenu={(e) => { e.preventDefault(); setVolPos(750); }}
               onClick={(e) => { if (e.ctrlKey) setVolPos(750); }}
               onWheel={(e) => {
                 e.preventDefault();
                 const current = volPosToDb(volPos);
                 const step = e.deltaY < 0 ? VOL_WHEEL_DB_STEP : -VOL_WHEEL_DB_STEP;
                 const nextDb = current === -Infinity
                   ? (step > 0 ? VOL_FLOOR_DB : current)
                   : Math.max(-Infinity, Math.round((current + step) / VOL_WHEEL_DB_STEP) * VOL_WHEEL_DB_STEP);
                 setVolPos(volDbToPos(nextDb < VOL_FLOOR_DB ? -Infinity : nextDb));
               }}
               onChange={(e) => setVolPos(+e.target.value)} />
        <EditableValue
          className="vol-value"
          title="더블클릭으로 dB 직접 입력 (예: 0, -6, +3.5, -inf)"
          value={volLabel}
          onCommit={(text) => {
            const db = parseDbText(text);
            if (db !== null) setVolPos(volDbToPos(db));
          }}
        />
      </div>
      {layoutMenuAt && (
        <BinauralMenu
          anchor={layoutMenuAt}
          path={row?.fullPath ?? ""}
          channels={wave?.channels ?? row?.ch ?? 0}
          override={currentOverride}
          onPick={async (preset) => {
            if (!row?.fullPath) return;
            /* 원본 _apply_binaural_layout: 빈 값이면 파일 지정을 지운다.
               "자동" 을 골랐을 때 폴더 지정까지 지우지는 않는다 (원본 set 과 동일). */
            if (!await putLayout(setFileOverride(layoutData, row.fullPath, preset))) return;
            void audioBridge.layout(preset, false, playing);
          }}
          onApplyToFolder={async (preset) => {
            if (!row?.fullPath) return;
            /* 원본 _apply_layout_to_current_folder — 같은 폴더의 같은 채널 수 파일에 적용 */
            if (!await putLayout(setFolderOverride(setFileOverride(layoutData, row.fullPath, ""), row.fullPath,
                                        wave?.channels ?? row?.ch ?? 0, preset))) return;
            void audioBridge.layout(preset, false, playing);
          }}
          onCompare={() => setCompareOpen(true)}
          onClose={() => setLayoutMenuAt(null)}
        />
      )}
      <button className={"tbtn segment" + (segmentsVisible ? " on" : "")}
              data-tip="세그먼트 표시/숨기기 (S)"
              aria-label="세그먼트 표시/숨기기"
              onClick={() => setSegmentsVisible((v) => !v)}>
        <IcoSegment size={16} />
      </button>

      {/* ── 채널 배치 확인용 비모달 창들 (원본 _AmbisonicFormatDialog /
             _ChannelOrderDialog / IEM 설치 안내) ── */}
      {compareOpen && row?.fullPath && (
        <ChannelOrderDialog key={row.fullPath}
          fileName={row.name}
          preset={(currentOverride || resolvedPreset).split("|")[0]}
          /* 원본: 저장된 순서 → **판정된 순서** → 없으면 "wave".
             판정값을 빼먹으면 iXML/마스크로 film 이 확정된 파일에서 A/B 기준이 뒤바뀐다. */
          currentOrder={currentOverride.split("|")[1]
            || layoutInfo?.applied_channel_order
            || layoutInfo?.automatic_channel_order
            || "wave"}
          choices={layoutInfo?.order_choices ?? []}
          unverified={orderUnverified}
          folderName={folderDisplayName(row.fullPath)}
          onPreview={(value) => {
            /* ⚠ 원본은 **바이노럴 토글이 켜져 있을 때만** 재로드하고 강제 재생한다
               (player_widget.py:3559). 꺼져 있는데 재생이 시작되면 스테레오로 A·B 를
               비교하게 되어 비교 자체가 의미가 없다 (조사 Q03).
               꺼져 있을 때는 판정만 미리듣기 값으로 바꿔 둔다. */
            setPreviewPreset(value);
            if (!binaural) { void audioBridge.layout(value, false, false); return; }
            onForcePlay?.();
            void audioBridge.layout(value, false, true);
          }}
          onSave={async (value, scope) => {
            if (!await putLayout(scope === "folder"
              ? setFolderOverride(setFileOverride(layoutData, row.fullPath!, ""), row.fullPath!,
                                  wave?.channels ?? row?.ch ?? 0, value)
              : setFileOverride(layoutData, row.fullPath!, value, true))) return;
            void audioBridge.layout(value, false, playing);
            setPreviewPreset("");
            setCompareOpen(false);
          }}
          onClose={() => {
            if (previewPreset) void audioBridge.layout(currentOverride, false, playing);
            /* "나중에" — 이번 실행 동안 이 폴더는 다시 묻지 않는다. 저장은 하지 않으므로
               다음 실행에서는 또 묻는다 (틀린 순서를 영구히 굳히지 않기 위함). */
            if (orderUnverified && row.fullPath) {
              orderAskedRef.current.add(
                folderScopeKey(row.fullPath, wave?.channels ?? row?.ch ?? 0));
            }
            setPreviewPreset(""); setCompareOpen(false);
          }}
        />
      )}
      {formatDialog && row?.fullPath && (
        <AmbisonicFormatDialog
          fileName={row.name}
          reason={formatDialog.reason}
          onPreview={(value) => {
            /* ⚠ 원본은 **바이노럴 토글이 켜져 있을 때만** 재로드하고 강제 재생한다
               (player_widget.py:3559). 꺼져 있는데 재생이 시작되면 스테레오로 A·B 를
               비교하게 되어 비교 자체가 의미가 없다 (조사 Q03).
               꺼져 있을 때는 판정만 미리듣기 값으로 바꿔 둔다. */
            setPreviewPreset(value);
            if (!binaural) { void audioBridge.layout(value, false, false); return; }
            setPos(0); resetPlayhead(0); onForcePlay?.();
            void audioBridge.layout(value, true, true);
          }}
          onSave={async (value) => {
            if (!await putLayout(setFileOverride(layoutData, row.fullPath!, value, true))) return;
            void audioBridge.layout(value, false, playing);
            setPreviewPreset("");
            setFormatDialog(null);
          }}
          onClose={() => {
            if (previewPreset) void audioBridge.layout(currentOverride, false, playing);
            setPreviewPreset(""); setFormatDialog(null);
          }}
        />
      )}
      {requiredDialog && row && (
        <LayoutRequiredDialog
          fileName={row.name}
          reason={requiredDialog.reason}
          /* 원본: 9채널이면 고정 두 줄, 그 밖에는 후보 라벨(정렬 순서) 목록 */
          choices={chCount === 9
            ? ["7.0.2: 일곱 개 평면 스피커와 두 개 높이 채널로 제작됐을 때 선택",
               "2차 AmbiX: ACN/SN3D 9채널 앰비소닉 파일일 때 선택"]
            : [...(layoutInfo?.candidates ?? [])].sort()
                .map((preset) => layoutChoiceLabel(preset, chCount))}
          onSelect={() => {
            setRequiredDialog(null);
            /* 원본은 [채널 배치 선택] 을 누르면 배치 메뉴를 연다 */
            const button = document.querySelector(".binaural-menu");
            if (button) setLayoutMenuAt(button.getBoundingClientRect());
          }}
          onClose={() => setRequiredDialog(null)}
        />
      )}
      {iemOpen && (
        <IemInstallDialog
          /* 실제 사유도 함께 — 미설치와 버전 불일치를 구분해 읽을 수 있다 (Q02) */
          reason={binauralUnavailableReason}
          /* 원본 app/binaural.py:IEM_SUITE_VERSION */
          version="1.15.0"
          /* 원본 app/binaural.py:IEM_SUITE_DOWNLOAD_URL */
          onOpenPage={() => {
            void openExternal("https://plugins.iem.at/download/");
            setIemOpen(false);
          }}
          onClose={() => setIemOpen(false)}
        />
      )}
    </>
  );

  return (
    <div className={"player " + (wide ? "controls-wide" : playerWidth < 920 ? "controls-compact" : "controls-two-row")} ref={playerRef}>
      <div className="ruler">
        {ticks().map((t, i) => (
          <div key={i} className="ruler-tick" style={{ left: t.x + "%" }}>{t.label}</div>
        ))}
      </div>

      {/* 원본 is_header_visible: 토글 ON + 세그먼트 1개 이상일 때만 헤더 영역을 뺀다 */}
      <div className={"wave-wrap" + (segmentsVisible && segRatios.length > 0 ? " segments-on" : "")} ref={wrapRef} onWheel={onWheel}
           onContextMenu={(e) => { e.preventDefault(); setSel(null); }}
           /* ── 파형 마우스 (원본 WaveformView.mousePressEvent/Move/Release, 2268) ──
              누른 지점이 기존 선택 영역 **안**이면 "extdrag" — 임계값(6px)을 넘으면
              그 영역을 잘라 DAW 로 끌어낸다 (dragRegionRequested). 밖이면 "select" —
              임계값을 넘으면 새 영역을 만들고, 못 넘고 놓으면 그 지점으로 seek 한다.
              ⚠ 임계값은 **화면 픽셀**이다: delta_px = |r - anchor| * w.
                 (가로 줌 폐지 전에는 zoom_factor 보정이 필요했다.) */
           onMouseDown={(e) => {
             if (e.button === 2) return;
             if (!row) return;
             const start = seekFromEvent(e);
             const insideSel = Boolean(sel && sel[0] <= start && start <= sel[1]);
             let moved = false;
             const move = (ev: MouseEvent) => {
               if (moved) return;
               const el = wrapRef.current!;
               const r = el.getBoundingClientRect();
               const x = Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width));
               const t = x;
               const deltaPx = Math.abs(t - start) * r.width;
               if (deltaPx < DRAG_THRESHOLD_PX) return;
               moved = true;
               if (insideSel && sel) {
                 /* 원본 extdrag — 선택 영역을 잘라 외부로 드래그 */
                 dragRegion(sel[0], sel[1]);
               } else {
                 setSel([Math.min(start, t), Math.max(start, t)]);
                 /* 새 영역은 놓을 때까지 계속 늘어난다 */
                 moved = false;
                 dragging.current = true;
               }
             };
             const up = () => {
               window.removeEventListener("mousemove", move);
               window.removeEventListener("mouseup", up);
               dragging.current = false;
               /* ⚠ 조건이 `!selRef.current`(선택이 아예 없을 때) 였다. 그래서 선택
                  영역을 한 번 만들면 **파형 클릭으로 플레이헤드를 옮길 수 없었다**
                  (사용자 신고 2026-09-04). 원본 기준은 "선택 **밖**을 눌렀는가" 다 —
                  선택 안을 누르면 extdrag(영역 내보내기), 밖을 누르면 select 모드이고
                  임계값을 못 넘고 놓으면 그 지점으로 seek 한다.
                  선택은 지우지 않는다 — 사용자가 만든 영역을 클릭 한 번에 날리면 안 된다. */
               /* 사용자 지시 2026-09-04: 선택 영역 **안쪽**을 클릭해도 플레이헤드가
                  옮겨져야 한다. 끌었으면(moved) 영역 내보내기로 처리되므로,
                  '끌지 않았다' 만 보면 된다. 선택 영역은 지우지 않는다. */
               if (!moved) {
                 setPos(start);
                 resetPlayhead(start);
                 onSeek?.(start * durMsNow());
               }
             };
             window.addEventListener("mousemove", move);
             window.addEventListener("mouseup", up);
           }}>
        {row ? (
          <>
            {!WAVEFORM_EXTS.has(row.fmt.toLowerCase())
              ? <div className="wave-empty">이 포맷은 파형 미지원 (재생만 가능)</div>
              : waveLoading
                /* 원본 paintEvent 의 _loading 분기 — 배경만 깔고 가운데 문구.
                   피크가 오기 전에 아무 파형이나 그리면 파일과 무관한 그림이 된다. */
                ? <div className="wave-loading">파형 로딩 중...</div>
                : waveFailed
                  ? <div className="wave-empty">파형 추출 실패</div>
                : <Waveform
                            /* ⚠ 예전에는 `Math.min(..., 4)` 로 레인을 4개로 묶어놓았다.
                               그래서 5.1(6채널) 파일이 4레인만 보여졌다 (사용자 신고).
                               원본은 상한 없이 실제 채널 수만큼 그린다. */
                            channels={wave?.channels ?? row.ch}
                            ampZoom={amp}
                            duration={row.dur} sampleRate={wave?.sampleRate || row.sr}
                            peaks={wave} />}
            {segmentsVisible && segRatios.length > 0 && (
              <div className="segment-header">
                {segRatios.map(([a, b], i) => (
                  <div key={i}
                       className={"seg" + (activeSeg === i ? " active" : "")}
                       data-i={i + 1}
                       style={{ left: a * 100 + "%", width: (b - a) * 100 + "%" }}
                       /* 원본 headerdrag (player_widget.py:2280): 누르는 즉시
                          세그먼트 시작 - 0.1s 로 seek 하고, 임계값을 넘게 끌면
                          그 세그먼트 구간을 잘라 DAW 로 내보낸다. 임계값을 못 넘고
                          놓으면 seek 만 남는다. 끌고 있는 동안 커서는 손을 쥔 모양.

                          ── 정책 변경 (사용자 지시 2026-09-03) ────────────────────
                          **세그먼트 헤더 클릭은 정지 상태에서도 바로 재생한다.**
                          (파형 몸통 클릭은 지금처럼 플레이헤드만 옮긴다 — 재생 안 함.)

                          왜 필요했나: 끝부분 헤더를 누르면 실제 파일 끝까지 재생되고
                          자연 종료된다. 그 순간 엔진이 소스를 닫는데(audio_engine
                          _teardown), playback.py 의 setPosition 은 `is_open()` 게이트가
                          있어서 **닫힌 뒤의 seek 을 엔진에 전달하지 않는다.**
                          그래서 그 다음부터 헤더를 아무리 눌러도 무반응이 됐다
                          (사용자 신고: "어느 순간 그냥 멈춰 · 1회 재생의 라이프타임이
                          정해져 있고 세그먼트 헤더는 그걸 갱신하지 않는 느낌").
                          여기서 재생을 같이 걸면 playback.py 의 play() 가 닫힌 소스를
                          재로딩하면서 setPosition 이 기억해 둔 `_pending_position_ms`
                          위치부터 시작한다 → 스스로 복구된다.
                          `playing` 일 때는 onToggle 을 부르지 않는다 — 그러면 일시정지가
                          되어 버린다 (onToggle 은 토글이다). */
                       onMouseDown={(event) => {
                         event.stopPropagation();
                         if (event.button !== 0 || !row) return;
                         const target = Math.max(0, a - SEG_SNAP_OFFSET_SEC / (row.dur || 1));
                         setActiveSeg(i);
                         setPos(target);
                         resetPlayhead(target);
                         onSeek?.(target * durMsNow());
                         /* ⚠ `if (!playing) onToggle()` 이면 **안 된다** (실측으로 실패).
                            재생이 자연 종료돼도 App 의 `playing` 은 true 로 남는다
                            (playback-state 처리가 setPlaying(false) 를 하지 않는다).
                            그래서 조건이 영원히 성립하지 않아 헤더 클릭이 여전히
                            무반응이었다 (사용자 재신고). React 상태에 의존하지 말고
                            **재생 명령을 직접 보낸다** — onForcePlay 는 App 에서
                            setPlaying(true) + audioBridge.play() 를 함께 한다.
                            이미 재생 중이면 엔진 쪽에서 무해하게 무시된다. */
                         onForcePlay?.();
                         const x0 = event.clientX;
                         const width = wrapRef.current?.getBoundingClientRect().width ?? 1;
                         const el = event.currentTarget as HTMLElement;
                         el.classList.add("grabbing");
                         let fired = false;
                         const move = (ev: MouseEvent) => {
                           if (fired) return;
                           if (Math.abs(ev.clientX - x0) < DRAG_THRESHOLD_PX) return;
                           void width;
                           fired = true;
                           dragRegion(a, b);
                         };
                         const up = () => {
                           el.classList.remove("grabbing");
                           window.removeEventListener("mousemove", move);
                           window.removeEventListener("mouseup", up);
                         };
                         window.addEventListener("mousemove", move);
                         window.addEventListener("mouseup", up);
                       }} />
                ))}
              </div>
            )}
            {sel && (
              <div className={"wave-sel" + (loop ? " loop" : "")}
                   style={{ left: sel[0] * 100 + "%", width: (sel[1] - sel[0]) * 100 + "%" }} />
            )}
            {/* 원본 paintEvent: 재생 중에만 amber 글로우가 숨쉰다
                (glow_alpha = 60 + 45*|0.5-phase|*2, 주기 96 * 8ms = 768ms) */}
            <div className={"wave-head" + (playing ? " pulsing" : "")}
                 style={{ left: pos * 100 + "%" }} />
          </>
        ) : (
          <div className="wave-empty">선택된 파일 없음</div>
        )}
      </div>

      {/* 가로 줌 폐지 — 파형 가로 이동 스크롤바 제거됨 */}

      <div className="control-rows">
        <div className="control-row transport">
          {/* ⚠ 재생/일시정지 버튼에는 색을 넣지 않는다 — 재생 중에만 주황으로
              바꿔 봤더니 누를 때마다 색이 바뀌어 정신없다는 지적을 받았다
              (2026-09-08). 정지 버튼만 항상 빨강이다 (app.css .tbtn.stop). */}
          <button className="tbtn primary"
                  aria-label={playing ? "일시정지" : "재생"} disabled={!row} onClick={onToggle}>
            {playing ? <IcoPause size={14} /> : <IcoPlay size={14} />}
          </button>
          <button className="tbtn stop" aria-label="정지" disabled={!row} onClick={onStop}><IcoStop size={13} /></button>
          <button className="tbtn" aria-label="처음으로" disabled={!row}
                  onClick={() => { setPos(0); resetPlayhead(0); onSeek?.(0); }}>
            <IcoSkip size={14} />
          </button>
          <button className={"tbtn loop" + (loop ? " on" : "")}
                  data-tip="반복 재생 — 영역 선택 시 그 부분만, 없으면 전체 (R)"
                  aria-label="반복 재생" onClick={() => setLoop((v) => !v)}>
            <IcoLoop size={13} />
          </button>

          <span className="time main-time"><strong>{fmtClock(pos * dur)}</strong> / {fmtClock(dur)}</span>

          <div className="track-info">
            {row && !narrowInfo && <span className="track-caption">현재 재생 중 :&nbsp;&nbsp;</span>}
            <span className="track-name">{row?.name ?? ""}</span>
          </div>
          {wide && monitoringControls(false)}
        </div>
        {!wide && <div className="control-row monitoring-row">{monitoringControls(true)}</div>}
      </div>
    </div>
  );
}
