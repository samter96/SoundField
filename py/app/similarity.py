"""유사 사운드 검색용 음향 특징 추출 (numpy 전용).

설계 근거는 `SoundField-Tauri-PoC/docs/SIMILAR_SEARCH_PLAN.md` 참고.

**비자명한 설계 결정 — 깨트리지 말 것**

1. 리샘플링을 하지 않는다. 라이브러리에 44.1k/48k/96k/192k 가 섞여 있어
   정수배 데시메이션을 쓰면 파일마다 다른 에일리어싱이 생겨 서로 비교가 깨진다.
   대신 프레임 길이를 **시간(64ms)** 으로, 멜 필터뱅크를 **Hz(30~16000)** 로 정의해
   샘플레이트가 달라도 같은 물리량이 나오게 한다. scipy 없이 안티에일리어싱
   필터를 제대로 구현하는 비용을 피하는 목적도 있다.
2. 분석 상한을 16kHz 로 고정한다. 192k 파일의 초고역 에너지가 스펙트럴
   센트로이드를 왜곡하는 것을 막는다. 44.1k(나이퀴스트 22k)까지 안전하다.
   부작용: 16kHz 에서 잘린 mp3 는 실제보다 어둡게 평가된다.
3. 음량에 의존하는 값을 특징에 넣지 않는다. MFCC 는 c0(전체 에너지)를 버리고
   c1..c20 만 쓰고, 엔벨로프는 크레스트비·어택위치·변동성만 쓴다.
   조용한 발소리도 발소리이므로 음량이 유사도를 끌면 안 된다.
4. 벡터는 정규화 없이 원값으로 반환한다. 차원별 스케일이 달라 코사인 유사도
   전에 z-점수 정규화가 필요하고, 그 평균·표준편차는 **말뭉치 통계**라
   추출 시점에 알 수 없다. `Normalizer` 로 검색 시점에 적용한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

import numpy as np

# 특징 벡터 규격 (버전이 바뀌면 저장된 벡터를 전부 다시 뽑아야 한다)
FEATURE_VERSION = 1
N_MFCC = 20           # c1..c20 (c0 = 전체 에너지는 버림)
N_MEL = 40
FRAME_SEC = 0.064
HOP_RATIO = 0.5
FMIN_HZ = 30.0
FMAX_HZ = 16000.0
WINDOW_SEC = 3.0      # 한 창의 분석 길이
MAX_WINDOWS = 4       # 한 영역에서 뽑는 창의 최대 개수

# 40(MFCC 평균·편차) + 40(차분 평균·편차) + 4(센트로이드·플랫니스) + 3(엔벨로프)
FEATURE_DIM = N_MFCC * 4 + 4 + 3

_EPS = 1e-9


# ---------------------------------------------------------------- 필터뱅크

def _hz_to_mel(f: np.ndarray | float) -> np.ndarray | float:
    return 2595.0 * np.log10(1.0 + np.asarray(f, dtype=np.float64) / 700.0)


def _mel_to_hz(m: np.ndarray | float) -> np.ndarray | float:
    return 700.0 * (10.0 ** (np.asarray(m, dtype=np.float64) / 2595.0) - 1.0)


@lru_cache(maxsize=32)
def _analysis_plan(sample_rate: int) -> Tuple[int, int, int, np.ndarray, np.ndarray, np.ndarray]:
    """샘플레이트별 분석 계획을 캐시한다.

    반환: (frame_len, hop, n_bins, window, 멜필터뱅크, 대역 주파수)
    """
    if sample_rate <= 0:
        raise ValueError("sample_rate 가 0 이하")
    frame_len = max(256, int(round(FRAME_SEC * sample_rate)))
    hop = max(1, int(round(frame_len * HOP_RATIO)))
    n_bins = frame_len // 2 + 1
    freqs = np.fft.rfftfreq(frame_len, 1.0 / sample_rate).astype(np.float64)

    fmax = min(FMAX_HZ, sample_rate * 0.49)
    fmin = min(FMIN_HZ, fmax * 0.5)
    edges = _mel_to_hz(np.linspace(_hz_to_mel(fmin), _hz_to_mel(fmax), N_MEL + 2))

    fb = np.zeros((N_MEL, n_bins), dtype=np.float32)
    for i in range(N_MEL):
        lo, mid, hi = edges[i], edges[i + 1], edges[i + 2]
        if hi <= lo:
            continue
        rising = (freqs > lo) & (freqs <= mid)
        falling = (freqs > mid) & (freqs < hi)
        if mid > lo:
            fb[i, rising] = ((freqs[rising] - lo) / (mid - lo)).astype(np.float32)
        if hi > mid:
            fb[i, falling] = ((hi - freqs[falling]) / (hi - mid)).astype(np.float32)
        # 삼각형이 어느 빈에도 걸치지 못하면 가장 가까운 빈 하나를 쓴다
        if not fb[i].any():
            fb[i, int(np.argmin(np.abs(freqs - mid)))] = 1.0
    # 대역폭 정규화 — 고역 필터가 넓어 에너지가 몰리는 것을 막는다
    fb /= (fb.sum(axis=1, keepdims=True) + _EPS)

    window = np.hanning(frame_len).astype(np.float32)
    return frame_len, hop, n_bins, window, fb, freqs.astype(np.float32)


@lru_cache(maxsize=4)
def _dct_matrix(n_out: int, n_in: int) -> np.ndarray:
    """DCT-II (orthonormal). c0 는 호출부에서 버린다."""
    n = np.arange(n_in, dtype=np.float64)
    k = np.arange(n_out, dtype=np.float64)[:, None]
    m = np.cos(np.pi * k * (2.0 * n + 1.0) / (2.0 * n_in)) * np.sqrt(2.0 / n_in)
    m[0] /= np.sqrt(2.0)
    return m.astype(np.float32)


# ------------------------------------------------------------ 특징 추출

def extract_features(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """모노 샘플에서 87차원 특징 벡터를 뽑는다.

    samples: float 모노 1차원 배열 (스케일 무관 — 음량 불변 특징만 쓴다)
    무음/너무 짧은 입력은 0 벡터를 반환한다 (`is_degenerate` 로 판별).
    """
    x = np.asarray(samples, dtype=np.float32).ravel()
    frame_len, hop, _n_bins, window, fb, freqs = _analysis_plan(int(sample_rate))

    if x.size < frame_len:
        x = np.pad(x, (0, frame_len - x.size))
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak < 1e-6:
        return np.zeros(FEATURE_DIM, dtype=np.float32)
    x = x / peak  # 음량 불변 — 절대 레벨은 특징에 넣지 않는다

    n_frames = 1 + (x.size - frame_len) // hop
    idx = np.arange(frame_len)[None, :] + hop * np.arange(n_frames)[:, None]
    frames = x[idx] * window
    spec = np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32)

    mel = np.log(spec @ fb.T + 1e-8)
    mfcc = (mel @ _dct_matrix(N_MFCC + 1, N_MEL).T)[:, 1:]  # c0 버림
    delta = np.diff(mfcc, axis=0) if mfcc.shape[0] > 1 else np.zeros((1, N_MFCC), np.float32)

    band = freqs <= min(FMAX_HZ, sample_rate * 0.49)
    bspec = spec[:, band]
    bfreq = freqs[band]
    power = bspec.sum(axis=1) + _EPS
    centroid = (bspec @ bfreq) / power
    flatness = (np.exp(np.mean(np.log(bspec + 1e-8), axis=1))
                / (np.mean(bspec, axis=1) + _EPS))

    env = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1) + 1e-12)
    env_mean = float(env.mean())
    env_max = float(env.max())
    crest = 20.0 * np.log10((env_max + _EPS) / (env_mean + _EPS))       # 타격음 vs 연속음
    attack_pos = float(np.argmax(env)) / max(1, env.size - 1)           # 어택 위치 비율
    dynamics = float(env.std() / (env_mean + _EPS))                     # 음량 변동성

    vec = np.concatenate([
        mfcc.mean(axis=0), mfcc.std(axis=0),
        delta.mean(axis=0), delta.std(axis=0),
        [centroid.mean(), centroid.std(), flatness.mean(), flatness.std()],
        [crest, attack_pos, dynamics],
    ]).astype(np.float32)
    return np.nan_to_num(vec, nan=0.0, posinf=0.0, neginf=0.0)


def is_degenerate(vec: np.ndarray) -> bool:
    """무음·추출 실패로 의미 없는 벡터인지."""
    return not np.any(np.asarray(vec, dtype=np.float32))


# ------------------------------------------------------------ 영역 → 벡터

def window_offsets(region_sec: float, *, window_sec: float = WINDOW_SEC,
                   max_windows: int = MAX_WINDOWS) -> List[float]:
    """영역 안에서 분석할 창들의 시작 위치(초, 영역 기준)를 고른다.

    영역이 창보다 짧으면 [0.0]. 길면 균등 간격으로 최대 max_windows 개.
    긴 앰비언스에서 앞 3초만 보고 판단하는 문제를 막기 위한 것이다.
    """
    if region_sec <= window_sec * 1.25 or max_windows <= 1:
        return [0.0]
    n = min(max_windows, max(2, int(region_sec // window_sec)))
    span = region_sec - window_sec
    return [span * i / (n - 1) for i in range(n)]


@dataclass(frozen=True)
class RegionFeatures:
    """영역 하나에서 뽑은 결과."""
    vector: np.ndarray          # 창별 벡터의 평균 (FEATURE_DIM,)
    windows: np.ndarray         # 창별 벡터 (n_windows, FEATURE_DIM)
    sample_rate: int
    analyzed_sec: float
    spans: Tuple[Tuple[float, float], ...] = ()   # 창별 (시작초, 끝초) — 파일 기준 절대값
    file_sec: float = 0.0        # 파일 실제 길이 (DB 값이 틀린 경우가 있어 여기서 알려준다)
    failed_windows: int = 0      # 읽기 실패로 건너뛴 창 수


def features_from_region(path: str, start_sec: float = 0.0,
                         end_sec: Optional[float] = None, *,
                         window_sec: float = WINDOW_SEC,
                         max_windows: int = MAX_WINDOWS) -> RegionFeatures:
    """파일의 [start_sec, end_sec) 영역에서 특징을 뽑는다.

    영역 전체를 읽지 않는다 — 균등 간격의 창만 seek 해서 읽는다.
    실측(2026-09-10)상 비용의 거의 전부가 파일 열기·읽기이므로, 창 수를
    늘리는 것이 곧 비용이다. 기본 4개.
    """
    import soundfile as sf  # 지연 import — 이 모듈은 numpy 만으로도 쓸 수 있어야 한다

    with sf.SoundFile(path) as fh:
        sr = int(fh.samplerate)
        total_sec = fh.frames / sr if sr else 0.0
        start = max(0.0, float(start_sec))
        end = total_sec if end_sec is None else min(total_sec, float(end_sec))
        region = max(0.0, end - start)
        if region <= 0.0:
            return RegionFeatures(np.zeros(FEATURE_DIM, np.float32),
                                  np.zeros((0, FEATURE_DIM), np.float32), sr, 0.0,
                                  (), total_sec, 0)

        win = min(window_sec, region)
        want = max(1, int(round(win * sr)))
        vecs = []
        spans = []
        analyzed = 0.0
        failed = 0
        for off in window_offsets(region, window_sec=win, max_windows=max_windows):
            # 창 하나가 실패해도 파일 전체를 버리지 않는다.
            # MP3 는 libsndfile 이 보고하는 프레임 수가 실제보다 큰 경우가 있어
            # **파일 끝에 붙는 마지막 창**에서 읽기가 깨진다 (실측 2026-09-10:
            # LucasFilmFx mp3 60개 중 53개가 마지막 창에서만 실패, 앞 3개는 정상).
            try:
                fh.seek(int(round((start + off) * sr)))
                data = fh.read(want, dtype="float32", always_2d=True)
            except Exception:
                failed += 1
                continue
            if not data.size:
                continue
            mono = data.mean(axis=1)
            v = extract_features(mono, sr)
            if not is_degenerate(v):
                vecs.append(v)
                spans.append((start + off, start + off + mono.size / sr))
                analyzed += mono.size / sr

    if not vecs:
        return RegionFeatures(np.zeros(FEATURE_DIM, np.float32),
                              np.zeros((0, FEATURE_DIM), np.float32), sr, 0.0,
                              (), total_sec, failed)
    stack = np.stack(vecs)
    return RegionFeatures(stack.mean(axis=0).astype(np.float32), stack, sr,
                          analyzed, tuple(spans), total_sec, failed)


# -------------------------------------------- 질의용: 영역 전체 듣기

# 사용자가 고른 영역을 얼마나 들을지 (사용자 결정 2026-09-10).
# 5분 이하면 한 조각도 빼지 않고 전부 듣는다. 그보다 길면 분석량을 5분치로
# 묶고 영역 전체에 걸쳐 나눠 훑는다 — 87분 영역을 다 읽으면 47초가 걸린다.
QUERY_BUDGET_SEC = 300.0
# 덩어리로 끊어 읽는 이유: 창 100개를 따로 찾아 읽으면(seek) 5분을 죽 읽는 것보다
# 더 느리다 (실측 2026-09-10 — 30초 파일에서 창 4개 표본 123~920ms vs 전체 읽기
# 77~310ms). 그래서 찾아가는 횟수를 이 개수로 묶는다.
QUERY_MAX_BLOCKS = 10
_READ_BLOCK_SEC = 30.0


def _blocks_for(region_sec: float, budget_sec: float,
                max_blocks: int) -> List[Tuple[float, float]]:
    """영역을 (시작오프셋, 길이) 덩어리 목록으로 나눈다.

    예산 안에 들어오는 영역은 통째로 한 덩어리. 넘으면 영역 전체에 균등 간격으로
    퍼진 덩어리 여러 개 — 앞부분만 듣고 판단하는 것을 막는다.
    """
    if region_sec <= budget_sec or max_blocks <= 1:
        return [(0.0, region_sec)]
    n = max_blocks
    each = budget_sec / n
    if region_sec - each <= 0:
        return [(0.0, region_sec)]
    step = (region_sec - each) / (n - 1)
    return [(step * i, each) for i in range(n)]


def features_from_region_full(path: str, start_sec: float = 0.0,
                              end_sec: Optional[float] = None, *,
                              window_sec: float = WINDOW_SEC,
                              budget_sec: float = QUERY_BUDGET_SEC,
                              max_blocks: int = QUERY_MAX_BLOCKS) -> RegionFeatures:
    """질의용 — 고른 영역을 빠짐없이(또는 예산 안에서 촘촘히) 듣고 한 벡터로 만든다.

    `features_from_region` 은 색인용이라 창을 4개만 표본한다. 114만 개를 훑는
    비용 때문이고, 질의는 파일 한 개짜리 동작이라 같은 제한을 쓸 이유가 없다.

    표본 4개와 전체 듣기는 **결과가 실제로 다르다** (실측 2026-09-10, 정규화 공간):
    20~45초 영역은 상위 5개 중 78% 만 같고, 150초 이상은 55% 만 같으며 1등도
    세 번 중 한 번 바뀐다. 원값 코사인으로는 0.99 로 보여 같아 보이지만 착시다.
    """
    import soundfile as sf

    with sf.SoundFile(path) as fh:
        sr = int(fh.samplerate)
        total_sec = fh.frames / sr if sr else 0.0
        start = max(0.0, float(start_sec))
        end = total_sec if end_sec is None else min(total_sec, float(end_sec))
        region = max(0.0, end - start)
        if region <= 0.0:
            return RegionFeatures(np.zeros(FEATURE_DIM, np.float32),
                                  np.zeros((0, FEATURE_DIM), np.float32), sr, 0.0,
                                  (), total_sec, 0)

        win = max(1, int(round(window_sec * sr)))
        vecs: List[np.ndarray] = []
        spans: List[Tuple[float, float]] = []
        analyzed = 0.0
        failed = 0

        for off, length in _blocks_for(region, budget_sec, max_blocks):
            pos = start + off
            try:
                fh.seek(int(round(pos * sr)))
            except Exception:
                failed += 1
                continue
            left = max(1, int(round(length * sr)))
            carry = np.zeros(0, dtype=np.float32)
            carry_at = pos
            while left > 0:
                try:
                    chunk = fh.read(min(int(_READ_BLOCK_SEC * sr), left),
                                    dtype="float32", always_2d=True)
                except Exception:
                    # mp3 는 파일 끝 근처에서 읽기가 깨진다 — 여기까지로 만족한다
                    failed += 1
                    break
                if not chunk.size:
                    break
                left -= chunk.shape[0]
                mono = chunk.mean(axis=1)
                buf = np.concatenate([carry, mono]) if carry.size else mono
                n_full = buf.size // win
                for i in range(n_full):
                    seg = buf[i * win:(i + 1) * win]
                    v = extract_features(seg, sr)
                    if not is_degenerate(v):
                        vecs.append(v)
                        at = carry_at + i * window_sec
                        spans.append((at, at + window_sec))
                        analyzed += window_sec
                carry = buf[n_full * win:]
                carry_at += n_full * window_sec
            # 남은 꼬리가 창의 절반을 넘으면 그것도 쓴다 (짧은 영역을 버리지 않기)
            if carry.size > win * 0.5:
                v = extract_features(carry, sr)
                if not is_degenerate(v):
                    vecs.append(v)
                    spans.append((carry_at, carry_at + carry.size / sr))
                    analyzed += carry.size / sr

    if not vecs:
        return RegionFeatures(np.zeros(FEATURE_DIM, np.float32),
                              np.zeros((0, FEATURE_DIM), np.float32), sr, 0.0,
                              (), total_sec, failed)
    stack = np.stack(vecs)
    return RegionFeatures(stack.mean(axis=0).astype(np.float32), stack, sr,
                          analyzed, tuple(spans), total_sec, failed)


# ------------------------------------------------------------ 정규화·유사도

# z-점수를 이 범위로 자른다. 한 차원이 유사도를 독점하는 것을 막는 안전장치.
# 말뭉치가 아주 작을 때(추출 초반) 편차가 0 에 가까운 차원이 생기고, 거기서
# 미세한 차이가 수천 배로 증폭돼 순위를 뒤집는 일이 실제로 있었다.
Z_CLIP = 10.0
# 이 미만이면 정규화 통계 자체가 불안정하다 — 검색 순위를 믿을 수 없다.
MIN_ROWS_FOR_STATS = 50


class Normalizer:
    """말뭉치 통계(차원별 평균·표준편차)로 z-점수 정규화.

    차원별 스케일이 크게 달라(센트로이드는 수천, MFCC 는 한 자리) 정규화 없이
    거리를 재면 센트로이드가 유사도를 독점한다.

    ⚠ 잘라내기(Z_CLIP)로도 표본이 극단적으로 적은 경우는 구제되지 않는다.
    말뭉치가 `MIN_ROWS_FOR_STATS` 미만이면 순위를 신뢰하지 말 것.
    """

    def __init__(self, mean: Sequence[float], std: Sequence[float]):
        self.mean = np.asarray(mean, dtype=np.float32).reshape(-1)
        self.std = np.asarray(std, dtype=np.float32).reshape(-1)
        if self.mean.size != FEATURE_DIM or self.std.size != FEATURE_DIM:
            raise ValueError("정규화 통계 차원이 FEATURE_DIM 과 다르다")
        self.std = np.where(self.std < 1e-6, 1.0, self.std).astype(np.float32)

    @classmethod
    def from_matrix(cls, matrix: np.ndarray) -> "Normalizer":
        m = np.asarray(matrix, dtype=np.float32).reshape(-1, FEATURE_DIM)
        return cls(m.mean(axis=0), m.std(axis=0))

    def apply(self, vectors: np.ndarray) -> np.ndarray:
        v = np.asarray(vectors, dtype=np.float32)
        z = (v - self.mean) / self.std
        return np.clip(z, -Z_CLIP, Z_CLIP).astype(np.float32)

    def to_dict(self) -> dict:
        return {"version": FEATURE_VERSION,
                "mean": self.mean.tolist(), "std": self.std.tolist()}

    @classmethod
    def from_dict(cls, data: dict) -> "Normalizer":
        return cls(data["mean"], data["std"])


def cosine_scores(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """정규화된 질의 벡터 1개 vs 말뭉치 행렬의 코사인 유사도 (-1~1).

    말뭉치가 158만 × 87 이면 행렬 곱 한 번으로 끝난다 — ANN 색인 불필요.
    """
    q = np.asarray(query, dtype=np.float32).reshape(-1)
    m = np.asarray(matrix, dtype=np.float32).reshape(-1, q.size)
    qn = q / (np.linalg.norm(q) + _EPS)
    mn = m / (np.linalg.norm(m, axis=1, keepdims=True) + _EPS)
    return (mn @ qn).astype(np.float32)
