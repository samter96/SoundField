import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { loadLayoutInfo, type LayoutInfo } from "../backend";
import { splitLayoutOverride } from "../layoutStore";

/* ── 바이노럴 채널 배치 메뉴 (원본 _build_binaural_layout_menu, player_widget.py:3481) ──
   구성/문구/활성 규칙을 원본 그대로 옮겼다.
     · 파일 없음        → "파일을 먼저 선택하세요" (비활성)
     · 후보 있고 미선택 + 자동판정 불가 → 안내 행(비활성)
         ambix+fuma 조합: "AmbiX와 FuMa를 번갈아 듣고 선택하세요"
         그 외:          "가능성이 높은 형식 중 하나를 선택하세요"
     · 첫 행 = **자동 판정** 결과 (강조색). 라벨은 판정 결과 라벨 /
       "판정 보류"(후보만 있을 때) / "지원 배치 없음". 선택 상태는 사용자 지정이 없을 때.
     · 구분선 → 서브메뉴 "스피커 채널 배치" / "앰비소닉 규격"
     · 프리셋 활성 조건: 스피커는 channels == 필요 채널 수,
       AmbiX 는 채널 수가 제곱수, FuMa 는 4채널
     · 후보면 "추천 · " 접두 + 굵게, 선택된 것은 " - 선택됨" 접미
   메뉴는 버튼 우상단 기준으로 **위쪽**으로 펼친다 (원본 _binaural_layout_menu_position:
   재생 바가 화면 아래라 메뉴 아래쪽을 버튼 위쪽에 맞춘다). */

const SPEAKER_PRESETS: Array<[string, string, number]> = [
  ["lcr", "3채널 좌·중·우", 3],
  ["quad", "4채널 쿼드", 4],
  ["5.0", "5.0", 5],
  ["5.1", "5.1", 6],
  ["6.1", "6.1", 7],
  ["7.0", "7.0", 7],
  ["7.1", "7.1", 8],
  ["7.0.2", "7.0.2", 9],
];

/** 원본 _layout_choice_label */
export function layoutChoiceLabel(preset: string, channels: number): string {
  if (preset === "quad") return "4채널 쿼드";
  if (preset === "ambix") {
    const order = channels > 0 ? Math.round(Math.sqrt(channels)) - 1 : -1;
    return order >= 0 ? `${order}차 AmbiX` : "AmbiX";
  }
  if (preset === "fuma") return "1차 FuMa";
  return ({
    lcr: "3채널 좌·중·우",
    "5.0": "5.0",
    "5.1": "5.1",
    "6.1": "6.1",
    "7.0": "7.0",
    "7.1": "7.1",
    "7.0.2": "7.0.2",
  } as Record<string, string>)[preset] ?? preset;
}

type Props = {
  /** 메뉴를 띄운 버튼 — 원본처럼 버튼 우상단 기준 위쪽으로 펼친다 */
  anchor: DOMRect;
  path: string;
  channels: number;
  /** 사용자가 이 파일에 지정해 둔 프리셋 (원본 _layout_overrides) */
  override: string;
  onPick: (preset: string) => void;
  /** 원본 "현재 폴더의 N채널 파일에 적용" — 지금 지정값을 폴더 범위로 저장한다 */
  onApplyToFolder?: (preset: string) => void;
  /** 원본 "채널 배치 비교해서 듣기" — 두 채널 순서를 번갈아 들어보는 창 */
  onCompare?: () => void;
  onClose: () => void;
};

export function BinauralMenu({ anchor, path, channels, override, onPick,
                              onApplyToFolder, onCompare, onClose }: Props) {
  const [info, setInfo] = useState<LayoutInfo | null>(null);
  const [flyout, setFlyout] = useState<"speaker" | "ambisonic" | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 236, h: 150 });

  useEffect(() => {
    let alive = true;
    if (!path) return;
    loadLayoutInfo(path, channels, override).then((got) => { if (alive) setInfo(got); });
    return () => { alive = false; };
  }, [path, channels, override]);

  useEffect(() => {
    const el = boxRef.current;
    if (el) setSize({ w: el.offsetWidth, h: el.offsetHeight });
  }, [info]);

  useEffect(() => {
    const close = () => onClose();
    const esc = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", esc);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", esc);
    };
  }, [onClose]);

  const candidates = new Set(info?.candidates ?? []);
  const current = override;
  const canAuto = Boolean(info?.can_auto_play);
  const selected = current || (canAuto ? info?.automatic_preset ?? "" : "");
  /* ⚠ 저장값에는 채널 **순서**까지 붙을 수 있다 ("5.1|film"). 체크 표시와
     [채널 배치 비교해서 듣기] 활성 판정은 **프리셋만** 떼서 비교해야 한다
     (조사 B02, 원본 selected_preset = split_layout_override(current)[0]).
     예전에는 토큰 전체를 "5.1" 같은 순수 프리셋과 비교해서, 순서를 한 번
     저장하면 체크가 사라지고 비교 기능도 비활성이 됐다. */
  const selectedPreset = splitLayoutOverride(selected)[0];

  const automaticResult = canAuto
    ? layoutChoiceLabel(info?.automatic_preset ?? "", channels)
    : candidates.size ? "판정 보류" : "지원 배치 없음";

  /* 원본은 메뉴 아래쪽을 버튼 위쪽에 맞추고 오른쪽 모서리를 정렬한다 */
  const left = Math.max(6, anchor.right - size.w + 1);
  const top = Math.max(6, anchor.top - size.h);

  /* ── 플라이아웃(하위 메뉴) 창 밖 잘림 방지 ────────────────────────────────
     기본은 왼쪽으로 펼치지만(`right: calc(100% + 4px)`), 메뉴가 창 왼쪽에 붙어 있으면
     그대로 잘려 나갔다 (사용자 신고). 열린 뒤 실제 위치를 재서
       · 왼쪽이 모자라면 오른쪽으로 뒤집고(.flip)
       · 아래가 넘치면 위로 끌어올린다(--fly-shift)
     둘 다 안 되면 창 안으로 밀어 넣는다. */
  const flyRef = useRef<HTMLDivElement | null>(null);
  const [flip, setFlip] = useState(false);
  const [shift, setShift] = useState(0);
  useLayoutEffect(() => {
    setFlip(false);
    setShift(0);
    if (!flyout) return;
    const el = flyRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    if (rect.left < 6) setFlip(true);
    const over = rect.bottom - (window.innerHeight - 6);
    if (over > 0) setShift(-Math.ceil(over));
  }, [flyout, left, top]);

  const item = (preset: string, baseLabel: string, enabled: boolean) => {
    let label = baseLabel;
    if (candidates.has(preset)) label = `추천 · ${label}`;
    if (selectedPreset === preset) label = `${label} - 선택됨`;
    return (
      <button key={preset}
              className={"bmenu-item"
                + (candidates.has(preset) || selectedPreset === preset ? " strong" : "")
                + (selectedPreset === preset ? " checked" : "")}
              disabled={!enabled}
              onClick={() => { onPick(preset); onClose(); }}>
        <span className="bmenu-check">{selectedPreset === preset ? "✓" : ""}</span>
        <span>{label}</span>
      </button>
    );
  };

  const order = channels > 0 ? Math.round(Math.sqrt(channels)) - 1 : -1;
  const isSquare = order >= 0 && (order + 1) ** 2 === channels;

  return (
    <div className="bmenu" ref={boxRef} style={{ left, top }}
         onMouseDown={(event) => event.stopPropagation()}>
      {!path ? (
        <div className="bmenu-item disabled">파일을 먼저 선택하세요</div>
      ) : (
        <>
          {candidates.size > 0 && !current && !canAuto && (
            <div className="bmenu-guide">
              {candidates.has("ambix") && candidates.has("fuma") && candidates.size === 2
                ? "AmbiX와 FuMa를 번갈아 듣고 선택하세요"
                : "가능성이 높은 형식 중 하나를 선택하세요"}
            </div>
          )}

          {/* 자동 판정 행 — 원본 _AutomaticLayoutMenuRow (강조색으로 결과 표시) */}
          <button className={"bmenu-item auto" + (!current ? " checked" : "")}
                  disabled={!(current || canAuto)}
                  onClick={() => { onPick(""); onClose(); }}>
            <span className="bmenu-check">{!current ? "✓" : ""}</span>
            <span>자동</span>
            <span className="bmenu-auto-result">{automaticResult}</span>
          </button>

          <div className="bmenu-sep" />

          <div className="bmenu-sub"
               onMouseEnter={() => setFlyout("speaker")}
               onMouseLeave={() => setFlyout(null)}>
            <button className="bmenu-item">
              <span className="bmenu-check" />
              <span>스피커 채널 배치</span>
              <span className="bmenu-arrow">›</span>
            </button>
            {flyout === "speaker" && (
              <div className={"bmenu-flyout" + (flip ? " flip" : "")} ref={flyRef}
                   style={shift ? { top: `calc(-5px + ${shift}px)` } : undefined}>
                {SPEAKER_PRESETS.map(([preset, label, need]) =>
                  item(preset, label, channels === need))}
              </div>
            )}
          </div>

          <div className="bmenu-sub"
               onMouseEnter={() => setFlyout("ambisonic")}
               onMouseLeave={() => setFlyout(null)}>
            <button className="bmenu-item">
              <span className="bmenu-check" />
              <span>앰비소닉 규격</span>
              <span className="bmenu-arrow">›</span>
            </button>
            {flyout === "ambisonic" && (
              <div className={"bmenu-flyout" + (flip ? " flip" : "")} ref={flyRef}
                   style={shift ? { top: `calc(-5px + ${shift}px)` } : undefined}>
                {item("ambix", order >= 0 && isSquare ? `${order}차 AmbiX` : "AmbiX", isSquare)}
                {item("fuma", "1차 FuMa", channels === 4)}
              </div>
            )}
          </div>

          <div className="bmenu-sep" />
          {/* 원본: 스피커 배치가 선택돼 있을 때만 활성 (앰비소닉은 비교 대상 아님) */}
          <button className="bmenu-item"
                  disabled={!["lcr", "5.0", "5.1", "7.0", "7.1", "7.0.2"].includes(selectedPreset)}
                  onClick={() => { onCompare?.(); onClose(); }}>
            <span className="bmenu-check" />
            <span>채널 배치 비교해서 듣기</span>
          </button>

          {/* 원본: 지금 지정된 배치가 있을 때만 나온다 */}
          {current && (
            <button className="bmenu-item"
                    onClick={() => { onApplyToFolder?.(current); onClose(); }}>
              <span className="bmenu-check" />
              <span>현재 폴더의 {channels}채널 파일에 적용</span>
            </button>
          )}
        </>
      )}
    </div>
  );
}
