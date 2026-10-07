"""배포본에서 공유하는 Python 브리지 진입점."""
import os
import sys

# 일부 PC 의 PATH 에 Perforce 등 다른 제품의 Qt6Core.dll이 먼저 잡혀 있으면
# PyQt6가 그 DLL을 잘못 읽어 "지정된 프로시저를 찾을 수 없습니다"로 실패한다.
if getattr(sys, "frozen", False):
    qt_bin = os.path.join(sys._MEIPASS, "PyQt6", "Qt6", "bin")
    if os.path.isdir(qt_bin):
        os.environ["PATH"] = qt_bin + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            _qt_dll_handle = os.add_dll_directory(qt_bin)
        try:
            import ctypes
            ctypes.windll.kernel32.SetDllDirectoryW(qt_bin)
        except Exception:
            pass


def selftest():
    """번들에 **조용히 빠질 수 있는 것들**을 한 줄씩 찍는다.

    ⚠ 이 점검을 지우지 말 것. 아래 항목은 없어도 앱이 에러 없이 돌고 결과만
      나빠져서, 사람이 눈치채기까지 오래 걸린다.
      실측 2026-09-07: UCS 동의어 사전이 번들에서 빠져 있었는데 아무 에러도 없이
      **모든 검색이 동의어 확장 없이** 돌았다 (digital+notice 원본 1,047건 → 0건).

    쓰는 법: `sf_bridge.exe selftest` — 설치본에서도 그대로 된다.
    새로 조용히 빠질 수 있는 것이 생기면 여기에 한 줄 추가할 것.
    """
    lines = []

    def check(name, fn):
        try:
            ok, detail = fn()
        except Exception as exc:                               # noqa: BLE001
            lines.append((name, False, "오류 %s: %s" % (type(exc).__name__, exc)))
            return
        lines.append((name, ok, detail))

    def _thesaurus():
        """검색어 동의어 확장 — 없으면 검색 결과가 크게 좁아진다."""
        from app import thesaurus
        t = thesaurus.get()
        groups = len(getattr(t, "_groups", []))
        terms = len(getattr(t, "_term_map", {}))
        return groups > 0, "그룹 %d / 용어 %d" % (groups, terms)

    def _scandir():
        """1차 인덱싱 폴더 훑기 가속 — 없으면 조용히 느려진다."""
        from app.library_manager import RustScandir
        return RustScandir is not None, "느린 방식으로 대체됨" if RustScandir is None else "사용"

    def _binaural():
        """바이노럴 모니터 — 없으면 바이노럴이 '미설치'로 잡힌다."""
        from app.binaural import locate_sidecar
        p = locate_sidecar()
        return bool(p), str(p or "")

    def _meta():
        from app.metadata import META_PARSER_VERSION, SUPPORTED_EXTS
        return True, "파서 %s / 확장자 %d종" % (META_PARSER_VERSION, len(SUPPORTED_EXTS))

    def _mod(name):
        def run():
            m = __import__(name)
            return True, getattr(m, "__version__", "")
        return run

    def _origin():
        """app.* 을 **번들 안 사본**에서 읽는지 확인한다.

        ⚠ 이 검사를 지우지 말 것. 예전에는 사이드카가 sys.path 맨 앞에 개발 PC 의
          원본 소스 트리를 넣어서, 포장된 exe 가 번들이 아니라 **그 소스 트리**에서
          app.* 을 읽었다. 그러면 번들에 파일이 빠져도 개발 PC 에서는 정상으로
          보이고 다른 PC 에서만 깨진다 (실측 2026-09-07: UCS 동의어 사전 누락이
          이렇게 가려져 며칠간 드러나지 않았다).
        """
        import app
        where = getattr(app, "__file__", "") or (list(getattr(app, "__path__", [""])) or [""])[0]
        if not getattr(sys, "frozen", False):
            return True, "개발 실행 — %s" % where
        base = getattr(sys, "_MEIPASS", "")
        inside = bool(base) and str(where).lower().startswith(str(base).lower())
        return inside, ("번들 안 " if inside else "번들 밖(!) ") + str(where)

    check("모듈 출처", _origin)
    check("동의어 사전", _thesaurus)
    check("폴더 훑기 가속", _scandir)
    check("바이노럴 모니터", _binaural)
    check("메타 추출", _meta)
    for mod in ("numpy", "soundfile", "sounddevice", "mutagen"):
        check(mod, _mod(mod))

    failed = 0
    for name, ok, detail in lines:
        if not ok:
            failed += 1
        sys.stdout.write("%-16s %-8s %s\n" % (name, "OK" if ok else "빠짐", detail))
    sys.stdout.write("결과 %s (실패 %d)\n" % ("통과" if failed == 0 else "실패", failed))
    sys.stdout.flush()
    return 1 if failed else 0


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else ""
    if kind == "selftest":
        raise SystemExit(selftest())
    if kind == "audio":
        from sf_audio_service import main as entry
    elif kind == "waveform":
        from sf_waveform_service import main as entry
    elif kind == "query":
        from sf_query import main as entry
    elif kind == "admin":
        from sf_admin import main as entry
    else:
        raise SystemExit(f"unknown bridge kind: {kind}")
    result = entry()
    if isinstance(result, int):
        raise SystemExit(result)


if __name__ == "__main__":
    main()
