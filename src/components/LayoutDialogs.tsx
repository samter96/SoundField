import { useState } from "react";
import { t } from "../i18n";

/* ── 채널 배치 A/B 비교 창 두 개 (원본 player_widget.py:2908, 2976) ────────────
   둘 다 **비모달**(setModal(False))이라 창을 열어둔 채 재생/조작이 계속 된다.
   버튼을 누르면 그 배치로 **처음부터** 미리듣기가 시작되고(previewRequested),
   [이 방식 사용]/[현재 규격 저장] 을 누를 때만 저장한다(saveRequested).
   저장 전까지는 미리듣기 값이므로 파일에 남지 않는다.

   ① _AmbisonicFormatDialog — "앰비소닉 규격 선택", 최소 폭 510
      제목    "{파일명}\n\n{사유}"
      안내    "파일명만으로 AmbiX와 FuMa를 구분할 수 없습니다. 두 규격을 번갈아
               들어보고 방향과 공간감이 자연스러운 쪽을 저장하세요."
      버튼    "AmbiX로 들어보기" / "FuMa로 들어보기" (토글, 하나만 눌린 상태)
      상태    처음 "아직 미리 들을 규격을 선택하지 않았습니다",
               고르면 "현재 미리듣기 · 1차 AmbiX|1차 FuMa · 처음부터 재생 중"
               (가운데 정렬, #aeb4bf)
      하단    "나중에" / "현재 규격 저장"(고르기 전까지 비활성)

   ② _ChannelOrderDialog — "채널 배치 비교해서 듣기", 최소 폭 520
      비교 대상은 wave ↔ film 두 개다.
      버튼 문구는 **실제 채널 순서를 밝힌다** — "SMPTE 방식으로 듣기 (L R C Ls Rs)".
      "현재 방식/다른 방식"은 무엇이 어떻게 다른지 알려주지 않아 바꿨다.
      순서 이름과 나열은 파이썬(app.binaural 의 _PRESET_WAVE_ORDER/_PRESET_FILM_ORDER)이
      만들어 order_choices 로 내려 준다 — 표를 여기에 복제하면 어긋난다.
      ⚠ wave 를 SMPTE 라고 부를 수 있는 건 5.1 까지다. 7.1 부터는 측면/후방 순서가
        뒤집혀 달라서 "WAV 규격"으로 부른다 (Avid KB SMPTE-file-FAQ).

      unverified=true (파일에 채널 이름표가 없어 자동으로 뜬 경우)
        제목    "채널 순서 확인 필요"
        본문    "이 파일에는 채널 이름 정보가 없어 순서를 확인할 수 없습니다.
                 지금은 {이름} 순서로 재생 중이며, 맞는지 확인되지 않았습니다."
        하단    왼쪽에 "이 폴더 전체에 적용 · {폴더명}"
      unverified=false (메뉴에서 직접 연 경우) — 기존 문구 유지
      하단    "나중에" / "이 방식 사용"(고르기 전까지 비활성)
      저장값  "{preset}|{wave|film}" — 프리셋과 채널 순서를 함께 담는다. */

type Common = {
  fileName: string;
  onPreview: (value: string) => void;
  onSave: (value: string) => void;
  onClose: () => void;
};

export function AmbisonicFormatDialog({ fileName, reason, onPreview, onSave, onClose }:
                                      Common & { reason: string }) {
  const [preset, setPreset] = useState("");
  return (
    <div className="layout-dialog ambisonic" role="dialog" aria-label="앰비소닉 규격 선택">
      <div className="layout-dialog-head">
        <span className="layout-dialog-title">앰비소닉 규격 선택</span>
        <button className="layout-dialog-x" aria-label="닫기" onClick={onClose}>✕</button>
      </div>
      <div className="layout-dialog-body">
        <div className="layout-dialog-text pre">{`${fileName}\n\n${reason}`}</div>
        <div className="layout-dialog-text">
          파일명만으로 AmbiX와 FuMa를 구분할 수 없습니다. 두 규격을 번갈아 들어보고
          방향과 공간감이 자연스러운 쪽을 저장하세요.
        </div>
        <div className="layout-dialog-row">
          {([["ambix", "AmbiX로 들어보기"], ["fuma", "FuMa로 들어보기"]] as const).map(
            ([value, label]) => (
              <button key={value}
                      className={"btn wide" + (preset === value ? " on" : "")}
                      onClick={() => { setPreset(value); onPreview(value); }}>
                {label}
              </button>
            ))}
        </div>
        <div className="layout-dialog-status">
          {preset
            ? `현재 미리듣기 · ${preset === "ambix" ? "1차 AmbiX" : "1차 FuMa"} · 처음부터 재생 중`
            : "아직 미리 들을 규격을 선택하지 않았습니다"}
        </div>
      </div>
      <div className="layout-dialog-foot">
        <div className="grow" />
        <button className="btn" onClick={onClose}>나중에</button>
        <button className="btn btn-primary" disabled={!preset}
                onClick={() => { if (preset) onSave(preset); }}>현재 규격 저장</button>
      </div>
    </div>
  );
}

export type OrderChoice = { order: string; name: string; roles: string[] };

export function ChannelOrderDialog({ fileName, preset, currentOrder, choices = [],
                                     unverified = false, folderName = "",
                                     onPreview, onSave, onClose }:
                                    Omit<Common, "onSave"> & {
                                      onSave: (value: string, scope: "file" | "folder") => void;
                                      preset: string;
                                      currentOrder: string;
                                      /** 이 프리셋의 순서 선택지 (파이썬 표가 정본) */
                                      choices?: OrderChoice[];
                                      /** 파일에 채널 이름표가 없어 자동으로 뜬 경우 */
                                      unverified?: boolean;
                                      /** 폴더 단위로 저장될 때 그 폴더 이름 */
                                      folderName?: string;
                                    }) {
  /* 선택지가 없으면(구버전 브리지 등) 현재/반대 두 개로 되돌린다 */
  const items: OrderChoice[] = choices.length ? choices : [
    { order: "wave", name: "SMPTE", roles: [] },
    { order: "film", name: "Film", roles: [] },
  ];
  const [selected, setSelected] = useState("");
  const [scope, setScope] = useState<"file" | "folder">("file");
  const labelOf = (c: OrderChoice) =>
    c.roles.length ? `${c.name} 방식으로 듣기 (${c.roles.join(" ")})`
                   : `${c.name} 방식으로 듣기`;
  const nameOf = (order: string) =>
    items.find((c) => c.order === order)?.name ?? order;
  return (
    <div className="layout-dialog order" role="dialog"
         aria-label={unverified ? "채널 순서 확인 필요" : "채널 배치 비교해서 듣기"}>
      <div className="layout-dialog-head">
        <span className="layout-dialog-title">
          {unverified ? "채널 순서 확인 필요" : "채널 배치 비교해서 듣기"}
        </span>
        <button className="layout-dialog-x" aria-label="닫기" onClick={onClose}>✕</button>
      </div>
      <div className="layout-dialog-body">
        <div className="layout-dialog-text pre">
          {unverified
            /* 이름표가 없으면 순서를 알 길이 없다. 그 사실을 숨기지 않고 밝힌다 —
               지금까지는 규격 기본값으로 조용히 재생하고 있었다. */
            ? `${fileName}\n\n이 파일에는 채널 이름 정보가 없어 순서를 확인할 수 없습니다.`
              + `\n지금은 ${nameOf(currentOrder)} 순서로 재생 중이며, 맞는지 확인되지 않았습니다.`
            : `${fileName}\n\n두 배치를 번갈아 듣고 더 자연스러운 쪽을 저장하세요.`}
        </div>
        <div className="layout-dialog-text">
          중앙 소리가 한쪽으로 치우치지 않는지, 뒤쪽 소리가 사라지거나 반대편으로
          이동하지 않는지 확인하세요. 저음이 방향감 있는 일반 소리처럼 들리지 않는
          쪽이 올바른 배치일 가능성이 높습니다.
        </div>
        <div className="layout-dialog-row">
          {items.map((choice) => (
            <button key={choice.order}
                    className={"btn wide" + (selected === choice.order ? " on" : "")}
                    onClick={() => {
                      setSelected(choice.order);
                      onPreview(`${preset}|${choice.order}`);
                    }}>
              {labelOf(choice)}
              {choice.order === currentOrder && <span className="sub"> · 지금 이 방식</span>}
            </button>
          ))}
        </div>
        <div className="layout-dialog-status">
          {selected
            ? `${nameOf(selected)} 방식 · 현재 위치에서 비교 중`
            : "두 방식을 눌러 번갈아 들어보세요"}
        </div>
      </div>
      <div className="layout-dialog-foot">
        {/* 폴더 단위로 저장될 때는 그 사실을 버튼 누르기 전에 알린다 */}
        {folderName
          ? <label className="layout-dialog-scope"
                   data-tip="폴더를 고르면 같은 폴더 안에서 채널 수가 같은 파일에만 적용됩니다">
              적용 범위{" "}
              <select value={scope} onChange={(e) => setScope(e.target.value as "file" | "folder")}>
                <option value="file">이 파일만</option>
                <option value="folder">이 폴더 ({folderName})</option>
              </select>
            </label>
          : <div className="grow" />}
        {folderName ? <div className="grow" /> : null}
        <button className="btn" onClick={onClose}>나중에</button>
        <button className="btn btn-primary" disabled={!selected}
                onClick={() => { if (selected) onSave(`${preset}|${selected}`, scope); }}>
          이 방식 사용
        </button>
      </div>
    </div>
  );
}

/* ── IEM 플러그인 설치 안내 (원본 _show_iem_install_popup, player_widget.py) ──
   제목 "IEM 플러그인 설치 필요".
   본문 "IEM MultiEncoder와 BinauralDecoder를 찾지 못했습니다.
         MultiEncoder와 BinauralDecoder는 무료·오픈소스 IEM Plug-in Suite에
         함께 포함되어 있습니다.
         검증 버전: IEM Plug-in Suite v{버전}
         설치를 마친 뒤 SoundField를 다시 실행하세요."
   버튼 "무료 설치 페이지 열기" / "닫기". */
export function IemInstallDialog({ version, reason, onOpenPage, onClose }: {
  version: string;
  /** 실제 사용 불가 사유 — 미설치와 버전 불일치를 구분해 보여 준다 (조사 Q02) */
  reason?: string;
  onOpenPage: () => void;
  onClose: () => void;
}) {
  return (
    <div className="layout-dialog iem" role="dialog" aria-label="IEM 플러그인 설치 필요">
      <div className="layout-dialog-head">
        <span className="layout-dialog-title">IEM 플러그인 설치 필요</span>
        <button className="layout-dialog-x" aria-label="닫기" onClick={onClose}>✕</button>
      </div>
      <div className="layout-dialog-body">
        <div className="layout-dialog-text pre">
          {/* 사유가 오면 그것을 첫 줄로 쓴다 — 미설치와 **버전 불일치**의 문구가 다르고,
              원본 friendly_ready_error 가 이미 두 경우를 구분해 문장을 만든다 (조사 Q02). */
          (reason ? `${t(reason)}\n\n`
                  : `${t("IEM MultiEncoder와 BinauralDecoder를 찾지 못했습니다.")}\n`)
            + t("MultiEncoder와 BinauralDecoder는 무료·오픈소스 IEM Plug-in Suite에 함께 포함되어 있습니다.")
            + "\n\n"
            + `${t("검증 버전: IEM Plug-in Suite v{0}", version)}\n`
            + t("설치를 마친 뒤 SoundField를 다시 실행하세요.")}
        </div>
      </div>
      <div className="layout-dialog-foot">
        <div className="grow" />
        <button className="btn btn-primary" onClick={onOpenPage}>무료 설치 페이지 열기</button>
        <button className="btn" onClick={onClose}>닫기</button>
      </div>
    </div>
  );
}

/* ── 채널 배치 확인 필요 안내 (원본 _show_layout_required_popup, player_widget.py:3728) ──
   판정이 {AmbiX, FuMa} 두 개로만 갈릴 때는 ①번 규격 선택 창이 뜨고, 그 밖의 경우
   (스피커 배치 후보들) 이 경고창이 뜬다. **비모달**이고 파일당 한 번만 뜬다.
   본문(원본 setText):
     "{파일명}
      {판정 사유}
      파일 내부 채널 정보와 파일명만으로 배치를 확정하지 못했습니다.
      현재는 일반 스테레오로 재생하며 바이노럴 설정은 켜진 상태로 유지됩니다."
   안내(원본 setInformativeText):
     "가능성이 높은 배치를 하나씩 선택해 들어보세요.
      {선택지 목록}
      소리가 스피커별로 분리된 일반 멀티채널이면 스피커 배치를, 앰비소닉 B-format이면
      AmbiX를 선택하세요.
      둘 다 자연스럽지 않으면 임의로 확정하지 말고 스테레오 상태를 유지한 뒤 원본 제작
      정보와 채널 규약을 확인하세요."
   사유에 "A-format" 이 들어가면 AMBEO 원본 전용 문구로 갈린다.
   선택지: 9채널이면 고정 두 줄(7.0.2 / 2차 AmbiX), 그 밖에는 후보 라벨 목록.
   버튼: [채널 배치 선택] (누르면 배치 메뉴를 연다) / [나중에] */
export function LayoutRequiredDialog({ fileName, reason, choices, onSelect, onClose }: {
  fileName: string;
  reason: string;
  choices: string[];
  onSelect: () => void;
  onClose: () => void;
}) {
  const aformat = reason.includes("A-format");
  const choiceText = choices.map((c) => `• ${c}`).join("\n");
  return (
    <div className="layout-dialog required" role="dialog" aria-label="채널 배치 확인 필요">
      <div className="layout-dialog-head">
        <span className="layout-dialog-title">채널 배치 확인 필요</span>
        <button className="layout-dialog-x" aria-label="닫기" onClick={onClose}>✕</button>
      </div>
      <div className="layout-dialog-body">
        <div className="layout-dialog-text pre">
          {`${fileName}\n\n${reason ? reason + "\n" : ""}`
            + (aformat
              ? "AMBEO 마이크 원본은 곧바로 바이노럴로 변환할 수 없습니다.\n"
              : "파일 내부 채널 정보와 파일명만으로 배치를 확정하지 못했습니다.\n")
            + "현재는 일반 스테레오로 재생하며 바이노럴 설정은 켜진 상태로 유지됩니다."}
        </div>
        <div className="layout-dialog-text pre secondary">
          {aformat
            ? "제작 과정에서 이미 AmbiX 또는 FuMa B-format으로 변환된 파일인지 먼저 "
              + "확인하세요. 변환된 파일이 확실할 때만 해당 규격을 선택해 들어보세요."
              + `\n\n확인 가능한 선택지\n${choiceText}\n\n`
              + "원본 A-format이라면 이 메뉴에서 임의로 배치를 지정하지 않는 것이 안전합니다."
            : `가능성이 높은 배치를 하나씩 선택해 들어보세요.\n\n${choiceText}\n\n`
              + "소리가 스피커별로 분리된 일반 멀티채널이면 스피커 배치를, "
              + "앰비소닉 B-format이면 AmbiX를 선택하세요.\n"
              + "둘 다 자연스럽지 않으면 임의로 확정하지 말고 스테레오 상태를 유지한 뒤 "
              + "원본 제작 정보와 채널 규약을 확인하세요."}
        </div>
      </div>
      <div className="layout-dialog-foot">
        <div className="grow" />
        <button className="btn btn-primary" onClick={onSelect}>채널 배치 선택</button>
        <button className="btn" onClick={onClose}>나중에</button>
      </div>
    </div>
  );
}
