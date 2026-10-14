"""다채널 → 스테레오 접기 규칙 — **ffmpeg swresample(rematrix.c) 기본 동작과 같게** (2026-09-16 사용자 결정).

왜 ffmpeg 인가
  일반 플레이어(foobar·크롬·VLC·윈도우 오디오 엔진)가 5.1 을 스테레오 장치로 내보낼 때
  듣는 소리가 이것이다. 계수(ITU-R BS.775)는 어디나 같지만 **전체 레벨**은 갈린다 —
  DAW(Nuendo MixConvert·Pro Tools)는 안 낮추고 마스터링에서 처리하고, ffmpeg·윈도우는
  계수 합으로 나눠(normalize) 절대 넘치지 않게 한다. 우리는 검색 도구라 "플레이어처럼"
  들리는 쪽을 택했다. 그 전(-4 dB 고정 + tanh 리미터)은 두 진영의 중간값으로 표준이
  아니었다 (BINAURAL_AUDIT_2026-09-15.md 참고).

계수 (rematrix.c 기본값 clev = slev = 1/√2, lfe_mix_level = 0)
  L' = L + 0.707·C + 0.707·Ls + 0.707·Lb + 1.0·TFL + 0.5·BC      (R' 대칭)
  · 측면(LS/RS)과 후방(LB/RB) 은 **둘 다** slev 로 같은 쪽에.
  · 뒤 센터(BC) 는 slev·1/√2 = 0.5 씩 양쪽에.
  · 높이 앞(TFL/TFR) 은 ffmpeg 가 계수 1.0 으로 같은 쪽 앞에 더한다.
  · LFE 는 버린다.
레벨 (normalize)
  출력 행별 |계수| 합의 **최댓값**으로 전체를 나눈다 → 입력이 풀스케일이어도 출력이
  1.0 을 넘을 수 없다. 리미터가 필요 없다 (그래서 없다).
  5.1/5.0 → 1/2.414 = -7.66 dB · 7.1/7.0 → 1/3.121 = -9.89 dB · quad → 1/1.707 = -4.65 dB

이 모듈은 numpy·PyQt 에 의존하지 않는다 — audio_engine(재생)과 binaural(바이노럴 음량
보정)이 **같은 표**를 보게 하는 게 목적이다.
"""
from __future__ import annotations

import math

DOWNMIX_L = {"L": 1.0, "C": 0.707, "LS": 0.707, "LB": 0.707, "TFL": 1.0, "BC": 0.5}
DOWNMIX_R = {"R": 1.0, "C": 0.707, "RS": 0.707, "RB": 0.707, "TFR": 1.0, "BC": 0.5}


def row_sums(roles) -> tuple[float, float]:
    """역할 목록(파일 채널 순서)에서 왼쪽/오른쪽 출력의 |계수| 합."""
    left = sum(DOWNMIX_L.get(r, 0.0) for r in roles)
    right = sum(DOWNMIX_R.get(r, 0.0) for r in roles)
    return left, right


def legacy_row_sum(channels: int) -> float:
    """역할을 모를 때(앰비소닉·판정 실패) audio_engine 이 쓰는 옛 식의 계수 합.
    4ch: 앞 + 0.707·뒤 / 6ch 이상: 앞 + 0.707·C + 0.707·서라운드 / 3~5ch: 앞 + 0.707·C."""
    if channels == 4:
        return 1.0 + 0.707
    if channels >= 6:
        return 1.0 + 0.707 + 0.707
    if channels >= 3:
        return 1.0 + 0.707
    return 1.0


def norm_gain(roles, channels: int) -> float:
    """접은 결과에 곱할 선형 이득 (ffmpeg normalize). 항상 ≤ 1."""
    if roles and len(roles) == channels:
        total = max(row_sums(roles))
    else:
        total = legacy_row_sum(channels)
    return 1.0 / total if total > 1.0 else 1.0


def norm_db(roles, channels: int) -> float:
    """같은 것을 dB 로 (5.1 → -7.66)."""
    return 20.0 * math.log10(norm_gain(roles, channels))


# ── 모노로 합치기 (Sum to Mono, 사용자 결정 2026-10-14 — '업계 표준 방식으로') ──────────────
# 스피커 배치: ITU-R BS.775 모노 다운믹스 계수 (ffmpeg rematrix 의 모노 출력과 같은 비율).
#   앞 L·R 0.707 · 센터 1.0 · 서라운드(옆·뒤) 0.5 · **LFE 는 버린다**(업계 공통).
#   계수 합으로 나눠 **가중 평균**을 낸다 → 입력이 풀스케일이어도 넘치지 않는다.
#   스테레오는 (L + R) ÷ 2 (편집기들의 '모노로 합치기' 기본값과 같다).
# 앰비소닉: 전 방향 채널(W) 하나 — B 포맷에서 모노를 얻는 표준. X·Y·Z 는 방향 성분이라 더하면 망가진다.
#   FuMa 는 W 를 −3 dB 낮춰 저장하는 규격이라 √2 를 곱해 AmbiX(SN3D) 와 같은 크기로 맞춘다.
# 배치를 모를 때(일반 4채널·판정 실패): 모든 채널의 평균.
MONO_GAIN = {"L": 0.707, "R": 0.707, "C": 1.0, "LFE": 0.0,
             "LS": 0.5, "RS": 0.5, "LB": 0.5, "RB": 0.5, "BC": 0.707,
             "TFL": 0.5, "TFR": 0.5}
FUMA_W_GAIN = math.sqrt(2.0)


def mono_weights(topology: str, preset: str, roles, channels: int) -> list:
    """파일 채널 순서대로의 모노 합치기 가중치. 곱해서 더하면 모노 한 채널이 된다."""
    n = int(channels or 0)
    if n <= 1:
        return [1.0]
    if topology == "ambisonic":
        w = [0.0] * n
        w[0] = FUMA_W_GAIN if preset == "fuma" else 1.0
        return w
    if roles and len(roles) == n:
        raw = [MONO_GAIN.get(r, 0.5) for r in roles]
    else:
        raw = [1.0] * n
    total = sum(raw)
    if total <= 0:
        return [1.0 / n] * n
    return [x / total for x in raw]
