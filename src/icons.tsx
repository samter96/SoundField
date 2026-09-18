/* 아이콘 — 전부 SVG path. 텍스트 기호(✓, ▶)는 쓰지 않는다.
   stroke 기반이라 어느 배율에서도 선이 뭉개지지 않는다. */
type P = { size?: number; className?: string };

type SOpt = { fill?: boolean; stroke?: number; size?: number; scaleStroke?: boolean };

const S = (d: string, fillOrOpt: boolean | SOpt = false) => {
  const opt: SOpt = typeof fillOrOpt === "boolean" ? { fill: fillOrOpt } : fillOrOpt;
  const fill = Boolean(opt.fill);
  const sw = opt.stroke ?? 1.4;
  const defaultSize = opt.size ?? 14;
  /* 트랜스포트처럼 글리프가 viewBox 를 꽉 채우는 아이콘은 선도 같이 커져야
     한다(scaleStroke). 작은 UI 아이콘은 비확대 선이 더 또렷하다. */
  const scaleStroke = Boolean(opt.scaleStroke);
  return function Icon({ size = defaultSize, className }: P) {
    return (
      <svg
        width={size}
        height={size}
        viewBox="0 0 16 16"
        className={className}
        fill={fill ? "currentColor" : "none"}
        stroke={fill ? "none" : "currentColor"}
        strokeWidth={sw}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d={d} vectorEffect={scaleStroke ? undefined : "non-scaling-stroke"} />
      </svg>
    );
  };
};

export const IcoCaret   = S("M6 3.5L10.5 8L6 12.5");
export const IcoDown    = S("M3.5 6L8 10.5L12.5 6");
export const IcoPlus    = S("M8 3.5v9M3.5 8h9");
export const IcoX       = S("M4 4l8 8M12 4l-8 8");
export const IcoSearch  = S("M7.2 11.4a4.2 4.2 0 1 0 0-8.4a4.2 4.2 0 0 0 0 8.4M10.4 10.4L13.5 13.5");
export const IcoGear    = S("M8 10a2 2 0 1 0 0-4a2 2 0 0 0 0 4M8 1.6l1 1.6l1.9-.3l.5 1.8l1.7.9l-.7 1.8l.7 1.8l-1.7.9l-.5 1.8l-1.9-.3l-1 1.6l-1-1.6l-1.9.3l-.5-1.8l-1.7-.9l.7-1.8l-.7-1.8l1.7-.9l.5-1.8l1.9.3z");
export const IcoRefresh = S("M13 8a5 5 0 1 1-1.6-3.7M13 2.2v3h-3");
export const IcoCheck   = S("M3.6 8.3l2.8 2.8L12.4 5");
/* 재생 — 삼각형이 viewBox 를 꽉 채운다. 30x26 버튼에서 size 16 = 안쪽 높이의 약 67%. */
export const IcoPlay    = S("M2.6 0.9L14.5 8L2.6 15.1z", { fill: true, size: 16 });
export const IcoPause   = S("M3.1 0.9h3.7v14.2H3.1zM9.2 0.9h3.7v14.2H9.2z", { fill: true, size: 16 });
export const IcoStop    = S("M2.1 2.1h11.8v11.8H2.1z", { fill: true, size: 16 });
/* 처음으로 — 막대 + 좌향 삼각형 (원본 ⏮ 도형). */
export const IcoSkip    = S("M1.5 1.1h2.3v13.8H1.5zM14.5 1.1L5.4 8l9.1 6.9z", { fill: true, size: 16 });
/* 반복 재생 — 원본(player_widget.py:1265)은 반지름 4.8 의 시계방향 290도 원호 +
   시작점(1시) 화살촉이다. 같은 형태를 유지하면서 viewBox 를 꽉 채우도록 키웠고,
   화살촉은 원본처럼 채운 삼각형으로 그린다. */
export function IcoLoop({ size = 16, className }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" className={className}
         aria-hidden="true">
      <path d="M11.1 2.63A6.2 6.2 0 1 1 4.01 3.25"
            fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      <path d="M13.04 0.05L7.87 1.98L11.75 5.47z" fill="currentColor" />
    </svg>
  );
}
/* 세그먼트 토글 아이콘 — 글리프가 자기 viewBox 를 꽉 채우도록 그렸다.
   따라서 렌더 size 가 곧 시각 크기다. 버튼 안쪽 20px 의 80% = 16px 기본값. */
export function IcoSegment({ size = 16, className }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" className={className}
         fill="none" stroke="currentColor" strokeWidth="1.4"
         strokeLinecap="square" strokeLinejoin="miter" aria-hidden="true">
      <rect x="0.8" y="0.8" width="14.4" height="14.4" rx="1.2" />
      <path d="M4.6 0.8v14.4M8 0.8v14.4M11.4 0.8v14.4" />
    </svg>
  );
}
export const IcoVolume  = S("M4 6.2h2l2.6-2.2v8L6 9.8H4zM10.6 6a2.8 2.8 0 0 1 0 4");
export const IcoHistory = S("M2.8 8a5.2 5.2 0 1 0 1.6-3.7M2.6 2.4v3h3M8 5.2V8l2 1.4");
export const IcoFolder  = S("M2.2 12.4V4.2a.8.8 0 0 1 .8-.8h2.6l1.2 1.6h5a.8.8 0 0 1 .8.8v6.6a.8.8 0 0 1-.8.8H3a.8.8 0 0 1-.8-.8z");
export const IcoStar    = S("M8 2.4l1.7 3.5l3.9.5l-2.8 2.7l.7 3.8L8 11.1l-3.5 1.8l.7-3.8L2.4 6.4l3.9-.5z");
export const IcoTrash   = S("M3.4 4.6h9.2M6.4 4.6V3.2h3.2v1.4M4.6 4.6l.6 8h5.6l.6-8M6.8 6.8v3.6M9.2 6.8v3.6");
/* 라이브러리 제거 — 목록에서 빼내는 동작. 휴지통(완전 제거)보다 한 단계 약한 표현으로
   폴더 안에 마이너스를 둔다 (사용자 지시 2026-09-02). */
export const IcoFolderMinus = S("M2.2 12.4V4.2a.8.8 0 0 1 .8-.8h2.6l1.2 1.6h5a.8.8 0 0 1 .8.8v6.6a.8.8 0 0 1-.8.8H3a.8.8 0 0 1-.8-.8zM5.9 9.4h4.2");
export const IcoInfo    = S("M8 14A6 6 0 1 0 8 2a6 6 0 0 0 0 12M8 7.2v3.6M8 5.2v.2");
export const IcoAlert   = S("M8 2.6l6 10.8H2zM8 6.6v3.2M8 11.6v.2");
/* 정확한 검색 과녁 — 원본 _PreciseToggle.paintEvent (multi_search.py:37)
   28px 버튼 안에 원 두 개(반지름 8.5 / 5.0, 펜 1.6)와 채운 가운데 점(반지름 1.9).
   viewBox 20 을 20px 로 그리면 반지름이 원본 픽셀과 1:1 로 맞는다. */
export function IcoTarget({ size = 20, className }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" className={className} aria-hidden="true">
      <circle cx="10" cy="10" r="8.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="10" cy="10" r="5" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="10" cy="10" r="1.9" fill="currentColor" />
    </svg>
  );
}
export const IcoMin     = S("M3.5 8h9");
export const IcoMax     = S("M3.6 3.6h8.8v8.8H3.6z");
export const IcoSun     = S("M8 11a3 3 0 1 0 0-6a3 3 0 0 0 0 6M8 1.6v1.6M8 12.8v1.6M1.6 8h1.6M12.8 8h1.6M3.5 3.5l1.1 1.1M11.4 11.4l1.1 1.1M12.5 3.5l-1.1 1.1M4.6 11.4l-1.1 1.1");
export const IcoDup     = S("M5.6 5.6V3.4h7v7h-2.2M3.4 5.6h7v7h-7z");
export const IcoLayers  = S("M8 2.2L14 5.4L8 8.6L2 5.4zM2 8.6l6 3.2l6-3.2M2 11.4l6 3.2l6-3.2");
export const IcoBan     = S("M8 14A6 6 0 1 0 8 2a6 6 0 0 0 0 12M4 4l8 8");
export const IcoExport  = S("M8 10.4V2.8M5.2 5.6L8 2.8l2.8 2.8M2.8 10v2.4a.8.8 0 0 0 .8.8h8.8a.8.8 0 0 0 .8-.8V10");
export const IcoMore    = S("M3.2 8h.1M7.95 8h.1M12.7 8h.1");

export function BrandMark({ size = 18 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <defs>
        <linearGradient id="bm" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--accent-hover)" />
          <stop offset="1" stopColor="var(--accent-pressed)" />
        </linearGradient>
      </defs>
      <circle cx="12" cy="12" r="10" fill="none" stroke="url(#bm)" strokeWidth="1.5" opacity=".55" />
      <path d="M5 12h2.4l1.6-5l2 10l2-7l1.4 2H19"
            fill="none" stroke="url(#bm)" strokeWidth="1.7"
            strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/* ── 툴 로고 (파형 막대) ─────────────────────────────────────────────────────
   앱 아이콘(tools/make_icon.py)과 **같은 글리프**다. 좌우 대칭 파형 막대 —
   스플래시·중앙 로더의 박동 막대와도 같은 모티프라 앱 전체가 하나로 묶인다.
   예전 마크(얇은 원 링 + 심박선)는 작은 크기에서 사라지고 값싸 보였다
   (사용자 지시 2026-09-03: 아이콘 고도화 후 툴 화면/스플래시에도 반영).
   가운데 막대만 밝게 — 아이콘과 같은 초점 규칙. */
export function WaveMark({ size = 16 }: P) {
  const env = [0.40, 0.76, 1.0, 0.76, 0.40];
  const barW = 2.6;
  const gap = 1.5;
  const span = env.length * barW + (env.length - 1) * gap;
  const left = (24 - span) / 2;
  const maxH = 17;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <defs>
        <linearGradient id="wm" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="var(--accent-hover)" />
          <stop offset="1" stopColor="var(--accent-pressed)" />
        </linearGradient>
      </defs>
      {env.map((e, i) => {
        const h = maxH * e;
        return (
          <rect key={i} x={left + i * (barW + gap)} y={12 - h / 2}
                width={barW} height={h} rx={barW / 2}
                fill={i === 2 ? "var(--accent-hover)" : "url(#wm)"} />
        );
      })}
    </svg>
  );
}

/* ── YSG Audio Labs 마크 (2026 리뉴얼) ──────────────────────────────────────
   2026 리뉴얼 로고의 **세잎매듭(trefoil)** 을 인앱 상단바용 흑백으로 다시 세운 것.
   구 마크(Y 스플리터)는 2026-09-15 에 은퇴했다 — 브랜드가 Tools → Labs 로 바뀌면서
   로고 자체가 매듭으로 교체됐다.

   원본 로고는 래스터뿐이라 픽셀을 트레이스하면 반투명하게 겹친 띠들이 22px 에서
   덩어리로 뭉갠다. 그래서 같은 위상의 매듭을 수식으로 다시 세웠다:
       x(t) = sin t + 2 sin 2t,  y(t) = cos t − 2 cos 2t,  z(t) = −sin 3t
   z 는 그리지 않는다 — 교차마다 어느 획이 아래로 지나가는지만 정한다.

   끊긴 자리는 SVG 마스크로 **실제로 뚫는다.** 배경색으로 덧칠하면 테마가 바뀌는
   순간 네모난 자국이 드러난다. 색은 currentColor 만 쓴다 — 무채색·네온·라이트
   세 테마에서 타이틀 글자색을 그대로 따라간다.

   ⚠ 좌표를 손으로 고치지 말 것. 기하는 브랜드 저장소의 생성기가 계산한다:
     C:\Users\samter96\Desktop\YSGAudioTools\brand\build_ysg_mark.py
     (확정 파라미터 — 형태 A · 선 매듭 · 획 2.10 · 바깥 궤도 원 없음, 사용자 선택
     2026-09-15). 그 생성기가 뱉는 YsgMark.tsx.txt 를 그대로 옮긴 것이다.
     ⚠ 같은 날 Sound_Search_program 저장소에 커밋된 판(검은 획 3.80)은 생성기
       최종본이 아니라 중간본이다 — 이쪽(2.14)이 맞다.

   ⚠ 마스크 id `ysg-mark-2026` 은 tools/build_release.ps1 이 "타이틀바 마크가
     빌드에 실제로 들어갔는지" 확인할 때 찾는 문자열이다. 바꾸면 그쪽도 같이. */
export function YsgMark({ size = 26 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <mask id="ysg-mark-2026" maskUnits="userSpaceOnUse" x="0" y="0" width="24" height="24">
        <rect width="24" height="24" fill="black" />
        <g fill="none" strokeLinecap="butt" strokeLinejoin="round">
          <path
            d="M12.00 9.87C13.75 9.87 15.49 10.20 17.04 10.81C18.59 11.42 19.93 12.30 20.93 13.34C21.93 14.38 22.59 15.56 22.84 16.71C23.08 17.86 22.92 18.97 22.40 19.88C21.87 20.79 20.99 21.48 19.87 21.84C18.75 22.21 17.40 22.23 16.00 21.88C14.61 21.53 13.16 20.81 11.86 19.77C10.56 18.74 9.41 17.39 8.53 15.88C7.66 14.36 7.07 12.69 6.83 11.05C6.58 9.40 6.67 7.79 7.07 6.41C7.46 5.02 8.16 3.87 9.03 3.08C9.91 2.29 10.95 1.87 12.00 1.87C13.05 1.87 14.09 2.29 14.97 3.08C15.84 3.87 16.54 5.02 16.93 6.41C17.33 7.79 17.42 9.40 17.17 11.05C16.93 12.69 16.34 14.36 15.47 15.88C14.59 17.39 13.44 18.74 12.14 19.77C10.84 20.81 9.39 21.53 8.00 21.88C6.60 22.23 5.25 22.21 4.13 21.84C3.01 21.48 2.13 20.79 1.60 19.88C1.08 18.97 0.92 17.86 1.16 16.71C1.41 15.56 2.07 14.38 3.07 13.34C4.07 12.30 5.41 11.42 6.96 10.81C8.51 10.20 10.25 9.87 12.00 9.87Z"
            stroke="white"
            strokeWidth="2.10"
          />
          <path
            d="M15.37 10.28C15.81 10.39 16.23 10.52 16.65 10.66C17.07 10.81 17.47 10.98 17.86 11.17C18.25 11.35 18.62 11.55 18.97 11.77"
            stroke="black"
            strokeWidth="2.14"
          />
          <path
            d="M13.43 18.59C13.12 18.91 12.79 19.22 12.46 19.51C12.12 19.80 11.77 20.06 11.42 20.31C11.06 20.55 10.70 20.77 10.33 20.97"
            stroke="black"
            strokeWidth="2.14"
          />
          <path
            d="M7.20 12.76C7.08 12.33 6.97 11.89 6.89 11.45C6.81 11.02 6.76 10.59 6.72 10.16C6.69 9.73 6.68 9.31 6.69 8.89"
            stroke="black"
            strokeWidth="2.14"
          />
          <path
            d="M17.21 7.74C17.30 8.37 17.33 9.02 17.30 9.69C17.28 10.36 17.19 11.04 17.05 11.73C16.91 12.41 16.72 13.09 16.46 13.77"
            stroke="white"
            strokeWidth="2.10"
          />
          <path
            d="M14.70 21.45C14.12 21.22 13.54 20.92 12.97 20.56C12.41 20.21 11.86 19.79 11.34 19.33C10.81 18.87 10.32 18.35 9.86 17.80"
            stroke="white"
            strokeWidth="2.10"
          />
          <path
            d="M4.08 12.43C4.58 12.04 5.13 11.69 5.72 11.38C6.31 11.07 6.95 10.80 7.61 10.57C8.27 10.35 8.97 10.18 9.67 10.07"
            stroke="white"
            strokeWidth="2.10"
          />
        </g>
      </mask>
      <rect width="24" height="24" fill="currentColor" mask="url(#ysg-mark-2026)" />
    </svg>
  );
}

/* 결과표 정렬 표시 — 원본 paintSection 은 7x4 채운 삼각형을 그린다.
   내림차순은 아래쪽, 오름차순은 위쪽 (CSS 로 회전). */
export function IcoSortTri({ size = 8, className }: P) {
  return (
    <svg width={size} height={(size * 4) / 7} viewBox="0 0 7 4" className={className}
         aria-hidden="true">
      <path d="M0 0h7L3.5 4z" fill="currentColor" />
    </svg>
  );
}
