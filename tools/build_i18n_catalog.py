# -*- coding: utf-8 -*-
"""Build the legacy UI English catalogue from Korean source literals.

The hand-curated dictionary in ``src/i18n_en.ts`` wins over this file.  This
catalogue is the migration safety net for the older screens that have not yet
been converted to explicit ``t(...)`` calls.  It is generated at development
time only; the application never calls a translation service at runtime.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
OUT = SRC / "i18n_generated_en.ts"
CACHE = ROOT / "tools" / "i18n_translation_cache.json"
HANGUL = re.compile(r"[가-힣]")
CODE_FRAGMENT = re.compile(
    r"(?:\bconst\b|\blet\b|\breturn\b|\bfunction\b|\bexport\b|\bimport\b|"
    r"className|=>|Array<|use[A-Z]\w*\(|set[A-Z]\w*\(|std::|Result<|\bfn\s+\w+|\.store\()"
)


def looks_like_ui_text(value: str) -> bool:
    """Reject scanner spillover into source code while keeping long help text."""
    return bool(
        HANGUL.search(value)
        and 1 < len(value) <= 900
        and not re.match(r"^[A-Za-z]:[\\/]", value)
        and not re.match(r"^[=_}\[]", value)
        and not CODE_FRAGMENT.search(value)
    )


def strip_ts_comments(text: str) -> str:
    out: list[str] = []
    i = 0
    quote: str | None = None
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < len(text):
                out.append(nxt)
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            i = text.find("\n", i)
            if i < 0:
                break
            out.append("\n")
            i += 1
            continue
        if ch == "/" and nxt == "*":
            end = text.find("*/", i + 2)
            i = len(text) if end < 0 else end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def compact(text: str) -> str:
    counter = iter(range(1000))
    text = re.sub(r"\$\{[^}]+\}", lambda _m: "{%d}" % next(counter), text)
    # Rust/Python format strings may contain several bare `{}` slots.  Giving
    # every slot index 0 would repeat the first runtime value in English.
    text = re.sub(r"\{(?::[^}]*)?\}", lambda _m: "{%d}" % next(counter), text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def quoted_values(code: str, quotes: str = "\"'`"):
    """Linear string scanner; avoids regex backtracking on large bundled sources."""
    i = 0
    while i < len(code):
        if code[i] not in quotes:
            i += 1
            continue
        quote = code[i]
        i += 1
        start = i
        value: list[str] = []
        while i < len(code):
            if code[i] == "\\" and i + 1 < len(code):
                value.extend((code[i], code[i + 1]))
                i += 2
                continue
            if code[i] == quote:
                yield "".join(value)
                i += 1
                break
            value.append(code[i])
            i += 1
        else:
            i = start
            break


def source_phrases() -> set[str]:
    phrases: set[str] = set()
    for path in SRC.rglob("*"):
        if path.suffix not in {".ts", ".tsx"} or path.name.startswith("i18n") or path.name == "data.ts":
            continue
        code = strip_ts_comments(path.read_text(encoding="utf-8-sig"))
        for value in quoted_values(code):
            value = bytes(value, "utf-8").decode("unicode_escape") if "\\u" in value else value
            value = value.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\'", "'")
            value = compact(value)
            if looks_like_ui_text(value):
                phrases.add(value)
        for match in re.finditer(r">([^<>{}]*[가-힣][^<>{}]*)<", code):
            value = compact(match.group(1).replace("&nbsp;", " "))
            if value:
                phrases.add(value)

    # Messages arriving from Rust/Python are translated at the rendering edge.
    for path in list((ROOT / "src-tauri" / "src").glob("*.rs")) + list((ROOT / "src-tauri" / "python").glob("*.py")):
        code = path.read_text(encoding="utf-8-sig", errors="replace")
        for raw in quoted_values(code, "\"'"):
            value = compact(raw.replace("\\n", " "))
            if looks_like_ui_text(value):
                phrases.add(value)
    return phrases


def google_translate(text: str) -> str:
    url = (
        "https://translate.googleapis.com/translate_a/single?client=gtx&sl=ko&tl=en&dt=t&q="
        + urllib.parse.quote(text)
    )
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                payload = json.loads(response.read())
            translated = "".join(part[0] for part in payload[0] if part and part[0])
            return translated.strip()
        except Exception:
            if attempt == 4:
                raise
            time.sleep(0.6 * (attempt + 1))
    raise RuntimeError("translation failed")


def batches(items: list[str], max_chars: int = 3500):
    batch: list[str] = []
    size = 0
    for item in items:
        added = len(item) + 20
        if batch and size + added > max_chars:
            yield batch
            batch, size = [], 0
        batch.append(item)
        size += added
    if batch:
        yield batch


def main() -> None:
    phrases = sorted(source_phrases())
    cache: dict[str, str] = {}
    if CACHE.exists():
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
    missing = [text for text in phrases if text not in cache]
    if missing:
        groups = list(batches(missing))
        completed = 0
        marker = "\n<<<SOUNDFIELD_I18N_SPLIT>>>\n"
        for index, group in enumerate(groups, 1):
            translated = google_translate(marker.join(group))
            parts = translated.split("<<<SOUNDFIELD_I18N_SPLIT>>>")
            if len(parts) != len(group):
                # The service occasionally changes whitespace around the marker.
                parts = re.split(r"\s*<<<\s*SOUNDFIELD_I18N_SPLIT\s*>>>\s*", translated)
            if len(parts) != len(group):
                raise RuntimeError(f"batch split failed: {len(parts)} != {len(group)}")
            for source, english in zip(group, parts):
                cache[source] = english.strip()
            completed += len(group)
            CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"translated {completed}/{len(missing)} (batch {index}/{len(groups)})", flush=True)

    selected = {key: cache[key] for key in phrases}
    lines = [
        "/* Generated by tools/build_i18n_catalog.py. Do not edit by hand. */",
        "export const GENERATED_EN: Record<string, string> = "
        + json.dumps(selected, ensure_ascii=False, indent=2)
        + ";",
        "",
    ]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"phrases": len(phrases), "new": len(missing), "output": str(OUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
