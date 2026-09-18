/* ── 한국어 / 영어 전환 ───────────────────────────────────────────────────────
   기본은 한국어다. 화면에 보이는 모든 한글 문장을 `t("...")` 로 감싸고,
   **한글 원문 자체를 열쇠로** 영문 사전(i18n_en.ts)을 찾는다 (사용자 결정 2026-09-08).

   왜 한글 원문을 열쇠로 쓰나
     · 이 코드베이스는 주석까지 전부 한글이다. `t("search.results")` 같은 이름으로
       바꾸면 코드만 보고 무슨 문장인지 알 수 없어 유지보수가 어려워진다.
     · 문장이 1,100개다. 이름을 새로 1,100개 지어 붙이는 비용이 크다.

   ⚠ 원문을 고치면 사전이 어긋난다. 그래서
     · 영문이 없으면 **한글을 그대로** 내보내고 (화면이 비지 않는다),
     · 개발 중에는 콘솔에 한 번 경고하고,
     · `python tools/check_i18n.py` 가 사전에 없는 문장을 전부 찾아낸다.
     문장을 고쳤으면 그 검사를 돌려 사전도 함께 고칠 것.

   값이 끼는 문장은 `{0}`, `{1}` 자리표를 쓴다:
     t("결과 {0}개", count)
   자바스크립트 `${}` 를 그대로 쓰면 문장마다 열쇠가 달라져 사전을 만들 수 없다. */

import { useSyncExternalStore } from "react";
import { EN } from "./i18n_en";

export type Lang = "ko" | "en";

let current: Lang = "ko";
const listeners = new Set<() => void>();
type OriginalNode = { ko: string; en: string };
const textOriginals = new WeakMap<Text, OriginalNode>();
const attrOriginals = new WeakMap<Element, Map<string, OriginalNode>>();
let observer: MutationObserver | null = null;

export const getLang = (): Lang => current;

export function setLang(next: Lang) {
  if (next === current) return;
  current = next;
  /* 문서 언어를 알려 준다 — 브라우저의 줄바꿈·글꼴 선택 규칙이 이 값을 본다 */
  try {
    document.documentElement.lang = next === "en" ? "en" : "ko";
    document.body.classList.toggle("lang-en", next === "en");
  } catch { /* 창이 아직 없을 때 */ }
  listeners.forEach((fn) => fn());
  ensureLegacyTranslationObserver();
  /* React의 같은-문자열 최적화보다 뒤에서 실행해 기존 화면도 확실히 되돌린다. */
  queueMicrotask(() => translateDocument(next));
}

function subscribe(fn: () => void) {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

/** 컴포넌트가 언어 변경에 다시 그려지게 한다. 최상위(App)에서 한 번 부르면
 *  자식들도 함께 다시 그려진다 (이 앱은 React.memo 를 쓰지 않는다). */
export function useLang(): Lang {
  return useSyncExternalStore(subscribe, getLang, getLang);
}

const missing = new Set<string>();

/** 화면에 보일 문장. 영어 모드에서 사전에 없으면 한글을 그대로 돌려준다. */
export function t(text: string, ...args: Array<string | number>): string {
  let out = text;
  if (current === "en") {
    const found = EN[text];
    if (found !== undefined) {
      out = found;
    } else if (!missing.has(text)) {
      missing.add(text);
      /* 화면을 망가뜨리지 않고 개발자에게만 알린다 */
      console.warn("[i18n] 영문 없음:", text);
    }
  }
  if (!args.length) return out;
  return out.replace(/\{(\d+)\}/g, (whole, index) => {
    const value = args[Number(index)];
    return value === undefined ? whole : String(value);
  });
}

/** 영문이 빠진 문장 목록 (개발 중 확인용) */
export function missingTranslations(): string[] {
  return [...missing];
}

/* ── 기존 화면 마이그레이션 안전망 ───────────────────────────────────────────
   새 코드와 자주 바뀌는 화면은 t()를 직접 쓰는 것이 기준이다. 다만 이 앱에는 이미
   표시 문장이 수백 개 있어 한 번에 JSX 구조를 갈아엎으면 동작 회귀 위험이 더 크다.
   생성 사전에 있는 **알려진 UI 문장만** 렌더링 경계에서 바꾼다. 검색 결과의 파일명,
   폴더명, 경로, 메타데이터는 아래 skip 범위로 보호한다.

   Rust/Python이 보내는 상태 문장도 이 경계를 통과하므로 백엔드 프로토콜 값은 한글
   그대로 유지할 수 있다. 앱 실행 중 네트워크 번역은 전혀 하지 않는다. */
const ATTRS = ["aria-label", "title", "placeholder", "data-tip", "alt"];
const USER_DATA_SELECTOR = [
  ".td", ".tree-label", ".history-item", ".blacklist-path", ".track-name",
  ".file-path", ".file-name", "[data-i18n-skip]",
].join(",");

type TemplateTranslation = {
  re: RegExp;
  order: number[];
  english: string;
  weight: number;
};

let templateTranslations: TemplateTranslation[] | null = null;

function escapeRe(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function getTemplates(): TemplateTranslation[] {
  if (templateTranslations) return templateTranslations;
  templateTranslations = Object.entries(EN)
    .filter(([key]) => /\{\d+\}/.test(key))
    .map(([key, english]) => {
      const order: number[] = [];
      let pattern = "^";
      let at = 0;
      for (const match of key.matchAll(/\{(\d+)\}/g)) {
        pattern += escapeRe(key.slice(at, match.index)) + "(.*?)";
        order.push(Number(match[1]));
        at = (match.index ?? 0) + match[0].length;
      }
      pattern += escapeRe(key.slice(at)) + "$";
      return { re: new RegExp(pattern, "s"), order, english, weight: key.replace(/\{\d+\}/g, "").length };
    })
    .sort((a, b) => b.weight - a.weight);
  return templateTranslations;
}

function renderedEnglish(source: string): string {
  const leading = source.match(/^\s*/)?.[0] ?? "";
  const trailing = source.match(/\s*$/)?.[0] ?? "";
  const core = source.slice(leading.length, source.length - trailing.length);
  if (!core || !/[가-힣]/.test(core)) return source;
  const compact = core.replace(/\s+/g, " ").trim();
  const exact = EN[core] ?? EN[compact];
  if (exact !== undefined) return leading + exact + trailing;
  /* 백엔드 예외는 뒤쪽 상세가 런타임 값이라 완전 일치 키를 만들 수 없다. */
  for (const [korean, english] of [["오류:", "Error:"], ["경고:", "Warning:"]] as const) {
    if (compact.startsWith(korean)) {
      return leading + english + compact.slice(korean.length) + trailing;
    }
  }
  for (const item of getTemplates()) {
    const match = item.re.exec(compact);
    if (!match) continue;
    const values = new Map<number, string>();
    item.order.forEach((index, capture) => {
      if (!values.has(index)) values.set(index, match[capture + 1]);
    });
    const translated = item.english.replace(/\{(\d+)\}/g, (whole, index) => {
      const value = values.get(Number(index));
      /* "{0} 숨기기"처럼 자리표 안에 고정 UI 라벨이 들어가는 경우도 번역한다.
         사전의 완전 일치 항목만 사용하므로 파일명·경로는 건드리지 않는다. */
      return value === undefined ? whole : (EN[value] ?? value);
    });
    return leading + translated + trailing;
  }
  return source;
}

function isUserData(node: Node): boolean {
  const element = node.nodeType === Node.ELEMENT_NODE ? node as Element : node.parentElement;
  return !!element?.closest(USER_DATA_SELECTOR);
}

function translateTextNode(node: Text, lang: Lang) {
  if (isUserData(node)) return;
  const value = node.nodeValue ?? "";
  const saved = textOriginals.get(node);
  if (lang === "ko") {
    if (saved && value === saved.en) node.nodeValue = saved.ko;
    return;
  }
  if (saved && value === saved.en) return;
  const english = renderedEnglish(value);
  if (english !== value) {
    textOriginals.set(node, { ko: value, en: english });
    node.nodeValue = english;
  }
}

function translateElementAttrs(element: Element, lang: Lang) {
  if (element.matches(USER_DATA_SELECTOR)) return;
  let records = attrOriginals.get(element);
  for (const attr of ATTRS) {
    const value = element.getAttribute(attr);
    if (value === null) continue;
    const saved = records?.get(attr);
    if (lang === "ko") {
      if (saved && value === saved.en) element.setAttribute(attr, saved.ko);
      continue;
    }
    if (saved && value === saved.en) continue;
    const english = renderedEnglish(value);
    if (english !== value) {
      if (!records) {
        records = new Map();
        attrOriginals.set(element, records);
      }
      records.set(attr, { ko: value, en: english });
      element.setAttribute(attr, english);
    }
  }
}

function translateTree(root: Node, lang: Lang) {
  if (root.nodeType === Node.TEXT_NODE) translateTextNode(root as Text, lang);
  if (root.nodeType === Node.ELEMENT_NODE) translateElementAttrs(root as Element, lang);
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  while (node) {
    if (node.nodeType === Node.TEXT_NODE) translateTextNode(node as Text, lang);
    else translateElementAttrs(node as Element, lang);
    node = walker.nextNode();
  }
}

function translateDocument(lang: Lang) {
  if (typeof document === "undefined" || !document.body) return;
  translateTree(document.body, lang);
}

function ensureLegacyTranslationObserver() {
  if (observer || typeof MutationObserver === "undefined" || !document.body) return;
  observer = new MutationObserver((changes) => {
    for (const change of changes) {
      if (change.type === "characterData") translateTextNode(change.target as Text, current);
      else if (change.type === "attributes") translateElementAttrs(change.target as Element, current);
      else change.addedNodes.forEach((node) => translateTree(node, current));
    }
  });
  observer.observe(document.body, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true,
    attributeFilter: ATTRS,
  });
}
