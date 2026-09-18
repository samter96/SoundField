import { IcoGear, IcoPlus, IcoRefresh, IcoStop, WaveMark } from "../icons";
import { pickLibraryFolder } from "../dialog";
import { useAppVersion } from "../version";
import { SEARCH_HELP } from "./SearchPanel";
import { t } from "../i18n";

type Props = {
  onOpen: (id: string) => void;
  onLibraryPicked: (path: string) => void;
  /* 원본 _check_fts_stale — index_meta 의 fts_stale 이 "1" 일 때만 복구 버튼이 뜬다 */
  ftsStale?: boolean;
  onFtsRebuild?: () => void;
  /* 원본 _do_quick_update / _do_full_update — 확인 후 인덱싱을 시작한다 */
  onQuickUpdate?: () => void;
  onFullUpdate?: () => void;
  indexingActive?: boolean;
  /** 취소를 이미 눌렀다 — 다시 못 누르게 한다 (원본 cancel_btn.setEnabled(False)) */
  cancelDisabled?: boolean;
  onCancelIndex?: () => void;
  /** 검색 힌트글의 왼쪽 위치(px) — 아래 header-hint 주석 참고 */
  hintLeft?: number;
};

export function Header({ onOpen, onLibraryPicked, ftsStale, onFtsRebuild,
                        onQuickUpdate, onFullUpdate, indexingActive = false,
                        cancelDisabled = false, onCancelIndex, hintLeft = 0 }: Props) {
  /* 버전은 실행 중인 exe 에서 읽는다 — version.ts 주석 참고 (글자로 박지 말 것) */
  const version = useAppVersion();
  return (
    <header className="header">
      <div className="brand">
        {/* 소속 표기는 윗줄 타이틀바로 옮겼다 — 여기는 제품 줄이다 */}
        <WaveMark size={17} />
        <span className="brand-name">SoundField</span>
        {version && <span className="brand-ver">V{version}</span>}
      </div>

      <button className="settings-btn" data-tip={t("환경설정 (Ctrl+P)")} aria-label={t("환경설정")}
              onClick={() => onOpen("settings")}>
        <IcoGear size={14} />
      </button>

      {/* 검색 필터 힌트 — 원래 검색 칸 위에 한 줄로 있었는데, 그 줄 때문에 첫 필터
          행이 밀려 사이드바 헤더와 줄이 어긋났다. 이 줄로 올려 두면 빈 공간을 쓰고
          검색 칸은 맨 위에서 시작한다 (사용자 지시 2026-09-08).

          ⚠ 위치는 **검색 필터 열에 맞춘다.** 흐름에 그냥 두면 제품 이름 옆에 붙은
            설명처럼 보인다는 지적을 받았다 (2026-09-08). 아래 첫 필터의 [전체]
            드롭다운과 **첫 글자가 세로로 맞아야** 한다. 사이드바 폭은 사용자가
            끌어 바꾸므로 그 값을 받아서(hintLeft) 따라가게 한다.
          도움말은 전역 툴팁(data-tip)에 맡긴다 — 전용 툴팁 코드를 따로 두지 않는다. */}
      <div className="header-hint" style={{ left: hintLeft }}>
        <span>{t("Tab/Enter 필터 추가 · Backspace 이동/제거")}</span>
        <button className="help-dot" aria-label={t("검색 도움말")} data-tip={t(SEARCH_HELP)}>?</button>
      </div>

      <div className="global-loader" aria-hidden="true" />
      <div className="header-spacer" />

      <button className="abtn header-action" data-tip={t("새로운 폴더 경로를 라이브러리로 등록합니다.")}
              onClick={async () => {
                /* 원본 _add_library: QFileDialog.getExistingDirectory(self, "라이브러리 폴더 선택")
                   별도 모달이 아니라 OS 폴더 선택 창이다. */
                const picked = await pickLibraryFolder();
                if (picked) onLibraryPicked(picked);
              }}>
        <IcoPlus size={13} /> {t("라이브러리 추가")}
      </button>
      <button className="abtn header-action quick-update"
              onClick={onQuickUpdate}
              data-tip={t("최근 변경된 파일만 빠르게 감지합니다.\n새 파일/삭제된 파일만 처리되어 속도가 빠릅니다.\n메타데이터는 변경된 파일에만 다시 추출됩니다.")}>
        <IcoRefresh size={13} /> {t("빠른 갱신")}
      </button>
      <button className="abtn header-action full-update"
              onClick={onFullUpdate}
              data-tip={t("기존 인덱스 보존 + 모든 파일 강제 재스캔.\n라이브러리 추가와 동일한 속도. mtime 같으면 메타는 보존.")}>
        <IcoRefresh size={13} /> {t("전체 갱신")}
      </button>
      <button className="abtn header-action danger" disabled={!indexingActive || cancelDisabled}
              onClick={onCancelIndex}
              data-tip={t("인덱싱 중지. 이미 스캔된 파일은 보관됨 — [전체 인덱스 업데이트] 다시 클릭하면 이어서 진행.")}>
        <IcoStop size={10} /> {t("취소")}
      </button>
      {/* 원본 fts_rebuild_btn — 검색 색인이 파일 목록과 어긋났을 때만 나타난다
          (objectName "ftsRecovery", 평소 setVisible(False)). */}
      {ftsStale && (
        <button className="fts-recovery"
                data-tip={t("검색 데이터가 실제 파일 목록과 어긋나 이 버튼이 나타났습니다.\n"
                  + "(보통 인덱싱 도중 앱이 꺼진 경우 — 일부 파일이 검색에 안 나올 수 있음)\n"
                  + "누르면 검색 데이터를 다시 만들어 파일 목록과 맞춥니다.")}
                onClick={onFtsRebuild}>
          {t("⚠ 검색 정리")}
        </button>
      )}
    </header>
  );
}
