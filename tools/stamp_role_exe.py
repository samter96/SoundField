"""브리지 실행 파일 사본에 역할별 파일 설명을 박는다.

작업 관리자의 "이름" 열은 exe 의 FileDescription 을 쓴다. 그런데 같은 exe 하나로는
프로세스마다 다른 설명을 줄 수 없다 — 그래서 `_internal` 을 공유하는 사본을 역할 수
만큼 만들고, 사본마다 설명을 다르게 박는다 (사용자 요청 2026-09-03: 작업 관리자에서
어떤 역할인지 보이게).

사본이 그대로 동작하는 이유: PyInstaller onedir 부트로더는 자기 옆의 `_internal` 을
찾을 뿐 파일 이름을 보지 않는다. 진입 스크립트는 exe 에 붙어 있고 역할은 인자로 받는다.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PyInstaller.utils.win32 import versioninfo as vi


def stamp(exe: Path, description: str, template: Path) -> None:
    info = vi.load_version_info_from_text_file(str(template))
    # StringFileInfo -> StringTable -> StringStruct 만 손댄다.
    # (VarFileInfo 쪽 kids 는 정수 목록이라 name 속성이 없다.)
    replace = {
        "FileDescription": description,
        "OriginalFilename": exe.name,
        "InternalName": exe.stem,
    }
    for kid in info.kids:
        for table in getattr(kid, "kids", []):
            for entry in getattr(table, "kids", []):
                name = getattr(entry, "name", None)
                if name in replace:
                    entry.val = replace[name]
    # 복사 직후에는 백신이 파일을 잠고 있어 PermissionError 가 난다 (실측: 1회 재시도로 통과).
    # 빌드가 그때마다 깨지지 않게 잠깐 기다렸다 다시 시도한다.
    for attempt in range(20):
        try:
            vi.write_version_info_to_executable(str(exe), info)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.25)


if __name__ == "__main__":
    exe, description, template = sys.argv[1], sys.argv[2], sys.argv[3]
    stamp(Path(exe), description, Path(template))
    print(f"  {Path(exe).name}  <-  {description}")
