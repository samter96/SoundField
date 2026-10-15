# -*- coding: utf-8 -*-
"""자동 업데이트 — 올리는 쪽 도구 (2026-10-14 준비). 받는 쪽은 src-tauri/src/updater.rs.

  python tools/update_tool.py manifest
      설치될 파일 목록(app/update_manifest.json)을 만든다. 갈래(brand)는 app/.brand 스탬프에서 읽는다.
      build_release.ps1 이 인스톨러 직전에 부른다.
      목록 = 파일마다 설치 폴더 안 경로 · 크기 · 지문(sha256). 앱은 이 목록끼리 비교해 바뀐 파일만 받는다.

  python tools/update_tool.py publish --dest "\\\\서버\\공유\\SoundField"
      공유 폴더(또는 이 PC 의 시험 폴더)에 올린다. 지문이 같은 파일은 이미 올라가 있으면 건너뛴다.

  python tools/update_tool.py publish --github samter96/SoundField
      공개 GitHub 의 릴리스 'update' 에 자산으로 올린다 (gh 로그인 계정으로).
      앱의 update.cfg 에는 https://github.com/samter96/SoundField/releases/download/update 를 적는다.

배포는 두 가지 (2026-10-15 사용자 결정 — 올리는 순간 모든 사용자가 자동으로 받으므로):
  개발자 배포 — 개발자 PC 만 받는다 (그 PC 의 %LOCALAPPDATA%\\SoundField\\update\\dev.cfg 가 가리키는 곳).
      python tools/update_tool.py publish --dest <개발자 폴더>
      python tools/update_tool.py publish --github <owner/repo> --tag update-dev
  정식 배포 — 개발자 배포에서 검증한 빌드를 **다시 빌드하지 않고** 그대로 정식 위치로 올린다.
      python tools/update_tool.py promote --from <개발자 폴더> --to <정식 폴더>
      python tools/update_tool.py promote --github <owner/repo>        (update-dev → update)
  ⚠ 정식 위치(update.cfg 의 source)에는 publish 로 바로 올리지 않는다 — 막혀 있다 (--official 로만 강제).
  정식으로 올린 빌드는 목록을 보관한다 (폴더: releases/<빌드>.json, GitHub: release-<빌드>.json) — 되돌리기용.
  내역(notes)은 승격할 때 지금의 UPDATE_NOTES.md 로 다시 붙인다 (본체 파일은 검증한 그대로).

올리는 곳의 모양 (폴더·릴리스 모두 평평하다 — 릴리스 자산은 폴더를 못 가진다):
  latest.json      최신 버전의 파일 목록 (manifest 와 같은 내용)
  <sha256>         파일 본체. 버전끼리 같은 파일은 한 번만 올라간다.

⚠ latest.json 은 **맨 마지막에** 바꾼다. 본체가 다 올라가기 전에 목록이 바뀌면 그 사이에 켠
  센터원이 없는 파일을 받으려다 실패한다.
⚠ 다른 갈래(brand)의 올리는 곳에는 올리지 않는다 — 이미 있는 latest.json 의 brand 를 확인한다.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
MANIFEST = os.path.join(APP, "update_manifest.json")
LATEST = "latest.json"
NOTES = os.path.join(ROOT, "UPDATE_NOTES.md")


def vkey(version):
    """'2.2.0' → (2, 2, 0). 꼬리(-test, +빌드)는 버린다 — 앱(updater.rs ver_key)과 같은 규칙."""
    head = re.split(r"[+-]", version)[0]
    return tuple(int(x) if x.isdigit() else 0 for x in head.split("."))


def load_notes(version):
    """UPDATE_NOTES.md → [{version, text}] (이 버전 이하만). 이 버전 칸이 없으면 멈춘다 —
    버전마다 내역을 적게 하려는 장치다. 앱은 사용자 버전보다 새 것만 골라 보여 준다."""
    if not os.path.isfile(NOTES):
        sys.exit("UPDATE_NOTES.md 가 없습니다")
    text = re.sub(r"<!--.*?-->", "", open(NOTES, encoding="utf-8").read(), flags=re.S)
    notes, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^##\s+(\S+)\s*$", line)
        if m:
            cur = {"version": m.group(1), "lines": []}
            notes.append(cur)
        elif cur is not None and not line.startswith("# "):
            cur["lines"].append(line.rstrip())
    out = [{"version": n["version"], "text": "\n".join(n["lines"]).strip("\n")}
           for n in notes if vkey(n["version"]) <= vkey(version)]
    if not any(vkey(n["version"]) == vkey(version) for n in out):
        sys.exit(f"UPDATE_NOTES.md 에 {version} 칸이 없습니다 — 이번 버전 내역을 먼저 적으세요")
    return out


def latest_of(manifest):
    latest = dict(manifest)
    latest["notes"] = load_notes(manifest["version"])
    return latest


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sources():
    """(설치 폴더 안 경로, 원본 파일) — soundfield_installer.iss 의 [Files] 와 같은 배치여야 한다."""
    out = [("SoundField.exe", os.path.join(APP, "soundfield.exe")),
           ("icon.ico", os.path.join(ROOT, "src-tauri", "icons", "icon.ico")),
           ("update.cfg", os.path.join(ROOT, "update.cfg"))]
    bridge = os.path.join(APP, "bridge")
    for base, _dirs, files in os.walk(bridge):
        for name in files:
            full = os.path.join(base, name)
            rel = os.path.relpath(full, bridge).replace("\\", "/")
            out.append(("bridge/" + rel, full))
    missing = [src for _, src in out if not os.path.isfile(src)]
    if missing:
        sys.exit(f"파일이 없습니다: {missing[:3]}")
    return sorted(out, key=lambda x: x[0].lower())


def cmd_manifest(args):
    stamp_path = os.path.join(APP, ".brand")
    if not os.path.isfile(stamp_path):
        sys.exit("app/.brand 가 없습니다 — build_release.ps1 로 빌드하세요")
    brand = open(stamp_path, encoding="ascii").read().strip()
    conf = json.load(open(os.path.join(ROOT, "src-tauri", "tauri.conf.json"), encoding="utf-8"))
    version = conf["version"]
    build = version + "+" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    files = [{"path": rel, "size": os.path.getsize(src), "sha256": sha256(src)} for rel, src in sources()]
    manifest = {"format": 1, "app": "SoundField", "brand": brand, "version": version,
                "build": build, "files": files}
    with open(MANIFEST, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    total = sum(x["size"] for x in files)
    print(f"update_manifest.json — {build}, 파일 {len(files)}개, {total / 1048576:.1f}MB")


def load_manifest():
    if not os.path.isfile(MANIFEST):
        sys.exit("app/update_manifest.json 이 없습니다 — 인스톨러 빌드(npm run installer)를 먼저 하세요")
    manifest = json.load(open(MANIFEST, encoding="utf-8"))
    by_path = dict(sources())
    # 목록을 만든 뒤 app/ 이 바뀌었으면 올리지 않는다 (목록과 다른 파일이 나가는 사고 방지)
    for f in manifest["files"]:
        src = by_path.get(f["path"])
        if src is None or os.path.getsize(src) != f["size"] or sha256(src) != f["sha256"]:
            sys.exit(f"목록과 실제 파일이 다릅니다: {f['path']} — 인스톨러 빌드를 다시 하세요")
    blobs = {}
    for f in manifest["files"]:
        blobs.setdefault(f["sha256"], (by_path[f["path"]], f["size"]))
    return manifest, blobs


def official_source():
    """이 판의 정식 위치 = update.cfg 의 source (없으면 None)"""
    path = os.path.join(ROOT, "update.cfg")
    if not os.path.isfile(path):
        return None
    for line in open(path, encoding="utf-8-sig").read().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        value = (value if key.strip().lower() == "source" else line).strip().strip('"')
        if value:
            return value
    return None


def is_official_folder(dest):
    off = official_source()
    return bool(off) and os.path.normcase(os.path.normpath(off)) == os.path.normcase(os.path.normpath(dest))


def is_official_tag(repo, tag):
    off = (official_source() or "").rstrip("/").lower()
    return off.endswith(f"/{repo.lower()}/releases/download/{tag.lower()}")


def guard_official(is_official, official_ok, where):
    if is_official and not official_ok:
        sys.exit(f"{where} 은(는) 정식 위치입니다 — 정식 배포는 개발자 배포에서 검증한 빌드를 promote 로 올립니다.\n"
                 f"  (꼭 바로 올려야 하면 --official)")


def safe_build(build):
    return re.sub(r"[^0-9A-Za-z._-]", "_", build)


def check_brand(old, body, where):
    if old.get("brand") != body["brand"] or old.get("app") != body["app"]:
        sys.exit(f"다른 판의 올리는 곳입니다 ({where}: {old.get('app')}/{old.get('brand')}) — 올리지 않습니다")


def write_json_atomic(path, body):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(body, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def cmd_publish_folder(dest, official_ok=False):
    guard_official(is_official_folder(dest), official_ok, dest)
    manifest, blobs = load_manifest()
    latest_of(manifest)   # 내역 칸이 없으면 아무것도 올리기 전에 멈춘다
    os.makedirs(dest, exist_ok=True)
    latest = os.path.join(dest, LATEST)
    if os.path.isfile(latest):
        old = json.load(open(latest, encoding="utf-8"))
        if old.get("brand") != manifest["brand"] or old.get("app") != manifest["app"]:
            sys.exit(f"다른 판의 올리는 곳입니다: {old.get('app')}/{old.get('brand')} — 올리지 않습니다")
    started = time.time()
    sent = sent_bytes = 0
    for sha, (src, size) in blobs.items():
        target = os.path.join(dest, sha)
        if os.path.isfile(target) and os.path.getsize(target) == size:
            continue
        part = target + ".part"
        shutil.copyfile(src, part)
        os.replace(part, target)
        sent += 1
        sent_bytes += size
    body = latest_of(manifest)   # 내역이 없으면 여기서 멈춘다 — 본체를 올리기 전에 확인하는 게 낫다
    tmp = latest + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(body, f, ensure_ascii=False, indent=1)
    os.replace(tmp, latest)
    if is_official_folder(dest):
        os.makedirs(os.path.join(dest, "releases"), exist_ok=True)
        write_json_atomic(os.path.join(dest, "releases", safe_build(body["build"]) + ".json"), body)
    print(f"올림 — {manifest['build']} → {dest}")
    print(f"  새 파일 {sent}개 {sent_bytes / 1048576:.1f}MB (이미 있던 {len(blobs) - sent}개 건너뜀), "
          f"{time.time() - started:.1f}초")


def gh(*argv, capture=True):
    return subprocess.run(["gh", *argv], check=True, text=True, encoding="utf-8",
                          capture_output=capture).stdout


def ensure_release(repo, tag):
    """릴리스(태그)의 자산 이름들. 없으면 시험판 릴리스로 만든다."""
    try:
        return set(gh("release", "view", tag, "-R", repo, "--json", "assets",
                      "-q", ".assets[].name").split())
    except subprocess.CalledProcessError:
        title = "SoundField developer builds" if tag.endswith("-dev") else "SoundField update store"
        gh("release", "create", tag, "-R", repo, "--title", title,
           "--notes", "Files for in-app updates. Install SoundField from the latest release instead.",
           "--prerelease", capture=False)
        return set()


def cmd_publish_github(repo, tag, official_ok=False):
    guard_official(is_official_tag(repo, tag), official_ok, f"github {repo} '{tag}'")
    manifest, blobs = load_manifest()
    latest_of(manifest)   # 내역 칸이 없으면 아무것도 올리기 전에 멈춘다
    names = ensure_release(repo, tag)
    if LATEST in names:
        with tempfile.TemporaryDirectory() as tmp:
            gh("release", "download", tag, "-R", repo, "-p", LATEST, "-D", tmp)
            old = json.load(open(os.path.join(tmp, LATEST), encoding="utf-8"))
        if old.get("brand") != manifest["brand"] or old.get("app") != manifest["app"]:
            sys.exit(f"다른 판의 릴리스입니다: {old.get('app')}/{old.get('brand')} — 올리지 않습니다")
    # 빈 파일은 올리지 않는다 — GitHub 이 크기 0 자산을 거부한다(HTTP 400). 앱은 빈 파일을 받지 않고 만든다.
    todo = [(sha, src) for sha, (src, size) in blobs.items() if sha not in names and size > 0]
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for sha, src in todo:
            path = os.path.join(tmp, sha)
            shutil.copyfile(src, path)
            paths.append(path)
        for i in range(0, len(paths), 20):
            gh("release", "upload", tag, "-R", repo, *paths[i:i + 20], capture=False)
        latest = os.path.join(tmp, LATEST)
        with open(latest, "w", encoding="utf-8", newline="\n") as f:
            json.dump(latest_of(manifest), f, ensure_ascii=False, indent=1)
        gh("release", "upload", tag, "-R", repo, latest, "--clobber", capture=False)
        if is_official_tag(repo, tag):
            keep = os.path.join(tmp, "release-" + safe_build(manifest["build"]) + ".json")
            shutil.copyfile(latest, keep)
            gh("release", "upload", tag, "-R", repo, keep, "--clobber", capture=False)
    print(f"올림 — {manifest['build']} → github {repo} 릴리스 '{tag}', 새 파일 {len(todo)}개")


def cmd_promote_folder(src, dst):
    """개발자 폴더의 빌드를 그대로 정식 폴더로 — 본체는 지문 그대로 복사, 내역만 지금 기록으로."""
    body = json.load(open(os.path.join(src, LATEST), encoding="utf-8"))
    body["notes"] = load_notes(body["version"])
    os.makedirs(dst, exist_ok=True)
    latest = os.path.join(dst, LATEST)
    if os.path.isfile(latest):
        check_brand(json.load(open(latest, encoding="utf-8")), body, dst)
    started = time.time()
    sent = 0
    blobs = {f["sha256"]: f["size"] for f in body["files"]}
    for sha, size in blobs.items():
        target = os.path.join(dst, sha)
        if os.path.isfile(target) and os.path.getsize(target) == size:
            continue
        part = target + ".part"
        shutil.copyfile(os.path.join(src, sha), part)
        if sha256(part) != sha:
            os.remove(part)
            sys.exit(f"개발자 폴더의 파일이 손상됐습니다: {sha} — 승격하지 않습니다")
        os.replace(part, target)
        sent += 1
    os.makedirs(os.path.join(dst, "releases"), exist_ok=True)
    write_json_atomic(os.path.join(dst, "releases", safe_build(body["build"]) + ".json"), body)
    write_json_atomic(latest, body)
    print(f"승격 — {body['build']} : {src} → {dst}, 새 파일 {sent}개, {time.time() - started:.1f}초")


def cmd_promote_github(repo, from_tag, to_tag):
    with tempfile.TemporaryDirectory() as tmp:
        gh("release", "download", from_tag, "-R", repo, "-p", LATEST, "-D", tmp)
        body = json.load(open(os.path.join(tmp, LATEST), encoding="utf-8"))
    body["notes"] = load_notes(body["version"])
    names = ensure_release(repo, to_tag)
    if LATEST in names:
        with tempfile.TemporaryDirectory() as tmp:
            gh("release", "download", to_tag, "-R", repo, "-p", LATEST, "-D", tmp)
            check_brand(json.load(open(os.path.join(tmp, LATEST), encoding="utf-8")), body, to_tag)
    blobs = {f["sha256"]: f["size"] for f in body["files"]}
    need = [sha for sha, size in blobs.items() if size > 0 and sha not in names]
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(0, len(need), 20):
            part = need[i:i + 20]
            args = ["release", "download", from_tag, "-R", repo, "-D", tmp, "--clobber"]
            for sha in part:
                args += ["-p", sha]
            gh(*args)
            for sha in part:
                if sha256(os.path.join(tmp, sha)) != sha:
                    sys.exit(f"개발자 배포의 파일이 손상됐습니다: {sha} — 승격하지 않습니다")
            gh("release", "upload", to_tag, "-R", repo, *[os.path.join(tmp, sha) for sha in part], capture=False)
        latest = os.path.join(tmp, LATEST)
        write_json_atomic(latest, body)
        keep = os.path.join(tmp, "release-" + safe_build(body["build"]) + ".json")
        shutil.copyfile(latest, keep)
        gh("release", "upload", to_tag, "-R", repo, keep, "--clobber", capture=False)
        gh("release", "upload", to_tag, "-R", repo, latest, "--clobber", capture=False)
    print(f"승격 — {body['build']} : github {repo} '{from_tag}' → '{to_tag}', 새 파일 {len(need)}개")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("manifest")
    pub = sub.add_parser("publish")
    pub.add_argument("--dest", help="올리는 곳 폴더")
    pub.add_argument("--github", help="owner/repo")
    pub.add_argument("--tag", default="update-dev", help="GitHub 릴리스 태그 (기본: 개발자 배포 update-dev)")
    pub.add_argument("--official", action="store_true", help="정식 위치에 바로 올리기 (평소엔 promote 를 쓴다)")
    pro = sub.add_parser("promote", help="개발자 배포의 빌드를 그대로 정식 위치로")
    pro.add_argument("--from", dest="src", help="개발자 폴더")
    pro.add_argument("--to", dest="dst", help="정식 폴더 (생략하면 update.cfg 의 source)")
    pro.add_argument("--github", help="owner/repo")
    pro.add_argument("--from-tag", default="update-dev")
    pro.add_argument("--to-tag", default="update")
    args = p.parse_args()
    if args.cmd == "manifest":
        cmd_manifest(args)
    elif args.cmd == "promote":
        if args.github:
            cmd_promote_github(args.github, args.from_tag, args.to_tag)
        elif args.src:
            cmd_promote_folder(args.src, args.dst or official_source() or sys.exit("--to 를 주세요"))
        else:
            sys.exit("--from 또는 --github 를 주세요")
    elif args.dest:
        cmd_publish_folder(args.dest, args.official)
    elif args.github:
        cmd_publish_github(args.github, args.tag, args.official)
    else:
        sys.exit("--dest 또는 --github 를 주세요")


if __name__ == "__main__":
    main()
