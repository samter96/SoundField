"""SoundField 원본의 쓰기 정책을 Tauri에서 호출하는 작은 브리지."""
import json
import os
import sys
from pathlib import Path

try:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ── 원본 모듈 위치 ─────────────────────────────────────────────────────────────
# ⚠ **포장된 빌드에서는 원본 소스 트리를 sys.path 에 넣지 않는다.**
#   넣으면 번들 안 사본이 아니라 개발 PC 의 소스 트리에서 app.* 을 읽는다.
#   그러면 "번들에 파일이 빠졌다" 같은 결함이 개발 PC 에서만 가려진다 —
#   실측 2026-09-07: UCS 동의어 사전이 번들에서 빠졌는데도 이 PC 에서는 소스
#   트리에서 읽혀 정상으로 보였고, 다른 PC 에서만 검색이 좁아졌다.
#   개발(비포장) 실행에서는 소스 트리가 필요하므로 그때만 넣는다.
#   SOUNDFIELD_PY_ROOT 로 명시하면 포장 빌드에서도 그 경로를 쓴다(진단용).
_EXPLICIT_ROOT = os.environ.get("SOUNDFIELD_PY_ROOT")
if _EXPLICIT_ROOT:
    PY_ROOT = Path(_EXPLICIT_ROOT)
elif getattr(sys, "frozen", False):
    PY_ROOT = Path(getattr(sys, "_MEIPASS", "."))
else:
    PY_ROOT = Path(__file__).resolve().parents[2] / "py"
if not getattr(sys, "frozen", False) or _EXPLICIT_ROOT:
    if str(PY_ROOT) not in sys.path:
        sys.path.insert(0, str(PY_ROOT))

from app.database import Database  # noqa: E402
from app.library_manager import LibraryManager  # noqa: E402
from app.metadata import META_PARSER_VERSION  # noqa: E402

STORE = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "SoundField"


def emit_progress(progress):
    data = {
        "phase": str(getattr(progress, "phase", "")),
        "total": int(getattr(progress, "total", 0) or 0),
        "scanned": int(getattr(progress, "scanned", 0) or 0),
        "indexed": int(getattr(progress, "indexed", 0) or 0),
        "skipped": int(getattr(progress, "skipped", 0) or 0),
        "errors": int(getattr(progress, "errors", 0) or 0),
        "current_file": str(getattr(progress, "current_file", "") or ""),
        "current_root": str(getattr(progress, "current_root", "") or ""),
        "message": str(getattr(progress, "message", "") or ""),
        "new_count": int(getattr(progress, "new_count", 0) or 0),
        "phase1_total": int(getattr(progress, "phase1_total", 0) or 0),
        "fts_total": int(getattr(progress, "fts_total", 0) or 0),
        "fts_done": int(getattr(progress, "fts_done", 0) or 0),
        "percent": int(getattr(progress, "percent", 0) or 0),
        "eta_seconds": getattr(progress, "eta_seconds", None),
        # 2단계 세션 누적 — indexed 는 **이번 조각**만 센다. total 은 세션 분모라
        # (index_meta.meta_session_total) 분자도 세션 누적이어야 짝이 맞는다.
        "base_done": int(getattr(progress, "base_done", 0) or 0),
        "meta_done": int(getattr(progress, "meta_done", 0) or 0),
    }
    print(json.dumps({"type": "progress", "data": data}, ensure_ascii=False), flush=True)


def emit_op_progress(op, done, total):
    """관리 작업 하나의 진행 상황 — 인덱싱 진행과 **다른 통로**로 보낸다.

    ⚠ `{"type": "progress"}` 를 쓰지 말 것. 그 줄은 Rust 가 index-event 로 넘기고
      메인 창의 **인덱싱 진행 표시**가 켜진다 — 검색 제외를 눌렀는데 인덱싱이
      도는 것처럼 보이게 된다. 이 통로는 그 창 안에서만 쓴다.
    """
    print(json.dumps({"type": "op_progress", "data": {
        "op": str(op), "done": int(done or 0), "total": int(total or 0),
    }}, ensure_ascii=False), flush=True)


def emit_fts_progress(done, total):
    print(json.dumps({"type": "progress", "data": {
        "phase": "scan", "total": 0, "scanned": 0, "indexed": 0,
        "skipped": 0, "errors": 0, "current_file": "", "current_root": "",
        "message": "검색 색인 마무리 중...", "new_count": 0,
        "phase1_total": 0, "fts_total": int(total or 0),
        "fts_done": int(done or 0), "percent": 0, "eta_seconds": None,
    }}, ensure_ascii=False), flush=True)


def clean_result(value):
    if not isinstance(value, dict):
        return value
    out = {k: v for k, v in value.items() if k != "progress"}
    progress = value.get("progress")
    orphan_ids = list(getattr(progress, "orphan_dup_ids", None) or [])
    if orphan_ids:
        out["orphan_ids"] = orphan_ids
    # 원본 _on_index_done / _on_background_meta_done 이 완료 팝업 요약과
    # "2단계 완료 팝업을 띄울지"(new_count > 0) 판단에 쓰는 값들.
    # progress 객체를 그대로 버리면 프런트가 이 숫자를 알 수 없다.
    for key in ("scanned", "new_count", "indexed", "errors", "total"):
        value_of = getattr(progress, key, None)
        if value_of is not None:
            out.setdefault(key, int(value_of))
    return out


def with_retry_outcome(db, result):
    """재시도 결과에 성공/실패/사유별 개수를 붙인다 (조사 I05).
    원본 _show_retry_result (main_window.py:7032) 와 같은 계산이다:
      · 성공 수는 **진행 객체의 indexed** 로 센다 — 성공한 행은 phase2_run_id 가
        비워지므로 DB 집계로는 셀 수 없다.
      · 실패 수와 사유는 그 번호표(run_id)로 DB 에서 집계한다.
    예전에는 "재분석을 완료했습니다" 만 알려, 호출은 성공했지만 파일 상당수가
    계속 실패한 상황을 완료 창에서 알 수 없었다."""
    out = clean_result(result)
    progress = result.get("progress") if isinstance(result, dict) else None
    run_id = str(getattr(progress, "phase2_run_id", "") or "")
    if not run_id:
        return out
    try:
        outcome = db.phase2_run_outcome(run_id)
    except Exception:
        return out
    out["retry"] = {
        "success": int(getattr(progress, "indexed", 0) or 0),
        "failed": int(outcome.get("failed") or 0),
        "reasons": [[str(err), int(count)] for err, count in (outcome.get("reasons") or [])],
    }
    return out


_CACHE = {}


_PREWARMED = None       # threading.Event — main() 에서 만든다


def prewarm():
    """완전 제거 경로가 쓰는 app.ui.player_widget(→ numpy/PyQt) 을 미리 올린다.
    실측 0.89초. 예전에는 이걸 **동기로** 먼저 해서 첫 요청(보통 인덱싱, 이 모듈이
    필요 없다)이 그 시간을 그대로 기다렸다 → 백그라운드 스레드로 옮겼다.
    필요한 작업(완전 제거)만 _PREWARMED 로 기다린다.

    ⚠ 리더 스레드가 `for line in sys.stdin` 이면 블로킹 중 TextIOWrapper 락을 계속
    잡아 이 import 가 그 락에서 멈춘다 (실측: 0.89초 → 55초). 리더는 os.read 로
    fd 0 을 직접 읽어 락 자체를 피한다."""
    try:
        from app.ui.player_widget import PEAKS_CACHE_DIR, _peaks_cache_path  # noqa: F401
    except Exception:
        pass
    if _PREWARMED is not None:
        _PREWARMED.set()


def wait_prewarm(timeout=30.0):
    """player_widget 이 필요한 작업 전에만 기다린다."""
    if _PREWARMED is not None:
        _PREWARMED.wait(timeout)


def get_manager():
    """LibraryManager 생성은 _bootstrap_db() 에서 스키마 마이그레이션 + 인덱스 검증을
    한다 — 실측 **2.04초** (실DB 150만 행). 원본은 앱 시작 때 한 번만 만들지만,
    PoC 는 요청마다 새 프로세스라 매번 다시 했다 (사용자 체감: 파일 1개 인덱싱도 느림).
    상주 프로세스에서 **manager 만** 재사용한다.

    ⚠ 쓰기 가능한 Database 연결은 **캐시하지 않는다.** 캐시했더니 앞선 요청이 남긴
    트랜잭션을 물고 있어, 다음 요청의 쓰기(purge 등)가 무한 대기했다
    (실측: 단독 프로세스 4.0초 → 상주에서 280초 초과). 연결 생성 자체는 0.00초다."""
    manager = _CACHE.get("manager")
    if manager is None:
        manager = LibraryManager(STORE)
        _CACHE["manager"] = manager
    return manager, Database(str(manager.db_path))


def run(req):
    op = str(req.get("op") or "")
    manager, db = get_manager()
    paths = [str(p) for p in (req.get("paths") or []) if p]
    path = str(req.get("path") or "")
    if op == "add_index":
        manager.add_root(path)
        for child in paths:
            if os.path.normcase(os.path.normpath(child)) != os.path.normcase(os.path.normpath(path)):
                manager.remove_root_only(child)
        return clean_result(manager.update_index(
            scan_path=path, progress_cb=emit_progress, run_phase2=False))
    if op == "index":
        targets = paths or ([path] if path else [None])
        results = []
        cancel = getattr(manager, "_cancel", None)
        for target in targets:
            results.append(clean_result(manager.update_index(
                scan_path=target, force_rescan=bool(req.get("force")),
                progress_cb=emit_progress, run_phase2=False)))
            # 취소했으면 남은 폴더는 시작하지 않는다 (원본 _on_index_done 은
            # _cancelling 이면 _pending_index_jobs 를 폐기한다, main_window.py:6954).
            # 예전에는 첫 폴더에서 취소해도 다음 폴더 스캔이 곧바로 시작됐다
            # (update_index 가 시작할 때 취소 플래그를 지우기 때문).
            if cancel is not None and cancel.is_set():
                break
        return {"success": all(r.get("success", False) for r in results),
                "cancelled": bool(cancel is not None and cancel.is_set()),
                "results": results}
    if op == "remove_roots":
        total = 0
        details = []
        cache_deleted = 0
        for target in paths:
            if req.get("purge"):
                # 원본 PurgeLibraryWorker: DB 행을 지우기 전에 캐시 키를 계산한다.
                wait_prewarm()          # 백그라운드 prewarm 이 끝난 뒤 import (중복 로드 방지)
                from app.ui.player_widget import PEAKS_CACHE_DIR, _peaks_cache_path
                prefix = os.path.normpath(target).rstrip("\\/") + os.sep
                cache_hits = []
                try:
                    cache_names = {f.name for f in PEAKS_CACHE_DIR.iterdir()
                                   if f.suffix == ".bin"}
                    db_ro = manager.open_db_for_search()
                    for batch in db_ro.iter_path_size_mtime_under(prefix):
                        for file_path, size, mtime in batch:
                            if size is None or mtime is None:
                                continue
                            cached = _peaks_cache_path(
                                file_path, stat_hint=(size, mtime), allow_stat=False)
                            if cached is not None and cached.name in cache_names:
                                cache_hits.append(cached)
                except OSError:
                    cache_hits = []
                result = manager.purge_root(target)
                total += int(result.get("deleted", 0) or 0)
                details.append(result)
                for cached in cache_hits:
                    try:
                        cached.unlink()
                        cache_deleted += 1
                    except OSError:
                        pass
            else:
                count, orphan_ids = manager.remove_root(target)
                total += int(count)
                details.append({"removed": count, "orphan_ids": orphan_ids})
        if req.get("purge"):
            # 재생 히스토리에서도 완전 제거한 루트 이하만 걷어낸다.
            history_path = STORE / "history.json"
            try:
                history = json.loads(history_path.read_text(encoding="utf-8"))
                prefixes = [os.path.normcase(os.path.normpath(p)).rstrip("\\/") + os.sep
                            for p in paths]
                kept = [p for p in history if not any(
                    os.path.normcase(os.path.normpath(str(p))).startswith(pre)
                    for pre in prefixes)]
                history_path.write_text(json.dumps(kept[-100:], ensure_ascii=False, indent=2),
                                        encoding="utf-8")
            except (OSError, ValueError, TypeError):
                pass
        return {"success": True, "count": total, "details": details,
                "cache_deleted": cache_deleted}
    if op == "repair_fts":
        # 색인 어긋남 자동 수리 — 원본 _start_fts_repair (main_window.py:5738).
        # 누락 채우기 + 유령 행 정리 + 개수 재검증까지 한다.
        return clean_result(manager.repair_fts(progress_cb=emit_fts_progress))
    if op == "backfill_fts":
        # 헤더 [검색 정리] 버튼 — 원본 FTSRebuildWorker (main_window.py:2950) 와
        # 같은 알고리즘이다. 누락분만 채우고(전체 재빌드 아님) **취소를 받는다**.
        # ⚠ 취소해도 이미 채운 부분은 그대로 유효하다 (INSERT OR REPLACE, 배치마다
        #   커밋). 개수가 맞아떨어질 때만 '어긋남' 표시를 내린다 — 취소하면 표시가
        #   남아 다음에 이어서 채울 수 있다.
        cancel = getattr(manager, "_cancel", None)
        inserted = db.backfill_missing_fts(
            progress_cb=emit_fts_progress, cancel_event=cancel)
        if cancel is not None and cancel.is_set():
            return {"success": False, "inserted": int(inserted),
                    "message": "검색 정리를 멈췄습니다 — 채운 부분(%s건)은 그대로 남습니다"
                               % format(int(inserted), ",")}
        return {"success": True, "inserted": int(inserted),
                "message": "검색 정리 완료 (+%s건)" % format(int(inserted), ",")}
    if op == "detect":
        # 빠른 갱신의 1단계. 원본처럼 DB에는 쓰지 않고 변경 통계/예시만 산정한다.
        return clean_result(manager.detect_changes(
            scan_path=path or None, progress_cb=emit_progress))
    if op == "parser_upgrade":
        # 원본 _retry_failed_on_parser_upgrade. 같은 파서 버전에서는 정확히 한 번만 돈다.
        current = str(META_PARSER_VERSION)
        previous = db.get_meta("meta_parser_version")
        if previous == current:
            return {"success": True, "reset": 0, "restored": 0}
        restored = db.restore_removed_failed() if previous is None else 0
        reset = db.reset_all_failed_metadata()
        db.set_meta("meta_parser_version", current)
        return {"success": True, "reset": reset, "restored": restored}
    if op == "ensure_term_index":
        # 정확 검색용 단어 색인. 이미 있으면 쓰기 없이 즉시 끝난다.
        if db.term_index_count() > 0:
            return {"success": True, "built": False}
        return {"success": bool(db.build_term_index()), "built": True}
    if op == "background_meta":
        # 원본 MainWindow._ensure_background_meta_running 이 호출하는 전용 경로.
        # 실패 항목을 임의로 pending 으로 되돌리지 않고, 현재 meta_extracted=0 큐만
        # 이어서 비운다. 강제 종료되어도 다음 실행이 남은 큐부터 계속한다.
        result = clean_result(manager.update_metadata_background(progress_cb=emit_progress))
        # 원본은 update_index 의 finally 에서만 checkpoint 한다 (library_manager.py:414).
        # 2단계는 대량 UPDATE 인데도 그 경로를 안 지나 WAL 이 계속 커진다 —
        # 커진 WAL 은 **새 연결의 첫 접근을 수 초 지연**시킨다(원본 주석이 명시).
        # 읽는 연결이 있으면 부분만 반영되고 무해하므로 여기서도 걷어낸다.
        try:
            db.checkpoint()
        except Exception:
            pass
        return result
    if op == "retry_paths":
        return with_retry_outcome(db, manager.update_index(
            phase2_only=True, reset_scope_paths=paths,
            reset_include_pending=True, reset_include_failed=True,
            progress_cb=emit_progress))
    if op == "retry_scope":
        return with_retry_outcome(db, manager.update_index(
            phase2_only=True, reset_scope_prefix=path,
            reset_include_pending=bool(req.get("include_pending", True)),
            reset_include_failed=bool(req.get("include_failed", True)),
            progress_cb=emit_progress))
    if op == "delete_paths":
        count, orphan_ids = db.delete_files_by_paths(paths)
        return {"success": True, "count": count, "orphan_ids": orphan_ids}
    if op == "delete_scope":
        count, orphan_ids = db.delete_incomplete_under(
            path, include_pending=bool(req.get("include_pending", True)),
            include_failed=bool(req.get("include_failed", True)))
        return {"success": True, "count": count, "orphan_ids": orphan_ids}
    if op == "hide_paths":
        # 44만 건이 걸릴 수 있어 진행 상황을 알려야 한다 (원본도 500개 묶음마다
        # progress_cb 를 부르고 "검색 제외 중... N / M (X%)" 를 보여준다).
        # 콜백을 빼면 화면이 끝날 때까지 아무 변화 없이 멈춘 것처럼 보인다.
        return {"success": True, "count": db.hide_files(
            paths, progress_cb=lambda done, total: emit_op_progress(op, done, total))}
    # 복원도 수십만 건이 걸릴 수 있다 — 검색 제외와 **같은 통로**로 진행 상황을 보낸다
    # (사용자 지시 2026-09-08: 비슷한 작업은 같은 정책으로).
    if op == "unhide_paths":
        return {"success": True, "count": db.unhide_files(
            paths, progress_cb=lambda done, total: emit_op_progress(op, done, total))}
    if op == "unhide_all":
        # ⚠ unhide_all 은 단일 UPDATE 라 중간 진행률을 만들 수 없다 (원본 그대로).
        #   total 0 을 보내 화면이 "총량 미정" 표시(흐르는 진행바)를 쓰게 한다.
        #   진행률을 만들려고 이 함수를 쪼개지 말 것 — 한 트랜잭션이라 중단 안전성이
        #   보장되는 지금 구조가 낫다.
        emit_op_progress(op, 0, 0)
        return {"success": True, "count": db.unhide_all()}
    if op == "unhide_ids":
        return {"success": True, "count": db.unhide_by_ids(
            paths, progress_cb=lambda done, total: emit_op_progress(op, done, total))}
    if op == "mark_dup_orphans":
        return {"success": True, "count": db.mark_dup_orphans(paths)}
    raise ValueError(f"지원하지 않는 관리 작업: {op}")


def main():
    """상주 서비스 — 한 줄에 요청 하나(JSON), 결과도 한 줄.
    · LibraryManager 를 프로세스 수명 동안 재사용해 매 요청 2초 비용을 없앤다.
    · {"op":"cancel"} 은 **별도 스레드**가 즉시 manager.cancel() 을 호출한다
      (메인 스레드는 인덱싱 중이라 큐를 못 읽는다). 원본도 협조적 취소다.
    · 부모가 죽어 stdin 이 닫히면(EOF) 스스로 종료한다 (고아 방지)."""
    # 인덱싱 처리량 로그 — 원본 library_manager 는 이미
    #   Phase2: {처리}/{전체} ({X} f/s)
    #   FTS bulk insert {N} 신규 경로 ({X}s)
    #   Phase2 시작 - pending_total={N}
    # 을 logger 로 남기는데, 사이드카에 **핸들러가 없어서 전부 버려졌다.**
    # 그래서 인덱싱이 끝난 뒤 처리량을 확인할 방법이 없었고, 진행률/속도 문제를
    # 사후 진단하려면 index_meta 타임스탬프를 역산해야 했다 (2026-09-07, 두 번 막힘).
    # 원본 app/main.py 의 setup_logging 을 사이드카는 지나지 않으므로 여기서 붙인다.
    # (오디오 사이드카도 같은 사고를 겪고 같은 방식으로 고쳤다 — sf_audio_service.py)
    import logging
    log_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SoundField"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=str(log_dir / "poc_index.log"), filemode="a",
            level=logging.INFO, encoding="utf-8",
            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        logging.getLogger(__name__).info("인덱싱 사이드카 시작 pid=%d", os.getpid())
    except Exception:
        pass

    import queue
    import threading

    jobs: "queue.Queue[dict]" = queue.Queue()

    def reader():
        """⚠ `for line in sys.stdin` 은 TextIOWrapper 의 락을 **블로킹 동안 계속**
        잡는다. 그 사이 메인 스레드에서 일어나는 import 가 stdin 에 접근하면 같이
        멈춘다 (실측: 완전 제거가 280초 넘게 정지). fd 0 을 직접 읽어 락을 피한다."""
        buf = b""
        while True:
            try:
                chunk = os.read(0, 65536)
            except OSError:
                chunk = b""
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    req = json.loads(line)
                except Exception as exc:
                    print(json.dumps({"type": "result", "data": {
                        "success": False, "message": str(exc)}}, ensure_ascii=False), flush=True)
                    continue
                if str(req.get("op") or "") == "cancel":
                    manager = _CACHE.get("manager")
                    if manager is not None:
                        try:
                            manager.cancel()
                        except Exception:
                            pass
                    print(json.dumps({"type": "cancelled"}, ensure_ascii=False), flush=True)
                    continue
                jobs.put(req)
        jobs.put(None)

    global _PREWARMED
    _PREWARMED = threading.Event()
    threading.Thread(target=reader, daemon=True).start()
    # 리더가 os.read 로 fd 0 을 직접 읽으므로 stdin 락 충돌이 없다 → 병렬 prewarm 안전
    threading.Thread(target=prewarm, daemon=True).start()
    while True:
        req = jobs.get()
        if req is None:
            return
        try:
            data = run(req)
        except Exception as exc:
            data = {"success": False, "message": str(exc)}
        # 요청이 남긴 연결/트랜잭션이 다음 요청의 쓰기를 막지 않게 정리한다
        _CACHE.pop("db", None)
        # 다음 작업을 위해 취소 플래그를 되돌린다 (원본도 작업 시작 시 초기화한다)
        manager = _CACHE.get("manager")
        if manager is not None:
            try:
                manager._cancel.clear()
            except Exception:
                pass
        print(json.dumps({"type": "result", "data": data},
                         ensure_ascii=False, default=str), flush=True)


if __name__ == "__main__":
    main()
